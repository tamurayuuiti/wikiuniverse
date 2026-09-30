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
├── wu/                     # 本体パッケージ(純 Python)
│   ├── paths.py            #   ★ データ配置の単一の真実源(Dirs + ACTIVE_LAYOUT_RUN)
│   ├── dumpio.py           #   ダウンロード(resume/並列)・gz ストリーム
│   ├── sqlparse.py         #   page/linktarget/redirect 解析 → ハッシュ表(parsed/)
│   ├── xmlparse.py         #   pages-articles XML → 本文リンク抽出(body-edges)
│   ├── catparse.py         #   categorylinks → 記事×カテゴリ対(categories)
│   ├── buildedges.py       #   pagelinks → 解決済みエッジ(チェックポイント resume 可)
│   ├── stats.py            #   全グラフ次数統計・ハブ top
│   ├── subsets.py          #   BFS / ID窓 サブセット抽出(メガハブ除外付き)
│   ├── analysis.py         #   Leiden(igraph/leidenalg)・指標・L2 階層化
│   ├── pipeline.py         #   エンドツーエンド解析(sweep→一次選択→hub 処理→レポート)
│   ├── report.py           #   プロット + Markdown レポート
│   └── cli.py              #   サブコマンド(python -m wu.cli ...)
├── scripts/                # フルグラフ系 9 本(run_full_leiden / layout_* / catalog / export 他)
├── tests/                  # 合成データ自己テスト 6 本(スクリプト式・synth_data/ は実行時再生成)
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

- パスを触る改修は `wu/paths.py` の `Dirs` クラスと `ACTIVE_LAYOUT_RUN` に集約する
  (ハードコード禁止の運用)。ファイル別の生成者/消費者/削除可否の完全版は
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
python -m wu.cli --base data download --files page,redirect,linktarget,pagelinks
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
python -m wu.cli --base data parse --dump-date 2026-09-02

# (2) pagelinks 全行ストリーム解析 → data/graph/edges_ns0.bin(~2-5分)
python -m wu.cli --base data edges

# (3) 全グラフ統計(~1分)
python -m wu.cli --base data stats
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
python -m wu.cli --base data subset-bfs \
  --seeds "東京都,大阪府,京都府,北海道,福岡県,愛知県,宮城県,広島県,新潟県,長野県,日本の地理,市町村" \
  --max-nodes 100000 --cap 60 --hops 4 --max-in-degree 10000 --name bfs_geo_100k

python -m wu.cli --base data analyze --subset bfs_geo_100k \
  --resolutions 0.5,1.0,2.0 --engine igraph
# 出力先: data/community/bfs_geo_100k/(--out-dir で変更可。場所はどこでもよい)
```

期待される概略値: `res=0.5: C=11 largest≈0.30 crossF≈0.24 mod≈0.65`。
Leiden には乱択性があるため **membership の完全一致は保証されない**(seed=42 固定・
同一バージョンなら通常一致するが、igraph のバージョン差で変わり得る)。指標が
±数% 以内に収まれば再現成功とみなす。

## 6. フルパイプライン → 座標出版(run 運用)

§4 のグラフ構築が済んだら、以下が本番データの一本道(各コマンドの詳細と期待値は
README の該当セクション、実測結果は results/SUMMARY.md):

```bash
# 1) コミュニティ検出(マクロ → 銀河の 2 段階)
python scripts/run_full_leiden.py --base data dedup
python scripts/run_full_leiden.py --base data detect --resolutions 1.0
python scripts/run_full_leiden.py --base data subdivide --resolution 1.0 --max-galaxy 10000
python scripts/run_full_leiden.py --base data metrics --tag res1_sub
python scripts/run_full_leiden.py --base data cluster --tag res1_sub
python scripts/run_full_leiden.py --base data export  --tag res1_sub

# 2) (任意) 本文リンク・カテゴリ: README「本文リンクグラフ」「カテゴリ整合性」

# 3) 銀河カタログ(data/final/ = データ契約)
python scripts/build_galaxy_catalog.py --base data --galaxy-tag res1_sub --macro-tag res1

