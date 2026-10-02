# wu/layout — 座標生成の実装本体
#
# layout_global(マクロ+銀河の 3D 配置)/ layout_local(銀河内記事座標。
# バッチ・並列ジョブの単位)/ parallel(並列ランチャ: LPT 分割・テレメトリ・マージ)/
# recompose(記事座標の run 間再構成)。ステージ宣言は wu/stages/layout.py。
