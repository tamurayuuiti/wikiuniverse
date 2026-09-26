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

    # ---- version-skew regression: pre-v2 catalog (macros without is_dust,
    #      galaxies without display_class) must still lay out with a warning
    import pyarrow as _pa
    _mpath = os.path.join(final, "macros.parquet")
    _mt = pq.read_table(_mpath)
    pq.write_table(_mt.drop(["is_dust"]), _mpath)
    _gpath = os.path.join(final, "galaxies.parquet")
    _gt = pq.read_table(_gpath)
    pq.write_table(_gt.drop(["display_class"]), _gpath)
    out_skew = run_script("layout_global.py", "--dim", "2", "--galaxy-tag", "res1_s")
    assert "pre-v2" in out_skew, "skew warning not emitted"
    # restore v2 catalog files (skew block degraded them in place)
    run_script("build_galaxy_catalog.py", "--galaxy-tag", "res1_s",
               "--macro-tag", "res1", "--top-neighbors", "4", "--top-pairs", "100")

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
    assert set(lm["quality"]) >= {"macro_native_overlap_frac", "galaxy_spill_frac",
                                  "galaxy_spill_count", "macro_proj_overlap"}, lm
    assert set(lm["quality"]["macro_proj_overlap"]) >= {"top_z", "side_x", "random_mean"}
    assert os.path.exists(os.path.join(lay, "preview.png"))
    assert any(f.startswith("preview_macro2d") and f.endswith(".html")
               for f in os.listdir(lay)), os.listdir(lay)

    # ---- full-3D arm (macro-dim 3) into its own subdir
    run_script("layout_global.py", "--dim", "3", "--macro-dim", "3",
               "--out-sub", "3d", "--galaxy-tag", "res1_s")
    lay3 = os.path.join(BASE, "layout", "3d")
    lm3 = json.load(open(os.path.join(lay3, "layout_meta.json"), encoding="utf-8"))
    assert lm3["mode"]["macro_dim"] == 3
    # --- 3D containment regression: every placed non-dust galaxy center must lie
    #     inside its macro sphere (catches z-composition bugs like the 2026-09-26 one)
    gpos3 = pq.read_table(os.path.join(lay3, "galaxy_positions.parquet")).to_pydict()
    mpos3 = pq.read_table(os.path.join(lay3, "macro_positions.parquet")).to_pydict()
    mxyz = _np.stack([mpos3["x"], mpos3["y"], mpos3["z"]], axis=1)
    mr = _np.asarray(mpos3["radius"])
    gxyz = _np.stack([gpos3["x"], gpos3["y"], gpos3["z"]], axis=1)
    gr = _np.asarray(gpos3["radius"])
    placed_mask = gr > 0
    mi = _np.asarray(gpos3["macro_id"])[placed_mask]
    d3 = _np.linalg.norm(gxyz[placed_mask] - mxyz[mi], axis=1)
    assert (d3 <= mr[mi] * 1.15).mean() > 0.98, \
        f"galaxies outside macro spheres in 3D: {(d3 > mr[mi] * 1.15).sum()}"
    assert os.path.exists(os.path.join(lay3, "preview.png"))
    assert any(f.startswith("preview_macro3d") and f.endswith(".html")
               for f in os.listdir(lay3)), os.listdir(lay3)

    # ---- local (intra-galaxy) article layout, batch range + merge
    run_script("layout_local.py", "--galaxies", "0-11", "--galaxy-tag", "res1_s",
               "--layout-sub", "")
    run_script("layout_local.py", "--galaxies", "12-9999", "--galaxy-tag", "res1_s",
               "--layout-sub", "", "--preview-galaxy", "0")
    assert os.path.exists(os.path.join(BASE, "layout", "preview_galaxy_0.png"))
    apq = os.path.join(BASE, "layout", "article_positions.parquet")
    assert os.path.exists(apq), "merged article positions missing"
    apt = pq.read_table(apq).to_pydict()
    assert len(apt["page_id"]) == 60
    P = _np.stack([apt["x"], apt["y"], apt["z"]], axis=1)
    assert _np.isfinite(P).all()
    C3 = _np.stack([gpos["x"], gpos["y"], gpos["z"]], axis=1)
    R3 = _np.asarray(gpos["radius"])
    gid = _np.asarray(apt["galaxy_id"])
    d = _np.linalg.norm(P - C3[gid], axis=1)
    assert (d <= _np.maximum(R3[gid] * 1.25, 1e-6)).all(), "article outside galaxy ball"

    # ---- viewer tiles export
    run_script("export_viewer_tiles.py", "--galaxy-tag", "res1_s", "--layout-sub", "")
    sp = os.path.join(BASE, "spatial")
    boot = json.load(open(os.path.join(sp, "bootstrap.json"), encoding="utf-8"))
    assert len(boot["galaxies"]) == G
    assert len(boot["macros"]) == len(m["macro_id"])   # ALL macro ids (pairs index raw)
    t0f = os.path.join(sp, "tiles", "gal_000000.bin")
    assert os.path.exists(t0f)
    import struct
    with open(t0f, "rb") as fh:
        nn, ne, nx = struct.unpack("<III", fh.read(12))
    assert nn > 0 and os.path.getsize(t0f) == 12 + nn * 12 + ne * 8 + nx * 12
    assert os.path.exists(os.path.join(sp, "tiles", "gal_000000.json"))
    assert os.path.exists(os.path.join(sp, "tiles_meta.json"))


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
