# wu/stages/community.py - コミュニティ検出ステージ群(フルグラフ Leiden)
#
# 責務:
# - 無向一意化(dedup)→ 一括検出(detect = マクロ)→ 再帰分割(subdivide = 銀河)
#   → 指標(metrics)→ L2 銀河団(cluster)→ 結合表(export)の正式チェーンを
#   ステージとして登録する。実体は wu.fullgraph の cmd_* へ委譲(挙動同一)。
# - prune(次数予算剪定)はレイアウト用グラフの実験腕 = experiment 分類
#   (D16: 構造決定には生グラフが正史。剪定は描画コスト削減と交差評価用)。
#
# 注意:
# - tag の解決: 各ステージの "tag" パラメータは既定 None = 共有パラメータ
#   (shared["tag"] = 銀河タグ、正準 res1_sub)へフォールバックする。
#   detect/subdivide のみソース/出力の tag を明示的に持つ。
# - detect の outputs 宣言は「第一 resolution の membership」を表す
#   (複数 resolution は 1 起動で全部書くが、契約は代表成果物で宣言する)。
#   tag パラメータは resolutions の第一値と整合させること(run 時に警告する)。

from __future__ import annotations

from ..community.fullgraph import (cmd_cluster, cmd_dedup, cmd_detect, cmd_export,
                         cmd_metrics, cmd_prune, cmd_subdivide, _res_tag)
from ..pipeline.stage import Param, stage


@stage(
    name="dedup",
    title="有向エッジ → 無向一意エッジ(ビットパック外部ソート)",
    group="canonical",
    params=(
        Param("chunk", int, 10_000_000, "ソートのチャンクサイズ(エッジ数)"),
        Param("edges_in", str, "edges_ns0.bin",
              "入力エッジファイル名(graph/ 下。本文リンク腕は edges_body_directed.bin)"),
        Param("out_name", str, "edges_undirected_unique.bin", "出力ファイル名"),
        Param("meta_name", str, "dedup_meta.json", "来歴メタのファイル名"),
    ),
    inputs=("graph.edges_ns0",),
    outputs=("graph.edges_undirected",),
)
def run_dedup(ctx) -> None:
    cmd_dedup(ctx.dirs, chunk=ctx.params["chunk"],
              edges_in=ctx.params["edges_in"], out_name=ctx.params["out_name"],
              meta_name=ctx.params["meta_name"])


@stage(
    name="detect",
    title="一括 Leiden = マクロコミュニティ検出(複数 resolution は 1 起動で共有構築)",
    group="canonical",
    params=(
        Param("tag", str, "res1", "出力タグ(membership_<tag>.npy)"),
        Param("resolutions", str, "1.0", "resolution リスト(カンマ区切り)"),
        Param("objective", str, "modularity", "最適化目的関数",
              choices=("modularity", "CPM")),
        Param("max_edges", int, 0, "スモーク: 先頭 N エッジのみ使用(0=全部)"),
        Param("seed", int, 42, "乱数シード(全工程共通の既定値)"),
        Param("force", bool, False, "既存 membership の上書き"),
        Param("edges_name", str, None,
              "エッジファイル名(既定: edges_undirected_unique.bin)"),
        Param("suffix", str, "", "タグの追加サフィックス(例 _B40)"),
    ),
    inputs=("graph.edges_undirected",),
    outputs=("community.membership",),
)
def run_detect(ctx) -> None:
    res = [float(x) for x in ctx.params["resolutions"].split(",")]
    # 契約上の tag と実際の出力名の整合を確認する(不一致は skip 判定を壊すため)
    expect = f"res{_res_tag(res[0])}{ctx.params['suffix']}"
    if ctx.params["tag"] != expect:
        ctx.log(f"[wu][warn] detect: tag={ctx.params['tag']!r} だが第一 "
                f"resolution からの期待は {expect!r}(成果物契約と不一致の恐れ)")
    cmd_detect(ctx.dirs, res, max_edges=ctx.params["max_edges"],
               seed=ctx.params["seed"], force=ctx.params["force"],
               objective=ctx.params["objective"],
               edges_name=ctx.params["edges_name"], suffix=ctx.params["suffix"])


