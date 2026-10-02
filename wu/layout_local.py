# wu/layout_local.py — 銀河内部のローカルレイアウト(記事座標)
#
# 責務:
# - 銀河ごとに独立したジョブとして記事座標を計算する(バッチ分割・並列・resume 可能):
#   力モデル = igraph FR(3D)の形状 + セクタアンカーバネ(優勢隣接銀河方向、確信度
#   ゲイン)+ rank 成層(外部次数分位で半径目標、内部ハブはコアへ)+ 弾性 prior +
#   ボール内クランプ。FR 正規化はロバスト(重心引き算 + p98 スケール)。
# - run 非依存の重計算は tag 毎に 1 度だけ事前計算して mmap 共有する(local_prep)。
# - 全銀河完了時にシャードから article_positions.parquet へ自動マージし、
#   checkpoint を修復する(完了の正典はシャード実在 = 並列レース対策)。
#
# 注意:
# - 旧 scripts/layout_local.py から移設(2026-10-02)。座標はジョブ割当・並列度に
#   依存しない(ビット一致を test_catalog が回帰保証)。scripts 側は互換ラッパで、
#   並列ランチャ(wu/layout_parallel.py)はこのラッパを subprocess 起動する。
# - 正準実行は python -m wu run layout_local(= 並列ランチャ経由)。
# - 以下の英語 docstring は移設時のまま保持。

"""Local (intra-galaxy) article layout — independent-batch jobs (B0 foundation).

B0 foundation (2026-09-30): the run-INDEPENDENT heavy work is precomputed ONCE
per galaxy tag into data/graph/local_prep/<tag>/ and memory-mapped by every job
and every layout run:

  internal_offs.npy / internal_buf.npy  bucketed intra-galaxy edges (old pass2)
  cross_u.npy / cross_g.npy / cross_w.npy  grouped cross-galaxy endpoint pairs
                                        (article, neighbour galaxy, edge count),
                                        sorted by (article, galaxy); also the
                                        input for the planned sector anchors (B1)
  ext_deg.npy                           per-article cross-edge degree
  prep_meta.json                        provenance (schema, input size+mtime)

The run-DEPENDENT anchor directions are rebuilt from cross_* in seconds with
bincount accumulation; this is mathematically identical to the old per-edge
np.add.at accumulation (only fp summation order differs, ~1e-16).
COORDINATES ARE INVARIANT under B0: the per-galaxy math (FR shape, anchor pull,
elastic prior, ball clamp) and all seeds are untouched, and job assignment
(ranges or LPT bins) does not enter any formula.

Each galaxy remains a fully independent job:
  internal edges  -> igraph FR (3D) shape, scaled into the galaxy ball
                     (B3: multilevel seed for n >= --ml-threshold;
                      B2-a: recentred + robust p98 normalization, --fr-norm)
  sector anchors  -> (B1) articles with cross-galaxy links are pulled toward
                     the ball boundary facing their DOMINANT neighbour galaxy
                     (top-1, blended with top-2 when comparable); the pull gain
                     scales with the confidence = weight share of the blended
                     neighbours, so diffuse articles stay interior
  radial strata   -> (B2) target radius follows the within-galaxy rank
                     quantile of external degree (scale-free), and internal
                     hubs with few external links are pulled toward the core
  elastic prior   -> keeps the FR internal structure while anchors bend it

Modes:
  --prep              build/refresh the prep artifacts only (launcher does this
                      once before spawning jobs; single-process runs auto-prep)
  --galaxies A-B      process a contiguous id range (one batch job)
  --job-spec FILE     process an explicit gid list (LPT bins from the launcher)
  --galaxies all      process everything remaining; merge when all are done
  --merge-only        merge shards into article_positions.parquet (no scans,
                      no anchor computation)

Inputs:
  community/full/membership_<galaxy-tag>.npy
  graph/edges_undirected_unique.bin
  graph/local_prep/<galaxy-tag>/            (auto-created when missing/stale)
  layout/<run>/galaxy_positions.parquet     (canonical centers + radii)

Outputs (layout/<run>/):
  article_shards/gal_XXXXXX.npy   per-galaxy positions (cache, deletable)
  layout_local_checkpoint.json    done-galaxy list (resume / batch boundary)
  parallel_jobs/job_<k>.gids.txt  launcher-written LPT bins (cache, deletable)
  article_positions.parquet       merged (page_id, galaxy_id, x, y, z)
  layout_local_meta.json          params/timings (+ parallel telemetry)

Batch usage (the §12 Colab-jobs prototype; LPT-balanced by the launcher):
  python scripts/run_local_parallel.py --base data --run <RUN> [--jobs N]
  python scripts/layout_local.py --base data --run <RUN> --galaxies 0-499
"""
from __future__ import annotations

import argparse
import datetime
import os
import random
import sys
import time

import numpy as np


from .dumpio import now_iso as _now, read_json, write_json  # noqa: E402,F401
from .paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402
from .stats import load_edges_mmap  # noqa: E402

PREP_SCHEMA = 2  # v2 adds int_deg.npy (B2 stratification)
CH = 4_000_000  # edge-scan chunk size


