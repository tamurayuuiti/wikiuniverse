# ローカル環境セットアップ手順(ハンドオフ)

作成: 2026-09-25 / 対象: jawiki グラフ分割 PoC(宇宙・銀河プロジェクト)

## 0. なぜローカルか

サンドボックス(チャット側ワークスペース)は **RAM 1GB / 2 vCPU** の制約があり、以下が実行できません。

| 処理 | 必要 RAM(実測ベース) | サンドボックス | ローカル 32GB |
|---|---|---|---|
| 全グラフ構築(142M エッジ) | ~1GB(ストリーミング) | ✅(済) | ✅ 数分 |
| 100k サブセット Leiden | ~600MB | ✅(済) | ✅ |
| 150k+ サブセット / 複数並列 | 1–4GB | ❌ | ✅ |
| **全 1.45M グラフ一括 Leiden** | **~12–16GB**(igraph ~58B/edge) | ❌ | ✅(本命) |
| 全グラフ 2 段階分割(粗分割→銀河内精密) | 8–16GB | ❌ | ✅ |

**役割分担の推奨**: コード改良・小実験・レポート分析 = どちらでも可 / 大規模計算・本番データ生成 = ローカル。

## 1. ローカルに反映するファイル

`wikiuniverse_handoff.zip`(またはワークスペースの `wikiuniverse/` ディレクトリ)を任意の場所に展開してください。**データ(ダンプ・中間生成物)は含まれていません** — §3 で取得します(合計 ~2.5GB 生成)。

```
wikiuniverse/
├── README.md               # 全体像・コマンド・指標定義・スケーリングメモ
├── LOCAL_SETUP.md          # 本書
├── PROVENANCE.json         # 使用ダンプの SHA-256(完全性検証用)
├── wu/                     # 本体パッケージ(純 Python、~2,500 行)
│   ├── __init__.py
│   ├── paths.py            #   ★ データレイアウトの単一の真実(Dirs クラス)
│   ├── dumpio.py           #   ダウンロード(resume/並列)・gz ストリーム
│   ├── sqlparse.py         #   page/linktarget/redirect 解析 → ハッシュ表(parsed/)
│   ├── buildedges.py       #   pagelinks → 解決済みエッジ(チェックポイント resume 可)
│   ├── stats.py            #   全グラフ次数統計・ハブ top
│   ├── subsets.py          #   BFS / ID窓 サブセット抽出(メガハブ除外付き)
│   ├── analysis.py         #   Leiden(igraph/leidenalg)・指標・L2 階層化
│   ├── pipeline.py         #   エンドツーエンド解析(sweep→一次選択→hub 処理→レポート)
│   ├── report.py           #   プロット + Markdown レポート
│   └── cli.py              #   サブコマンド(python -m wu.cli ...)
├── scripts/                # 補助(show_stats / summarize_runs / make_notebook 等)
├── tests/test_synthetic.py # 合成データ自己テスト(ネットワーク不要・全経路検証)
├── notebooks/poc_colab.ipynb  # Colab 版(自己完結 25 セル)
└── results/                # 第1回検証の全成果物(~23MB)
    ├── SUMMARY.md          # ★ 評価レポート本体(暫定判定 B)
    ├── runs_table.txt      # 全 run 比較表
    └── bfs_geo_100k_raw/ 他 16 ディレクトリ
        ├── report.md       #   日本語レポート
        ├── metrics.json    #   全数値(再解析・比較用)
        ├── communities.parquet  # ノード→コミュニティ割当
        └── *.png           #   サイズ分布 / out_ratio / conductance / 独立性マップ / 階層
```

### ローカルで生成されるデータ(`--base data` 既定、工程別分離 — 方針 2026-09-25)

```
data/
├── dump/        # 元の Wikipedia ダンプ 4 ファイル(~1.13GB)
├── parsed/      # 中間データ: articles.parquet / article_ids.npy / ns0_all_hashes.npz /
│                #   lt_hash_ns0.npy / rd_map.npz / meta.json(~190MB)
├── graph/       # edges_ns0.bin(1.14GB)/ indeg.npy / outdeg.npy / full_stats.json /
│   └── subsets/ #   サブセット別 nodes.parquet, edges_internal.npy, node_stats.npz
└── community/   # analyze の既定出力先(metrics.json, communities.parquet, report.md, *.png)

# 将来の予約名(必要になった時点で追加): data/layout/, data/spatial/, data/final/
```

パスを触る改修は `wu/paths.py` の `Dirs` クラスに集約してください(ハードコード禁止の運用)。

## 2. 環境要件

- **Python 3.10–3.12**(検証: 3.11.2)
- パッケージ(検証バージョン): `numpy 2.4.6 / scipy 1.17.1 / pandas 3.0.6 / pyarrow / matplotlib / python-igraph 1.0.0 / leidenalg 0.12.0`

```bash
pip install numpy scipy pandas pyarrow matplotlib igraph leidenalg
```

- **ディスク**: ~8GB 空き(ダンプ 1.13GB + parsed/ ~0.2GB + graph/edges_ns0.bin 1.14GB + サブセット/解析出力)
- **RAM**: 16GB でサブセット実験まで / **32GB 推奨**(全グラフ Leiden 用)
- **OS**: Windows/macOS/Linux いずれも可。コードはクロスプラットフォーム(メモリ返却最適化 `malloc_trim` は Linux 専用だが不在時は自動無効化)。Windows でメモリが気になる場合は WSL2 推奨。
- 動作確認: 展開ディレクトリで