@stage(
    name="subdivide",
    title="マクロ内を再帰 Leiden で銀河サイズへ分割(2 段階分割)",
    group="canonical",
    params=(
        Param("tag", str, "res1", "分割元タグ(マクロ)"),
        Param("out_tag", str, "res1_sub", "出力タグ(銀河)"),
        Param("min_size", int, 100, "最小コミュニティサイズ"),
        Param("max_galaxy", int, 10000,
              "銀河のサイズ上限(正準運用値。旧 CLI 既定は 20000 だった)"),
        Param("sub_resolution", float, 1.0, "内部 Leiden の resolution"),
        Param("sub_objective", str, "modularity", "内部の目的関数",
              choices=("modularity", "CPM")),
        Param("depth", int, 4, "再帰の深さ上限"),
        Param("seed", int, 42, "乱数シード"),
        Param("chunk", int, 4_000_000, "誘導エッジ収集のチャンクサイズ"),
        Param("edges_name", str, None, "エッジファイル名(既定: 無向一意)"),
    ),
    inputs=("community.membership", "graph.edges_undirected"),
    outputs=("community.membership_out", "community.subdivide_meta"),
)
def run_subdivide(ctx) -> None:
    cmd_subdivide(ctx.dirs, ctx.params["tag"], min_size=ctx.params["min_size"],
                  max_galaxy=ctx.params["max_galaxy"],
                  sub_resolution=ctx.params["sub_resolution"],
                  sub_objective=ctx.params["sub_objective"],
                  depth=ctx.params["depth"], seed=ctx.params["seed"],
                  chunk=ctx.params["chunk"], out_tag=ctx.params["out_tag"],
                  edges_name=ctx.params["edges_name"])


@stage(
    name="metrics",
    title="コミュニティ指標のチャンク計算(全体/コミュニティ別/間ペア)",
    group="canonical",
    params=(
        Param("tag", str, None, "対象タグ(既定: 共有の銀河タグ)"),
        Param("chunk", int, 4_000_000, "エッジ走査チャンク"),
        Param("edges_name", str, None, "評価グラフ(既定: 無向一意。剪定評価で差替)"),
        Param("label", str, None, "出力ラベル(既定: tag。評価差し替え時に別名)"),
    ),
    inputs=("community.membership", "graph.edges_undirected"),
    outputs=("community.metrics_json", "community.per_community",
             "community.top_pairs", "community.pairs"),
)
def run_metrics(ctx) -> None:
    tag = ctx.params["tag"] or ctx.shared["tag"]
    cmd_metrics(ctx.dirs, tag, chunk=ctx.params["chunk"],
                edges_name=ctx.params["edges_name"], label=ctx.params["label"])


@stage(
    name="cluster",
    title="L2 銀河団クラスタリング(コミュニティ間重みグラフの Leiden)",
    group="canonical",
    params=(
        Param("tag", str, None, "対象タグ(既定: 共有の銀河タグ)"),
        Param("cluster_resolution", float, 1.0, "L2 の resolution"),
        Param("seed", int, 42, "乱数シード"),
    ),
    inputs=("community.pairs", "community.membership"),
    outputs=("community.clusters_node", "community.clusters_comm",
             "community.clusters_meta"),
)
def run_cluster(ctx) -> None:
    tag = ctx.params["tag"] or ctx.shared["tag"]
    cmd_cluster(ctx.dirs, tag, cluster_resolution=ctx.params["cluster_resolution"],
                seed=ctx.params["seed"])


@stage(
    name="export",
    title="membership の結合表 parquet 化(page_id+title+comm+cluster)",
    group="canonical",
    params=(
        Param("tag", str, None, "対象タグ(既定: 共有の銀河タグ)"),
    ),
    inputs=("community.membership", "community.per_community",
            "community.clusters_node"),
    outputs=("community.membership_parquet",),
)
def run_export(ctx) -> None:
    tag = ctx.params["tag"] or ctx.shared["tag"]
    cmd_export(ctx.dirs, tag)


@stage(
    name="prune",
    title="次数予算剪定(レイアウト用グラフの実験腕。構造決定には不使用 = D16)",
    group="experiment",
    params=(
        Param("budget", int, 40, "高次数ノードあたりの保持辺数の上限"),
        Param("mode", str, "smart",
              "smart=低次数側の辺は常時保持(推奨)/ both=従来型(破砕するので非推奨)",
              choices=("smart", "both")),
        Param("seed", int, 42, "乱数シード"),
    ),
    inputs=("graph.edges_undirected",),
    outputs=("graph.edges_pruned",),
)
def run_prune(ctx) -> None:
    cmd_prune(ctx.dirs, budget=ctx.params["budget"], mode=ctx.params["mode"],
              seed=ctx.params["seed"])
