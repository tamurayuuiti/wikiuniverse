# tests/fixture.py — 合成テストデータ(小さな偽 Wikipedia ダンプ)の共有ビルダー
#
# 責務:
# - 全テストが共有する fixture(60 記事規模の page/linktarget/redirect/pagelinks/
#   categorylinks ダンプ)を tests/synth_data/ へ構築する。エスケープ・リダイレクト
#   チェーン/ダングリング・赤リンク・セルフループ・非 ns0 行などの境界ケースを
#   意図的に含める(解析系のテストはこのデータで網羅性を担保する)。
#
# 注意:
# - synth_data/ は実行時に再生成される gitignore 対象の生成物。テストは先頭で
#   rmtree → build_fixtures して使う(実行間の状態を持ち越さない)。
# - 旧 test_synthetic.py からの抽出(2026-10-02、テスト再編)。中身は無変更で、
#   6 本のテストがここから import する( fixture = テストデータ配置の単一の真実源)。
import gzip
import os
import shutil

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
    rows.append("(3002,14,'Category:テスト2',0,0,0.5,'20260101000000','20260101000000',1,50,'wikitext',NULL)")
    with gzip.open(os.path.join(dump, "jawiki-latest-page.sql.gz"), "wb") as f:
        f.write(b"CREATE TABLE `page` (\n `page_id` int\n) ENGINE=InnoDB;\nINSERT INTO `page` VALUES\n")
        f.write((",\n".join(rows) + ";\n").encode())

    # --- linktarget: all article titles, redirect titles, red links, ns14, ns1
    lt = {}
    nxt = 1
    for t in list(art_titles.values()) + list(redir.values()) + ["存在しない記事X", "赤リンクA", "赤リンクB"]:
        lt[t] = nxt; nxt += 1
    lt["Category:テスト"] = nxt; nxt += 1
    lt["Category:テスト2"] = nxt; nxt += 1
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
    # --- categorylinks fixture
    def CLT(t):
        return lt[t]
    cl_rows = []
    def cl(src, cat, typ="page"):
        cl_rows.append(f"({src},'あんはさんと\\nソート','2026-01-01 00:00:00','あんはさんと','{typ}',1,{CLT(cat)})")
    cl(1000, "Category:テスト"); cl(1001, "Category:テスト"); cl(1004, "Category:テスト")
    cl(1002, "Category:テスト2"); cl(1003, "Category:テスト2")
    cl(2000, "Category:テスト")                      # redirect src -> not article
    cl(1005, "Category:テスト", typ="subcat")        # skipped
    cl(1006, "Category:テスト", typ="file")          # skipped
    with gzip.open(os.path.join(dump, "jawiki-latest-categorylinks.sql.gz"), "wb") as f:
        f.write(b"CREATE TABLE `categorylinks` (\n `cl_from` int\n) ENGINE=InnoDB;\nINSERT INTO `categorylinks` VALUES\n")
        f.write((",\n".join(cl_rows) + ";\n").encode())

    return kept_expect, len(edges)


# ------------------------------------------------------------------- test --

