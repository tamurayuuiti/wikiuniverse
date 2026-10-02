# wu/catalog.py — 銀河カタログの生成(data/final/ = レイアウト・ビューアのデータ契約)
#
# 責務:
# - 既存成果物(membership/pairs/per_community/purity/clusters)から、表示とレイアウトが
#   使う台帳を一括生成する: galaxies/macros/galaxy_pairs_topK/macro_pairs/catalog_meta。
# - 銀河名のキュレーション(メタカテゴリ除外 → tf-idf → 語幹正規化 → 代表記事
#   フォールバック)と display_class(galaxy/medium/dust)の分類を含む。
#
# 注意:
# - main(a) は Namespace 注入でステージから呼ばれる(test_catalog が _stem_name
#   含め担保)。正準実行は python -m wu run catalog。
# - purity/clusters/per_community_<macro> は任意入力(欠けても縮退して動作する)ため、
#   ステージ契約の inputs には必須分のみ宣言している(wu/stages/catalog.py 参照)。

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

Naming: purity tf-idf category (share >= 0.15, non-blacklisted; maintenance
suffixes such as "...stub items" are stem-normalized -> name_source
"category_stem") -> representative article title (+ "etc.") -> "" (the viewer
then shows galaxy#<id>).

Usage:
  python -m wu run catalog --base data   # 参数 override: --set catalog.<key>=<value> \
      --galaxy-tag res1_sub --macro-tag res1 [--top-neighbors 8] [--top-pairs 30000]
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

import numpy as np


from ..dumpio import write_json  # noqa: E402
from ..paths import Dirs  # noqa: E402
import re  # noqa: E402

# カテゴリ名のブラックリスト(技術・保守系。頻度フィルタをすり抜ける中頻度メタ)
NAME_BLACKLIST = [
    r"を使用しているページ", r"^ウィキデータにある", r"^ウィキデータにない",
    r"誤りがあるページ", r"仮リンク", r"保護中のページ", r"翻訳を必要とする",
    r"にbackgroundと", r"^すべての", r"曖昧さ回避$", r"不明$", r"名目録$",
    r"^Template", r"^Pages ", r"^Articles ", r"^Use dmy",
]
NAME_BLACKLIST_RE = [re.compile(x) for x in NAME_BLACKLIST]

# 媒介銀河(medium)判定: 代表記事が年・元号・汎用ハブ
MEDIUM_REP_PATTERNS = [
    re.compile(r"^\d{1,4}年"), re.compile(r"^(昭和|平成|大正|明治|慶応|江戸時代|19世紀|20世紀|21世紀)$"),
]
MEDIUM_HUBS = {"日本", "英語", "ISBN", "地理座標系", "ウェイバックマシン", "YouTube",
               "X_(ソーシャル・ネットワーキング・サービス)", "デジタルオブジェクト識別子",
               "ISSN", "PubMed", "国立国会図書館", "アメリカ合衆国", "日本の郵便番号",
               "NDL", "VIAF", "LCCN", "CiNii", "GND"}


def _blacklisted(title: str) -> bool:
    return any(r.search(title) for r in NAME_BLACKLIST_RE)


# 保守サフィックスの語幹正規化。主題別スタブカテゴリ(「〜関連のスタブ項目」等)は
# 成員数百〜数千で頻度フィルタを通過し tf-idf で勝つため、単純除外すると rep
# フォールバック(ハブ記事汚染: 「YouTube等」「日本等」)へ品質が劣化する。
# そこで保守部分を除いた主題語幹を採用する(「シングル関連のスタブ項目」→「シングル」、
# 「野球に関する記事」→「野球」、「地理座標系の一覧」→「地理座標系」)。
# 語幹が空・短すぎ・ブラックリスト該当の場合は "" を返し、従来のフォールバックに委ねる。
MAINT_SUFFIX_RE = [
    re.compile(r"^(?P<stem>.+?)(?:に関する|関連の|関連)?スタブ(?:項目|記事)?$"),
    re.compile(r"^(?P<stem>.+?)に関する記事(?:の一覧)?$"),
    re.compile(r"^(?P<stem>.+?)の一覧$"),
]


def _stem_name(title: str) -> str:
    """カテゴリ名 → 主題語幹。正規化できない(しない)場合は ""。"""
    if not title or _blacklisted(title):
        return ""
    for rx in MAINT_SUFFIX_RE:
        m = rx.match(title)
        if not m:
            continue
        stem = m.group("stem").rstrip("の_ ・")
        if len(stem) >= 2 and not _blacklisted(stem):
            return stem
    return ""


def _parse_top3(s3: str):
    out = []
    for part in (s3 or "").split(" / "):
        m = re.match(r"(.+)\(([\d.]+)\)$", part.strip())
        if m:
            out.append((m.group(1), float(m.group(2))))
    return out


def main(a=None):
    # a=None のときだけ CLI 引数を解析する(ステージからは Namespace を注入
    # して呼ぶ = 引数解析と処理本体の分離。既存の CLI 挙動は不変)。
    if a is None:
        ap = argparse.ArgumentParser()
        ap.add_argument("--base", default="data")
        ap.add_argument("--galaxy-tag", default="res1_sub")
        ap.add_argument("--macro-tag", default="res1")
        ap.add_argument("--top-neighbors", type=int, default=8)
        ap.add_argument("--top-pairs", type=int, default=30000)
        a = ap.parse_args()
    dirs = Dirs(a.base)
    full = str(dirs.community_full)
    out_dir = str(dirs.final)
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

    # ---- curated naming + display class
    raw_names = (purity["name"] if purity else [""] * G)
    name_shares = (purity["top1_share_filt"] if purity else [None] * G)
    top3_filt = (purity.get("top3_filt") if purity else None)
    reps = per_g["rep_titles"]
    out_ratio = np.asarray(per_g["out_ratio_c"], np.float64)

    names, name_sources, display_classes = [], [], []
    for g_i in range(G):
        n_art = int(n_c[g_i])
        rep_list = [x.strip() for x in str(reps[g_i]).split(",") if x.strip()]
        # 1) dust
        if n_art <= 1:
            display_classes.append("dust")
        # 2) medium: hub-ish reps or extreme out_ratio
        elif ((rep_list and (any(p_.search(rep_list[0]) for p_ in MEDIUM_REP_PATTERNS)
                             or rep_list[0] in MEDIUM_HUBS
                             or sum(1 for r in rep_list[:3] if r in MEDIUM_HUBS) >= 2))
              or (n_art >= 1000 and not np.isnan(out_ratio[g_i]) and out_ratio[g_i] >= 0.90)):
            display_classes.append("medium")
        else:
            display_classes.append("galaxy")
        # 3) name: filtered category (non-blacklisted, share>=0.15) -> rep fallback
        #    採用カテゴリ名は保守サフィックスの語幹正規化を通す(category_stem)
        chosen, src = "", "none"
        cands = _parse_top3(top3_filt[g_i]) if top3_filt is not None else []
        if raw_names[g_i] and not _blacklisted(raw_names[g_i]) and \
                (name_shares[g_i] or 0) >= 0.15:
            cands = [(raw_names[g_i], name_shares[g_i])] + cands
        for t, sh in cands:
            if sh >= 0.15 and not _blacklisted(t):
                stem = _stem_name(t)
                chosen, src = (stem, "category_stem") if stem else (t, "category")
                break
        if not chosen and rep_list:
            chosen, src = (rep_list[0] + ("等" if len(rep_list) > 1 else "")), "rep"
        names.append(chosen)
        name_sources.append(src)
    n_medium = display_classes.count("medium")
    n_by_cat = name_sources.count("category")
    print(f"[catalog] display_class: galaxy={display_classes.count('galaxy')} "
          f"medium={n_medium} dust={display_classes.count('dust')}; "
          f"name_source: category={n_by_cat} "
          f"category_stem={name_sources.count('category_stem')} "
          f"rep={name_sources.count('rep')}")

    galaxies = pa.table({
        "galaxy_id": pa.array(np.arange(G, dtype=np.int32)),
        "macro_id": pa.array(macro_of),
        "cluster_l2": pa.array(cluster_of),
        "n_articles": pa.array(n_c.astype(np.int32)),
        "e_in": pa.array(np.asarray(per_g["E_in_c"], np.int64)),
        "e_out": pa.array(np.asarray(per_g["E_out_c"], np.int64)),
        "out_ratio": pa.array(np.asarray(per_g["out_ratio_c"], np.float64), type=pa.float64()),
        "is_dust": pa.array(is_dust),
        "display_class": pa.array(display_classes, type=pa.string()),
        "name": pa.array([str(x) if x else "" for x in names], type=pa.string()),
        "name_source": pa.array(name_sources, type=pa.string()),
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
    macro_dust = nodes_per_macro <= 1
    macros = pa.table({
        "macro_id": pa.array(np.arange(Mac, dtype=np.int32)),
        "n_articles": pa.array(nodes_per_macro.astype(np.int32)),
        "is_dust": pa.array(macro_dust),
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
        "n_macros": Mac, "n_macros_effective": int((~macro_dust).sum()),
        "n_dust": int(is_dust.sum()), "n_medium_galaxies": n_medium,
        "named_by_category": n_by_cat,
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
