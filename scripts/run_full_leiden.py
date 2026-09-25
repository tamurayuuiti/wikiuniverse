"""Full-graph Leiden pipeline for the complete jawiki ns0 graph.

Subcommands (run in order; each stage persists to disk so the pipeline is
resumable — `all` runs everything):

  dedup    graph/edges_ns0.bin (directed, page_id)
             -> graph/edges_undirected_unique.bin (undirected unique, compact idx)
  detect   build igraph in memory-bounded batches -> native C Leiden
             -> community/full/membership_res<R>.npy  (int32, per compact idx)
  metrics  chunked per-community + global metrics for one resolution
             -> community/full/metrics_res<R>.json / per_community_res<R>.parquet
                / top_pairs_res<R>.json / pairs_res<R>.npz
  cluster  level-2 (galaxy clusters) from the weighted community graph
             -> community/full/clusters_comm_res<R>.npy + clusters_meta_res<R>.json
  export   page_id/title/comm/cluster join -> community/full/membership_res<R>.parquet

Memory budget (measured scaling, 32GB machine):
  dedup   peak ~4 GB   (packed int64 keys + np.unique sort)
  detect  ~7-9 GB RSS  (igraph ~58B/edge for ~105M undirected edges + batches)
  metrics ~2 GB        (chunked passes over the edge file)
Smoke test before the long run:  detect --max-edges 5000000

Usage:
  python scripts/run_full_leiden.py --base data dedup
  python scripts/run_full_leiden.py --base data detect --resolutions 0.5,1.0,2.0
  python scripts/run_full_leiden.py --base data metrics --resolution 1.0
  python scripts/run_full_leiden.py --base data cluster --resolution 1.0
  python scripts/run_full_leiden.py --base data export  --resolution 1.0
  (or) python scripts/run_full_leiden.py --base data all --resolutions 0.5,1.0,2.0 --primary 1.0
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.dumpio import read_json, write_json  # noqa: E402
from wu.paths import Dirs  # noqa: E402
from wu.stats import load_edges_mmap, load_titles  # noqa: E402

SEED = 42
SHIFT = 21  # compact idx < 2^21 = 2,097,152  (jawiki: 1,516,326 articles)


def _safe_stdout():
    """Avoid UnicodeEncodeError on Windows cmd (cp932) when printing titles."""
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _res_tag(res: float) -> str:
    return f"{res:g}"


def _full_dir(dirs: Dirs) -> str:
    d = os.path.join(dirs.community, "full")
    os.makedirs(d, exist_ok=True)
    return d


def _versions() -> dict:
    import igraph
    import platform
    return {"python": platform.python_version(), "igraph": igraph.__version__,
            "numpy": np.__version__, "platform": platform.platform()}


def _load_article_ids(dirs: Dirs) -> np.ndarray:
    return np.load(dirs.article_ids)  # sorted page_ids; position == compact idx


# ------------------------------------------------------------------- dedup --

def cmd_dedup(dirs: Dirs, chunk: int = 10_000_000):
    t0 = time.time()
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    if n >= (1 << SHIFT):
        raise RuntimeError(f"too many articles ({n}) for SHIFT={SHIFT}; bump SHIFT")
    E = load_edges_mmap(dirs.edges_bin)
    m = len(E)
    keys = np.empty(m, dtype=np.int64)
    bad = 0
    for i in range(0, m, chunk):
        blk = np.asarray(E[i : i + chunk]).astype(np.int64)
        s = np.clip(np.searchsorted(article_ids, blk[:, 0]), 0, n - 1)
        d = np.clip(np.searchsorted(article_ids, blk[:, 1]), 0, n - 1)
        ok = (article_ids[s] == blk[:, 0]) & (article_ids[d] == blk[:, 1])
        bad += int((~ok).sum())
        s, d = s[ok], d[ok]
        lo = np.minimum(s, d)
        hi = np.maximum(s, d)
        keys[i : i + len(lo)] = (lo << SHIFT) | hi
        if len(lo) < len(blk):
            keys[i + len(lo) : i + len(blk)] = -1  # invalid marker
        print(f"  remap {i + len(blk):,}/{m:,} ({time.time()-t0:.0f}s)", flush=True)
    keys = keys[keys >= 0]
    mask = (keys & ((1 << SHIFT) - 1)) != (keys >> SHIFT)  # drop self loops (none expected)
    keys = keys[mask]
    print(f"[dedup] sorting {len(keys):,} keys ...", flush=True)
    uq = np.unique(keys)
    del keys
    out_path = os.path.join(dirs.graph, "edges_undirected_unique.bin")
    out = np.empty(len(uq) * 2, dtype=np.int32)
    out[0::2] = (uq >> SHIFT).astype(np.int32)
    out[1::2] = (uq & ((1 << SHIFT) - 1)).astype(np.int32)
    out.tofile(out_path)
    meta = {
        "stage": "dedup", "created_at": _now(),
        "n_articles": n, "n_directed_edges": m,
        "n_endpoint_remap_failures": bad, "n_self_loops_dropped": int((~mask).sum()),
        "n_undirected_unique_edges": int(len(uq)),
        "reciprocity_implied": float(1.0 - len(uq) / max(1, m - bad)) if m else None,
        "shift": SHIFT, "secs": round(time.time() - t0, 1),
        "out_file": out_path, "out_bytes": os.path.getsize(out_path),
        **_versions(),
    }
    write_json(os.path.join(dirs.graph, "dedup_meta.json"), meta)
    print(f"[dedup] {m:,} directed -> {len(uq):,} undirected unique "
          f"({meta['secs']}s) -> {out_path}")
    return meta


def _load_undirected(dirs: Dirs) -> np.ndarray:
    p = os.path.join(dirs.graph, "edges_undirected_unique.bin")
    if not os.path.exists(p):
        sys.exit("edges_undirected_unique.bin not found - run `dedup` first")
    cnt = os.path.getsize(p) // 8
    return np.memmap(p, dtype=np.int32, mode="r", shape=(cnt, 2))


# ------------------------------------------------------------------ detect --

def cmd_detect(dirs: Dirs, resolutions, max_edges: int = 0, seed: int = SEED,
               force: bool = False, batch: int = 300_000, n_iterations: int = 3):
    import igraph as ig

    outdir = _full_dir(dirs)
    E = _load_undirected(dirs)
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    todo = []
    for res in resolutions:
        tag = _res_tag(res)
        p = os.path.join(outdir, f"membership_res{tag}.npy")
        if os.path.exists(p) and not force:
            print(f"[detect] res={tag}: exists, skip (--force to redo)")
            continue
        todo.append((res, tag, p))
    if not todo:
        return
    total = len(E) if not max_edges else min(max_edges, len(E))
    if max_edges:
        print(f"[detect] SMOKE MODE: using first {total:,} of {len(E):,} edges")
    t0 = time.time()
    g = ig.Graph(n=n)
    for i in range(0, total, batch):
        blk = np.asarray(E[i : min(i + batch, total)])
        g.add_edges(list(zip(blk[:, 0].tolist(), blk[:, 1].tolist())))
        if (i // batch) % 20 == 0:
            print(f"  igraph build {i + len(blk):,}/{total:,} edges ({time.time()-t0:.0f}s)", flush=True)
    print(f"[detect] graph built: {g.vcount():,} nodes {g.ecount():,} edges "
          f"({time.time()-t0:.0f}s)", flush=True)

    detect_meta_path = os.path.join(outdir, "detect_meta.json")
    meta = read_json(detect_meta_path, {}) or {"runs": {}}
    for res, tag, p in todo:
        t1 = time.time()
        random.seed(seed)
        cl = g.community_leiden("modularity", resolution=res, n_iterations=n_iterations)
        memb = np.asarray(cl.membership, dtype=np.int32)
        np.save(p, memb)
        sizes = np.bincount(memb)
        info = {
            "resolution": res, "seed": seed, "n_iterations": n_iterations,
            "objective": "modularity", "n_nodes": int(n), "n_edges_used": int(total),
            "smoke": bool(max_edges),
            "n_communities": int(len(cl)), "modularity": float(cl.modularity),
            "quality": float(cl.quality),
            "largest_comm_share": float(sizes.max() / n),
            "top5_comm_share": float(np.sort(sizes)[::-1][:5].sum() / n),
            "median_comm_size": float(np.median(sizes[sizes > 0])),
            "secs": round(time.time() - t1, 1), "finished_at": _now(),
        }
        meta["runs"][tag] = info
        print(f"[detect] res={tag}: C={info['n_communities']:,} "
              f"mod={info['modularity']:.4f} largest={info['largest_comm_share']:.3f} "
              f"median={info['median_comm_size']:.0f} ({info['secs']}s)")
    meta.update({"graph": {"n_nodes": int(n), "n_edges_total": int(len(E))},
                 "versions": _versions(), "updated_at": _now()})
    write_json(detect_meta_path, meta)
    del g


# ----------------------------------------------------------------- metrics --

def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    m = ~np.isnan(values) & (weights > 0)
    if not m.any():
        return float("nan")
    vals, w = values[m], weights[m].astype(float)
    o = np.argsort(vals)
    vals, w = vals[o], w[o]
    cw = np.cumsum(w)
    return float(vals[np.searchsorted(cw, cw[-1] / 2)])


def cmd_metrics(dirs: Dirs, res: float, chunk: int = 4_000_000):
    t0 = time.time()
    outdir = _full_dir(dirs)
    tag = _res_tag(res)
    memb_path = os.path.join(outdir, f"membership_res{tag}.npy")
    if not os.path.exists(memb_path):
        sys.exit(f"membership_res{tag}.npy not found - run `detect` first")
    memb = np.load(memb_path)
    E = _load_undirected(dirs)
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    C = int(memb.max()) + 1
    print(f"[metrics] res={tag}: n={n:,} C={C:,} edges={len(E):,}")

    e_in = np.zeros(C, np.int64)
    e_out = np.zeros(C, np.int64)
    n_c = np.bincount(memb, minlength=C).astype(np.int64)
    deg = np.zeros(n, np.int64)
    b_inter = np.zeros(n, bool)
    pair_keys_chunks, pair_cnts_chunks = [], []
    total_edges = 0
    total_cross = 0
    for i in range(0, len(E), chunk):
        blk = np.asarray(E[i : i + chunk])
        u, v = blk[:, 0].astype(np.int64), blk[:, 1].astype(np.int64)
        total_edges += len(blk)
        deg += np.bincount(u, minlength=n)
        deg += np.bincount(v, minlength=n)
        mu, mv = memb[u], memb[v]
        same = mu == mv
        e_in += np.bincount(mu[same], minlength=C)
        total_cross += int((~same).sum())
        if (~same).any():
            e_out += np.bincount(mu[~same], minlength=C)
            e_out += np.bincount(mv[~same], minlength=C)
            b_inter[u[~same]] = True
            b_inter[v[~same]] = True
            a = np.minimum(mu[~same], mv[~same]).astype(np.int64)
            b = np.maximum(mu[~same], mv[~same]).astype(np.int64)
            uk, uc = np.unique(a * C + b, return_counts=True)
            pair_keys_chunks.append(uk)
            pair_cnts_chunks.append(uc)
        if (i // chunk) % 10 == 0:
            print(f"  metrics {i + len(blk):,}/{len(E):,} ({time.time()-t0:.0f}s)", flush=True)

    # merge pair tables
    if pair_keys_chunks:
        all_k = np.concatenate(pair_keys_chunks)
        all_c = np.concatenate(pair_cnts_chunks)
        del pair_keys_chunks, pair_cnts_chunks
        uq, inv = np.unique(all_k, return_inverse=True)
        pair_w = np.bincount(inv, weights=all_c).astype(np.int64)
        pair_a = (uq // C).astype(np.int32)
        pair_b = (uq % C).astype(np.int32)
        del all_k, all_c, uq, inv
    else:
        pair_a = pair_b = np.zeros(0, np.int32)
        pair_w = np.zeros(0, np.int64)
    np.savez_compressed(os.path.join(outdir, f"pairs_res{tag}.npz"),
                        a=pair_a, b=pair_b, w=pair_w)

    bn_inter = np.bincount(memb[b_inter], minlength=C).astype(np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        out_ratio = np.where(e_in + e_out > 0, e_out / np.maximum(1, e_in + e_out), np.nan)
        conduct = np.where(2 * e_in + e_out > 0, e_out / np.maximum(1, 2 * e_in + e_out), np.nan)
        avg_deg = 2 * e_in / np.maximum(1, n_c)

    sizes_nz = n_c[n_c > 0]
    glob = {
        "resolution": res, "seed": SEED, "generated_at": _now(),
        "n_nodes": n, "n_communities": C,
        "n_communities_nonempty": int((n_c > 0).sum()),
        "n_edges_undirected": total_edges,
        "n_cross_community_edges": total_cross,
        "cross_edge_fraction": float(total_cross / max(1, total_edges)),
        "largest_comm_share": float(n_c.max() / n),
        "top5_comm_share": float(np.sort(n_c)[::-1][:5].sum() / n),
        "mean_out_ratio_edge_weighted": float(e_out.sum() / max(1, e_in.sum() + e_out.sum())),
        "node_weighted_median_out_ratio": _weighted_median(out_ratio, n_c),
        "size_percentiles": {f"p{p}": float(np.percentile(sizes_nz, p)) for p in (5, 25, 50, 75, 95, 99)},
        "size_max": int(sizes_nz.max()),
        "out_ratio_percentiles": {f"p{p}": float(np.nanpercentile(out_ratio, p)) for p in (10, 25, 50, 75, 90)},
        "conductance_percentiles": {f"p{p}": float(np.nanpercentile(conduct, p)) for p in (10, 25, 50, 75, 90)},
        "boundary_node_ratio_node_weighted": float(b_inter.sum() / n),
        "avg_degree_full": float(2 * total_edges / n),
        "secs": round(time.time() - t0, 1),
    }

    # representative labels: top-3 nodes by internal degree per community (vectorized)
    pid_col, title_col = load_titles(dirs.parsed)
    order = np.lexsort((-deg, memb))  # within each comm: descending degree
    starts = np.searchsorted(memb[order], np.arange(C), side="left")
    rep_idx = []
    rep_comm = []
    for c in range(C):
        if n_c[c] == 0:
            continue
        k = min(3, int(n_c[c]))
        rep_idx.extend(order[starts[c] : starts[c] + k].tolist())
        rep_comm.extend([c] * k)
    rep_idx = np.asarray(rep_idx, dtype=np.int64)
    rep_pid = article_ids[rep_idx]
    pos = np.clip(np.searchsorted(pid_col, rep_pid), 0, len(pid_col) - 1)
    rep_titles = [title_col[p].as_py() for p in pos]
    labels = {}
    for c, t in zip(rep_comm, rep_titles):
        labels.setdefault(c, []).append(t)
    labels = {c: labels.get(c, []) for c in range(C)}

    # per-community parquet
    import pyarrow as pa
    import pyarrow.parquet as pq
    tbl = pa.table({
        "comm": pa.array(np.arange(C, dtype=np.int32)),
        "N_c": pa.array(n_c),
        "E_in_c": pa.array(e_in),
        "E_out_c": pa.array(e_out),
        "out_ratio_c": pa.array(out_ratio, type=pa.float64()),
        "conductance_c": pa.array(conduct, type=pa.float64()),
        "avg_degree_c": pa.array(avg_deg, type=pa.float64()),
        "boundary_nodes_c": pa.array(bn_inter),
        "boundary_node_ratio_c": pa.array(bn_inter / np.maximum(1, n_c), type=pa.float64()),
        "rep_titles": pa.array([", ".join(labels[c][:3]) for c in range(C)], type=pa.string()),
    })
    pq.write_table(tbl, os.path.join(outdir, f"per_community_res{tag}.parquet"), compression="zstd")

    # top pairs
    topk = np.argsort(pair_w)[::-1][:50]
    top_pairs = [{"comm_a": int(pair_a[k]), "comm_b": int(pair_b[k]), "edges": int(pair_w[k]),
                  "a_labels": labels.get(int(pair_a[k]), [])[:2],
                  "b_labels": labels.get(int(pair_b[k]), [])[:2]} for k in topk]
    write_json(os.path.join(outdir, f"top_pairs_res{tag}.json"), top_pairs)

    write_json(os.path.join(outdir, f"metrics_res{tag}.json"), {"global": glob, "versions": _versions()})
    print(f"[metrics] res={tag}: C={C:,} crossF={glob['cross_edge_fraction']:.4f} "
          f"mod-largest={glob['largest_comm_share']:.3f} oR_p50="
          f"{glob['out_ratio_percentiles']['p50']:.3f} ({glob['secs']}s)")
    return glob


# ----------------------------------------------------------------- cluster --

def cmd_cluster(dirs: Dirs, res: float, cluster_resolution: float = 1.0, seed: int = SEED):
    import igraph as ig

    t0 = time.time()
    outdir = _full_dir(dirs)
    tag = _res_tag(res)
    memb = np.load(os.path.join(outdir, f"membership_res{tag}.npy"))
    pz = np.load(os.path.join(outdir, f"pairs_res{tag}.npz"))
    a, b, w = pz["a"], pz["b"], pz["w"]
    C = int(memb.max()) + 1
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    if len(a) == 0:
        sys.exit("empty pair table")
    g = ig.Graph(n=C, edges=list(zip(a.tolist(), b.tolist())), directed=False)
    g.es["weight"] = w.astype(np.float64).tolist()
    random.seed(seed)
    cl = g.community_leiden("modularity", weights="weight",
                            resolution=cluster_resolution, n_iterations=3)
    cl_comm = np.asarray(cl.membership, dtype=np.int32)   # community -> cluster
    K = int(cl_comm.max()) + 1
    cl_node = cl_comm[memb]                                # node -> cluster
    np.save(os.path.join(outdir, f"clusters_comm_res{tag}.npy"), cl_comm)
    np.save(os.path.join(outdir, f"clusters_node_res{tag}.npy"), cl_node)

    # level-2 metrics
    import pyarrow.parquet as pq
    tbl = pq.read_table(os.path.join(outdir, f"per_community_res{tag}.parquet")).to_pydict()
    n_c = np.asarray(tbl["N_c"], np.int64)
    e_in_c = np.asarray(tbl["E_in_c"], np.int64)

    N2 = np.bincount(cl_comm, weights=n_c, minlength=K).astype(np.int64)
    E_in2 = np.bincount(cl_comm, weights=e_in_c, minlength=K)
    E_between2 = np.zeros(K)
    E_out2 = np.zeros(K)
    ca, cb = cl_comm[a], cl_comm[b]
    same = ca == cb
    np.add.at(E_between2, ca[same], w[same])
    np.add.at(E_out2, ca[~same], w[~same])
    np.add.at(E_out2, cb[~same], w[~same])
    with np.errstate(divide="ignore", invalid="ignore"):
        ein2_total = E_in2 + E_between2
        out_ratio2 = np.where(ein2_total + E_out2 > 0, E_out2 / np.maximum(1, ein2_total + E_out2), np.nan)
    glob = {
        "resolution_l1": res, "resolution_l2": cluster_resolution, "seed": seed,
        "generated_at": _now(), "n_clusters": K, "n_communities": C,
        "modularity_community_graph": float(cl.modularity),
        "largest_cluster_share": float(N2.max() / n),
        "top5_cluster_share": float(np.sort(N2)[::-1][:5].sum() / n),
        "mean_out_ratio2_edge_weighted": float(E_out2.sum() / max(1e-9, ein2_total.sum() + E_out2.sum())),
        "cluster_size_nodes_percentiles": {f"p{p}": float(np.percentile(N2[N2 > 0], p))
                                           for p in (25, 50, 75, 95)},
        "secs": round(time.time() - t0, 1),
    }
    rows = []
    for k in range(K):
        rows.append({"cluster": k, "n_comms": int(np.bincount(cl_comm, minlength=K)[k]),
                     "N_nodes": int(N2[k]), "E_in_articles": float(E_in2[k]),
                     "E_between_comms": float(E_between2[k]), "E_out2": float(E_out2[k]),
                     "out_ratio2": float(out_ratio2[k]) if not np.isnan(out_ratio2[k]) else None})
    write_json(os.path.join(outdir, f"clusters_meta_res{tag}.json"),
               {"global": glob, "clusters": rows, "versions": _versions()})
    print(f"[cluster] res={tag}: K={K:,} largest={glob['largest_cluster_share']:.3f} "
          f"modL2={glob['modularity_community_graph']:.4f} "
          f"oR2_w={glob['mean_out_ratio2_edge_weighted']:.3f} ({glob['secs']}s)")
    return glob


# ------------------------------------------------------------------ export --

def cmd_export(dirs: Dirs, res: float, with_clusters: bool = True):
    import pyarrow as pa
    import pyarrow.parquet as pq

    t0 = time.time()
    outdir = _full_dir(dirs)
    tag = _res_tag(res)
    memb = np.load(os.path.join(outdir, f"membership_res{tag}.npy"))
    cl_path = os.path.join(outdir, f"clusters_node_res{tag}.npy")
    cl_node = np.load(cl_path) if (with_clusters and os.path.exists(cl_path)) else None
    article_ids = _load_article_ids(dirs)
    pid_col, title_col = load_titles(dirs.parsed)
    n = len(article_ids)
    pos = np.clip(np.searchsorted(pid_col, article_ids), 0, len(pid_col) - 1)
    titles = pa.array([title_col[p].as_py() for p in pos], type=pa.string())
    cols = {
        "idx": pa.array(np.arange(n, dtype=np.int32)),
        "page_id": pa.array(article_ids),
        "title": titles,
        "comm": pa.array(memb),
    }
    if cl_node is not None:
        cols["cluster"] = pa.array(cl_node)
    out = os.path.join(outdir, f"membership_res{tag}.parquet")
    pq.write_table(pa.table(cols), out, compression="zstd")
    print(f"[export] {n:,} rows -> {out} ({time.time()-t0:.0f}s, "
          f"{os.path.getsize(out)/1e6:.1f} MB)")


# --------------------------------------------------------------------- all --

def cmd_all(dirs: Dirs, resolutions, primary: float, max_edges: int, seed: int):
    if not os.path.exists(os.path.join(dirs.graph, "edges_undirected_unique.bin")):
        cmd_dedup(dirs)
    else:
        print("[all] dedup: exists, skip")
    cmd_detect(dirs, resolutions, max_edges=max_edges, seed=seed)
    if max_edges:
        print("[all] smoke mode: skipping metrics/cluster/export")
        return
    cmd_metrics(dirs, primary)
    cmd_cluster(dirs, primary)
    cmd_export(dirs, primary)


def main():
    _safe_stdout()
    ap = argparse.ArgumentParser(prog="run_full_leiden")
    ap.add_argument("--base", default="data")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("dedup"); p.add_argument("--chunk", type=int, default=10_000_000)
    p = sub.add_parser("detect")
    p.add_argument("--resolutions", default="0.5,1.0,2.0")
    p.add_argument("--max-edges", type=int, default=0, help="smoke: only first N edges")
    p.add_argument("--seed", type=int, default=SEED)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("metrics"); p.add_argument("--resolution", type=float, required=True)
    p = sub.add_parser("cluster"); p.add_argument("--resolution", type=float, required=True)
    p.add_argument("--cluster-resolution", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=SEED)
    p = sub.add_parser("export"); p.add_argument("--resolution", type=float, required=True)
    p = sub.add_parser("all")
    p.add_argument("--resolutions", default="0.5,1.0,2.0")
    p.add_argument("--primary", type=float, default=1.0)
    p.add_argument("--max-edges", type=int, default=0)
    p.add_argument("--seed", type=int, default=SEED)

    a = ap.parse_args()
    dirs = Dirs(a.base)
    if a.cmd == "dedup":
        cmd_dedup(dirs, chunk=a.chunk)
    elif a.cmd == "detect":
        cmd_detect(dirs, [float(x) for x in a.resolutions.split(",")],
                   max_edges=a.max_edges, seed=a.seed, force=a.force)
    elif a.cmd == "metrics":
        cmd_metrics(dirs, a.resolution)
    elif a.cmd == "cluster":
        cmd_cluster(dirs, a.resolution, cluster_resolution=a.cluster_resolution, seed=a.seed)
    elif a.cmd == "export":
        cmd_export(dirs, a.resolution)
    elif a.cmd == "all":
        cmd_all(dirs, [float(x) for x in a.resolutions.split(",")],
                primary=a.primary, max_edges=a.max_edges, seed=a.seed)


if __name__ == "__main__":
    main()
