"""正式チェーンのランナー E2E: fixture → parse … catalog を `wu run` 意味論で完走。

チェーン(座標・出版の手前まで):
  parse → edges → stats → categories → dedup → detect → subdivide → metrics →
  cluster → export → purity → catalog
検証: 全ステージの成果物実体 / catalog の包含違反 0 / 全 chain 再 plan = skip
(resume 意味論)/ manifest の stage 記録。fixture は 60 記事の合成データなので
Leiden 含め十数秒で完走する(実データフルパイプラインはユーザーローカル領域)。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from fixture import BASE, build_fixtures  # noqa: E402

import wu.stages  # noqa: E402,F401  (ステージ登録)
from wu.paths import Dirs  # noqa: E402
from wu.pipeline import config as pcfg, runner  # noqa: E402
from wu.pipeline.stage import get  # noqa: E402

CHAIN = ("parse", "edges", "stats", "categories", "dedup", "detect",
         "subdivide", "metrics", "cluster", "export", "purity", "catalog",
         "layout_global", "layout_local", "publish")


def main():
    if os.path.exists(BASE):
        shutil.rmtree(BASE)
    build_fixtures()
    dirs = Dirs(BASE)
    shared = {"run": "r_chain", "galaxy_tag": "res1_s", "macro_tag": "res1",
              "tag": "res1_s"}
    # fixture スケール(60 記事)に合わせた分割パラメータ + tag 体系
    cfg = pcfg.apply_overrides(pcfg.EMPTY, [
        "subdivide.min_size=3", "subdivide.max_galaxy=6",
        "subdivide.out_tag=res1_s", "catalog.top_neighbors=4",
        "catalog.top_pairs=100", "layout_local.jobs=2",
    ])

    stages = [get(n) for n in CHAIN]
    ent = runner.plan(stages, dirs, cfg, shared)
    got = {e["stage"].name: e["status"] for e in ent}
    assert all(s == "run" for s in got.values()), got  # 契約宣言の正しさの証明
    rc = runner.execute(ent, dirs, cfg, shared, "chain")
    assert rc == 0, rc

    # ---- 成果物の実体(各ステージの宣言 outputs が実際に生成された)
    cf = str(dirs.community_full)
    for f in ("membership_res1.npy", "membership_res1_s.npy",
              "subdivide_meta_res1_s.json", "metrics_res1_s.json",
              "per_community_res1_s.parquet", "pairs_res1_s.npz",
              "clusters_node_res1_s.npy", "membership_res1_s.parquet",
              "purity_res1_s.parquet", "purity_res1_s.json"):
        assert os.path.exists(os.path.join(cf, f)), f
    final = str(dirs.final)
    for f in ("galaxies.parquet", "macros.parquet", "galaxy_pairs_topK.parquet",
              "macro_pairs.parquet", "catalog_meta.json"):
        assert os.path.exists(os.path.join(final, f)), f
    meta = json.load(open(os.path.join(final, "catalog_meta.json"),
                          encoding="utf-8"))
    assert meta["containment_violations"] == 0, meta
    assert meta["n_articles"] == 60, meta
    assert meta["n_galaxies"] > 0

    # ---- 座標 run(layout_global → layout_local 並列ランチャ → マージ)
    lay = os.path.join(BASE, "layout", "r_chain")
    for f in ("galaxy_positions.parquet", "macro_positions.parquet",
              "layout_meta.json", "article_positions.parquet",
              "layout_local_meta.json", "parallel_meta.json"):
        assert os.path.exists(os.path.join(lay, f)), f
    import pyarrow.parquet as pq
    apt = pq.read_table(os.path.join(lay, "article_positions.parquet")).to_pydict()
    assert len(apt["page_id"]) == 60, len(apt["page_id"])
    lmeta = json.load(open(os.path.join(lay, "layout_meta.json"),
                           encoding="utf-8"))
    assert lmeta["quality"]["galaxy_spill_count"] == 0, lmeta["quality"]
    assert lmeta["r_expo"] == 0.5, lmeta  # 既定腕 = 従来の √n

    # ---- 公開面(publish: bootstrap + tiles + tiles_meta、契約バージョン付き)
    sp = os.path.join(BASE, "spatial")
    boot = json.load(open(os.path.join(sp, "bootstrap.json"), encoding="utf-8"))
    assert boot["meta"]["layout_run"] == "r_chain", boot["meta"]
    assert boot["meta"]["schema_version"] == 1, boot["meta"]
    assert len(boot["galaxies"]) == meta["n_galaxies"]
    tmeta = json.load(open(os.path.join(sp, "tiles_meta.json"),
                           encoding="utf-8"))
    assert tmeta["n_tiles"] == meta["n_galaxies"], tmeta
    assert os.path.exists(os.path.join(sp, "tiles", "gal_000000.bin"))
    # tiles_meta の総数は bin ヘッダ実測と一致する(F22 シャドーイング回帰)
    import struct
    sum_ne = sum_nx = 0
    for g_i in range(meta["n_galaxies"]):
        bf = os.path.join(sp, "tiles", f"gal_{g_i:06d}.bin")
        if not os.path.exists(bf):
            continue
        with open(bf, "rb") as fh:
            _nn, ne_i, nx_i = struct.unpack("<III", fh.read(12))
        sum_ne += ne_i
        sum_nx += nx_i
    assert tmeta["internal_edges"] == sum_ne, (tmeta, sum_ne)
    assert tmeta["cross_links"] == sum_nx, (tmeta, sum_nx)

    # ---- manifest: 全 12 ステージが ok で記録されている
    mans = [f for f in os.listdir(str(dirs.manifests)) if f.endswith("_chain.json")]
    assert len(mans) == 1
    man = json.load(open(os.path.join(str(dirs.manifests), mans[0]),
                         encoding="utf-8"))
    assert [r["name"] for r in man["stages"]] == list(CHAIN)
    assert all(r["status"] == "ok" for r in man["stages"]), man["stages"]

    # ---- resume 意味論: 全 chain の再 plan = skip(生成物由来)
    ent2 = runner.plan(stages, dirs, cfg, shared)
    got2 = {e["stage"].name: e["status"] for e in ent2}
    assert all(s == "skip" for s in got2.values()), got2

    # ---- "all" の展開 = canonical 全 chain(download を除く移行済み範囲)
    all_names = [s.name for s in runner.expand_targets(["all"])]
    for n in CHAIN:
        assert n in all_names, (n, all_names)
    assert "prune" not in all_names and "body_edges" not in all_names

    print("\n*** STAGES CHAIN E2E TEST PASSED ***")
    print(f"chain={len(CHAIN)} galaxies={meta['n_galaxies']} "
          f"canonical={len(all_names)}: {all_names}")


if __name__ == "__main__":
    main()
