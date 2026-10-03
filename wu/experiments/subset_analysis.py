# wu/experiments/subset_analysis.py — サブセットのエンドツーエンド解析(第1期の実験系)
#
# 責務:
# - 1 つのサブセット(graph/subsets/<name>/)に対して Leiden の resolution スイープ
#   → 一次選択 → 指標 → ハブ解析 → L2 階層 → プロット/レポートを一括実行する
#   (analyze_subset)。出力は community/<subset名>/ の report.md + metrics.json 他。
#
# 注意:
# - 挙動は test_synthetic が担保する。ハブ抑制ユーティリティは
#   wu/experiments/hubsup.py にある。
# - 位置づけは「実験系(experiment)」: 第1期のサブセット検証装置であり、
#   全グラフ正式チェーン(detect/subdivide/metrics/…)とは別物。分類は
#   設計決定 D-g(knowledge/work/pipeline-cleanup.md)参照。

from __future__ import annotations

import datetime
import json
import os
import time

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ..community import analysis as an
from ..dumpio import read_json, write_json
from .hubsup import filter_edges_by_hub, prune_edges_degree_budget
from ..paths import Dirs


def _pick_primary(sweep_results, target_median=(200, 3000), ideal=800):
    """バンド内でコミュニティサイズ中央値が `ideal` に(対数スケールで)最も近い
    resolution を選ぶ。縮退した結果(median<50)はペナルティを受ける。"""
    best, best_d = None, 1e18
    for s in sweep_results:
        med = s["p50"]
        d = abs(np.log(max(med, 1) / ideal))
        if med < 50:
            score = 1000 + d
        elif target_median[0] <= med <= target_median[1]:
            score = d
        else:
            score = 100 + d
        if score < best_d:
            best, best_d = s, score
    return best


