# wu/purity.py — カテゴリ純度の計算(purity v2: tf-idf 命名・メタカテゴリ除外)
#
# 責務:
# - リンク由来コミュニティ(銀河)がカテゴリ=主題とどれだけ一致するかを定量し、
#   銀河名の材料(name 列)を生成する。出力は community/full/purity_<tag>.{parquet,json}。
#
# 注意:
# - 旧 scripts/community_purity.py から移設(2026-10-02)。main(a=None) は
#   Namespace 注入でステージからも呼べる(挙動は CLI と同一、test_catalog が担保)。
# - 正準実行は python -m wu run purity(入力は membership + categories 系成果物)。

"""E4 v2: Category purity of communities with meta-category filtering.

Problem with v1: ubiquitous maintenance categories (すべてのスタブ記事, 存命人物,
ウィキデータにある座標, ISBNマジックリンク...) dominate top1_share — they are
the category-version of template hubs, not topics.

v2 additions:
  --max-cat-freq N : ignore categories attached to more than N articles
                     (default 20000 ≈ 1.3% of jawiki articles) when NAMING.
  name / name_score: best category by tf-idf score
                     score = P(cat|comm) * ln(N_articles / cat_freq)
  top1_share_filt  : purity computed over filtered categories only
  nameable_share   : node share of communities with top1_share_filt >= --name-th

Raw (unfiltered) stats are kept for comparability.

Usage:
  python scripts/community_purity.py --base data --tag res1_sub [--max-cat-freq 20000]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import numpy as np


from .dumpio import write_json  # noqa: E402
from .paths import Dirs  # noqa: E402


def main(a=None):
    # a=None のときだけ CLI 引数を解析する(ステージからは Namespace を注入
    # して呼ぶ = 引数解析と処理本体の分離。既存の CLI 挙動は不変)。
    if a is None:
        ap = argparse.ArgumentParser()
        ap.add_argument("--base", default="data")
        ap.add_argument("--tag", required=True)
        ap.add_argument("--top", type=int, default=15)
        ap.add_argument("--max-cat-freq", type=int, default=20000,
                        help="categories with more member articles are treated as meta "
                             "and excluded from naming/filtered purity")
        ap.add_argument("--name-th", type=float, default=0.30,
                        help="top1_share_filt threshold for 'nameable' communities")
        a = ap.parse_args()
    dirs = Dirs(a.base)

    import pyarrow as pa
    import pyarrow.parquet as pq

    memb = np.load(str(dirs.community_full / f"membership_{a.tag}.npy"))
    n = len(memb)
    C = int(memb.max()) + 1
    ac_path = os.path.join(dirs.graph, "article_categories.bin")
    M = os.path.getsize(ac_path) // 8
    AC = np.memmap(ac_path, dtype=np.int32, mode="r", shape=(M, 2))
    cats = pq.read_table(os.path.join(dirs.graph, "categories.parquet")).to_pydict()
    cat_titles = cats["title"]
    K = len(cat_titles)

    art = np.asarray(AC[:, 0]).astype(np.int64)
    cat = np.asarray(AC[:, 1]).astype(np.int64)
    cat_freq = np.bincount(cat, minlength=K)
    comm = memb[art]

    # global idf for naming
    idf = np.log(max(1, n) / np.maximum(1, cat_freq))

    def seg_stats(mask_pairs: np.ndarray, use_idf: bool):
        """Return per-community segment arrays over filtered pairs."""
        cm, ct = comm[mask_pairs], cat[mask_pairs]
        key = cm * K + ct
        uk, cnt = np.unique(key, return_counts=True)
        cu = uk // K
        ku = uk % K
        seg_start = np.searchsorted(cu, np.arange(C), side="left")
        seg_end = np.searchsorted(cu, np.arange(C), side="right")
        top1_cnt = np.zeros(C, np.int64)
        top1_cat = np.full(C, -1, np.int64)
        n_cats = (seg_end - seg_start).astype(np.int64)
        ent = np.zeros(C, np.float64)
        for c in range(C):
            s, e = seg_start[c], seg_end[c]
            if e <= s:
                continue
            seg = cnt[s:e].astype(np.float64)
            if use_idf:
                score = seg * idf[ku[s:e]]
                j = int(np.argmax(score))
            else:
                j = int(np.argmax(seg))
            top1_cnt[c] = int(seg[j])
            top1_cat[c] = ku[s + j]
            tot = seg.sum()
            p = seg / tot
            h = float(-(p * np.log(p)).sum())
            ent[c] = h / np.log(e - s) if e - s > 1 else 0.0
        return top1_cnt, top1_cat, n_cats, ent, (cu, cnt, seg_start, seg_end)

    n_c = np.bincount(memb, minlength=C)
    uniq_art = np.unique(art)
    awc = np.bincount(memb[uniq_art], minlength=C)

    # raw (all categories)
    all_mask = np.ones(M, bool)
    r_cnt, r_cat, r_ncats, r_ent, _ = seg_stats(all_mask, use_idf=False)
    # filtered (non-meta categories), named by tf-idf
    keep_cat = cat_freq <= a.max_cat_freq
    f_mask = keep_cat[cat]
    f_cnt, f_cat, f_ncats, f_ent, _ = seg_stats(f_mask, use_idf=True)
    # filtered coverage: articles having >=1 non-meta category
    f_art = np.unique(art[f_mask]) if f_mask.any() else np.zeros(0, np.int64)
    f_awc = np.bincount(memb[f_art], minlength=C) if len(f_art) else np.zeros(C, np.int64)

    with np.errstate(divide="ignore", invalid="ignore"):
        top1_share = np.where(awc > 0, r_cnt / np.maximum(1, awc), np.nan)
        top1_share_filt = np.where(f_awc > 0, f_cnt / np.maximum(1, f_awc), np.nan)

    # filtered segments for top3 strings
    cm_f, ct_f = comm[f_mask], cat[f_mask]
    key_f = cm_f * K + ct_f
    uk_f, cnt_f = np.unique(key_f, return_counts=True)
    cu_f = uk_f // K
    ku_f = uk_f % K
    ss_f = np.searchsorted(cu_f, np.arange(C), side="left")
    se_f = np.searchsorted(cu_f, np.arange(C), side="right")

    names, shares_f, ents_f, top3s = [], [], [], []
    for c in range(C):
        names.append(cat_titles[f_cat[c]] if f_cat[c] >= 0 else "")
        shares_f.append(float(top1_share_filt[c]) if not np.isnan(top1_share_filt[c]) else None)
        ents_f.append(float(f_ent[c]))
        s, e = ss_f[c], se_f[c]
        if e <= s:
            top3s.append("")
        else:
            order = np.argsort(cnt_f[s:e])[::-1][:3]
            top3s.append(" / ".join(f"{cat_titles[ku_f[s + j]]}"
                                    f"({cnt_f[s + j] / max(1, f_awc[c]):.2f})" for j in order))

    out_pq = str(dirs.community_full / f"purity_{a.tag}.parquet")
    pa_tbl = pa.table({
        "comm": pa.array(np.arange(C, dtype=np.int32)),
        "N_c": pa.array(n_c),
        "n_with_cats": pa.array(awc),
        "n_with_filtered_cats": pa.array(f_awc),
        "n_cats": pa.array(r_ncats),
        "n_cats_filt": pa.array(f_ncats),
        "top1_share": pa.array(top1_share, type=pa.float64()),
        "top1_share_filt": pa.array(top1_share_filt, type=pa.float64()),
        "top1_title": pa.array([cat_titles[r_cat[c]] if r_ncats[c] else "" for c in range(C)],
                               type=pa.string()),
        "name": pa.array(names, type=pa.string()),          # tf-idf best non-meta category
        "entropy_norm": pa.array(r_ent, type=pa.float64()),
        "entropy_norm_filt": pa.array(ents_f, type=pa.float64()),
        "top3_filt": pa.array(top3s, type=pa.string()),
    })
    pq.write_table(pa_tbl, out_pq, compression="zstd")

    eff = n_c >= 2
    w = n_c[eff].astype(float)

    def stats_of(x):
        xv = x[eff]
        ok = ~np.isnan(xv)
        if not ok.any():
            return {}
        v, ww = xv[ok], w[ok]
        o = np.argsort(v)
        cw = np.cumsum(ww[o]) / ww.sum()
        return {
            "node_weighted_mean": round(float((v * ww).sum() / ww.sum()), 4),
            "percentiles": {f"p{p}": round(float(v[o][min(len(v) - 1, np.searchsorted(cw, p / 100))]), 4)
                            for p in (10, 25, 50, 75, 90)},
        }

    nameable = eff & ~np.isnan(top1_share_filt) & (top1_share_filt >= a.name_th)
    agg = {
        "tag": a.tag, "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "n_communities": C, "n_effective": int(eff.sum()),
        "n_categories_total": K,
        "max_cat_freq": a.max_cat_freq,
        "n_meta_categories_excluded": int((cat_freq > a.max_cat_freq).sum()),
        "top1_share_raw": stats_of(top1_share),
        "top1_share_filt": stats_of(top1_share_filt),
        "entropy_norm_filt_mean": round(float(np.nanmean(f_ent[eff])), 4) if eff.any() else None,
        "nameable_share_nodes": round(float(n_c[nameable].sum() / max(1, n)), 4),
        "nameable_share_effective_comms": round(float(nameable.sum() / max(1, eff.sum())), 4),
        "name_threshold": a.name_th,
    }
    write_json(str(dirs.community_full / f"purity_{a.tag}.json"), agg)

    print(json.dumps(agg, ensure_ascii=False, indent=1))
    order = np.argsort(-n_c)
    shown = 0
    print(f"\nTop communities by size (filtered naming):")
    for c in order:
        if n_c[c] < 2:
            continue
        print(f"  c{int(c):5d} N={n_c[c]:7,d} purityF={shares_f[c]} name={names[c][:40]:42s} "
              f"| {top3s[c][:70]}")
        shown += 1
        if shown >= a.top:
            break
    print(f"\nwrote {out_pq}")


if __name__ == "__main__":
    main()
