"""Wikimedia SQL ダンプのストリーミングパーサ -> コンパクトな numpy/parquet 成果物。

文字列タイトルはすべて blake2b(digest_size=8) でハッシュ化し、join は巨大な
Python dict ではなくソート済み配列の searchsorted で行う(低 RAM)。
"""
from __future__ import annotations

import os
import re
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .dumpio import read_json, stream_decompressed, write_json
from hashlib import blake2b

# ---------------------------------------------------------------- hashing ---

_ESCAPES = {
    ord("\\"): ord("\\"),  # placeholder, handled by _unescape
}
_UNESC_MAP = {
    "0": b"\0", "b": b"\b", "n": b"\n", "r": b"\r", "t": b"\t",
    "Z": b"\x1a", "\\": b"\\", "'": b"'", '"': b'"',
}
_UNESC_RE = re.compile(rb"\\(.)", re.S)


def unescape(s: bytes) -> bytes:
    if b"\\" not in s:
        return s
    return _UNESC_RE.sub(lambda m: _UNESC_MAP.get(m.group(1).decode("latin1"), m.group(1)), s)


def h64(title: bytes) -> int:
    """(正規化された=アンダースコア区切りの)タイトルの決定的な 64bit ハッシュ。"""
    v = int.from_bytes(blake2b(title, digest_size=8).digest(), "big")
    return v if v != 0 else 1  # 0 is the 'missing' sentinel


# --------------------------------------------------------------- growlist ---

class GrowArr:
    """幾何成長の追記専用 int 配列(Python リストのメモリオーバーヘッドを回避)。"""

    def __init__(self, dtype=np.int64, cap=1 << 16):
        self.dtype = np.dtype(dtype)
        self.buf = np.empty(cap, dtype=self.dtype)
        self.n = 0

    def extend(self, arr):
        k = len(arr)
        if self.n + k > len(self.buf):
            newcap = max(len(self.buf) * 2, self.n + k)
            nb = np.empty(newcap, dtype=self.dtype)
            nb[: self.n] = self.buf[: self.n]
            self.buf = nb
        self.buf[self.n : self.n + k] = arr
        self.n += k

    def append(self, v):
        self.extend(np.array([v], dtype=self.dtype))

    def result(self):
        return self.buf[: self.n].copy()


# ------------------------------------------------------- page.sql.gz parse --

# (page_id, page_namespace, page_title, page_is_redirect, ...)
PAGE_RE = re.compile(rb"\((\d+),(-?\d+),'((?:[^'\\]|\\.)*)',(\d+)")


