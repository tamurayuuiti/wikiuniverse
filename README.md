# wikiuniverse — jawiki 全記事グラフ「宇宙/銀河団/銀河/記事」可視化ツールキット

**検証課題**: 「実際の jawiki グラフは、内部結合が強く・外部結合が弱く・計算的に独立させやすい単位に分割できるか?」

検証結果と数値の正典 → [`results/SUMMARY.md`](results/SUMMARY.md)(判定: **B 条件付きで有望**、確定条件は同 §9)。
環境構築・データ取得・パイプライン実行・座標出版の手順 → [`LOCAL_SETUP.md`](LOCAL_SETUP.md)。

## 設計原則

1. **3 層分離**: 保存層(全ノード・全エッジ)/ レイアウト層(smart 剪定・ハブ抑制した近似グラフ)/ 描画層(LOD・間引き)
2. **低 RAM ストリーミング**: 巨大 dict / NetworkX 全読み込みを避け、
   - タイトル結合は blake2b64 ハッシュ + ソート済み配列 `searchsorted`
   - エッジは int32 ペアの生バイナリ(`edges_ns0.bin`)+ memmap
   - SQL ダンプはチャンク逐次解析(チェックポイント resume 付き)
   - igraph は **バッチ add_edges + malloc_trim**、Leiden は **igraph ネイティブ engine**(leidenalg の 2 倍メモリ効率)
3. **再現性**: dump 日付・シード(42)・パラメータ・実行時間をすべて `meta.json` / `metrics.json` に記録

## リポジトリ構成(Git 追跡対象)

```
wikiuniverse/
├── README.md              # 本書(概要・全工程のコマンド・指標定義)
├── LOCAL_SETUP.md         # 環境構築・データ取得・フルパイプライン・座標 run 運用
├── PROVENANCE.json        # 基準ダンプ(2026-09-02 版)の SHA-256
├── requirements.txt       # Python 依存(検証バージョン準拠)
├── wu/                    # 本体パッケージ
│   ├── paths.py           # ★ データ配置の単一の真実源(Dirs + ACTIVE_LAYOUT_RUN。
│   │                      #   docstring に data/ ツリー全体・4 分類・run/tag 命名規約)
│   ├── dumpio.py          # ダウンロード(resume/並列)、gz ストリーム、json I/O
│   ├── sqlparse.py        # page/linktarget/redirect のストリーミング解析 → parsed/
│   ├── xmlparse.py        # pages-articles XML → 本文 [[リンク]] 抽出(body-edges)
│   ├── catparse.py        # categorylinks → 記事×カテゴリ対(categories)
│   ├── buildedges.py      # pagelinks → 解決済み ns0 エッジ(ベクトル化 join、チェックポイント)
│   ├── stats.py           # 全グラフ次数統計・ハブ検出(チャンク bincount)
│   ├── subsets.py         # ID窓 / BFS(次数キャップ+メガハブ除外)サブセット抽出
│   ├── analysis.py        # Leiden(igraph/leidenalg)、コミュニティ指標、ハブ解析、L2 階層化
│   ├── pipeline.py        # 1 サブセットのエンドツーエンド解析(resolution スイープ→一次選択→レポート)
│   ├── report.py          # プロット(matplotlib)+ Markdown レポート
│   └── cli.py             # サブコマンド群(python -m wu.cli …)
├── scripts/               # フルグラフ系スクリプト 11 本
│   ├── run_full_leiden.py         # dedup/detect/subdivide/metrics/cluster/export/prune
│   ├── layout_global.py           # ① マクロ+銀河の座標(3D パッキング/FR)
│   ├── layout_local.py            # ② 銀河内記事座標(アンカーバネ、銀河単位バッチ)
│   ├── run_local_parallel.py      # ② の並列実行+自動マージ
│   ├── recompose_articles.py      # ③ 既存 run の記事座標を新 run へ再構成
│   ├── build_galaxy_catalog.py    # data/final/ 銀河・マクロ台帳の生成
│   ├── export_viewer_tiles.py     # data/spatial/ への出版(bootstrap + tiles)
│   ├── community_purity.py        # カテゴリ純度(purity v2、tf-idf 命名)
│   ├── compare_body_vs_pagelinks.py  # 本文次数 vs pagelinks 次数の対比較
│   ├── audit_tiles.py             # [検査] data/spatial/ のタイル+サイドカー整合(読み取り専用)
│   └── audit_names.py             # [検査] 命名内訳 + 銀河団ラベルのテンプレ出力(読み取り専用)
├── tests/                 # 合成データ自己テスト 6 本(スクリプト式・ネットワーク不要。
│                          #   test_synthetic / test_paths / test_bodylinks / test_catlinks /
│                          #   test_subdivide / test_catalog。synth_data/ は実行時再生成)
├── viewer/                # React+TS+three ビューア(v6「連続宇宙」)
├── notebooks/poc_colab.ipynb  # Colab 実行版 PoC ノートブック(閲覧用)
└── results/
    ├── SUMMARY.md         # ★ 評価レポート(最新状態・テーマ別)
    └── macro_label_overrides.json  # 銀河団ラベルのキュレーション表(正典。data/final/ へコピーして使用)
```

