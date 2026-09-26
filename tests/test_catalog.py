"""E2E smoke test: synthetic fixture -> full chain -> galaxy catalog.

Chain: fixtures -> parse -> edges -> dedup -> detect -> subdivide -> metrics ->
cluster -> categories -> purity(v2) -> build_galaxy_catalog.
Asserts catalog structure (containment, pair aggregation, dust flags).
"""
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from test_synthetic import BASE, build_fixtures  # noqa: E402


def run_script(name, *args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", name),
                        "--base", BASE, *args], capture_output=True, text=True)
    assert r.returncode == 0, f"{name} failed:\n{r.stdout}\n{r.stderr}"
    return r.stdout


def main():
    if os.path.exists(BASE):
        import shutil
        shutil.rmtree(BASE)
    build_fixtures()

    from wu.buildedges import build_edges
    from wu.catparse import build_category_artifacts
    from wu.dumpio import FILES
    from wu.paths import Dirs
    from wu.sqlparse import (build_linktarget_artifacts, build_page_artifacts,
                             build_redirect_artifacts)

    dirs = Dirs(BASE)
    dirs.ensure_core()
    build_page_artifacts(dirs.dump_file(FILES["page"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_linktarget_artifacts(dirs.dump_file(FILES["linktarget"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_redirect_artifacts(dirs.dump_file(FILES["redirect"]), dirs.parsed, dirs.meta, chunk_bytes=1 << 20)
    build_edges(dirs.dump_file(FILES["pagelinks"]), dirs.parsed, dirs.edges_bin,
                dirs.edges_ckpt, chunk_bytes=1 << 20)
    build_category_artifacts(dirs.dump_file(FILES["categorylinks"]),
                             dirs.dump_file(FILES["linktarget"]), dirs.parsed, dirs.graph)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "run_full_leiden", os.path.join(ROOT, "scripts", "run_full_leiden.py"))
    rfl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rfl)

    rfl.cmd_dedup(dirs)
    rfl.cmd_detect(dirs, [1.0], force=True)                       # membership_res1
    rfl.cmd_subdivide(dirs, "res1", min_size=3, max_galaxy=6,
                      sub_resolution=1.0, depth=4, out_tag="res1_s")
    rfl.cmd_metrics(dirs, "res1_s")                               # per_community + pairs
    rfl.cmd_cluster(dirs, "res1_s")                               # clusters_node

    run_script("community_purity.py", "--tag", "res1_s", "--max-cat-freq", "20000")
    out = run_script("build_galaxy_catalog.py", "--galaxy-tag", "res1_s",
                     "--macro-tag", "res1", "--top-neighbors", "4", "--top-pairs", "100")

    # ---- assertions
    final = os.path.join(BASE, "final")
    for f in ("galaxies.parquet", "macros.parquet", "galaxy_pairs_topK.parquet",
              "macro_pairs.parquet", "catalog_meta.json"):
        assert os.path.exists(os.path.join(final, f)), f
    meta = json.load(open(os.path.join(final, "catalog_meta.json"), encoding="utf-8"))
    assert meta["containment_violations"] == 0, meta
    assert meta["n_articles"] == 60, meta

    import pyarrow.parquet as pq
    g = pq.read_table(os.path.join(final, "galaxies.parquet")).to_pydict()
    G = len(g["galaxy_id"])
    assert G == meta["n_galaxies"]
    assert sum(g["n_articles"]) == 60
    assert sum(g["is_dust"]) == meta["n_dust"]
    # every non-dust galaxy with edges has neighbors string; weights positive
    for i in range(G):
        if g["n_neighbors"][i] > 0:
            assert g["top_neighbors"][i] != ""
            for part in g["top_neighbors"][i].split(","):
                gid, wgt = part.split(":")
                assert 0 <= int(gid) < G and int(wgt) > 0

    m = pq.read_table(os.path.join(final, "macros.parquet")).to_pydict()
    assert sum(m["n_articles"]) == 60
    mp = pq.read_table(os.path.join(final, "macro_pairs.parquet")).to_pydict()
    gp = pq.read_table(os.path.join(final, "galaxy_pairs_topK.parquet")).to_pydict()
    if len(mp["w"]):
        assert max(mp["w"]) >= min(mp["w"])
    if len(gp["w"]):
        assert gp["w"][0] >= gp["w"][-1]  # sorted desc

    # ---- catalog v2 curation columns
    assert "display_class" in g and "name_source" in g
    assert set(g["display_class"]) <= {"galaxy", "medium", "dust"}
    assert sum(g["display_class"][i] == "dust" for i in range(G)) == meta["n_dust"]

    # ---- global layout smoke (dim=2 for speed)
    out2 = run_script("layout_global.py", "--dim", "2", "--galaxy-tag", "res1_s")
    lay = os.path.join(BASE, "layout")
    for f in ("galaxy_positions.parquet", "macro_positions.parquet",
              "layout_meta.json", "preview.png"):
        assert os.path.exists(os.path.join(lay, f)), f
    gpos = pq.read_table(os.path.join(lay, "galaxy_positions.parquet")).to_pydict()
    assert len(gpos["galaxy_id"]) == G
    import numpy as _np
    xyz = _np.stack([gpos["x"], gpos["y"]])
    assert _np.isfinite(xyz).all(), "non-finite coords"
    assert _np.asarray(gpos["radius"]).min() >= 0
    lm = json.load(open(os.path.join(lay, "layout_meta.json"), encoding="utf-8"))
    assert lm["n_galaxies"] == G

    # purity v2 outputs present with new schema
    pur = pq.read_table(os.path.join(dirs.community, "full", "purity_res1_s.parquet")).to_pydict()
    assert "name" in pur and "top1_share_filt" in pur
    pjson = json.load(open(os.path.join(dirs.community, "full", "purity_res1_s.json"),
                           encoding="utf-8"))
    assert "top1_share_filt" in pjson and "nameable_share_nodes" in pjson

    print("\n*** CATALOG E2E TEST PASSED ***")
    print(f"galaxies={G} macros={len(m['macro_id'])} dust={meta['n_dust']} "
          f"macro_pairs={len(mp['w'])} galaxy_pairs_topK={len(gp['w'])}")
    print(out.strip().splitlines()[-2])


if __name__ == "__main__":
    main()
