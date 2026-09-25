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

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "synth_data")

# ---------------------------------------------------------------- fixtures --

def esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace("'", "\\'")


def build_fixtures():
    if os.path.exists(BASE):
        shutil.rmtree(BASE)
    dump = os.path.join(BASE, "dump")
    os.makedirs(dump)

    # --- pages: 60 articles 1000..1059, 4 ns0 redirects 2000..2003, 1 ns1 redirect, others
    art_titles = {}
    for i in range(60):
        art_titles[1000 + i] = f"記事_{i:02d}"
    art_titles[1050] = "東京_(曖昧さ回避)"
    art_titles[1051] = "日本の鉄道駅一覧"
    art_titles[1052] = "2024年"
    art_titles[1059] = "テスト'記事"  # apostrophe title

    redir = {2000: "リダイレクト元A", 2001: "リダイレクト元B", 2002: "リダイレクト元C", 2003: "宙ぶらりん"}
    rd_target = {2000: art_titles[1000], 2001: art_titles[1001], 2002: "リダイレクト元A", 2003: "存在しない記事X"}

    rows = []
    for pid, t in sorted(art_titles.items()):
        rows.append(f"({pid},0,'{esc(t)}',0,0,0.5,'20260101000000','20260101000000',1,100,'wikitext',NULL)")
    for pid, t in sorted(redir.items()):
        rows.append(f"({pid},0,'{esc(t)}',1,0,0.5,'20260101000000','20260101000000',1,50,'wikitext',NULL)")
    rows.append("(2004,1,'Sandbox_r',1,0,0.5,'20260101000000','20260101000000',1,50,'wikitext',NULL)")
    rows.append("(3000,1,'ノートX',0,0,0.5,'20260101000000','20260101000000',1,50,'wikitext',NULL)")
    rows.append("(3001,14,'Category:テスト',0,0,0.5,'20260101000000','20260101000000',1,50,'wikitext',NULL)")
    with gzip.open(os.path.join(dump, "jawiki-latest-page.sql.gz"), "wb") as f:
        f.write(b"CREATE TABLE `page` (\n `page_id` int\n) ENGINE=InnoDB;\nINSERT INTO `page` VALUES\n")
        f.write((",\n".join(rows) + ";\n").encode())

    # --- linktarget: all article titles, redirect titles, red links, ns14, ns1
    lt = {}
    nxt = 1
    for t in list(art_titles.values()) + list(redir.values()) + ["存在しない記事X", "赤リンクA", "赤リンクB"]:
        lt[t] = nxt; nxt += 1
    lt["Category:テスト"] = nxt; nxt += 1
    lt["ノートX"] = nxt; nxt += 1
    rows = []
    for t, i in lt.items():
        ns = 14 if t.startswith("Category:") else (1 if t == "ノートX" else 0)
        rows.append(f"({i},{ns},'{esc(t)}')")
    with gzip.open(os.path.join(dump, "jawiki-latest-linktarget.sql.gz"), "wb") as f:
        f.write(b"CREATE TABLE `linktarget` (\n `lt_id` bigint\n) ENGINE=InnoDB;\nINSERT INTO `linktarget` VALUES\n")
        f.write((",\n".join(rows) + ";\n").encode())

    # --- redirect table
    rows = []
    for pid, t in sorted(redir.items()):
        rows.append(f"({pid},0,'{esc(rd_target[pid])}','','')")
    rows.append("(2004,1,'Sandbox','','')")
    with gzip.open(os.path.join(dump, "jawiki-latest-redirect.sql.gz"), "wb") as f:
        f.write(b"CREATE TABLE `redirect` (\n `rd_from` int\n) ENGINE=InnoDB;\nINSERT INTO `redirect` VALUES\n")
        f.write((",\n".join(rows) + ";\n").encode())

    # --- pagelinks: designed edges
    def L(t):  # title -> lt_id
        return lt[t]

    edges = []          # (pl_from, pl_from_ns, lt_id) expected-kept bookkeeping below
    kept_expect = []
    # cluster chains A(1000-1019) B(1020-1039) C(1040-1059)
    for base in (1000, 1020, 1040):
        for i in range(19):
            edges.append((base + i, 0, L(art_titles[base + i + 1])))
            kept_expect.append((base + i, base + i + 1))
    # cross-cluster
    for s, d in [(1000, 1020), (1020, 1040), (1040, 1000), (1005, 1025)]:
        edges.append((s, 0, L(art_titles[d]))); kept_expect.append((s, d))
    # via redirect title (resolves to 1000)
    edges.append((1005, 0, L("リダイレクト元A"))); kept_expect.append((1005, 1000))
    # via redirect chain (2002 title -> A -> 記事_00)
    edges.append((1006, 0, L("リダイレクト元C"))); kept_expect.append((1006, 1000))
    # apostrophe title link
    edges.append((1001, 0, L("テスト'記事"))); kept_expect.append((1001, 1059))
    # red link
    edges.append((1007, 0, L("赤リンクA")))
    # dangling redirect
    edges.append((1008, 0, L("宙ぶらりん")))
    # self loop
    edges.append((1009, 0, L(art_titles[1009])))
    # non-ns0 target
    edges.append((1010, 0, L("Category:テスト")))
    # source is redirect page
    edges.append((2000, 0, L(art_titles[1005])))
    # source non-ns0
    edges.append((3000, 1, L(art_titles[1005])))
    edges.append((3001, 14, L(art_titles[1005])))

    rows = [f"({a},{b},{c})" for a, b, c in edges]
    with gzip.open(os.path.join(dump, "jawiki-latest-pagelinks.sql.gz"), "wb") as f:
        f.write(b"CREATE TABLE `pagelinks` (\n `pl_from` int\n) ENGINE=InnoDB;\nINSERT INTO `pagelinks` VALUES\n")
        f.write((",\n".join(rows) + ";\n").encode())
    return kept_expect, len(edges)


# ------------------------------------------------------------------- test --

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

    from wu.subsets import extract_subset, select_id_window, bfs_nodes, title_to_pid
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

    from wu.pipeline import analyze_subset
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
