"""Run layout_local over all remaining galaxies with N parallel subprocesses
(Windows-safe; each galaxy shard is independent = the §12 batch model).

B0 foundation (2026-09-30):
  1. the shared prep artifacts (grouped cross pairs + internal buckets) are
     built ONCE here before any job spawns (jobs only memory-map them),
  2. galaxies are assigned to jobs by greedy LPT bin-packing on the measured
     FR cost model cost ~ n_articles^1.93 (02 §O) instead of equal id counts,
     which removes the straggler that used to set the wall time,
  3. --jobs defaults to the machine's cpu count,
  4. per-job walls and bin costs are recorded in <run>/parallel_meta.json and
     folded into layout_local_meta.json by the merge step.
Coordinates are invariant under all of the above (per-galaxy math and seeds
are untouched); the launcher only changes WHO computes WHAT.

Usage:
  python scripts/run_local_parallel.py --base data [--jobs N] [--run <RUN>]
Finishes with a scan-free merge pass (layout_local --merge-only).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.dumpio import read_json, write_json  # noqa: E402
from wu.paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402

COST_EXP = 1.93  # measured FR scaling (02 §O): t ~ c * n^1.93


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 8,
                    help="parallel job count (default: cpu count; tune down if "
                         "RAM-bound, each job memory-maps the shared prep)")
    ap.add_argument("--galaxy-tag", default="res1_sub")
    ap.add_argument("--run", default=ACTIVE_LAYOUT_RUN,
                    help="layout run name (default: wu.paths.ACTIVE_LAYOUT_RUN)")
    ap.add_argument("--fr-mode", default="ml", choices=["ml", "flat"],
                    help="B3 arm selection, forwarded to every layout_local job "
                         "(ml = multilevel-seeded FR, flat = legacy arm)")
    ap.add_argument("--ml-threshold", type=int, default=2000,
                    help="forwarded to jobs (article count where multilevel kicks in)")
    ap.add_argument("--ml-niter", type=int, default=100,
                    help="forwarded to jobs (FR refine iterations on the seed)")
    a = ap.parse_args()
    dirs = Dirs(a.base)
    lay = str(dirs.layout_run(a.run))
    memb_path = str(dirs.community_full / f"membership_{a.galaxy_tag}.npy")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "layout_local.py")

    gpos_path = os.path.join(lay, "galaxy_positions.parquet")
    if not os.path.exists(gpos_path):
        sys.exit(f"[parallel] {gpos_path} not found: run "
                 f"`python scripts/layout_global.py --base {a.base} "
                 f"--run {a.run}` first (jobs need this run's galaxy "
                 f"centers/radii; failing here instead of spawning jobs "
                 f"that all crash)")

    import numpy as np
    memb = np.load(memb_path)
    G = int(memb.max()) + 1
    ck = read_json(os.path.join(lay, "layout_local_checkpoint.json"), {}) or {}
    done = set(int(x) for x in ck.get("done", []))
    remaining = [g for g in range(G) if g not in done]

    # ---- 1. shared prep, once, before any job (jobs never race on it)
    t0 = time.time()
    r = subprocess.run([sys.executable, script, "--base", a.base, "--prep",
                        "--galaxy-tag", a.galaxy_tag],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[parallel] prep failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    prep_secs = round(time.time() - t0, 1)
    print(f"[parallel] prep ready ({prep_secs}s)")

    par_dir = os.path.join(lay, "parallel_jobs")
    if not remaining:
        print("[parallel] nothing remaining; merging")
    else:
        # ---- 2. LPT bin-packing on the cost model (straggler removal)
        n_g = np.bincount(memb, minlength=G).astype(np.float64)
        costs = n_g ** COST_EXP
        order = sorted(remaining, key=lambda g: -costs[g])
        bins = [[] for _ in range(a.jobs)]
        load = [0.0] * a.jobs
        for g in order:
            k = min(range(a.jobs), key=lambda i: load[i])
            bins[k].append(g)
            load[k] += costs[g]
        if os.path.isdir(par_dir):
            shutil.rmtree(par_dir)
        os.makedirs(par_dir, exist_ok=True)
        specs = []
        for k, b in enumerate(bins):
            if not b:
                continue
            p = os.path.join(par_dir, f"job_{k:02d}.gids.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("\n".join(str(x) for x in sorted(b)) + "\n")
            specs.append((p, sorted(b), load[k]))
        tot = sum(load) or 1.0
        print(f"[parallel] {len(remaining)} galaxies -> {len(specs)} jobs "
              f"(LPT on n^{COST_EXP}; bin cost share "
              f"{max(load) / tot:.2f} max vs {1 / len(specs):.2f} ideal)")

        # ---- 3. spawn + live progress monitor + per-job walls
        #      (job stdout goes to a per-job log; the launcher aggregates the
        #      side-channel .progress files so long runs are never silent)
        procs = []
        prog_files = []
        t0 = time.time()
        for p, nb, _ in specs:
            stem = p[:-len(".gids.txt")] if p.endswith(".gids.txt") else p
            log_path = stem + ".log"
            prog_files.append(stem + ".progress")
            cmd = [sys.executable, script, "--base", a.base,
                   "--galaxy-tag", a.galaxy_tag, "--run", a.run,
                   "--fr-mode", a.fr_mode,
                   "--ml-threshold", str(a.ml_threshold),
                   "--ml-niter", str(a.ml_niter),
                   "--job-spec", p]
            lf = open(log_path, "w", encoding="utf-8")
            procs.append((p, nb, time.time(),
                          subprocess.Popen(cmd, stdout=lf,
                                           stderr=subprocess.STDOUT), lf))
            print(f"[parallel] spawned {os.path.basename(p)} ({nb} galaxies, "
                  f"log={os.path.basename(log_path)})")
        last_print = 0.0
        while any(pr.poll() is None for _, _, _, pr, _ in procs):
            now = time.time()
            if now - last_print >= 10.0:
                last_print = now
                d_tot = all_tot = 0
                for pf in prog_files:
                    try:
                        with open(pf, encoding="utf-8") as fh:
                            parts = fh.read().split()
                        if len(parts) >= 4:
                            d_tot += int(parts[0])
                            all_tot += int(parts[1])
                    except OSError:
                        continue
                el = now - t0
                eta = (el * (all_tot - d_tot) / d_tot) if d_tot else -1.0
                print(f"[parallel] progress {d_tot}/{all_tot} galaxies "
                      f"({100.0 * d_tot / max(1, all_tot):.0f}%) "
                      f"elapsed {el / 60:.1f}m "
                      f"eta {eta / 60:.1f}m", flush=True)
            time.sleep(2.0)
        walls = []
        fails = 0
        for p, nb, ts, proc, lf in procs:
            proc.communicate()
            lf.close()
            walls.append(round(time.time() - ts, 1))
            if proc.returncode != 0:
                fails += 1
                stem = p[:-len(".gids.txt")] if p.endswith(".gids.txt") else p
                with open(stem + ".log", encoding="utf-8", errors="replace") as fh:
                    print(fh.read()[-2000:])
        total = round(time.time() - t0, 1)
        print(f"[parallel] {len(procs)} jobs finished in {total}s "
              f"(walls={walls}, failures={fails})")

        # ---- 3b. aggregate per-galaxy telemetry from the job metas
        all_times = []
        job_rows = []
        q_n = q_cv_n = q_ml = 0
        q_rec_sum = q_cv_sum = 0.0
        for (p, nb, _), wall in zip(specs, walls):
            stem = p[:-len(".gids.txt")] if p.endswith(".gids.txt") else p
            jm = read_json(stem + ".meta.json", {}) or {}
            all_times.extend(jm.get("per_galaxy_secs", []))
            fq = jm.get("fr_quality", {})
            q_n += fq.get("n_eval", 0)
            q_cv_n += fq.get("n_cv", 0)
            q_rec_sum += fq.get("adj_recall_sum", 0.0)
            q_cv_sum += fq.get("edge_len_cv_sum", 0.0)
            q_ml += fq.get("n_ml_applied", 0)
            job_rows.append({"job": os.path.basename(p), "galaxies": nb,
                             "wall_secs": wall,
                             "n_done": jm.get("n_done")})
        fr_quality = {"n_eval": q_n,
                      "adj_recall_mean": round(q_rec_sum / q_n, 4) if q_n else None,
                      "edge_len_cv_mean": round(q_cv_sum / q_cv_n, 4) if q_cv_n else None,
                      "n_ml_applied": q_ml}
        agg = {}
        if all_times:
            tt = sorted(all_times)
            import numpy as _np
            agg = {"n": len(tt),
                   "p50": round(float(_np.percentile(tt, 50)), 4),
                   "p95": round(float(_np.percentile(tt, 95)), 4),
                   "max": round(float(tt[-1]), 4),
                   "mean": round(float(sum(tt) / len(tt)), 4)}
        write_json(os.path.join(lay, "parallel_meta.json"),
                   {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "jobs": len(procs), "prep_secs": prep_secs,
                    "fr_mode": a.fr_mode, "ml_threshold": a.ml_threshold,
                    "ml_niter": a.ml_niter,
                    "cost_model": f"n^{COST_EXP}", "lpt": True,
                    "bin_galaxies": [nb for _, nb, _ in specs],
                    "bin_cost_share": [round(l / tot, 4) for _, _, l in specs],
                    "walls_secs": walls, "total_secs": total,
                    "failures": fails,
                    "per_galaxy_secs": agg, "fr_quality": fr_quality,
                    "jobs_detail": job_rows})
        if agg:
            print(f"[parallel] per-galaxy secs: p50={agg['p50']} "
                  f"p95={agg['p95']} max={agg['max']} (n={agg['n']})")
        if fails:
            sys.exit("some jobs failed; rerun to resume")

    # ---- 4. scan-free merge (also writes layout_local_meta.json + telemetry)
    subprocess.run([sys.executable, script, "--base", a.base,
                    "--galaxy-tag", a.galaxy_tag, "--run", a.run,
                    "--merge-only"], check=True)


if __name__ == "__main__":
    main()
