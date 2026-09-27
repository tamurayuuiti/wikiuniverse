"""Run layout_local over all remaining galaxies with N parallel subprocesses
(Windows-safe; each galaxy shard is independent = the \u00a712 batch model).

Usage:
  python scripts/run_local_parallel.py --base data --jobs 8 [--run <layout run>]
Finishes with a merge pass (layout_local --galaxies all).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.dumpio import read_json  # noqa: E402
from wu.paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--galaxy-tag", default="res1_sub")
    ap.add_argument("--run", default=ACTIVE_LAYOUT_RUN,
                    help="layout run name under data/layout "
                         "(default: wu.paths.ACTIVE_LAYOUT_RUN)")
    a = ap.parse_args()
    dirs = Dirs(a.base)
    lay = str(dirs.layout_run(a.run))
    memb_path = str(dirs.community_full / f"membership_{a.galaxy_tag}.npy")
    import numpy as np
    G = int(np.load(memb_path).max()) + 1
    ck = read_json(os.path.join(lay, "layout_local_checkpoint.json"), {}) or {}
    done = set(int(x) for x in ck.get("done", []))
    remaining = [g for g in range(G) if g not in done]
    if not remaining:
        print("[parallel] nothing remaining; merging")
    else:
        chunks = np.array_split(np.array(remaining), a.jobs)
        procs = []
        t0 = time.time()
        for ch in chunks:
            if len(ch) == 0:
                continue
            lo, hi = int(ch.min()), int(ch.max())
            cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "layout_local.py"), "--base", a.base, "--galaxies", f"{lo}-{hi}",
                   "--galaxy-tag", a.galaxy_tag, "--run", a.run]
            procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE))
            print(f"[parallel] spawned job {lo}-{hi}")
        fails = 0
        for p in procs:
            _, err = p.communicate()
            if p.returncode != 0:
                fails += 1
                print(err.decode("utf-8", "replace")[-2000:])
        print(f"[parallel] {len(procs)} jobs finished in {time.time()-t0:.0f}s "
              f"(failures={fails})")
        if fails:
            sys.exit("some jobs failed; rerun to resume")
    subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "layout_local.py"), "--base", a.base, "--galaxies", "all",
                    "--galaxy-tag", a.galaxy_tag, "--run", a.run], check=True)


if __name__ == "__main__":
    main()
