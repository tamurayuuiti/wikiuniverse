"""Compare pagelinks-derived vs body-text-derived out-degrees for the same articles.

Quantifies the template/transclusion inflation of the pagelinks table:
  inflation_ratio(article) = deg_pagelinks / max(1, deg_body)

Also counts "pure template articles" (deg_body == 0 but deg_pagelinks >= 50):
articles whose entire link footprint comes from transcluded templates.

Usage:
  python -m wu.experiments.compare_body_vs_pagelinks --base data \
      [--body edges_body_directed.bin] [--max-pid N] [--top 25]

--max-pid restricts to articles with page_id <= N (useful when the body graph
was built from a partial dump, e.g. pages-articles1 covers p1..p114794).
Output: graph/body_vs_pagelinks.json + console table.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import numpy as np


from ..dumpio import write_json  # noqa: E402
from ..paths import Dirs  # noqa: E402
from ..stats import load_edges_mmap, load_titles  # noqa: E402


def out_degree_per_article(edges_bin: str, article_ids: np.ndarray,
                           max_pid: int | None = None, chunk: int = 5_000_000) -> np.ndarray:
    n = len(article_ids)
    E = load_edges_mmap(edges_bin)
    deg = np.zeros(n, np.int64)
    hi = np.searchsorted(article_ids, max_pid, side="right") if max_pid else n
    for i in range(0, len(E), chunk):
        blk = np.asarray(E[i : i + chunk])
        s = blk[:, 0].astype(np.int64)
        if max_pid:
            s = s[s <= max_pid]
            if len(s) == 0:
                continue
        pos = np.clip(np.searchsorted(article_ids, s), 0, n - 1)
        deg += np.bincount(pos, minlength=n)
    return deg[:hi], hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data")
    ap.add_argument("--body", default="edges_body_directed.bin")
    ap.add_argument("--pagelinks", default="edges_ns0.bin")
    ap.add_argument("--max-pid", type=int, default=None)
    ap.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    dirs = Dirs(a.base)
    article_ids = np.load(dirs.article_ids)

    pl_path = os.path.join(dirs.graph, a.pagelinks)
    bd_path = os.path.join(dirs.graph, a.body)
    for p in (pl_path, bd_path):
        if not os.path.exists(p):
            sys.exit(f"missing: {p}")

    deg_pl, hi = out_degree_per_article(pl_path, article_ids, a.max_pid)
    deg_bd, _ = out_degree_per_article(bd_path, article_ids, a.max_pid)

    ids = article_ids[:hi]
    # paired stats over articles covered by the body graph (deg_bd defined for all,
    # but articles absent from the body dump show 0 - restrict to those with pl>0 or bd>0)
    cov = (deg_pl > 0) | (deg_bd > 0)
    pl, bd = deg_pl[cov], deg_bd[cov]
    ratio = pl / np.maximum(1, bd)

    def pct(x, ps=(10, 25, 50, 75, 90, 99)):
        return {f"p{p}": round(float(np.percentile(x, p)), 3) for p in ps}

    pure_template = int(((deg_bd == 0) & (deg_pl >= 50)).sum())
    stats = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "max_pid": a.max_pid, "articles_compared": int(cov.sum()),
        "pagelinks_outdeg": {"mean": round(float(pl.mean()), 2), **pct(pl)},
        "body_outdeg": {"mean": round(float(bd.mean()), 2), **pct(bd)},
        "inflation_ratio_pl_over_body": {"mean": round(float(ratio.mean()), 2), **pct(ratio)},
        "pearson_deg_pl_deg_bd": round(float(np.corrcoef(pl, bd)[0, 1]), 4),
        "spearman_deg_pl_deg_bd": round(float(np.corrcoef(
            np.argsort(np.argsort(pl)), np.argsort(np.argsort(bd)))[0, 1]), 4),
        "pure_template_articles_bd0_pl50+": pure_template,
        "share_pure_template": round(pure_template / max(1, int(cov.sum())), 4),
    }

    pid_col, title_col = load_titles(dirs.parsed)
    order = np.argsort(-(deg_pl - deg_bd))[: a.top]
    top = []
    for i in order:
        if not cov[i]:
            continue
        pos = int(np.clip(np.searchsorted(pid_col, ids[i]), 0, len(pid_col) - 1))
        top.append({"title": title_col[pos].as_py(), "page_id": int(ids[i]),
                    "deg_pagelinks": int(deg_pl[i]), "deg_body": int(deg_bd[i]),
                    "ratio": round(float(deg_pl[i] / max(1, deg_bd[i])), 2)})
    out = {"stats": stats, "top_inflated": top}
    write_json(os.path.join(dirs.graph, "body_vs_pagelinks.json"), out)

    print(json.dumps(stats, ensure_ascii=False, indent=1))
    print(f"\nTop inflated articles (pagelinks deg - body deg):")
    for t in top[:15]:
        print(f"  {t['title'][:40]:42s} pl={t['deg_pagelinks']:6d} body={t['deg_body']:5d} "
              f"x{t['ratio']:.1f}")
    print(f"\nwrote {os.path.join(dirs.graph, 'body_vs_pagelinks.json')}")


if __name__ == "__main__":
    main()
