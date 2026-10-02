# wu/hubsup.py — ハブ抑制ユーティリティ(次数予算剪定・メガハブ端の除外)
#
# 責務:
# - レイアウト用グラフのハブ抑制 2 手法を提供する:
#   prune_edges_degree_budget(smart/both 次数予算剪定 = 決定 D8)と
#   filter_edges_by_hub(フルグラフ次数キャップによるメガハブ端の除外)。
# - 利用元: サブセット解析(wu/subset_analysis.py)とフルグラフ剪定
#   (python -m wu run prune)。
#
# 注意:
# - 本体は「構造決定」には不採用(生グラフが正史)。剪定グラフはレイアウト/
#   描画コスト削減と交差評価のための実験腕として使う。
# - 挙動・数値は test_synthetic / test_subdivide が担保する。
#   (今回は分割のみ = 差分レビュー可能性を優先)。

from __future__ import annotations

import numpy as np

from ..community import analysis as an
from ..paths import Dirs


def filter_edges_by_hub(E: np.ndarray, nodes: dict, dirs: Dirs,
                        indeg_cap: int | None = None, outdeg_cap: int | None = None):
    """Drop edges whose endpoint is a mega-hub in the FULL graph (layout-graph
    style hub suppression). Returns (E_filtered, info)."""
    if indeg_cap is None and outdeg_cap is None:
        return E, {"dropped": 0}
    article_ids = np.load(dirs.article_ids)
    pid = np.asarray(nodes["page_id"], dtype=np.int64)
    pos = np.clip(np.searchsorted(article_ids, pid), 0, len(article_ids) - 1)
    keep_n = np.ones(len(pid), bool)
    info = {}
    if indeg_cap is not None:
        indeg = np.load(dirs.indeg)[pos]
        info["nodes_indeg_capped"] = int((indeg > indeg_cap).sum())
        keep_n &= indeg <= indeg_cap
    if outdeg_cap is not None:
        outdeg = np.load(dirs.outdeg)[pos]
        info["nodes_outdeg_capped"] = int((outdeg > outdeg_cap).sum())
        keep_n &= outdeg <= outdeg_cap
    m = keep_n[E[:, 0]] & keep_n[E[:, 1]]
    info["edges_dropped"] = int((~m).sum())
    info["edges_dropped_frac"] = float((~m).sum() / max(1, len(E)))
    info["nodes_isolated_by_filter"] = int((~keep_n).sum())
    return E[m], info


def prune_edges_degree_budget(E: np.ndarray, budget: int, seed: int = an.SEED,
                              mode: str = "both"):
    """Degree-budget pruning of the layout graph.

    modes:
      both : keep edge iff random-rank < budget at BOTH endpoints
             (hard cap per node; fragments star-peripheries -> not recommended)
      smart: keep edge iff rank<budget at either endpoint OR either endpoint has
             degree<=budget. Rationale: galaxy structure lives in low-degree
             (stub<->local-hub) edges; only hub<->hub "intergalactic highways"
             are thinned. Preserves connectivity of the periphery.
    """
    rng = np.random.default_rng(seed)
    E = np.asarray(E)
    n_edges = len(E)
    if n_edges == 0:
        return E, {"budget": budget, "mode": mode, "kept": 0}
    n = int(E.max()) + 1
    deg = np.bincount(E[:, 0], minlength=n) + np.bincount(E[:, 1], minlength=n)
    prio = rng.random(n_edges)

    def _ranks(col):
        node = E[:, col]
        order = np.lexsort((prio, node))
        node_sorted = node[order]
        starts = np.searchsorted(node_sorted, np.arange(n), side="left")
        rank = np.empty(n_edges, dtype=np.int64)
        rank[order] = np.arange(n_edges) - starts[node_sorted]
        return node, rank

    if mode == "both":
        keep = np.ones(n_edges, bool)
        for col in (0, 1):
            _, rank = _ranks(col)
            keep &= rank < budget
    elif mode == "smart":
        keep = np.zeros(n_edges, bool)
        for col in (0, 1):
            node, rank = _ranks(col)
            keep |= (rank < budget) | (deg[node] <= budget)
    else:
        raise ValueError(f"unknown mode: {mode}")
    info = {"budget": budget, "mode": mode, "kept": int(keep.sum()),
            "kept_frac": float(keep.mean())}
    return E[keep], info
