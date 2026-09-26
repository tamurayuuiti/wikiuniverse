"""CLI entry point: python -m wu.cli <command> [options]

Data layout (policy 2026-09-25, see wu/paths.py):
  <base>/dump/  <base>/parsed/  <base>/graph/(+subsets/)  <base>/community/

Commands:
  download   Download dump files (page, redirect, linktarget, pagelinks[, categorylinks])
  parse      Build parsed artifacts from page/linktarget/redirect dumps
  edges      Stream pagelinks -> resolved ns0 edge list (checkpointed)
  stats      Full-graph degree/hub statistics
  subset-id  Extract page-id-window subset (contrast/baseline use)
  subset-bfs Extract BFS subset from seed titles
  analyze    Leiden + metrics + hierarchy + report for one subset
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys

from .dumpio import download_all, read_json, write_json
from .paths import Dirs


def cmd_download(a):
    dirs = Dirs(a.base)
    dirs.ensure(dirs.dump)
    download_all(a.files.split(","), dirs.dump, workers=a.workers)


def cmd_parse(a):
    from .dumpio import FILES
    from .sqlparse import (build_linktarget_artifacts, build_page_artifacts,
                           build_redirect_artifacts)
    dirs = Dirs(a.base)
    dirs.ensure(dirs.parsed)
    build_page_artifacts(dirs.dump_file(FILES["page"]), dirs.parsed, dirs.meta)
    build_linktarget_artifacts(dirs.dump_file(FILES["linktarget"]), dirs.parsed, dirs.meta)
    build_redirect_artifacts(dirs.dump_file(FILES["redirect"]), dirs.parsed, dirs.meta)
    meta = read_json(dirs.meta, {}) or {}
    meta["dump_date"] = a.dump_date or meta.get("dump_date") or "unknown"
    meta["parsed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    meta.setdefault("files", {})
    for k in ("page", "redirect", "linktarget", "pagelinks"):
        f = dirs.dump_file(FILES[k])
        if os.path.exists(f):
            meta["files"][k] = {"bytes": os.path.getsize(f), "name": FILES[k]}
    meta.setdefault("layout", {"policy": "2026-09-25",
                               "dirs": {"dump": dirs.dump, "parsed": dirs.parsed,
                                        "graph": dirs.graph, "community": dirs.community}})
    write_json(dirs.meta, meta)


def cmd_edges(a):
    from .buildedges import build_edges
    from .dumpio import FILES
    dirs = Dirs(a.base)
    dirs.ensure(dirs.graph)
    build_edges(dirs.dump_file(FILES["pagelinks"]), dirs.parsed,
                dirs.edges_bin, dirs.edges_ckpt, chunk_bytes=a.chunk_bytes)


def cmd_stats(a):
    from .stats import full_stats
    full_stats(Dirs(a.base))


def cmd_subset_id(a):
    from .subsets import extract_subset, select_id_window
    dirs = Dirs(a.base)
    pids = select_id_window(dirs.parsed, a.k, a.mode)
    name = a.name or f"idwin_{a.mode}_{a.k // 1000}k"
    out = dirs.subset(name)
    dirs.ensure(out)
    meta = {"strategy": f"page_id_window({a.mode})", "k": a.k,
            "dump_date": _dump_date(dirs), "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
    extract_subset(dirs.edges_bin, pids, out, meta, parsed_dir=dirs.parsed)


def cmd_subset_bfs(a):
    from .subsets import bfs_nodes, extract_subset, title_to_pid
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


def cmd_categories(a):
    from .catparse import build_category_artifacts
    from .dumpio import FILES
    dirs = Dirs(a.base)
    dirs.ensure(dirs.graph)
    build_category_artifacts(dirs.dump_file(FILES["categorylinks"]),
                             dirs.dump_file(FILES["linktarget"]),
                             dirs.parsed, dirs.graph, meta_path=dirs.meta)


def cmd_body_edges(a):
    from .xmlparse import build_body_edges
    from .dumpio import FILES
    dirs = Dirs(a.base)
    dirs.ensure(dirs.graph)
    xml = a.xml or dirs.dump_file(FILES[a.source])
    if not os.path.exists(xml):
        sys.exit(f"XML dump not found: {xml}\n"
                 f"download: python -m wu.cli --base {a.base} download --files {a.source}")
    build_body_edges(xml, dirs.parsed,
                     os.path.join(dirs.graph, a.out_name),
                     os.path.join(dirs.graph, "body_checkpoint.json"),
                     limit_pages=a.limit_pages, strip_refs=a.strip_refs)


def cmd_analyze(a):
    from .pipeline import analyze_subset
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

    p = sub.add_parser("download"); p.add_argument("--files", default="page,redirect,linktarget,pagelinks")
    p.add_argument("--workers", type=int, default=3); p.set_defaults(fn=cmd_download)

    p = sub.add_parser("parse"); p.add_argument("--dump-date", default=None); p.set_defaults(fn=cmd_parse)
    p = sub.add_parser("edges"); p.add_argument("--chunk-bytes", type=int, default=1 << 24); p.set_defaults(fn=cmd_edges)
    p = sub.add_parser("stats"); p.set_defaults(fn=cmd_stats)

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

    p = sub.add_parser("categories"); p.set_defaults(fn=cmd_categories)

    p = sub.add_parser("body-edges")
    p.add_argument("--source", default="pages-articles",
                   choices=["pages-articles", "pages-articles1"],
                   help="which dump file key to use when --xml is not given")
    p.add_argument("--xml", default=None, help="explicit path to pages-articles*.xml.bz2")
    p.add_argument("--out-name", default="edges_body_directed.bin")
    p.add_argument("--limit-pages", type=int, default=0, help="process at most N pages (0=all)")
    p.add_argument("--strip-refs", action="store_true",
                   help="also remove <ref>...</ref> citation links")
    p.set_defaults(fn=cmd_body_edges)

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

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
