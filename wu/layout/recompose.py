# wu/layout/recompose.py — 記事座標の run 間再構成(再レイアウトなしの移植)
#
# 責務:
# - グローバル配置だけが変わった新 run へ、既存 run の記事座標を
#   「銀河中心への相対座標のスケール + 平行移動」で移植する(銀河内の
#   相対配置は不変 = 高コストな記事レイアウトの再計算を避ける)。
#
# 注意:
# - test_catalog の recompose 節が挙動を担保する。正準実行は
#   python -m wu run recompose --set recompose.from_run=… --set recompose.to_run=…

"""グローバルレイアウトのパラメータ変更後、記事座標を剛体+スケールで再構成する
(例: macro_z_squash / r_spacing の変更)。~1h の layout_local 再実行を回避する:
各銀河の記事点群を新しい中心へ平行移動し、新旧の半径比でスケールする
(内部形状は保存される)。

Usage(run 名は <YYYYMMDD>_<slug> 規約。wu/paths.py 参照):
  python -m wu run layout_global --base data --run <NEW_RUN>
  python -m wu run recompose --base data \
      --set recompose.from_run=<OLD_RUN> --set recompose.to_run=<NEW_RUN>
  python -m wu run publish --base data --run <NEW_RUN>
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np


from ..paths import Dirs  # noqa: E402


def main(a):
    # a=None のときだけ CLI 引数を解析する(ステージからは Namespace を
    # 注入して呼ぶ = 引数解析と処理本体の分離。CLI 挙動は不変)。
    dirs = Dirs(a.base)
    t0 = time.time()
    import pyarrow as pa
    import pyarrow.parquet as pq

    lo = str(dirs.layout_run(a.from_run))
    ln = str(dirs.layout_run(a.to_run))
    go = pq.read_table(os.path.join(lo, "galaxy_positions.parquet")).to_pydict()
    gn = pq.read_table(os.path.join(ln, "galaxy_positions.parquet")).to_pydict()
    co = np.stack([go["x"], go["y"], go["z"]], axis=1)
    cn = np.stack([gn["x"], gn["y"], gn["z"]], axis=1)
    ro = np.maximum(np.asarray(go["radius"], np.float64), 1e-6)
    rn = np.maximum(np.asarray(gn["radius"], np.float64), 1e-6)

    apq = pq.read_table(os.path.join(lo, "article_positions.parquet"))
    P = np.stack([apq.column("x").to_numpy(), apq.column("y").to_numpy(),
                  apq.column("z").to_numpy()], axis=1).astype(np.float64)
    gid = apq.column("galaxy_id").to_numpy().astype(np.int64)
    scale = (rn / ro)[gid][:, None]
    P = cn[gid] + (P - co[gid]) * scale
    os.makedirs(ln, exist_ok=True)
    pq.write_table(pa.table({
        "idx": apq.column("idx"), "page_id": apq.column("page_id"),
        "galaxy_id": apq.column("galaxy_id"),
        "x": pa.array(P[:, 0].astype(np.float32)),
        "y": pa.array(P[:, 1].astype(np.float32)),
        "z": pa.array(P[:, 2].astype(np.float32)),
    }), os.path.join(ln, "article_positions.parquet"), compression="zstd")
    print(f"[recompose] {len(P):,} articles {a.from_run} -> {a.to_run} "
          f"({time.time()-t0:.0f}s)")

