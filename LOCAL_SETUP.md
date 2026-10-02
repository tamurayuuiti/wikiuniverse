# ローカル環境セットアップと運用手順

対象: jawiki グラフ分割・可視化パイプライン(宇宙・銀河プロジェクト)。
概要とコマンドの全体像は README.md、結果の正典は results/SUMMARY.md。

## 0. なぜローカル実行か

チャット側サンドボックスは **RAM 1GB / 2 vCPU** 相当の制約があり、役割を分ける:

| 処理 | 必要 RAM(実測ベース) | サンドボックス | ローカル 32GB |
|---|---|---|---|
| サブセット抽出・解析(〜100k) | ~600MB | ✅ | ✅ |
| 小規模レイアウト・テスト・レポート分析 | <1GB | ✅ | ✅ |
| **全 1.45M グラフ一括 Leiden** | **~12–16GB**(igraph ~58B/edge) | ❌ | ✅ |
| 全グラフ 2 段階分割 + 銀河カタログ + 座標 + 出版 | 8–16GB | ❌ | ✅ |

**役割分担**: コード改良・小実験・レポート分析 = どちらでも可 / 大規模計算・本番データ生成 = ローカル。

## 1. リポジトリの内容とデータ配置

Git リポジトリ(または同期したワークスペース写し)を任意の場所に置く。**データ
(ダンプ・中間生成物)はリポジトリに含まれない** — §3 で取得する(SQL 4 種 ~1.13GB、
本文リンク用 XML 4.7GB、カテゴリ 176MB)。

```
wikiuniverse/
├── README.md               # 全体像・全工程コマンド・指標定義・コミット規約
├── LOCAL_SETUP.md          # 本書
├── PROVENANCE.json         # 基準ダンプ(2026-09-02 版)の SHA-256(完全性検証用)
├── requirements.txt        # Python 依存(検証バージョン準拠)
├── configs/                # 設定(追跡ファイル = 単一の真実源)
│   ├── canonical.json      #   正式パイプラインの正準パラメータ
│   └── publish.json        #   出版中の正典座標 run(ACTIVE_LAYOUT_RUN の真実源)
├── wu/                     # 本体パッケージ(純 Python)
│   ├── paths.py            #   ★ データ配置の単一の真実源(Dirs)
│   ├── pipeline/           #   パイプライン基盤(成果物契約/ステージ宣言/設定/ランナー)
│   ├── stages/             #   ステージ実体(import 順 = 正式チェーン順)
│   ├── dumpio.py           #   ダウンロード(resume/並列)・gz ストリーム
│   ├── sqlparse.py         #   page/linktarget/redirect 解析 → ハッシュ表(parsed/)
│   ├── xmlparse.py         #   pages-articles XML → 本文リンク抽出(body-edges)
│   ├── catparse.py         #   categorylinks → 記事×カテゴリ対(categories)
│   ├── buildedges.py       #   pagelinks → 解決済みエッジ(チェックポイント resume 可)
│   ├── stats.py            #   全グラフ次数統計・ハブ top
│   ├── publish.py          #   Viewer 公開面の生成(spatial/、SCHEMA_VERSION 付き)
│   ├── community/          #   コミュニティ検出+カタログ本体(fullgraph/analysis/purity/catalog)
│   ├── layout/             #   座標生成本体(layout_global/layout_local/parallel/recompose)
│   ├── audit/              #   読み取り専用監査 3 本(tiles/names/layout)
│   ├── experiments/        #   [実験] サブセット抽出・端到解析・ハブ抑制・レポート・
│   │                       #   本文次数比較ツール(canonical チェーン外)
│   └── cli.py              #   python -m wu <sub>(stages/plan/run + subset-id/subset-bfs/analyze)
├── tests/                  # スクリプト式テスト 10 本 + fixture.py(共有合成データ)
├── viewer/                 # React+TS+three ビューア(v6)
├── notebooks/poc_colab.ipynb  # Colab 実行版 PoC(閲覧用)
└── results/SUMMARY.md      # ★ 評価レポート(最新状態・テーマ別)
```

### ローカルで生成されるデータ(`--base data` 既定、工程別分離、すべて Git 追跡対象外)

```
data/
├── dump/        # [原始] Wikipedia ダンプ(SQL 5 ファイル + pages-articles XML。再取得可)
├── parsed/      # [中間] articles.parquet / article_ids.npy / hash 表 / rd_map.npz / meta.json
├── graph/       # [中間] edges_ns0.bin / edges_undirected_unique.bin / edges_body_* /
│   └── subsets/ #   次数・統計・catlinks 系 / サブセット切り身
├── community/   # [成果] Leiden 出力(full/ = フルグラフ run。tag 規約は下記)
├── final/       # [成果=契約] 銀河/マクロ台帳(galaxies / macros / pairs / catalog_meta)
├── layout/      # [成果] 座標 run 別ディレクトリ(命名 <YYYYMMDD>_<slug>、§6)
│   └── <run>/   #   macro/galaxy/article_positions + layout_meta + preview + shards(キャッシュ)
└── spatial/     # [成果=配信] ビューア公開面(bootstrap.json + tiles/)。URL は run 名と無縁
```

