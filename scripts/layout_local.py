"""Local (intra-galaxy) article layout — the independent-batch prototype (§12).

Each galaxy is a fully independent job:
  internal edges  -> igraph FR (3D) shape, scaled into the galaxy ball
  anchor vectors  -> articles with cross-galaxy links are pulled toward the
                     ball boundary facing their linked neighbour galaxies
                     (accumulated direction vectors, no per-pair storage)
  elastic prior   -> keeps the FR internal structure while anchors bend it

Inputs:
  community/full/membership_<galaxy-tag>.npy
  graph/edges_undirected_unique.bin
  layout/<run>/galaxy_positions.parquet   (canonical centers + radii)

Outputs (layout/<run>/):
  article_positions.parquet   page_id, idx, galaxy_id, x, y, z (float32)
  layout_local_meta.json      timings (per-galaxy p50/p95 -> batch extrapolation)
  layout_local_checkpoint.json  done-galaxy list (resume / batch boundary)

Batch usage (§12 Colab-jobs prototype):
  python scripts/layout_local.py --base data --run 20260926_baseline --galaxies 0-99
  python scripts/layout_local.py --base data --run 20260926_baseline --galaxies 100-199
  python scripts/layout_local.py --base data --run 20260926_baseline --galaxies all
(positions arrays are merged at the end from the checkpointed per-galaxy files
when --galaxies all completes; partial jobs write per-galaxy .npy shards)
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
from wu.paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402
from wu.stats import load_edges_mmap  # noqa: E402


def _fr_unit(n: int, edges_local: np.ndarray, dim: int, seed: int) -> np.ndarray:
    """FR layout normalized to unit ball (deterministic via random.seed)."""
    import igraph as ig
    random.seed(seed)
    g = ig.Graph(n=n)
    if len(edges_local):
        g.add_edges([(int(u), int(v)) for u, v in edges_local])
        lay = g.layout_fruchterman_reingold(dim=dim)
    else:
        lay = g.layout_fruchterman_reingold(dim=dim)
    c = np.asarray(lay.coords, np.float64)
    r = np.linalg.norm(c, axis=1)
    m = r.max()
    if m > 1e-9:
        c = c / m
    else:
        rng = np.random.default_rng(seed)
        c = rng.normal(size=(n, dim))
        c /= max(np.linalg.norm(c, axis=1).max(), 1e-9)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--galaxy-tag", default="res1_sub")
    ap.add_argument("--run", default=ACTIVE_LAYOUT_RUN,
                    help="layout run name under data/layout "
                         "(default: wu.paths.ACTIVE_LAYOUT_RUN)")
    ap.add_argument("--galaxies", default="all",
                    help="'all' or 'A-B' inclusive id range (one batch job)")
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
    shard_dir = os.path.join(lay_dir, "article_shards")
    os.makedirs(shard_dir, exist_ok=True)
    t0 = time.time()

    import pyarrow as pa
    import pyarrow.parquet as pq

    memb = np.load(dirs.community_full / f"membership_{a.galaxy_tag}.npy")
    n = len(memb)
    G = int(memb.max()) + 1
    gpos = pq.read_table(os.path.join(lay_dir, "galaxy_positions.parquet")).to_pydict()
    centers = np.stack([gpos["x"], gpos["y"], gpos["z"]], axis=1).astype(np.float64)
    radii = np.asarray(gpos["radius"], np.float64)
    E = load_edges_mmap(os.path.join(dirs.graph, "edges_undirected_unique.bin"))

    # ---- galaxy range for this job
    if a.galaxies == "all":
        glo, ghi = 0, G - 1
    else:
        lo, hi = a.galaxies.split("-")
        glo, ghi = max(0, int(lo)), min(G - 1, int(hi))

    ck_path = os.path.join(lay_dir, "layout_local_checkpoint.json")
    ck = read_json(ck_path, {}) or {"done": {}}
    done = set(int(k) for k in ck["done"])

    # ---- pass 1: per-galaxy internal edge counts + per-article anchor vectors
    print(f"[local] pass1: anchor vectors + bucket counts over {len(E):,} edges")
    cnt = np.zeros(G, np.int64)
    anchor = np.zeros((n, 3), np.float64)
    ext_deg = np.zeros(n, np.int64)
    CH = 4_000_000
    for i in range(0, len(E), CH):
        blk = np.asarray(E[i : i + CH]).astype(np.int64)
        u, v = blk[:, 0], blk[:, 1]
        gu, gv = memb[u], memb[v]
        same = gu == gv
        if same.any():
            cnt += np.bincount(gu[same], minlength=G)
        cr = np.flatnonzero(~same)
        if len(cr):
            uu, vv = u[cr], v[cr]
            guu, gvv = gu[cr], gv[cr]
            d = centers[gvv] - centers[guu]
            dn = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)
            np.add.at(anchor, uu, dn)
            np.add.at(anchor, vv, -dn)
            np.add.at(ext_deg, uu, 1)
            np.add.at(ext_deg, vv, 1)
        if (i // CH) % 5 == 0:
            print(f"  pass1 {i + len(blk):,}/{len(E):,} ({time.time()-t0:.0f}s)", flush=True)

    # ---- pass 2: bucket internal edges
    offs = np.zeros(G + 1, np.int64)
    offs[1:] = np.cumsum(cnt)
    buf = np.empty((int(cnt.sum()), 2), np.int32)
    fill = offs[:-1].copy()
    for i in range(0, len(E), CH):
        blk = np.asarray(E[i : i + CH])
        u, v = blk[:, 0].astype(np.int64), blk[:, 1].astype(np.int64)
        gu, gv = memb[u], memb[v]
        same = gu == gv
        if not same.any():
            continue
        c = gu[same]
        us, vs = u[same].astype(np.int32), v[same].astype(np.int32)
        oc = np.argsort(c, kind="stable")
        cs = c[oc]
        uniq, first = np.unique(cs, return_index=True)
        uidx = np.searchsorted(uniq, cs)
        rank = np.arange(len(cs)) - first[uidx]
        pos = fill[uniq][uidx] + rank
        buf[pos, 0] = us[oc]
        buf[pos, 1] = vs[oc]
        fill[uniq] += np.diff(np.append(first, len(cs)))
        if (i // CH) % 5 == 0:
            print(f"  pass2 {i + len(blk):,}/{len(E):,} ({time.time()-t0:.0f}s)", flush=True)
    assert np.array_equal(fill, offs[1:])
    print(f"[local] pass2 done: {len(buf):,} internal edges ({time.time()-t0:.0f}s)")

    # ---- per-galaxy jobs
    order = np.argsort(memb, kind="stable")
    starts = np.searchsorted(memb[order], np.arange(G), side="left")
    ends = np.searchsorted(memb[order], np.arange(G), side="right")
    times = []
    n_done = 0
    skipped = sum(1 for x in range(glo, ghi + 1) if x in done)
    print(f"[local] range {glo}-{ghi}: {skipped} already done (resume), "
          f"{ghi - glo + 1 - skipped} to process", flush=True)
    for g in range(glo, ghi + 1):
        if g in done:
            continue
        members = order[starts[g] : ends[g]]
        ng = len(members)
        if ng == 0:
            done.add(g)
            continue
        tg = time.time()
        R = float(radii[g])
        if ng == 1:
            pos_g = np.zeros((1, 3))
        else:
            le = buf[offs[g] : offs[g + 1]]
            loc = np.stack([np.searchsorted(members, le[:, 0].astype(np.int64)),
                            np.searchsorted(members, le[:, 1].astype(np.int64))],
                           axis=1).astype(np.int32)
            fr = _fr_unit(ng, loc, 3, a.seed + g)
            pos_g = fr * (R * 0.92)
            av = anchor[members]
            avn = np.linalg.norm(av, axis=1)
            has = avn > 1e-9
            av_unit = np.zeros_like(av)
            av_unit[has] = av[has] / avn[has][:, None]
            # boundary radius per article: more external links -> closer to surface
            tgt_r = R * (0.55 + 0.45 * np.clip(ext_deg[members] / 20.0, 0, 1))
            target = av_unit * tgt_r[:, None]
            for _ in range(a.iters):
                pull = np.zeros_like(pos_g)
                pull[has] = a.kappa * (target[has] - pos_g[has])
                prior = a.lam * (fr * (R * 0.92) - pos_g)
                pos_g = pos_g + pull + prior
                rg = np.linalg.norm(pos_g, axis=1)
                over = rg > R
                if over.any():
                    pos_g[over] *= (R / rg[over])[:, None]
        np.save(os.path.join(shard_dir, f"gal_{g:06d}.npy"),
                (centers[g] + pos_g).astype(np.float32))
        done.add(g)
        n_done += 1
        dt = time.time() - tg
        times.append(dt)
        print(f"  g={g} n={ng:,} e={int(offs[g + 1] - offs[g]):,} {dt:.2f}s", flush=True)
        if n_done % 50 == 0:
            write_json(ck_path, {"done": sorted(done)})
            el = time.time() - t0
            mean = float(np.mean(times))
            rem = (G - len(done)) if a.galaxies == "all" else \
                sum(1 for x in range(g + 1, ghi + 1) if x not in done)
            print(f"[local] {len(done):,}/{G:,} done ({n_done} this run), "
                  f"elapsed {el/60:.1f}m, eta {mean * rem / 60:.1f}m "
                  f"(mean {mean:.2f}s/galaxy)", flush=True)
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
                  f"r/R p50={np.percentile(r / max(R,1e-9), 50):.2f} "
                  f"p95={np.percentile(r / max(R,1e-9), 95):.2f} "
                  f"max={r.max() / max(R,1e-9):.2f}")
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                fig, axes = plt.subplots(1, 3, figsize=(15, 5.2), facecolor="black")
                for ax, (i1, i2, nm) in zip(axes, [(0, 1, "xy"), (0, 2, "xz"), (1, 2, "yz")]):
                    ax.set_facecolor("black")
                    ax.scatter(rel[:, i1], rel[:, i2], s=1.2, c="#9ecbff",
                               alpha=0.8, linewidths=0)
                    ax.add_patch(plt.Circle((0, 0), R, fill=False, ec="#555555", lw=0.8))
                    ax.set_aspect("equal")
                    ax.axis("off")
                    ax.set_title(f"{nm}  (ball R={R:.0f})", color="#888888", fontsize=9)
                fig.suptitle(f"galaxy {g}: article layout (n={len(P)})",
                             color="white", fontsize=10)
                fig.tight_layout()
                fp = os.path.join(lay_dir, f"preview_galaxy_{g}.png")
                fig.savefig(fp, dpi=120, facecolor="black")
                plt.close(fig)
                print(f"[local] preview -> {fp}")
            except Exception as e:
                print(f"[local] preview skipped: {e}")

    # ---- merge when complete
    meta = {"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "galaxy_tag": a.galaxy_tag, "run": a.run,
            "galaxies_range": [glo, ghi], "n_done_this_run": n_done,
            "n_done_total": len(done), "n_galaxies": G,
            "params": {"iters": a.iters, "kappa": a.kappa, "lam": a.lam, "seed": a.seed},
            "secs": round(time.time() - t0, 1)}
    if times:
        tt = np.array(times)
        meta["per_galaxy_secs"] = {"p50": round(float(np.percentile(tt, 50)), 4),
                                   "p95": round(float(np.percentile(tt, 95)), 4),
                                   "max": round(float(tt.max()), 4)}
        meta["extrapolation_all_galaxies_secs"] = round(float(tt.sum() / n_done * max(1, G - len(done) + n_done)), 1) if n_done else None
    if len(done) >= G:
        article_ids = np.load(dirs.article_ids)
        P = np.empty((n, 3), np.float32)
        for g in range(G):
            members = order[starts[g] : ends[g]]
            sh = np.load(os.path.join(shard_dir, f"gal_{g:06d}.npy"))
            P[members] = sh
        pq.write_table(pa.table({
            "idx": pa.array(np.arange(n, dtype=np.int32)),
            "page_id": pa.array(article_ids),
            "galaxy_id": pa.array(memb),
            "x": pa.array(P[:, 0]), "y": pa.array(P[:, 1]), "z": pa.array(P[:, 2]),
        }), os.path.join(lay_dir, "article_positions.parquet"), compression="zstd")
        meta["merged"] = "article_positions.parquet"
        print(f"[local] merged {n:,} article positions -> "
              f"{os.path.join(lay_dir, 'article_positions.parquet')}")
    write_json(os.path.join(lay_dir, "layout_local_meta.json"), meta)
    el = time.time() - t0
    rem_all = G - len(done)
    eta = (float(np.mean(times)) * rem_all / 60) if times and rem_all else 0.0
    print(f"[local] done: {n_done} galaxies this run, {len(done):,}/{G:,} total "
          f"({meta['secs']}s" + (f", per-galaxy p50={meta['per_galaxy_secs']['p50']}s" if times else "")
          + (f", remaining {rem_all:,} galaxies ~{eta:.0f}m at this pace" if rem_all else ", ALL COMPLETE") + ")")


if __name__ == "__main__":
    main()
