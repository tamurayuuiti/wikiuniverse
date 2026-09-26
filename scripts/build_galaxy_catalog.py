"""Build the galaxy catalog — the data contract between analysis, layout and the viewer.

Inputs (all produced by earlier stages; nothing heavy is recomputed):
  community/full/membership_<galaxy_tag>.npy     (e.g. res1_sub)
  community/full/membership_<macro_tag>.npy      (e.g. res1)
  community/full/per_community_<galaxy_tag>.parquet
  community/full/pairs_<galaxy_tag>.npz          (galaxy-galaxy aggregated weights)
  community/full/purity_<galaxy_tag>.parquet     (optional: category names)
  community/full/clusters_node_<galaxy_tag>.npy  (optional: L2 ids)
  community/full/per_community_<macro_tag>.parquet (optional: macro stats)

Outputs (data/final/):
  galaxies.parquet        galaxy_id, macro_id, cluster_l2, n_articles, e_in, e_out,
                          out_ratio, is_dust, name, name_share, rep_titles,
                          top_neighbors ("id:w,id:w,..."), n_neighbors
  macros.parquet          macro_id, n_articles, n_galaxies, e_in, e_out, out_ratio,
                          rep_titles, top_galaxies
  galaxy_pairs_topK.parquet  a, b, w (global top-K inter-galaxy edges)
  macro_pairs.parquet     macro_a, macro_b, w (aggregated; the "few lines" for far zoom)
  catalog_meta.json       counts, params, containment check

Usage:
  python scripts/build_galaxy_catalog.py --base data \
      --galaxy-tag res1_sub --macro-tag res1 [--top-neighbors 8] [--top-pairs 30000]
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.dumpio import write_json  # noqa: E402
from wu.paths import Dirs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--galaxy-tag", default="res1_sub")
    ap.add_argument("--macro-tag", default="res1")
    ap.add_argument("--top-neighbors", type=int, default=8)
    ap.add_argument("--top-pairs", type=int, default=30000)
    a = ap.parse_args()
    dirs = Dirs(a.base)
    full = os.path.join(dirs.community, "full")
    out_dir = os.path.join(dirs.base, "final")
    os.makedirs(out_dir, exist_ok=True)

    import pyarrow as pa
    import pyarrow.parquet as pq

    def p(*x):
        return os.path.join(full, *x)

    gal = np.load(p(f"membership_{a.galaxy_tag}.npy"))
    mac = np.load(p(f"membership_{a.macro_tag}.npy"))
    n = len(gal)
    G = int(gal.max()) + 1
    Mac = int(mac.max()) + 1
    per_g = pq.read_table(p(f"per_community_{a.galaxy_tag}.parquet")).to_pydict()
    pz = np.load(p(f"pairs_{a.galaxy_tag}.npz"))
    pa_, pb_, pw_ = pz["a"], pz["b"], pz["w"]

    purity_path = p(f"purity_{a.galaxy_tag}.parquet")
    purity = pq.read_table(purity_path).to_pydict() if os.path.exists(purity_path) else None
    cl_path = p(f"clusters_node_{a.galaxy_tag}.npy")
    cl_node = np.load(cl_path) if os.path.exists(cl_path) else None

    print(f"[catalog] n={n:,} galaxies={G:,} macros={Mac:,} pairs={len(pa_):,}")

    # ---- galaxy -> macro (any member; containment check)
    order = np.argsort(gal, kind="stable")
    gal_sorted = gal[order]
    first_pos = np.searchsorted(gal_sorted, np.arange(G), side="left")
    any_node = order[np.clip(first_pos, 0, n - 1)]
    macro_of = mac[any_node].astype(np.int32)
    # containment violations: galaxies whose members span >1 macro
    mac_sorted_by_gal = mac[order]
    changes = (gal_sorted[1:] == gal_sorted[:-1]) & (mac_sorted_by_gal[1:] != mac_sorted_by_gal[:-1])
    viol_gal = np.unique(gal_sorted[1:][changes])
    containment_violations = int(len(viol_gal))
    if containment_violations:
        macro_of[viol_gal] = -1  # ambiguous -> flagged
        print(f"[catalog][warn] {containment_violations} galaxies span multiple macros (flagged -1)")

    n_c = np.asarray(per_g["N_c"], np.int64)
    is_dust = n_c <= 1

    # ---- neighbors per galaxy (top M by aggregated weight)
    src = np.concatenate([pa_, pb_]).astype(np.int64)
    dst = np.concatenate([pb_, pa_]).astype(np.int64)
    w = np.concatenate([pw_, pw_]).astype(np.float64)
    n_neighbors = np.bincount(src, minlength=G).astype(np.int32)
    srt = np.lexsort((-w, src))
    src_s, dst_s, w_s = src[srt], dst[srt], w[srt]
    starts = np.searchsorted(src_s, np.arange(G), side="left")
    M = a.top_neighbors
    top_neighbors = []
    for g in range(G):
        s = starts[g]
        e = min(s + M, len(src_s))
        if g >= len(starts) or (s < len(src_s) and src_s[s] != g):
            top_neighbors.append("")
            continue
        top_neighbors.append(",".join(f"{int(dst_s[i])}:{int(w_s[i])}" for i in range(s, e)))

    # ---- L2 cluster per galaxy (majority not needed: clusters_node is node-level;
    #      take the cluster of the same any_node member)
    cluster_of = (np.asarray(cl_node)[any_node].astype(np.int32)
                  if cl_node is not None else np.full(G, -1, np.int32))

    names = (purity["name"] if purity else [""] * G)
    name_shares = (purity["top1_share_filt"] if purity else [None] * G)

    galaxies = pa.table({
        "galaxy_id": pa.array(np.arange(G, dtype=np.int32)),
        "macro_id": pa.array(macro_of),
        "cluster_l2": pa.array(cluster_of),
        "n_articles": pa.array(n_c.astype(np.int32)),
        "e_in": pa.array(np.asarray(per_g["E_in_c"], np.int64)),
        "e_out": pa.array(np.asarray(per_g["E_out_c"], np.int64)),
        "out_ratio": pa.array(np.asarray(per_g["out_ratio_c"], np.float64), type=pa.float64()),
        "is_dust": pa.array(is_dust),
        "name": pa.array([str(x) if x else "" for x in names], type=pa.string()),
        "name_share": pa.array([float(x) if x is not None else None for x in name_shares],
                               type=pa.float64()),
        "rep_titles": pa.array(per_g["rep_titles"], type=pa.string()),
        "top_neighbors": pa.array(top_neighbors, type=pa.string()),
        "n_neighbors": pa.array(n_neighbors),
    })
    pq.write_table(galaxies, os.path.join(out_dir, "galaxies.parquet"), compression="zstd")

    # ---- macros table
    n_per_macro = np.bincount(macro_of[~is_dust & (macro_of >= 0)], minlength=Mac).astype(np.int64)
    gal_per_macro = np.bincount(np.where(macro_of >= 0, macro_of, 0), minlength=Mac).astype(np.int64)
    nodes_per_macro = np.bincount(mac, minlength=Mac).astype(np.int64)
    macro_stats = {}
    mp_path = p(f"per_community_{a.macro_tag}.parquet")
    if os.path.exists(mp_path):
        pm = pq.read_table(mp_path).to_pydict()
        macro_stats = {"e_in": pm["E_in_c"], "e_out": pm["E_out_c"],
                       "out_ratio": pm["out_ratio_c"], "rep_titles": pm["rep_titles"]}
    # top galaxies per macro (by size)
    top_gal_str = []
    gorder = np.argsort(-n_c)
    seen = {m: [] for m in range(Mac)}
    for g in gorder:
        m = int(macro_of[g])
        if m >= 0 and len(seen[m]) < 5 and not is_dust[g]:
            seen[m].append(int(g))
    for m in range(Mac):
        top_gal_str.append(",".join(str(x) for x in seen[m]))
    macros = pa.table({
        "macro_id": pa.array(np.arange(Mac, dtype=np.int32)),
        "n_articles": pa.array(nodes_per_macro.astype(np.int32)),
        "n_galaxies": pa.array(gal_per_macro.astype(np.int32)),
        "e_in": pa.array(np.asarray(macro_stats.get("e_in", np.zeros(Mac, np.int64)), np.int64)),
        "e_out": pa.array(np.asarray(macro_stats.get("e_out", np.zeros(Mac, np.int64)), np.int64)),
        "out_ratio": pa.array(np.asarray(macro_stats.get("out_ratio", np.full(Mac, np.nan)), np.float64),
                              type=pa.float64()),
        "rep_titles": pa.array([str(x) for x in macro_stats.get("rep_titles", [""] * Mac)],
                               type=pa.string()),
        "top_galaxies": pa.array(top_gal_str, type=pa.string()),
    })
    pq.write_table(macros, os.path.join(out_dir, "macros.parquet"), compression="zstd")

    # ---- galaxy pairs top-K (global)
    k = min(a.top_pairs, len(pw_))
    topk = np.argsort(-pw_)[:k]
    gp = pa.table({
        "a": pa.array(pa_[topk].astype(np.int32)),
        "b": pa.array(pb_[topk].astype(np.int32)),
        "w": pa.array(pw_[topk].astype(np.int64)),
    })
    pq.write_table(gp, os.path.join(out_dir, "galaxy_pairs_topK.parquet"), compression="zstd")

    # ---- macro pairs (aggregate galaxy pairs; skip intra-macro and flagged)
    ma, mb = macro_of[pa_], macro_of[pb_]
    okm = (ma >= 0) & (mb >= 0) & (ma != mb)
    lo = np.minimum(ma[okm], mb[okm]).astype(np.int64)
    hi = np.maximum(ma[okm], mb[okm]).astype(np.int64)
    key = lo * Mac + hi
    uk, inv = np.unique(key, return_inverse=True)
    kw = np.bincount(inv, weights=pw_[okm].astype(np.float64))
    mpr = np.argsort(-kw)
    mp = pa.table({
        "macro_a": pa.array((uk[mpr] // Mac).astype(np.int32)),
        "macro_b": pa.array((uk[mpr] % Mac).astype(np.int32)),
        "w": pa.array(kw[mpr]),
    })
    pq.write_table(mp, os.path.join(out_dir, "macro_pairs.parquet"), compression="zstd")

    meta = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "galaxy_tag": a.galaxy_tag, "macro_tag": a.macro_tag,
        "n_articles": n, "n_galaxies": G, "n_galaxies_nontrivial": int((~is_dust).sum()),
        "n_macros": Mac, "n_dust": int(is_dust.sum()),
        "containment_violations": containment_violations,
        "n_galaxy_pairs_total": int(len(pw_)),
        "n_galaxy_pairs_topK": int(k),
        "n_macro_pairs": int(len(kw)),
        "macro_pairs_weight_sum": float(kw.sum()),
        "named_galaxies": int(sum(1 for x in names if x)) if purity else 0,
        "params": {"top_neighbors": M, "top_pairs": a.top_pairs},
    }
    write_json(os.path.join(out_dir, "catalog_meta.json"), meta)
    print(f"[catalog] galaxies={G:,} (dust={meta['n_dust']}, named={meta['named_galaxies']}) "
          f"macros={Mac:,} pairs_topK={k:,} macro_pairs={len(kw):,} "
          f"violations={containment_violations}")
    print(f"[catalog] wrote -> {out_dir}")


if __name__ == "__main__":
    main()
