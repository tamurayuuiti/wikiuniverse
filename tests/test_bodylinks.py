"""Regression test for wu/xmlparse.build_body_edges (E3 body-text link graph).

Re-uses the synthetic SQL fixture universe from test_synthetic (記事_00..59 +
redirects) and adds a small MediaWiki-style XML dump (WITH default xmlns, like
real dumps) exercising:
  - alias links [[T|alias]], anchors [[T#sec]], duplicates
  - redirect resolution + self-link drop after resolution
  - [[Category:...]] / [[en:...]] prefix skips
  - <!-- comment -->, <nowiki>, <includeonly> suppression
  - links inside template CALL parameters and <ref> are KEPT (own source)
  - ns != 0 pages and <redirect/> pages skipped
  - ASCII ucfirst normalization
"""
import bz2
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from fixture import BASE, build_fixtures  # noqa: E402

XML = """<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/" version="0.11">
  <page>
    <title>記事_00</title><ns>0</ns><id>1000</id>
    <revision><text xml:space="preserve">本文 [[記事_01]] と [[記事_01|エイリアス]] と
[[リダイレクト元A]](解決後自分自身) と [[記事_02#セクション]] と [[Category:テスト]] と
[[en:Article]] と &lt;!-- [[記事_03]] --&gt; と &lt;nowiki&gt;[[記事_04]]&lt;/nowiki&gt; と
{{Navbox|param=[[記事_05]]}} と &lt;ref&gt;[[記事_06]]&lt;/ref&gt; と
&lt;includeonly&gt;[[記事_07]]&lt;/includeonly&gt; と [[記事_08]] [[記事_08]]
そして [[存在しない記事ZZZ]]</text></revision>
  </page>
  <page>
    <title>記事_01</title><ns>0</ns><id>1001</id>
    <revision><text xml:space="preserve">相互リンク [[記事_00]]</text></revision>
  </page>
  <page>
    <title>リダイレクト元A</title><ns>0</ns><id>2000</id>
    <redirect title="記事_00" /><revision><text>#REDIRECT [[記事_00]]</text></revision>
  </page>
  <page>
    <title>ノート:記事_02</title><ns>1</ns><id>3000</id>
    <revision><text>[[記事_05]] は無視されるべき</text></revision>
  </page>
  <page>
    <title>記事_09</title><ns>0</ns><id>1009</id>
    <revision><text xml:space="preserve"></text></revision>
  </page>
</mediawiki>
"""


def main():
    if os.path.exists(BASE):
        import shutil
        shutil.rmtree(BASE)
    build_fixtures()

    from wu.paths import Dirs
    from wu.dumpio import FILES
    from wu.sqlparse import (build_linktarget_artifacts, build_page_artifacts,
                             build_redirect_artifacts)
    from wu.xmlparse import build_body_edges, normalize_target, _prefix_skipped

    # --- unit checks
    assert normalize_target("lower case".encode()) == b"Lower_case"
    assert normalize_target(b"A__B  C#sec") == b"A_B_C"
    assert normalize_target(b":Category:X") == b"Category:X"
    assert normalize_target(b"#anchor") is None
    assert _prefix_skipped(b"Category:X") and _prefix_skipped(b"en:X")
    assert _prefix_skipped(b"\xe3\x82\xab\xe3\x83\x86\xe3\x82\xb4\xe3\x83\xaa:X")  # カテゴリ:X
    assert not _prefix_skipped(b"CSI:\xe7\xa7\x91\xe5\xad\xa6\xe6\x8d\x9c\xe6\x9f\xbb\xe7\x8f\xad")  # CSI:科学捜査班

    dirs = Dirs(BASE)
    dirs.ensure_core()
    build_page_artifacts(dirs.dump_file(FILES["page"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_linktarget_artifacts(dirs.dump_file(FILES["linktarget"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_redirect_artifacts(dirs.dump_file(FILES["redirect"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)

    xml_path = os.path.join(BASE, "dump", "mini-articles.xml.bz2")
    with bz2.open(xml_path, "wb") as f:
        f.write(XML.encode("utf-8"))

    out_bin = os.path.join(dirs.graph, "edges_body_directed.bin")
    ck = os.path.join(dirs.graph, "body_checkpoint.json")
    c = build_body_edges(xml_path, dirs.parsed, out_bin, ck, flush_every=2, log_every=0)

    # --- counter assertions
    assert c["pages_seen"] == 5, c
    assert c["ns_nonzero_skipped"] == 1, c
    assert c["redirect_pages_skipped"] == 1, c
    assert c["articles"] == 3, c                     # 記事_00, 記事_01, 記事_09
    assert c["links_prefixed_skipped"] == 2, c       # Category:テスト, en:Article
    assert c["links_self"] == 1, c                   # リダイレクト元A -> 記事_00 == self
    assert c["links_redlink"] == 1, c                # 存在しない記事ZZZ
    assert c["links_dup_within_article"] == 2, c     # 記事_01 alias dup + 記事_08 dup
    assert c["edges_kept"] == 6, c

    E = np.fromfile(out_bin, dtype=np.int32).reshape(-1, 2)
    got = sorted(map(tuple, E.tolist()))
    exp = sorted([(1000, 1001), (1000, 1002), (1000, 1005), (1000, 1006),
                  (1000, 1008), (1001, 1000)])
    assert got == exp, f"edges mismatch:\n got={got}\n exp={exp}"

    # --- strip_refs variant: ref link (記事_06) must disappear
    out_bin2 = os.path.join(dirs.graph, "edges_body_noref.bin")
    ck2 = os.path.join(dirs.graph, "body_checkpoint2.json")
    c2 = build_body_edges(xml_path, dirs.parsed, out_bin2, ck2,
                          flush_every=2, log_every=0, strip_refs=True)
    assert c2["edges_kept"] == 5, c2
    E2 = np.fromfile(out_bin2, dtype=np.int32).reshape(-1, 2)
    assert (1000, 1006) not in sorted(map(tuple, E2.tolist()))

    print("\n*** BODY-LINKS TEST PASSED ***")
    print({k: c[k] for k in ("articles", "links_found", "edges_kept",
                             "links_redlink", "links_prefixed_skipped")})


if __name__ == "__main__":
    main()
