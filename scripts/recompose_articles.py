"""Rigid+scale recomposition of article positions after a global-layout parameter
change (e.g. --z-squash / --r-spacing). Avoids the ~1h layout_local re-run:
each galaxy's article cloud is translated to the new center and scaled by the
new/old radius ratio (internal shape preserved).

Usage (run names follow the <YYYYMMDD>_<slug> convention; see wu/paths.py):
  python scripts/layout_global.py --base data --macro-dim 3 --run 20260928_newglobal
  python scripts/recompose_articles.py --base data \
      --from-run 20260926_baseline --to-run 20260928_newglobal
  python scripts/export_viewer_tiles.py --base data --run 20260928_newglobal
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.paths import Dirs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--from-run", required=True,
                    help="source layout run (provides the article positions)")
    ap.add_argument("--to-run", required=True,
                    help="target layout run (provides the new galaxy centers/radii)")
    a = ap.parse_args()
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


if __name__ == "__main__":
    main()
