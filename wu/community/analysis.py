"""Community detection + independence metrics + hierarchy test.

Metric definitions (matching the PoC spec):
  N_c        nodes in community c
  E_in_c     undirected unique edges with both ends in c
  E_out_c    undirected unique edges with exactly one end in c (other end in
             another community of the same subset)   [inter-community cut]
  out_ratio_c       = E_out_c / (E_in_c + E_out_c)
  conductance_c     = E_out_c / (2*E_in_c + E_out_c)          (= cut / vol(c))
  avg_degree_c      = 2*E_in_c / N_c
  boundary_nodes_c  = nodes of c having >=1 edge leaving c (inter-community)
  boundary_node_ratio_c = boundary_nodes_c / N_c
  E_ext_c    undirected unique links between nodes of c and nodes OUTSIDE the
             subset (boundary pairs, deduped across directions)  [leakage]
  boundary_any_nodes_c = nodes of c with inter-community OR outside-subset links

All "edge counts" are unique undirected article links unless noted.
"""
from __future__ import annotations

import os
import time

import ctypes
import gc

import igraph as ig
import leidenalg
import numpy as np

try:
    _LIBC = ctypes.CDLL("libc.so.6")
except OSError:  # non-linux
    _LIBC = None


def trim():
    """Return freed heap pages to the OS (critical in low-RAM sandboxes)."""
    gc.collect()
    if _LIBC is not None:
        _LIBC.malloc_trim(0)
import pyarrow as pa
import pyarrow.parquet as pq

from ..dumpio import read_json, write_json
from ..stats import hub_flags

SEED = 42


# ------------------------------------------------------------------ loading --

def load_subset(subset_dir: str):
    nodes = pq.read_table(os.path.join(subset_dir, "nodes.parquet")).to_pydict()
    n = len(nodes["idx"])
    internal = np.load(os.path.join(subset_dir, "edges_internal.npy"))
    ns = np.load(os.path.join(subset_dir, "node_stats.npz"))
    node_stats = {"ext_out": ns["ext_out"].astype(np.int64),
                  "ext_in": ns["ext_in"].astype(np.int64),
                  "boundary_any": ns["boundary_any"]}
    return n, nodes, internal, node_stats


def undirected_unique(internal: np.ndarray) -> np.ndarray:
    """Dedupe directed pairs into unique undirected int32 edges (low RAM)."""
    if len(internal) == 0:
        return np.zeros((0, 2), np.int32)
    a = internal[:, 0]
    b = internal[:, 1]
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    del a, b
    mx = int(max(lo.max(), hi.max())) + 1
    if mx <= (1 << 20):  # n <= ~1M: pack into int64 (shift 20 bits)
        keys = lo.astype(np.int64)
        keys <<= 20
        keys |= hi
        del lo, hi
        uq = np.unique(keys)
        del keys
        out = np.empty((len(uq), 2), np.int32)
        out[:, 0] = (uq >> 20).astype(np.int32)
        out[:, 1] = (uq & ((1 << 20) - 1)).astype(np.int32)
        del uq
        return out
    pairs = np.unique(np.stack([lo, hi], axis=1), axis=0)
    return pairs.astype(np.int32)


# ---------------------------------------------------------------- detection --