- パスを触る改修は `wu/paths.py` の `Dirs` クラスと成果物レジストリ
  (`wu/pipeline/artifacts.py`)に集約する(ハードコード禁止の運用)。
  正典 run 名は `configs/publish.json`。ファイル別の生成者/消費者/削除可否の完全版は
  paths.py の docstring が正典。
- community の tag 規約: `res<R>`=マクロ一括 / `res<R>_sub`=2 段階(銀河)/
  `_body`=本文リンクグラフ / `_B40` 等=剪定グラフ系。
- `data/` の中間生成物(parsed/graph/shards/チェックポイント)は上流から再生成できる。
  成果物(community/final/layout の positions/spatial)は原則保持。

## 2. 環境要件

- **Python 3.10–3.13**(検証: 3.11.2 / 3.13.11)
- 依存: `pip install -r requirements.txt`(ピン留め — バージョンは同ファイルが正)
- **ディスク**: フルパイプラインで ~15GB 空き(dump 1.13GB + XML 4.7GB + edges 1.14GB +
  本文 edges 0.6GB + parsed/community/final/layout/spatial)
- **RAM**: 16GB でサブセット実験まで / **32GB 推奨**(全グラフ Leiden 用、実測 RSS 7–9GB)
- **OS**: Windows/macOS/Linux いずれも可。コードはクロスプラットフォーム(メモリ返却最適化
  `malloc_trim` は Linux 専用だが不在時は自動無効化)。Windows でメモリが気になる場合は WSL2 推奨。
- ビューアは Node.js(npm)+ 静的配信用の http サーバ(Python 同梱のもので可)。

### 動作確認(ネット不要・数分)

テストはスクリプト式(pytest ではない)。6 本を順に実行し、それぞれ末尾の PASS 表示を確認する:

```bash
python tests/test_synthetic.py    # パイプライン E2E(合成ダンプ)
python tests/test_paths.py        # Dirs / run 命名規約
python tests/test_bodylinks.py    # 本文リンク抽出(合成 XML)
python tests/test_catlinks.py     # カテゴリ解析(合成 SQL)
python tests/test_subdivide.py    # 2 段階分割の回帰(構造アサート)
python tests/test_catalog.py      # カタログ→レイアウト→タイル出版の E2E(合成)
```

`tests/synth_data/` は実行時に再生成される作業用フィクスチャ(削除自由)。

## 3. データセットの取得

ダンプは大容量のため転送ではなく **Wikimedia 公式から直接取得する**(PROVENANCE.json と
同一ファイル)。家庭回線なら SQL 4 種で数分の規模。

### 方法 A: 同梱ダウンローダ(resume・並列対応、推奨)

```bash
cd wikiuniverse
python -m wu --base data run download   # 既定4種(追加: --set download.files=…)
# → data/dump/ に 4 ファイル(~1.13GB)。中断しても再実行で続きから取得。
```

### 方法 B: 手動(ブラウザ/wget/curl)

`https://dumps.wikimedia.org/jawiki/latest/` から次の 4 ファイルを `data/dump/` へ:

| ファイル | サイズ(2026-09-02 版) |
|---|---|
| jawiki-latest-page.sql.gz | 171,061,922 B |
| jawiki-latest-redirect.sql.gz | 14,125,619 B |
| jawiki-latest-linktarget.sql.gz | 140,849,240 B |
| jawiki-latest-pagelinks.sql.gz | 800,089,153 B |

### 追加ファイル(本文リンク・カテゴリ工程用)

| ファイル | サイズ | 使う工程 |
|---|---|---|
| jawiki-latest-pages-articles.xml.bz2 | ~4.7GB | `body-edges`(本文リンクグラフ) |
| jawiki-latest-categorylinks.sql.gz | ~176MB | `categories`(カテゴリ純度・銀河命名) |

いずれも `download --files pages-articles` / `--files categorylinks` で取得できる。

### 完全性検証(任意だが推奨)

`PROVENANCE.json` に基準ダンプ(SQL 4 ファイル)の SHA-256 を記録してある。

```bash
sha256sum data/dump/*.gz   # Windows: certutil -hashfile <file> SHA256
```

- **一致** → §4 の検証値が完全一致するはず。
- **不一致(latest が更新されていた場合)** → それでも問題ない。`parse` の `--dump-date` を
  新しい日付にし、§4 の検証値が数%程度ずれることを許容する(構造結論は変わらない)。

