# wu/audit_layout.py — 座標 run の監査(読み取り専用・再計算なし)
#
# 責務: ①spill 一覧(超過順・文脈列付き)②マクロ毎の平坦度+重心配置率
#   (同成員数の等方 null ベースライン対比、真の円盤候補を * 表示)
#   ③corr(flat, bary_frac) ④layout_meta との突合(不一致は必ず WARN)。
# 注意: 旧 scripts/audit_layout.py から移設(2026-10-02)。平坦度は layout_global
#   から import して式・ドメインの同一性を保証する。cp932 コンソール安全
#   (出力文字は cp932 エンコード可能範囲に制限 + errors=replace 保険、テスト済み)。

"""Audit a coordinate-layout run (data/layout/<run>/) — read-only, no recompute.

Third audit script (after audit_tiles / audit_names). layout_global.py records
quality NUMBERS in layout_meta.json but not their context; this script answers
"which galaxies, in which surroundings" from the published parquets alone:

  1. spill list    non-dust galaxies whose sphere does not fit inside their
                   macro sphere (d3 + r > 1.02 Rm — the same rule as
                   quality.galaxy_spill_count), sorted by excess, with the
                   context columns (class, sizes, r/Rm, macro scale, name)
                   needed to tell placement-regime causes apart.
  2. per macro     galaxy-cloud flatness (the same PCA anisotropy as
                   quality.galaxy_flat_*, imported from layout_global so the
                   numbers are identical) + bary_frac = the share of members
                   placed by the centroid rule (medium galaxies + regular
                   galaxies with zero intra-macro pairs). Each row carries a
                   per-n ISOTROPIC NULL baseline (fixed seed): the eigenvalue
                   ratio is heavily small-n biased (isotropic clouds measure
                   ~0.96 at n=4), so macros with flat > null p90 are flagged
                   (*) as real pancake candidates.
  3. corr(flat, bary_frac)   Pearson r over macros with >= 4 members:
                   quantifies whether disk-like macro clouds are explained by
                   centroid-placed members piling up near the boundary in the
                   direction of their link targets.
  4. run summary   layout_meta params/quality cross-checked against the
                   recomputation (the flatness cross-check follows the run's
                   galaxy_flat_domain_min; legacy runs without the key are
                   compared on the old n>=4 domain). Any mismatch prints a
                   WARN (never silent — same principle as the viewer's
                   local# fallback warning).

Reads: layout/<run>/{galaxy_positions,macro_positions}.parquet + layout_meta.json
(required); final/galaxies.parquet (names), final/macros.parquet (labels) and
final/galaxy_pairs_topK.parquet (intra-macro degrees) are OPTIONAL context —
missing files/columns degrade with a warning, never with a KeyError.
Writes nothing.

Usage:
  python scripts/audit_layout.py --base data [--run RUN]
      # --run default = wu.paths.ACTIVE_LAYOUT_RUN
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from .layout_global import flatness  # noqa: E402  (指標の単一の真実源)
from .dumpio import read_json  # noqa: E402
from .paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402

SPILL_TOL = 1.02  # containment tolerance of the layout quality metric
TOP_IDS = 20      # galaxy_spill_ids cap recorded in layout_meta.json

_NULL_CACHE: dict = {}


def _null_flat(n: int, samples: int = 200):
    """(mean, p90) of flatness for a PERFECTLY ISOTROPIC n-point cloud.

    The PCA eigenvalue ratio is heavily small-n biased (n=4 -> mean ~0.96):
    this null is the baseline for deciding whether a macro's cloud is a real
    pancake or just small. Fixed seed + numpy's RNG stability policy =>
    identical numbers on any machine/run.
    """
    if n not in _NULL_CACHE:
        rng = np.random.default_rng(12345)
        fl = np.array([flatness(rng.normal(size=(n, 3)))
                       for _ in range(samples)])
        _NULL_CACHE[n] = (round(float(fl.mean()), 5),
                          round(float(np.percentile(fl, 90)), 5))
    return _NULL_CACHE[n]


class RunMissing(FileNotFoundError):
    """The run directory or its required artifacts do not exist (fail fast)."""


def _strings(d: dict, key: str, n: int) -> list:
    """Defensive string-column read: a missing column or None cells become "".

    Catalogs predating name curation have no name/rep_titles/display_class
    columns; the audit must report, not crash (hardening lesson from the
    fixture e2e: KeyError on a catalog without `name`).
    """
    col = d.get(key)
    if col is None:
        return [""] * n
    return ["" if x is None else str(x) for x in col]


def load_run(dirs: Dirs, run: str | None) -> dict:
    """Load one run's published artifacts. Raises RunMissing when incomplete."""
    lay = dirs.layout_run(run)
    need = ("galaxy_positions.parquet", "macro_positions.parquet",
            "layout_meta.json")
    missing = [f for f in need
               if not os.path.exists(os.path.join(str(lay), f))]
    if missing:
        raise RunMissing(
            f"run '{lay.name}' に必須ファイルがありません: {', '.join(missing)}"
            f". 先に生成してください: python scripts/layout_global.py"
            f" --base {dirs.base} --run {lay.name}")
    import pyarrow.parquet as pq
    gpos = pq.read_table(os.path.join(str(lay),
                                      "galaxy_positions.parquet")).to_pydict()
    mpos = pq.read_table(os.path.join(str(lay),
                                      "macro_positions.parquet")).to_pydict()
    meta = read_json(os.path.join(str(lay), "layout_meta.json"), {}) or {}
    return {"run": lay.name, "gpos": gpos, "mpos": mpos, "meta": meta}