### データ生成物(`--base data` 既定、Git 追跡対象外)

パイプラインの生成物はすべて `data/` 配下(工程別分離):

```
data/
├── dump/        # [原始] Wikipedia ダンプ(再取得可)
├── parsed/      # [中間] page/linktarget/redirect 解析 → articles.parquet, hash 表, rd_map
├── graph/       # [中間] edges_ns0.bin(有向 142.4M)/ edges_undirected_unique.bin(108.2M)/
│   │            #   edges_body_*(本文リンク)/ indeg・outdeg / catlinks 系 / 統計・チェックポイント
│   └── subsets/ # [中間] サブセット切り身(subset-bfs / subset-idwin)
├── community/   # [成果] Leiden 出力
│   └── full/    #   フルグラフ run: membership_<tag>.npy, pairs, metrics, purity, clusters(L2)
├── final/       # [成果=契約] 銀河/マクロ台帳(build_galaxy_catalog → galaxies/macros/pairs)
│                #   + macro_label_overrides.json(任意 = 銀河団ラベルの手動キュレーション。
│                #     追跡下の正典コピーは results/macro_label_overrides.json)
├── layout/      # [成果] 座標 run。1 run = 1 ディレクトリ(命名 <YYYYMMDD>_<slug>)
│   └── <run>/   #   macro/galaxy/article_positions.parquet + layout_meta + preview + shards
└── spatial/     # [成果=配信] ビューア公開面: bootstrap.json + tiles/(run 名と無縁の固定 URL)
```

**`wu/paths.py` がデータ配置の単一の真実源**(Dirs + `ACTIVE_LAYOUT_RUN` = 公開中の正典 run)。
スクリプトはパスを手組みせず `Dirs` を使う。ファイル別の分類(原始・中間・成果・キャッシュ)・
community の tag 規約・run の命名と採用手順は paths.py docstring と LOCAL_SETUP.md §1/§6 が正。
実験結果の要約は `results/SUMMARY.md` に集約する(run 別の個別レポートは作らない)。

## 使い方(サブセット解析)

```bash
pip install -r requirements.txt   # numpy scipy pandas pyarrow matplotlib igraph leidenalg

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
# 既定出力先: data/community/<subset名>(--out-dir で変更可)
python3 -m wu.cli --base $BASE analyze --subset bfs_geo_100k \
  --resolutions 0.5,1.0,2.0 --engine igraph

# レイアウト用グラフ実験
#   smart 次数予算剪定(推奨): --degree-budget 40 --degree-budget-mode smart
#   従来型(破砕するので非推奨): --degree-budget 40 --degree-budget-mode both
#   ハブ重み: --hub-weight log|deg1|deg0.5
```

## 全グラフ Leiden(全 1.45M ノードのコミュニティ分割 → 銀河/銀河団)