def analyze_subset(subset_dir: str, out_dir: str, resolutions=(0.5, 1.0, 2.0),
                   seed: int = an.SEED, full_stats_json: str | None = None,
                   dirs: Dirs | None = None, hub_indeg_cap=None, hub_outdeg_cap=None,
                   degree_budget: int | None = None, hub_weight: str | None = None,
                   degree_budget_mode: str = "smart", engine: str = "igraph"):
    t_start = time.time()
    os.makedirs(out_dir, exist_ok=True)
    n, nodes, internal, node_stats = an.load_subset(subset_dir)
    E_raw = an.undirected_unique(internal)
    del internal
    os.makedirs(out_dir, exist_ok=True)
    E_raw_path = os.path.join(out_dir, "E_raw.npy")
    np.save(E_raw_path, E_raw)
    del E_raw
    an.trim()
    E_raw = np.load(E_raw_path, mmap_mode="r")  # file-backed: reclaimable under pressure
    E = E_raw
    subset_meta = read_json(os.path.join(subset_dir, "subset_meta.json"), {}) or {}
    if full_stats_json is None and dirs is not None:
        full_stats_json = dirs.full_stats
    hub_filter_info = None
    prune_info = None
    if degree_budget:
        E, prune_info = prune_edges_degree_budget(E, degree_budget, seed=seed,
                                                  mode=degree_budget_mode)
        subset_meta = dict(subset_meta)
        subset_meta["degree_budget"] = prune_info
        print(f"[degree-budget {degree_budget_mode}] B={degree_budget}: kept {prune_info['kept']:,} "
              f"({prune_info['kept_frac']:.1%}) edges")
    if dirs and (hub_indeg_cap or hub_outdeg_cap):
        E, hub_filter_info = filter_edges_by_hub(E, nodes, dirs, hub_indeg_cap, hub_outdeg_cap)
        subset_meta = dict(subset_meta)
        subset_meta["hub_filter"] = {"indeg_cap": hub_indeg_cap, "outdeg_cap": hub_outdeg_cap,
                                     **hub_filter_info}
        print(f"[hub-filter] indeg<={hub_indeg_cap} outdeg<={hub_outdeg_cap}: "
              f"dropped {hub_filter_info['edges_dropped']:,} edges "
              f"({hub_filter_info['edges_dropped_frac']:.1%}), E={len(E):,}")
    edge_w = None
    if hub_weight and dirs:
        article_ids = np.load(dirs.article_ids)
        pid = np.asarray(nodes["page_id"], dtype=np.int64)
        pos = np.clip(np.searchsorted(article_ids, pid), 0, len(article_ids) - 1)
        deg = (np.load(dirs.indeg)[pos].astype(np.float64)
               + np.load(dirs.outdeg)[pos].astype(np.float64))
        du, dv = deg[E[:, 0]], deg[E[:, 1]]
        if hub_weight == "log":
            edge_w = 1.0 / (np.log(2.0 + du) * np.log(2.0 + dv))
            formula = "1/(log(2+d_u)*log(2+d_v))"
        elif hub_weight.startswith("deg"):
            alpha = float(hub_weight[3:] or 1.0)
            edge_w = 1.0 / np.power(np.maximum(du, 1.0) * np.maximum(dv, 1.0), alpha)
            formula = f"1/(d_u*d_v)^{alpha}"
        else:
            raise ValueError(f"unknown hub_weight mode: {hub_weight}")
        subset_meta = dict(subset_meta)
        subset_meta["hub_weight"] = {"formula": formula, "mode": hub_weight,
                                     "w_p5": float(np.percentile(edge_w, 5)),
                                     "w_p50": float(np.percentile(edge_w, 50)),
                                     "w_p95": float(np.percentile(edge_w, 95)),
                                     "w_max": float(edge_w.max())}
        print(f"[hub-weight] {formula}; w p5/p50/p95/max = {subset_meta['hub_weight']['w_p5']:.2e}/"
              f"{subset_meta['hub_weight']['w_p50']:.2e}/{subset_meta['hub_weight']['w_p95']:.2e}/"
              f"{subset_meta['hub_weight']['w_max']:.2e}")
    print(f"[analyze] {subset_dir}: n={n:,} E_undirected={len(E):,} "
          f"ext_out={int(node_stats['ext_out'].sum()):,}")

    # ---- resolution sweep
    sweep = []
    memb_by_res = {}
    for res in resolutions:
        t0 = time.time()
        memb, quality, modularity = an.run_leiden(n, E, resolution=res, seed=seed,
                                                  weights=edge_w, engine=engine)
        per, glob = an.community_metrics(n, E, memb, node_stats, nodes)
        sizes = per["N_c"][per["N_c"] > 0]
        sweep.append({
            "resolution": res, "n_communities": glob["n_communities"],
            "largest_comm_share": glob["largest_comm_share"],
            "top5_comm_share": glob["top5_comm_share"],
            "cross_edge_fraction": glob["cross_edge_fraction"],
            "mean_out_ratio_edge_weighted": glob["mean_out_ratio_edge_weighted"],
            "p25": float(np.percentile(sizes, 25)), "p50": float(np.percentile(sizes, 50)),
            "p75": float(np.percentile(sizes, 75)),
            "quality": float(quality), "modularity": float(modularity),
            "secs": round(time.time() - t0, 1),
        })
        memb_by_res[res] = memb
        an.trim()
        print(f"  res={res}: C={glob['n_communities']} largest={glob['largest_comm_share']:.3f} "
              f"median_size={sweep[-1]['p50']:.0f} cross_frac={glob['cross_edge_fraction']:.3f} "
              f"({sweep[-1]['secs']}s)")

    primary = _pick_primary(sweep)
    res = primary["resolution"]
    memb = memb_by_res[res]
    print(f"[analyze] primary resolution={res} (median size {primary['p50']:.0f})")

    # ---- full metrics at primary resolution
    per, glob = an.community_metrics(n, E, memb, node_stats, nodes)
    if E is not E_raw and len(E_raw):
        u, v = E_raw[:, 0], E_raw[:, 1]
        same = memb[u] == memb[v]
        glob["raw_eval"] = {
            "n_edges_raw": int(len(E_raw)),
            "cross_edge_fraction_raw": float((~same).sum() / len(E_raw)),
        }
        del u, v, same
        an.trim()
    labels = an.community_labels(n, E, memb, nodes, per, top_n=3)
    top_pairs = an.top_inter_pairs(E, memb, top_k=20)
    hub_rows, hub_stats = an.hub_analysis(n, E, memb, nodes, node_stats, top_k=30)
    hier = an.hierarchy_level2(memb, E, per, resolution=1.0, seed=seed)

    # ---- persist
    pid = np.asarray(nodes["page_id"], dtype=np.int64)
    cl = np.asarray(hier["comm_to_cluster"], dtype=np.int32) if hier else np.full(glob["n_communities"], -1, np.int32)
    tbl = pa.table({
        "idx": pa.array(np.arange(n, dtype=np.int32)),
        "page_id": pa.array(pid),
        "title": pa.array(nodes["title"], type=pa.string()),
        "comm": pa.array(memb),
        "cluster": pa.array(cl[memb]),
    })
    pq.write_table(tbl, os.path.join(out_dir, "communities.parquet"), compression="zstd")

    per_json = {}
    for k, v in per.items():
        arr = np.asarray(v)
        per_json[k] = [None if (isinstance(x, float) and np.isnan(x)) else (int(x) if arr.dtype.kind == "i" else float(x)) for x in arr.tolist()]

    metrics = {
        "meta": {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "subset_dir": subset_dir, "seed": seed, "resolution": res,
            "hub_indeg_cap": hub_indeg_cap, "hub_outdeg_cap": hub_outdeg_cap, "hub_weight": hub_weight,
            "engine": engine,
            "hub_filter_info": hub_filter_info, "degree_budget_info": prune_info,
            "resolutions_tried": list(resolutions),
            "total_secs": round(time.time() - t_start, 1),
        },
        "subset_meta": subset_meta,
        "sweep": sweep,
        "global": glob,
        "per_community": per_json,
        "community_labels": {str(k): v for k, v in labels.items()},
        "top_inter_pairs": top_pairs,
        "hub_rows": hub_rows,
        "hub_stats": hub_stats,
        "hierarchy": hier,
        "full_graph_stats": read_json(full_stats_json, None) if full_stats_json else None,
    }
    write_json(os.path.join(out_dir, "metrics.json"), metrics)

    from .report import make_plots, write_report
    plots = make_plots(out_dir, per, glob, hier)
    ctx = {
        "subset_name": os.path.basename(subset_dir.rstrip("/")),
        "subset_meta": subset_meta, "glob": glob, "per": per, "labels": labels,
        "top_pairs": top_pairs, "hub_rows": hub_rows, "hub_stats": hub_stats,
        "hier": hier, "sweep": sweep,
        "meta": {**metrics["meta"], "dump_date": (subset_meta.get("dump_date") or "?")},
    }
    ctx["plots"] = plots
    write_report(os.path.join(out_dir, "report.md"), ctx)
    # cleanup work file (regenerable from subset)
    try:
        del E, E_raw
        os.remove(E_raw_path)
    except Exception:
        pass
    print(f"[analyze] done -> {out_dir} ({metrics['meta']['total_secs']}s)")
    return metrics
