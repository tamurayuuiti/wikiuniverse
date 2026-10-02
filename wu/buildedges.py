"""pagelinks.sql.gz のストリーム解析 -> 解決済み ns0 記事->記事エッジリスト(int32 バイナリ)。

スキーマ(2026 ダンプ): pagelinks(pl_from, pl_from_namespace, pl_target_id)
  pl_target_id -> linktarget(lt_id, lt_namespace, lt_title)

解決パイプライン(チャンク毎に完全ベクトル化):
  1. pl_from_namespace == 0 の行を残す
  2. pl_from がリダイレクトページの行を落とす(リダイレクトはノードではない)
  3. lt_id -> blake2b64(title)(dense 配列経由。0 = 非 ns0/欠落)
  4. hash -> page_id(ソート済み ns0-all ハッシュ表の searchsorted。miss = 赤リンク)
  5. リダイレクト先は rd_map で解決(宙づり -> 除去)
  6. セルフループを除去。src は非リダイレクト記事であることを要求
  7. (src, dst) int32 ペアを edges_ns0.bin へ追記

チェックポイント付き: (解凍後オフセット, 既読行数)が永続化されるため、
中断した実行は gzip ストリームの高速送りから再開できる。
"""
from __future__ import annotations

import itertools
import os
import re
import time

import numpy as np

from .dumpio import read_json, stream_decompressed, write_json

PL_RE = re.compile(rb"\((\d+),(-?\d+),(\d+)\)")


def build_edges(pagelinks_gz: str, parsed_dir: str, out_bin: str, ckpt_path: str,
                chunk_bytes: int = 1 << 24, log_every: float = 30.0):
    t0 = time.time()
    z = np.load(os.path.join(parsed_dir, "ns0_all_hashes.npz"))
    all_h, all_pid = z["hashes"], z["page_ids"]
    article_ids = np.load(os.path.join(parsed_dir, "article_ids.npy"))
    lt_hash = np.load(os.path.join(parsed_dir, "lt_hash_ns0.npy"), mmap_mode="r")
    rd = np.load(os.path.join(parsed_dir, "rd_map.npz"))
    rd_from, rd_final = rd["rd_from"], rd["rd_final"]

    ck = read_json(ckpt_path, None)
    counters = {
        "rows_total": 0, "from_non_ns0": 0, "src_is_redirect": 0, "src_not_article": 0,
        "target_non_ns0": 0, "target_redlink": 0, "target_dangling_redirect": 0,
        "self_loops": 0, "kept": 0,
    }
    skip_to = 0
    if ck and ck.get("state") != "done":
        skip_to = ck["decomp_offset"]
        counters.update(ck["counters"])
        print(f"[resume] from decomp_offset={skip_to/1e9:.2f}GB kept={counters['kept']:,}")
    elif ck and ck.get("state") == "done":
        print("[skip] edges already built")
        counters.update(ck.get("counters", {}))
        return counters

    os.makedirs(os.path.dirname(out_bin), exist_ok=True)
    fmode = "r+b" if (skip_to and os.path.exists(out_bin)) else "wb"
    fout = open(out_bin, fmode)
    if skip_to:
        fout.seek(counters["kept"] * 8)
        fout.truncate()

    last_log = t0
    n_lt = len(lt_hash)
    carry = b""

    for off, chunk in stream_decompressed(pagelinks_gz, chunk_bytes, skip_to=skip_to):
        # cut at last complete tuple; carry the remainder to the next chunk
        chunk = carry + chunk
        cut = chunk.rfind(b")")
        if cut < 0:
            carry = chunk
            continue
        carry = chunk[cut + 1 :]
        chunk = chunk[: cut + 1]
        matches = PL_RE.findall(chunk)
        if matches:
            flat = itertools.chain.from_iterable(matches)
            arr = np.fromiter(map(int, flat), dtype=np.int64, count=3 * len(matches)).reshape(-1, 3)
            del matches, flat
            n = len(arr)
            counters["rows_total"] += n

            m = arr[:, 1] == 0
            counters["from_non_ns0"] += int(n - m.sum())
            arr = arr[m]
            src = arr[:, 0]
            lt = arr[:, 2]

            # (2) src must not be a redirect page
            if len(rd_from):
                p = np.searchsorted(rd_from, src)
                pc = np.clip(p, 0, len(rd_from) - 1)
                is_redir = rd_from[pc] == src
            else:
                is_redir = np.zeros(len(src), bool)
            counters["src_is_redirect"] += int(is_redir.sum())
            keep = ~is_redir
            src, lt = src[keep], lt[keep]

            # (6a) src must be an existing article
            p = np.searchsorted(article_ids, src)
            pc = np.clip(p, 0, len(article_ids) - 1)
            ok_src = article_ids[pc] == src
            counters["src_not_article"] += int((~ok_src).sum())
            src, lt = src[ok_src], lt[ok_src]

            # (3) lt_id -> title hash
            valid_lt = (lt >= 0) & (lt < n_lt)
            counters["target_non_ns0"] += int((~valid_lt).sum())
            src, lt = src[valid_lt], lt[valid_lt]
            hv = np.asarray(lt_hash)[lt] if len(lt) else lt
            nz = hv != 0
            counters["target_non_ns0"] += int((~nz).sum())
            src, hv = src[nz], hv[nz]

            # (4) hash -> page_id
            if len(hv):
                p = np.searchsorted(all_h, hv)
                pc = np.clip(p, 0, len(all_h) - 1)
                found = all_h[pc] == hv
                counters["target_redlink"] += int((~found).sum())
                src, dst = src[found], all_pid[pc[found]]

                # (5) redirect resolution
                if len(rd_from) and len(dst):
                    p = np.searchsorted(rd_from, dst)
                    pc = np.clip(p, 0, len(rd_from) - 1)
                    hit = rd_from[pc] == dst
                    if hit.any():
                        fin = rd_final[pc]
                        dst2 = np.where(hit, fin, dst)
                        bad = hit & (fin == 0)
                        counters["target_dangling_redirect"] += int(bad.sum())
                        good = ~bad
                        src, dst = src[good], dst2[good]

                # (6b) self loops
                sl = src == dst
                counters["self_loops"] += int(sl.sum())
                src, dst = src[~sl], dst[~sl]

                if len(src):
                    out = np.empty(len(src) * 2, dtype=np.int32)
                    out[0::2] = src.astype(np.int32)
                    out[1::2] = dst.astype(np.int32)
                    out.tofile(fout)
                    counters["kept"] += len(src)
            # NOTE: `tail` (incomplete tuple remainder) is dropped here because
            # stream_decompressed re-reads whole chunks; to stay exact we keep a
            # module-level carry instead:
        del chunk
        now = time.time()
        if now - last_log > log_every:
            gb = off / 1e9
            speed = (off - skip_to) / max(now - t0, 1e-9) / 1e6
            print(f"  edges: {gb:.2f}GB decomp, rows={counters['rows_total']:,}, "
                  f"kept={counters['kept']:,}, {speed:.0f}MB/s, {now-t0:.0f}s", flush=True)
            fout.flush()
            write_json(ckpt_path, {"decomp_offset": off, "counters": counters, "state": "running"})
            last_log = now

    fout.flush()
    fout.close()
    write_json(ckpt_path, {"decomp_offset": off, "counters": counters, "state": "done",
                           "secs": round(time.time() - t0, 1),
                           "out_bytes": os.path.getsize(out_bin)})
    print(f"[edges] done: {counters}")
    return counters
