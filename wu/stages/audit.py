# wu/stages/audit.py - 読み取り専用監査ステージ(3 本)
#
# 責務:
# - audit_tiles: 公開面(タイル+サイドカー)の整合計測(local#NN の原因切り分け)。
# - audit_names: 銀河/銀河団の命名内訳 + ラベルのテンプレ出力。
# - audit_layout: 座標 run の spill 文脈・平坦度(null 対比)・重心配置率・相関。
#
# 注意:
# - 監査は再計算も書き込みも行わない(outputs 空 = 常に実行)。
# - 旧 CLI の終了コード(0=正常/2=対象なし)はランナーの規約へ翻訳する:
#   rc != 0 は RuntimeError にして manifest に failed として記録させる。

from __future__ import annotations

from argparse import Namespace

from ..audit import layout as al_mod
from ..audit import names as an_mod
from ..audit import tiles as at_mod
from ..pipeline.stage import Param, stage


def _rc(name: str, rc) -> None:
    if rc:
        raise RuntimeError(f"{name}: 監査が非ゼロで終了(rc={rc}。出力参照)")


@stage(
    name="audit_tiles",
    title="公開面の整合監査(タイル/サイドカー/タイトル。読み取り専用)",
    group="audit",
    params=(Param("limit", int, 20, "詳細表示する不良タイルの上限"),),
    inputs=("spatial.bootstrap", "spatial.tiles"),
    outputs=(),
)
def run_audit_tiles(ctx) -> None:
    _rc("audit_tiles", at_mod.main(Namespace(base=str(ctx.dirs.base),
                                             limit=ctx.params["limit"])))


@stage(
    name="audit_names",
    title="命名の内訳監査(銀河名/銀河団ラベル。読み取り専用)",
    group="audit",
    params=(
        Param("top", int, 25, "サイズ上位の表示件数"),
        Param("dump_macro_labels", bool, False,
              "ラベル編集用テンプレートを final/ へ出力する"),
    ),
    inputs=("final.galaxies", "final.macros"),
    outputs=(),
)
def run_audit_names(ctx) -> None:
    _rc("audit_names", an_mod.main(Namespace(
        base=str(ctx.dirs.base), top=ctx.params["top"],
        dump_macro_labels=ctx.params["dump_macro_labels"])))


@stage(
    name="audit_layout",
    title="座標 run の監査(spill 文脈/平坦度 null 対比/重心配置率。読み取り専用)",
    group="audit",
    params=(Param("run", str, None, "監査する run(既定: 共有 run = 正典)"),),
    inputs=("layout.galaxy_positions", "layout.macro_positions", "layout.meta"),
    outputs=(),
)
def run_audit_layout(ctx) -> None:
    p = ctx.params
    _rc("audit_layout", al_mod.main(Namespace(
        base=str(ctx.dirs.base), run=p["run"] or ctx.shared["run"])))