`scripts/run_full_leiden.py`(工程別サブコマンド制、各段階がディスクに保存され再開可能):

```bash
# 0) スモーク(数分): 先頭 500 万エッジで動作確認 → 本番前に membership_res*.npy を削除するか --force
python scripts/run_full_leiden.py --base data dedup
python scripts/run_full_leiden.py --base data detect --resolutions 1.0 --max-edges 5000000 --force

# 1) 無向一意化: 142.4M 有向 → 108.2M 無向一意(~4-6分、RAM ピーク ~4-5GB)
python scripts/run_full_leiden.py --base data dedup

# 2) 検出: igraph ネイティブ Leiden(グラフ構築 ~6-10分 + res ごと ~2-45分、RSS ~7-9GB)
#    複数 resolution は 1 回の起動でまとめて実行(グラフ構築を共有)
python scripts/run_full_leiden.py --base data detect --resolutions 0.5,1.0,2.0

# 3) 2 段階分割(本流): 一括 Leiden はモジュラリティ解像度限界(√2m ≈ 1.5万ノード未満を
#    分解不能)とハブ支配により「数十個のマクロコミュニティ + 孤立ノード」になる。
#    → subdivide(マクロコミュニティ内の誘導部分グラフで Leiden を再帰実行)で
#      銀河サイズ(数百〜1万ノード)まで分割する。
python scripts/run_full_leiden.py --base data subdivide --resolution 1.0 --max-galaxy 10000

# 4) 指標(チャンク方式、~3-6分)→ 5) L2 銀河団クラスタリング(~1分)→ 6) エクスポート
python scripts/run_full_leiden.py --base data metrics --tag res1_sub
python scripts/run_full_leiden.py --base data cluster --tag res1_sub
python scripts/run_full_leiden.py --base data export  --tag res1_sub
# (一括分割のまま評価したい場合: metrics/cluster/export --resolution 1.0)

# レイアウト用グラフ(次数予算剪定)の構築と交差評価
python scripts/run_full_leiden.py --base data prune --budget 40 --mode smart   # ~53% 辺保持、RAM ~4-5GB
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
tag は `res<R>`(マクロ一括)または `res<R>_sub`(2 段階=銀河)。

期待値の目安: 無向一意エッジ 108.2M(相互リンク率 ~24%)、res=1.0 一括で実効マクロ ~63 +
孤立シングルトン ~436、subdivide 後に銀河 ~1,535(中央値 ~279)。`cluster` の出力がそのまま
**Universe → Galaxy Cluster(L2) → Galaxy(L1) → Article** の表示階層になる。
実測値の正典は results/SUMMARY.md。

## 本文リンクグラフ(テンプレート由来リンクの除去)

pagelinks 表はトランスクルード(ナビボックス等)由来リンクを含む(平均次数 94 の主因)。
記事自身の wikitext 内の [[リンク]] のみから「真の記事グラフ」を構築する:

```bash
python -m wu.cli --base data download --files pages-articles   # 4.7GB(resume 対応)
python -m wu.cli --base data body-edges                        # ~15-25分、checkpoint resume 対応
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

結果(要約): 本文グラフはエッジ -42%・平均次数 46.9 だが、**銀河の out_ratio はむしろ悪化
(0.738→0.850)** — navbox は「銀河間高速道路」であると同時に「銀河内セメント」でもあった。
構造決定には生グラフ(pagelinks)を正史とし、本文グラフは意味的接続の描画・間引き判断に使う
(詳細と数値は results/SUMMARY.md §6)。

## カテゴリ整合性(銀河は主題と一致しているか)

リンク由来のコミュニティ(銀河)がカテゴリ=主題とどれだけ一致するかを定量する。
純度(top1_share)が高いほど「銀河の名前」をカテゴリから自動付与できる。

