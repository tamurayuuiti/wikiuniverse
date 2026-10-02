# wu/fullgraph.py — フルグラフのコミュニティ検出(マクロ → 銀河の 2 段階 Leiden)
#
# 責務:
# - 全グラフ(jawiki ns0 全体)のコミュニティ検出パイプラインの処理本体:
#   dedup(無向一意化)/ detect(一括 Leiden = マクロ)/ subdivide(再帰分割 = 銀河)/
#   metrics(指標)/ cluster(L2 銀河団)/ export(結合表)/ prune(次数予算剪定)。
# - 各段階はディスクへ保存され再開可能(工程別サブコマンド制)。
#
# 注意:
# - cmd_* 関数が処理本体で、ステージ(wu/stages/community.py)が参数付きで委譲する。
#   正準実行: python -m wu run dedup|detect|subdivide|metrics|cluster|export
#   (prune はレイアウト用グラフの実験腕 = experiment 分類)。
# - 挙動は test_subdivide / test_catalog が担保する。
# - tag 規約: res<R> = 一括マクロ、<tag>_sub(既定 res1_sub)= 2 段階の銀河。

"""Full-graph Leiden pipeline for the complete jawiki ns0 graph.

Subcommands (each stage persists to disk; the pipeline is resumable):

  dedup     graph/edges_ns0.bin (directed, page_id)
              -> graph/edges_undirected_unique.bin (undirected unique, compact idx)
  detect    build igraph in memory-bounded batches -> native C Leiden
              -> community/full/membership_res<R>.npy
  subdivide ★ two-stage refinement: for each macro community, extract its
            induced subgraph and run Leiden recursively until every piece is
            <= max_galaxy nodes. This bypasses the modularity resolution
            limit (~sqrt(2m) nodes) that prevents one-shot galaxy-sized
            partitions at full scale.
              -> community/full/membership_<tag>_sub.npy + subdivide_meta_<tag>_sub.json
  metrics   chunked per-community + global metrics for any membership tag
  cluster   level-2 (galaxy clusters) from the weighted community graph
  export    page_id/title/comm/cluster join -> membership_<tag>.parquet

Tags: detect writes tag "res<R>" (e.g. res1). subdivide writes "res<R>_sub".
metrics/cluster/export accept either --resolution (=> tag res<R>) or --tag.

Memory budget (32GB machine):
  dedup      peak ~4-5 GB   detect ~7-9 GB RSS
  subdivide  ~2-4 GB + per-subgraph Leiden (largest blob 246k nodes is trivial)
  metrics    ~3 GB (2 passes + induced-edge buffer ~0.7 GB)

Usage (typical full sequence):
  python -m wu run dedup detect subdivide metrics cluster export --base data
  (各ステージの参数は wu/stages/community.py の Param 宣言。override は
   --set <stage>.<key>=<value>。tag 規約: res<R> = マクロ / res<R>_sub = 銀河)
Smoke: detect --max-edges 5000000 --force   (then rerun real detect with --force)
"""
from __future__ import annotations

import datetime
import json
import os
import random
import sys
import time

import numpy as np


from ..dumpio import now_iso as _now, read_json, write_json  # noqa: E402,F401
from ..paths import Dirs  # noqa: E402
from ..stats import load_edges_mmap, load_titles  # noqa: E402

from .analysis import SEED  # noqa: E402  # 乱数シードの単一の真実源
SHIFT = 21  # compact idx < 2^21 = 2,097,152  (jawiki: 1,516,326 articles)


def _res_tag(res: float) -> str:
    return f"{res:g}"


def _full_dir(dirs: Dirs) -> str:
    d = str(dirs.community_full)
    os.makedirs(d, exist_ok=True)
    return d


def _versions() -> dict:
    import igraph
    import platform
    return {"python": platform.python_version(), "igraph": igraph.__version__,
            "numpy": np.__version__, "platform": platform.platform()}


def _load_article_ids(dirs: Dirs) -> np.ndarray:
    return np.load(dirs.article_ids)  # sorted page_ids; position == compact idx


DEFAULT_EDGES = "edges_undirected_unique.bin"


