# wu/stages/stats.py - ステージ: 全グラフ次数統計・ハブ検出
#
# 責務:
# - graph/edges_ns0.bin(解決済み有向エッジ)から全記事の in/out 次数を
#   チャンク bincount で計算し、indeg.npy / outdeg.npy / full_stats.json を書く。
# - 実体は wu.stats.full_stats(低 RAM の memmap + チャンク走査)。ここでは
#   ステージ契約(入出力・パラメータ)の宣言と委譲のみを行う。
#
# 注意:
# - wu.stats.full_stats への委譲(実装・出力は単一)。

from __future__ import annotations

from ..pipeline.stage import Param, stage
from ..stats import full_stats


@stage(
    name="stats",
    title="全グラフ次数統計・ハブ検出(チャンク bincount、低 RAM)",
    group="canonical",
    params=(
        Param("titles_needed", int, 200,
              "full_stats.json に記録する上位ハブのタイトル件数"),
    ),
    inputs=("graph.edges_ns0", "parsed.article_ids"),
    outputs=("graph.full_stats", "graph.indeg", "graph.outdeg"),
)
def run_stats(ctx) -> None:
    full_stats(ctx.dirs, titles_needed=ctx.params["titles_needed"])
