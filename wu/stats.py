"""memmap + チャンクパスで計算するフルグラフ統計(低 RAM)。

入出力ポリシー: graph/edges_ns0.bin + parsed/article_ids.npy を読み、
graph/indeg.npy、graph/outdeg.npy、graph/full_stats.json を書き出す。
"""
from __future__ import annotations

import os
import re
import time

import numpy as np

from .dumpio import write_json
from .paths import Dirs

HUB_PATTERNS = {
    "disambig": re.compile(r"\(曖昧さ回避\)|曖昧さ回避$"),
    "list": re.compile(r"一覧$|一覧_|^リスト|の一覧"),
    "year": re.compile(r"^\d{1,4}年$|^\d{1,4}年の|^\d{4}年度"),
    "date": re.compile(r"^\d{1,2}月\d{1,2}日$"),
    "jp_geo": re.compile(r"^日本の|^日本における"),
    "index": re.compile(r"^索引|^Wikipedia:|^Category:"),
}


def hub_flags(title: str) -> list:
    return [k for k, p in HUB_PATTERNS.items() if p.search(title)]


def load_edges_mmap(path: str) -> np.ndarray:
    n = os.path.getsize(path) // 8
    return np.memmap(path, dtype=np.int32, mode="r", shape=(n, 2))


def load_titles(parsed_dir: str):
    import pyarrow.parquet as pq
    tbl = pq.read_table(os.path.join(parsed_dir, "articles.parquet"), columns=["page_id", "title"])
    return tbl.column("page_id").to_numpy(), tbl.column("title")


def full_stats(dirs: Dirs, out_json: str | None = None, titles_needed: int = 200):
    t0 = time.time()
    out_json = out_json or dirs.full_stats
    E = load_edges_mmap(dirs.edges_bin)
    article_ids = np.load(dirs.article_ids)  # sorted page_ids
    n_nodes = len(article_ids)

    # remap page_id -> compact idx (all edge endpoints are articles by construction)
    outdeg = np.zeros(n_nodes, dtype=np.int64)
    indeg = np.zeros(n_nodes, dtype=np.int64)
    CH = 5_000_000
    n_edges = len(E)
    bad = 0
    for i in range(0, n_edges, CH):
        blk = np.asarray(E[i : i + CH]).astype(np.int64)
        s = np.searchsorted(article_ids, blk[:, 0])
        d = np.searchsorted(article_ids, blk[:, 1])
        sc = np.clip(s, 0, n_nodes - 1)
        dc = np.clip(d, 0, n_nodes - 1)
        ok = (article_ids[sc] == blk[:, 0]) & (article_ids[dc] == blk[:, 1])
        bad += int((~ok).sum())
        outdeg += np.bincount(sc[ok], minlength=n_nodes)
        indeg += np.bincount(dc[ok], minlength=n_nodes)

    deg = outdeg + indeg
    dirs.ensure(dirs.graph)
    np.save(dirs.indeg, indeg.astype(np.int32))
    np.save(dirs.outdeg, outdeg.astype(np.int32))
    top = np.argsort(deg)[::-1][:titles_needed]
    pid_col, title_col = load_titles(dirs.parsed)
    top_pid = article_ids[top]
    pos = np.clip(np.searchsorted(pid_col, top_pid), 0, len(pid_col) - 1)
    top_titles = [title_col[p].as_py() for p in pos]

    top_rows = []
    for rank, (p, t, i) in enumerate(zip(top_pid.tolist(), top_titles, top.tolist())):
        top_rows.append({
            "rank": rank + 1, "page_id": p, "title": t,
            "in": int(indeg[i]), "out": int(outdeg[i]), "total": int(deg[i]),
            "flags": hub_flags(t),
        })

    pct = [50, 75, 90, 95, 99, 99.9]
    stats = {
        "n_articles": n_nodes,
        "n_edges_directed_resolved": int(n_edges),
        "endpoint_remap_failures": bad,
        "avg_out_degree": float(n_edges / n_nodes),
        "degree_percentiles": {f"p{p}": float(np.percentile(deg, p)) for p in pct},
        "max_degree": int(deg.max()),
        "n_isolated_articles": int((deg == 0).sum()),
        "edge_share_top0.1pct_nodes": float(np.sort(deg)[::-1][: max(1, n_nodes // 1000)].sum() / max(1, deg.sum())),
        "edge_share_top1pct_nodes": float(np.sort(deg)[::-1][: max(1, n_nodes // 100)].sum() / max(1, deg.sum())),
        "secs": round(time.time() - t0, 1),
    }
    write_json(out_json, {"stats": stats, "top_hubs": top_rows[:60]})
    print(f"[stats] edges={n_edges:,} nodes={n_nodes:,} avg_deg={stats['avg_out_degree']:.1f} "
          f"max_deg={stats['max_degree']:,} in {stats['secs']}s -> {out_json}")
    return stats, top_rows
