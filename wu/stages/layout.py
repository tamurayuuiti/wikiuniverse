# wu/stages/layout.py - 座標ステージ(layout_global / layout_local / recompose)
#
# 責務:
# - layout_global: マクロ+銀河の 3D 配置(銀河カタログ → layout/<run>/)。
# - layout_local: 銀河内記事座標。**並列ランチャ(wu.layout_parallel)へ委譲する**
#   (LOCAL_SETUP の推奨経路 = 事前計算共有 + LPT 分割 + 走査なしマージ。
#   逐次実行・バッチ分割はモジュール CLI(python -m wu.layout.layout_local)が担う)。
# - recompose: 既存 run の記事座標を新 run へ再構成するユーティリティ。
#
# 注意:
# - run 名は共有パラメータ(shared["run"]、既定 = 正典 ACTIVE_LAYOUT_RUN)。
#   両ステージとも同一 run の中で galaxy_positions → article_positions の順。
# - 実体は main(a=None) への Namespace 注入(CLI と同一経路 = 挙動不変)。
# - layout_local の腕フラグ(fr_mode/anchor_mode/fr_norm 等)は全ジョブへ
#   パススルーされる(ランチャがジョブ命令行を組む。脱落防止はテスト済み)。

from __future__ import annotations

import os
from argparse import Namespace

from ..layout import layout_global as lg_mod
from ..layout import parallel as lp_mod
from ..layout import recompose as rc_mod
from ..pipeline.stage import Param, stage


@stage(
    name="layout_global",
    title="マクロ+銀河の 3D 配置(球パッキング/FR、spill 構造保証)",
    group="canonical",
    params=(
        Param("run", str, None, "座標 run 名(既定: 共有 run = 正典)"),
        Param("pack", float, 0.6, "銀河半径の充塡率係数"),
        Param("r_spacing", float, 1.5, "銀河半径のフロア係数(r >= 1.5*n^(1/3))"),
        Param("r_expo", float, 0.5,
              "半径則の指数(n_g/n_M の何乗。0.5=従来 √n・ビット同一 / "
              "1/3=記事体積密度が均等な 3D 整合腕)"),
        Param("macro_dim", int, 3, "マクロ配置の次元(3=正準、2=実験腕)",
              choices=(2, 3)),
        Param("macro_z_squash", float, 1.0, "マクロ z 圧縮(1.0=正準・焼き込み無し)"),
        Param("macro_w_power", float, 1.0,
              "マクロ FR 引力の重み温度 w^τ(1.0=中立。実測: >1 は意味的近接を悪化させた)"),
        Param("views", int, 24, "射影重なり統計のランダム視点の数"),
        Param("seed", int, 42, "乱数シード"),
        Param("pairs", str, "catalog", "銀河間ペアの出所(catalog=final/ 台帳 / npz)",
              choices=("catalog", "npz")),
        Param("galaxy_tag", str, None, "銀河タグ(既定: 共有の galaxy_tag)"),
    ),
    inputs=("final.galaxies", "final.macros", "final.galaxy_pairs",
            "final.macro_pairs"),
    outputs=("layout.galaxy_positions", "layout.macro_positions", "layout.meta"),
    always_run=True,
)
def run_layout_global(ctx) -> None:
    p = ctx.params
    lg_mod.main(Namespace(
        base=str(ctx.dirs.base), run=p["run"] or ctx.shared["run"],
        pack=p["pack"], r_spacing=p["r_spacing"], r_expo=p["r_expo"],
        macro_dim=p["macro_dim"], macro_z_squash=p["macro_z_squash"],
        macro_w_power=p["macro_w_power"], views=p["views"], seed=p["seed"],
        pairs=p["pairs"],
        galaxy_tag=p["galaxy_tag"] or ctx.shared["galaxy_tag"]))


@stage(
    name="layout_local",
    title="銀河内記事座標(並列ランチャ: 事前計算共有 + LPT 分割 + 集約マージ)",
    group="canonical",
    params=(
        Param("run", str, None, "座標 run 名(既定: 共有 run = 正典)"),
        Param("jobs", int, 0, "並列ジョブ数(0 = CPU 数)"),
        Param("galaxy_tag", str, None, "銀河タグ(既定: 共有の galaxy_tag)"),
        Param("fr_mode", str, "ml", "銀河内 FR(ml=マルチレベル初期化 / flat=平坦アニール)",
              choices=("ml", "flat")),
        Param("ml_threshold", int, 2000, "マルチレベル初期化を適用する銀河サイズ以上"),
        Param("ml_niter", int, 100, "マルチレベル後の refine 反復数"),
        Param("anchor_mode", str, "sector",
              "アンカーバネ(sector=優勢隣接銀河方向 / sum=総和単位ベクトル=比較腕)",
              choices=("sector", "sum")),
        Param("fr_norm", str, "p98", "FR 正規化(p98=ロバスト / max=最大正規化=比較腕)",
              choices=("p98", "max")),
        Param("sector_ratio", float, 0.5, "top-2 隣接ブレンドの重量比閾値"),
    ),
    inputs=("layout.galaxy_positions", "community.membership",
            "graph.edges_undirected"),
    outputs=("layout.article_positions", "layout.local_meta",
             "layout.parallel_meta"),
    always_run=True,  # 並列再開は内部 checkpoint が担う
)
def run_layout_local(ctx) -> None:
    p = ctx.params
    lp_mod.main(Namespace(
        base=str(ctx.dirs.base), jobs=p["jobs"] or (os.cpu_count() or 8),
        galaxy_tag=p["galaxy_tag"] or ctx.shared["galaxy_tag"],
        run=p["run"] or ctx.shared["run"], fr_mode=p["fr_mode"],
        ml_threshold=p["ml_threshold"], ml_niter=p["ml_niter"],
        anchor_mode=p["anchor_mode"], fr_norm=p["fr_norm"],
        sector_ratio=p["sector_ratio"]))


@stage(
    name="recompose",
    title="記事座標の run 間再構成(グローバル変更のみ時に再レイアウトせず移植)",
    group="optional",
    params=(
        Param("from_run", str, None, "移植元 run(必須)"),
        Param("to_run", str, None, "移植先 run(必須。galaxy_positions 生成済み)"),
    ),
    inputs=(),   # run 名がパラメータ依存のため契約は docstring で明示する
    outputs=(),
    always_run=True,  # 軽量・冪等
)
def run_recompose(ctx) -> None:
    p = ctx.params
    if not p.get("from_run") or not p.get("to_run"):
        raise ValueError(
            "recompose には from_run / to_run の両方が必要です"
            "(例: --set recompose.from_run=… --set recompose.to_run=…)")
    rc_mod.main(Namespace(base=str(ctx.dirs.base), from_run=p["from_run"],
                          to_run=p["to_run"]))