```bash
python -m wu.cli --base data download --files categorylinks   # 176MB
python -m wu.cli --base data categories                        # ~1-2分
#   → graph/categories.parquet + graph/article_categories.bin

python scripts/community_purity.py --base data --tag res1_sub        # 生グラフ銀河の純度
python scripts/community_purity.py --base data --tag res1_body_sub   # 本文グラフ銀河の純度
#   → community/full/purity_<tag>.parquet / purity_<tag>.json + コンソール表
#   --max-cat-freq 20000(既定)でメタカテゴリ(すべてのスタブ記事/存命人物/
#       ウィキデータ座標 等)を命名から除外し、tf-idf で name 列を生成(purity v2)
```

結果(要約): nameable_share ノード 37.8% / 実効コミュニティ 63.8%。年代・元号・学術識別子
などの「媒介銀河」は純度が低く out_ratio 0.92–0.97 = 銀河間物質(詳細は results/SUMMARY.md §7)。

## 銀河カタログ(data/final/ = レイアウト・ビューアのデータ契約)

既存成果物(membership/pairs/per_community/purity)から、表示とレイアウトが使う
台帳を一括生成する:

```bash
python scripts/build_galaxy_catalog.py --base data --galaxy-tag res1_sub --macro-tag res1
#   → data/final/galaxies.parquet        銀河台帳(macro_id, n_articles, e_in/out, out_ratio,
#                                         is_dust, name(カテゴリ名。スタブ等の保守サフィックスは
#                                         語幹正規化 → results/SUMMARY.md §7.1), rep_titles,
#                                         top_neighbors, n_neighbors, cluster_l2)
#   → data/final/macros.parquet          マクロ台帳(実効 63 行 + 統計 + top_galaxies)
#   → data/final/galaxy_pairs_topK.parquet  中景用バンドル(重み top-K)
#   → data/final/macro_pairs.parquet     遠景用「数本」(マクロ間集約重み)
#   → data/final/catalog_meta.json       件数・包含チェック(containment_violations)
#   銀河台帳の name はキュレーション済み: メタカテゴリのブラックリスト除外 →
#   tf-idf カテゴリ名(share>=0.15)→ 代表記事フォールバック。display_class 列で
#   galaxy / medium(年代・元号・汎用ハブ等の媒介銀河)/ dust(孤立)を分類
```

## グローバルレイアウト(宇宙の空間配置)

銀河カタログから階層レイアウトを生成する(上位コミュニティ配置 → 下位配置 → 座標合成):

```bash
python scripts/layout_global.py --base data --run <RUN> --pack 0.6 --seed 42
#   <RUN> = data/layout/<run>/ の名前(規約 <YYYYMMDD>_<slug>。省略時は wu/paths.py の
#   ACTIVE_LAYOUT_RUN)。run の採用手順は LOCAL_SETUP.md §6
#   正準 = 完全 3D(既定のまま): マクロ 3D 球パッキング + 銀河の 3D 球緩和/クランプ。
#   焼き込みレンズは無い — 地図/hybrid ビューは視聴時の z 圧縮(ビューアのスライダー)で
#   行う(D17)。実験腕: --macro-dim 2 / --macro-z-squash <1 / --macro-w-power >1
#   --r-spacing 1.5(銀河半径フロア r>=1.5*n^(1/3)、小銀河の記事詰まり防止)
#   → data/layout/<run>/galaxy_positions.parquet  (galaxy_id, macro_id, x,y,z, radius, display_class)
#   → data/layout/<run>/macro_positions.parquet   (マクロの中心と半径)
#   → data/layout/<run>/layout_meta.json / preview.png / preview_*.html
```