```bash
python -m pytest tests/ -q 2>/dev/null || python tests/test_synthetic.py
# 末尾に "*** ALL SYNTHETIC TESTS PASSED ***" が出れば OK(ネット不要・数秒)
```

## 3. データセットの取得(ローカルで再ダウンロードが正解)

サンドボックスの `.cache/` にもダンプ実体はありますが、(a) 永続スナップショット対象外・(b) 合計 1.1GB+edges 1.14GB で転送非現実的、のため **Wikimedia 公式から直接取得してください**(同じファイルです)。家庭回線なら数分で終わる規模です。

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

### 完全性検証(任意だが推奨)

`PROVENANCE.json` にサンドボックスが実際に解析した 4 ファイルの SHA-256 を記録してあります。

```bash
sha256sum data/dump/*.gz   # Windows: certutil -hashfile <file> SHA256
```

- **一致** → 第1回検証と完全に同一データ。下の検証値も一致するはずです。
- **不一致(latest が更新されていた場合)** → それでも問題ありません。`parse` の `--dump-date` を新しい日付にし、§4 の検証値が数%程度ずれることを許容してください(構造結論は変わりません)。

> 次のフェーズでカテゴリ基準サブセットをやる場合は `jawiki-latest-categorylinks.sql.gz`(176MB)も同様に取得できます(パーサは未実装 — 次ターンで追加予定)。

## 4. グラフ構築と検証(ローカルでの初回実行)

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

`edges` は **チェックポイント付き**(`data/graph/edges_checkpoint.json`)なので、中断したら同じコマンドを再実行すれば続きから進みます。

## 5. 第1回検証の再現(任意)

```bash
# 100k 地理サブセット( extraction ~1分)
python -m wu.cli --base data subset-bfs \
  --seeds "東京都,大阪府,京都府,北海道,福岡県,愛知県,宮城県,広島県,新潟県,長野県,日本の地理,市町村" \
  --max-nodes 100000 --cap 60 --hops 4 --max-in-degree 10000 --name bfs_geo_100k

# 解析(~2分。ローカルなら resolutions を増やしても軽い)
python -m wu.cli --base data analyze --subset bfs_geo_100k \
  --out-dir results/bfs_geo_100k_raw --resolutions 0.5,1.0,2.0 --engine igraph
```

期待される概略値: `res=0.5: C=11 largest≈0.30 crossF≈0.24 mod≈0.65`。
Leiden には乱択性があるため **membership の完全一致は保証されません**(seed=42 固定・同一バージョンなら通常一致しますが、igraph のバージョン差で変わり得ます)。指標が ±数% 以内に収まれば再現成功とみなしてください。

## 6. ローカル移行後に最初にやること(次フェーズ)

1. **全グラフ一括 Leiden** — ✅ 完了(2026-09-26、ユーザーローカル)。結果は results/SUMMARY.md フェーズ 2/2b/2c 参照
2. **2 段階分割(subdivide)** — ✅ 完了。銀河 1,535 個(中央値 279)
3. **E1/E2 剪定実験** — ✅ 完了。銀河 oR ≈0.72 は剪定に頑健 → E3 が決定的
4. **E3: 本文リンクグラフ** — ✅ 実装完了(`wu/xmlparse.py` + `body-edges` + 汎用 dedup + compare スクリプト)。フルランは README「本文リンクグラフ」節のコマンドで(ディスク +4.7GB、所要 ~20-60 分)
5. **categorylinks パーサ追加**(カテゴリ基準サブセット)— 未実装
6. **境界情報フォーマット設計(§12.2)と銀河バッチレイアウト試作** — E3 結果を受けて設計

## 7. トラブルシュート

| 症状 | 対処 |
|---|---|
| **生成した .md/.json が VS Code で文字化け** | 2026-09-25 以前のコードは Windows 既定(cp932)で書き出していた。**修正済みファイルを同期後、`stats` や `analyze` を再実行して再生成**(または VS Code 右下の encoding →「Reopen with Encoding」→ Shift JIS →「Save with Encoding」→ UTF-8 で変換)。恒久保険として環境変数 `PYTHONUTF8=1` の設定も推奨 |
| download が途中で切れる | 同じコマンド再実行(Range resume 対応) |
| `edges` がメモリ不足 | 元々 ~400MB ピーク設計。他アプリ終了。それでもダメなら `--chunk-bytes 8388608` |
| analyze が OOM(巨大サブセット) | `--engine igraph` を使う(leidenalg は 2 倍消費)/ `--degree-budget 40 --degree-budget-mode smart` でレイアウト用グラフ化 |
| pandas/pyarrow バージョン差エラー | `pip install -U pandas pyarrow`(pandas 2.x でも概ね動作、検証は 3.0.6) |
| Windows で RSS が高止まり | `malloc_trim` 不在のため。WSL2 なら Linux と同挙動 |
| 数値が検証値と微妙に違う | ダンプが更新されている可能性(§3 の SHA-256 確認) |
