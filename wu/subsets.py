"""Subset extraction from the full resolved edge list (chunked scans, low RAM).

Strategies (recorded in subset_meta.json):
  * id_window : contiguous window of page_id order (contrast case; leaks ~95%)
  * bfs       : k-hop out-link BFS from seed articles with
                - per-node out-link sampling cap
                - mega-hub exclusion: discovered nodes with full-graph in-degree
                  > max_in_degree are NOT added to the subset (seeds exempt).
                  Rationale: template/year/infobox hubs act as "intergalactic
                  medium", not galaxy members; including them collapses the
                  subset into one blob and explodes boundary traffic.

Outputs per subset dir:
  nodes.parquet        page_id, idx (compact), title
  edges_internal.npy   int32 (n,2) compact idx pairs (directed, reciprocals kept)
  node_stats.npz       ext_out (directed links to outside), ext_in, boundary_any
  subset_meta.json     counts, params, provenance

Layout policy: subsets live under <base>/graph/subsets/<name>/.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .dumpio import write_json
from .stats import load_edges_mmap


def _titles_for(parsed_dir: str, pids: np.ndarray):
    tbl = pq.read_table(os.path.join(parsed_dir, "articles.parquet"), columns=["page_id", "title"])
    pid_col = tbl.column("page_id").to_numpy()
    title_col = tbl.column("title")
    pos = np.clip(np.searchsorted(pid_col, pids), 0, len(pid_col) - 1)
    return [title_col[p].as_py() for p in pos]


def select_id_window(parsed_dir: str, k: int, mode: str = "late"):
    ids = np.load(os.path.join(parsed_dir, "article_ids.npy"))  # sorted page_ids
    if mode == "late":
        return ids[-k:].copy()
    elif mode == "early":
        return ids[:k].copy()
    else:
        m = len(ids) // 2
        return ids[m - k // 2 : m - k // 2 + k].copy()


def title_to_pid(parsed_dir: str, title: str) -> int | None:
    from .sqlparse import h64

    z = np.load(os.path.join(parsed_dir, "ns0_all_hashes.npz"))
    hv = np.uint64(h64(title.replace(" ", "_").encode("utf-8")))
    pos = np.searchsorted(z["hashes"], hv)
    if pos < len(z["hashes"]) and z["hashes"][pos] == hv:
        return int(z["page_ids"][pos])
    return None


def _pid_to_idx(parsed_dir: str, pids: np.ndarray) -> np.ndarray:
    article_ids = np.load(os.path.join(parsed_dir, "article_ids.npy"))
    return np.clip(np.searchsorted(article_ids, pids), 0, len(article_ids) - 1).astype(np.int64)


def bfs_nodes(edges_bin: str, seed_pids, max_nodes: int, cap_per_node: int = 40,
              max_hops: int = 4, rng_seed: int = 42, parsed_dir: str | None = None,
              graph_dir: str | None = None, max_in_degree: int | None = 10000):
    """Out-link BFS with per-node sampling cap and mega-hub exclusion."""
    rng = np.random.default_rng(rng_seed)
    E = load_edges_mmap(edges_bin)

    indeg = None
    article_ids = None
    if parsed_dir and graph_dir and max_in_degree:
        p = os.path.join(graph_dir, "indeg.npy")
        if os.path.exists(p):
            indeg = np.load(p)
            article_ids = np.load(os.path.join(parsed_dir, "article_ids.npy"))

    visited = set(int(s) for s in seed_pids)
    frontier = np.array(sorted(visited), dtype=np.int64)
    info = {"hops": [], "excluded_hubs": 0, "max_in_degree": max_in_degree}
    for hop in range(max_hops):
        if len(frontier) == 0 or len(visited) >= max_nodes:
            break
        fs = np.sort(frontier)
        pairs_src, pairs_dst = [], []
        CH = 4_000_000
        for i in range(0, len(E), CH):
            blk = np.asarray(E[i : i + CH]).astype(np.int64)
            pos = np.searchsorted(fs, blk[:, 0])
            pc = np.clip(pos, 0, len(fs) - 1)
            m = fs[pc] == blk[:, 0]
            if m.any():
                pairs_src.append(blk[m, 0])
                pairs_dst.append(blk[m, 1])
        if not pairs_src:
            break
        src = np.concatenate(pairs_src)
        dst = np.concatenate(pairs_dst)
        del pairs_src, pairs_dst
        # cap per source: shuffle, then keep first `cap` per src group
        order = rng.permutation(len(src))
        src, dst = src[order], dst[order]
        uniq, inv = np.unique(src, return_inverse=True)
        o2 = np.argsort(inv, kind="stable")
        inv_sorted = inv[o2]
        starts = np.searchsorted(inv_sorted, np.arange(len(uniq)), side="left")
        within = np.arange(len(src)) - starts[inv_sorted]
        rank = np.empty(len(src), dtype=np.int64)
        rank[o2] = within
        m = rank < cap_per_node
        dst = dst[m]
        # mega-hub exclusion (applies to discovered nodes, not seeds)
        if indeg is not None and len(dst):
            pos = np.clip(np.searchsorted(article_ids, dst), 0, len(article_ids) - 1)
            deg = indeg[pos]
            ok = deg <= max_in_degree
            info["excluded_hubs"] += int((~ok).sum())
            dst = dst[ok]
        # new nodes not yet visited
        vs = np.fromiter(visited, dtype=np.int64, count=len(visited))
        vs.sort()
        pos = np.searchsorted(vs, dst)
        pc = np.clip(pos, 0, len(vs) - 1)
        not_in = ~((pos < len(vs)) & (vs[pc] == dst))
        new = np.unique(dst[not_in])
        room = max_nodes - len(visited)
        if len(new) > room:
            new = np.sort(new[rng.permutation(len(new))[:room]])
        visited.update(new.tolist())
        info["hops"].append({"hop": hop + 1, "frontier": int(len(frontier)),
                             "new_nodes": int(len(new)), "total": len(visited)})
        frontier = new
    return np.array(sorted(visited), dtype=np.int64), info


def extract_subset(edges_bin: str, node_pids: np.ndarray, out_dir: str, meta: dict,
                   parsed_dir: str | None = None):
    """One full scan: collect internal edges; count per-node external traffic."""
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    nodes = np.sort(np.unique(node_pids)).astype(np.int64)
    n = len(nodes)
    E = load_edges_mmap(edges_bin)
    CH = 4_000_000
    ii_s, ii_d = [], []
    ext_out = np.zeros(n, np.int32)
    ext_in = np.zeros(n, np.int32)
    bnd_any = np.zeros(n, bool)
    n_int = n_out = n_in = 0
    for i in range(0, len(E), CH):
        blk = np.asarray(E[i : i + CH]).astype(np.int64)
        s, d = blk[:, 0], blk[:, 1]
        ps = np.searchsorted(nodes, s)
        pds = np.searchsorted(nodes, d)
        ms = (ps < n) & (nodes[np.clip(ps, 0, n - 1)] == s)
        md = (pds < n) & (nodes[np.clip(pds, 0, n - 1)] == d)
        both = ms & md
        if both.any():
            ii_s.append(ps[both].astype(np.int32))
            ii_d.append(pds[both].astype(np.int32))
            n_int += int(both.sum())
        only_s = ms & ~md
        if only_s.any():
            np.add.at(ext_out, ps[only_s], 1)
            bnd_any[ps[only_s]] = True
            n_out += int(only_s.sum())
        only_d = md & ~ms
        if only_d.any():
            np.add.at(ext_in, pds[only_d], 1)
            bnd_any[pds[only_d]] = True
            n_in += int(only_d.sum())

    internal = np.stack([np.concatenate(ii_s), np.concatenate(ii_d)], axis=1).astype(np.int32) \
        if n_int else np.zeros((0, 2), np.int32)
    np.save(os.path.join(out_dir, "edges_internal.npy"), internal)
    np.savez(os.path.join(out_dir, "node_stats.npz"),
             ext_out=ext_out, ext_in=ext_in, boundary_any=bnd_any)

    titles = _titles_for(parsed_dir, nodes) if parsed_dir else [""] * n
    tbl = pa.table({
        "page_id": pa.array(nodes, type=pa.int64()),
        "idx": pa.array(np.arange(n, dtype=np.int32), type=pa.int32()),
        "title": pa.array(titles, type=pa.string()),
    })
    pq.write_table(tbl, os.path.join(out_dir, "nodes.parquet"), compression="zstd")

    meta = dict(meta)
    meta.update({
        "n_nodes": n,
        "n_edges_internal_directed": n_int,
        "n_edges_outgoing": n_out,
        "n_edges_incoming": n_in,
        "leak_ratio_out": float(n_out / max(1, n_int + n_out)),
        "boundary_any_nodes": int(bnd_any.sum()),
        "boundary_any_ratio": float(bnd_any.sum() / max(1, n)),
        "build_secs": round(time.time() - t0, 1),
        "page_id_min": int(nodes.min()), "page_id_max": int(nodes.max()),
    })
    write_json(os.path.join(out_dir, "subset_meta.json"), meta)
    print(f"[subset] {out_dir}: nodes={n:,} internal={n_int:,} "
          f"out={n_out:,} in={n_in:,} leak_out={meta['leak_ratio_out']:.3f} ({meta['build_secs']}s)")
    return meta
