"""半径則の実験腕(--r-expo)のテスト: 既定 0.5 = 従来 √n、1/3 腕で半径が変化し、
包含(spill ≡ 0)は両腕で保たれることを最小カタログ fixture で検証する。

チェーン: 合成カタログ(final/ 直書き)→ layout_global ステージ ×2 腕 →
galaxy_positions の半径比較 + quality 検証。数秒・ネットワーク不要。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

import wu.stages  # noqa: E402,F401  (ステージ登録)
from wu.paths import Dirs  # noqa: E402
from wu.pipeline import config as pcfg, runner  # noqa: E402
from wu.pipeline.stage import get  # noqa: E402

TMP = os.path.join(HERE, "synth_data", "rexpo_fixture")


def build_catalog():
    """マクロ2つ(m0: 3銀河チェーン + m1: 2銀河)の最小カタログを final/ へ直書き。"""
    final = os.path.join(TMP, "final")
    os.makedirs(final, exist_ok=True)
    G, NG = 5, 3
    pq.write_table(pa.table({
        "galaxy_id": pa.array(np.arange(G), type=pa.int32()),
        "macro_id": pa.array([0, 0, 0, 1, 1], type=pa.int32()),
        "n_articles": pa.array([300, 200, 100, 500, 50], type=pa.int32()),
        "is_dust": [False] * G,
        "display_class": ["galaxy"] * G,
        "name": ["a", "b", "c", "d", "e"],
    }), os.path.join(final, "galaxies.parquet"))
    pq.write_table(pa.table({
        "macro_id": pa.array([0, 1], type=pa.int32()),
        "n_articles": pa.array([600, 550], type=pa.int32()),
        "n_galaxies": pa.array([NG, 2], type=pa.int32()),
        "is_dust": [False, False],
    }), os.path.join(final, "macros.parquet"))
    pq.write_table(pa.table({
        "a": pa.array([0, 1, 3], type=pa.int64()),
        "b": pa.array([1, 2, 4], type=pa.int64()),
        "w": [5.0, 3.0, 2.0],
    }), os.path.join(final, "galaxy_pairs_topK.parquet"))
    pq.write_table(pa.table({
        "macro_a": pa.array([0], type=pa.int64()),
        "macro_b": pa.array([1], type=pa.int64()),
        "w": [7.0],
    }), os.path.join(final, "macro_pairs.parquet"))


def run_arm(run_name: str, r_expo=None):
    dirs = Dirs(TMP)
    shared = {"run": run_name, "galaxy_tag": "res1_s", "macro_tag": "res1",
              "tag": "res1_s"}
    over = [] if r_expo is None else [f"layout_global.r_expo={r_expo}"]
    cfg = pcfg.apply_overrides(pcfg.EMPTY, over)
    ent = runner.plan([get("layout_global")], dirs, cfg, shared, force=True)
    assert ent[0]["status"] == "run", ent[0]
    rc = runner.execute(ent, dirs, cfg, shared, f"rexpo-{run_name}")
    assert rc == 0, rc
    lay = os.path.join(TMP, "layout", run_name)
    gpos = pq.read_table(os.path.join(lay, "galaxy_positions.parquet")).to_pydict()
    meta = json.load(open(os.path.join(lay, "layout_meta.json"),
                          encoding="utf-8"))
    return gpos, meta


def main():
    import shutil
    if os.path.exists(TMP):
        shutil.rmtree(TMP)
    build_catalog()

    g05, m05 = run_arm("20260101_expo05")             # 既定(0.5 = √n)
    g33, m33 = run_arm("20260101_expo33", r_expo=1/3)  # 3D 整合腕

    # meta に腕が記録される(来歴)
    assert m05["r_expo"] == 0.5, m05["r_expo"]
    assert abs(m33["r_expo"] - 1 / 3) < 1e-12, m33["r_expo"]

    r05 = np.asarray(g05["radius"])
    r33 = np.asarray(g33["radius"])
    # 腕によって半径が変わる(同じカタログ・同じシードで座標系の差は半径則のみ)
    assert not np.allclose(r05, r33), (r05, r33)
    # 1/3 腕では小銀河ほど相対的に大きくなる(f^(1/3) > f^(1/2) for f<1):
    # 最小銀河(gid4, n=50)の「半径/マクロ半径」比が大きくなることを確認
    mpos = pq.read_table(os.path.join(TMP, "layout", "20260101_expo05",
                                      "macro_positions.parquet")).to_pydict()
    mpos3 = pq.read_table(os.path.join(TMP, "layout", "20260101_expo33",
                                       "macro_positions.parquet")).to_pydict()
    Rm = np.asarray(mpos["radius"])
    Rm3 = np.asarray(mpos3["radius"])
    mid = np.asarray(g05["macro_id"])
    rel05 = r05 / Rm[mid]
    rel33 = r33 / Rm3[mid]
    i_min = int(np.argmin(np.asarray(g05["n_articles"])))
    assert rel33[i_min] > rel05[i_min], (rel05, rel33)

    # 包含(spill ≡ 0)は両腕で構造保証
    assert m05["quality"]["galaxy_spill_count"] == 0, m05["quality"]
    assert m33["quality"]["galaxy_spill_count"] == 0, m33["quality"]
    # 直接包含検証(macro_positions と突き合わせ: d3 + r <= 1.02 Rm)
    for run, gpos in (("20260101_expo05", g05), ("20260101_expo33", g33)):
        mp = pq.read_table(os.path.join(TMP, "layout", run,
                                        "macro_positions.parquet")).to_pydict()
        mxyz = np.stack([mp["x"], mp["y"], mp["z"]], axis=1)
        mR = np.asarray(mp["radius"])
        xyz = np.stack([gpos["x"], gpos["y"], gpos["z"]], axis=1)
        rr = np.asarray(gpos["radius"])
        midx = np.asarray(gpos["macro_id"])
        d3 = np.linalg.norm(xyz - mxyz[midx], axis=1)
        assert (d3 + rr <= mR[midx] * 1.02 + 1e-9).all(), run

    print("\n*** R-EXPO ARM TEST PASSED ***")
    print(f"r(0.5)={np.round(r05, 3).tolist()}")
    print(f"r(1/3)={np.round(r33, 3).tolist()}")


if __name__ == "__main__":
    main()
