# wikiuniverse — jawiki 全記事グラフ「宇宙/銀河団/銀河/記事」可視化 PoC ツールキット

**最重要仮説**: 「実際の jawiki グラフは、内部結合が強く・外部結合が弱く・計算的に独立させやすい単位に分割できるか?」を検証するためのパイプライン。

第 1 回検証結果と数値は → [`results/SUMMARY.md`](results/SUMMARY.md)(暫定判定: **B 条件付きで有望**)

## 設計原則

1. **3 層分離**: 保存層(全ノード・全エッジ)/ レイアウト層(smart 剪定・ハブ抑制した近似グラフ)/ 描画層(LOD・間引き)
2. **低 RAM ストリーミング**: 巨大 dict / NetworkX 全読み込みを避け、
   - タイトル結合は blake2b64 ハッシュ + ソート済み配列 `searchsorted`
   - エッジは int32 ペアの生バイナリ(`edges_ns0.bin`)+ memmap
   - SQL ダンプはチャンク逐次解析(チェックポイント resume 付き)
   - igraph は **バッチ add_edges + malloc_trim**、Leiden は **igraph ネイティブ engine**(leidenalg の 2 倍メモリ効率)
3. **再現性**: dump 日付・シード(42)・パラメータ・実行時間をすべて `meta.json` / `metrics.json` に記録

## ディレクトリ

### コード(ワークスペース直下、構成は不変)

```
wikiuniverse/
├── wu/                    # 本体パッケージ
│   ├── paths.py           # ★ データレイアウトの単一の真実(Dirs クラス)
│   ├── dumpio.py          # ダウンロード(resume/並列)、gz ストリーム、json I/O
│   ├── sqlparse.py        # page/linktarget/redirect のストリーミング解析 → parsed/
│   ├── buildedges.py      # pagelinks → 解決済み ns0 エッジ(ベクトル化 join、チェックポイント)
│   ├── stats.py           # 全グラフ次数統計・ハブ検出(チャンク bincount)
│   ├── subsets.py         # ID窓 / BFS(次数キャップ+メガハブ除外)サブセット抽出
│   ├── analysis.py        # Leiden(igraph/leidenalg)、コミュニティ指標、ハブ解析、L2 階層化
│   ├── pipeline.py        # 1 サブセットのエンドツーエンド解析(resolution スイープ→一次選択→レポート)
│   ├── report.py          # プロット(matplotlib)+ Markdown レポート
│   └── cli.py             # サブコマンド群
├── scripts/               # 補助スクリプト(show_stats / summarize_runs / make_notebook 等)
├── tests/test_synthetic.py# 合成データによる全経路自己テスト(ネットワーク不要)
├── notebooks/poc_colab.ipynb
└── results/               # 共有用レポート(SUMMARY.md と検証 run の report/metrics/plots)
```

### データ生成物(`--base data` 既定、工程別に分離 — 方針 2026-09-25)

```
data/
├── dump/        # 元の Wikipedia ダンプ(.sql.gz)
├── parsed/      # ダンプ→中間データ: articles.parquet, article_ids.npy,
│                #   ns0_all_hashes.npz, lt_hash_ns0.npy, rd_map.npz, meta.json
├── graph/       # 全 ns0 グラフ: edges_ns0.bin(1.14GB), edges_checkpoint.json,
│   │            #   indeg.npy, outdeg.npy, full_stats.json
│   └── subsets/ # サブセット別: nodes.parquet, edges_internal.npy, node_stats.npz, subset_meta.json
└── community/   # Leiden・階層コミュニティ出力(analyze の既定 out-dir)
                 #   run 別: metrics.json, communities.parquet, report.md, *.png

# 将来の予約名(必要になった時点で追加、空ディレクトリは作らない):
# data/layout/   # グローバル・ローカル 3D 座標
# data/spatial/  # Morton/Octree・空間チャンク・LOD
# data/final/    # Web 配信用の最終データ
```

パスの追加・変更時は必ず `wu/paths.py` の `Dirs` に集約すること(ハードコード禁止)。
共有したいレポート類は `results/` へ明示的に `--out-dir results/<name>` で出力する運用。

