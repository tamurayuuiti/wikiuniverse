# wu/audit/tiles.py — 公開面(data/spatial/)の整合監査(読み取り専用・再計算なし)
#
# 責務: タイル bin/サイドカー json の欠落・サイズ/件数不一致・タイトル欠落を計測し、
#   `local#NN` 表示の原因がデータ側(A)かビューア側 LRU(B)かを切り分ける。
# 注意: 正準実行は python -m wu run audit_tiles(読み取り専用・再計算なし)。

"""出版済みビューアタイル(data/spatial/)の監査 - 読み取り専用・再計算なし。

目的: ビューアは記事タイトルを解決できないときラベル ``local#<idx>`` へ
フォールバックする。原因は独立に 2 つあり、本スクリプトはそれらを区別する:

  (A) データ側 - sidecar ``gal_XXXXXX.json`` が欠落/読めない、または
      ``gal_XXXXXX.bin`` の宣言 ``n`` よりタイトルが少ない。この場合、
      不足分はデコード時に ``local#k`` で補完される(tileCache.padTitles)。
  (B) ビューア側 - ディスク上のタイトルは完全だが、タイルがメモリ内 LRU から
      追い出された後もその点群が画面上に残っており、hover がタイトルを
      引けなかった。本スクリプトは (B) を観測できない。(A) が欠損 0 を
      報告してもビューアが ``local#NN`` を出すなら、原因は (B) である。

銀河タイルごとの検査:
  - .bin が存在し、ヘッダの ``n`` が bootstrap ``galaxies[gid][6]`` と一致
  - .bin のサイズが宣言の n / n_edges / n_cross と一致
  - .json が存在・パース可能で、``len(t) == n``
  - タイトルが非空文字列で、``local#`` で字面開始するものが無い
  - 総和: 記事総数・銀河内エッジ総数・クロスのリンク総数

比較用に、記録済みのグラフ総量も表示する(記録値は results/SUMMARY.md §1 と
LOCAL_SETUP.md §3 にある。本スクリプトは何も再計算しない)。

使い方:
  python -m wu run audit_tiles --base data   (--set audit_tiles.limit=N)
"""
from __future__ import annotations

import json
import os
import struct
import sys


from ..paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402

# 記録済みの基準値(再計算しない。正は results/SUMMARY.md §1 / LOCAL_SETUP.md §3)。
RECORDED = {
    "articles_ns0_nonredirect": 1_516_326,
    "edges_directed_resolved": 142_446_159,
    "edges_undirected_unique": 108_164_716,
}


def main(a) -> int:
    # a=None のときだけ CLI 引数を解析する(ステージは Namespace 注入で呼ぶ)。
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