## 4. グラフ構築と検証(初回実行)

```bash
cd wikiuniverse

# (1) ページ/リダイレクト/linktarget 解析 → data/parsed/(合計 ~1分)
python -m wu --base data run parse --set parse.dump_date=2026-09-02

# (2) pagelinks 全行ストリーム解析 → data/graph/edges_ns0.bin(~2-5分)
python -m wu --base data run edges

# (3) 全グラフ統計(~1分)
python -m wu --base data run stats
```

### 検証値(2026-09-02 版ダンプなら完全一致するはず)

| 項目 | 期待値 |
|---|---|
| 記事ノード(ns0 非リダイレクト) | **1,516,326** |
| ns0 リダイレクト | 953,958(dangling 199) |
| pagelinks 総行数 | 204,654,087 |
| 赤リンク除外 | 15,209,916 |
| セルフループ除去 | 28,087 |
| **解決済み ns0→ns0 有向エッジ(kept)** | **142,446,159** |
| `data/graph/edges_ns0.bin` サイズ | 1,139,569,272 B |
| 平均次数 / 最大次数 | 93.9 / 421,728(日本) |

`edges` は **チェックポイント付き**(`data/graph/edges_checkpoint.json`)なので、中断したら
同じコマンドを再実行すれば続きから進む。

## 5. サブセット検証の再現(小規模・任意)

README「使い方(サブセット解析)」の通り `subset-bfs` → `analyze`。代表例(地理系 100k):

```bash
python -m wu --base data subset-bfs \
  --seeds "東京都,大阪府,京都府,北海道,福岡県,愛知県,宮城県,広島県,新潟県,長野県,日本の地理,市町村" \
  --max-nodes 100000 --cap 60 --hops 4 --max-in-degree 10000 --name bfs_geo_100k

python -m wu --base data analyze --subset bfs_geo_100k \
  --resolutions 0.5,1.0,2.0 --engine igraph
# 出力先: data/community/bfs_geo_100k/(--out-dir で変更可。場所はどこでもよい)
```

期待される概略値: `res=0.5: C=11 largest≈0.30 crossF≈0.24 mod≈0.65`。
Leiden には乱択性があるため **membership の完全一致は保証されない**(seed=42 固定・
同一バージョンなら通常一致するが、igraph のバージョン差で変わり得る)。指標が
±数% 以内に収まれば再現成功とみなす。

## 6. フルパイプライン → 座標出版(run 運用)

§4 のグラフ構築が済んだら、以下が本番データの一本道。**正準エントリは
`python -m wu run`**(ステージレジストリ + ランナー。実行計画の dry-run は
`python -m wu plan …`、契約一覧は `python -m wu stages`、実行のたびに
`data/manifests/` へ台帳が記録される)。実行経路はこれに一本化されている
(バッチ分割・preview 等の細粒度操作だけモジュール CLI `python -m wu.layout.layout_local`)。

```bash
# 1) コミュニティ検出(マクロ → 銀河の 2 段階)+ 純度 + 銀河カタログ
python -m wu run dedup detect subdivide metrics cluster export purity catalog --base data
#   tag 等の正準値はステージ定義の既定(res1 → res1_sub・max_galaxy 10000・
#   purity v2・catalog topK)。変える場合は --set subdivide.max_galaxy=… 等で

# 2) (任意) 本文リンク・剪定腕: python -m wu run body_edges / prune --set …

# 3) 銀河団ラベルのキュレーション: 正典コピー(リポジトリ追跡下)は
#    results/macro_label_overrides.json。data/final/ へコピーすると出版時に
#    適用される。編集し直す場合はテンプレ出力 → label 編集 → data/final/ に保存 →
#    変更を正典コピーにも反映(両方まとめてコミット)
cp results/macro_label_overrides.json data/final/macro_label_overrides.json
python -m wu run audit_names --base data --set audit_names.dump_macro_labels=true

# 4) 座標 run の生成(マクロ+銀河 → 銀河内記事 → 出版)を新 run 名で
python -m wu run layout_global layout_local publish --base data --run <RUN>
#   layout_local は並列ランチャ経由(事前計算 local_prep は初回のみ数分・全 run 共用、
#   LPT 分割の並列ジョブ → 走査なしマージ)。フルランの目安 ~5分(jobs=CPU 数)

# 5) 配信とビューア
python -m http.server 8000        # リポジトリルート(別ターミナル)
cd viewer && npm install && npm run dev    # http://localhost:5173
```

### run の命名と採用

- run 名 = `data/layout/` 直下のディレクトリ名。**規約: `<YYYYMMDD>_<slug>`**
  (ASCII 小文字。日付は生成日、slug は「その run が何か」= 役割・主要パラメータ)。
  正確なパラメータと品質指標は各 run の `layout_meta.json` が正典。