def load_catalog(dirs: Dirs) -> dict:
    """Optional data/final/ context: names, macro labels, intra-macro degrees."""
    import pyarrow.parquet as pq
    cat = {"names": {}, "macro_labels": {}, "pairs": None, "warnings": []}
    gp = os.path.join(str(dirs.final), "galaxies.parquet")
    if os.path.exists(gp):
        gal = pq.read_table(gp).to_pydict()
        names = _strings(gal, "name", len(gal["galaxy_id"]))
        cat["names"] = {int(g): nm
                        for g, nm in zip(gal["galaxy_id"], names) if nm}
    else:
        cat["warnings"].append(
            "final/galaxies.parquet が見つからない: 銀河名は空で表示する")
    mp = os.path.join(str(dirs.final), "macros.parquet")
    if os.path.exists(mp):
        mac = pq.read_table(mp).to_pydict()
        reps = _strings(mac, "rep_titles", len(mac["macro_id"]))
        # ラベル規約は audit_names と同じ: 先頭要素の 16 文字
        cat["macro_labels"] = {int(m): r.split(",")[0].strip()[:16]
                               for m, r in zip(mac["macro_id"], reps)}
    pp = os.path.join(str(dirs.final), "galaxy_pairs_topK.parquet")
    if os.path.exists(pp):
        pqd = pq.read_table(pp).to_pydict()
        cat["pairs"] = (np.asarray(pqd["a"], np.int64),
                        np.asarray(pqd["b"], np.int64))
    else:
        cat["warnings"].append(
            "final/galaxy_pairs_topK.parquet が見つからない: 孤立銀河"
            "(マクロ内ペア 0 本)を判定できず、重心配置率は medium のみで数える")
    return cat