def _fr_unit(n: int, edges_local: np.ndarray, dim: int, seed: int,
             fr_mode: str = "ml", ml_threshold: int = 2000,
             ml_niter: int = 100, stats: dict | None = None,
             fr_norm: str = "p98") -> np.ndarray:
    """FR layout normalized to unit ball (deterministic via random.seed).

    B3: for n >= ml_threshold the flat force-directed anneal is replaced by a
    MULTILEVEL seed: Louvain contraction -> weighted FR on the cluster graph ->
    member expansion with deterministic jitter -> FR refine from those initial
    coordinates (igraph `seed` param) with ml_niter iterations. igraph FR cost
    grows ~n^1.93 and linearly in niter (02 §O), so spending iterations on a
    good initial state is both faster and better-conditioned than flat
    annealing. Galaxies below the threshold keep the exact legacy flat path
    (blast radius limited; median galaxy is 279 articles = 0.18 s).
    """
    import igraph as ig
    random.seed(seed)
    g = ig.Graph(n=n)
    if len(edges_local):
        g.add_edges([(int(u), int(v)) for u, v in edges_local])
    coords = None
    if fr_mode == "ml" and n >= ml_threshold and len(edges_local):
        cl = g.community_multilevel()
        memb = np.asarray(cl.membership, np.int64)
        k = int(memb.max()) + 1
        if 8 <= k < n:
            eu = np.asarray(edges_local, np.int64).reshape(-1, 2)
            cu, cv = memb[eu[:, 0]], memb[eu[:, 1]]
            msk = cu != cv
            keys = cu[msk] * np.int64(k) + cv[msk]
            uniq, cnt = np.unique(keys, return_counts=True)
            ce = [(int(u // k), int(u % k)) for u in uniq]
            random.seed(seed + 1)
            gc = ig.Graph(n=k)
            gc.add_edges(ce)
            gc.es["weight"] = cnt.astype(float).tolist()
            coarse = np.asarray(gc.layout_fruchterman_reingold(
                weights="weight", dim=dim).coords)
            rng = np.random.default_rng(seed + 7)
            init = coarse[memb] + rng.normal(scale=0.02, size=(n, dim))
            random.seed(seed)
            lay = g.layout_fruchterman_reingold(dim=dim, seed=init.tolist(),
                                                niter=ml_niter)
            coords = np.asarray(lay.coords, np.float64)
            if stats is not None:
                stats["ml"] = stats.get("ml", 0) + 1
    if coords is None:
        lay = g.layout_fruchterman_reingold(dim=dim)
        coords = np.asarray(lay.coords, np.float64)
    c = coords
    if fr_norm == "p98":
        # B2-a: recentre + robust scale. The legacy max-norm let a single
        # isolated outlier article set the scale (cloud shrank to ~half the
        # ball, off-centre; 02 §O-6). p98 -> 1.0 with outliers clamped onto
        # the unit ball keeps the body filling the galaxy sphere.
        c = c - c.mean(axis=0)
        r = np.linalg.norm(c, axis=1)
        sc = float(np.percentile(r, 98))
        if sc > 1e-9:
            c = c / sc
            r = r / sc
        over = r > 1.0
        if over.any():
            c[over] /= r[over][:, None]
    else:
        r = np.linalg.norm(c, axis=1)
        m = r.max()
        if m > 1e-9:
            c = c / m
        else:
            rng = np.random.default_rng(seed)
            c = rng.normal(size=(n, dim))
            c /= max(np.linalg.norm(c, axis=1).max(), 1e-9)
    return c


def galaxy_layout_quality(pos: np.ndarray, edges_local: np.ndarray,
                          k: int = 8, n_samples: int = 24, seed: int = 0):
    """Intra-galaxy layout quality: graph-vs-space neighbour recall and edge
    length dispersion. recall = mean over sampled articles of
    |spatial top-k ∩ graph neighbours| / min(k, deg); edge_len_cv = std/mean of
    embedded edge lengths (lower = more uniform springs). Returns None when
    the graph is too small to measure. Sampled-article masks keep the cost at
    O(n_samples * E) instead of an O(n^2) distance matrix.
    """
    n = len(pos)
    if n < 2 * k + 2 or len(edges_local) == 0:
        return None
    eu = np.asarray(edges_local, np.int64).reshape(-1, 2)
    deg = np.bincount(eu.reshape(-1), minlength=n)
    cand = np.flatnonzero(deg >= 2)
    if len(cand) < 4:
        return None
    rng = np.random.default_rng(seed)
    samp = rng.choice(cand, size=min(n_samples, len(cand)), replace=False)
    rec = []
    for s in samp:
        s = int(s)
        msk = (eu[:, 0] == s) | (eu[:, 1] == s)
        eu_s = eu[msk]
        nb = set(np.where(eu_s[:, 0] == s, eu_s[:, 1], eu_s[:, 0]).tolist())
        d = np.linalg.norm(pos - pos[s], axis=1)
        d[s] = np.inf
        top = set(np.argsort(d)[:k].tolist())
        rec.append(len(top & nb) / min(k, len(nb)))
    el = np.linalg.norm(pos[eu[:, 0]] - pos[eu[:, 1]], axis=1)
    mean = float(el.mean())
    return {"adj_recall": round(float(np.mean(rec)), 4),
            "edge_len_cv": round(float(el.std() / mean), 4) if mean > 1e-12 else None}


def _prep_paths(dirs: Dirs, tag: str) -> dict:
    d = dirs.local_prep_dir(tag)
    return {
        "dir": d,
        "offs": d / "internal_offs.npy",
        "buf": d / "internal_buf.npy",
        "cu": d / "cross_u.npy",
        "cg": d / "cross_g.npy",
        "cw": d / "cross_w.npy",
        "ext": d / "ext_deg.npy",
        "int": d / "int_deg.npy",
        "meta": d / "prep_meta.json",
    }


def _source_stamp(memb_path: str, edges_path: str) -> dict:
    out = {}
    for p in (memb_path, edges_path):
        st = os.stat(p)
        out[os.path.basename(p)] = [st.st_size, st.st_mtime_ns]
    return out


def prep_is_fresh(pp: dict, memb_path: str, edges_path: str) -> bool:
    """Prep artifacts exist and their recorded inputs match the current ones."""
    if not pp["meta"].exists():
        return False
    for k in ("offs", "buf", "cu", "cg", "cw", "ext", "int"):
        if not pp[k].exists():
            return False
    meta = read_json(pp["meta"], {}) or {}
    return (meta.get("schema") == PREP_SCHEMA
            and meta.get("sources") == _source_stamp(memb_path, edges_path))


def cmd_prep(dirs: Dirs, tag: str, memb_path: str, edges_path: str) -> dict:
    """Bucket internal edges and group cross-galaxy endpoint pairs, once per tag.

    Grouped cross pairs carry the FULL multiplicity (edge count) of each
    (article, neighbour-galaxy) relation, so anchors and the planned sector
    anchors (B1) need no further scan of the 108M-edge file.
    """
    t0 = time.time()
    pp = _prep_paths(dirs, tag)
    if prep_is_fresh(pp, memb_path, edges_path):
        meta = read_json(pp["meta"], {}) or {}
        print(f"[prep] {tag}: fresh "
              f"({meta.get('n_internal_edges', 0):,} internal edges, "
              f"{meta.get('n_cross_rows', 0):,} cross rows) -> {pp['dir']}")
        return meta
    os.makedirs(pp["dir"], exist_ok=True)
    memb = np.load(memb_path)
    n = len(memb)
    G = int(memb.max()) + 1
    E = load_edges_mmap(edges_path)

    # ---- pass 1: internal-edge counts per galaxy + grouped cross pair keys
    cnt = np.zeros(G, np.int64)
    key_chunks = []
    for i in range(0, len(E), CH):
        blk = np.asarray(E[i:i + CH]).astype(np.int64)
        u, v = blk[:, 0], blk[:, 1]
        gu, gv = memb[u], memb[v]
        same = gu == gv
        if same.any():
            cnt += np.bincount(gu[same], minlength=G)
        cr = np.flatnonzero(~same)
        if len(cr):
            uu, vv = u[cr], v[cr]
            guu, gvv = gu[cr], gv[cr]
            key_chunks.append(uu * G + gvv)
            key_chunks.append(vv * G + guu)
        if (i // CH) % 5 == 0:
            print(f"  prep pass1 {i + len(blk):,}/{len(E):,} "
                  f"({100.0 * (i + len(blk)) / max(1, len(E)):.0f}%, "
                  f"{time.time() - t0:.0f}s)", flush=True)
    keys = np.concatenate(key_chunks) if key_chunks else np.zeros(0, np.int64)
    del key_chunks
    uniq, w = np.unique(keys, return_counts=True)
    del keys
    cu = (uniq // G).astype(np.int32)
    cg = (uniq % G).astype(np.int32)
    cw = w.astype(np.int32)
    del uniq, w
    ext = np.bincount(cu.astype(np.int64), weights=cw.astype(np.float64),
                      minlength=n)
    ext = np.rint(ext).astype(np.int32)

    # ---- pass 2: bucket internal edges (order identical to the pre-B0 code:
    #      per-chunk stable sort by community + rank scatter; the F7 asserts stay)
    offs = np.zeros(G + 1, np.int64)
    offs[1:] = np.cumsum(cnt)
    buf = np.empty((int(cnt.sum()), 2), np.int32)
    fill = offs[:-1].copy()
    for i in range(0, len(E), CH):
        blk = np.asarray(E[i:i + CH])
        u, v = blk[:, 0].astype(np.int64), blk[:, 1].astype(np.int64)
        gu, gv = memb[u], memb[v]
        same = gu == gv
        if not same.any():
            continue
        c = gu[same]
        us, vs = u[same].astype(np.int32), v[same].astype(np.int32)
        oc = np.argsort(c, kind="stable")
        cs = c[oc]
        uniq_c, first = np.unique(cs, return_index=True)
        uidx = np.searchsorted(uniq_c, cs)
        rank = np.arange(len(cs)) - first[uidx]
        pos = fill[uniq_c][uidx] + rank
        buf[pos, 0] = us[oc]
        buf[pos, 1] = vs[oc]
        fill[uniq_c] += np.diff(np.append(first, len(cs)))
        if (i // CH) % 5 == 0:
            print(f"  prep pass2 {i + len(blk):,}/{len(E):,} "
                  f"({100.0 * (i + len(blk)) / max(1, len(E)):.0f}%, "
                  f"{time.time() - t0:.0f}s)", flush=True)
    assert np.array_equal(fill, offs[1:])
    # per-article internal degree (B2 core/hub stratification input)
    int_deg = (np.bincount(buf[:, 0].astype(np.int64), minlength=n)
               + np.bincount(buf[:, 1].astype(np.int64), minlength=n))
    int_deg = int_deg.astype(np.int32)

    np.save(pp["offs"], offs)
    np.save(pp["buf"], buf)
    np.save(pp["cu"], cu)
    np.save(pp["cg"], cg)
    np.save(pp["cw"], cw)
    np.save(pp["ext"], ext)
    np.save(pp["int"], int_deg)
    meta = {"schema": PREP_SCHEMA, "generated_at": _now(), "tag": tag,
            "n_articles": n, "n_galaxies": G,
            "n_internal_edges": int(cnt.sum()),
            "n_cross_rows": int(len(cu)),
            "sources": _source_stamp(memb_path, edges_path),
            "secs": round(time.time() - t0, 1)}
    write_json(pp["meta"], meta)
    print(f"[prep] {tag}: {int(cnt.sum()):,} internal edges, "
          f"{len(cu):,} grouped cross rows ({meta['secs']}s) -> {pp['dir']}")
    return meta


def ensure_prep(dirs: Dirs, tag: str, memb_path: str, edges_path: str,
                auto: bool = True) -> dict:
    """Return prep paths; build them when missing/stale if auto (single process)."""
    pp = _prep_paths(dirs, tag)
    if not prep_is_fresh(pp, memb_path, edges_path):
        if not auto:
            raise RuntimeError(
                f"prep artifacts missing/stale for tag {tag!r}; run "
                f"`layout_local.py --prep --galaxy-tag {tag}` first "
                f"(the parallel launcher does this automatically)")
        cmd_prep(dirs, tag, memb_path, edges_path)
    return pp


def load_prep(pp: dict) -> dict:
    """Memory-map the shared prep artifacts (read-only, zero-copy across jobs)."""
    return {
        "offs": np.load(pp["offs"], mmap_mode="r"),
        "buf": np.load(pp["buf"], mmap_mode="r"),
        "cu": np.load(pp["cu"], mmap_mode="r"),
        "cg": np.load(pp["cg"], mmap_mode="r"),
        "cw": np.load(pp["cw"], mmap_mode="r"),
        "ext": np.load(pp["ext"], mmap_mode="r"),
        "int": np.load(pp["int"], mmap_mode="r"),
    }


def build_anchors(prep: dict, memb: np.ndarray, centers: np.ndarray,
                  n: int) -> np.ndarray:
    """Per-article anchor vector = sum over grouped cross pairs of
    w * unit(centre[neighbour] - centre[own]); chunked, bincount accumulation
    (mathematically identical to the pre-B0 per-edge np.add.at, ~4x faster)."""
    cu, cg, cw = prep["cu"], prep["cg"], prep["cw"]
    anchor = np.zeros((n, 3), np.float64)
    own = memb[cu]
    for i in range(0, len(cu), CH):
        sl = slice(i, min(i + CH, len(cu)))
        d = centers[cg[sl].astype(np.int64)] - centers[own[sl].astype(np.int64)]
        dn = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
        wv = cw[sl].astype(np.float64)
        u64 = cu[sl].astype(np.int64)
        for dim in range(3):
            anchor[:, dim] += np.bincount(u64, weights=wv * dn[:, dim],
                                          minlength=n)
    return anchor


def build_anchor_dirs(prep: dict, memb: np.ndarray, centers: np.ndarray,
                      n: int, ratio: float = 0.5):
    """B1 sector anchors: per-article direction toward its dominant neighbour.

    Returns (dir, conf). dir = unit vector toward the top-1 neighbour galaxy,
    blended with top-2 when w2 >= ratio*w1 (articles tied to two galaxies sit
    between them); conf = share of the article's cross-edge weight carried by
    the blended neighbours = pull gain (diffuse "cosmopolitan" articles get a
    weak pull and stay interior). Replaces the legacy summed-unit-vector
    anchor, where opposing links cancelled and multi-direction articles lost
    their anchor entirely (02 §O, D34). Vectorized over the sorted grouped
    cross pairs: one argsort by (article, weight desc) + segment heads.
    """
    cu, cg, cw = prep["cu"], prep["cg"], prep["cw"]
    dirv = np.zeros((n, 3))
    conf = np.zeros(n)
    if len(cu) == 0:
        return dirv, conf
    wmax = int(cw.max())
    keys = cu.astype(np.int64) * (wmax + 1) + (wmax - cw.astype(np.int64))
    ordw = np.argsort(keys, kind="stable")
    cu_s, cg_s, cw_s = cu[ordw], cg[ordw], cw[ordw]
    starts = np.concatenate([[0], np.flatnonzero(np.diff(cu_s) != 0) + 1])
    ends = np.concatenate([starts[1:], [len(cu_s)]])
    i1 = starts
    i2 = np.where(ends - starts > 1, starts + 1, -1)
    safe2 = np.maximum(i2, 0)
    u1 = cu_s[i1].astype(np.int64)
    w1 = cw_s[i1].astype(np.float64)
    w2 = np.where(i2 >= 0, cw_s[safe2], 0).astype(np.float64)
    g1 = cg_s[i1].astype(np.int64)
    g2 = np.where(i2 >= 0, cg_s[safe2], 0).astype(np.int64)
    own = memb[u1]
    d1 = centers[g1] - centers[own]
    d1 /= np.maximum(np.linalg.norm(d1, axis=1, keepdims=True), 1e-9)
    blend = (i2 >= 0) & (w2 >= ratio * w1)
    d2 = centers[g2] - centers[own]
    d2 /= np.maximum(np.linalg.norm(d2, axis=1, keepdims=True), 1e-9)
    vec = w1[:, None] * d1 + np.where(blend[:, None], w2[:, None] * d2, 0.0)
    vec /= np.maximum(np.linalg.norm(vec, axis=1, keepdims=True), 1e-9)
    dirv[u1] = vec
    used = w1 + np.where(blend, w2, 0.0)
    extd = np.asarray(prep["ext"], np.float64)
    conf[u1] = used / np.maximum(extd[u1], 1.0)
    return dirv, conf


def _rank_quantile(v: np.ndarray) -> np.ndarray:
    """Within-galaxy rank quantile in [0, 1] (scale-free across galaxy sizes;
    B2 replaces the fixed ext/20 saturation with this)."""
    n = len(v)
    if n < 2:
        return np.zeros(n)
    idx = np.argsort(v, kind="stable")
    q = np.empty(n)
    q[idx] = np.arange(n) / (n - 1)
    return q


def cmd_merge(dirs: Dirs, lay_dir: str, memb: np.ndarray, n: int, G: int):
    """Merge per-galaxy shards into article_positions.parquet (scan-free).

    Also REPAIRS the done-checkpoint: parallel jobs never share it (they race
    on overwrites), so the union of {main checkpoint, job metas' done lists,
    galaxies whose shard file exists} is the ground truth. Shard existence is
    authoritative: a shard is written only after its galaxy completed.
    """
    shard_dir = os.path.join(lay_dir, "article_shards")
    ck_path = os.path.join(lay_dir, "layout_local_checkpoint.json")
    ck = read_json(ck_path, {}) or {"done": {}}
    done = set(int(x) for x in ck.get("done", []))
    pj = os.path.join(lay_dir, "parallel_jobs")
    if os.path.isdir(pj):
        for fn in os.listdir(pj):
            if fn.endswith(".meta.json"):
                jm = read_json(os.path.join(pj, fn), {}) or {}
                done.update(int(x) for x in jm.get("done", []))
    if os.path.isdir(shard_dir):
        for fn in os.listdir(shard_dir):
            if fn.startswith("gal_") and fn.endswith(".npy"):
                done.add(int(fn[4:10]))
    done = sorted(x for x in done if 0 <= x < G)
    if len(done) != len(ck.get("done", [])):
        write_json(ck_path, {"done": done})
        print(f"[local] checkpoint repaired: {len(ck.get('done', []))} -> "
              f"{len(done)} done")
    order = np.argsort(memb, kind="stable")
    starts = np.searchsorted(memb[order], np.arange(G), side="left")
    ends = np.searchsorted(memb[order], np.arange(G), side="right")
    article_ids = np.load(dirs.article_ids)
    P = np.empty((n, 3), np.float32)
    for g in range(G):
        members = order[starts[g]:ends[g]]
        if len(members) == 0:
            continue
        sh = np.load(os.path.join(shard_dir, f"gal_{g:06d}.npy"))
        P[members] = sh
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({
        "idx": pa.array(np.arange(n, dtype=np.int32)),
        "page_id": pa.array(article_ids),
        "galaxy_id": pa.array(memb),
        "x": pa.array(P[:, 0]), "y": pa.array(P[:, 1]), "z": pa.array(P[:, 2]),
    }), os.path.join(lay_dir, "article_positions.parquet"), compression="zstd")
    print(f"[local] merged {n:,} article positions -> "
          f"{os.path.join(lay_dir, 'article_positions.parquet')}")


def main(a=None):
    # a=None のときだけ CLI 引数を解析する(ステージからは Namespace を
    # 注入して呼ぶ = 引数解析と処理本体の分離。CLI 挙動は不変)。
    if a is None:
        ap = argparse.ArgumentParser()
        ap.add_argument("--base", default="data")
        ap.add_argument("--galaxy-tag", default="res1_sub")
        ap.add_argument("--run", default=ACTIVE_LAYOUT_RUN,
                        help="layout run name under data/layout "
                             "(default: wu.paths.ACTIVE_LAYOUT_RUN)")
        ap.add_argument("--galaxies", default="all",
                        help="'all' or 'A-B' inclusive id range (one batch job)")
        ap.add_argument("--job-spec", default=None,
                        help="path to a launcher-written gid list (LPT bin); "
                             "overrides --galaxies")
        ap.add_argument("--prep", action="store_true",
                        help="build/refresh the shared prep artifacts and exit")
        ap.add_argument("--merge-only", action="store_true",
                        help="merge existing shards into article_positions.parquet "
                             "and exit (no edge scans, no anchor computation)")
        ap.add_argument("--fr-mode", default="ml", choices=["ml", "flat"],
                        help="ml = multilevel-seeded FR for galaxies with n >= "
                             "--ml-threshold (default); flat = legacy single-level "
                             "FR (comparison arm)")
        ap.add_argument("--ml-threshold", type=int, default=2000,
                        help="article count where the multilevel FR kicks in "
                             "(smaller galaxies keep the exact legacy flat path)")
        ap.add_argument("--ml-niter", type=int, default=100,
                        help="FR refine iterations on top of the multilevel seed "
                             "(the flat path keeps igraph's default 500)")
        ap.add_argument("--anchor-mode", default="sector", choices=["sector", "sum"],
                        help="sector = B1 dominant-neighbour sector direction with "
                             "confidence-scaled gain (default); sum = legacy "
                             "summed-unit-vector anchor (comparison arm)")
        ap.add_argument("--fr-norm", default="p98", choices=["p98", "max"],
                        help="p98 = recentred + robust p98 scale with outlier clamp "
                             "(B2-a, default); max = legacy max-norm (comparison arm)")
        ap.add_argument("--sector-ratio", type=float, default=0.5,
                        help="blend the top-2 neighbour direction when w2 >= ratio*w1")
        ap.add_argument("--iters", type=int, default=40)
        ap.add_argument("--kappa", type=float, default=0.05, help="anchor pull gain")
        ap.add_argument("--lam", type=float, default=0.06, help="elastic prior gain")
        ap.add_argument("--seed", type=int, default=42)
        ap.add_argument("--preview-galaxy", type=int, default=None,
                        help="after the run, render a 3-view scatter of this galaxy's "
                             "article shard (layout/<run>/preview_galaxy_<id>.png)")
        a = ap.parse_args()
    dirs = Dirs(a.base)
    lay_dir = str(dirs.layout_run(a.run))
    gpos_path = os.path.join(lay_dir, "galaxy_positions.parquet")
    if not a.prep and not os.path.exists(gpos_path):
        sys.exit(f"[local] {gpos_path} not found: run "
                 f"`python scripts/layout_global.py --base {a.base} "
                 f"--run {a.run}` first (the local layout needs this run's "
                 f"galaxy centers/radii)")
    shard_dir = os.path.join(lay_dir, "article_shards")
    os.makedirs(shard_dir, exist_ok=True)
    t0 = time.time()

    import pyarrow.parquet as pq

    memb_path = str(dirs.community_full / f"membership_{a.galaxy_tag}.npy")
    edges_path = os.path.join(dirs.graph, "edges_undirected_unique.bin")
    memb = np.load(memb_path)
    n = len(memb)
    G = int(memb.max()) + 1

    if a.prep:
        cmd_prep(dirs, a.galaxy_tag, memb_path, edges_path)
        return

    pp = ensure_prep(dirs, a.galaxy_tag, memb_path, edges_path,
                     auto=not bool(a.job_spec))
    prep = load_prep(pp)

    if a.merge_only:
        cmd_merge(dirs, lay_dir, memb, n, G)
        meta = {"generated_at": _now(), "galaxy_tag": a.galaxy_tag,
                "run": a.run, "mode": "merge-only", "n_galaxies": G,
                "secs": round(time.time() - t0, 1)}
        par = os.path.join(lay_dir, "parallel_meta.json")
        if os.path.exists(par):
            meta["parallel"] = read_json(par, {})
        write_json(os.path.join(lay_dir, "layout_local_meta.json"), meta)
        return

    gpos = pq.read_table(gpos_path).to_pydict()
    centers = np.stack([gpos["x"], gpos["y"], gpos["z"]], axis=1).astype(np.float64)
    radii = np.asarray(gpos["radius"], np.float64)

    offs = np.asarray(prep["offs"])
    buf = prep["buf"]

    # ---- galaxy list for this job
    if a.job_spec:
        gids = np.loadtxt(a.job_spec, dtype=np.int64, ndmin=1)
        glo, ghi = int(gids.min()), int(gids.max())
        selected = set(int(x) for x in gids)
    elif a.galaxies == "all":
        glo, ghi = 0, G - 1
        selected = None
    else:
        lo, hi = a.galaxies.split("-")
        glo, ghi = max(0, int(lo)), min(G - 1, int(hi))
        selected = None

    ck_path = os.path.join(lay_dir, "layout_local_checkpoint.json")
    ck = read_json(ck_path, {}) or {"done": {}}
    done = set(int(k) for k in ck["done"])

    # ---- run-dependent anchors, lazily: a no-op invocation (everything done,
    #      e.g. preview-only) must not pay the multi-second anchor rebuild
    to_do = sum(1 for x in range(glo, ghi + 1)
                if x not in done and (selected is None or x in selected))
    anchor = anchor_dir = anchor_conf = ext_deg = int_deg = None
    anchor_secs = 0.0
    if to_do:
        ta = time.time()
        print(f"[local] building {a.anchor_mode} anchors from "
              f"{len(prep['cu']):,} grouped cross rows ...", flush=True)
        ext_deg = np.asarray(prep["ext"])
        int_deg = np.asarray(prep["int"])
        if a.anchor_mode == "sector":
            anchor_dir, anchor_conf = build_anchor_dirs(
                prep, memb, centers, n, ratio=a.sector_ratio)
        else:
            anchor = build_anchors(prep, memb, centers, n)
        anchor_secs = round(time.time() - ta, 1)
        print(f"[local] anchors from prep ({len(prep['cu']):,} grouped rows) "
              f"in {anchor_secs}s", flush=True)

    # parallel jobs report progress/telemetry through side files (their stdout
    # belongs to a log file; the launcher polls the .progress line)
    prog_path = job_meta_path = None
    if a.job_spec:
        stem = (a.job_spec[:-len(".gids.txt")]
                if a.job_spec.endswith(".gids.txt") else a.job_spec)
        prog_path = stem + ".progress"
        job_meta_path = stem + ".meta.json"
    job_total = sum(1 for x in range(glo, ghi + 1)
                    if x not in done and (selected is None or x in selected))

    order = np.argsort(memb, kind="stable")
    starts = np.searchsorted(memb[order], np.arange(G), side="left")
    ends = np.searchsorted(memb[order], np.arange(G), side="right")
    times = []
    n_done = 0
    fr_stats = {"ml": 0}
    q_rec, q_cv, q_rad, q_n = [], [], [], 0
    skipped = sum(1 for x in range(glo, ghi + 1)
                  if x in done and (selected is None or x in selected))
    print(f"[local] range {glo}-{ghi}: {skipped} already done (resume), "
          f"to process", flush=True)
    for g in range(glo, ghi + 1):
        if g in done or (selected is not None and g not in selected):
            continue
        members = order[starts[g]:ends[g]]
        ng = len(members)
        if ng == 0:
            done.add(g)
            continue
        tg = time.time()
        R = float(radii[g])
        if ng == 1:
            pos_g = np.zeros((1, 3))
        else:
            le = np.asarray(buf[offs[g]:offs[g + 1]])
            loc = np.stack([np.searchsorted(members, le[:, 0].astype(np.int64)),
                            np.searchsorted(members, le[:, 1].astype(np.int64))],
                           axis=1).astype(np.int32)
            fr = _fr_unit(ng, loc, 3, a.seed + g, fr_mode=a.fr_mode,
                          ml_threshold=a.ml_threshold, ml_niter=a.ml_niter,
                          stats=fr_stats, fr_norm=a.fr_norm)
            pos_g = fr * (R * 0.92)
            ext_m = ext_deg[members].astype(np.float64)
            if a.anchor_mode == "sector":
                # B1: sector direction + confidence gain; B2: rank-quantile
                # radial band + hub-core correction (scale-free per galaxy)
                av = anchor_dir[members]
                cf = anchor_conf[members]
                has = cf > 1e-9
                q_ext = _rank_quantile(ext_m)
                q_int = _rank_quantile(int_deg[members].astype(np.float64))
                tgt_r = R * (0.35 + 0.65 * q_ext)
                core = (q_int > 0.9) & (q_ext < 0.5)
                tgt_r[core] *= 0.6
                target = av * tgt_r[:, None]
                gain = np.zeros(ng)
                gain[has] = a.kappa * cf[has]
            else:
                # legacy: summed-unit-vector anchor + fixed /20 band
                av = anchor[members]
                avn = np.linalg.norm(av, axis=1)
                has = avn > 1e-9
                av_unit = np.zeros_like(av)
                av_unit[has] = av[has] / avn[has][:, None]
                # boundary radius per article: more external links -> surface
                tgt_r = R * (0.55 + 0.45 * np.clip(ext_m / 20.0, 0, 1))
                target = av_unit * tgt_r[:, None]
                gain = np.zeros(ng)
                gain[has] = a.kappa
            for _ in range(a.iters):
                pull = np.zeros_like(pos_g)
                pull[has] = gain[has][:, None] * (target[has] - pos_g[has])
                prior = a.lam * (fr * (R * 0.92) - pos_g)
                pos_g = pos_g + pull + prior
                rg = np.linalg.norm(pos_g, axis=1)
                over = rg > R
                if over.any():
                    pos_g[over] *= (R / rg[over])[:, None]
        if 200 <= ng <= 20000 and q_n < 60:
            q = galaxy_layout_quality(pos_g, loc)
            if q:
                q_rec.append(q["adj_recall"])
                if q["edge_len_cv"] is not None:
                    q_cv.append(q["edge_len_cv"])
                q_rad.append(float(np.median(np.linalg.norm(pos_g, axis=1)) / R))
                q_n += 1
        np.save(os.path.join(shard_dir, f"gal_{g:06d}.npy"),
                (centers[g] + pos_g).astype(np.float32))
        done.add(g)
        n_done += 1
        dt = time.time() - tg
        times.append(dt)
        print(f"  g={g} n={ng:,} e={int(offs[g + 1] - offs[g]):,} {dt:.2f}s",
              flush=True)
        if prog_path and (n_done % 5 == 0 or n_done == job_total):
            el = time.time() - t0
            mean = float(np.mean(times))
            with open(prog_path, "w", encoding="utf-8") as fh:
                fh.write(f"{n_done} {job_total} {el:.1f} "
                         f"{mean * (job_total - n_done):.1f}\n")
        if n_done % 50 == 0:
            if a.job_spec is None:
                write_json(ck_path, {"done": sorted(done)})
            else:
                write_json(job_meta_path, {
                    "n_done": n_done, "wall_secs": round(time.time() - t0, 1),
                    "per_galaxy_secs": sorted(round(t, 4) for t in times),
                    "done": sorted(done)})
            el = time.time() - t0
            mean = float(np.mean(times))
            rem = sum(1 for x in range(g + 1, ghi + 1)
                      if x not in done and (selected is None or x in selected))
            print(f"[local] {len(done):,}/{G:,} done ({n_done} this run), "
                  f"elapsed {el / 60:.1f}m, eta {mean * rem / 60:.1f}m "
                  f"(mean {mean:.2f}s/galaxy)", flush=True)
    if a.job_spec is None:
        write_json(ck_path, {"done": sorted(done)})

    # ---- optional single-galaxy preview
    if a.preview_galaxy is not None:
        g = a.preview_galaxy
        shp = os.path.join(shard_dir, f"gal_{g:06d}.npy")
        if not os.path.exists(shp):
            print(f"[local] preview: shard for galaxy {g} not found "
                  f"(run with --galaxies covering it)")
        else:
            P = np.load(shp).astype(np.float64)
            c = centers[g]
            R = float(radii[g])
            rel = P - c
            r = np.linalg.norm(rel, axis=1)
            print(f"[local] preview galaxy {g}: n={len(P)} R={R:.1f} "
                  f"r/R p50={np.percentile(r / max(R, 1e-9), 50):.2f} "
                  f"p95={np.percentile(r / max(R, 1e-9), 95):.2f} "
                  f"max={r.max() / max(R, 1e-9):.2f}")
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                fig, axes = plt.subplots(1, 3, figsize=(15, 5.2), facecolor="black")
                for ax, (i1, i2, nm) in zip(axes, [(0, 1, "xy"), (0, 2, "xz"),
                                                   (1, 2, "yz")]):
                    ax.set_facecolor("black")
                    ax.scatter(rel[:, i1], rel[:, i2], s=1.2, c="#9ecbff",
                               alpha=0.8, linewidths=0)
                    ax.add_patch(plt.Circle((0, 0), R, fill=False, ec="#555555",
                                            lw=0.8))
                    ax.set_aspect("equal")
                    ax.axis("off")
                    ax.set_title(f"{nm}  (ball R={R:.0f})", color="#888888",
                                 fontsize=9)
                fig.suptitle(f"galaxy {g}: article layout (n={len(P)})",
                               color="white", fontsize=10)
                fig.tight_layout()
                fp = os.path.join(lay_dir, f"preview_galaxy_{g}.png")
                fig.savefig(fp, dpi=120, facecolor="black")
                plt.close(fig)
                print(f"[local] preview -> {fp}")
            except Exception as e:  # previews optional
                print(f"[local] preview skipped: {e}")

    # ---- merge when complete (any single-process invocation triggers it once
    #      every galaxy is done; parallel jobs skip it and the launcher merges
    #      via --merge-only)
    merged = None
    if a.job_spec is None and len(done) >= G:
        cmd_merge(dirs, lay_dir, memb, n, G)
        merged = "article_positions.parquet"
    meta = {"generated_at": _now(), "galaxy_tag": a.galaxy_tag, "run": a.run,
            "galaxies_range": [glo, ghi],
            "job_spec": os.path.basename(a.job_spec) if a.job_spec else None,
            "n_done_this_run": n_done, "n_done_total": len(done),
            "n_galaxies": G,
            "params": {"iters": a.iters, "kappa": a.kappa, "lam": a.lam,
                       "seed": a.seed, "fr_mode": a.fr_mode,
                       "ml_threshold": a.ml_threshold,
                       "ml_niter": a.ml_niter,
                       "anchor_mode": a.anchor_mode, "fr_norm": a.fr_norm,
                       "sector_ratio": a.sector_ratio},
            "fr_quality": {
                "n_eval": q_n,
                "adj_recall_mean": round(float(np.mean(q_rec)), 4) if q_rec else None,
                "edge_len_cv_mean": round(float(np.mean(q_cv)), 4) if q_cv else None,
                "radial_p50_mean": round(float(np.mean(q_rad)), 4) if q_rad else None,
                "n_ml_applied": fr_stats["ml"]},
            "prep": {"dir": str(pp["dir"]), "anchor_secs": anchor_secs},
            "secs": round(time.time() - t0, 1)}
    if times:
        tt = np.array(times)
        meta["per_galaxy_secs"] = {"p50": round(float(np.percentile(tt, 50)), 4),
                                   "p95": round(float(np.percentile(tt, 95)), 4),
                                   "max": round(float(tt.max()), 4)}
    if merged:
        meta["merged"] = merged
    if n_done == 0:
        # preview-only / no-op invocations must not erase the parallel telemetry
        par = os.path.join(lay_dir, "parallel_meta.json")
        if os.path.exists(par):
            meta["parallel"] = read_json(par, {})
    if job_meta_path and times:
        write_json(job_meta_path, {
            "n_done": n_done, "wall_secs": round(time.time() - t0, 1),
            "per_galaxy_secs": sorted(round(t, 4) for t in times),
            # job-level quality sums; the launcher averages them into
            # parallel_meta.fr_quality (single-process runs write fr_quality
            # directly, parallel jobs used to lose it entirely)
            "done": sorted(done),
            "fr_quality": {"n_eval": q_n,
                           "adj_recall_sum": round(float(sum(q_rec)), 4),
                           "edge_len_cv_sum": round(float(sum(q_cv)), 4),
                           "n_cv": len(q_cv),
                           "radial_sum": round(float(sum(q_rad)), 4),
                           "n_ml_applied": fr_stats["ml"]}})
    # parallel jobs skip the run meta write (the launcher's --merge-only owns it)
    if a.job_spec is None:
        write_json(os.path.join(lay_dir, "layout_local_meta.json"), meta)
    el = time.time() - t0
    rem_all = G - len(done)
    eta = (float(np.mean(times)) * rem_all / 60) if times and rem_all else 0.0
    if q_rec:
        cv = f"{np.mean(q_cv):.3f}" if q_cv else "n/a"
        rad = f"{np.mean(q_rad):.3f}" if q_rad else "n/a"
        print(f"[local] fr_quality: mode={a.fr_mode} anchor={a.anchor_mode} "
              f"norm={a.fr_norm} ml_applied={fr_stats['ml']} "
              f"adj_recall={np.mean(q_rec):.3f} edge_cv={cv} radial_p50={rad} "
              f"(n_eval={q_n})")
    print(f"[local] done: {n_done} galaxies this run, {len(done):,}/{G:,} total "
          f"({meta['secs']}s"
          + (f", per-galaxy p50={meta['per_galaxy_secs']['p50']}s"
             if times and "per_galaxy_secs" in meta else "")
          + (f", remaining {rem_all:,} galaxies ~{eta:.0f}m at this pace"
             if rem_all else ", ALL COMPLETE") + ")")


if __name__ == "__main__":
    main()