アルゴリズム(階層レイアウト v1.5、**正準方針 = 完全 3D**):
①マクロ配置(既定 `--macro-dim 3`)= 3D 球パッキング(macro_pairs 重み付き FR + 球分離緩和、
重なり ≤2%)。`--macro-w-power τ` は FR 引力の重み温度(w^τ。1.0=中立、>1 で強いリンクの
近接を強調)。**hybrid/地図ビューはレイアウトではなく視点パラメータ**: プレビュー/ビューアの
z-compress スライダー(1.0=純 3D ↔ 0.05=ほぼ地図)が視聴時に z を圧縮する(xy 不変なので
俯瞰は同一、レイアウト焼き直し不要)。
②マクロ内で銀河を配置: **マクロ内ペアを持つ(連結な)銀河**は重み付き FR(3D)、
**マクロ内ペアを1本も持たない銀河**(top-K リンクが全てマクロ外 = 外向きの銀河)は
リンク先の重み付き重心(他マクロの隣接=そのマクロ方向の境界点)へ配置し、ペア自体が
無い場合はフィボナッチ球スロットへ。その後、3D 球緩和 + 3D 放射クランプ(|中心|+r ≤ R_M)
でマクロ球に収める(完全一致点は決定的方向で分離される)。半径はマクロ R_M ∝ √記事数、
銀河 r_g = max(pack·R_M·√(n_g/n_M), r_spacing·n_g^(1/3))、体積キャップ (Σr³)^(1/3) ≤ 0.9·R_M。
③**媒介銀河(display_class=medium)も同じ重心規則**で配置し(「銀河間物質」が橋渡しの
相手に面した位置に来る = D16 の特別描画の座標側裏付け)、通常銀河からの押し出し緩和で
重なりを解く。
④dust は遠方シェル(フィボナッチ球面)。品質は `layout_meta.json` の `quality`:
`macro_native_overlap_frac`(配置次元でのマクロ重なり)、`macro_proj_overlap`
(top/side/ランダム視点射影の重なり率 = 「地図らしさ」の視点依存性)、
`galaxy_spill_frac`/`galaxy_spill_count`(3D 包含: 銀河球がマクロ球に収まっているか)、
`galaxy_overlap_frac`(マクロ内の銀河球の重なり = 緩和品質、体積重み)、
`galaxy_flat_mean`/`galaxy_flat_p90`(マクロ毎の銀河点群の PCA 異方性: ≈1=円盤状 /
≈2/3=等方的。円盤化の客観指標)、`macro_adj_recall_top5`・`macro_adj_spearman`
(マクロ配置の意味的近接の保持度: リンク強さ上位の隣接が空間的にも近いか /
重みと空間距離の順位相関、負=強いリンクほど近い)。比較実行は `--run <name>` で
別ディレクトリへ。preview.png(4 パネル: xy/xz/yz/深度カラー)+ **preview_macro*.html**
(three.js・OrbitControls・z-compress スライダー、データ埋め込み)。1,971 銀河 + 63 マクロなら ~2 秒。

## 銀河内部ローカルレイアウト(記事座標)

銀河ごとに**完全に独立したジョブ**(境界を跨ぐ依存がないためバッチ分割・並列・resume が可能):

```bash
python scripts/run_local_parallel.py --base data --jobs 8   # 全銀河(フルラン ~63分)→ 自動マージ
python scripts/layout_local.py --base data --run <RUN> --galaxies all
# バッチ分割例: --galaxies 0-499 / --galaxies 500-1499 (resume 対応、checkpoint 記録)
#   → data/layout/<run>/article_shards/gal_XXXXXX.npy (銀河毎・マージ後は削除可)
#   → 全銀河完了時に自動マージ: article_positions.parquet (page_id, galaxy_id, x,y,z)
#   → layout_local_meta.json (銀河毎所要時間 p50/p95 = バッチ外挿の根拠)
```

力モデル: 内部エッジ = igraph FR(3D)の形状 + **アンカーバネ**(銀河間リンクが多い記事ほど、
リンク先銀河方向のボール境界面へ。方向ベクトル集約なのでペア保存不要)+ 弾性 prior
(FR 形状の保持)+ ボール内クランプ。銀河中心・半径は対象 run(--run、既定は正典 run)の
galaxy_positions 由来。「銀河 = アンカー制約付きレイアウト単位」(results/SUMMARY.md §9 の
確定条件)の実装そのもの。

## データ出版とビューア

座標 run を公開面(`data/spatial/`)へ出版し、ビューアを起動する:

```bash
python scripts/export_viewer_tiles.py --base data    # --run 既定 = wu/paths.py の ACTIVE_LAYOUT_RUN
#   銀河団ラベル = rep_titles 先頭 16 文字。data/final/macro_label_overrides.json
#   (手動キュレーション・任意)があれば非空 label が優先される。キュレーション表の
#   正典コピーは results/macro_label_overrides.json(data/final/ へコピーして使う)。
#   テンプレ再生成(見直し用): python scripts/audit_names.py --base data --dump-macro-labels
python -m http.server 8000                           # リポジトリルートで起動(/data を配信)

cd viewer && npm install
# 別ターミナル(リポジトリルートで http.server 起動済みとして):
npm run dev        # http://localhost:5173 (vite proxy が /data を :8000 へ)
```

配信の型: 宇宙+銀河ビューは `bootstrap.json` の 1 fetch、記事データはズームインした銀河の
タイル(`spatial/tiles/gal_XXXXXX.bin` + `.json`)だけを on-demand fetch(「見える範囲だけ
読む」)。file:// は CORS で不可のため http サーバ必須。出版元 run は bootstrap.json の
`meta.layout_run` に記録される(URL 契約は run 名と無縁に固定)。

### ビューア v6「連続宇宙」(React+TS+Tailwind+three)

構成: `src/types/catalog.ts`(データ契約)/ `src/data/`(bootstrap+tileCache)/
`src/three/`(core+universeLayer+starField+focus+lod+curves+textures、React 非依存)/
`src/state/`(store+commands)/ `src/ui/`(Hud/InfoPanel/Tooltip)。
コメントは日本語・である調(ファイルヘッダーに責務/注意)、型 import は `import type`、
ディレクトリ跨ぎは `@/` エイリアス(実装内のコメントが仕様の実例)。

設計の骨格(離散 focus なし、単一連続空間+距離駆動 LOD):
- **連続性**: 段階切替/フェード/テレポートなし。クリックは全て fly-to(cubic ease、1.35s)、
  Esc は ego 解除→親マクロ俯瞰→home の連続上昇。
- **自己相似**: 全階層が同一 px 則 `screenPx = worldR·proj/dist`。塊スプライトは遠景で
  最小 px clamp により「一点(星)」へ収束し、子は emerge=9px から α 平滑で湧く
  (resolve=55px で完全出現)。宇宙:銀河団 = 銀河団:銀河 = 銀河:星。
- **エッジの階層対応**(距離帯のクロスフェード): 遠景=マクロバンドル(α=0.55·smoothstep(700,1700,D))
  → 中景=銀河バンドル(0.24·smoothstep(120,450,D)) → 近景=銀河内部エッジ(px 則)
  + **実クロスリンク**(浮上銀河の実記事間アーク。隣接タイルが揃った分だけ進歩的に実位置化、
  未着分は隣接銀河中心へのビーム) → 記事クリック=ego 網(実リンクのハイライト)。
- **星の描画**: 次数=光度・色温度(銀河色相基準+青白/暖色の混合)、shader 瞬き(uTime+aPhase)、
  px サイズ=親塊 px×0.071×次数係数(自己相似)。
- **ライティング**: 無光源+加算ブレンド+UnrealBloom(strength 0.62)+FogExp2+対数深度バッファ
  (5 桁スケール対応)。銀河ハブ星・銀河名ラベル(top42、距離 LOD α)・マクロ二層グロー。
- **カメラ**: OrbitControls 慣性(damping 0.075)、wheel=乗算ドリー(対数ズーム)、
  銀河クリック=r·5 へ、マクロクリック=r·2.4 へ、検索/ランダム=連続ジャンプ。
- **タイル契約**(export_viewer_tiles.py と固定): bin = header u32×3 + pos f32 n×3 +
  edges u32 ne×2 + cross u32 nx×3、全配列 rank 順。sidecar json = タイトル(rank 順)。
  deg はクライアント算出。z スライダはキャッシュタイルの z を in-place 再構成
  (gz=マクロ z アンカー、zBase=canonical コピー)。
  タイトル解決は sidecar json(正)→ 浮上エントリ保持の参照(タイルが LRU 追い出し
  されても hover 名を維持)→ `local#NN`(最後のフォールバック = データ欠落か版ズレの
  兆候。`scripts/audit_tiles.py` で計測)。点群は geometry が pos を参照保持するため
  タイル喪失後も描画され続ける → 浮上中銀河は `pinTiles` で追い出しから保護する。

