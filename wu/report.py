"""Plots + markdown report generation (matplotlib Agg, English labels to avoid
missing CJK fonts; narrative text is Japanese in report.md)."""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def _md_table(headers, rows):
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(out)


def make_plots(out_dir: str, per: dict, glob: dict, hier, degree_hist=None):
    os.makedirs(out_dir, exist_ok=True)
    n_c = per["N_c"]
    keep = n_c > 0

    # 1. community size histogram
    fig, ax = plt.subplots(figsize=(7, 4))
    sizes = n_c[keep]
    ax.hist(sizes, bins=np.logspace(np.log10(max(1, sizes.min())), np.log10(sizes.max()), 50))
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("community size (nodes)")
    ax.set_ylabel("#communities")
    ax.set_title(f"Community size distribution (C={glob['n_communities']})")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "size_hist.png"), dpi=110)
    plt.close(fig)

    # 2. out_ratio distribution (unweighted + node-weighted)
    fig, ax = plt.subplots(figsize=(7, 4))
    orr = per["out_ratio_c"][keep]
    w = n_c[keep].astype(float)
    ax.hist(orr, bins=40, range=(0, 1), weights=np.ones_like(orr) / len(orr),
            alpha=0.6, label="per-community (uniform)")
    ax.hist(orr, bins=40, range=(0, 1), weights=w / w.sum(),
            alpha=0.6, label="node-weighted")
    ax.set_xlabel("out_ratio = E_out/(E_in+E_out)")
    ax.set_ylabel("share")
    ax.set_title("Inter-community out_ratio distribution")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "out_ratio_hist.png"), dpi=110)
    plt.close(fig)

    # 3. conductance distribution
    fig, ax = plt.subplots(figsize=(7, 4))
    cc = per["conductance_c"][keep]
    ax.hist(cc[~np.isnan(cc)], bins=40)
    ax.set_xlabel("conductance = E_out/(2*E_in+E_out)")
    ax.set_ylabel("#communities")
    ax.set_title("Conductance distribution")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "conductance_hist.png"), dpi=110)
    plt.close(fig)

    # 4. E_in vs E_out scatter (independence map)
    fig, ax = plt.subplots(figsize=(7, 5))
    m = keep & (per["E_in_c"] > 0)
    ax.scatter(per["E_in_c"][m], per["E_out_c"][m], s=np.clip(n_c[m] / 20, 3, 200),
               c=per["out_ratio_c"][m], cmap="viridis", alpha=0.7)
    lim = max(per["E_in_c"][m].max(), per["E_out_c"][m].max())
    ax.plot([1, lim], [1, lim], "k--", lw=1, label="E_out = E_in")
    ax.plot([1, lim], [1, lim * 0.2], "g:", lw=1.5, label="out_ratio = 0.17")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("E_in (internal edges)")
    ax.set_ylabel("E_out (inter-community edges)")
    ax.set_title("Independence map (marker size ~ N_c, color = out_ratio)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "ein_vs_eout.png"), dpi=110)
    plt.close(fig)

    # 5. boundary/ext leakage
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(per["ext_ratio_c"][keep & ~np.isnan(per["ext_ratio_c"])], bins=40, range=(0, 1))
    ax.set_xlabel("ext_ratio = E_ext/(E_in+E_out+E_ext)  [links leaving the SUBSET]")
    ax.set_ylabel("#communities")
    ax.set_title("Subset-leakage ratio per community")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "ext_ratio_hist.png"), dpi=110)
    plt.close(fig)

    # 6. hierarchy scatter
    if hier and hier.get("clusters"):
        rows = hier["clusters"]
        fig, ax = plt.subplots(figsize=(7, 5))
        xs = [r["N_nodes"] for r in rows]
        ys = [(r["out_ratio2"] if r["out_ratio2"] is not None else np.nan) for r in rows]
        ss = [max(20, r["N_nodes"] / max(xs) * 900) for r in rows]
        ax.scatter(xs, ys, s=ss, alpha=0.65, c=range(len(rows)), cmap="tab20")
        for r in rows[:12]:
            if r["out_ratio2"] is not None:
                ax.annotate(f"C{r['cluster']}", (r["N_nodes"], r["out_ratio2"]), fontsize=8)
        ax.set_xscale("log")
        ax.set_xlabel("cluster size (nodes)")
        ax.set_ylabel("out_ratio at level-2")
        ax.set_title(f"Level-2 clusters (K={hier['global']['n_clusters']})")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "hierarchy.png"), dpi=110)
        plt.close(fig)
    return [f for f in sorted(os.listdir(out_dir)) if f.endswith(".png")]


