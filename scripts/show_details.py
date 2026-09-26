import json
import os

BASE = os.environ.get("ARENA_WORKSPACE", "/home/user") + "/wikiuniverse/results"

for run in ["bfs_geo_100k_raw", "bfs_mixed_30k_igraph", "bfs_geo_30k"]:
    m = json.load(open(os.path.join(BASE, run, "metrics.json"), encoding="utf-8"))
    per = m["per_community"]
    print(f"### {run} res={m['meta']['resolution']}")
    import numpy as np
    N = np.array(per["N_c"])
    br = np.array([x for x in per["boundary_node_ratio_c"]])
    ba = np.array(per["boundary_any_nodes_c"])
    w = N / max(1, N.sum())
    print(f"  boundary_node_ratio: p25={np.nanpercentile(br,25):.3f} p50={np.nanpercentile(br,50):.3f} p75={np.nanpercentile(br,75):.3f} node-weighted={np.nansum(br*w):.3f}")
    print(f"  boundary_any nodes total share={ba.sum()/max(1,N.sum()):.3f}")
    tp = m["top_inter_pairs"][:8]
    lab = m["community_labels"]
    for p in tp:
        a, b = str(p["comm_a"]), str(p["comm_b"])
        print(f"  pair ({p['comm_a']},{p['comm_b']}) w={p['edges']:7d} | {', '.join(lab.get(a,[])[:2])} <-> {', '.join(lab.get(b,[])[:2])}")
    hs = m["hub_stats"]
    print(f"  hub: top1%_edge_share={hs['top1pct_internal_edge_incidence_share']:.3f} max_deg={hs['max_internal_degree']}")
    print(f"  top hubs:", ", ".join(h["title"] for h in m["hub_rows"][:8]))
    print()
