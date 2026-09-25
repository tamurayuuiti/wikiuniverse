"""Generate notebooks/poc_colab.ipynb: a self-contained Colab notebook that
writes the wu package to disk via %%writefile cells and runs the pipeline."""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WU = os.path.join(HERE, "wu")
OUT = os.path.join(HERE, "notebooks", "poc_colab.ipynb")

MODULES = ["dumpio.py", "sqlparse.py", "buildedges.py", "stats.py",
           "subsets.py", "analysis.py", "pipeline.py", "report.py", "cli.py", "__init__.py"]


def md(src):
    return {"cell_type": "markdown", "metadata": {}, "source": src.splitlines(keepends=True)}


def code(src):
    return {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
            "source": src.splitlines(keepends=True)}


cells = []
cells.append(md("""# jawiki グラフ分割 PoC — Colab ノートブック

**目的**: jawiki 全記事リンクグラフをコミュニティ分割し、「内部結合が強く外部結合が弱い単位(=銀河)」が得られるかを定量評価する。

構成(§16.3 の骨子に対応):
1. 環境セットアップ
2. `wu` パッケージの書き込み
3. データ取得(Wikimedia ダンプ 4 ファイル, 計 ~1.13GB)
4. ページ正規化(page/redirect/linktarget 解析)
5. リンクグラフ構築(pagelinks ストリーム → 解決済みエッジ)
6. 基本統計(全グラフ次数・ハブ)
7. サブセット抽出(BFS / ID窓)
8. Leiden + コミュニティ指標
9. ヒストグラム表示
10. コミュニティ間エッジ上位表示
11. 階層化テスト(L2)
12. 結論まとめ

注意:
- Colab 無料枠 RAM ~13GB では 100k ノード級サブセットまで安定。全グラフ(142M エッジ, ~10GB)は 32GB ローカル推奨。
- ダウンロードに ~10 分、edges 構築に ~5-15 分(接続速度による)。
- セッションが切れたら「ランタイムを再起動してすべて実行」で再開(ダウンロードは resume 対応)。
"""))

cells.append(code("""# 1. 環境セットアップ
!pip install -q igraph leidenalg pyarrow
import sys, os
print(sys.version)
"""))

cells.append(code("""# 2. wu パッケージ用ディレクトリ
import os
os.makedirs("wu", exist_ok=True)
os.makedirs("data", exist_ok=True)
os.makedirs("results", exist_ok=True)
"""))

for mod in MODULES:
    src = open(os.path.join(WU, mod)).read()
    cells.append(code(f"%%writefile wu/{mod}\n" + src))

cells.append(code("""# 3. データ取得(page / redirect / linktarget / pagelinks, 計 ~1.13GB, resume 対応)
from wu.dumpio import download_all
download_all(["page", "redirect", "linktarget", "pagelinks"], "data/dump", workers=4)
!ls -la data/dump/"""))

cells.append(code("""# 4. ページ正規化: page/linktarget/redirect を解析 → ハッシュ表・リダイレクト連鎖解決
from wu.cli import main as wu_main
import sys
def wu(*args):
    sys.argv = ["wu", "--base", "data", *args]
    wu_main()

wu("parse", "--dump-date", "2026-09-02")  # ← ダンプ日付は latest の実値に更新すること
import json; print(json.dumps(json.load(open("data/parsed/meta.json")), ensure_ascii=False, indent=1))"""))

cells.append(code("""# 5. リンクグラフ構築: pagelinks → 解決済み ns0→ns0 エッジ(赤リンク/リダイレクト/セルフループ処理)
wu("edges")
import os; print("edges_ns0.bin:", os.path.getsize("data/graph/edges_ns0.bin")/1e9, "GB")"""))

cells.append(code("""# 6. 基本統計(全グラフ次数分布・ハブ top)
wu("stats")
import json
d = json.load(open("data/graph/full_stats.json"))
print(json.dumps(d["stats"], ensure_ascii=False, indent=1))
for h in d["top_hubs"][:25]:
    print(f'{h["rank"]:3d}. {h["title"][:40]:42s} in={h["in"]:7,d} out={h["out"]:6,d} {",".join(h["flags"])}')"""))

