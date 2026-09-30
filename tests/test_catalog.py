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

    # ---- 保守サフィックスの語幹正規化(_stem_name 単体)
    spec_n = importlib.util.spec_from_file_location(
        "bgc_names", os.path.join(ROOT, "scripts", "build_galaxy_catalog.py"))
    bgc = importlib.util.module_from_spec(spec_n)
    spec_n.loader.exec_module(bgc)
    for _src, _want in [("シングル関連のスタブ項目", "シングル"),
                        ("アイドルに関するスタブ", "アイドル"),
                        ("山岳関連のスタブ項目", "山岳"),
                        ("神道関連のスタブ項目", "神道"),
                        ("サッカー選手に関するスタブ項目", "サッカー選手"),
                        ("野球に関する記事", "野球"),
                        ("天文学に関する記事の一覧", "天文学"),
                        ("地理座標系の一覧", "地理座標系")]:
        assert bgc._stem_name(_src) == _want, (_src, bgc._stem_name(_src))
    for _src in ["数学", "スタブ", "すべてのスタブ記事の一覧", "Xのスタブ"]:
        assert bgc._stem_name(_src) == "", _src  # 非該当/語幹短すぎ/ブラックリスト

    # ---- global layout smoke (v1.5 canonical: full 3D, sphere relax,
    #      bary placement for mediums AND unlinked galaxies, no baked lens)
    out2 = run_script("layout_global.py", "--galaxy-tag", "res1_s",
                      "--run", "r_main")

    # ---- version-skew regression: pre-v2 catalog (macros without is_dust,
    #      galaxies without display_class) must still lay out with a warning
    import pyarrow as _pa
    _mpath = os.path.join(final, "macros.parquet")
    _mt = pq.read_table(_mpath)
    pq.write_table(_mt.drop(["is_dust"]), _mpath)
    _gpath = os.path.join(final, "galaxies.parquet")
    _gt = pq.read_table(_gpath)
    pq.write_table(_gt.drop(["display_class"]), _gpath)
    out_skew = run_script("layout_global.py", "--galaxy-tag", "res1_s",
                          "--run", "r_main")
    assert "pre-v2" in out_skew, "skew warning not emitted"
    # restore v2 catalog files (skew block degraded them in place)
    run_script("build_galaxy_catalog.py", "--galaxy-tag", "res1_s",
               "--macro-tag", "res1", "--top-neighbors", "4", "--top-pairs", "100")

    lay = os.path.join(BASE, "layout", "r_main")
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
                                  "galaxy_spill_count", "macro_proj_overlap",
                                  "galaxy_overlap_frac", "galaxy_flat_mean",
                                  "galaxy_flat_p90", "galaxy_flat_n_macros",
                                  "macro_adj_recall_top5",
                                  "macro_adj_spearman"}, lm
    assert set(lm["quality"]["macro_proj_overlap"]) >= {"top_z", "side_x", "random_mean"}
    # v1.5 canonical: the old placement knobs are gone from the schema entirely
    for gone in ("dim", "z_squash", "galaxy_relax", "medium_place"):
        assert gone not in lm, f"{gone} should have been removed"
        assert gone not in lm["mode"], f"mode.{gone} should have been removed"
    assert lm["mode"]["canonical"] is True, lm["mode"]
    assert lm["mode"]["macro_dim"] == 3
    assert os.path.exists(os.path.join(lay, "preview.png"))
    assert any(f.startswith("preview_macro3d") and f.endswith(".html")
               for f in os.listdir(lay)), os.listdir(lay)

    # --- 3D containment regression on the canonical run: every placed non-dust
    #     galaxy sphere must fit inside its macro sphere (|c| + r <= Rm). Catches
    #     z-composition bugs (2026-09-26) and the pancake/lens regimes (v1.5).
    mpos = pq.read_table(os.path.join(lay, "macro_positions.parquet")).to_pydict()
    mxyz = _np.stack([mpos["x"], mpos["y"], mpos["z"]], axis=1)
    mr = _np.asarray(mpos["radius"])
    gxyz = _np.stack([gpos["x"], gpos["y"], gpos["z"]], axis=1)
    gr = _np.asarray(gpos["radius"])
    placed_mask = gr > 0
    cls_g = [str(c) for c in gpos["display_class"]]
    nondust = placed_mask & _np.asarray([c != "dust" for c in cls_g])
    mi_nd = _np.asarray(gpos["macro_id"])[nondust]
    d3_nd = _np.linalg.norm(gxyz[nondust] - mxyz[mi_nd], axis=1)
    assert (d3_nd + gr[nondust] <= mr[mi_nd] * 1.02 + 1e-9).all(), \
        f"non-dust galaxy bodies outside macro sphere: " \
        f"{int((d3_nd + gr[nondust] > mr[mi_nd] * 1.02).sum())}"
    q = lm["quality"]
    if q["galaxy_flat_mean"] is not None:
        assert 0.0 <= q["galaxy_flat_mean"] <= 1.0, q
        assert 0.0 <= q["galaxy_flat_p90"] <= 1.0, q

    # ---- v1.5 helper unit tests (flatness / adjacency / medium_local / fib slots)
    spec_lg = importlib.util.spec_from_file_location(
        "layout_global_mod", os.path.join(ROOT, "scripts", "layout_global.py"))
    lg = importlib.util.module_from_spec(spec_lg)
    spec_lg.loader.exec_module(lg)

    rng_t = _np.random.default_rng(7)
    planar = _np.stack([rng_t.normal(size=200), rng_t.normal(size=200),
                        _np.zeros(200)], axis=1)
    f_pl = lg.flatness(planar)
    f_sp = lg.flatness(rng_t.normal(size=(200, 3)))
    assert f_pl > 0.95, f_pl                                   # pancake ~ 1
    assert f_sp < f_pl - 0.1, (f_sp, f_pl)                     # isotropic < pancake
    assert lg.flatness(_np.zeros((2, 3))) == 0.0               # degenerate

    # chain graph on a line, weights decreasing with distance -> perfect recall
    centers_t = _np.array([[0.0, 0, 0], [10, 0, 0], [25, 0, 0], [45, 0, 0]])
    adjm = lg.macro_adjacency(centers_t, [(0, 1), (1, 2), (2, 3)],
                              [3.0, 2.0, 1.0], k=3)
    assert adjm["recall"] == 1.0, adjm
    assert adjm["spearman"] is not None and adjm["spearman"] < -0.9, adjm
    tiny = lg.macro_adjacency(_np.zeros((2, 3)), [(0, 1)], [1.0])
    assert tiny["recall"] is None and tiny["spearman"] is None

    # medium_local: intra-macro centroid / cross-macro boundary point / fallback
    mcen = _np.array([[0.0, 0, 0], [200, 0, 0]])
    mof = _np.array([0, 0, 0, 1])          # gid0 = medium, 1/2 regular, 3 other macro
    midx_t = {0: 0, 1: 1}
    reg_local = {1: _np.array([-10.0, 0, 0]), 2: _np.array([10.0, 0, 0])}
    p_in = lg.medium_local(reg_local, [(1, 1.0), (2, 1.0)], mcen, mof,
                           midx_t, 0, 50.0, 1.0, 42)
    assert _np.allclose(p_in, [0, 0, 0], atol=1e-9), p_in
    p_cross = lg.medium_local(reg_local, [(3, 1.0)], mcen, mof, midx_t,
                              0, 50.0, 1.0, 42)
    assert abs(p_cross[0] - (50.0 - 1.0) * 0.85) < 1e-9, p_cross
    assert _np.allclose(p_cross[1:], [0, 0], atol=1e-9), p_cross
    p_fb = lg.medium_local(reg_local, [], mcen, mof, midx_t, 0, 50.0, 1.0, 42)
    assert 0 < _np.linalg.norm(p_fb) <= (50.0 - 1.0) * 0.6 * 1.2, p_fb
    p_fb2 = lg.medium_local(reg_local, [], mcen, mof, midx_t, 0, 50.0, 1.0, 42,
                            fallback=_np.array([1.0, 2.0, 3.0]))
    assert _np.allclose(p_fb2, [1.0, 2.0, 3.0]), p_fb2

    # _fib_ball_slots: volume-uniform BALL (not a plane), deterministic, bounded
    S = lg._fib_ball_slots(60, 10.0, 42)
    assert S.shape == (60, 3)
    assert (_np.linalg.norm(S, axis=1) <= 10.0 + 1e-9).all()
    assert lg.flatness(S) < 0.9, lg.flatness(S)
    assert _np.allclose(S, lg._fib_ball_slots(60, 10.0, 42))
    assert lg._fib_ball_slots(0, 10.0).shape == (0, 3)

    # relax_disks: exactly-coincident bodies must separate (the zero-vector
    # degeneracy that stacked unlinked galaxies onto one point, 2026-09-29)
    stk = lg.relax_disks(_np.zeros((12, 3)), _np.ones(12) * 5.0, iters=120)
    dd = _np.linalg.norm(stk[:, None, :] - stk[None, :, :], axis=2)
    ii_t, jj_t = _np.triu_indices(12, 1)
    assert (dd[ii_t, jj_t] >= 10.2 * 0.99).all(), dd[ii_t, jj_t].min()

    # fr_layout isolated nodes: 3D arm must fill a BALL (pancake regression
    # guard, 2026-09-29); the dim=2 experimental arm keeps the planar spiral
    edges_c = [(i, i + 1) for i in range(9)]              # chain over nodes 0..9
    C3 = lg.fr_layout(40, edges_c, None, 3, 42)
    iso3 = C3[10:]
    assert len(iso3) == 30
    assert lg.flatness(iso3) < 0.9, lg.flatness(iso3)      # NOT a disk
    assert (_np.linalg.norm(C3, axis=1) <= 1.0 + 1e-6).all()
    C2 = lg.fr_layout(40, edges_c, None, 2, 42)
    assert C2.shape == (40, 2)
    n2 = _np.linalg.norm(C2[10:], axis=1)
    assert (n2 > 1e-6).all() and (n2 <= 1.0 + 1e-6).all()   # spiral, not origin

    # ---- local (intra-galaxy) article layout, batch range + merge
    run_script("layout_local.py", "--galaxies", "0-11", "--galaxy-tag", "res1_s",
               "--run", "r_main")
    run_script("layout_local.py", "--galaxies", "12-9999", "--galaxy-tag", "res1_s",
               "--run", "r_main", "--preview-galaxy", "0")
    assert os.path.exists(os.path.join(lay, "preview_galaxy_0.png"))
    apq = os.path.join(lay, "article_positions.parquet")
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

    # ---- B0 prep artifacts: existence + brute-force equality of the grouped
    #      cross pairs and ext_deg (the foundation must not lose/duplicate edges)
    prep_dir = os.path.join(BASE, "graph", "local_prep", "res1_s")
    for f in ("internal_offs.npy", "internal_buf.npy", "cross_u.npy",
              "cross_g.npy", "cross_w.npy", "ext_deg.npy", "prep_meta.json"):
        assert os.path.exists(os.path.join(prep_dir, f)), f
    cu = _np.load(os.path.join(prep_dir, "cross_u.npy"))
    cg = _np.load(os.path.join(prep_dir, "cross_g.npy"))
    cw = _np.load(os.path.join(prep_dir, "cross_w.npy"))
    ext = _np.load(os.path.join(prep_dir, "ext_deg.npy"))
    memb_s = _np.load(os.path.join(BASE, "community", "full", "membership_res1_s.npy"))
    Eu = _np.fromfile(os.path.join(BASE, "graph", "edges_undirected_unique.bin"),
                      dtype=_np.int32).reshape(-1, 2)
    brute = {}
    for u, v in Eu.tolist():
        gu, gv = int(memb_s[u]), int(memb_s[v])
        if gu != gv:
            brute[(u, gv)] = brute.get((u, gv), 0) + 1
            brute[(v, gu)] = brute.get((v, gu), 0) + 1
    got = {(int(a), int(b)): int(w) for a, b, w in zip(cu, cg, cw)}
    assert got == brute, f"cross pair grouping mismatch: {len(got)} vs {len(brute)}"
    ext_brute = _np.zeros(len(memb_s), _np.int64)
    for (u, gv), w in brute.items():
        ext_brute[u] += w
    assert _np.array_equal(ext, ext_brute), "ext_deg mismatch"
    # internal buckets: per-galaxy edge SET equals brute internal edges
    offs = _np.load(os.path.join(prep_dir, "internal_offs.npy"))
    buf = _np.load(os.path.join(prep_dir, "internal_buf.npy"))
    for g in (0, 1):
        le = buf[offs[g]:offs[g + 1]]
        members_g = _np.flatnonzero(memb_s == g)
        pos_of = {int(m): i for i, m in enumerate(members_g)}
        got_e = sorted(tuple(sorted((pos_of[int(x)], pos_of[int(y)])))
                       for x, y in le.tolist())
        exp_e = sorted(tuple(sorted((pos_of[u], pos_of[v])))
                       for u, v in Eu.tolist()
                       if memb_s[u] == g and memb_s[v] == g)
        assert got_e == exp_e, f"internal bucket mismatch g={g}"

    # ---- B0 anchor equivalence: grouped+bincount == legacy per-edge add.at
    spec_ll = importlib.util.spec_from_file_location(
        "layout_local_mod", os.path.join(ROOT, "scripts", "layout_local.py"))
    ll = importlib.util.module_from_spec(spec_ll)
    spec_ll.loader.exec_module(ll)
    pp = ll._prep_paths(Dirs(BASE), "res1_s")
    prep = ll.load_prep(pp)
    anc_new = ll.build_anchors(prep, memb_s, C3.astype(_np.float64), len(memb_s))
    anc_old = _np.zeros((len(memb_s), 3))
    for u, v in Eu.tolist():
        gu, gv = int(memb_s[u]), int(memb_s[v])
        if gu == gv:
            continue
        dd = C3[gv] - C3[gu]
        dd = dd / max(_np.linalg.norm(dd), 1e-9)
        anc_old[u] += dd
        anc_old[v] -= dd
    assert _np.allclose(anc_new, anc_old, atol=1e-9), \
        f"anchor drift: {_np.abs(anc_new - anc_old).max()}"

    # ---- recompose: new global params -> article positions without re-run
    # NOTE: z_squash has no effect on tiny synthetic graphs (igraph FR init is
    # planar for n<=~6), so we vary --pack to force a radius/scale change.
    run_script("layout_global.py", "--pack", "0.3",
               "--run", "r_c2", "--galaxy-tag", "res1_s")
    run_script("recompose_articles.py", "--from-run", "r_main", "--to-run", "r_c2")
    p_old = pq.read_table(os.path.join(lay, "article_positions.parquet")).to_pydict()
    p_new = pq.read_table(os.path.join(BASE, "layout", "r_c2",
                                       "article_positions.parquet")).to_pydict()
    assert len(p_new["x"]) == 60
    moved = sum(1 for i in range(60) if abs(p_new["z"][i] - p_old["z"][i]) > 1e-6
                or abs(p_new["x"][i] - p_old["x"][i]) > 1e-6)
    assert moved > 0, "recompose produced identical positions"

    # ---- parallel launcher: all-done branch -> merge only
    run_script("run_local_parallel.py", "--jobs", "2", "--galaxy-tag", "res1_s",
               "--run", "r_main")

    # ---- B0 invariance: an LPT-split 3-job parallel run must reproduce the
    #      sequential range-split article positions EXACTLY (coordinates are
    #      job-assignment independent by design)
    run_script("layout_global.py", "--galaxy-tag", "res1_s", "--run", "r_par")
    run_script("run_local_parallel.py", "--jobs", "3", "--galaxy-tag", "res1_s",
               "--run", "r_par")
    p_par = pq.read_table(os.path.join(BASE, "layout", "r_par",
                                       "article_positions.parquet")).to_pydict()
    assert _np.array_equal(p_par["x"], apt["x"]) and \
        _np.array_equal(p_par["y"], apt["y"]) and \
        _np.array_equal(p_par["z"], apt["z"]), \
        "parallel LPT split changed coordinates"
    pmeta = json.load(open(os.path.join(BASE, "layout", "r_par",
                                        "parallel_meta.json"), encoding="utf-8"))
    assert pmeta["lpt"] is True and pmeta["failures"] == 0, pmeta
    # B0 telemetry: per-galaxy timings must survive parallel mode (job metas ->
    # aggregated by the launcher) and progress/log side files must exist
    assert pmeta["per_galaxy_secs"].get("n", 0) > 0, pmeta
    pjobs = os.path.join(BASE, "layout", "r_par", "parallel_jobs")
    assert os.path.exists(os.path.join(pjobs, "job_00.meta.json")), os.listdir(pjobs)
    assert os.path.exists(os.path.join(pjobs, "job_00.progress")), os.listdir(pjobs)
    assert os.path.exists(os.path.join(pjobs, "job_00.log")), os.listdir(pjobs)
    lmeta = json.load(open(os.path.join(BASE, "layout", "r_par",
                                        "layout_local_meta.json"), encoding="utf-8"))
    assert lmeta["mode"] == "merge-only" and "parallel" in lmeta, lmeta
    assert lmeta["parallel"]["per_galaxy_secs"].get("n", 0) > 0, lmeta

    # ---- B0 fail-fast: a run without galaxy_positions must exit at once with
    #      an actionable message, not spawn jobs that crash (2026-09-30 incident)
    for scr, extra in (("layout_local.py", []),
                       ("run_local_parallel.py", ["--jobs", "2"])):
        r_nf = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", scr),
                               "--base", BASE, "--galaxy-tag", "res1_s",
                               "--run", "r_noglobal", *extra],
                              capture_output=True, text=True)
        assert r_nf.returncode != 0, f"{scr} should fail on a run without globals"
        assert "layout_global.py" in (r_nf.stdout + r_nf.stderr), r_nf.stderr[-500:]

    # ---- B3 multilevel FR: determinism / validity / quality metrics / gating
    import igraph as _ig
    g1200 = _ig.Graph.Barabasi(1200, m=15, directed=False)
    e1200 = [e.tuple for e in g1200.es]
    c_ml1 = ll._fr_unit(1200, e1200, 3, 7, fr_mode="ml", ml_threshold=300)
    c_ml2 = ll._fr_unit(1200, e1200, 3, 7, fr_mode="ml", ml_threshold=300)
    c_fl = ll._fr_unit(1200, e1200, 3, 7, fr_mode="flat")
    assert _np.array_equal(c_ml1, c_ml2), "multilevel FR not deterministic"
    for c in (c_ml1, c_fl):
        assert _np.isfinite(c).all()
        assert abs(_np.linalg.norm(c, axis=1).max() - 1.0) < 1e-9
    q_ml = ll.galaxy_layout_quality(c_ml1, e1200)
    q_fl = ll.galaxy_layout_quality(c_fl, e1200)
    assert q_ml and q_fl
    assert 0.0 < q_ml["adj_recall"] <= 1.0, q_ml
    assert 0.0 < q_fl["adj_recall"] <= 1.0, q_fl
    # chain graph: spatial nearest neighbours must recover graph neighbours
    chain = [(i, i + 1) for i in range(59)]
    c_ch = ll._fr_unit(60, chain, 3, 1, fr_mode="flat")
    q_ch = ll.galaxy_layout_quality(c_ch, chain)
    assert q_ch and q_ch["adj_recall"] > 0.9, q_ch
    # gating: below --ml-threshold the default (ml) and flat arms must agree
    # EXACTLY (legacy path untouched = small galaxies never move)
    run_script("layout_global.py", "--galaxy-tag", "res1_s", "--run", "r_b3a")
    run_script("layout_global.py", "--galaxy-tag", "res1_s", "--run", "r_b3b")
    run_script("layout_local.py", "--galaxies", "0-5", "--galaxy-tag", "res1_s",
               "--run", "r_b3a")
    run_script("layout_local.py", "--galaxies", "0-5", "--galaxy-tag", "res1_s",
               "--run", "r_b3b", "--fr-mode", "flat")
    for g in range(6):
        sa = os.path.join(BASE, "layout", "r_b3a", "article_shards", f"gal_{g:06d}.npy")
        sb = os.path.join(BASE, "layout", "r_b3b", "article_shards", f"gal_{g:06d}.npy")
        assert _np.array_equal(_np.load(sa), _np.load(sb)), \
            f"ml/flat differ below threshold (g={g})"
    lm_b3 = json.load(open(os.path.join(BASE, "layout", "r_b3a",
                                        "layout_local_meta.json"), encoding="utf-8"))
    assert lm_b3["params"]["fr_mode"] == "ml"
    assert lm_b3["params"]["ml_threshold"] == 2000
    assert set(lm_b3["fr_quality"]) >= {"n_eval", "adj_recall_mean",
                                        "edge_len_cv_mean", "n_ml_applied"}

    # ---- B3 launcher pass-through: --fr-mode must reach every job and the meta
    run_script("layout_global.py", "--galaxy-tag", "res1_s", "--run", "r_b3c")
    run_script("run_local_parallel.py", "--jobs", "2", "--galaxy-tag", "res1_s",
               "--run", "r_b3c", "--fr-mode", "flat")
    pmc = json.load(open(os.path.join(BASE, "layout", "r_b3c",
                                      "parallel_meta.json"), encoding="utf-8"))
    assert pmc["fr_mode"] == "flat" and pmc["failures"] == 0, pmc
    # fr_quality must survive parallel mode (aggregated from job metas; the
    # fixture galaxies are below the evaluation size band so n_eval == 0)
    assert set(pmc["fr_quality"]) >= {"n_eval", "adj_recall_mean",
                                      "edge_len_cv_mean", "n_ml_applied"}, pmc
    assert pmc["fr_quality"]["n_eval"] == 0

    # ---- checkpoint repair: after a parallel run + merge, a single-process
    #      `--galaxies all` must see everything done (2026-09-30 race fix)
    out_b3c = run_script("layout_local.py", "--galaxies", "all",
                         "--galaxy-tag", "res1_s", "--run", "r_b3c")
    assert "done: 0 galaxies this run" in out_b3c, out_b3c[-500:]

    # ---- no-op invocation (run complete) must not rebuild anchors and must
    #      still serve preview + merge (2026-09-30 lazy-anchor fix)
    run_script("layout_local.py", "--galaxies", "all", "--galaxy-tag", "res1_s",
               "--run", "r_par", "--preview-galaxy", "0")

    # ---- viewer tiles export
    run_script("export_viewer_tiles.py", "--galaxy-tag", "res1_s", "--run", "r_main")
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

    # ---- macro label overrides: export must prefer hand-curated labels
    #      (empty labels are ignored; removing the file restores defaults)
    with open(os.path.join(final, "macro_label_overrides.json"), "w",
              encoding="utf-8") as f:
        json.dump({"macros": [{"macro_id": 0, "label": "テスト銀河団名"},
                              {"macro_id": 1, "label": ""}]}, f, ensure_ascii=False)
    run_script("export_viewer_tiles.py", "--galaxy-tag", "res1_s", "--run", "r_main")
    boot2 = json.load(open(os.path.join(sp, "bootstrap.json"), encoding="utf-8"))
    assert any(row[5] == "テスト銀河団名" for row in boot2["macros"]), boot2["macros"][:3]
    os.remove(os.path.join(final, "macro_label_overrides.json"))
    run_script("export_viewer_tiles.py", "--galaxy-tag", "res1_s", "--run", "r_main")
    boot3 = json.load(open(os.path.join(sp, "bootstrap.json"), encoding="utf-8"))
    assert all(row[5] != "テスト銀河団名" for row in boot3["macros"])
    # 空 label のオーバーライドは無視される(= 既定導出のまま)
    assert boot2["macros"][1][5] == boot3["macros"][1][5]

    # ---- tile CONTENT round-trip: local idx must map back to the exact global
    #      undirected internal edges of the galaxy (catches searchsorted-on-
    #      permutation class bugs, 2026-09-27)
    import struct as _st
    with open(t0f, "rb") as fh:
        raw = fh.read()
    nn, ne, nx = _st.unpack("<III", raw[:12])
    ed = np.frombuffer(raw, offset=12 + nn * 12, count=ne * 2, dtype=np.uint32).reshape(-1, 2)
    cr = np.frombuffer(raw, offset=12 + nn * 12 + ne * 8, count=nx * 3, dtype=np.uint32).reshape(-1, 3)
    memb_s = np.load(os.path.join(BASE, "community", "full", "membership_res1_s.npy"))
    ord_s = np.argsort(memb_s, kind="stable")
    st0 = np.searchsorted(memb_s[ord_s], 0, side="left")
    en0 = np.searchsorted(memb_s[ord_s], 0, side="right")
    members0 = ord_s[st0:en0]
    assert ed.max(initial=0) < nn and cr[:, 0].max(initial=0) < nn
    got = sorted(tuple(sorted((int(members0[a]), int(members0[b])))) for a, b in ed.tolist())
    Eu = np.fromfile(os.path.join(BASE, "graph", "edges_undirected_unique.bin"),
                     dtype=np.int32).reshape(-1, 2)
    in0 = (memb_s[Eu[:, 0]] == 0) & (memb_s[Eu[:, 1]] == 0)
    exp = sorted(tuple(sorted((int(a), int(b)))) for a, b in Eu[in0].tolist())
    assert got == exp, f"tile edges mismatch: got={got} exp={exp} members0={members0.tolist()} ed={ed.tolist()}"


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