def build_page_artifacts(page_gz: str, parsed_dir: str, meta_path: str, chunk_bytes=1 << 24):
    """page.sql.gz の解析 ->
      articles.parquet        : page_id, title(str)(ns0・非リダイレクト)、page_id 順ソート
      ns0_all_hashes.npz      : 全 ns0 ページ(記事+リダイレクト)のソート済みハッシュ + page_id
      article_ids.npy         : ns0 非リダイレクト記事のソート済み page_id(int64)
    """
    os.makedirs(parsed_dir, exist_ok=True)
    t0 = time.time()
    all_ids = GrowArr(np.int64)
    all_hashes = GrowArr(np.uint64)
    art_ids = GrowArr(np.int64)
    art_hashes = GrowArr(np.uint64)

    pq_path = os.path.join(parsed_dir, "articles.parquet")
    writer = None
    pq_ids, pq_titles = [], []
    counts = {"rows": 0, "ns0": 0, "ns0_redirect": 0, "ns0_article": 0}

    def flush_pq():
        nonlocal writer, pq_ids, pq_titles
        if not pq_ids:
            return
        tbl = pa.table({
            "page_id": pa.array(np.array(pq_ids, dtype=np.int64), type=pa.int64()),
            "title": pa.array(pq_titles, type=pa.string()),
        })
        if writer is None:
            writer = pq.ParquetWriter(pq_path, tbl.schema, compression="zstd")
        writer.write_table(tbl)
        pq_ids, pq_titles = [], []

    for off, chunk in stream_decompressed(page_gz, chunk_bytes):
        c_all_ids, c_all_h, c_art_ids, c_art_h = [], [], [], []
        for m in PAGE_RE.finditer(chunk):
            counts["rows"] += 1
            ns = int(m.group(2))
            if ns != 0:
                continue
            counts["ns0"] += 1
            pid = int(m.group(1))
            title = unescape(m.group(3))
            hv = h64(title)
            c_all_ids.append(pid)
            c_all_h.append(hv)
            is_redir = m.group(4) != b"0"
            if is_redir:
                counts["ns0_redirect"] += 1
            else:
                counts["ns0_article"] += 1
                c_art_ids.append(pid)
                c_art_h.append(hv)
                pq_ids.append(pid)
                pq_titles.append(title.decode("utf-8", "replace"))
        if c_all_ids:
            all_ids.extend(np.fromiter(c_all_ids, dtype=np.int64, count=len(c_all_ids)))
            all_hashes.extend(np.fromiter(c_all_h, dtype=np.uint64, count=len(c_all_h)))
            art_ids.extend(np.fromiter(c_art_ids, dtype=np.int64, count=len(c_art_ids)))
            art_hashes.extend(np.fromiter(c_art_h, dtype=np.uint64, count=len(c_art_h)))
        flush_pq()
        print(f"  page: {counts['rows']:,} rows, ns0={counts['ns0']:,} "
              f"articles={counts['ns0_article']:,} ({time.time()-t0:.0f}s)", flush=True)
    if writer is not None:
        writer.close()

    # sort all-ns0 table by hash for searchsorted joins
    ah = all_hashes.result()
    ai = all_ids.result()
    order = np.argsort(ah, kind="stable")
    ah, ai = ah[order], ai[order]
    np.savez(os.path.join(parsed_dir, "ns0_all_hashes.npz"), hashes=ah, page_ids=ai)

    # articles sorted by page_id (dump is PK order, but sort to be safe) + parallel hash array
    aids = art_ids.result()
    ahas = art_hashes.result()
    o2 = np.argsort(aids, kind="stable")
    np.save(os.path.join(parsed_dir, "article_ids.npy"), aids[o2])
    np.save(os.path.join(parsed_dir, "article_hashes_sorted_by_id.npy"), ahas[o2])

    meta = read_json(meta_path, {}) or {}
    meta["page"] = {**counts, "secs": round(time.time() - t0, 1),
                    "max_page_id": int(aids.max()) if len(aids) else 0}
    write_json(meta_path, meta)
    print(f"[page] {counts} in {time.time()-t0:.0f}s")
    return counts


# ------------------------------------------------- linktarget.sql.gz parse --

LT_RE = re.compile(rb"\((\d+),(-?\d+),'((?:[^'\\]|\\.)*)'")


def build_linktarget_artifacts(lt_gz: str, parsed_dir: str, meta_path: str, chunk_bytes=1 << 24):
    """linktarget.sql.gz の解析 -> lt_hash_ns0.npy: lt_id で索引される dense int64 配列。
    ns0 ターゲットは blake2b64(lt_title)、欠落/非 ns0 は 0 を保持する。"""
    t0 = time.time()
    ids = GrowArr(np.int64)
    hashes = GrowArr(np.uint64)
    counts = {"rows": 0, "ns0": 0}
    max_id = 0
    for off, chunk in stream_decompressed(lt_gz, chunk_bytes):
        c_ids, c_h = [], []
        for m in LT_RE.finditer(chunk):
            counts["rows"] += 1
            if int(m.group(2)) != 0:
                continue
            counts["ns0"] += 1
            lt_id = int(m.group(1))
            c_ids.append(lt_id)
            c_h.append(h64(unescape(m.group(3))))
            if lt_id > max_id:
                max_id = lt_id
        if c_ids:
            ids.extend(np.fromiter(c_ids, dtype=np.int64, count=len(c_ids)))
            hashes.extend(np.fromiter(c_h, dtype=np.uint64, count=len(c_h)))
            print(f"  linktarget: {counts['rows']:,} rows ns0={counts['ns0']:,} ({time.time()-t0:.0f}s)", flush=True)
    dense = np.zeros(max_id + 2, dtype=np.uint64)
    dense[ids.result()] = hashes.result()
    np.save(os.path.join(parsed_dir, "lt_hash_ns0.npy"), dense)
    meta = read_json(meta_path, {}) or {}
    meta["linktarget"] = {**counts, "max_lt_id": int(max_id), "secs": round(time.time() - t0, 1)}
    write_json(meta_path, meta)
    print(f"[linktarget] {counts} max_lt_id={max_id:,} in {time.time()-t0:.0f}s")
    return counts


