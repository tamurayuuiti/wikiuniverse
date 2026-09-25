import json
import os

BASE = os.environ.get("ARENA_WORKSPACE", "/home/user") + "/wikiuniverse/results"

for run in ["bfs_geo_100k_raw", "bfs_mixed_30k_igraph"]:
    m = json.load(open(os.path.join(BASE, run, "metrics.json")))
    g, per, meta = m["global"], m["per_community"], m["meta"]
    print(f"########## {run} (res={meta['resolution']}, engine={meta.get('engine')}) ##########")
    for k in ("n_communities", "largest_comm_share", "top5_comm_share", "cross_edge_fraction",
              "mean_out_ratio_edge_weighted", "node_weighted_median_out_ratio"):
        print(f"  {k}: {g[k]}")
    print("  out_ratio pct:", g["out_ratio_percentiles"])
    print("  conductance pct:", g["conductance_percentiles"])
    print("  size pct:", g["size_percentiles"])
    h = m.get("hierarchy")
    if h:
        print("  L2 global:", h["global"])
        for r in sorted(h["clusters"], key=lambda r: -r["N_nodes"])[:8]:
            print(f"    cluster{r['cluster']}: comms={r['n_comms']:3d} N={r['N_nodes']:6d} "
                  f"out_ratio2={r['out_ratio2']:.3f}" if r["out_ratio2"] is not None else "")
    # share of nodes living in communities with out_ratio < 0.3 / < 0.5
    import numpy as np
    N = np.array(per["N_c"]); OR = np.array([x if x is not None else np.nan for x in per["out_ratio_c"]])
    tot = N.sum()
    for th in (0.3, 0.5):
        share = N[(OR < th)].sum() / tot
        print(f"  node share in comms with out_ratio<{th}: {share:.3f}")
    print()
