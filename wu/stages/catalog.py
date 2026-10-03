# wu/stages/catalog.py - 純度・銀河カタログのステージ(community → final の橋渡し)
#
# 責務:
# - purity: カテゴリ純度(purity v2)の計算 = 銀河名の材料づくり。
# - catalog: data/final/ 台帳の生成(レイアウト・ビューアのデータ契約)。
#
# 注意:
# - 実体は wu.purity.main / wu.catalog.main(Namespace 注入で呼ぶ = 引数解析と
#   処理本体の分離。CLI ラッパと同一経路なので挙動は変わらない)。
# - tag 系パラメータの既定 None = 共有パラメータ(shared)へフォールバック。
# - catalog の任意入力(purity / clusters / per_community_<macro>)は契約 inputs に
#   含めない(欠けても縮退動作する既存仕様を尊重。必須分のみ宣言)。

from __future__ import annotations

from argparse import Namespace

from ..community import catalog as catalog_mod
from ..community import purity as purity_mod
from ..pipeline.stage import Param, stage


@stage(
    name="purity",
    title="カテゴリ純度の計算(purity v2: tf-idf 命名・メタカテゴリ除外)",
    group="canonical",
    params=(
        Param("tag", str, None, "対象タグ(既定: 共有の銀河タグ)"),
        Param("top", int, 15, "コミュニティ別に表示する上位カテゴリ数"),
        Param("max_cat_freq", int, 20000,
              "この件数を超えるカテゴリはメタカテゴリとして命名から除外"),
        Param("name_th", float, 0.30, "nameable 判定の top1_share_filt 閾値"),
    ),
    inputs=("community.membership", "graph.categories",
            "graph.article_categories"),
    outputs=("community.purity", "community.purity_json"),
)
def run_purity(ctx) -> None:
    tag = ctx.params["tag"] or ctx.shared["tag"]
    purity_mod.main(Namespace(base=str(ctx.dirs.base), tag=tag,
                              top=ctx.params["top"],
                              max_cat_freq=ctx.params["max_cat_freq"],
                              name_th=ctx.params["name_th"]))


@stage(
    name="catalog",
    title="銀河カタログ生成(data/final/ = レイアウト・ビューアのデータ契約)",
    group="canonical",
    params=(
        Param("galaxy_tag", str, None, "銀河タグ(既定: 共有の galaxy_tag)"),
        Param("macro_tag", str, None, "マクロタグ(既定: 共有の macro_tag)"),
        Param("top_neighbors", int, 8, "銀河台帳に記録する隣接銀河の数"),
        Param("top_pairs", int, 30000, "中景用バンドルに保持する銀河間ペア数"),
    ),
    inputs=("community.membership", "community.membership_macro",
            "community.per_community", "community.pairs"),
    outputs=("final.galaxies", "final.macros", "final.galaxy_pairs",
             "final.macro_pairs", "final.catalog_meta"),
)
def run_catalog(ctx) -> None:
    gt = ctx.params["galaxy_tag"] or ctx.shared["galaxy_tag"]
    mt = ctx.params["macro_tag"] or ctx.shared["macro_tag"]
    catalog_mod.main(Namespace(base=str(ctx.dirs.base), galaxy_tag=gt,
                               macro_tag=mt,
                               top_neighbors=ctx.params["top_neighbors"],
                               top_pairs=ctx.params["top_pairs"]))
