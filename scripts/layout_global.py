"""Global hierarchical layout (Phase 3, step 2): macro disks + galaxy disks.

Design (matches D16 policy "銀河を空間的に分離して配置").
Hierarchical 2.5D: the universe map (macro level) is packed in 2D so that the
top-down view -- what users see at universe zoom -- has no overlapping macro
disks. Depth (z) appears only INSIDE macros: galaxies sit in a squashed 3D
lens (z *= --z-squash), relaxed in xy. Article positions (next phase) will be
full 3D inside each galaxy ball.

  1. Macro level: weighted FR (dim=2 always) over effective macros using
     macro_pairs -> centers; radius R_M proportional to sqrt(n_articles).
  2. Macro-macro disk relaxation + expansion loop until xy overlap <= 2%.
  3. Galaxy level: weighted FR (dim) over intra-macro galaxy pairs; xy used for
     disk placement/relaxation/clamping, z squashed for depth.
     Galaxy radius r_g = pack * R_M * sqrt(n_g / n_M).
  4. Intra-macro xy disk relaxation + radial clamp inside macro disk.
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


def disk_overlap_fraction(centers: np.ndarray, radii: np.ndarray) -> float:
    """Pairwise circle-circle intersection area / total disk area (2D projection)."""
    k = len(centers)
    if k < 2:
        return 0.0
    c2 = centers[:, :2]
    i, j = np.triu_indices(k, 1)
    d = np.linalg.norm(c2[j] - c2[i], axis=1)
    r0, r1 = radii[i], radii[j]
    inter = np.zeros(len(d))
    m = d < (r0 + r1)
    dd, a, b = d[m], r0[m], r1[m]
    dd = np.maximum(dd, 1e-9)
    part = np.clip((dd ** 2 + a ** 2 - b ** 2) / (2 * dd * a), -1, 1)
    part2 = np.clip((dd ** 2 + b ** 2 - a ** 2) / (2 * dd * b), -1, 1)
    inter[m] = (a ** 2 * np.arccos(part) + b ** 2 * np.arccos(part2)
                - 0.5 * np.sqrt(np.maximum(0, (-dd + a + b) * (dd + a - b) * (dd - a + b) * (dd + a + b))))
    total = np.pi * np.sum(radii ** 2)
    return float(inter.sum() / max(total, 1e-9))


def sphere_overlap_fraction(centers: np.ndarray, radii: np.ndarray) -> float:
    """Pairwise sphere-sphere intersection volume / total volume (3D)."""
    k = len(centers)
    if k < 2:
        return 0.0
    i, j = np.triu_indices(k, 1)
    d = np.maximum(np.linalg.norm(centers[j] - centers[i], axis=1), 1e-9)
    r0, r1 = radii[i], radii[j]
    m = d < (r0 + r1)
    dd, a, b = d[m], r0[m], r1[m]
    vol = (np.pi * (a + b - dd) ** 2 *
           (dd ** 2 + 2 * dd * (a + b) - 3 * (a - b) ** 2)) / (12 * dd)
    total = (4.0 / 3.0) * np.pi * np.sum(radii ** 3)
    return float(vol.sum() / max(total, 1e-9))


def projection_overlap_stats(centers: np.ndarray, radii: np.ndarray,
                             n_views: int = 24, seed: int = 7) -> dict:
    """Disk-overlap fraction of projected circles over canonical axes + random
    view directions. Quantifies 'how map-like' the layout is from each view."""
    rng = np.random.default_rng(seed)
    dirs3 = [np.array([0, 0, 1.0]), np.array([1, 0, 0.0]), np.array([0, 1, 0.0])]
    extra = rng.normal(size=(n_views, 3))
    extra /= np.linalg.norm(extra, axis=1, keepdims=True)
    dirs3 += [e for e in extra]
    vals = {}
    allv = []
    for w in dirs3:
        if abs(w[2]) < 0.99:
            u = np.cross(w, [0, 0, 1.0])
        else:
            u = np.cross(w, [1, 0, 0.0])
        u /= np.linalg.norm(u)
        v = np.cross(w, u)
        P = np.stack([centers @ u, centers @ v], axis=1)
        ov = disk_overlap_fraction(P, radii)
        allv.append(ov)
    vals["top_z"] = round(allv[0], 5)
    vals["side_x"] = round(allv[1], 5)
    vals["side_y"] = round(allv[2], 5)
    vals["random_mean"] = round(float(np.mean(allv[3:])), 5)
    vals["random_worst"] = round(float(np.max(allv[3:])), 5)
    return vals


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


HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>wikiuniverse layout preview __MODE__</title>
<style>body{margin:0;background:#000;overflow:hidden}
#info{position:absolute;top:8px;left:10px;color:#9aa;font:12px/1.5 monospace;white-space:pre}</style>
<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>
</head><body><div id="info">__INFO__</div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D = __DATA__;
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x000000, 0.00018);
const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 1, 200000);
camera.position.set(0, -3200, 1800);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
// galaxies: instanced spheres
const geo = new THREE.SphereGeometry(1, 10, 10);
const mat = new THREE.MeshBasicMaterial();
const mesh = new THREE.InstancedMesh(geo, mat, D.g.length);
const m4 = new THREE.Matrix4(); const col = new THREE.Color();
D.g.forEach((g, i) => {
  m4.makeScale(g[3], g[3], g[3]); m4.setPosition(g[0], g[1], g[2]);
  mesh.setMatrixAt(i, m4); mesh.setColorAt(i, col.set(g[4]));
});
scene.add(mesh);
// macro wireframe spheres
D.m.forEach(mk => {
  const g2 = new THREE.SphereGeometry(mk[3], 20, 12);
  const w = new THREE.LineSegments(new THREE.WireframeGeometry(g2),
        new THREE.LineBasicMaterial({color:0x444444, transparent:true, opacity:0.10}));
  w.position.set(mk[0], mk[1], mk[2]);
  scene.add(w);
});
addEventListener('resize', () => {
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
</script></body></html>
"""