## 使い方

```bash
pip install numpy scipy pandas pyarrow matplotlib igraph leidenalg

BASE=data   # 作業ディレクトリ(dump ~1.2GB + edges ~1.2GB が生成される)

python3 -m wu.cli --base $BASE download --files page,redirect,linktarget,pagelinks  # ~1.13GB
python3 -m wu.cli --base $BASE parse --dump-date 2026-09-02
python3 -m wu.cli --base $BASE edges          # ~4分 (2vCPU) → edges_ns0.bin (1.14GB)
python3 -m wu.cli --base $BASE stats          # 全グラフ次数・ハブ統計

# サブセット(例: 地理系 BFS 10万ノード)
python3 -m wu.cli --base $BASE subset-bfs \
  --seeds "東京都,大阪府,京都府,北海道,福岡県,愛知県,宮城県,広島県,新潟県,長野県,日本の地理,市町村" \
  --max-nodes 100000 --cap 60 --hops 4 --max-in-degree 10000 --name bfs_geo_100k

# 解析(Leiden スイープ → 指標 → ハブ → L2 階層 → レポート)
# 既定出力先: data/community/<subset名>。共有用は results/ へ明示出力。
python3 -m wu.cli --base $BASE analyze --subset bfs_geo_100k \
  --out-dir results/bfs_geo_100k_raw --resolutions 0.5,1.0,2.0 --engine igraph

# レイアウト用グラフ実験
#   smart 次数予算剪定(推奨): --degree-budget 40 --degree-budget-mode smart
#   従来型(破砕するので非推奨): --degree-budget 40 --degree-budget-mode both
#   ハブ重み: --hub-weight log|deg1|deg0.5
```

## 全グラフ Leiden(全 1.45M ノードのコミュニティ分割 → 銀河/銀河団プロトタイプ)

`scripts/run_full_leiden.py`(工程別サブコマンド制、各段階がディスクに保存され再開可能):

```bash
# 0) スモーク(数分): 先頭 500 万エッジで動作確認 → 本番前に membership_res*.npy を削除するか --force
python scripts/run_full_leiden.py --base data dedup
python scripts/run_full_leiden.py --base data detect --resolutions 1.0 --max-edges 5000000 --force

# 1) 無向一意化: 142.4M 有向 → ~100-110M 無向一意(~4-6分、RAM ピーク ~4-5GB)
python scripts/run_full_leiden.py --base data dedup

# 2) 検出: igraph ネイティブ Leiden(グラフ構築 ~6-10分 + res ごと ~10-45分、RSS ~7-9GB)
#    複数 resolution は 1 回の起動でまとめて実行(グラフ構築を共有)
python scripts/run_full_leiden.py --base data detect --resolutions 0.5,1.0,2.0

# 3) 指標(チャンク方式、~3-6分)→ 4) L2 銀河団クラスタリング(1分)→ 5) エクスポート
#    ★ フルスケールでは modularity の解像度限界(√2m ≈ 1.5万ノード未満を分解不能)と
#      ハブ支配により、一括 Leiden は「数十個のマクロコミュニティ + 孤立ノード」になる。
#      → subdivide(マクロコミュニティ内の誘導部分グラフで Leiden を再帰実行)で
#        銀河サイズ(数百〜数万ノード)まで分割するのが本流(2 段階方式)。
python scripts/run_full_leiden.py --base data subdivide --resolution 1.0 --max-galaxy 10000
python scripts/run_full_leiden.py --base data metrics --tag res1_sub
python scripts/run_full_leiden.py --base data cluster --tag res1_sub
python scripts/run_full_leiden.py --base data export  --tag res1_sub
# (一括分割のまま評価したい場合: metrics/cluster/export --resolution 1.0)

# レイアウト用グラフ(次数予算剪定)の構築と交差評価
python scripts/run_full_leiden.py --base data prune --budget 40 --mode smart   # ~60-70% 辺保持、RAM ~4-5GB
#   既存の分割を剪定グラフ上で評価(分割はそのまま、評価グラフだけ差し替え):
python scripts/run_full_leiden.py --base data metrics --tag res1_sub --edges edges_pruned_smart40.bin --label res1_sub_B40
#   剪定グラフで分割そのものを作り直す場合:
python scripts/run_full_leiden.py --base data detect --resolutions 1.0 --edges edges_pruned_smart40.bin --suffix _B40 --force
python scripts/run_full_leiden.py --base data subdivide --tag res1_B40 --edges edges_pruned_smart40.bin --max-galaxy 10000
python scripts/run_full_leiden.py --base data metrics --tag res1_B40_sub --edges edges_pruned_smart40.bin
python scripts/run_full_leiden.py --base data cluster --tag res1_B40_sub
```

