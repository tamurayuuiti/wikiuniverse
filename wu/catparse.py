"""categorylinks の解析 -> 記事->カテゴリの所属配列。

スキーマ(2026 ダンプ):
  categorylinks(cl_from, cl_sortkey, cl_timestamp, cl_sortkey_prefix,
                cl_type enum('page','subcat','file'), cl_collation_id, cl_target_id)
  cl_target_id -> linktarget(lt_id, lt_namespace=14, lt_title)

出力(graph/):
  categories.parquet      cat_id(int32), lt_id(int64), title(str)
  article_categories.bin  int32 ペア (article_compact_idx, cat_id)、idx 順ソート
  catlinks_meta.json      カウンタ/来歴

cl_type='subcat' の行(カテゴリ階層)はカウントするがまだ保存しない
(将来: 命名/ズームレベル用のカテゴリツリー)。
"""
from __future__ import annotations

import os
import re
import time

import numpy as np

from .dumpio import read_json, stream_decompressed, write_json
from .sqlparse import GrowArr, h64, unescape

LT_RE = re.compile(rb"\((\d+),(-?\d+),'((?:[^'\\]|\\.)*)'")
# (cl_from, 'sortkey', 'ts', 'prefix', 'type', collation, target)
CL_RE = re.compile(
    rb"\((\d+),'(?:[^'\\]|\\.)*','\d{4}-[^']*','(?:[^'\\]|\\.)*','(page|subcat|file)',(\d+),(\d+)\)")


def build_category_artifacts(catlinks_gz: str, linktarget_gz: str, parsed_dir: str,
                             graph_dir: str, meta_path: str | None = None,
                             chunk_bytes: int = 1 << 24):
    import pyarrow as pa
    import pyarrow.parquet as pq

    t0 = time.time()
    os.makedirs(graph_dir, exist_ok=True)
    article_ids = np.load(os.path.join(parsed_dir, "article_ids.npy"))
    n_art = len(article_ids)

    # ---- pass A: linktarget ns14 -> categories table + dense lt_id->cat_id
    cat_lt = GrowArr(np.int64)
    cat_titles = []
    counts = {"lt_rows": 0, "lt_ns14": 0}
    max_lt = 0
    for off, chunk in stream_decompressed(linktarget_gz, chunk_bytes):
        ids_chunk, titles_chunk = [], []
        for m in LT_RE.finditer(chunk):
            counts["lt_rows"] += 1
            if int(m.group(2)) != 14:
                continue
            counts["lt_ns14"] += 1
            lt_id = int(m.group(1))
            ids_chunk.append(lt_id)
            titles_chunk.append(unescape(m.group(3)).decode("utf-8", "replace"))
            if lt_id > max_lt:
                max_lt = lt_id
        if ids_chunk:
            cat_lt.extend(np.fromiter(ids_chunk, dtype=np.int64, count=len(ids_chunk)))
            cat_titles.extend(titles_chunk)
    lt_ids = cat_lt.result()
    K = len(lt_ids)
    dense = np.full(max_lt + 2, -1, dtype=np.int32)
    dense[lt_ids] = np.arange(K, dtype=np.int32)
    tbl = pa.table({
        "cat_id": pa.array(np.arange(K, dtype=np.int32)),
        "lt_id": pa.array(lt_ids),
        "title": pa.array(cat_titles, type=pa.string()),
    })
    pq.write_table(tbl, os.path.join(graph_dir, "categories.parquet"), compression="zstd")
    print(f"[categories] {K:,} category targets (lt ns14), max_lt={max_lt:,} "
          f"({time.time()-t0:.0f}s)", flush=True)

    # ---- pass B: categorylinks -> (article_idx, cat_id) pairs
    out_bin = os.path.join(graph_dir, "article_categories.bin")
    counters = {"rows": 0, "type_page": 0, "type_subcat": 0, "type_file": 0,
                "cl_from_not_article": 0, "target_not_category": 0, "pairs_kept": 0}
    CAP = 1 << 22
    buf_a = np.empty(CAP, np.int32)
    buf_c = np.empty(CAP, np.int32)
    buf_n = 0
    fout = open(out_bin, "wb")

    def flush():
        nonlocal buf_n
        if buf_n:
            out = np.empty(buf_n * 2, np.int32)
            out[0::2] = buf_a[:buf_n]
            out[1::2] = buf_c[:buf_n]
            out.tofile(fout)
            buf_n = 0

    for off, chunk in stream_decompressed(catlinks_gz, chunk_bytes):
        a_list, c_list = [], []
        for m in CL_RE.finditer(chunk):
            counters["rows"] += 1
            typ = m.group(2)
            if typ != b"page":
                counters["type_subcat" if typ == b"subcat" else "type_file"] += 1
                continue
            counters["type_page"] += 1
            tgt = int(m.group(4))
            if tgt >= len(dense) or dense[tgt] < 0:
                counters["target_not_category"] += 1
                continue
            pid = int(m.group(1))
            pos = np.searchsorted(article_ids, pid)
            if pos >= n_art or article_ids[min(pos, n_art - 1)] != pid:
                counters["cl_from_not_article"] += 1
                continue
            a_list.append(int(pos))
            c_list.append(int(dense[tgt]))
        if a_list:
            k = len(a_list)
            if buf_n + k > CAP:
                flush()
            buf_a[buf_n : buf_n + k] = np.fromiter(a_list, np.int32, k)
            buf_c[buf_n : buf_n + k] = np.fromiter(c_list, np.int32, k)
            buf_n += k
            counters["pairs_kept"] += k
        print(f"  catlinks: {counters['rows']:,} rows, kept={counters['pairs_kept']:,} "
              f"({time.time()-t0:.0f}s)", flush=True)
    flush()
    fout.close()

    meta = read_json(meta_path, {}) or {} if meta_path else {}
    meta["categorylinks"] = {**counters, "n_categories": K,
                             "secs": round(time.time() - t0, 1),
                             "out_bytes": os.path.getsize(out_bin)}
    if meta_path:
        write_json(meta_path, meta)
    else:
        write_json(os.path.join(graph_dir, "catlinks_meta.json"), meta)
    print(f"[catlinks] {counters} ({time.time()-t0:.0f}s)")
    return counters