def write_html_preview(path, g_centers, g_radius, gal_colors, dust_mask,
                       m_centers, R_m, eff_m, labels, mode_tag, info):
    """gal_colors: hex color per galaxy index (already curated)."""
    garr = []
    for i in np.flatnonzero(~dust_mask):
        garr.append([round(float(g_centers[i, 0]), 2), round(float(g_centers[i, 1]), 2),
                     round(float(g_centers[i, 2]), 2), round(float(max(g_radius[i], 1.0)), 2),
                     gal_colors[i]])
    dust_i = np.flatnonzero(dust_mask)
    if len(dust_i):
        # dust as tiny dim spheres (few thousand max)
        for i in dust_i:
            garr.append([round(float(g_centers[i, 0]), 2), round(float(g_centers[i, 1]), 2),
                         round(float(g_centers[i, 2]), 2), 1.5, "#333333"])
    marr = []
    for k, m in enumerate(eff_m):
        marr.append([round(float(m_centers[k, 0]), 2), round(float(m_centers[k, 1]), 2),
                     round(float(m_centers[k, 2]), 2), round(float(R_m[k]), 2),
                     labels.get(int(m), "")])
    data = json.dumps({"g": garr, "m": marr}, separators=(",", ":"))
    html = (HTML_TEMPLATE.replace("__DATA__", data).replace("__MODE__", mode_tag)
            .replace("__INFO__", info))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--dim", type=int, default=3, choices=[2, 3])
    ap.add_argument("--pack", type=float, default=0.6)
    ap.add_argument("--z-squash", type=float, default=0.35,
                    help="z compression of galaxy lens inside macro disks")
    ap.add_argument("--macro-dim", type=int, default=2, choices=[2, 3],
                    help="2 = 2.5D universe map (macros packed in plane), "
                         "3 = full 3D sphere packing")
    ap.add_argument("--macro-z-squash", type=float, default=1.0,
                    help="with --macro-dim 3: squash macro sphere z (hybrid ellipsoid "
                         "universe, e.g. 0.5)")
    ap.add_argument("--out-sub", default="",
                    help="output subdir under data/layout (e.g. 25d / 3d / hyb)")
    ap.add_argument("--views", type=int, default=24)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--pairs", default="catalog", choices=["catalog", "npz"],
                    help="galaxy pair source: catalog topK parquet or full pairs npz")
    ap.add_argument("--galaxy-tag", default="res1_sub")
    a = ap.parse_args()
    dirs = Dirs(a.base)
    final = os.path.join(dirs.base, "final")
    out_dir = os.path.join(dirs.base, "layout", a.out_sub) if a.out_sub \
        else os.path.join(dirs.base, "layout")
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
    m_coords = fr_layout(len(eff_m), m_edges, m_w, a.macro_dim, a.seed)
    if a.macro_dim == 3 and a.macro_z_squash != 1.0:
        m_coords[:, 2] *= a.macro_z_squash
    R_m = R_TOTAL * np.sqrt(n_art_m[eff_m] / max(1, n_art_m.sum()))
    m_centers = m_coords * (R_TOTAL * 1.6)
    native_overlap = (sphere_overlap_fraction if a.macro_dim == 3
                      else disk_overlap_fraction)
    m_centers = relax_disks(m_centers, R_m, iters=250)
    for _round in range(10):  # relax + expand until macro bodies barely overlap
        ov = native_overlap(m_centers, R_m)
        if ov <= 0.02:
            break
        m_centers = relax_disks(m_centers, R_m, iters=200)
        m_centers = m_centers * 1.06
    m_centers = relax_disks(m_centers, R_m, iters=300)
    macro_overlap = native_overlap(m_centers, R_m)
    if m_centers.shape[1] == 2:
        m_centers = np.concatenate([m_centers, np.zeros((len(m_centers), 1))], axis=1)
    proj = projection_overlap_stats(m_centers, R_m, n_views=a.views, seed=a.seed)
    print(f"[layout] macros: {len(eff_m)} placed (macro_dim={a.macro_dim}), "
          f"native overlap={macro_overlap:.4f}, proj top/side/mean="
          f"{proj['top_z']}/{proj['side_x']}/{proj['random_mean']} ({time.time()-t0:.1f}s)")

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
    g_centers = np.zeros((G, 3), np.float64)  # always 3D (z squashed lens)
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
            coords3 = np.zeros((1, 3))
        else:
            coords3 = fr_layout(len(members), e_local, w_local, 3, a.seed + int(m))
        xy = coords3[:, :2]
        zz = coords3[:, 2] * a.z_squash
        avail = max(Rm - g_radius[members].max(), Rm * 0.3)
        centers_xy = relax_disks(xy * avail, g_radius[members], iters=40)
        # radial clamp: keep every galaxy disk inside its macro disk (xy)
        lim = np.maximum(Rm - g_radius[members], 0.05 * Rm)
        d = np.linalg.norm(centers_xy, axis=1)
        sc = np.where(d > lim, lim / np.maximum(d, 1e-9), 1.0)
        centers_xy = centers_xy * sc[:, None]
        g_centers[members, 0] = m_centers[m_i, 0] + centers_xy[:, 0]
        g_centers[members, 1] = m_centers[m_i, 1] + centers_xy[:, 1]
        g_centers[members, 2] = m_centers[m_i, 2] + zz * avail
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

    # ---------- 4b. quality metrics ----------
    in_macro = np.flatnonzero(placed & ~dust_g)
    dist_m = np.linalg.norm(g_centers[in_macro, :2] - m_centers[
        [list(eff_m).index(int(macro_of[i])) for i in in_macro], :2], axis=1)
    spill = dist_m + g_radius[in_macro] > R_m[
        [list(eff_m).index(int(macro_of[i])) for i in in_macro]] * 1.02
    spill_frac = float(spill.mean()) if len(spill) else 0.0

    # ---------- 5. outputs ----------
    zcol = g_centers[:, 2]
    pq.write_table(pa.table({
        "galaxy_id": pa.array(np.arange(G, dtype=np.int32)),
        "macro_id": pa.array(macro_of.astype(np.int32)),
        "x": pa.array(g_centers[:, 0]), "y": pa.array(g_centers[:, 1]),
        "z": pa.array(zcol), "radius": pa.array(g_radius),
        "display_class": pa.array([str(c) for c in cls], type=pa.string()),
        "n_articles": pa.array(n_art_g.astype(np.int32)),
    }), os.path.join(out_dir, "galaxy_positions.parquet"), compression="zstd")

    mz = m_centers[:, 2]
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

    meta = {"generated_at": _now(), "dim": a.dim, "pack": a.pack,
            "z_squash": a.z_squash, "seed": a.seed,
            "pairs_source": a.pairs, "galaxy_tag": a.galaxy_tag,
            "n_galaxies": G, "n_macros_effective": int(len(eff_m)),
            "n_dust_shell": int(len(dust_idx)), "R_TOTAL": R_TOTAL,
            "mode": {"macro_dim": a.macro_dim, "macro_z_squash": a.macro_z_squash,
                     "z_squash": a.z_squash},
            "quality": {"macro_native_overlap_frac": round(macro_overlap, 5),
                        "macro_proj_overlap": proj,
                        "galaxy_spill_frac": round(spill_frac, 5),
                        "galaxy_spill_count": int(spill.sum())},
            "secs": round(time.time() - t0, 1)}
    print(f"[layout] quality: macro_overlap={macro_overlap:.4f} "
          f"galaxy_spill={spill_frac:.4f} ({int(spill.sum())})")
    write_json(os.path.join(out_dir, "layout_meta.json"), meta)

    # ---------- 6. preview: multi-view PNG + interactive HTML ----------
    mode_tag = (f"macro{a.macro_dim}d" +
                (f"_mz{a.macro_z_squash:g}" if a.macro_dim == 3 and a.macro_z_squash != 1.0 else "") +
                f"_z{a.z_squash:g}")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        cmap = plt.get_cmap("tab20")
        colors_hex = [matplotlib.colors.to_hex(cmap(int(m) % 20)) for m in range(Mac)]
        idx_in = np.flatnonzero((~dust_g) & placed)
        colors_all = ["#888888" if cls[i] == "medium" else colors_hex[int(macro_of[i])]
                      for i in range(G)]
        colors = [colors_all[i] for i in idx_in]
        dust_i = np.flatnonzero(dust_g | ~placed)
        fig, axes = plt.subplots(2, 2, figsize=(16, 16), facecolor="black")
        panels = [("xy (map view)", 0, 1, None), ("xz", 0, 2, None),
                  ("yz", 1, 2, None), ("xy colored by z", 0, 1, "z")]
        top_m = eff_m[np.argsort(-n_art_m[eff_m])[:8]]
        for ax, (name, i1_, i2_, depth) in zip(axes.flat, panels):
            ax.set_facecolor("black")
            if depth is None:
                ax.scatter(g_centers[idx_in, i1_], g_centers[idx_in, i2_],
                           s=np.clip(g_radius[idx_in] * 0.8, 0.5, 300),
                           c=colors, alpha=0.85, linewidths=0)
            else:
                ax.scatter(g_centers[idx_in, i1_], g_centers[idx_in, i2_],
                           s=np.clip(g_radius[idx_in] * 0.8, 0.5, 300),
                           c=g_centers[idx_in, 2], cmap="coolwarm", alpha=0.9,
                           linewidths=0)
            if len(dust_i):
                ax.scatter(g_centers[dust_i, i1_], g_centers[dust_i, i2_],
                           s=0.4, c="#333333", alpha=0.5, linewidths=0)
            for k, m in enumerate(eff_m):
                ax.add_patch(plt.Circle((m_centers[k, i1_], m_centers[k, i2_]), R_m[k],
                                        fill=False, edgecolor="#444444", lw=0.6))
            if name.startswith("xy ("):
                for m in top_m:
                    k = list(eff_m).index(int(m))
                    lbl = str(mac["rep_titles"][int(m)]).split(",")[0][:14]
                    ax.text(m_centers[k, i1_], m_centers[k, i2_] + R_m[k] * 1.03, lbl,
                            color="#999999", fontsize=7, ha="center")
            ax.set_aspect("equal")
            ax.axis("off")
            ax.set_title(f"{name}  [mode={mode_tag}]", color="#777777", fontsize=9)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "preview.png"), dpi=110, facecolor="black")
        plt.close(fig)
        labels_top = {int(m): str(mac["rep_titles"][int(m)]).split(",")[0][:16]
                      for m in eff_m}
        info = (f"mode={mode_tag} seed={a.seed} G={G} macros={len(eff_m)}\n"
                f"native_overlap={macro_overlap:.4f} spill={spill_frac:.4f}\n"
                f"proj top/side/mean={proj['top_z']}/{proj['side_x']}/{proj['random_mean']}\n"
                f"drag=orbit wheel=zoom")
        hp = write_html_preview(os.path.join(out_dir, f"preview_{mode_tag}.html"),
                                g_centers, g_radius, colors_all,
                                dust_g | ~placed, m_centers, R_m, eff_m,
                                labels_top, mode_tag, info)
        print(f"[layout] preview -> {os.path.join(out_dir, 'preview.png')} + {hp}")
    except Exception as e:  # previews optional
        print(f"[layout] preview skipped: {e}")

    print(f"[layout] done ({meta['secs']}s) -> {out_dir}")


if __name__ == "__main__":
    main()
