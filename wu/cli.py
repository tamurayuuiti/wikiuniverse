"""CLI entry point: python -m wu <command> [options]

正式パイプラインの実行系はステージレジストリ経由(stages / plan / run)。
取得・解析・コミュニティ検出・カタログ・座標・出版・監査はすべて
`python -m wu run <stage>` で実行する(契約は wu/stages/ と wu/pipeline/)。

研究・実験系コマンド(ステージ化していないもの):
  subset-id  Extract page-id-window subset (対照/ベースライン用)
  subset-bfs Extract BFS subset from seed titles
  analyze    Leiden + metrics + hierarchy + report for one subset

Pipeline (stage registry + config + runner; see wu/pipeline/):
  stages     List registered stages with their input/output contracts
  plan       Show the execution plan (run/skip/missing-input) without side effects
  run        Execute stages (name... or "all") and record a manifest under
             data/manifests/ (resume = existing outputs are skipped, --force to redo)
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

from .dumpio import read_json
from .paths import Dirs


def cmd_subset_id(a):
    from .experiments.subsets import extract_subset, select_id_window
    dirs = Dirs(a.base)
    pids = select_id_window(dirs.parsed, a.k, a.mode)
    name = a.name or f"idwin_{a.mode}_{a.k // 1000}k"
    out = dirs.subset(name)
    dirs.ensure(out)
    meta = {"strategy": f"page_id_window({a.mode})", "k": a.k,
            "dump_date": _dump_date(dirs), "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
    extract_subset(dirs.edges_bin, pids, out, meta, parsed_dir=dirs.parsed)


def cmd_subset_bfs(a):
    from .experiments.subsets import bfs_nodes, extract_subset, title_to_pid
    dirs = Dirs(a.base)
    seeds = [s.strip() for s in a.seeds.split(",") if s.strip()]
    seed_pids, missing = [], []
    for s in seeds:
        p = title_to_pid(dirs.parsed, s)
        (seed_pids.append(p) if p else missing.append(s))
    if missing:
        print(f"[warn] seeds not found: {missing}")
    if not seed_pids:
        sys.exit("no valid seeds")
    nodes, info = bfs_nodes(dirs.edges_bin, seed_pids,
                            max_nodes=a.max_nodes, cap_per_node=a.cap,
                            max_hops=a.hops, rng_seed=a.seed,
                            parsed_dir=dirs.parsed, graph_dir=dirs.graph,
                            max_in_degree=a.max_in_degree)
    name = a.name or f"bfs_{len(nodes) // 1000}k"
    out = dirs.subset(name)
    dirs.ensure(out)
    meta = {"strategy": "outlink_bfs", "seeds": seeds, "seeds_missing": missing,
            "seed_page_ids": seed_pids, "max_nodes": a.max_nodes, "cap_per_node": a.cap,
            "max_hops": a.hops, "rng_seed": a.seed, "bfs_info": info,
            "dump_date": _dump_date(dirs), "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
    extract_subset(dirs.edges_bin, nodes, out, meta, parsed_dir=dirs.parsed)


def cmd_analyze(a):
    from .experiments.subset_analysis import analyze_subset
    dirs = Dirs(a.base)
    subset_dir = a.subset_dir or dirs.subset(a.subset)
    out_dir = a.out_dir or dirs.community_run(os.path.basename(subset_dir.rstrip("/")))
    dirs.ensure(out_dir)
    resolutions = tuple(float(x) for x in a.resolutions.split(","))
    analyze_subset(subset_dir, out_dir, resolutions=resolutions, seed=a.seed,
                   full_stats_json=None, dirs=dirs,
                   hub_indeg_cap=a.hub_indeg_cap, hub_outdeg_cap=a.hub_outdeg_cap,
                   degree_budget=a.degree_budget, degree_budget_mode=a.degree_budget_mode,
                   hub_weight=a.hub_weight, engine=a.engine)


def _dump_date(dirs: Dirs) -> str:
    m = read_json(dirs.meta, {}) or {}
    return m.get("dump_date", "?")


def cmd_stages(a):
    """登録済みステージの一覧(分類・入出力契約・パラメータ)を表示する。"""
    import wu.stages  # noqa: F401  (import によりステージが登録される)
    from .pipeline.stage import all_stages
    groups = {}
    for st in all_stages():
        groups.setdefault(st.group, []).append(st)
    print("[wu] 登録済みステージ(group = canonical 正式 / optional 随伴 /"
          " audit 読み取り専用監査 / experiment 研究・実験系):")
    for g in ("canonical", "optional", "audit", "experiment"):
        for st in groups.get(g, []):
            print(f"\n  {st.name}  [{g}]  {st.title}")
            if st.inputs:
                print(f"    in : {', '.join(st.inputs)}")
            if st.outputs:
                print(f"    out: {', '.join(st.outputs)}")
            for p in st.params:
                ch = f" choices={list(p.choices)}" if p.choices else ""
                print(f"    --{p.name.replace('_', '-')}"
                      f" (既定 {p.default!r}{ch}): {p.help}")


def _pipeline_args(a):
    """plan/run 共通: 設定読み込み + 共有パラメータ解決。"""
    import wu.stages  # noqa: F401
    from .pipeline import config as pcfg
    cfg = pcfg.load(a.config)
    cfg = pcfg.apply_overrides(cfg, getattr(a, "overrides", None))
    shared = pcfg.shared_params(cfg, {
        "run": getattr(a, "run", None),
        "galaxy_tag": getattr(a, "galaxy_tag", None),
        "macro_tag": getattr(a, "macro_tag", None),
        "dump_date": getattr(a, "dump_date", None),
    })
    return cfg, shared


def cmd_plan(a):
    """実行計画(実行/スキップ/入力不足)を表示する(dry-run。副作用なし)。"""
    from .pipeline import runner
    from .paths import Dirs
    cfg, shared = _pipeline_args(a)
    stages = runner.expand_targets(a.targets)
    entries = runner.plan(stages, Dirs(a.base), cfg, shared, force=a.force)
    print(runner.format_plan(entries))


def cmd_run(a):
    """ステージを実行し、実行台帳(manifest)を data/manifests/ へ記録する。"""
    from .pipeline import runner
    from .paths import Dirs
    cfg, shared = _pipeline_args(a)
    stages = runner.expand_targets(a.targets)
    entries = runner.plan(stages, Dirs(a.base), cfg, shared, force=a.force)
    print(runner.format_plan(entries))
    label = a.label or "-".join(t for t in a.targets)[:40]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc = runner.execute(entries, Dirs(a.base), cfg, shared, label,
                        repo_root=repo_root)
    sys.exit(rc)


def main():
    # Windows コンソール(cp932 等)で日本語タイトル出力時にクラッシュしないための保険
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="wu")
    ap.add_argument("--base", default="data",
                    help="base dir; layout: dump/ parsed/ graph/ community/ (see wu/paths.py)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("subset-id")
    p.add_argument("--k", type=int, default=10000); p.add_argument("--mode", default="late",
                    choices=["late", "early", "middle"]); p.add_argument("--name", default=None)
    p.set_defaults(fn=cmd_subset_id)

    p = sub.add_parser("subset-bfs")
    p.add_argument("--seeds", required=True); p.add_argument("--max-nodes", type=int, default=30000)
    p.add_argument("--cap", type=int, default=40); p.add_argument("--hops", type=int, default=4)
    p.add_argument("--max-in-degree", type=int, default=10000)
    p.add_argument("--seed", type=int, default=42); p.add_argument("--name", default=None)
    p.set_defaults(fn=cmd_subset_bfs)

    p = sub.add_parser("analyze")
    p.add_argument("--subset", default=None, help="subset name under <base>/graph/subsets/")
    p.add_argument("--subset-dir", default=None, help="explicit subset dir (overrides --subset)")
    p.add_argument("--out-dir", default=None, help="default: <base>/community/<subset>")
    p.add_argument("--resolutions", default="0.5,1.0,2.0")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--hub-indeg-cap", type=int, default=None)
    p.add_argument("--hub-outdeg-cap", type=int, default=None)
    p.add_argument("--degree-budget", type=int, default=None)
    p.add_argument("--degree-budget-mode", default="smart", choices=["smart", "both"])
    p.add_argument("--hub-weight", default=None, help="'log' or 'deg<alpha>' e.g. deg1, deg0.5")
    p.add_argument("--engine", default="igraph", choices=["igraph", "leidenalg"])
    p.set_defaults(fn=cmd_analyze)

    # ---- パイプライン基盤(stages/plan/run)。ステージ契約は wu/stages/ 参照 ----
    p = sub.add_parser("stages", help="登録済みステージと入出力契約の一覧")
    p.set_defaults(fn=cmd_stages)

    def _add_pipe_args(sp, with_run_opts=True):
        sp.add_argument("targets", nargs="+",
                        help="ステージ名(複数可)または all(= canonical 全 chain)")
        sp.add_argument("--config", default=None,
                        help="設定 JSON(既定: なし = ステージ既定値のみ。"
                             "リポジトリ同梱の正準設定は configs/canonical.json)")
        sp.add_argument("--set", action="append", dest="overrides",
                        metavar="STAGE.KEY=VALUE",
                        help="パラメータ上書き(複数可。値は JSON リテラル解釈)")
        sp.add_argument("--galaxy-tag", default=None)
        sp.add_argument("--macro-tag", default=None)
        sp.add_argument("--force", action="store_true",
                        help="生成物があっても再実行する")
        if with_run_opts:
            sp.add_argument("--run", default=None,
                            help="座標 run 名(既定: 正典 ACTIVE_LAYOUT_RUN)")
            sp.add_argument("--label", default=None,
                            help="manifest ファイル名用ラベル")

    p = sub.add_parser("plan", help="実行計画の表示(dry-run、副作用なし)")
    _add_pipe_args(p, with_run_opts=False)
    p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("run", help="ステージ実行 + manifest 記録")
    _add_pipe_args(p)
    p.set_defaults(fn=cmd_run)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