def build_igraph_batched(n: int, edges: np.ndarray, weights=None, batch: int = 300_000):
    """Memory-safe igraph construction. NOTE: python-igraph's sparse-matrix and
    generator paths materialize giant python lists; batched add_edges + malloc_trim
    keeps RSS bounded. edges may be a memmap."""
    g = ig.Graph(n=n)
    m = len(edges)
    for i in range(0, m, batch):
        blk = np.asarray(edges[i : i + batch])
        g.add_edges(list(zip(blk[:, 0].tolist(), blk[:, 1].tolist())))
        if (i // batch) % 10 == 0:
            trim()
    if weights is not None:
        g.es["weight"] = np.asarray(weights, dtype=np.float64).tolist()
    trim()
    return g


def run_leiden(n: int, edges: np.ndarray, resolution: float = 1.0, seed: int = SEED,
               weights=None, engine: str = "igraph"):
    """engine='igraph': native C Leiden (low RAM, ~58B/edge total; recommended).
    engine='leidenalg': python leidenalg (copies graph internally, ~2x RAM)."""
    g = build_igraph_batched(n, edges, weights)
    if engine == "igraph":
        import random as _random
        _random.seed(seed)
        cl = g.community_leiden(
            "modularity", weights="weight" if weights is not None else None,
            resolution=resolution, n_iterations=3,
        )
        memb = np.asarray(cl.membership, dtype=np.int32)
        q, mod = float(cl.quality), float(cl.modularity)
        del g, cl
    else:
        part = leidenalg.find_partition(
            g, leidenalg.RBConfigurationVertexPartition,
            weights="weight" if weights is not None else None,
            resolution_parameter=resolution, seed=seed,
        )
        memb = np.asarray(part.membership, dtype=np.int32)
        q, mod = part.quality(), part.modularity
        del g, part
    trim()
    return memb, q, mod


# ------------------------------------------------------------------ metrics --

def community_metrics(n: int, E: np.ndarray, memb: np.ndarray,
                      node_stats: dict, nodes: dict):
    """E: undirected unique edges (compact idx). Returns (per_comm dict arrays, globals)."""
    t0 = time.time()
    C = int(memb.max()) + 1
    u, v = E[:, 0], E[:, 1]
    mu, mv = memb[u], memb[v]
    same = mu == mv

    e_in = np.bincount(mu[same], minlength=C).astype(np.int64)
    e_out = np.zeros(C, np.int64)
    cross_u, cross_v = u[~same], v[~same]
    if len(cross_u):
        e_out += np.bincount(mu[~same], minlength=C)
        e_out += np.bincount(mv[~same], minlength=C)
    n_c = np.bincount(memb, minlength=C).astype(np.int64)

    # boundary nodes (inter-community)
    b_inter = np.zeros(n, bool)
    if len(cross_u):
        b_inter[cross_u] = True
        b_inter[cross_v] = True
    bn_inter = np.bincount(memb[b_inter], minlength=C).astype(np.int64)

    # outside-subset traffic (per-node directed counts) -> per-community
    ext_out_c = np.bincount(memb, weights=node_stats["ext_out"], minlength=C).astype(np.int64)
    ext_in_c = np.bincount(memb, weights=node_stats["ext_in"], minlength=C).astype(np.int64)
    ext_c = ext_out_c + ext_in_c
    b_any = b_inter | node_stats["boundary_any"]
    bn_any = np.bincount(memb[b_any], minlength=C).astype(np.int64)

    with np.errstate(divide="ignore", invalid="ignore"):
        out_ratio = np.where(e_in + e_out > 0, e_out / np.maximum(1, e_in + e_out), np.nan)
        conduct = np.where(2 * e_in + e_out > 0, e_out / np.maximum(1, 2 * e_in + e_out), np.nan)
        avg_deg = 2 * e_in / np.maximum(1, n_c)
        ext_ratio = np.where(e_in + e_out + ext_out_c > 0,
                             ext_out_c / np.maximum(1, e_in + e_out + ext_out_c), np.nan)

    per = {
        "comm": np.arange(C), "N_c": n_c, "E_in_c": e_in, "E_out_c": e_out,
        "E_ext_c": ext_c, "E_ext_out_c": ext_out_c, "E_ext_in_c": ext_in_c,
        "out_ratio_c": out_ratio, "conductance_c": conduct,
        "avg_degree_c": avg_deg, "boundary_nodes_c": bn_inter,
        "boundary_node_ratio_c": bn_inter / np.maximum(1, n_c),
        "boundary_any_nodes_c": bn_any, "ext_ratio_c": ext_ratio,
    }

    total_cross = int((~same).sum())
    glob = {
        "n_communities": C,
        "n_nodes": n,
        "n_edges_internal_undirected": int(len(E)),
        "n_cross_community_edges": total_cross,
        "n_edges_to_outside_subset": int(node_stats["ext_out"].sum()),
        "n_edges_from_outside_subset": int(node_stats["ext_in"].sum()),
        "largest_comm_share": float(n_c.max() / n),
        "top5_comm_share": float(np.sort(n_c)[::-1][:5].sum() / n),
        "cross_edge_fraction": float(total_cross / max(1, len(E))),
        "mean_out_ratio_edge_weighted": float(e_out.sum() / max(1, e_in.sum() + e_out.sum())),
        "size_percentiles": {f"p{p}": float(np.percentile(n_c, p)) for p in (5, 25, 50, 75, 95)},
        "out_ratio_percentiles": {f"p{p}": float(np.nanpercentile(out_ratio, p)) for p in (10, 25, 50, 75, 90)},
        "conductance_percentiles": {f"p{p}": float(np.nanpercentile(conduct, p)) for p in (10, 25, 50, 75, 90)},
        "node_weighted_median_out_ratio": float(_weighted_median(out_ratio, n_c)),
        "secs": round(time.time() - t0, 2),
    }
    return per, glob


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    m = ~np.isnan(values) & (weights > 0)
    if not m.any():
        return float("nan")
    vals, w = values[m], weights[m].astype(float)
    o = np.argsort(vals)
    vals, w = vals[o], w[o]
    cw = np.cumsum(w)
    return float(vals[np.searchsorted(cw, cw[-1] / 2)])


def top_inter_pairs(E: np.ndarray, memb: np.ndarray, top_k: int = 20):
    C = int(memb.max()) + 1
    u, v = E[:, 0].astype(np.int64), E[:, 1].astype(np.int64)
    mu, mv = memb[u], memb[v]
    cross = mu != mv
    a = np.minimum(mu[cross], mv[cross])
    b = np.maximum(mu[cross], mv[cross])
    keys = a * C + b
    uk, cnt = np.unique(keys, return_counts=True)
    o = np.argsort(cnt)[::-1][:top_k]
    out = []
    for k, c in zip(uk[o], cnt[o]):
        out.append({"comm_a": int(k // C), "comm_b": int(k % C), "edges": int(c)})
    return out


def community_labels(n: int, E: np.ndarray, memb: np.ndarray, nodes: dict, per, top_n=3):
    """Representative titles per community: highest internal-degree members."""
    deg = np.zeros(n, np.int64)
    if len(E):
        deg += np.bincount(E[:, 0], minlength=n)
        deg += np.bincount(E[:, 1], minlength=n)
    C = len(per["comm"])
    labels = {}
    order = np.argsort(-deg)
    seen = {c: 0 for c in range(C)}
    titles = nodes["title"]
    for i in order:
        c = int(memb[i])
        if seen[c] < top_n:
            labels.setdefault(c, []).append(titles[i])
            seen[c] += 1
        if all(v >= top_n for v in seen.values()):
            break
    return {c: labels.get(c, []) for c in range(C)}


def hub_analysis(n: int, E: np.ndarray, memb: np.ndarray, nodes: dict, node_stats: dict, top_k=30):
    deg_in = np.zeros(n, np.int64)
    if len(E):
        deg_in += np.bincount(E[:, 0], minlength=n)
        deg_in += np.bincount(E[:, 1], minlength=n)
    pid = np.asarray(nodes["page_id"], dtype=np.int64)
    deg_ext = node_stats["ext_out"] + node_stats["ext_in"]
    total = deg_in + deg_ext
    o = np.argsort(-total)[:top_k]
    titles = nodes["title"]
    rows = []
    for r, i in enumerate(o):
        rows.append({
            "rank": r + 1, "idx": int(i), "title": titles[i], "page_id": int(pid[i]),
            "deg_internal": int(deg_in[i]), "deg_outside": int(deg_ext[i]),
            "comm": int(memb[i]), "flags": hub_flags(titles[i]),
        })
    # concentration: share of internal edges incident to top-1% degree nodes
    k1 = max(1, n // 100)
    top_nodes = np.argsort(-deg_in)[:k1]
    mask = np.zeros(n, bool)
    mask[top_nodes] = True
    if len(E):
        share = float((mask[E[:, 0]] | mask[E[:, 1]]).sum() / len(E))
    else:
        share = 0.0
    stats = {
        "top1pct_internal_edge_incidence_share": share,
        "degree_percentiles": {f"p{p}": float(np.percentile(deg_in, p)) for p in (50, 75, 90, 99)},
        "max_internal_degree": int(deg_in.max()) if n else 0,
    }
    return rows, stats


# ---------------------------------------------------------------- hierarchy --

def pair_matrix(E: np.ndarray, memb: np.ndarray):
    """Return (a, b, w) arrays of the weighted inter-community graph."""
    C = int(memb.max()) + 1
    u, v = E[:, 0].astype(np.int64), E[:, 1].astype(np.int64)
    mu, mv = memb[u], memb[v]
    cross = mu != mv
    a = np.minimum(mu[cross], mv[cross])
    b = np.maximum(mu[cross], mv[cross])
    keys = a * C + b
    uk, cnt = np.unique(keys, return_counts=True)
    return uk // C, uk % C, cnt.astype(np.float64), C


def hierarchy_level2(memb: np.ndarray, E: np.ndarray, per: dict, resolution: float = 1.0,
                     seed: int = SEED):
    a, b, w, C = pair_matrix(E, memb)
    if C < 2 or len(a) == 0:
        return None
    g = ig.Graph(n=C, edges=list(zip(a.tolist(), b.tolist())), directed=False)
    g.es["weight"] = w.tolist()
    part = leidenalg.find_partition(g, leidenalg.RBConfigurationVertexPartition,
                                    weights="weight", resolution_parameter=resolution, seed=seed)
    cl = np.asarray(part.membership, dtype=np.int32)
    K = int(cl.max()) + 1

    e_in_c = per["E_in_c"]
    ext_c = per["E_ext_c"]
    n_c = per["N_c"]
    # level2 aggregates
    E_in2 = np.bincount(cl, weights=e_in_c, minlength=K)
    E_between2 = np.zeros(K)  # intra-cluster inter-community weights
    E_out2 = np.zeros(K)      # inter-cluster weights
    for x, y, wt in zip(a.tolist(), b.tolist(), w.tolist()):
        cx, cy = cl[x], cl[y]
        if cx == cy:
            E_between2[cx] += wt
        else:
            E_out2[cx] += wt
            E_out2[cy] += wt
    N2 = np.bincount(cl, weights=n_c, minlength=K).astype(np.int64)
    E_ext2 = np.bincount(cl, weights=ext_c, minlength=K)
    ncomms = np.bincount(cl, minlength=K)
    with np.errstate(divide="ignore", invalid="ignore"):
        ein2_total = E_in2 + E_between2
        out_ratio2 = np.where(ein2_total + E_out2 > 0, E_out2 / np.maximum(1, ein2_total + E_out2), np.nan)
        conduct2 = np.where(2 * ein2_total + E_out2 > 0, E_out2 / np.maximum(1, 2 * ein2_total + E_out2), np.nan)

    total_intra_w = float(E_in2.sum() + E_between2.sum())
    glob = {
        "n_clusters": K,
        "resolution": resolution,
        "modularity_community_graph": float(part.modularity),
        "mean_out_ratio2_edge_weighted": float(E_out2.sum() / max(1e-9, total_intra_w + E_out2.sum())),
        "largest_cluster_share": float(N2.max() / max(1, N2.sum())),
        "out_ratio2_percentiles": {f"p{p}": float(np.nanpercentile(out_ratio2, p)) for p in (10, 50, 90)} if K > 2 else {},
    }
    rows = []
    for k in range(K):
        rows.append({
            "cluster": k, "n_comms": int(ncomms[k]), "N_nodes": int(N2[k]),
            "E_in_articles": float(E_in2[k]), "E_between_comms": float(E_between2[k]),
            "E_out2": float(E_out2[k]), "E_ext_outside_subset": float(E_ext2[k]),
            "out_ratio2": float(out_ratio2[k]) if not np.isnan(out_ratio2[k]) else None,
            "conductance2": float(conduct2[k]) if not np.isnan(conduct2[k]) else None,
        })
    return {"clusters": rows, "global": glob, "comm_to_cluster": cl.tolist()}
