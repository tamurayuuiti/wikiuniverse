"""Audit the published viewer tiles (data/spatial/) — read-only, no recompute.

Purpose: the viewer falls back to the label ``local#<idx>`` whenever it cannot
resolve an article title. That happens for two independent reasons, and this
script separates them:

  (A) DATA side  — the sidecar ``gal_XXXXXX.json`` is missing, unreadable, or
      has fewer titles than the ``n`` declared in ``gal_XXXXXX.bin``. Then the
      shortfall is padded with ``local#k`` at decode time (tileCache.padTitles).
  (B) VIEWER side — titles are complete on disk but the tile was evicted from
      the in-memory LRU while its points were still on screen, so hover could
      not look the title up. This script cannot see (B); if (A) reports 0
      missing titles yet the viewer still shows ``local#NN``, the cause is (B).

Checks per galaxy tile:
  - .bin exists and its header ``n`` matches bootstrap ``galaxies[gid][6]``
  - .bin size matches the declared n / n_edges / n_cross
  - .json exists, parses, and ``len(t) == n``
  - titles are non-empty strings and none literally start with ``local#``
  - sums: total articles, total internal edges, total cross links

Also prints the recorded graph totals for comparison (these live in
results/SUMMARY.md §1 and LOCAL_SETUP.md §3; nothing is recomputed here).

Usage:
  python scripts/audit_tiles.py --base data [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402

# 記録済みの基準値(再計算しない。正は results/SUMMARY.md §1 / LOCAL_SETUP.md §3)。
RECORDED = {
    "articles_ns0_nonredirect": 1_516_326,
    "edges_directed_resolved": 142_446_159,
    "edges_undirected_unique": 108_164_716,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--limit", type=int, default=20,
                    help="max problem galaxies to list in detail")
    a = ap.parse_args()
    dirs = Dirs(a.base)
    tiles = str(dirs.tiles)
    boot_path = os.path.join(str(dirs.spatial), "bootstrap.json")

    if not os.path.exists(boot_path):
        print(f"[audit] bootstrap.json が見つかりません: {boot_path}")
        print("        export_viewer_tiles.py で出版してから実行してください。")
        return 2
    with open(boot_path, encoding="utf-8") as f:
        boot = json.load(f)
    meta = boot.get("meta", {})
    galaxies = boot.get("galaxies", [])
    print(f"[audit] bootstrap: run={meta.get('layout_run')} "
          f"generated_at={meta.get('generated_at')} "
          f"n_articles={meta.get('n_articles'):,} n_galaxies={meta.get('n_galaxies'):,}")
    if str(meta.get("layout_run")) != ACTIVE_LAYOUT_RUN:
        print(f"[audit][warn] 出版元 run ({meta.get('layout_run')}) ≠ "
              f"ACTIVE_LAYOUT_RUN ({ACTIVE_LAYOUT_RUN})")

    n_missing_bin = n_missing_json = n_bad_json = 0
    n_size_mismatch = n_header_mismatch = 0
    n_title_short = n_title_empty = n_title_local = 0
    tot_articles = tot_internal = tot_cross = tot_titles = 0
    problems: list[str] = []

    for gid, row in enumerate(galaxies):
        n_boot = int(row[6])
        bin_p = os.path.join(tiles, f"gal_{gid:06d}.bin")
        js_p = os.path.join(tiles, f"gal_{gid:06d}.json")
        if not os.path.exists(bin_p):
            n_missing_bin += 1
            problems.append(f"gal_{gid:06d}: .bin 無し (bootstrap n={n_boot})")
            continue
        with open(bin_p, "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                n_size_mismatch += 1
                problems.append(f"gal_{gid:06d}: .bin ヘッダ不足")
                continue
            n, ne, nx = struct.unpack("<III", head)
            want = 12 + n * 12 + ne * 8 + nx * 12
            actual = os.path.getsize(bin_p)
        tot_articles += n
        tot_internal += ne
        tot_cross += nx
        if n != n_boot:
            n_header_mismatch += 1
            problems.append(f"gal_{gid:06d}: .bin n={n} ≠ bootstrap n={n_boot}")
        if actual != want:
            n_size_mismatch += 1
            problems.append(f"gal_{gid:06d}: .bin サイズ {actual} ≠ 期待 {want}")
        # sidecar
        if not os.path.exists(js_p):
            n_missing_json += 1
            n_title_short += n
            problems.append(f"gal_{gid:06d}: .json 無し → 全 {n} 件が local# 化")
            continue
        try:
            with open(js_p, encoding="utf-8") as f:
                titles = json.load(f).get("t", [])
        except Exception as exc:  # noqa: BLE001
            n_bad_json += 1
            n_title_short += n
            problems.append(f"gal_{gid:06d}: .json 解析失敗 ({exc}) → 全 {n} 件 local# 化")
            continue
        got = len(titles)
        tot_titles += got
        if got < n:
            n_title_short += n - got
            problems.append(f"gal_{gid:06d}: タイトル {got}/{n} → {n - got} 件 local# 化")
        empty = sum(1 for t in titles if not isinstance(t, str) or not t)
        loc = sum(1 for t in titles if isinstance(t, str) and t.startswith("local#"))
        if empty:
            n_title_empty += empty
            problems.append(f"gal_{gid:06d}: 空タイトル {empty} 件")
        if loc:
            n_title_local += loc
            problems.append(f"gal_{gid:06d}: タイトルが local#… のまま {loc} 件(出版側の欠陥)")

    print("\n[audit] ---- データ側(A)の結果 ----")
    print(f"  .bin 無し                 : {n_missing_bin}")
    print(f"  .bin ヘッダ n 不一致       : {n_header_mismatch}")
    print(f"  .bin サイズ不一致          : {n_size_mismatch}")
    print(f"  .json 無し                : {n_missing_json}")
    print(f"  .json 解析失敗             : {n_bad_json}")
    print(f"  タイトル不足(合計件数)     : {n_title_short:,}")
    print(f"  空タイトル                : {n_title_empty}")
    print(f"  タイトルが local#… のまま  : {n_title_local}")

    print("\n[audit] ---- タイル合計 ----")
    print(f"  記事(bin n の合計)        : {tot_articles:,}")
    print(f"  タイトル(json t の合計)    : {tot_titles:,}")
    print(f"  内部エッジ                : {tot_internal:,}")
    print(f"  クロスリンク(cap 後)       : {tot_cross:,}")

    print("\n[audit] ---- 記録済みのグラフ基準値(参考・再計算せず) ----")
    for k, v in RECORDED.items():
        print(f"  {k:28s}: {v:,}")
    print("  ※ 記事ノードは銀河分割の membership 長(= bin n の合計)と一致するはず。")
    print("  ※ 内部+クロスは cap/サンプリング後の値なので無向一意エッジとは一致しない。")

    if problems:
        print(f"\n[audit] ---- 問題のあるタイル(先頭 {a.limit} 件 / 全 {len(problems)} 件) ----")
        for line in problems[: a.limit]:
            print(f"  {line}")
        verdict = "データ側に欠陥あり → export_viewer_tiles.py を再実行してください。"
    else:
        verdict = ("データ側はクリーン(全タイトル完備)。それでも viewer で local#NN が出るなら\n"
                   "        原因はビューア側の LRU 追い出しです(描画中タイルがキャッシュから\n"
                   "        落ちて hover がタイトルを引けない)。starField の pinTiles 保護と\n"
                   "        Entry.titles 参照で修正済み。")
    print(f"\n[audit] 判定: {verdict}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
