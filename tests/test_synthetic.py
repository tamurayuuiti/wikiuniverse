"""Synthetic end-to-end test of the wu pipeline (no network).

Builds tiny fake SQL dumps that exercise: escapes, redirects (incl. chain +
dangling), red links, self loops, non-ns0 rows, then runs
parse -> edges -> stats -> subset -> analyze and asserts counters.
"""
import gzip
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fixture import BASE, build_fixtures, esc  # noqa: E402,F401


def main():
    kept_expect, n_rows = build_fixtures()

    from wu.dumpio import FILES, read_json
    from wu.paths import Dirs
    from wu.sqlparse import (build_linktarget_artifacts, build_page_artifacts,
                             build_redirect_artifacts)
    from wu.buildedges import build_edges

    dirs = Dirs(BASE)
    dirs.ensure_core()
    art = dirs.parsed
    d = dirs.dump
    meta_path = dirs.meta
    build_page_artifacts(dirs.dump_file(FILES["page"]), art, meta_path, chunk_bytes=1 << 20)
    build_linktarget_artifacts(dirs.dump_file(FILES["linktarget"]), art, meta_path, chunk_bytes=1 << 20)
    build_redirect_artifacts(dirs.dump_file(FILES["redirect"]), art, meta_path, chunk_bytes=1 << 20)

    # redirect chain check
    rd = np.load(os.path.join(art, "rd_map.npz"))
    rdm = dict(zip(rd["rd_from"].tolist(), rd["rd_final"].tolist()))
    assert rdm[2000] == 1000, rdm
    assert rdm[2002] == 1000, f"chain failed: {rdm}"   # C -> A -> 記事_00
    assert rdm[2003] == 0, f"dangling failed: {rdm}"

    ck = build_edges(dirs.dump_file(FILES["pagelinks"]), art,
                     dirs.edges_bin, dirs.edges_ckpt, chunk_bytes=1 << 20)
    assert ck["rows_total"] == n_rows, ck
    assert ck["from_non_ns0"] == 2, ck
    assert ck["src_is_redirect"] == 1, ck
    assert ck["target_redlink"] == 1, ck
    assert ck["target_dangling_redirect"] == 1, ck
    assert ck["self_loops"] == 1, ck
    assert ck["target_non_ns0"] == 1, ck
    assert ck["kept"] == len(kept_expect), f"kept {ck['kept']} != {len(kept_expect)}"

    E = np.fromfile(dirs.edges_bin, dtype=np.int32).reshape(-1, 2)
    got = sorted(map(tuple, E.tolist()))
    exp = sorted(kept_expect)
    assert got == exp, f"edge mismatch:\n got={got[:10]}\n exp={exp[:10]}"

    # resume path: rerun should be a no-op
    ck2 = build_edges(dirs.dump_file(FILES["pagelinks"]), art,
                      dirs.edges_bin, dirs.edges_ckpt, chunk_bytes=1 << 20)
    assert ck2["kept"] == ck["kept"]

    from wu.stats import full_stats
    full_stats(dirs)

    from wu.experiments.subsets import (extract_subset, select_id_window,
                                       bfs_nodes, title_to_pid)
    pids = select_id_window(art, 20, "late")
    assert pids.tolist() == list(range(1040, 1060))
    meta = extract_subset(dirs.edges_bin, pids, dirs.subset("idwin"),
                          {"strategy": "test"}, parsed_dir=art)
    # C-chain 19 edges internal; 1040->1000 outgoing; incoming: 1020->1040 and 1001->1059
    assert meta["n_edges_internal_directed"] == 19, meta
    assert meta["n_edges_outgoing"] == 1, meta
    assert meta["n_edges_incoming"] == 2, meta

    tpid = title_to_pid(art, "記事_05")
    assert tpid == 1005
    nodes, info = bfs_nodes(dirs.edges_bin, [1000], max_nodes=100, cap_per_node=10,
                            parsed_dir=art, graph_dir=dirs.graph)
    assert 1001 in set(nodes.tolist())

    from wu.experiments.subset_analysis import analyze_subset
    m = analyze_subset(dirs.subset("idwin"), dirs.community_run("idwin"),
                       resolutions=(0.5, 1.0), seed=42, dirs=dirs)
    adir = dirs.community_run("idwin")
    for f in ("metrics.json", "report.md", "communities.parquet", "size_hist.png"):
        assert os.path.exists(os.path.join(adir, f)), f
    print("\n*** ALL SYNTHETIC TESTS PASSED ***")
    print(json_summary(m))


def json_summary(m):
    g = m["global"]
    return {k: g[k] for k in ("n_communities", "n_nodes", "n_edges_internal_undirected",
                              "largest_comm_share", "cross_edge_fraction")}


if __name__ == "__main__":
    main()
