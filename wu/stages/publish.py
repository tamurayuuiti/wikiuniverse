# wu/stages/publish.py — 出版ステージ(Viewer 公開面の生成)
#
# 責務:
# - 座標 run + カタログ → data/spatial/(bootstrap.json + tiles/)の生成。
#   ビューアは常にここを読む(URL 契約は run 名と無縁に固定)。
#
# 注意:
# - run/galaxy_tag の既定 None = 共有パラメータへフォールバック
#   (正典 run からの出版 = 採用切替は設定/共有パラメータ側で行う)。
# - cross_cap = 記事あたりクロスリンク数の上限(タイルサイズの制御)。

from __future__ import annotations

from argparse import Namespace

from .. import publish as publish_mod
from ..pipeline.stage import Param, stage


@stage(
    name="publish",
    title="Viewer 公開面の生成(spatial/ への出版: bootstrap + タイル + sidecar)",
    group="canonical",
    params=(
        Param("run", str, None, "出版元の座標 run(既定: 共有 run = 正典)"),
        Param("galaxy_tag", str, None, "銀河タグ(既定: 共有の galaxy_tag)"),
        Param("cross_cap", int, 8, "記事あたりクロスリンクの上限(タイル size 制御)"),
    ),
    inputs=("layout.article_positions", "layout.galaxy_positions",
            "layout.macro_positions", "final.galaxies", "final.macros",
            "final.galaxy_pairs", "final.macro_pairs", "parsed.articles"),
    outputs=("spatial.bootstrap", "spatial.tiles", "spatial.tiles_meta"),
)
def run_publish(ctx) -> None:
    p = ctx.params
    publish_mod.main(Namespace(
        base=str(ctx.dirs.base),
        galaxy_tag=p["galaxy_tag"] or ctx.shared["galaxy_tag"],
        run=p["run"] or ctx.shared["run"], cross_cap=p["cross_cap"]))