def audit(dirs: Dirs, run: str | None = None) -> dict:
    """Audit one run; returns {"spill", "macros", "corr", "summary", "warnings"}.

    spill rows are dicts sorted by excess desc; macro rows carry flatness and
    the centroid-placement share. Tests call this directly.
    """
    rd = load_run(dirs, run)
    cat = load_catalog(dirs)
    gpos, mpos, meta = rd["gpos"], rd["mpos"], rd["meta"]

    gid = np.asarray(gpos["galaxy_id"], np.int64)
    G = len(gid)
    row_of = {int(g): i for i, g in enumerate(gid)}
    gmid = np.asarray(gpos["macro_id"], np.int64)
    gxyz = np.stack([np.asarray(gpos[k], np.float64) for k in "xyz"], axis=1)
    gr = np.asarray(gpos["radius"], np.float64)
    gcls = _strings(gpos, "display_class", G)
    gn = np.asarray(gpos.get("n_articles", [0] * G), np.int64)

    mid = np.asarray(mpos["macro_id"], np.int64)
    mrow_of = {int(m): i for i, m in enumerate(mid)}
    mxyz = np.stack([np.asarray(mpos[k], np.float64) for k in "xyz"], axis=1)
    mr = np.asarray(mpos["radius"], np.float64)
    mn = np.asarray(mpos.get("n_articles", [0] * len(mid)), np.int64)
    mgal = np.asarray(mpos.get("n_galaxies", [0] * len(mid)), np.int64)

    warnings = list(cat["warnings"])

    # 監査対象は quality.galaxy_spill_* と同じ集合: 実効マクロ(R > 0)に属する
    # 非 dust 銀河(dust シェルと dust マクロの成員は包含の対象外)。
    ok_m = np.array([int(m) in mrow_of for m in gmid])
    mrow = np.array([mrow_of.get(int(m), 0) for m in gmid], np.int64)
    Rg = np.where(ok_m, mr[mrow], 0.0)
    Cg = np.where(ok_m[:, None], mxyz[mrow], 0.0)
    sel = np.asarray([c != "dust" for c in gcls]) & (Rg > 0)

    d3 = np.linalg.norm(gxyz - Cg, axis=1)
    ratio = (d3 + gr) / np.maximum(Rg, 1e-9)
    excess = d3 + gr - Rg
    spill = sel & (ratio > SPILL_TOL)

    sp_idx = np.flatnonzero(spill)
    sp_idx = sp_idx[np.lexsort((gid[sp_idx], -excess[sp_idx]))]
    spill_rows = [{
        "gid": int(gid[i]), "class": gcls[i], "n_articles": int(gn[i]),
        "r": float(gr[i]), "Rm": float(Rg[i]), "d3": float(d3[i]),
        "r_over_Rm": float(gr[i] / max(Rg[i], 1e-9)),
        "ratio": float(ratio[i]), "excess": float(excess[i]),
        "macro_n_articles": int(mn[mrow[i]]),
        "macro_n_galaxies": int(mgal[mrow[i]]),
        "name": cat["names"].get(int(gid[i]), ""),
    } for i in sp_idx]

    # 重心配置(bary)の内訳: medium + 孤立銀河(マクロ内ペア 0 本の通常銀河)。
    # 次数は layout_global の配置規則と同じく「両端が同一マクロの通常銀河」の
    # ペアのみで数える(medium 経由のペアは配置に寄与しないため数えない)。
    is_med = np.asarray([c == "medium" for c in gcls])
    is_reg = np.asarray([c == "galaxy" for c in gcls])
    intra_deg = np.zeros(G, np.int64)
    if cat["pairs"] is not None:
        pa, pb = cat["pairs"]
        ra = np.array([row_of.get(int(x), -1) for x in pa], np.int64)
        rb = np.array([row_of.get(int(x), -1) for x in pb], np.int64)
        keep = (ra >= 0) & (rb >= 0)
        ra, rb = ra[keep], rb[keep]
        same = (gmid[ra] == gmid[rb]) & is_reg[ra] & is_reg[rb]
        np.add.at(intra_deg, ra[same], 1)
        np.add.at(intra_deg, rb[same], 1)

    macro_rows = []
    for i_m in range(len(mid)):
        R = float(mr[i_m])
        if R <= 0:
            continue                      # dust/空マクロに監査対象はいない
        mem = np.flatnonzero(sel & (gmid == int(mid[i_m])))
        if len(mem) == 0:
            continue
        # 平坦度は layout_meta と同一ドメイン(FLAT_MIN_MEMBERS 以上)で cross-check
        # するが、監査表自体は 4 成員以上すべてに表示する(null 対比で解釈可能)
        flat = flatness(gxyz[mem] - mxyz[i_m]) if len(mem) >= 4 else None
        null = _null_flat(len(mem)) if len(mem) >= 3 else None
        n_med = int(is_med[mem].sum())
        n_unl = int((is_reg[mem] & (intra_deg[mem] == 0)).sum())
        macro_rows.append({
            "macro_id": int(mid[i_m]), "n_members": len(mem),
            "n_articles": int(mn[i_m]), "flat": flat,
            "null_mean": null[0] if null else None,
            "null_p90": null[1] if null else None,
            "true_disk": bool(flat is not None and null is not None
                              and flat > null[1]),
            "n_medium": n_med, "n_unlinked": n_unl,
            "bary_frac": (n_med + n_unl) / len(mem),
            "label": cat["macro_labels"].get(int(mid[i_m]), ""),
        })
    # 円盤候補(平坦なもの)を先に。flat None(成員 < 4)は末尾。
    macro_rows.sort(key=lambda r: (r["flat"] is None,
                                   -(r["flat"] or 0.0), r["macro_id"]))

    pts = [(r["flat"], r["bary_frac"]) for r in macro_rows
           if r["flat"] is not None]
    corr = None
    if len(pts) >= 3:
        f = np.asarray([p[0] for p in pts], np.float64)
        b = np.asarray([p[1] for p in pts], np.float64)
        if f.std() > 1e-12 and b.std() > 1e-12:
            corr = float(np.corrcoef(f, b)[0, 1])

    # ---- cross-check against layout_meta (WARN, never silent) ----
    q = meta.get("quality", {}) or {}
    # meta と同一ドメインで cross-check する: 新 run は quality に
    # galaxy_flat_domain_min を記録(現行 8)、旧 run はキーが無い = 旧ドメイン 4
    dom = int(q.get("galaxy_flat_domain_min", 4))
    flats_wide = [r["flat"] for r in macro_rows if r["flat"] is not None]
    flats_dom = [r["flat"] for r in macro_rows
                 if r["flat"] is not None and r["n_members"] >= dom]

    def _fstats(v):
        return ((round(float(np.mean(v)), 5),
                 round(float(np.percentile(v, 90)), 5)) if v else (None, None))

    wide_mean, wide_p90 = _fstats(flats_wide)
    flat_mean, flat_p90 = _fstats(flats_dom)
    n_true_disk = sum(1 for r in macro_rows if r["true_disk"])
    n_spill = int(spill.sum())
    if q.get("galaxy_spill_count") is not None \
            and int(q["galaxy_spill_count"]) != n_spill:
        warnings.append(
            f"WARN: spill 件数の不一致: layout_meta={q['galaxy_spill_count']}"
            f" vs 監査再計算={n_spill}(版ズレかスキーマ変更の兆候)")
    for k, v in (("galaxy_flat_mean", flat_mean),
                 ("galaxy_flat_p90", flat_p90)):
        mv = q.get(k)
        if mv is not None and v is not None and abs(float(mv) - v) > 2e-5:
            warnings.append(
                f"WARN: {k} の不一致: layout_meta={mv} vs 監査再計算={v}")
    ids_meta = q.get("galaxy_spill_ids")
    if ids_meta is not None:
        ids_audit = [r["gid"] for r in spill_rows][:TOP_IDS]
        if [int(x) for x in ids_meta] != ids_audit:
            warnings.append(
                "WARN: galaxy_spill_ids が監査の spill 一覧(超過順)と不一致: "
                f"meta={list(ids_meta)} vs audit={ids_audit}")
    elif n_spill:
        warnings.append(
            "WARN: layout_meta に galaxy_spill_ids がない(旧版レイアウトの"
            "生成物。layout_global 再実行で記録される)")

    summary = {
        "run": rd["run"], "generated_at": meta.get("generated_at"),
        "params": {k: meta.get(k) for k in
                   ("pack", "r_spacing", "seed", "macro_w_power")},
        "mode": meta.get("mode", {}),
        "n_galaxies": G, "n_macros_effective": int((mr > 0).sum()),
        "n_audited": int(sel.sum()), "n_spill": n_spill,
        "spill_frac": round(n_spill / max(1, int(sel.sum())), 5),
        "flat_domain_min": dom,
        "flat_mean": flat_mean, "flat_p90": flat_p90,
        "n_flat_macros": len(flats_dom),
        "flat_mean_wide": wide_mean, "flat_p90_wide": wide_p90,
        "n_flat_macros_wide": len(flats_wide),
        "n_true_disk": n_true_disk,
        "n_medium_total": int((is_med & sel).sum()),
        "n_unlinked_total": int((is_reg & sel & (intra_deg == 0)).sum()),
        "bary_frac_mean": (round(float(np.mean(
            [r["bary_frac"] for r in macro_rows])), 5)
            if macro_rows else None),
        "corr_flat_bary": corr, "corr_n": len(pts),
        "galaxy_spill_ids": ids_meta,
    }
    return {"spill": spill_rows, "macros": macro_rows, "corr": corr,
            "summary": summary, "warnings": warnings}