# ---------------------------------------------------- redirect.sql.gz parse --

RD_RE = re.compile(rb"\((\d+),(-?\d+),'((?:[^'\\]|\\.)*)','((?:[^'\\]|\\.)*)'")


def build_redirect_artifacts(rd_gz: str, parsed_dir: str, meta_path: str, chunk_bytes=1 << 24):
    """redirect.sql.gz の解析 -> rd_map.npz {rd_from(ソート済み int64), rd_final(int64 page_id, 0=宙づり)}

    ターゲットは ns0 ハッシュ表に対して解決する。チェーンは最大 10 hop まで
    追跡する。最終ターゲットが記事でない場合(欠落/リダイレクトループ)は 0 になる。
    """
    z = np.load(os.path.join(parsed_dir, "ns0_all_hashes.npz"))
    all_h, all_pid = z["hashes"], z["page_ids"]
    article_ids = np.load(os.path.join(parsed_dir, "article_ids.npy"))

    t0 = time.time()
    froms = GrowArr(np.int64)
    tos = GrowArr(np.int64)
    counts = {"rows": 0, "ns0_local": 0, "target_found": 0, "dangling": 0}
    for off, chunk in stream_decompressed(rd_gz, chunk_bytes):
        f_list, t_list = [], []
        for m in RD_RE.finditer(chunk):
            counts["rows"] += 1
            if int(m.group(2)) != 0:
                continue
            if m.group(4) not in (b"",):  # rd_interwiki non-empty -> external redirect
                continue
            counts["ns0_local"] += 1
            hv = np.uint64(h64(unescape(m.group(3))))
            pos = np.searchsorted(all_h, hv)
            if pos < len(all_h) and all_h[pos] == hv:
                t_list.append(int(all_pid[pos]))
                counts["target_found"] += 1
            else:
                t_list.append(0)
                counts["dangling"] += 1
            f_list.append(int(m.group(1)))
        if f_list:
            froms.extend(np.array(f_list, dtype=np.int64))
            tos.extend(np.array(t_list, dtype=np.int64))
    rf = froms.result()
    rt = tos.result()
    o = np.argsort(rf, kind="stable")
    rf, rt = rf[o], rt[o]

    # chase chains (vectorized, max 10 hops)
    cur = rt.copy()
    for _ in range(10):
        pos = np.searchsorted(rf, cur)
        pos_c = np.clip(pos, 0, len(rf) - 1) if len(rf) else pos
        if len(rf) == 0:
            break
        hit = (cur != 0) & (rf[pos_c] == cur)
        if not hit.any():
            break
        cur[hit] = rt[pos_c[hit]]
    # final must be an existing article
    pos = np.searchsorted(article_ids, cur)
    pos_c = np.clip(pos, 0, len(article_ids) - 1)
    ok = (cur != 0) & (article_ids[pos_c] == cur)
    final = np.where(ok, cur, 0)
    counts["final_resolved"] = int(ok.sum())
    np.savez(os.path.join(parsed_dir, "rd_map.npz"), rd_from=rf, rd_final=final)
    meta = read_json(meta_path, {}) or {}
    meta["redirect"] = {**counts, "secs": round(time.time() - t0, 1)}
    write_json(meta_path, meta)
    print(f"[redirect] {counts} in {time.time()-t0:.0f}s")
    return counts
