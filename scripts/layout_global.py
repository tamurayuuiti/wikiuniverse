"""Global hierarchical layout (Phase 3, step 2): macro disks + galaxy disks.

Design (matches D16 policy "銀河を空間的に分離して配置"):
  1. Macro level: weighted force-layout (igraph FR) over effective macros using
     macro_pairs aggregated weights -> macro centers. Macro radius R_M is
     proportional to sqrt(n_articles) so that disk areas sum to the canvas.
  2. Macro-macro disk relaxation: push overlapping disks apart (few iterations).
  3. Galaxy level: for each macro, weighted FR over its galaxies using
     intra-macro galaxy pair weights -> local unit-disk coordinates, composed
     into the macro disk. Galaxy radius r_g = pack * R_M * sqrt(n_g / n_M).
  4. Intra-macro disk relaxation (no overlaps within a macro).
  5. Dust galaxies (n=1) get positions on a far shell (cosmic dust ring).

Outputs (data/layout/):
  galaxy_positions.parquet  galaxy_id, macro_id, x, y, z, radius, display_class, n_articles
  macro_positions.parquet   macro_id, x, y, z, radius, n_articles, n_galaxies
  layout_meta.json          params/seed/timings
  preview.png               2D projection (xy)

Usage:
  python scripts/layout_global.py --base data [--dim 3] [--pack 0.6] [--seed 42]
      [--pairs catalog|npz] [--galaxy-tag res1_sub]
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

from wu.dumpio import write_json  # noqa: E402
from wu.paths import Dirs  # noqa: E402

R_TOTAL = 1000.0  # canvas scale: disk areas sum to pi*R_TOTAL^2


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def fr_layout(n_nodes: int, edges, weights, dim: int, seed: int):
    """Weighted Fruchterman-Reingold via igraph; returns (n,dim) float array
    normalized to unit disk. Isolated nodes get deterministic spiral slots."""
    import igraph as ig
    random.seed(seed)
    np.random.seed(seed)
    g = ig.Graph(n=n_nodes)
    if len(edges):
        g.add_edges([(int(u), int(v)) for u, v in edges])
        if weights is not None:
            w = np.asarray(weights, np.float64)
            g.es["weight"] = (w / max(w.max(), 1e-9)).tolist()
        # NOTE: igraph FR `seed` param means initial coordinate matrix, not RNG seed;
        # determinism comes from random.seed() above (igraph uses Python random).
        lay = g.layout_fruchterman_reingold(weights="weight", dim=dim)
    else:
        lay = g.layout_fruchterman_reingold(dim=dim)
    coords = np.asarray(lay.coords, dtype=np.float64)
    # isolated nodes (degree 0) may sit at origin -> deterministic spiral
    deg = np.asarray(g.degree(), dtype=np.int64)
    iso = np.flatnonzero(deg == 0)
    if len(iso):
        k = np.arange(len(iso))
        ang = k * 2.399963229728653  # golden angle
        rad = 0.9 * np.sqrt((k + 0.5) / max(1, len(iso)))
        coords[iso, 0] = rad * np.cos(ang)
        coords[iso, 1] = rad * np.sin(ang)
        if dim == 3:
            coords[iso, 2] = 0.0
    r = np.linalg.norm(coords, axis=1)
    m = r.max()
    if m > 0:
        coords /= m
    return coords


def relax_disks(centers: np.ndarray, radii: np.ndarray, iters: int = 40) -> np.ndarray:
    """Push overlapping disks apart (vectorized O(k^2), k small)."""
    c = centers.astype(np.float64).copy()
    k = len(c)
    if k < 2:
        return c
    idx_i, idx_j = np.triu_indices(k, 1)
    ri, rj = radii[idx_i], radii[idx_j]
    min_d = (ri + rj) * 1.02
    for _ in range(iters):
        d = c[idx_j] - c[idx_i]
        dist = np.linalg.norm(d, axis=1)
        bad = dist < min_d
        if not bad.any():
            break
        push = (min_d[bad] - dist[bad])[:, None] * 0.5
        dirv = d[bad] / np.maximum(dist[bad], 1e-9)[:, None]
        c[idx_i[bad]] -= dirv * push
        c[idx_j[bad]] += dirv * push
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--dim", type=int, default=3, choices=[2, 3])
    ap.add_argument("--pack", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pairs", default="catalog", choices=["catalog", "npz"],
                    help="galaxy pair source: catalog topK parquet or full pairs npz")
    ap.add_argument("--galaxy-tag", default="res1_sub")
    a = ap.parse_args()
    dirs = Dirs(a.base)
    final = os.path.join(dirs.base, "final")
    out_dir = os.path.join(dirs.base, "layout")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    import pyarrow as pa
    import pyarrow.parquet as pq

    gal = pq.read_table(os.path.join(final, "galaxies.parquet")).to_pydict()
    mac = pq.read_table(os.path.join(final, "macros.parquet")).to_pydict()
    G = len(gal["galaxy_id"])
    Mac = len(mac["macro_id"])
    n_art_g = np.asarray(gal["n_articles"], np.int64)
    n_art_m = np.asarray(mac["n_articles"], np.int64)
    macro_of = np.asarray(gal["macro_id"], np.int64)

    # --- defensive reads: tolerate catalogs built by the pre-v2 builder ---
    skew = []
    if "is_dust" in gal:
        dust_g = np.asarray(gal["is_dust"], bool)
    else:
        dust_g = n_art_g <= 1
        skew.append("galaxies.is_dust")
    if "is_dust" in mac:
        dust_m = np.asarray(mac["is_dust"], bool)
    else:
        dust_m = n_art_m <= 1
        skew.append("macros.is_dust")
    if "display_class" in gal:
        cls = gal["display_class"]
    else:
        cls = ["dust" if dust_g[i] else "galaxy" for i in range(G)]
        skew.append("galaxies.display_class")
    if skew:
        print(f"[layout][warn] catalog is pre-v2 (missing: {', '.join(skew)}); "
              f"derived from n_articles. Re-run scripts/build_galaxy_catalog.py "
              f"(v2) for curated names / display_class (medium galaxies won't be "
              f"flagged in this run).")

    # ---------- 1. macro layout ----------
    eff_m = np.flatnonzero(~dust_m & (n_art_m > 0))
    midx = {int(m): i for i, m in enumerate(eff_m)}
    mp = pq.read_table(os.path.join(final, "macro_pairs.parquet")).to_pydict()
    me_a = np.asarray([midx.get(int(x), -1) for x in mp["macro_a"]])
    me_b = np.asarray([midx.get(int(x), -1) for x in mp["macro_b"]])
    ok = (me_a >= 0) & (me_b >= 0)
    m_edges = list(zip(me_a[ok].tolist(), me_b[ok].tolist()))
    del me_a, me_b
    m_w = np.asarray(mp["w"], np.float64)[ok]
    m_coords = fr_layout(len(eff_m), m_edges, m_w, a.dim, a.seed)
    R_m = R_TOTAL * np.sqrt(n_art_m[eff_m] / max(1, n_art_m.sum()))
    # spread: scale FR unit-disk coords so typical separation ~ sum radii scale
    m_centers = m_coords * (R_TOTAL * 1.6)
    m_centers = relax_disks(m_centers, R_m, iters=80)
    print(f"[layout] macros: {len(eff_m)} placed ({time.time()-t0:.1f}s)")

    # ---------- 2. galaxy pairs ----------
    if a.pairs == "npz":
        pz = np.load(os.path.join(dirs.community, "full", f"pairs_{a.galaxy_tag}.npz"))
        gp_a, gp_b, gp_w = pz["a"].astype(np.int64), pz["b"].astype(np.int64), pz["w"].astype(np.float64)
    else:
        gpq = pq.read_table(os.path.join(final, "galaxy_pairs_topK.parquet")).to_pydict()
        gp_a = np.asarray(gpq["a"], np.int64)
        gp_b = np.asarray(gpq["b"], np.int64)
        gp_w = np.asarray(gpq["w"], np.float64)
    mac_a = macro_of[gp_a]
    mac_b = macro_of[gp_b]
    same_macro = mac_a == mac_b
    del mac_b

    # ---------- 3. per-macro galaxy layout ----------
    g_centers = np.zeros((G, a.dim), np.float64)
    g_radius = np.zeros(G, np.float64)
    placed = np.zeros(G, bool)
    for m_i, m in enumerate(eff_m):
        members = np.flatnonzero((macro_of == m) & (~dust_g))
        if len(members) == 0:
            continue
        Rm = R_m[m_i]
        n_m = n_art_m[m]
        g_radius[members] = a.pack * Rm * np.sqrt(n_art_g[members] / max(1, n_m))
        local_idx = {int(g): i for i, g in enumerate(members)}
        sel = same_macro & (mac_a == m)
        e_local = [(local_idx[int(gp_a[i])], local_idx[int(gp_b[i])]) for i in np.flatnonzero(sel)]
        w_local = gp_w[sel]
        if len(members) == 1:
            coords = np.zeros((1, a.dim))
        else:
            coords = fr_layout(len(members), e_local, w_local, a.dim, a.seed + int(m))
        avail = max(Rm - g_radius[members].max(), Rm * 0.3)
        centers_local = relax_disks(coords * avail, g_radius[members], iters=40)
        g_centers[members] = m_centers[m_i] + centers_local
        placed[members] = True
    print(f"[layout] galaxies placed: {int(placed.sum())} ({time.time()-t0:.1f}s)")

    # ---------- 4. dust shell ----------
    dust_idx = np.flatnonzero(dust_g | ~placed)
    if len(dust_idx):
        shell = np.linalg.norm(m_centers, axis=1).max() * 1.5 + R_TOTAL * 0.2
        k = np.arange(len(dust_idx))
        yv = 1 - 2 * (k + 0.5) / len(k)
        rr = np.sqrt(np.maximum(0, 1 - yv ** 2))
        phi = k * 2.399963229728653
        g_centers[dust_idx, 0] = shell * rr * np.cos(phi)
        g_centers[dust_idx, 1] = shell * rr * np.sin(phi)
        if a.dim == 3:
            g_centers[dust_idx, 2] = shell * yv
        else:
            g_centers[dust_idx, 2:] = 0.0
        g_radius[dust_idx] = a.pack * R_TOTAL * np.sqrt(1 / max(1, n_art_m.sum()))

    # ---------- 5. outputs ----------
    zcol = g_centers[:, 2] if a.dim == 3 else np.zeros(G)
    pq.write_table(pa.table({
        "galaxy_id": pa.array(np.arange(G, dtype=np.int32)),
        "macro_id": pa.array(macro_of.astype(np.int32)),
        "x": pa.array(g_centers[:, 0]), "y": pa.array(g_centers[:, 1]),
        "z": pa.array(zcol), "radius": pa.array(g_radius),
        "display_class": pa.array([str(c) for c in cls], type=pa.string()),
        "n_articles": pa.array(n_art_g.astype(np.int32)),
    }), os.path.join(out_dir, "galaxy_positions.parquet"), compression="zstd")

    mz = m_centers[:, 2] if a.dim == 3 else np.zeros(len(eff_m))
    full_mx = np.zeros(Mac); full_my = np.zeros(Mac); full_mz = np.zeros(Mac); full_R = np.zeros(Mac)
    for i, m in enumerate(eff_m):
        full_mx[m], full_my[m], full_mz[m], full_R[m] = m_centers[i, 0], m_centers[i, 1], mz[i], R_m[i]
    pq.write_table(pa.table({
        "macro_id": pa.array(np.arange(Mac, dtype=np.int32)),
        "x": pa.array(full_mx), "y": pa.array(full_my), "z": pa.array(full_mz),
        "radius": pa.array(full_R),
        "n_articles": pa.array(n_art_m.astype(np.int32)),
        "n_galaxies": pa.array(np.asarray(mac["n_galaxies"], np.int32)),
        "is_dust": pa.array(dust_m),
    }), os.path.join(out_dir, "macro_positions.parquet"), compression="zstd")

    meta = {"generated_at": _now(), "dim": a.dim, "pack": a.pack, "seed": a.seed,
            "pairs_source": a.pairs, "galaxy_tag": a.galaxy_tag,
            "n_galaxies": G, "n_macros_effective": int(len(eff_m)),
            "n_dust_shell": int(len(dust_idx)), "R_TOTAL": R_TOTAL,
            "secs": round(time.time() - t0, 1)}
    write_json(os.path.join(out_dir, "layout_meta.json"), meta)

    # ---------- 6. preview ----------
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 11), facecolor="black")
        ax.set_facecolor("black")
        show = ~dust_g
        cmap = plt.get_cmap("tab20")
        colors = [cmap(int(macro_of[i]) % 20) if cls[i] != "medium" else "#888888"
                  for i in np.flatnonzero(show)]
        ax.scatter(g_centers[show, 0], g_centers[show, 1],
                   s=np.clip(g_radius[show] * 0.8, 0.5, 400), c=colors, alpha=0.85,
                   linewidths=0)
        for i, m in enumerate(eff_m):
            ax.add_patch(plt.Circle((m_centers[i, 0], m_centers[i, 1]), R_m[i],
                                    fill=False, edgecolor="#444444", lw=0.6))
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(f"wikiuniverse global layout (dim={a.dim}, G={G}, seed={a.seed})",
                     color="white", fontsize=10)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "preview.png"), dpi=130, facecolor="black")
        plt.close(fig)
        print(f"[layout] preview -> {os.path.join(out_dir, 'preview.png')}")
    except Exception as e:  # matplotlib optional
        print(f"[layout] preview skipped: {e}")

    print(f"[layout] done ({meta['secs']}s) -> {out_dir}")


if __name__ == "__main__":
    main()