出力は `data/community/full/`:
`membership_<tag>.npy`(ノード→コミュニティ、compact idx 順)/ `detect_meta.json` /
`subdivide_meta_<tag>.json`(マクロコミュニティ別の分割診断)/
`metrics_<tag>.json`(全体指標)/ `per_community_<tag>.parquet`(コミュニティ別指標+代表記事)/
`top_pairs_<tag>.json`(コミュニティ間エッジ top50)/ `pairs_<tag>.npz` /
`clusters_comm_<tag>.npy`・`clusters_node_<tag>.npy`・`clusters_meta_<tag>.json`(L2=銀河団)/
`membership_<tag>.parquet`(page_id+title+comm+cluster の結合表)。
tag は `res<R>`(一括)または `res<R>_sub`(2 段階)。

期待値の目安(実行前に共有しておくべき想定):
- 無向一意エッジ ~100–110M(相互リンク率 ~23–30%、正確な値は `graph/dedup_meta.json`)
- res=1.0 で コミュニティ数 ~1,000–5,000、modularity ~0.6–0.8、最大シェア <5% が望ましい
- `cluster` の出力がそのまま **Universe → Galaxy Cluster(L2) → Galaxy(L1) → Article** の §4.2 階層

## 本文リンクグラフ(E3: テンプレート由来リンクの除去)

pagelinks 表はトランスクルード(ナビボックス等)由来リンクを含む(平均次数 94 の主因)。
記事自身の wikitext 内の [[リンク]] のみから「真の記事グラフ」を構築する:

```bash
python -m wu.cli --base data download --files pages-articles   # 4.7GB(resume 対応)
python -m wu.cli --base data body-edges                        # 15-40分、checkpoint resume 対応
#   派生実験: --strip-refs(引用内リンクも除去)/ --limit-pages N(スモーク)
#   再実行したい場合は graph/body_checkpoint.json を削除

python scripts/run_full_leiden.py --base data dedup --edges-in edges_body_directed.bin \
    --out-name edges_body_undirected.bin --meta-name dedup_body_meta.json
python scripts/run_full_leiden.py --base data detect --resolutions 1.0 \
    --edges edges_body_undirected.bin --suffix _body --force
python scripts/run_full_leiden.py --base data subdivide --tag res1_body \
    --edges edges_body_undirected.bin --max-galaxy 10000
python scripts/run_full_leiden.py --base data metrics --tag res1_body_sub --edges edges_body_undirected.bin
python scripts/run_full_leiden.py --base data cluster --tag res1_body_sub
python scripts/run_full_leiden.py --base data export  --tag res1_body_sub

# pagelinks 次数 vs 本文次数の対比較(テンプレート膨張率の定量)
python scripts/compare_body_vs_pagelinks.py --base data
#   部分 XML で検証した場合は --max-pid 114794 を付ける
```

## カテゴリ整合性(E4: 銀河は主題と一致しているか)

リンク由来のコミュニティ(銀河)がカテゴリ=主題とどれだけ一致するかを定量する。
純度(top1_share)が高いほど「銀河の名前」をカテゴリから自動付与できる。