def print_report(res: dict) -> None:
    s = res["summary"]
    mode = s.get("mode") or {}
    print(f"[audit-layout] run: {s['run']}  generated_at: {s['generated_at']}")
    print(f"  params: pack={s['params'].get('pack')} "
          f"r_spacing={s['params'].get('r_spacing')} seed={s['params'].get('seed')} "
          f"macro_w_power={s['params'].get('macro_w_power')} "
          f"canonical={mode.get('canonical')}")

    print(f"\n== ① spill 一覧(d3 + r > {SPILL_TOL} Rm、超過順、dust 除外)==")
    print(f"  {s['n_spill']} / {s['n_audited']} 件"
          f"(frac={s['spill_frac']})  対象 = 実効マクロ内の非 dust 銀河")
    if res["spill"]:
        print("  gid  class   n_art      r      Rm      d3  r/Rm  (d3+r)/Rm"
              "   excess  m_nart  m_ngal  name")
        for r in res["spill"]:
            print(f"  {r['gid']:>4} {r['class']:<7} {r['n_articles']:>6}"
                  f" {r['r']:>7.2f} {r['Rm']:>7.2f} {r['d3']:>7.2f}"
                  f" {r['r_over_Rm']:>6.3f} {r['ratio']:>9.3f}"
                  f" {r['excess']:>8.2f} {r['macro_n_articles']:>7}"
                  f" {r['macro_n_galaxies']:>6}  {r['name'][:20]}")
    else:
        print("  spill なし(全銀河球がマクロ球内に収まっている)")

    print("\n== ② マクロ毎の平坦度 + 重心配置率(平坦度降順)==")
    # 出力文字は cp932 コンソール(Windows のパイプ既定)でもエンコード可能な
    # 範囲に保つこと(≈/≥/— は不可。テストで検証済み)
    print("  flat = 1 − λ3/λ1(~1 円盤、成員4以上のみ)  "
          "bary = (medium + 孤立銀河) / 成員")
    print("  null90 = 同成員数の完全等方点群の平坦度 90%点(固定seed)。"
          "* 印 = flat > null90 の「真の円盤候補」")
    print("  (小マクロは等方でも flat が高く測られる = 固有値比の小nバイアス。"
          "layout_meta の flat 統計は成員8以上のドメイン)")
    print("  macro  n_mem  n_art    flat  null90 *  n_med  n_unl  bary_frac  label")
    for r in res["macros"]:
        fl = f"{r['flat']:.3f}" if r["flat"] is not None else "-"
        nu = f"{r['null_p90']:.3f}" if r.get("null_p90") is not None else "-"
        mark = "*" if r.get("true_disk") else " "
        print(f"  {r['macro_id']:>5} {r['n_members']:>6} {r['n_articles']:>7}"
              f" {fl:>7} {nu:>7} {mark} {r['n_medium']:>6} {r['n_unlinked']:>6}"
              f" {r['bary_frac']:>10.3f}  {r['label']}")

    print("\n== ③ corr(flat, bary_frac) ==")
    if res["corr"] is None:
        print(f"  計算不能(対象マクロ {s['corr_n']} 本 < 3、または分散ゼロ)")
    else:
        verdict = ("正の相関: 重心配置成員の比率が高いマクロほど平坦 ="
                   " 『円盤化は重心配置銀河の境界付近への集中が主因』の仮説を支持"
                   if res["corr"] > 0.5 else
                   "弱い/負の相関: 円盤化の主因は重心配置の集中度ではなさそう"
                   "(他の要因を疑う)")
        print(f"  r = {res['corr']:+.4f}  (n = {s['corr_n']} マクロ)")
        print(f"  解釈: {verdict}")

    print("\n== ④ run サマリ ==")
    print(f"  銀河 {s['n_galaxies']} / 実効マクロ {s['n_macros_effective']}"
          f" / 監査対象 {s['n_audited']}"
          f" / spill {s['n_spill']}")
    print(f"  flat_mean={s['flat_mean']} flat_p90={s['flat_p90']}"
          f" (n={s['n_flat_macros']}, ドメイン=成員{s['flat_domain_min']}以上"
          f" = layout_meta と同一)"
          f"  [参考: 成員4以上全域 mean={s['flat_mean_wide']}"
          f" p90={s['flat_p90_wide']} n={s['n_flat_macros_wide']}]")
    print(f"  真の円盤候補(flat > null90): {s['n_true_disk']} マクロ"
          f"  bary_frac_mean={s['bary_frac_mean']}"
          f"  medium={s['n_medium_total']} 孤立={s['n_unlinked_total']}")
    if s.get("galaxy_spill_ids"):
        print(f"  layout_meta の galaxy_spill_ids: {s['galaxy_spill_ids']}")

    if res["warnings"]:
        print("\n-- 警告 --")
        for w in res["warnings"]:
            print(f"  {w}")


def main(a=None) -> int:
    # Console-encoding guard for Windows: when stdout/stderr is a pipe (the
    # test-suite subprocesses) Python uses the ANSI codepage (cp932), which
    # cannot encode every Unicode char and raises UnicodeEncodeError mid-report.
    # All printed text is verified cp932-encodable by the tests, so this only
    # degrades hypothetical future edits to '?' instead of crashing.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(errors="replace")
    # a=None のときだけ CLI 引数を解析する(ステージは Namespace 注入で呼ぶ)。
    if a is None:
        ap = argparse.ArgumentParser(
            description="read-only layout-run audit (spill context, flatness, "
                        "centroid-placement share, correlation)")
        ap.add_argument("--base", default="data")
        ap.add_argument("--run", default=None,
                        help=f"layout run name (default: {ACTIVE_LAYOUT_RUN})")
        a = ap.parse_args()
    try:
        res = audit(Dirs(a.base), a.run)
    except RunMissing as e:
        print(f"[audit-layout] {e}")
        return 2
    print_report(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