- **正典ポインタ**: `configs/publish.json` の `active_layout_run`(wu/paths.py の
  `ACTIVE_LAYOUT_RUN` がここから読む)。全レイアウト系コマンド
  (layout_global / layout_local / recompose / publish=export)の `--run`
  既定値であり、「いま公開中の座標」を指す。
- 新しい座標 run を**採用**する手順: 4) を新 run 名で実行 →
  `configs/publish.json` を新 run 名に更新してコミット → `python -m wu run publish`
  で再出版(ビューアは常に spatial/ を読むので URL は不変)。
  比較実験は run を並べるだけでよい(上書き事故が起きない)。
- 既存 run の記事座標を再利用する場合は 4) の layout_local の代わりに
  `python -m wu run recompose --set recompose.from_run=<旧RUN> --set recompose.to_run=<新RUN>`
  (`--set recompose.from_run=<旧RUN> --set recompose.to_run=<新RUN>`)。
- `article_shards/` と `layout_local_checkpoint.json` はマージ完了後削除してよい(キャッシュ)。
- 退役した旧 run は `data/layout/` の外(リポジトリ直下 `archive/` など、Git 管理外の
  保管場所)へ移動すればよい。

## 7. トラブルシュート

| 症状 | 対処 |
|---|---|
| **生成した .md/.json が文字化け** | コードは UTF-8 明示書き込み。それでも化ける場合は環境変数 `PYTHONUTF8=1` を設定して再生成する(Windows 既定 cp932 への保険)。既存ファイルは VS Code 右下の encoding →「Reopen with Encoding」→ Shift JIS →「Save with Encoding」→ UTF-8 で変換可 |
| download が途中で切れる | 同じコマンド再実行(Range resume 対応) |
| `edges` がメモリ不足 | 元々 ~400MB ピーク設計。他アプリ終了。それでもダメなら `--chunk-bytes 8388608` |
| analyze が OOM(巨大サブセット) | `--engine igraph` を使う(leidenalg は 2 倍消費)/ `--degree-budget 40 --degree-budget-mode smart` でレイアウト用グラフ化 |
| pandas/pyarrow バージョン差エラー | `pip install -U pandas pyarrow`(pandas 2.x でも概ね動作、検証は 3.0.6) |
| Windows で RSS が高止まり | `malloc_trim` 不在のため。WSL2 なら Linux と同挙動 |
| 数値が検証値と微妙に違う | ダンプが更新されている可能性(§3 の SHA-256 確認) |
| ビューアがデータを取得できない | リポジトリルートで `python -m http.server 8000` が起動しているか(vite proxy の転送先)。file:// 直オープンは CORS で不可 |
| 星の hover 名が `local#NN` になる | 記事が無いのではなく**タイトルが引けていない**状態。①`python -m wu run audit_tiles --base data` でサイドカー(`spatial/tiles/gal_*.json`)の欠落・不足を計測(欠落があれば `python -m wu run publish` を再実行)②データ側が 0 件ならビューア側のタイル LRU 追い出しが原因(浮上中銀河は `pinTiles` で保護・`Entry.titles` 参照で hover 名をキャッシュ状態から独立させ済み。DevTools の `[tile N] サイドカー…` 警告で判別) |
| 銀河名が「〜のスタブ」等のまま | 保守サフィックスは語幹正規化済み(`wu/community/catalog.py` の `_stem_name`)。`python -m wu run audit_names --base data` で再計測し、残存パターンがあれば `MAINT_SUFFIX_RE` か `NAME_BLACKLIST` に追加 → `python -m wu run catalog publish` 再実行(再 Leiden/再レイアウト不要) |
| 銀河団ラベルが「ISBN」「地理座標系」等ハブ記事名になる | 既定導出(rep_titles 先頭 16 文字)の限界。リポジトリ同梱のキュレーション表(正典)`results/macro_label_overrides.json` を `data/final/` へコピー → `python -m wu run publish` 再実行(§6 の 3b)。編集し直す場合: `python -m wu run audit_names --base data --set audit_names.dump_macro_labels=true` → `final/macro_label_overrides.template.json` の label を編集 → `final/macro_label_overrides.json` として保存(正典コピーへの反映も忘れずに) |
| 銀河団が平坦(円盤状)に見える / `layout_meta.json` の `galaxy_spill_count > 0` | `python -m wu run audit_layout --base data --run <RUN>` で計測する(読み取り専用・再計算なし): ①spill 一覧(超過順、r/Rm・マクロ規模・銀河名などの文脈列付き)②マクロ毎の平坦度 + 重心配置率(medium + 孤立銀河の成員比)③両者の相関(円盤化 = 重心配置銀河の境界付近への集中という仮説の定量検証)。`layout_meta.json` の quality 数値と突合し、不一致は警告する |
