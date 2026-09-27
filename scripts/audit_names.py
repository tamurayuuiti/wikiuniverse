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
members survives the frequency filter and can win the tf-idf naming score, so
the galaxy gets named after the stub category instead of its topic.
Such names are topically real (that galaxy *is* the short articles about XX)
but read like maintenance labels. To suppress them, add the patterns printed
below to NAME_BLACKLIST in build_galaxy_catalog.py and re-run
build_galaxy_catalog.py + export_viewer_tiles.py (no re-Leiden, no re-layout).

Usage:
  python scripts/audit_names.py --base data [--top 25]
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.paths import Dirs  # noqa: E402

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
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
    print(f"  → 抑制したい場合は build_galaxy_catalog.py の NAME_BLACKLIST へ正規表現を追加し、")
    print(f"    build_galaxy_catalog.py → export_viewer_tiles.py を再実行(再 Leiden/再レイアウト不要)。")

    # 大きい銀河ほど名前が重要: サイズ降順で確認
    print(f"\n[names] ---- サイズ上位 {a.top} 銀河 ----")
    print(f"  {'gid':>6s} {'n':>6s} {'class':8s} {'share':>6s} {'src':8s} name / rep_titles")
    for i in sorted(range(G), key=lambda i: -nart[i])[: a.top]:
        sh = shares[i]
        sh_s = f"{float(sh):.3f}" if sh is not None else "  -  "
        print(f"  {int(gal['galaxy_id'][i]):6d} {nart[i]:6,d} {cls[i]:8s} {sh_s:>6s} "
              f"{srcs[i]:8s} {names[i] or '(無名)'}  |  {reps[i][:56]}")

    # マクロ(銀河団)ラベル = ビューア表示そのままの導出
    if os.path.exists(mp):
        mac = pq.read_table(mp).to_pydict()
        M = len(mac["macro_id"])
        mrep = [str(x or "") for x in mac.get("rep_titles", [""] * M)]
        mn = [int(x) for x in mac["n_articles"]]
        mdust = [bool(x) for x in mac.get("is_dust", [False] * M)]
        eff = [i for i in range(M) if not mdust[i] and mn[i] > 1]
        print(f"\n[names] macros={M:,} (実効={len(eff):,} / dust={M - len(eff):,})")
        print("  銀河団ラベルは「内部次数最大の代表記事タイトル先頭 16 文字」(export 側の導出):")
        for i in sorted(eff, key=lambda i: -mn[i])[: a.top]:
            label = mrep[i].split(",")[0].strip()[:16] or f"銀河団{i + 1}"
            print(f"    macro {int(mac['macro_id'][i]):4d} n={mn[i]:8,d}  "
                  f"label=「{label}」  rep_titles={mrep[i][:60]}")
        print("  ※ 銀河団には主題名キュレーションが無い(代表記事名そのまま)。")
        print("    L2 銀河団(galaxies.cluster_l2)は算出済みだがビューア未使用 = 名称なし。")
    else:
        print(f"\n[names] macros.parquet が見つかりません: {mp}")

    print("\n[names] 判定: 上記の件数を見て NAME_BLACKLIST 拡張の要否を判断してください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
