import json
import os

BASE = os.environ.get("ARENA_WORKSPACE", "/home/user") + "/wikiuniverse/results"
runs = [
    ("bfs_geo_30k", "geo 30k raw"),
    ("bfs_geo_30k_hubweight", "geo 30k w=log"),
    ("bfs_geo_30k_hubw_deg1", "geo 30k w=deg^-1"),
    ("bfs_geo_30k_budget40", "geo 30k both-B40"),
    ("bfs_geo_30k_smart40", "geo 30k smart-B40"),
    ("bfs_mixed_30k", "mixed 30k raw"),
    ("bfs_geo_100k_budget60", "geo 100k both-B60"),
    ("bfs_geo_100k_smart40", "geo 100k smart-B40"),
    ("idwin_late_10k", "idwin 10k raw"),
]
print(f"{'run':24s} {'res':>4s} {'C':>5s} {'largest':>8s} {'medN':>7s} {'crossF':>7s} {'oR_p50':>7s} {'cond_p50':>8s} {'L2K':>4s} {'oR2_w':>7s} {'raw_crossF':>10s}")
for d, label in runs:
    p = os.path.join(BASE, d, "metrics.json")
    if not os.path.exists(p):
        print(f"{label:24s}  (missing)")
        continue
    m = json.load(open(p))
    g = m["global"]
    per = m["per_community"]
    sizes = [s for s in per["N_c"] if s > 0]
    import statistics
    med = statistics.median(sizes) if sizes else 0
    orr = [x for x in per["out_ratio_c"] if x is not None]
    orr.sort()
    o50 = orr[len(orr)//2] if orr else float("nan")
    cc = [x for x in per["conductance_c"] if x is not None]
    cc.sort()
    c50 = cc[len(cc)//2] if cc else float("nan")
    h = m.get("hierarchy")
    hk = h["global"]["n_clusters"] if h else "-"
    ho = h["global"]["mean_out_ratio2_edge_weighted"] if h else None
    raw = g.get("raw_eval", {}).get("cross_edge_fraction_raw")
    print(f"{label:24s} {m['meta']['resolution']:>4} {g['n_communities']:>5} "
          f"{g['largest_comm_share']:>8.3f} {med:>7.0f} {g['cross_edge_fraction']:>7.3f} "
          f"{o50:>7.3f} {c50:>8.3f} {str(hk):>4s} {(f'{ho:.3f}' if ho is not None else '-'):>7s} "
          f"{(f'{raw:.3f}' if raw is not None else '-'):>10s}")

print("\n=== mixed 30k: community labels (res primary) ===")
m = json.load(open(os.path.join(BASE, "bfs_mixed_30k", "metrics.json")))
per = m["per_community"]
order = sorted(range(len(per["N_c"])), key=lambda i: -per["N_c"][i])[:14]
for i in order:
    lab = ", ".join(m["community_labels"].get(str(i), [])[:4])
    print(f"  c{i:2d} N={per['N_c'][i]:5d} out_ratio={per['out_ratio_c'][i]:.3f} | {lab}")

print("\n=== mixed 30k hierarchy L2 ===")
h = m.get("hierarchy")
if h:
    for r in sorted(h["clusters"], key=lambda r: -r["N_nodes"]):
        print(f"  cluster{r['cluster']}: comms={r['n_comms']:2d} N={r['N_nodes']:6d} out_ratio2={r['out_ratio2']}")

print("\n=== geo 100k smart40: top communities ===")
m2 = json.load(open(os.path.join(BASE, "bfs_geo_100k_smart40", "metrics.json")))
per2 = m2["per_community"]
order2 = sorted(range(len(per2["N_c"])), key=lambda i: -per2["N_c"][i])[:10]
for i in order2:
    lab = ", ".join(m2["community_labels"].get(str(i), [])[:4])
    print(f"  c{i:2d} N={per2['N_c'][i]:6d} out_ratio={per2['out_ratio_c'][i]:.3f} cond={per2['conductance_c'][i]:.3f} | {lab}")