## 指標定義(評価レポート共通)

コミュニティ c について(エッジは対象範囲内部の無向一意リンク):

```
N_c       : ノード数
E_in_c    : 内部エッジ数
E_out_c   : c と他コミュニティを跨ぐエッジ数(c 側からカウント)
out_ratio_c = E_out_c / (E_in_c + E_out_c)
conductance_c = E_out_c / (2·E_in_c + E_out_c)   (= cut/vol)
avg_degree_c  = 2·E_in_c / N_c
boundary_nodes_c      : コミュニティ間エッジを 1 本以上持つノード数
boundary_node_ratio_c : boundary_nodes_c / N_c
E_ext_out_c / E_ext_in_c : 対象範囲「外」への/外からの有向リンク数(リーク、別建て報告)
```

全体: コミュニティ数、最大シェア、top5 シア、cross_edge_fraction(=コミュニティ間エッジ/内部エッジ)、
out_ratio・conductance・サイズの分布、コミュニティ間エッジ top20 ペア、L2 階層(コミュニティ間
重み付きグラフへさらに Leiden)の同一指標。

## スケーリングメモ(実測に基づく)

| 規模 | エンジン | RAM 実測 | 備考 |
|---|---|---|---|
| 30k / 1.2M エッジ | igraph native | ~350MB | 1GB 環境でフルパイプライン可 |
| 100k / 5.1M エッジ | igraph native | **523MB** | leidenalg は >912MB で OOM |
| 1.45M / 142M エッジ(全グラフ) | igraph native | **~10GB**(58B/edge) | 32GB マシンで実行実績あり(RSS 7–9GB) |

フルグラフの本流は 2 段階分割: ① 一括 Leiden でマクロ(~63)② subdivide でマクロ内を
再帰分割して銀河(~1,535)③ 各銀河を独立バッチでローカルレイアウト。境界情報は
「隣接コミュニティ ID + 集約重み + アンカー座標」のみを渡す(銀河間リンク全量は渡さない)。

## コミット規約

```text
<type>: <変更内容の簡潔な要約>
```

- `feat:` ユーザー向けの新機能・機能追加 / `fix:` 不具合・誤動作・既存仕様の問題の修正 /
  `refactor:` 挙動を基本的に変えず構造・責務・可読性・保守性を改善 /
  `perf:` 処理速度・計算量・メモリ使用量の改善 /
  `chore:` バージョン・設定・構成などの保守作業 / `docs:` README などのドキュメント更新
- `type` は**コミット全体の主目的**で決定する(副次的な効果では変更しない)。
- 変更内容は**主要な変更・成果を一文で要約**し、細かな実装変更は列挙しない。
- 関連する変更はまとめ、**「何を」「どのように変更したか」**が分かる程度に具体的にする。
- 必要に応じてコンポーネント名・関数名・技術用語を使用する。
- 簡潔・客観的な日本語とし、既存のコミット履歴と同程度の粒度・文体を維持する。

## 既知の注意点

- pagelinks は**テンプレート展開後**のリンクを含む(平均次数 94 の主因)。本文リンクのみの
  グラフは `body-edges`(XML 4.7GB)で構築可能(上掲セクション)。両者の使い分けが
  確定条件(results/SUMMARY.md §9)。
- 赤リンク 15.2M は保存層で破棄(ダンプに実体がないため)。将来「赤リンク=未誕生の星」として復元するなら linktarget 表(7M ns0 タイトル)から可能。
- BFS サブセットの out-link 方向のみ(バックリンク BFS は未実装)。
- `idwin`(ID 窓)サブセットは構造評価に使用禁止(リーク 95%、対照実験用)。
