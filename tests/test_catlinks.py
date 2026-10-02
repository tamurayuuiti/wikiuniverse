"""E4 regression test: categorylinks parsing + community purity tool."""
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from fixture import BASE, build_fixtures  # noqa: E402


def main():
    if os.path.exists(BASE):
        import shutil
        shutil.rmtree(BASE)
    build_fixtures()

    from wu.catparse import build_category_artifacts
    from wu.dumpio import FILES
    from wu.paths import Dirs
    from wu.sqlparse import build_linktarget_artifacts, build_page_artifacts

    dirs = Dirs(BASE)
    dirs.ensure_core()
    build_page_artifacts(dirs.dump_file(FILES["page"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_linktarget_artifacts(dirs.dump_file(FILES["linktarget"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    counters = build_category_artifacts(dirs.dump_file(FILES["categorylinks"]),
                                        dirs.dump_file(FILES["linktarget"]),
                                        dirs.parsed, dirs.graph)

    assert counters["rows"] == 8, counters
    assert counters["type_page"] == 6, counters
    assert counters["type_subcat"] == 1, counters
    assert counters["type_file"] == 1, counters
    assert counters["cl_from_not_article"] == 1, counters   # redirect 2000
    assert counters["target_not_category"] == 0, counters
    assert counters["pairs_kept"] == 5, counters

    AC = np.fromfile(os.path.join(dirs.graph, "article_categories.bin"), dtype=np.int32).reshape(-1, 2)
    got = sorted(map(tuple, AC.tolist()))
    # compact idx = pid - 1000; cat_id: テスト=0, テスト2=1 (linktarget discovery order)
    exp = sorted([(0, 0), (1, 0), (4, 0), (2, 1), (3, 1)])
    assert got == exp, f"pairs mismatch: {got} != {exp}"

    # categories parquet titles
    import pyarrow.parquet as pq
    cats = pq.read_table(os.path.join(dirs.graph, "categories.parquet")).to_pydict()
    assert cats["title"] == ["Category:テスト", "Category:テスト2"], cats["title"]

    # ---- purity tool on a hand-made membership
    full = os.path.join(dirs.community, "full")
    os.makedirs(full, exist_ok=True)
    memb = np.full(60, 2, np.int32)
    memb[[0, 1, 4]] = 0    # all Category:テスト -> pure
    memb[[2, 3]] = 1       # all Category:テスト2 -> pure
    np.save(os.path.join(full, "membership_test.npy"), memb)

    r = subprocess.run([sys.executable, "-m", "wu", "--base", BASE,
                        "run", "purity", "--galaxy-tag", "test"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr
    agg = json.load(open(os.path.join(full, "purity_test.json"), encoding="utf-8"))
    assert agg["top1_share_raw"]["node_weighted_mean"] == 1.0, agg
    pur = pq.read_table(os.path.join(full, "purity_test.parquet")).to_pydict()
    assert pur["top1_title"][0] == "Category:テスト"
    assert pur["top1_title"][1] == "Category:テスト2"
    assert pur["name"][0] == "Category:テスト"          # tf-idf name (v2)
    assert pur["n_with_filtered_cats"][2] == 0          # comm2 has no categorized articles

    print("\n*** CATLINKS/PURITY TEST PASSED ***")
    print({k: counters[k] for k in ("rows", "pairs_kept")},
          "purity_mean=", agg["top1_share_raw"]["node_weighted_mean"])


if __name__ == "__main__":
    main()