def _load_undirected(dirs: Dirs, name: str | None = None) -> np.ndarray:
    name = name or DEFAULT_EDGES
    p = name if os.path.isabs(name) else os.path.join(dirs.graph, name)
    if not os.path.exists(p):
        sys.exit(f"{p} not found - run `dedup` (or `prune`) first")
    cnt = os.path.getsize(p) // 8
    return np.memmap(p, dtype=np.int32, mode="r", shape=(cnt, 2))


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    m = ~np.isnan(values) & (weights > 0)
    if not m.any():
        return float("nan")
    vals, w = values[m], weights[m].astype(float)
    o = np.argsort(vals)
    vals, w = vals[o], w[o]
    cw = np.cumsum(w)
    return float(vals[np.searchsorted(cw, cw[-1] / 2)])


def _build_graph(n: int, edges: np.ndarray, batch: int = 300_000, quiet: bool = True):
    import igraph as ig
    g = ig.Graph(n=n)
    m = len(edges)
    for i in range(0, m, batch):
        blk = np.asarray(edges[i : i + batch])
        g.add_edges(list(zip(blk[:, 0].tolist(), blk[:, 1].tolist())))
        if not quiet and (i // batch) % 20 == 0:
            print(f"  igraph build {i + len(blk):,}/{m:,}", flush=True)
    return g


def _leiden_once(n: int, edges: np.ndarray, objective: str, resolution: float,
                 seed: int = SEED, n_iterations: int = 3):
    g = _build_graph(n, edges)
    random.seed(seed)
    cl = g.community_leiden(objective, resolution=resolution, n_iterations=n_iterations)
    memb = np.asarray(cl.membership, dtype=np.int32)
    mod = float(cl.modularity) if objective.lower() == "modularity" else None
    q = float(cl.quality)
    del g
    return memb, mod, q


# ------------------------------------------------------------------- dedup --

def cmd_dedup(dirs: Dirs, chunk: int = 10_000_000, edges_in: str = "edges_ns0.bin",
              out_name: str = "edges_undirected_unique.bin",
              meta_name: str = "dedup_meta.json"):
    t0 = time.time()
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    if n >= (1 << SHIFT):
        raise RuntimeError(f"too many articles ({n}) for SHIFT={SHIFT}; bump SHIFT")
    E = load_edges_mmap(os.path.join(dirs.graph, edges_in))
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
            keys[i + len(lo) : i + len(blk)] = -1
        print(f"  remap {i + len(blk):,}/{m:,} ({time.time()-t0:.0f}s)", flush=True)
    keys = keys[keys >= 0]
    mask = (keys & ((1 << SHIFT) - 1)) != (keys >> SHIFT)
    n_self = int((~mask).sum())
    keys = keys[mask]
    print(f"[dedup] sorting {len(keys):,} keys ...", flush=True)
    uq = np.unique(keys)
    del keys
    out_path = os.path.join(dirs.graph, out_name)
    out = np.empty(len(uq) * 2, dtype=np.int32)
    out[0::2] = (uq >> SHIFT).astype(np.int32)
    out[1::2] = (uq & ((1 << SHIFT) - 1)).astype(np.int32)
    out.tofile(out_path)
    meta = {
        "stage": "dedup", "created_at": _now(), "in_file": edges_in,
        "n_articles": n, "n_directed_edges": m,
        "n_endpoint_remap_failures": bad, "n_self_loops_dropped": n_self,
        "n_undirected_unique_edges": int(len(uq)),
        "reciprocity_implied": float(1.0 - len(uq) / max(1, m - bad)) if m else None,
        "shift": SHIFT, "secs": round(time.time() - t0, 1),
        "out_file": out_path, "out_bytes": os.path.getsize(out_path),
        **_versions(),
    }
    write_json(os.path.join(dirs.graph, meta_name), meta)
    print(f"[dedup] {m:,} directed -> {len(uq):,} undirected unique "
          f"({meta['secs']}s) -> {out_path}")
    return meta


# ------------------------------------------------------------------ detect --

def cmd_detect(dirs: Dirs, resolutions, max_edges: int = 0, seed: int = SEED,
               force: bool = False, batch: int = 300_000, n_iterations: int = 3,
               objective: str = "modularity", edges_name: str | None = None,
               suffix: str = ""):
    outdir = _full_dir(dirs)
    E = _load_undirected(dirs, edges_name)
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    todo = []
    for res in resolutions:
        tag = f"res{_res_tag(res)}{suffix}"
        p = os.path.join(outdir, f"membership_{tag}.npy")
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
    g = _build_graph(n, E[:total] if max_edges else E, batch=batch, quiet=False)
    print(f"[detect] graph built: {g.vcount():,} nodes {g.ecount():,} edges "
          f"({time.time()-t0:.0f}s)", flush=True)

    detect_meta_path = os.path.join(outdir, "detect_meta.json")
    meta = read_json(detect_meta_path, {}) or {"runs": {}}
    for res, tag, p in todo:
        t1 = time.time()
        random.seed(seed)
        cl = g.community_leiden(objective, resolution=res, n_iterations=n_iterations)
        memb = np.asarray(cl.membership, dtype=np.int32)
        np.save(p, memb)
        sizes = np.bincount(memb)
        info = {
            "resolution": res, "objective": objective, "seed": seed,
            "edges_file": edges_name or DEFAULT_EDGES, "tag": tag,
            "n_iterations": n_iterations, "n_nodes": int(n), "n_edges_used": int(total),
            "smoke": bool(max_edges),
            "n_communities": int(len(cl)), "modularity": float(cl.modularity),
            "quality": float(cl.quality),
            "n_singleton_comms": int((sizes == 1).sum()),
            "largest_comm_share": float(sizes.max() / n),
            "top5_comm_share": float(np.sort(sizes)[::-1][:5].sum() / n),
            "median_comm_size_nontrivial": float(np.median(sizes[sizes >= 2])) if (sizes >= 2).any() else None,
            "secs": round(time.time() - t1, 1), "finished_at": _now(),
        }
        meta["runs"][tag] = info
        print(f"[detect] res={tag}: C={info['n_communities']:,} "
              f"mod={info['modularity']:.4f} largest={info['largest_comm_share']:.3f} "
              f"singletons={info['n_singleton_comms']} ({info['secs']}s)")
    meta.update({"graph": {"n_nodes": int(n), "n_edges_total": int(len(E))},
                 "versions": _versions(), "updated_at": _now()})
    write_json(detect_meta_path, meta)
    del g


# ------------------------------------------------------------------ prune --

def cmd_prune(dirs: Dirs, budget: int, mode: str = "smart", seed: int = SEED):
    """レイアウト用グラフの構築: 無向一意グラフの次数予算剪定。

    smart モード(推奨): どちらかの端点でランダム順位 < budget、または
    どちらかの端点の無向次数 <= budget ならエッジを残す。
    銀河構造(低次数のスポーク)を保ちつつ、ハブ同士の「銀河間高速道路」だけを
    間引く。
    出力: graph/edges_pruned_<mode><budget>.bin
    RAM ピーク: 108M エッジで ~4-5 GB(フル配列 + lexsort 作業領域)。
    """
    from ..experiments.hubsup import prune_edges_degree_budget

    t0 = time.time()
    E_mm = _load_undirected(dirs)
    m = len(E_mm)
    print(f"[prune] loading {m:,} edges ...", flush=True)
    E = np.asarray(E_mm)
    Ep, info = prune_edges_degree_budget(E, budget, seed=seed, mode=mode)
    del E
    Ep = np.ascontiguousarray(Ep, dtype=np.int32)
    out = os.path.join(dirs.graph, f"edges_pruned_{mode}{budget}.bin")
    Ep.tofile(out)
    n = len(_load_article_ids(dirs))
    meta = {"stage": "prune", "created_at": _now(), "mode": mode, "budget": budget,
            "seed": seed, "in_file": DEFAULT_EDGES, "n_edges_in": m,
            "n_edges_out": int(len(Ep)), "out_file": out,
            "out_bytes": os.path.getsize(out),
            "avg_degree_out": float(2 * len(Ep) / n), "secs": round(time.time() - t0, 1),
            **info, **_versions()}
    write_json(os.path.join(dirs.graph, f"prune_meta_{mode}{budget}.json"), meta)
    print(f"[prune] {mode} B={budget}: {m:,} -> {len(Ep):,} edges "
          f"({info['kept_frac']:.1%}, avg_deg {meta['avg_degree_out']:.1f}) "
          f"({meta['secs']}s) -> {out}")
    del Ep
    return meta


# --------------------------------------------------------------- subdivide --

def _partition_recursive(n_local: int, edges: np.ndarray, objective: str, resolution: float,
                         max_size: int, depth: int, seed: int):
    """Leiden + max_size を超える断片の再帰分割。"""
    memb, mod, q = _leiden_once(n_local, edges, objective, resolution, seed=seed)
    info = {"modularity": mod, "quality": q}
    sizes = np.bincount(memb)
    big = np.flatnonzero(sizes > max_size)
    if depth <= 0 or len(big) == 0:
        return memb, info
    new = memb.copy()
    next_free = int(memb.max()) + 1
    for c in big:
        mem = np.flatnonzero(memb == c)
        mask = (memb[edges[:, 0]] == c) & (memb[edges[:, 1]] == c) if len(edges) else np.zeros(0, bool)
        e = edges[mask]
        loc = np.stack([np.searchsorted(mem, e[:, 0]),
                        np.searchsorted(mem, e[:, 1])], axis=1).astype(np.int32)
        sub, subinfo = _partition_recursive(len(mem), loc, objective, resolution,
                                            max_size, depth - 1, seed)
        new[mem] = next_free + sub
        next_free += int(sub.max()) + 1
    uq, inv = np.unique(new, return_inverse=True)
    info["recursive_splits"] = int(len(big))
    return inv.astype(np.int32), info


def cmd_subdivide(dirs: Dirs, tag: str, min_size: int = 100, max_galaxy: int = 10000,
                  sub_resolution: float = 1.0, sub_objective: str = "modularity",
                  depth: int = 4, seed: int = SEED, chunk: int = 4_000_000,
                  out_tag: str | None = None, edges_name: str | None = None):
    """2 段階分割: 各マクロコミュニティの内部を、全ての断片が
    <= max_galaxy ノードになるまで分割する。モジュラリティの解像度限界を回避する。"""
    t0 = time.time()
    outdir = _full_dir(dirs)
    memb_path = os.path.join(outdir, f"membership_{tag}.npy")
    if not os.path.exists(memb_path):
        sys.exit(f"membership_{tag}.npy not found - run `detect` first")
    memb = np.load(memb_path)
    E = _load_undirected(dirs, edges_name)
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    C = int(memb.max()) + 1
    out_tag = out_tag or f"{tag}_sub"
    print(f"[subdivide] tag={tag}: n={n:,} C={C:,} edges={len(E):,} "
          f"min_size={min_size} max_galaxy={max_galaxy} sub_res={sub_resolution} "
          f"objective={sub_objective} depth={depth}")

    # ---- pass 1: count induced edges per community
    cnt = np.zeros(C, np.int64)
    for i in range(0, len(E), chunk):
        blk = np.asarray(E[i : i + chunk])
        mu = memb[blk[:, 0].astype(np.int64)]
        same = mu == memb[blk[:, 1].astype(np.int64)]
        cnt += np.bincount(mu[same], minlength=C)
    offs = np.zeros(C + 1, np.int64)
    offs[1:] = np.cumsum(cnt)
    total_ind = int(cnt.sum())
    print(f"[subdivide] induced edges total: {total_ind:,} "
          f"(pass1 {time.time()-t0:.0f}s)", flush=True)

    # ---- pass 2: bucket induced edges (CSR-style scatter)
    # NOTE: `fill[c] = pos + 1` with duplicate community ids in a chunk is WRONG
    # (fancy-index assignment keeps only the last write). Instead: stable-sort the
    # chunk's same-community edges by community, compute each edge's rank within
    # its community group, and place it at (current head + rank).
    buf = np.empty((total_ind, 2), np.int32)
    fill = offs[:-1].copy()
    for i in range(0, len(E), chunk):
        blk = np.asarray(E[i : i + chunk])
        u = blk[:, 0].astype(np.int64)
        v = blk[:, 1].astype(np.int64)
        mu = memb[u]
        same = mu == memb[v]
        if not same.any():
            continue
        c = mu[same]
        us = u[same].astype(np.int32)
        vs = v[same].astype(np.int32)
        oc = np.argsort(c, kind="stable")
        cs = c[oc]
        uniq, first = np.unique(cs, return_index=True)
        uniq_idx = np.searchsorted(uniq, cs)
        rank = np.arange(len(cs), dtype=np.int64) - first[uniq_idx]
        cnt_uniq = np.diff(np.append(first, len(cs)))
        pos = fill[uniq][uniq_idx] + rank
        buf[pos, 0] = us[oc]
        buf[pos, 1] = vs[oc]
        fill[uniq] += cnt_uniq
    assert np.array_equal(fill, offs[1:]), "bucket fill mismatch (scatter bug)"
    del fill
    print(f"[subdivide] bucketed ({time.time()-t0:.0f}s)", flush=True)

    # ---- per-community subdivision
    order = np.argsort(memb, kind="stable")
    starts = np.searchsorted(memb[order], np.arange(C + 1))
    refined = np.empty(n, np.int32)
    next_label = 0
    diag = []
    for c in range(C):
        members = order[starts[c] : starts[c + 1]]
        size = len(members)
        e_glob = buf[offs[c] : offs[c + 1]]
        if size < min_size:
            refined[members] = next_label
            diag.append({"orig_comm": c, "size": size, "E_in": int(len(e_glob)),
                         "action": "kept", "C_sub": 1,
                         "label_start": next_label})
            next_label += 1
            continue
        loc = np.stack([np.searchsorted(members, e_glob[:, 0].astype(np.int64)),
                        np.searchsorted(members, e_glob[:, 1].astype(np.int64))],
                       axis=1).astype(np.int32)
        t1 = time.time()
        labels, info = _partition_recursive(size, loc, sub_objective, sub_resolution,
                                            max_galaxy, depth, seed)
        lsizes = np.bincount(labels)
        refined[members] = next_label + labels
        diag.append({"orig_comm": c, "size": size, "E_in": int(len(e_glob)),
                     "action": "split", "C_sub": int(labels.max()) + 1,
                     "modularity_sub": info.get("modularity"),
                     "largest_sub_share": float(lsizes.max() / size),
                     "median_sub_size": float(np.median(lsizes[lsizes > 0])),
                     "secs": round(time.time() - t1, 1),
                     "label_start": next_label})
        if size >= 5000:
            print(f"  comm {c}: size={size:,} E_in={len(e_glob):,} -> "
                  f"C_sub={int(labels.max())+1} largest={lsizes.max()/size:.3f} "
                  f"({time.time()-t1:.0f}s)", flush=True)
        next_label += int(labels.max()) + 1
    del buf

    C_final = next_label
    out_path = os.path.join(outdir, f"membership_{out_tag}.npy")
    np.save(out_path, refined)
    fsizes = np.bincount(refined, minlength=C_final)
    meta = {
        "stage": "subdivide", "created_at": _now(),
        "in_tag": tag, "out_tag": out_tag,
        "params": {"min_size": min_size, "max_galaxy": max_galaxy,
                   "sub_resolution": sub_resolution, "sub_objective": sub_objective,
                   "depth": depth, "seed": seed,
                   "edges_file": edges_name or DEFAULT_EDGES},
        "C_in": C, "C_final": int(C_final),
        "n_singleton_comms_final": int((fsizes == 1).sum()),
        "largest_final_share": float(fsizes.max() / n),
        "size_percentiles_final": {f"p{p}": float(np.percentile(fsizes[fsizes >= 2], p))
                                   for p in (25, 50, 75, 95, 99)} if (fsizes >= 2).any() else {},
        "size_max_final": int(fsizes.max()),
        "secs": round(time.time() - t0, 1),
        "communities": diag,
        **_versions(),
    }
    write_json(os.path.join(outdir, f"subdivide_meta_{out_tag}.json"), meta)
    print(f"[subdivide] C: {C} -> {C_final:,} (singletons={meta['n_singleton_comms_final']}) "
          f"largest={meta['largest_final_share']:.4f} "
          f"median(nontrivial)={meta['size_percentiles_final'].get('p50')} "
          f"({meta['secs']}s) -> {out_path}")
    return meta


# ----------------------------------------------------------------- metrics --

def cmd_metrics(dirs: Dirs, tag: str, chunk: int = 4_000_000,
                edges_name: str | None = None, label: str | None = None):
    t0 = time.time()
    outdir = _full_dir(dirs)
    memb_path = os.path.join(outdir, f"membership_{tag}.npy")
    if not os.path.exists(memb_path):
        sys.exit(f"membership_{tag}.npy not found - run detect/subdivide first")
    memb = np.load(memb_path)
    E = _load_undirected(dirs, edges_name)
    out_tag = label or tag
    article_ids = _load_article_ids(dirs)
    n = len(article_ids)
    C = int(memb.max()) + 1
    print(f"[metrics] tag={tag}: n={n:,} C={C:,} edges={len(E):,}")

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
    np.savez_compressed(os.path.join(outdir, f"pairs_{out_tag}.npz"),
                        a=pair_a, b=pair_b, w=pair_w)

    bn_inter = np.bincount(memb[b_inter], minlength=C).astype(np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        out_ratio = np.where(e_in + e_out > 0, e_out / np.maximum(1, e_in + e_out), np.nan)
        conduct = np.where(2 * e_in + e_out > 0, e_out / np.maximum(1, 2 * e_in + e_out), np.nan)
        avg_deg = 2 * e_in / np.maximum(1, n_c)

    eff = n_c >= 2                      # "effective" communities (drop singletons)
    sizes_eff = n_c[eff]
    n_single = int((n_c == 1).sum())
    n_isolated = int((deg == 0).sum())
    glob = {
        "tag": tag, "out_tag": out_tag, "edges_file": edges_name or DEFAULT_EDGES,
        "seed": SEED, "generated_at": _now(),
        "n_nodes": n, "n_communities": C,
        "n_singleton_comms": n_single,
        "n_communities_effective": int(eff.sum()),
        "n_isolated_nodes": n_isolated,
        "n_edges_undirected": total_edges,
        "n_cross_community_edges": total_cross,
        "cross_edge_fraction": float(total_cross / max(1, total_edges)),
        "largest_comm_share": float(n_c.max() / n),
        "top5_comm_share": float(np.sort(n_c)[::-1][:5].sum() / n),
        "mean_out_ratio_edge_weighted": float(e_out.sum() / max(1, e_in.sum() + e_out.sum())),
        "node_weighted_median_out_ratio": _weighted_median(out_ratio, n_c),
        "size_percentiles_all": {f"p{p}": float(np.percentile(n_c[n_c > 0], p))
                                 for p in (5, 25, 50, 75, 95, 99)},
        "size_percentiles_effective": {f"p{p}": float(np.percentile(sizes_eff, p))
                                       for p in (5, 25, 50, 75, 95, 99)} if eff.any() else {},
        "size_max": int(n_c.max()),
        "out_ratio_percentiles_effective": {f"p{p}": float(np.nanpercentile(out_ratio[eff], p))
                                            for p in (10, 25, 50, 75, 90)} if eff.any() else {},
        "conductance_percentiles_effective": {f"p{p}": float(np.nanpercentile(conduct[eff], p))
                                              for p in (10, 25, 50, 75, 90)} if eff.any() else {},
        "node_share_in_comms_out_ratio_lt_0.3": float(n_c[eff & (out_ratio < 0.3)].sum() / n),
        "node_share_in_comms_out_ratio_lt_0.5": float(n_c[eff & (out_ratio < 0.5)].sum() / n),
        "boundary_node_ratio_node_weighted": float(b_inter.sum() / n),
        "avg_degree_full": float(2 * total_edges / n),
        "secs": round(time.time() - t0, 1),
    }

    # representative labels: top-3 nodes by internal degree per community
    pid_col, title_col = load_titles(dirs.parsed)
    deg_order = np.lexsort((-deg, memb))
    starts = np.searchsorted(memb[deg_order], np.arange(C), side="left")
    rep_idx, rep_comm = [], []
    for c in range(C):
        if n_c[c] == 0:
            continue
        k = min(3, int(n_c[c]))
        rep_idx.extend(deg_order[starts[c] : starts[c] + k].tolist())
        rep_comm.extend([c] * k)
    rep_idx = np.asarray(rep_idx, dtype=np.int64)
    pos = np.clip(np.searchsorted(pid_col, article_ids[rep_idx]), 0, len(pid_col) - 1)
    rep_titles = [title_col[p].as_py() for p in pos]
    labels = {}
    for c, t in zip(rep_comm, rep_titles):
        labels.setdefault(c, []).append(t)
    labels = {c: labels.get(c, []) for c in range(C)}

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
    pq.write_table(tbl, os.path.join(outdir, f"per_community_{out_tag}.parquet"), compression="zstd")

    topk = np.argsort(pair_w)[::-1][:50]
    top_pairs = [{"comm_a": int(pair_a[k]), "comm_b": int(pair_b[k]), "edges": int(pair_w[k]),
                  "a_labels": labels.get(int(pair_a[k]), [])[:2],
                  "b_labels": labels.get(int(pair_b[k]), [])[:2]} for k in topk]
    write_json(os.path.join(outdir, f"top_pairs_{out_tag}.json"), top_pairs)
    write_json(os.path.join(outdir, f"metrics_{out_tag}.json"), {"global": glob, "versions": _versions()})
    print(f"[metrics] tag={out_tag}: C_eff={glob['n_communities_effective']} "
          f"singletons={n_single} crossF={glob['cross_edge_fraction']:.4f} "
          f"largest={glob['largest_comm_share']:.3f} "
          f"oR_p50_eff={glob['out_ratio_percentiles_effective'].get('p50')} "
          f"({glob['secs']}s)")
    return glob


# ----------------------------------------------------------------- cluster --

def cmd_cluster(dirs: Dirs, tag: str, cluster_resolution: float = 1.0, seed: int = SEED):
    import igraph as ig
    import pyarrow.parquet as pq

    t0 = time.time()
    outdir = _full_dir(dirs)
    memb = np.load(os.path.join(outdir, f"membership_{tag}.npy"))
    pz = np.load(os.path.join(outdir, f"pairs_{tag}.npz"))
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
    cl_comm = np.asarray(cl.membership, dtype=np.int32)
    K = int(cl_comm.max()) + 1
    cl_node = cl_comm[memb]
    np.save(os.path.join(outdir, f"clusters_comm_{tag}.npy"), cl_comm)
    np.save(os.path.join(outdir, f"clusters_node_{tag}.npy"), cl_node)

    tbl = pq.read_table(os.path.join(outdir, f"per_community_{tag}.parquet")).to_pydict()
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
        "tag": tag, "resolution_l2": cluster_resolution, "seed": seed,
        "generated_at": _now(), "n_clusters": K, "n_communities": C,
        "modularity_community_graph": float(cl.modularity),
        "largest_cluster_share": float(N2.max() / n),
        "top5_cluster_share": float(np.sort(N2)[::-1][:5].sum() / n),
        "mean_out_ratio2_edge_weighted": float(E_out2.sum() / max(1e-9, ein2_total.sum() + E_out2.sum())),
        "cluster_size_nodes_percentiles": {f"p{p}": float(np.percentile(N2[N2 > 0], p))
                                           for p in (25, 50, 75, 95)},
        "n_clusters_nontrivial": int((comms_per_cluster_nt := np.bincount(cl_comm, minlength=K) >= 2).sum()),
        "cluster_size_nodes_percentiles_nontrivial": {
            f"p{p}": float(np.percentile(N2[comms_per_cluster_nt], p)) for p in (25, 50, 75, 95)
        } if comms_per_cluster_nt.any() else {},
        "secs": round(time.time() - t0, 1),
    }
    comms_per_cluster = np.bincount(cl_comm, minlength=K)
    rows = []
    for k in range(K):
        rows.append({"cluster": k, "n_comms": int(comms_per_cluster[k]),
                     "N_nodes": int(N2[k]), "E_in_articles": float(E_in2[k]),
                     "E_between_comms": float(E_between2[k]), "E_out2": float(E_out2[k]),
                     "out_ratio2": float(out_ratio2[k]) if not np.isnan(out_ratio2[k]) else None})
    write_json(os.path.join(outdir, f"clusters_meta_{tag}.json"),
               {"global": glob, "clusters": rows, "versions": _versions()})
    print(f"[cluster] tag={tag}: K={K:,} largest={glob['largest_cluster_share']:.3f} "
          f"modL2={glob['modularity_community_graph']:.4f} "
          f"oR2_w={glob['mean_out_ratio2_edge_weighted']:.3f} ({glob['secs']}s)")
    return glob


# ------------------------------------------------------------------ export --

def cmd_export(dirs: Dirs, tag: str, with_clusters: bool = True):
    import pyarrow as pa
    import pyarrow.parquet as pq

    t0 = time.time()
    outdir = _full_dir(dirs)
    memb = np.load(os.path.join(outdir, f"membership_{tag}.npy"))
    cl_path = os.path.join(outdir, f"clusters_node_{tag}.npy")
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
    out = os.path.join(outdir, f"membership_{tag}.parquet")
    pq.write_table(pa.table(cols), out, compression="zstd")
    print(f"[export] {n:,} rows -> {out} ({time.time()-t0:.0f}s, "
          f"{os.path.getsize(out)/1e6:.1f} MB)")


# --------------------------------------------------------------------- all --