# 3b) 銀河団ラベルのキュレーション: 正典コピー(リポジトリ追跡下)は
#     results/macro_label_overrides.json。data/final/ へコピーすると (5) の出版時に
#     適用される。編集し直す場合はテンプレ出力 → label 編集 → data/final/ に保存 →
#     変更を正典コピーにも反映(両方まとめてコミット)
cp results/macro_label_overrides.json data/final/macro_label_overrides.json
python scripts/audit_names.py --base data --dump-macro-labels   # テンプレ再生成(見直し用)

# 4) 座標 run の生成(① マクロ+銀河 → ② 銀河内記事 → マージ)
python scripts/layout_global.py      --base data --run <RUN> --macro-dim 3
python scripts/run_local_parallel.py --base data --run <RUN>   # jobs 既定=CPU 数
#   初回のみ共有事前計算(data/graph/local_prep/<tag>/、数分・全 run で共用)が走り、
#   その後 LPT 分割の並列ジョブ → 走査なしマージ。フルランの目安は B0 基盤導入前で
#   ~63分(jobs 8・2026-09-26 実測)、導入後の実測は knowledge/02 §O を更新予定

# 5) 出版(spatial/ へ。ビューアは常にここを読む)
python scripts/export_viewer_tiles.py --base data --run <RUN>

# 6) 配信とビューア
python -m http.server 8000        # リポジトリルート(別ターミナル)
cd viewer && npm install && npm run dev    # http://localhost:5173
```

### run の命名と採用

- run 名 = `data/layout/` 直下のディレクトリ名。**規約: `<YYYYMMDD>_<slug>`**
  (ASCII 小文字。日付は生成日、slug は「その run が何か」= 役割・主要パラメータ)。
  正確なパラメータと品質指標は各 run の `layout_meta.json` が正典。
- **正典ポインタ**: `wu/paths.py` の `ACTIVE_LAYOUT_RUN`。全レイアウト系スクリプト
  (layout_global / layout_local / run_local_parallel / export_viewer_tiles)の `--run`
  既定値であり、「いま公開中の座標」を指す。
- 新しい座標 run を**採用**する手順: 4)→5) を新 run 名で実行 →
  `wu/paths.py` の `ACTIVE_LAYOUT_RUN` を新 run 名に更新してコミット。
  比較実験は run を並べるだけでよい(上書き事故が起きない)。
- 既存 run の記事座標を再利用する場合は 4) の run_local_parallel の代わりに
  `python scripts/recompose_articles.py --base data --from-run <旧RUN> --to-run <新RUN>`。
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
| 星の hover 名が `local#NN` になる | 記事が無いのではなく**タイトルが引けていない**状態。①`python scripts/audit_tiles.py --base data` でサイドカー(`spatial/tiles/gal_*.json`)の欠落・不足を計測(欠落があれば `export_viewer_tiles.py` を再実行)②データ側が 0 件ならビューア側のタイル LRU 追い出しが原因(浮上中銀河は `pinTiles` で保護・`Entry.titles` 参照で hover 名をキャッシュ状態から独立させ済み。DevTools の `[tile N] サイドカー…` 警告で判別) |
| 銀河名が「〜のスタブ」等のまま | 保守サフィックスは語幹正規化済み(`build_galaxy_catalog.py` の `_stem_name`)。`python scripts/audit_names.py --base data` で再計測し、残存パターンがあれば `MAINT_SUFFIX_RE` か `NAME_BLACKLIST` に追加 → catalog + export 再実行(再 Leiden/再レイアウト不要) |
| 銀河団ラベルが「ISBN」「地理座標系」等ハブ記事名になる | 既定導出(rep_titles 先頭 16 文字)の限界。リポジトリ同梱のキュレーション表(正典)`results/macro_label_overrides.json` を `data/final/` へコピー → `export_viewer_tiles.py` 再実行(§6 の 3b)。編集し直す場合: `python scripts/audit_names.py --base data --dump-macro-labels` → `final/macro_label_overrides.template.json` の label を編集 → `final/macro_label_overrides.json` として保存(正典コピーへの反映も忘れずに) |
