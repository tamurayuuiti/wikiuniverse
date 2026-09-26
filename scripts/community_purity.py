"""E4: Category purity of communities — do link-based galaxies align with topics?

For a membership file (any tag), computes per community:
  coverage      share of articles having >=1 category
  n_cats        distinct categories in the community
  top1_share    share of categorized articles carrying the dominant category (purity)
  entropy_norm  normalized Shannon entropy over category distribution (0=pure, 1=mixed)
  top3          dominant category titles with shares

Outputs: community/full/purity_<tag>.parquet + purity_<tag>.json + console table.

Usage:
  python scripts/community_purity.py --base data --tag res1_sub [--top 15]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.dumpio import write_json  # noqa: E402
from wu.paths import Dirs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--tag", required=True, help="membership tag, e.g. res1_sub / res1_body_sub")
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args()
    dirs = Dirs(a.base)

    import pyarrow.parquet as pq
    memb_path = os.path.join(dirs.community, "full", f"membership_{a.tag}.npy")
    if not os.path.exists(memb_path):
        sys.exit(f"{memb_path} not found")
    memb = np.load(memb_path)
    n = len(memb)
    C = int(memb.max()) + 1

    ac_path = os.path.join(dirs.graph, "article_categories.bin")
    M = os.path.getsize(ac_path) // 8
    AC = np.memmap(ac_path, dtype=np.int32, mode="r", shape=(M, 2))
    cats = pq.read_table(os.path.join(dirs.graph, "categories.parquet")).to_pydict()
    cat_titles = cats["title"]
    K = len(cat_titles)

    comm = memb[AC[:, 0].astype(np.int64)].astype(np.int64)
    cat = AC[:, 1].astype(np.int64)
    key = comm * K + cat
    uk, cnt = np.unique(key, return_counts=True)
    cu, ku = uk // K, uk % K
    print(f"[purity] tag={a.tag}: C={C:,} cat_pairs={M:,} (comm,cat) uniq={len(uk):,}")

    n_c = np.bincount(memb, minlength=C)
    uniq_art = np.unique(np.asarray(AC[:, 0]))
    awc = np.bincount(memb[uniq_art.astype(np.int64)], minlength=C)  # articles with >=1 cat

    # per-community segment stats (cu is sorted by comm since key sorted)
    seg_start = np.searchsorted(cu, np.arange(C), side="left")
    seg_end = np.searchsorted(cu, np.arange(C), side="right")
    top1_cnt = np.zeros(C, np.int64)
    top1_cat = np.zeros(C, np.int64)
    n_cats = (seg_end - seg_start).astype(np.int64)
    ent = np.zeros(C, np.float64)
    for c in range(C):
        s, e = seg_start[c], seg_end[c]
        if e <= s:
            continue
        seg = cnt[s:e]
        j = int(np.argmax(seg))
        top1_cnt[c] = seg[j]
        top1_cat[c] = ku[s + j]
        tot = seg.sum()
        p = seg / tot
        h = float(-(p * np.log(p)).sum())
        ent[c] = h / np.log(e - s) if e - s > 1 else 0.0

    with np.errstate(divide="ignore", invalid="ignore"):
        top1_share = np.where(awc > 0, top1_cnt / np.maximum(1, awc), np.nan)
        coverage = awc / np.maximum(1, n_c)

    titles_col, share_col, ent_col = [], [], []
    for c in range(C):
        s, e = seg_start[c], seg_end[c]
        if e <= s:
            titles_col.append("")
        else:
            order = np.argsort(cnt[s:e])[::-1][:3]
            titles_col.append(" / ".join(
                f"{cat_titles[ku[s + j]]}({cnt[s + j] / max(1, awc[c]):.2f})" for j in order))
        share_col.append(float(top1_share[c]) if not np.isnan(top1_share[c]) else None)
        ent_col.append(float(ent[c]))

    import pyarrow as pa
    out_pq = os.path.join(dirs.community, "full", f"purity_{a.tag}.parquet")
    pa_tbl = pa.table({
        "comm": pa.array(np.arange(C, dtype=np.int32)),
        "N_c": pa.array(n_c),
        "n_with_cats": pa.array(awc),
        "coverage": pa.array(coverage, type=pa.float64()),
        "n_cats": pa.array(n_cats),
        "top1_share": pa.array(top1_share, type=pa.float64()),
        "top1_title": pa.array([cat_titles[top1_cat[c]] if n_cats[c] else "" for c in range(C)],
                               type=pa.string()),
        "entropy_norm": pa.array(ent_col, type=pa.float64()),
        "top3": pa.array(titles_col, type=pa.string()),
    })
    pq.write_table(pa_tbl, out_pq, compression="zstd")

    eff = n_c >= 2
    w = n_c[eff].astype(float)
    ts = top1_share[eff]
    ok = ~np.isnan(ts)

    def wpct(x, ps=(10, 25, 50, 75, 90)):
        v, ww = x[ok], w[ok]
        o = np.argsort(v)
        cw = np.cumsum(ww[o]) / ww.sum()
        return {f"p{p}": round(float(v[o][np.searchsorted(cw, p / 100)]), 4) for p in ps}

    agg = {
        "tag": a.tag, "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "n_communities": C, "n_effective": int(eff.sum()),
        "n_categories_total": K,
        "coverage_node_weighted": round(float(awc.sum() / max(1, (n_c[eff]).sum())), 4),
        "top1_share_node_weighted_mean": round(float((ts[ok] * w[ok]).sum() / w[ok].sum()), 4),
        "top1_share_percentiles_node_weighted": wpct(ts),
        "entropy_norm_node_weighted_mean": round(float((ent[eff][ok] * w[ok]).sum() / w[ok].sum()), 4),
    }
    write_json(os.path.join(dirs.community, "full", f"purity_{a.tag}.json"), agg)

    print(json.dumps(agg, ensure_ascii=False, indent=1))
    order = np.argsort(-n_c)
    print(f"\nTop {a.top} communities by size:")
    for c in order[: a.top]:
        if n_c[c] < 2:
            continue
        print(f"  c{int(c):5d} N={n_c[c]:7,d} cov={coverage[c]:.2f} purity={top1_share[c]:.3f} "
              f"ent={ent[c]:.3f} | {titles_col[c][:80]}")
    print(f"\nwrote {out_pq}")


if __name__ == "__main__":
    main()