def fmt(x, nd=3):
    if x is None:
        return "-"
    if isinstance(x, float):
        if np.isnan(x):
            return "nan"
        return f"{x:.{nd}f}"
    return f"{x:,}" if isinstance(x, (int, np.integer)) else str(x)


def write_report(path: str, ctx: dict):
    """ctx keys: subset_name, subset_meta, glob, per, labels, top_pairs, hub_rows,
    hub_stats, hier, sweep, full_stats(optional), meta(extra provenance)."""
    g = ctx["glob"]
    per = ctx["per"]
    sm = ctx.get("subset_meta", {})
    lines = []
    A = lines.append
    A(f"# コミュニティ分割 PoC レポート: {ctx['subset_name']}")
    A("")
    A(f"生成: {ctx['meta'].get('generated_at','')} / seed={ctx['meta'].get('seed')} / "
      f"resolution={ctx['meta'].get('resolution')} / dump={ctx['meta'].get('dump_date','?')}")
    A("")
    A("## 1. サブセット概要")
    A("")
    A(_md_table(["項目", "値"], [
        ["戦略", sm.get("strategy", "?")],
        ["ノード数", fmt(sm.get("n_nodes"))],
        ["内部エッジ(有向・解決後)", fmt(sm.get("n_edges_internal_directed"))],
        ["内部エッジ(無向・一意)", fmt(g.get("n_edges_internal_undirected"))],
        ["サブセット外へのリンク(outgoing)", fmt(sm.get("n_edges_outgoing"))],
        ["サブセット外からのリンク(incoming)", fmt(sm.get("n_edges_incoming"))],
        ["リーク率 out/(int+out)", fmt(sm.get("leak_ratio_out"))],
        ["ページID範囲", f"{fmt(sm.get('page_id_min'))}〜{fmt(sm.get('page_id_max'))}"],
    ]))
    A("")
    A("## 2. resolution スイープ(コミュニティサイズ分布)")
    A("")
    A(_md_table(["resolution", "#comms", "最大シェア", "top5シェア", "サイズ中央値", "p25", "p75", "cross_edge_fraction"],
                [[s["resolution"], s["n_communities"], fmt(s["largest_comm_share"]),
                  fmt(s["top5_comm_share"]), fmt(s["p50"]), fmt(s["p25"]), fmt(s["p75"]),
                  fmt(s["cross_edge_fraction"])] for s in ctx.get("sweep", [])]))
    A("")
    A("## 3. 全体指標(採用 resolution)")
    A("")
    A(_md_table(["指標", "値"], [
        ["コミュニティ数", fmt(g.get("n_communities"))],
        ["最大コミュニティのノードシェア", fmt(g.get("largest_comm_share"))],
        ["上位5コミュニティのシェア", fmt(g.get("top5_comm_share"))],
        ["cross_edge_fraction(全体)", fmt(g.get("cross_edge_fraction"))],
        ["out_ratio(エッジ重み付き平均)", fmt(g.get("mean_out_ratio_edge_weighted"))],
        ["out_ratio(ノード重み付き中央値)", fmt(g.get("node_weighted_median_out_ratio"))],
        ["out_ratio p25/p50/p75", "/".join(fmt(g.get("out_ratio_percentiles", {}).get(f"p{p}")) for p in (25, 50, 75))],
        ["conductance p25/p50/p75", "/".join(fmt(g.get("conductance_percentiles", {}).get(f"p{p}")) for p in (25, 50, 75))],
        ["サブセット外へのリンク数(有向)", fmt(g.get("n_edges_to_outside_subset"))],
        ["サブセット外からのリンク数(有向)", fmt(g.get("n_edges_from_outside_subset"))],
    ]))
    A("")
    A("## 4. 上位コミュニティ(サイズ順 top 15)")
    A("")
    order = np.argsort(-per["N_c"])[:15]
    A(_md_table(["comm", "代表記事", "N_c", "E_in", "E_out", "E_ext(場外)", "out_ratio",
                 "conductance", "avg_deg", "境界ノード率"],
                [[int(c), "、".join(ctx["labels"].get(int(c), [])[:3]),
                  fmt(int(per["N_c"][c])), fmt(int(per["E_in_c"][c])), fmt(int(per["E_out_c"][c])),
                  fmt(int(per["E_ext_c"][c])), fmt(float(per["out_ratio_c"][c])),
                  fmt(float(per["conductance_c"][c])), fmt(float(per["avg_degree_c"][c]), 1),
                  fmt(float(per["boundary_node_ratio_c"][c]))] for c in order if per["N_c"][c] > 0]))
    A("")
    A("## 5. コミュニティ間エッジ top 20")
    A("")
    A(_md_table(["comm A", "comm B", "エッジ数", "A代表", "B代表"],
                [[p["comm_a"], p["comm_b"], fmt(p["edges"]),
                  "、".join(ctx["labels"].get(p["comm_a"], [])[:2]),
                  "、".join(ctx["labels"].get(p["comm_b"], [])[:2])]
                 for p in ctx.get("top_pairs", [])]))
    A("")
    A("## 6. ハブ記事の影響(次数 top 20)")
    A("")
    A(_md_table(["#", "記事", "内部次数", "場外次数", "comm", "フラグ"],
                [[h["rank"], h["title"], fmt(h["deg_internal"]), fmt(h["deg_outside"]),
                  h["comm"], ",".join(h["flags"])] for h in ctx.get("hub_rows", [])[:20]]))
    hs = ctx.get("hub_stats", {})
    A("")
    A(f"- 上位1%ノードが接する内部エッジのシェア: {fmt(hs.get('top1pct_internal_edge_incidence_share'))}")
    A(f"- 内部次数 max: {fmt(hs.get('max_internal_degree'))} / "
      f"p50・p90・p99: {fmt(hs.get('degree_percentiles', {}).get('p50'))}・"
      f"{fmt(hs.get('degree_percentiles', {}).get('p90'))}・{fmt(hs.get('degree_percentiles', {}).get('p99'))}")
    A("")
    A("## 7. 階層化テスト(level-2)")
    A("")
    hier = ctx.get("hier")
    if hier:
        hg = hier["global"]
        A(_md_table(["指標", "値"], [
            ["上位クラスタ数 K", fmt(hg.get("n_clusters"))],
            ["最大クラスタのシェア", fmt(hg.get("largest_cluster_share"))],
            ["out_ratio2(エッジ重み付き平均)", fmt(hg.get("mean_out_ratio2_edge_weighted"))],
            ["コミュニティ間グラフ modularity", fmt(hg.get("modularity_community_graph"))],
        ]))
        A("")
        rows = sorted(hier["clusters"], key=lambda r: -r["N_nodes"])[:15]
        A(_md_table(["cluster", "#comms", "N_nodes", "E_in(記事)", "E_between(comm間)",
                     "E_out2", "E_ext(場外)", "out_ratio2", "conductance2"],
                    [[r["cluster"], r["n_comms"], fmt(r["N_nodes"]), fmt(r["E_in_articles"], 0),
                      fmt(r["E_between_comms"], 0), fmt(r["E_out2"], 0), fmt(r["E_ext_outside_subset"], 0),
                      fmt(r["out_ratio2"]), fmt(r["conductance2"])] for r in rows]))
    else:
        A("(コミュニティ数が少なく階層化テストはスキップ)")
    A("")
    A("## 8. 図")
    A("")
    for p in ctx.get("plots", []):
        A(f"![{p}]({p})")
    A("")
    with open(path, "w") as f:
        f.write("\n".join(lines))
    return path