cells.append(code("""# 7a. スモークテスト用サブセット: 地理系 BFS 30k(1-2分)
wu("subset-bfs", "--seeds", "東京都,大阪府,京都府,北海道,福岡県,愛知県,宮城県,広島県,新潟県,長野県,日本の地理,市町村",
   "--max-nodes", "30000", "--cap", "40", "--hops", "3", "--max-in-degree", "10000", "--name", "bfs_geo_30k")"""))

cells.append(code("""# 7b. 対照実験: ID窓サブセット(リーク率が跳ね上がることを確認 — 構造評価には使用禁止)
wu("subset-id", "--k", "10000", "--mode", "late", "--name", "idwin_late_10k")"""))

cells.append(code("""# 8-11. Leiden(resolution スイープ)→ 指標 → ハブ → L2 階層化 → レポート/プロット
wu("analyze", "--subset", "bfs_geo_30k", "--out-dir", "results/bfs_geo_30k",
   "--resolutions", "0.5,1.0,2.0", "--engine", "igraph")"""))

cells.append(code("""# 9. ヒストグラム表示
from IPython.display import display, Image, Markdown
import glob
for p in sorted(glob.glob("results/bfs_geo_30k/*.png")):
    display(Image(p))"""))

cells.append(code("""# 10-11. 数値確認: 全体指標 / top ペア / 階層
import json
m = json.load(open("results/bfs_geo_30k/metrics.json"))
print("== global ==");  print(json.dumps(m["global"], ensure_ascii=False, indent=1))
print("== sweep ==");    print(json.dumps(m["sweep"], ensure_ascii=False, indent=1))
print("== top pairs =="); 
lab = m["community_labels"]
for p in m["top_inter_pairs"][:15]:
    print(p["edges"], "|", ",".join(lab.get(str(p["comm_a"]),[])[:2]), "<->", ",".join(lab.get(str(p["comm_b"]),[])[:2]))
print("== hierarchy =="); print(json.dumps(m["hierarchy"]["global"] if m["hierarchy"] else None, ensure_ascii=False, indent=1))
display(Markdown(open("results/bfs_geo_30k/report.md").read()))"""))

cells.append(code("""# 12. 本番 PoC: 100k ノード(Colab RAM 注意: igraph エンジンのとき ~600MB)
wu("subset-bfs", "--seeds", "東京都,大阪府,京都府,北海道,福岡県,愛知県,宮城県,広島県,新潟県,長野県,日本の地理,市町村",
   "--max-nodes", "100000", "--cap", "60", "--hops", "4", "--max-in-degree", "10000", "--name", "bfs_geo_100k")
wu("analyze", "--subset", "bfs_geo_100k", "--out-dir", "results/bfs_geo_100k_raw",
   "--resolutions", "0.5,1.0,2.0", "--engine", "igraph")"""))

cells.append(code("""# 12b. レイアウト用グラフ実験: smart 次数予算剪定(銀河を壊さずハブ間高速道路のみ剪定)
wu("analyze", "--subset", "bfs_geo_100k", "--out-dir", "results/bfs_geo_100k_smart40",
   "--degree-budget", "40", "--degree-budget-mode", "smart", "--resolutions", "1.0", "--engine", "igraph")"""))

cells.append(code("""# 13. 結果の保存(Google Drive へ)
# from google.colab import drive; drive.mount('/content/drive')
# !cp -r results /content/drive/MyDrive/wikiuniverse_results
!zip -qr results.zip results data/parsed/meta.json data/graph/full_stats.json
print("done")"""))

nb = {
    "cells": cells,
    "metadata": {
        "colab": {"provenance": [], "toc_visible": True},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python", "version": "3.11"},
    },
    "nbformat": 4,
    "nbformat_minor": 0,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w") as f:
    json.dump(nb, f, ensure_ascii=False, indent=1)
print("wrote", OUT, os.path.getsize(OUT), "bytes,", len(cells), "cells")
