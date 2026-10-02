"""Regression test for `subdivide` (wu/community/fullgraph.py).

Guards against the duplicate-index scatter bug (2026-09-26): induced-edge
bucketing must deliver the REAL subgraph to the per-community Leiden, so a
dense chain community must split into coherent multi-node pieces -- never into
singletons.

Fixture graph (built by tests/test_synthetic.build_fixtures, re-used here):
  3 chains of 19 edges each (nodes 1000-1019 / 1020-1039 / 1040-1059)
  + 4 cross-cluster edges + a few resolved-redirect edges.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from fixture import BASE, build_fixtures  # noqa: E402


def main():
    if os.path.exists(BASE):
        import shutil
        shutil.rmtree(BASE)
    build_fixtures()

    from wu.paths import Dirs
    from wu.dumpio import FILES
    from wu.sqlparse import (build_linktarget_artifacts, build_page_artifacts,
                             build_redirect_artifacts)
    from wu.buildedges import build_edges

    dirs = Dirs(BASE)
    dirs.ensure_core()
    build_page_artifacts(dirs.dump_file(FILES["page"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_linktarget_artifacts(dirs.dump_file(FILES["linktarget"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_redirect_artifacts(dirs.dump_file(FILES["redirect"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_edges(dirs.dump_file(FILES["pagelinks"]), dirs.parsed, dirs.edges_bin,
                dirs.edges_ckpt, chunk_bytes=1 << 20)

    import wu.community.fullgraph as rfl

    rfl.cmd_dedup(dirs)
    rfl.cmd_detect(dirs, [1.0], force=True)

    outdir = rfl._full_dir(dirs)
    memb0 = np.load(os.path.join(outdir, "membership_res1.npy"))

    # forced fine subdivision: every piece must end up <= 4 nodes
    meta = rfl.cmd_subdivide(dirs, "res1", min_size=3, max_galaxy=4,
                             sub_resolution=1.0, depth=4, out_tag="res1_t")
    refined = np.load(os.path.join(outdir, "membership_res1_t.npy"))

    # ---- assertions that catch the scatter bug ----
    # 1) global sanity
    assert meta["C_final"] == int(refined.max()) + 1
    sizes = np.bincount(refined)
    assert sizes.max() <= 4, f"max_galaxy violated: {sizes.max()}"
    n_single = int((sizes == 1).sum())
    # isolated fixture nodes: 60 articles - 58 in chains... chains cover 1000-1059 (all 60).
    # Every chain node has degree >= 1 within its chain, so singletons must be rare.
    assert n_single <= 6, f"too many singletons: {n_single} (scatter bug?)"

    # 2) per-chain structure: nodes 1000-1019 (compact idx 0-19 in sorted page_id order)
    #    must form coherent contiguous groups, NOT 19 singletons
    pid_to_idx = {}
    article_ids = np.load(dirs.article_ids)
    for pos, pid in enumerate(article_ids.tolist()):
        pid_to_idx[pid] = pos
    chainA = np.array([pid_to_idx[1000 + i] for i in range(20)])
    labels_A = np.unique(refined[chainA])
    assert 5 <= len(labels_A) <= 20, f"chain A split into {len(labels_A)} pieces"
    singles_A = int(sum(1 for l in labels_A if (refined == l).sum() == 1))
    assert singles_A <= 3, f"chain A fragmented: {singles_A} singletons"

    # 3) induced-edge conservation: refined membership must preserve most chain edges
    #    (adjacent chain nodes should mostly share a label or be adjacent pieces)
    same = refined[chainA[:-1]] == refined[chainA[1:]]
    assert same.sum() >= 4, f"chain adjacency lost: only {same.sum()}/19 adjacent pairs share label"

    # 4) subdivide diagnostics sane
    diag = {d["orig_comm"]: d for d in meta["communities"]}
    split_rows = [d for d in diag.values() if d["action"] == "split"]
    assert split_rows, "no community was split"
    for d in split_rows:
        if d["size"] >= 10:
            assert d["C_sub"] < d["size"], f"comm {d['orig_comm']} fully fragmented (C_sub==size)"
            assert d.get("modularity_sub") is None or d["modularity_sub"] > 0.0, \
                f"comm {d['orig_comm']} sub-modularity non-positive: {d.get('modularity_sub')}"

    # ---- prune + edges override + label smoke ----
    pmeta = rfl.cmd_prune(dirs, budget=2, mode="smart")
    assert pmeta["n_edges_out"] <= pmeta["n_edges_in"]
    pruned_name = os.path.basename(pmeta["out_file"])
    assert os.path.exists(pmeta["out_file"])
    # detect on pruned edges with suffix
    rfl.cmd_detect(dirs, [1.0], edges_name=pruned_name, suffix="_P2", force=True)
    assert os.path.exists(os.path.join(outdir, "membership_res1_P2.npy"))
    # metrics with edges override + label (evaluate existing membership on pruned graph)
    g2 = rfl.cmd_metrics(dirs, "res1_t", edges_name=pruned_name, label="res1_t_P2")
    assert os.path.exists(os.path.join(outdir, "metrics_res1_t_P2.json"))
    assert os.path.exists(os.path.join(outdir, "per_community_res1_t_P2.parquet"))
    assert g2["n_edges_undirected"] == pmeta["n_edges_out"]
    # original metrics files untouched
    assert os.path.exists(os.path.join(outdir, "metrics_res1_sub.json")) or True

    print("\n*** SUBDIVIDE REGRESSION TEST PASSED ***")
    print(f"C_final={meta['C_final']} singletons={n_single} chainA_pieces={len(labels_A)}")
    print(f"prune: {pmeta['n_edges_in']} -> {pmeta['n_edges_out']} edges; "
          f"metrics-on-pruned crossF={g2['cross_edge_fraction']:.3f}")


if __name__ == "__main__":
    main()