```bash
python -m wu.cli --base data download --files categorylinks   # 176MB
python -m wu.cli --base data categories                       # ~2-5分
#   → graph/categories.parquet + graph/article_categories.bin

python scripts/community_purity.py --base data --tag res1_sub        # 生グラフ銀河の純度
python scripts/community_purity.py --base data --tag res1_body_sub   # 本文グラフ銀河の純度
#   → community/full/purity_<tag>.parquet / purity_<tag>.json + コンソール表
#   v2: --max-cat-freq 20000 でメタカテゴリ(すべてのスタブ記事/存命人物/
#       ウィキデータ座標 等)を命名から除外し、tf-idf で name 列を生成
```

## 銀河カタログ(data/final/ = レイアウト・ビューアのデータ契約)

既存成果物(membership/pairs/per_community/purity)から、表示とレイアウトが使う
台帳を一括生成する(D16 方針の成果物):

```bash
python scripts/build_galaxy_catalog.py --base data --galaxy-tag res1_sub --macro-tag res1
#   → data/final/galaxies.parquet        銀河台帳(macro_id, n_articles, e_in/out, out_ratio,
#                                         is_dust, name(カテゴリ), rep_titles, top_neighbors,
#                                         n_neighbors, cluster_l2)
#   → data/final/macros.parquet          マクロ台帳(63 行 + 統計 + top_galaxies)
#   → data/final/galaxy_pairs_topK.parquet  中景用バンドル(重み top-K)
#   → data/final/macro_pairs.parquet     遠景用「数本」(マクロ間集約重み)
#   → data/final/catalog_meta.json       件数・包含チェック(containment_violations)
```

## 指標定義(評価レポート共通)

コミュニティ c について(エッジはサブセット内部の無向一意リンク):

```
N_c       : ノード数
E_in_c    : 内部エッジ数
E_out_c   : c と他コミュニティを跨ぐエッジ数(c 側からカウント)
out_ratio_c = E_out_c / (E_in_c + E_out_c)
conductance_c = E_out_c / (2·E_in_c + E_out_c)   (= cut/vol)
avg_degree_c  = 2·E_in_c / N_c
boundary_nodes_c      : 社区間エッジを 1 本以上持つノード数
boundary_node_ratio_c : boundary_nodes_c / N_c
E_ext_out_c / E_ext_in_c : サブセット「外」への/外からの有向リンク数(リーク、別建て報告)
```

全体: コミュニティ数、最大シェア、top5 シア、cross_edge_fraction(=社区間エッジ/内部エッジ)、
out_ratio・conductance・サイズの分布、コミュニティ間エッジ top20 ペア、L2 階層(コミュニティ間
重み付きグラフへさらに Leiden)の同一指標。

## スケーリングメモ(実測に基づく)

| 規模 | エンジン | RAM 実測 | 備考 |
|---|---|---|---|
| 30k / 1.2M エッジ | igraph native | ~350MB | 1GB サンドボックスでフルパイプライン可 |
| 100k / 5.1M エッジ | igraph native | **523MB** | leidenalg は >912MB で OOM |
| 1.45M / 142M エッジ(全グラフ) | igraph native | **~10GB 想定**(58B/edge) | ローカル 32GB で実行可能見込み。Colab(13GB)はギリギリ → 2 段階分割を推奨 |

全グラフ 2 段階案: ① smart-B 剪定グラフで粗い Leiden(res 低め)→ 銀河候補 ② 各銀河を独立バッチで精密 Leiden + ローカルレイアウト(§12 の Colab Job 001…分割)。境界情報は「隣接コミュニティ ID + 集約重み + アンカー座標」のみを渡す。

## 既知の注意点

- pagelinks は**テンプレート展開後**のリンクを含む(平均次数 94 の主因)。真の「本文リンクのみ」が必要なら XML ダンプ(4.7GB)の wikitext 解析が必要(未実装・次フェーズ候補)。
- 赤リンク 15.2M は保存層でも現時点で破棄(ダンプに実体がないため)。将来「赤リンク=未誕生の星」として復元するなら linktarget 表(7M ns0 タイトル)から可能。
- BFS サブセットの out-link 方向のみ(バックリンク BFS は未実装)。
- `idwin`(ID 窓)サブセットは構造評価に使用禁止(リーク 95%、対照実験用)。
