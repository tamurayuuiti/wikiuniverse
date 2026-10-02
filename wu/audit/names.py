# wu/audit_names.py — 銀河/銀河団の命名監査(読み取り専用・再計算なし)
#
# 責務: build_galaxy_catalog が実際に決めた名前(name_source 分布・保守ラベル残存・
#   上位銀河の名前)と、ビューアに出る銀河団ラベル(既定導出 + オーバーライド)を報告する。
#   --dump-macro-labels で編集可能なオーバーライド表のテンプレートを出力する。
# 注意: 正準実行は python -m wu run audit_names(読み取り専用・再計算なし)。

"""Audit galaxy/macro naming in data/final/ — read-only, no recompute.

`build_galaxy_catalog.py` decides every display name. This script reports what
it actually decided, so naming policy can be tuned against real numbers:

  - name_source distribution (category / rep / none) per display_class
  - how many names look like maintenance labels rather than topics
    (stub / disambiguation / list / "articles needing ..." etc.)
  - the top-N galaxies by size with name, share, source and rep_titles
  - macro (galaxy-cluster) labels as the viewer will show them
    (= macros.rep_titles first entry, truncated to 16 chars)

Why "XXのスタブ" shows up: jawiki has topic-specific maintenance categories
(`Category:日本の地理のスタブ`, `Category:映画のスタブ`, ...). The global
`すべてのスタブ記事` is huge and is dropped by purity's `--max-cat-freq`
(default 20000), but a topic stub category with a few hundred–few thousand
members survives the frequency filter and can win the tf-idf naming score.
Countermeasure (implemented): build_galaxy_catalog.py stem-normalizes such
names ("シングル関連のスタブ項目" -> "シングル", name_source=category_stem),
so stub-suffixed names should no longer appear after a catalog rebuild; if
they do, the pattern is not covered by MAINT_SUFFIX_RE there.

Macro (galaxy-cluster) labels default to macros.rep_titles[0][:16] and can be
hand-curated via data/final/macro_label_overrides.json. `--dump-macro-labels`
writes an editable template (macro size, default label, top member galaxy
names) to data/final/macro_label_overrides.template.json; export_viewer_tiles.py
applies non-empty labels from macro_label_overrides.json at publish time.

Usage:
  python -m wu run audit_names --base data   (--set audit_names.top=N 等)
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter


from ..paths import Dirs  # noqa: E402

# 「主題名ではなく保守ラベルに見える」名前の検出用(報告専用。除外はしない)。
SUSPECT = {
    "スタブ": re.compile(r"スタブ"),
    "曖昧さ回避": re.compile(r"曖昧さ回避"),
    "一覧": re.compile(r"一覧"),
    "記事名・語句": re.compile(r"記事名|^(記事|語句|単語)(の一覧|$)"),
    "存命人物・人物": re.compile(r"存命人物|人物の一覧|^姓$|^名$"),
    "識別子系": re.compile(r"識別子|ISBN|ISSN|DOI|NDL|VIAF|LCCN|CiNii|GND|allcinema|titlestyle",
                        re.IGNORECASE),
    "年・元号": re.compile(r"^\d{1,4}年|^(昭和|平成|大正|明治|慶応|江戸時代|\d{1,2}世紀)$"),
    "座標・地図": re.compile(r"座標|Kartographer|地図"),
    "ウィキデータ": re.compile(r"ウィキデータ"),
}


def main(a) -> int:
    # a=None のときだけ CLI 引数を解析する(ステージは Namespace 注入で呼ぶ)。
    dirs = Dirs(a.base)

    import pyarrow.parquet as pq

    gp = os.path.join(str(dirs.final), "galaxies.parquet")
    mp = os.path.join(str(dirs.final), "macros.parquet")
    if not os.path.exists(gp):
        print(f"[names] galaxies.parquet が見つかりません: {gp}")
        return 2
    gal = pq.read_table(gp).to_pydict()
    G = len(gal["galaxy_id"])

    names = [str(x or "") for x in gal["name"]]
    srcs = [str(x or "none") for x in gal["name_source"]]
    cls = [str(x or "galaxy") for x in gal["display_class"]]
    nart = [int(x) for x in gal["n_articles"]]
    shares = gal.get("name_share") or [None] * G
    reps = [str(x or "") for x in gal.get("rep_titles", [""] * G)]

    print(f"[names] galaxies={G:,}  (display_class: "
          + ", ".join(f"{k}={v}" for k, v in sorted(Counter(cls).items())) + ")")
    print(f"[names] name_source: "
          + ", ".join(f"{k}={v}" for k, v in sorted(Counter(srcs).items())))
    print(f"[names] 名前あり={sum(1 for x in names if x):,} / 名前なし={sum(1 for x in names if not x):,}")

    # クラス×ソースのクロス集計(どのクラスがフォールバック頼みか)
    print("\n[names] ---- display_class × name_source ----")
    cross = Counter(zip(cls, srcs))
    for c in sorted({k[0] for k in cross}):
        row = ", ".join(f"{s}={cross[(c, s)]}" for s in sorted({k[1] for k in cross if k[0] == c}))
        print(f"  {c:8s}: {row}")

    # 保守ラベルに見える名前
    print("\n[names] ---- 保守ラベルに見える名前(報告のみ・除外しない) ----")
    total_suspect = 0
    for label, rx in SUSPECT.items():
        hits = [i for i in range(G) if names[i] and rx.search(names[i])]
        arts = sum(nart[i] for i in hits)
        total_suspect += len(hits)
        sample = ", ".join(names[i] for i in sorted(hits, key=lambda i: -nart[i])[:4])
        print(f"  {label:10s}: {len(hits):5,d} 銀河 / {arts:9,d} 記事   例: {sample}")
    print(f"  (重複計上あり: 1 銀河が複数パターンに該当しうる)")
    print(f"  → スタブ/一覧系は build_galaxy_catalog.py の語幹正規化(name_source=category_stem)")
    print(f"    適用済みのはず。残存があれば正規化対象外パターンなので NAME_BLACKLIST への")
    print(f"    追加を検討し、build_galaxy_catalog.py → export_viewer_tiles.py を再実行")
    print(f"    (再 Leiden/再レイアウト不要)。")

    # 大きい銀河ほど名前が重要: サイズ降順で確認
    print(f"\n[names] ---- サイズ上位 {a.top} 銀河 ----")
    print(f"  {'gid':>6s} {'n':>6s} {'class':8s} {'share':>6s} {'src':8s} name / rep_titles")
    for i in sorted(range(G), key=lambda i: -nart[i])[: a.top]:
        sh = shares[i]
        sh_s = f"{float(sh):.3f}" if sh is not None else "  -  "
        print(f"  {int(gal['galaxy_id'][i]):6d} {nart[i]:6,d} {cls[i]:8s} {sh_s:>6s} "
              f"{srcs[i]:8s} {names[i] or '(無名)'}  |  {reps[i][:56]}")

    # マクロ(銀河団)ラベル = ビューア表示そのままの導出(+オーバーライド反映)
    if os.path.exists(mp):
        mac = pq.read_table(mp).to_pydict()
        M = len(mac["macro_id"])
        mrep = [str(x or "") for x in mac.get("rep_titles", [""] * M)]
        mn = [int(x) for x in mac["n_articles"]]
        mdust = [bool(x) for x in mac.get("is_dust", [False] * M)]
        eff = [i for i in range(M) if not mdust[i] and mn[i] > 1]

        # export_viewer_tiles.py が出版時に適用するオーバーライド(同一形式を読む)
        ovr = {}
        ovr_path = os.path.join(str(dirs.final), "macro_label_overrides.json")
        if os.path.exists(ovr_path):
            with open(ovr_path, encoding="utf-8") as f:
                for ent in json.load(f).get("macros", []):
                    lbl = str(ent.get("label") or "").strip()
                    if lbl:
                        ovr[int(ent["macro_id"])] = lbl

        def default_label(i: int) -> str:
            return mrep[i].split(",")[0].strip()[:16] or f"銀河団{i + 1}"

        print(f"\n[names] macros={M:,} (実効={len(eff):,} / dust={M - len(eff):,})"
              + (f"  overrides={len(ovr)} 件" if ovr else ""))
        print("  既定ラベル=「内部次数最大の代表記事タイトル先頭 16 文字」。"
              "macro_label_overrides.json があれば優先される (ovr):")
        for i in sorted(eff, key=lambda i: -mn[i])[: a.top]:
            mid = int(mac["macro_id"][i])
            label = ovr.get(mid, default_label(i))
            mark = " (ovr)" if mid in ovr else ""
            print(f"    macro {mid:4d} n={mn[i]:8,d}  "
                  f"label=「{label}」{mark}  rep_titles={mrep[i][:60]}")
        print("  ※ L2 銀河団(galaxies.cluster_l2)は算出済みだがビューア未使用 = 名称なし。")

        if a.dump_macro_labels:
            # マクロごとの所属銀河名(サイズ降順・dust 除く・名前ありのみ)= キュレーションのヒント
            by_macro = {}
            for i in range(G):
                if cls[i] != "dust" and names[i]:
                    by_macro.setdefault(int(gal["macro_id"][i]), []).append((nart[i], names[i]))
            tmpl = {"_comment": "マクロラベルのオーバーライド。label を編集して "
                                 "data/final/macro_label_overrides.json として保存 → "
                                 "export_viewer_tiles.py を再実行すると出版に反映される。"
                                 "_ で始まるキーと空 label は無視される。",
                    "macros": []}
            for i in sorted(eff, key=lambda i: -mn[i]):
                mid = int(mac["macro_id"][i])
                gals = sorted(by_macro.get(mid, []), reverse=True)[:5]
                tmpl["macros"].append({
                    "macro_id": mid, "n_articles": mn[i],
                    "default_label": default_label(i),
                    "top_galaxies": " / ".join(f"{nm}({n:,})" for n, nm in gals),
                    "label": ovr.get(mid, default_label(i))})
            out_p = os.path.join(str(dirs.final), "macro_label_overrides.template.json")
            with open(out_p, "w", encoding="utf-8") as f:
                json.dump(tmpl, f, ensure_ascii=False, indent=1)
            print(f"\n[names] テンプレート出力: {out_p} (実効マクロ {len(tmpl['macros'])} 件)")
            print("        label を編集して data/final/macro_label_overrides.json として保存し、")
            print("        python -m wu run publish --base data を再実行してください。")
    else:
        print(f"\n[names] macros.parquet が見つかりません: {mp}")

    print("\n[names] 判定: 上記の件数が許容範囲か確認してください"
          "(スタブ系は語幹正規化後は 0 件になっているはず)。")
    return 0

