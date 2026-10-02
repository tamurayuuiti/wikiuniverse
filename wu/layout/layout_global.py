# wu/layout_global.py — グローバル階層レイアウト(マクロ=銀河団 + 銀河の 3D 配置)
#
# 責務:
# - 銀河カタログ(data/final/)から宇宙の空間配置を生成する:
#   ①マクロ配置(3D 球パッキング + 重み付き FR + 球分離緩和)
#   ②マクロ内銀河配置(連結=重み付き FR / 孤立・媒介=リンク先の重み付き重心 +
#     隣接マクロ方向の決定的な円錐内散布 / 球緩和 + 無条件の包含クランプ)
#   ③dust 遠方シェル ④品質指標(layout_meta.json の quality)⑤プレビュー出力。
# - 正準方針 = 完全 3D・焼き込みレンズ無し(地図ビューは視聴時の z 圧縮)。
#
# 注意:
# - 銀河球の包含は構造保証: 無条件の包含クランプ + 体積キャップ(r <= 0.9 R_M)
#   により d3 + r <= R_M が常に成立する(spill == 0。非ゼロは実装バグ)。
#   回帰 fixture(単銀河マクロ)は test_catalog にある。
# - --r-expo は半径則の実験腕(既定 0.5 = √n でビット同一。
#   1/3 にすると銀河間で記事の体積密度が均等になる = 3D 正準との整合腕)。
# - 正準実行は python -m wu run layout_global(wu/stages/layout.py)。

"""グローバル階層レイアウト: マクロ + 銀河の 3D 配置。

設計: 銀河は所属マクロ球の内側に、互いに空間分離して配置する。
正準 = 完全 3D・焼き込みレンズ無し(地図/hybrid ビューはビューア/プレビューの
視聴時 z 圧縮であって、別レイアウトではない)。

構造不変条件(いずれも test_catalog が回帰テストする):
  - 3D 放射クランプは全マクロへ無条件に適用する。`len(P) >= 2` でゲートすると
    単一通常銀河のマクロがスキップされ、ペアの無い孤立銀河がフィボナッチスロット上で
    マクロ球から突き出せる(d3+r は 1.114 Rm に達した)。無条件クランプ +
    体積キャップ(r <= 0.9 Rm)により d3 + r <= Rm が構造的に成立する:
    galaxy_spill_count == 0(非ゼロはバグ)。
  - マクロ間 centroid 寄与は、隣接マクロ方向を軸とする決定的な円錐内へ散布する
    (_cone_spread: 半開角 <= 0.75 rad・径深 0.35-0.95、銀河×隣接マクロ毎の
    シード = ビット再現、RNG 状態を消費しない)。境界の単一点を狙わせると、
    同じ優勢隣接を共有する銀河が全て集まって薄いキャップへ潰れる。
    径深上限 0.95 は包含を保つ。

マクロ内ペアエッジを持たない銀河("unlinked": top-K リンクが全てマクロ外)は
平面へ潰さない: リンク先の重み付き重心へ固定する(medium 銀河と同じ規則)。
使えるペアが全く無い場合は決定的なフィボナッチ球スロットへ置く。

  1. マクロ層: 実効マクロへの重み付き FR(macro_dim、既定 3 = 正準)が
     macro_pairs から中心を決める; 半径 R_M ∝ sqrt(n_M)。macro_w_power は
     FR 引力の重み温度 w^tau(tau=1 中立; tau>1 で強いリンクの近接を強調)。
  2. マクロ間緩和 + 拡大ループ(native overlap <= 2% まで)。
  3. 銀河層: マクロ内の連結(リンクを持つ)銀河への重み付き FR(dim 3);
     半径 r_g = max(pack * R_M * sqrt(n_g/n_M), r_spacing * n_g^(1/3));
     体積キャップ (sum r^3)^(1/3) <= 0.9 * R_M; 球緩和 + 3D 放射クランプ
     (|center| + r <= R_M)。unlinked 銀河: リンク先の bary 重心
     (マクロ間リンクはマクロ境界方向へ引く)または fib-ball スロット。
  4. medium 銀河(display_class=medium、「銀河間物質」概念)も同じ bary 規則を
     使い、その後の押し出しパスで通常銀河との重なりを解く。
  5. dust 銀河(n=1)は遠方シェル(宇宙塵リング)へ置く。

品質指標(layout_meta.json "quality"): macro_native_overlap_frac、
macro_proj_overlap、galaxy_spill_frac/count(3D 包含)、galaxy_spill_ids
(はみ出した銀河 id の超過順 top 20 = 監査を待たずに毎 run「誰が」見える:
python -m wu run audit_layout)、galaxy_overlap_frac(マクロ内の銀河球の重なり)、
galaxy_flat_mean/p90(マクロ毎の銀河点群の PCA 異方性; ~1 = 円盤状。ドメインは
成員数 >= FLAT_MIN_MEMBERS (8) のマクロ — それ以下は等方配置でも固有値比が 1 近くに
出る小nバイアスがあるため、wu/audit/layout.py が成員数別の等方 null と対比する)、
macro_adj_recall_top5 / macro_adj_spearman(マクロ配置の意味的近接の保持度)。

出力(data/layout/<run>/。run 名は共有パラメータ run、既定は正典 run):
  galaxy_positions.parquet  galaxy_id, macro_id, x, y, z, radius, display_class, n_articles
  macro_positions.parquet   macro_id, x, y, z, radius, n_articles, n_galaxies
  layout_meta.json          パラメータ/seed/所要時間 + run 名
  preview.png               2D 射影(xy)
  preview_<mode>.html       インタラクティブ three.js プレビュー(z-compress スライダー)

Usage:
  python -m wu run layout_global --base data --run <RUN>
      parameters (defaults/descriptions): wu/stages/layout.py の Param 宣言
      override: --set layout_global.<name>=<value>
"""
from __future__ import annotations

import datetime
import json
import os
import random
import sys
import time

import numpy as np


from ..dumpio import now_iso as _now, write_json  # noqa: E402,F401
from ..paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402

R_TOTAL = 1000.0  # canvas scale: disk areas sum to pi*R_TOTAL^2
# galaxy_flat_* 品質指標のドメイン: この成員数以上のマクロだけが対象。
# `flatness` の背後の PCA 固有値比は小nバイアスが強い(等方 null の実測:
# n=4 mean 0.955 / n=6 0.879 / n=8 0.806)ため、小さいマクロは測定アーティファクトで
# パーセンタイルを支配してしまう。小マクロは wu/audit/layout.py が成員数別の
# 等方 null 対比で判定する。
FLAT_MIN_MEMBERS = 8


def _fib_ball_slots(count: int, radius: float, seed_off: int = 0) -> np.ndarray:
    """決定的な体積一様の球内スロット(フィボナッチ球面方向 + cbrt 間隔の半径)。

    孤立ノードは 3D 配置で「球内」を満たす必要がある: 平面スパイラルだと
    unlinked 成員が支配的なマクロが目に見えるパンケーキへ潰れる。"""
    k = np.arange(count, dtype=np.float64)
    n = max(1, count)
    y = 1 - 2 * (k + 0.5) / n
    rr = np.sqrt(np.maximum(0.0, 1 - y * y))
    # seed offset rotates the golden-angle phase ONLY (must not shift k: the
    # latitude/radius sequences are defined on 0..n-1)
    phi = (k + (int(seed_off) % 97) * 0.6180339887498949) * 2.399963229728653
    rad = radius * ((k + 0.5) / n) ** (1.0 / 3.0)
    return np.stack([rad * rr * np.cos(phi), rad * rr * np.sin(phi), rad * y],
                    axis=1)


_CONE_MAX = 0.75  # rad (~43 deg): half-angle of the rim cone spread


def _fib_dir(k: int) -> np.ndarray:
    """決定的な単位方向 #k(997 スロットのフィボナッチ球)。
    _cone_spread の安定な垂直フレーム構築にのみ使う。"""
    y = 1 - 2 * (k + 0.5) / 997.0
    rr = np.sqrt(max(0.0, 1 - y * y))
    phi = k * 2.399963229728653
    return np.array([rr * np.cos(phi), rr * np.sin(phi), float(y)])


def _cone_spread(d_unit: np.ndarray, seed_key: int):
    """(単位ベクトル, 径深係数 0.35-0.95): `d_unit` を軸とする円錐内の
    決定的サンプル(境界単一点への集中の解消)。

    円盤一様な円錐サンプリング(半開角は sqrt)× 方位角 × 径深を、
    `seed_key` から無理数乗算だけで導出する — RNG 状態を使わないので
    並列/resume でもビット再現する。径深上限 0.95 は包含を保つ:
    0.95*(Rm-r)+r <= Rm。
    """
    u1 = (seed_key * 0.6180339887498949) % 1.0
    u2 = (seed_key * 0.7548776662466927) % 1.0
    u3 = (seed_key * 0.5432109876543210) % 1.0
    alpha = _CONE_MAX * np.sqrt(u1)
    theta = 2 * np.pi * u2
    h = _fib_dir(int(seed_key) % 997)
    t = h - d_unit * np.dot(h, d_unit)
    tn = np.linalg.norm(t)
    if tn < 1e-9:  # d_unit (anti)parallel to h: deterministic fallback frame
        e0 = (np.array([1.0, 0, 0]) if abs(d_unit[0]) < 0.9
              else np.array([0, 1.0, 0]))
        t = e0 - d_unit * np.dot(e0, d_unit)
        tn = np.linalg.norm(t)
    t = t / tn
    b = np.cross(d_unit, t)
    e_perp = t * np.cos(theta) + b * np.sin(theta)
    v = d_unit * np.cos(alpha) + e_perp * np.sin(alpha)
    return v, 0.35 + 0.60 * u3


def fr_layout(n_nodes: int, edges, weights, dim: int, seed: int,
              w_power: float = 1.0):
    """igraph による重み付き Fruchterman-Reingold。(n,dim) float 配列を
    単位円盤へ正規化して返す。孤立ノードは決定的なスパイラルスロットを得る。
    w_power は重み温度: 引力に (w/w_max)^w_power を使う
    (1.0 = 中立; >1 で強いリンクを強調、<1 で平坦化)。"""
    import igraph as ig
    random.seed(seed)
    np.random.seed(seed)
    g = ig.Graph(n=n_nodes)
    if len(edges):
        g.add_edges([(int(u), int(v)) for u, v in edges])
        if weights is not None:
            w = np.asarray(weights, np.float64)
            wn = w / max(w.max(), 1e-9)
            if w_power != 1.0:
                wn = wn ** w_power
            g.es["weight"] = wn.tolist()
        # NOTE: igraph FR `seed` param means initial coordinate matrix, not RNG seed;
        # determinism comes from random.seed() above (igraph uses Python random).
        lay = g.layout_fruchterman_reingold(
            weights=("weight" if weights is not None else None), dim=dim)
    else:
        lay = g.layout_fruchterman_reingold(dim=dim)
    coords = np.asarray(lay.coords, dtype=np.float64)
    # isolated nodes (degree 0) may sit at origin -> deterministic slots
    # (3D: fibonacci ball; the planar spiral is kept only for the dim=2 arm)
    deg = np.asarray(g.degree(), dtype=np.int64)
    iso = np.flatnonzero(deg == 0)
    if len(iso):
        if dim == 3:
            coords[iso] = _fib_ball_slots(len(iso), 0.9, seed)
        else:
            k = np.arange(len(iso))
            ang = k * 2.399963229728653  # golden angle
            rad = 0.9 * np.sqrt((k + 0.5) / max(1, len(iso)))
            coords[iso, 0] = rad * np.cos(ang)
            coords[iso, 1] = rad * np.sin(ang)
    r = np.linalg.norm(coords, axis=1)
    m = r.max()
    if m > 0:
        coords /= m
    return coords


def disk_overlap_fraction(centers: np.ndarray, radii: np.ndarray) -> float:
    """円-円の交差面積の総和 / 全円盤面積(2D 射影の重なり率)。"""
    k = len(centers)
    if k < 2:
        return 0.0
    c2 = centers[:, :2]
    i, j = np.triu_indices(k, 1)
    d = np.linalg.norm(c2[j] - c2[i], axis=1)
    r0, r1 = radii[i], radii[j]
    inter = np.zeros(len(d))
    m = d < (r0 + r1)
    dd, a, b = d[m], r0[m], r1[m]
    dd = np.maximum(dd, 1e-9)
    part = np.clip((dd ** 2 + a ** 2 - b ** 2) / (2 * dd * a), -1, 1)
    part2 = np.clip((dd ** 2 + b ** 2 - a ** 2) / (2 * dd * b), -1, 1)
    inter[m] = (a ** 2 * np.arccos(part) + b ** 2 * np.arccos(part2)
                - 0.5 * np.sqrt(np.maximum(0, (-dd + a + b) * (dd + a - b) * (dd - a + b) * (dd + a + b))))
    total = np.pi * np.sum(radii ** 2)
    return float(inter.sum() / max(total, 1e-9))


def sphere_overlap_fraction(centers: np.ndarray, radii: np.ndarray) -> float:
    """球-球の交差体積の総和 / 全体積(3D の重なり率)。"""
    k = len(centers)
    if k < 2:
        return 0.0
    i, j = np.triu_indices(k, 1)
    d = np.maximum(np.linalg.norm(centers[j] - centers[i], axis=1), 1e-9)
    r0, r1 = radii[i], radii[j]
    m = d < (r0 + r1)
    dd, a, b = d[m], r0[m], r1[m]
    vol = (np.pi * (a + b - dd) ** 2 *
           (dd ** 2 + 2 * dd * (a + b) - 3 * (a - b) ** 2)) / (12 * dd)
    total = (4.0 / 3.0) * np.pi * np.sum(radii ** 3)
    return float(vol.sum() / max(total, 1e-9))


def projection_overlap_stats(centers: np.ndarray, radii: np.ndarray,
                             n_views: int = 24, seed: int = 7) -> dict:
    """正準軸 + ランダム視点方向へ射影した円の重なり率。
    「各視点から見てどれだけ地図らしいか」を定量する。"""
    rng = np.random.default_rng(seed)
    dirs3 = [np.array([0, 0, 1.0]), np.array([1, 0, 0.0]), np.array([0, 1, 0.0])]
    extra = rng.normal(size=(n_views, 3))
    extra /= np.linalg.norm(extra, axis=1, keepdims=True)
    dirs3 += [e for e in extra]
    vals = {}
    allv = []
    for w in dirs3:
        if abs(w[2]) < 0.99:
            u = np.cross(w, [0, 0, 1.0])
        else:
            u = np.cross(w, [1, 0, 0.0])
        u /= np.linalg.norm(u)
        v = np.cross(w, u)
        P = np.stack([centers @ u, centers @ v], axis=1)
        ov = disk_overlap_fraction(P, radii)
        allv.append(ov)
    vals["top_z"] = round(allv[0], 5)
    vals["side_x"] = round(allv[1], 5)
    vals["side_y"] = round(allv[2], 5)
    vals["random_mean"] = round(float(np.mean(allv[3:])), 5)
    vals["random_worst"] = round(float(np.max(allv[3:])), 5)
    return vals


def relax_disks(centers: np.ndarray, radii: np.ndarray, iters: int = 40) -> np.ndarray:
    """重なる天体を押し離す(ベクトル化 O(k^2)、k は小さい; 次元非依存 =
    2D 円盤にも 3D 球にも働く)。完全一致の天体には決定的な分離方向を与える:
    ゼロベクトルは押せないため、これが無いと同一位置の重なりは永久に解けない
    (unlinked 銀河の境界集中で顕在化した)。"""
    c = centers.astype(np.float64).copy()
    k = len(c)
    if k < 2:
        return c
    idx_i, idx_j = np.triu_indices(k, 1)
    ri, rj = radii[idx_i], radii[idx_j]
    min_d = (ri + rj) * 1.02
    for _ in range(iters):
        d = c[idx_j] - c[idx_i]
        dist = np.linalg.norm(d, axis=1)
        bad = dist < min_d
        if not bad.any():
            break
        zero = bad & (dist < 1e-9)
        if zero.any():
            kk = np.flatnonzero(zero)
            ang = (idx_i[kk] * 7 + idx_j[kk] * 13 + 1) * 2.399963229728653
            d[kk, 0] = np.cos(ang)
            d[kk, 1] = np.sin(ang)
            if d.shape[1] > 2:
                el = ((idx_i[kk] * 3 + idx_j[kk] * 5) % 89 + 0.5) / 89.0 * np.pi
                d[kk, 2] = np.cos(el)
            dist[kk] = np.linalg.norm(d[kk], axis=1)
        push = (min_d[bad] - dist[bad])[:, None] * 0.5
        dirv = d[bad] / np.maximum(dist[bad], 1e-9)[:, None]
        c[idx_i[bad]] -= dirv * push
        c[idx_j[bad]] += dirv * push
    return c


def flatness(P: np.ndarray) -> float:
    """点群の PCA 異方性: 1 - lam3/lam1、値域 [0, 1]。

    ~1.0 = パンケーキ(円盤状)、~2/3 = 等方球。マクロ毎に
    「塊が円盤に見える」問題を客観的に定量するための指標。
    3 点以上が必要; 縮退した点群には 0.0 を返す。
    """
    P = np.asarray(P, np.float64)
    if len(P) < 3:
        return 0.0
    C = np.cov((P - P.mean(axis=0)).T)
    ev = np.linalg.eigvalsh(C)          # ascending
    lam1, lam3 = float(ev[-1]), float(ev[0])
    if lam1 <= 1e-12:
        return 0.0
    return float(1.0 - max(lam3, 0.0) / lam1)


def macro_adjacency(centers: np.ndarray, edges, weights, k: int = 5) -> dict:
    """マクロ配置の意味的近接の保持度。

    recall  = マクロ毎の |グラフ top-K 隣接 ∩ 空間 top-K| / K の平均
    spearman = リンクを持つペアについての重みと空間距離の順位相関
              (負 = 強いリンクほど近くに配置されている)
    {"recall": float|None, "spearman": float|None} を返す; マクログラフが
    意味を持つには小さすぎる場合(マクロ < 3 またはエッジ無し)は None。
    """
    n = len(centers)
    edges = [(int(u), int(v)) for u, v in edges]
    if n < 3 or not edges:
        return {"recall": None, "spearman": None}
    w = np.asarray(weights, np.float64)
    D = np.linalg.norm(centers[:, None, :] - centers[None, :, :], axis=2)
    np.fill_diagonal(D, np.inf)
    recalls, ws, ds = [], [], []
    adj = {i: [] for i in range(n)}
    for (u, v), wv in zip(edges, w):
        adj[u].append((v, wv))
        adj[v].append((u, wv))
        ws.append(wv)
        ds.append(D[u, v])
    for i in range(n):
        if not adj[i]:
            continue
        kk = min(k, len(adj[i]), n - 1)
        gtop = {int(j) for j, _ in sorted(adj[i], key=lambda t: -t[1])[:kk]}
        stop = set(np.argsort(D[i])[:kk].tolist())
        recalls.append(len(gtop & stop) / kk)
    spear = None
    if len(ws) >= 3:
        rw = np.argsort(np.argsort(np.asarray(ws), kind="stable"), kind="stable")
        rd = np.argsort(np.argsort(np.asarray(ds), kind="stable"), kind="stable")
        sw, sd = float(rw.std()), float(rd.std())
        if sw > 0 and sd > 0:
            spear = float(np.corrcoef(rw, rd)[0, 1])
    return {"recall": float(np.mean(recalls)) if recalls else None,
            "spearman": spear}


def medium_local(reg_local: dict, pairs, macro_centers: np.ndarray,
                 macro_of, midx: dict, m_own, Rm: float, r_med: float,
                 fallback_seed: int, fallback=None) -> np.ndarray:
    """1 つの medium 銀河のローカル位置 = リンク先の重み付き重心。

    reg_local: dict gid -> 配置済み通常銀河(このマクロ)のローカル 3D ベクトル。
    pairs: この medium 銀河の (other_gid, weight) の反復可能オブジェクト。
    マクロ内の隣接は配置済みローカル位置を寄与する; マクロ外の隣接は
    「相手のマクロ中心」の方向を軸に、マクロ球内の円錐散布点(CONE-SPREAD)を
    寄与する(他クラスタを橋渡しする medium は、その相手に向き合う境界近くに
    来る)。同一の優勢隣接を共有する銀河を境界の単一点へ集めると、包含クランプが
    それを薄いキャップへ潰す(パンケーキ化の主因)ため、寄与は必ず円錐内へ
    散布する。円錐サンプルは seed_key = fallback_seed + 7 * neighbour_macro_id
    により (銀河, 隣接マクロ) 毎に決定的 — RNG 状態を使わないので、結果は
    ジョブ割当/resume 順に依存しない。配置済み位置を持たない隣接(他の medium 等)
    は無視する; medium 同士の分離は後段の押し出しパスが解く。使えるペアが
    全く無い場合は `fallback`(例: unlinked 通常銀河のフィボナッチ球スロット)を
    返し、それも無ければ決定的な黄金角スロットを返す。
    """
    num = np.zeros(3)
    wsum = 0.0
    own_i = midx[int(m_own)]
    own_c = macro_centers[own_i]
    rim = max(Rm - r_med, 0.05 * Rm)
    for other, wv in pairs:
        wv = float(wv)
        if wv <= 0:
            continue
        other = int(other)
        mo = int(macro_of[other])
        if mo == int(m_own):
            p = reg_local.get(other)
            if p is None:
                continue
            num += wv * np.asarray(p, np.float64)
            wsum += wv
        elif mo in midx:
            d = macro_centers[midx[mo]] - own_c
            dn = float(np.linalg.norm(d))
            if dn < 1e-9:
                continue
            v, depthf = _cone_spread(d / dn,
                                     int(fallback_seed) + 7 * int(mo))
            num += wv * (v * (rim * depthf))
            wsum += wv
    if wsum > 0:
        return num / wsum
    if fallback is not None:
        return np.asarray(fallback, np.float64)
    phi = (int(fallback_seed) % 1000) * 2.399963229728653
    return np.array([rim * 0.6 * np.cos(phi), rim * 0.6 * np.sin(phi),
                     rim * 0.6 * 0.35 * np.sin(2 * phi)])


HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>wikiuniverse layout preview __MODE__</title>
<style>body{margin:0;background:#000;overflow:hidden}
#info{position:absolute;top:8px;left:10px;color:#9aa;font:12px/1.5 monospace;white-space:pre}
#ui{position:absolute;bottom:12px;left:12px;color:#9aa;font:12px monospace}
#ui input{vertical-align:middle;width:220px}</style>
<script type="importmap">{"imports":{"three":"https://unpkg.com/three@0.160.0/build/three.module.js","three/addons/":"https://unpkg.com/three@0.160.0/examples/jsm/"}}</script>
</head><body><div id="info">__INFO__</div>
<div id="ui">z-compress (3D&#8596;hybrid&#8596;map): <input id="zsq" type="range" min="0.05" max="1" step="0.05" value="1"> <span id="zval">1.00</span></div>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
const D = __DATA__;
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x000000, 0.00018);
const camera = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 1, 200000);
camera.position.set(0, -3200, 1800);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
// galaxies: instanced spheres
const geo = new THREE.SphereGeometry(1, 10, 10);
const mat = new THREE.MeshBasicMaterial();
const mesh = new THREE.InstancedMesh(geo, mat, D.g.length);
const m4 = new THREE.Matrix4(); const col = new THREE.Color();
D.g.forEach((g, i) => {
  m4.makeScale(g[3], g[3], g[3]); m4.setPosition(g[0], g[1], g[2]);
  mesh.setMatrixAt(i, m4); mesh.setColorAt(i, col.set(g[4]));
});
scene.add(mesh);
// macro wireframe spheres
const macroObjs = [];
D.m.forEach(mk => {
  const g2 = new THREE.SphereGeometry(mk[3], 20, 12);
  const w = new THREE.LineSegments(new THREE.WireframeGeometry(g2),
        new THREE.LineBasicMaterial({color:0x444444, transparent:true, opacity:0.10}));
  w.position.set(mk[0], mk[1], mk[2]);
  scene.add(w); macroObjs.push(w);
});
// view-time z compression: 3D canonical data -> hybrid/map views without re-layout
function applyZ(k){
  for (let i = 0; i < D.g.length; i++) {
    const g = D.g[i];
    m4.makeScale(g[3], g[3], g[3]);
    m4.setPosition(g[0], g[1], g[2] * k);
    mesh.setMatrixAt(i, m4);
  }
  mesh.instanceMatrix.needsUpdate = true;
  for (let i = 0; i < D.m.length; i++) {
    macroObjs[i].position.z = D.m[i][2] * k;
    macroObjs[i].scale.set(1, 1, k);
  }
}
const zsl = document.getElementById('zsq');
zsl.addEventListener('input', () => {
  const k = parseFloat(zsl.value);
  document.getElementById('zval').textContent = k.toFixed(2);
  applyZ(k);
});
addEventListener('resize', () => {
  camera.aspect = innerWidth/innerHeight; camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
</script></body></html>
"""


def write_html_preview(path, g_centers, g_radius, gal_colors, dust_mask,
                       m_centers, R_m, eff_m, labels, mode_tag, info):
    """gal_colors: hex color per galaxy index (already curated)."""
    garr = []
    for i in np.flatnonzero(~dust_mask):
        garr.append([round(float(g_centers[i, 0]), 2), round(float(g_centers[i, 1]), 2),
                     round(float(g_centers[i, 2]), 2), round(float(max(g_radius[i], 1.0)), 2),
                     gal_colors[i]])
    dust_i = np.flatnonzero(dust_mask)
    if len(dust_i):
        # dust as tiny dim spheres (few thousand max)
        for i in dust_i:
            garr.append([round(float(g_centers[i, 0]), 2), round(float(g_centers[i, 1]), 2),
                         round(float(g_centers[i, 2]), 2), 1.5, "#333333"])
    marr = []
    for k, m in enumerate(eff_m):
        marr.append([round(float(m_centers[k, 0]), 2), round(float(m_centers[k, 1]), 2),
                     round(float(m_centers[k, 2]), 2), round(float(R_m[k]), 2),
                     labels.get(int(m), "")])
    data = json.dumps({"g": garr, "m": marr}, separators=(",", ":"))
    html = (HTML_TEMPLATE.replace("__DATA__", data).replace("__MODE__", mode_tag)
            .replace("__INFO__", info))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def main(a):
    # a=None のときだけ CLI 引数を解析する(ステージからは Namespace を
    # 注入して呼ぶ = 引数解析と処理本体の分離。CLI 挙動は不変)。
    dirs = Dirs(a.base)
    final = str(dirs.final)
    out_dir = str(dirs.layout_run(a.run))
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()

    import pyarrow as pa
    import pyarrow.parquet as pq

    gal = pq.read_table(os.path.join(final, "galaxies.parquet")).to_pydict()
    mac = pq.read_table(os.path.join(final, "macros.parquet")).to_pydict()
    G = len(gal["galaxy_id"])
    Mac = len(mac["macro_id"])
    n_art_g = np.asarray(gal["n_articles"], np.int64)
    n_art_m = np.asarray(mac["n_articles"], np.int64)
    macro_of = np.asarray(gal["macro_id"], np.int64)

    # --- defensive reads: tolerate catalogs built by the pre-v2 builder ---
    skew = []
    if "is_dust" in gal:
        dust_g = np.asarray(gal["is_dust"], bool)
    else:
        dust_g = n_art_g <= 1
        skew.append("galaxies.is_dust")
    if "is_dust" in mac:
        dust_m = np.asarray(mac["is_dust"], bool)
    else:
        dust_m = n_art_m <= 1
        skew.append("macros.is_dust")
    if "display_class" in gal:
        cls = gal["display_class"]
    else:
        cls = ["dust" if dust_g[i] else "galaxy" for i in range(G)]
        skew.append("galaxies.display_class")
    if skew:
        print(f"[layout][warn] catalog is pre-v2 (missing: {', '.join(skew)}); "
              f"derived from n_articles. Re-run: python -m wu run catalog "
              f"(v2) for curated names / display_class (medium galaxies won't be "
              f"flagged in this run).")

    # ---------- 1. macro layout ----------
    eff_m = np.flatnonzero(~dust_m & (n_art_m > 0))
    midx = {int(m): i for i, m in enumerate(eff_m)}
    mp = pq.read_table(os.path.join(final, "macro_pairs.parquet")).to_pydict()
    me_a = np.asarray([midx.get(int(x), -1) for x in mp["macro_a"]])
    me_b = np.asarray([midx.get(int(x), -1) for x in mp["macro_b"]])
    ok = (me_a >= 0) & (me_b >= 0)
    m_edges = list(zip(me_a[ok].tolist(), me_b[ok].tolist()))
    del me_a, me_b
    m_w = np.asarray(mp["w"], np.float64)[ok]
    m_coords = fr_layout(len(eff_m), m_edges, m_w, a.macro_dim, a.seed,
                         w_power=a.macro_w_power)
    if a.macro_dim == 3 and a.macro_z_squash != 1.0:
        m_coords[:, 2] *= a.macro_z_squash
    R_m = R_TOTAL * np.sqrt(n_art_m[eff_m] / max(1, n_art_m.sum()))
    m_centers = m_coords * (R_TOTAL * 1.6)
    native_overlap = (sphere_overlap_fraction if a.macro_dim == 3
                      else disk_overlap_fraction)
    m_centers = relax_disks(m_centers, R_m, iters=250)
    for _round in range(10):  # relax + expand until macro bodies barely overlap
        ov = native_overlap(m_centers, R_m)
        if ov <= 0.02:
            break
        m_centers = relax_disks(m_centers, R_m, iters=200)
        m_centers = m_centers * 1.06
    m_centers = relax_disks(m_centers, R_m, iters=300)
    macro_overlap = native_overlap(m_centers, R_m)
    if m_centers.shape[1] == 2:
        m_centers = np.concatenate([m_centers, np.zeros((len(m_centers), 1))], axis=1)
    proj = projection_overlap_stats(m_centers, R_m, n_views=a.views, seed=a.seed)
    print(f"[layout] macros: {len(eff_m)} placed (macro_dim={a.macro_dim}), "
          f"native overlap={macro_overlap:.4f}, proj top/side/mean="
          f"{proj['top_z']}/{proj['side_x']}/{proj['random_mean']} ({time.time()-t0:.1f}s)")

    # ---------- 2. galaxy pairs ----------
    if a.pairs == "npz":
        pz = np.load(dirs.community_full / f"pairs_{a.galaxy_tag}.npz")
        gp_a, gp_b, gp_w = pz["a"].astype(np.int64), pz["b"].astype(np.int64), pz["w"].astype(np.float64)
    else:
        gpq = pq.read_table(os.path.join(final, "galaxy_pairs_topK.parquet")).to_pydict()
        gp_a = np.asarray(gpq["a"], np.int64)
        gp_b = np.asarray(gpq["b"], np.int64)
        gp_w = np.asarray(gpq["w"], np.float64)
    mac_a = macro_of[gp_a]
    mac_b = macro_of[gp_b]
    same_macro = mac_a == mac_b
    del mac_b

    # ---------- 3. per-macro galaxy layout ----------
    g_centers = np.zeros((G, 3), np.float64)
    g_radius = np.zeros(G, np.float64)
    placed = np.zeros(G, bool)
    is_med = np.asarray([str(c) == "medium" for c in cls])
    # pair index for bary placement (mediums + unlinked galaxies). topK parquet
    # may list a pair from both sides; duplicates only double symmetric
    # centroid weights = harmless.
    ends = np.concatenate([gp_a, gp_b]).astype(np.int64)
    others = np.concatenate([gp_b, gp_a]).astype(np.int64)
    ws_all = np.concatenate([gp_w, gp_w])
    po = np.argsort(ends, kind="stable")
    ends_s, others_s, ws_s = ends[po], others[po], ws_all[po]

    def pairs_of(g):
        lo = int(np.searchsorted(ends_s, g, "left"))
        hi = int(np.searchsorted(ends_s, g, "right"))
        return list(zip(others_s[lo:hi].tolist(), ws_s[lo:hi].tolist()))

    macro_clouds = []          # per-macro (local centers, radii) for metrics
    n_bary = 0
    for m_i, m in enumerate(eff_m):
        members = np.flatnonzero((macro_of == m) & (~dust_g))
        if len(members) == 0:
            continue
        Rm = R_m[m_i]
        n_m = n_art_m[m]
        frac = n_art_g[members] / max(1, n_m)
        # 半径則の指数は実験腕(--r-expo)。既定 0.5 では従来の np.sqrt を
        # そのまま使いビット同一を保証する(frac**0.5 へ書き換えないこと —
        # 冪乗と sqrt はビット一致が保証されない)。
        r_form = a.pack * Rm * (np.sqrt(frac) if a.r_expo == 0.5
                                else frac ** a.r_expo)
        r_floor = a.r_spacing * np.maximum(1, n_art_g[members]) ** (1.0 / 3.0)
        r_g = np.maximum(r_form, r_floor)
        # volume cap: summed galaxy spheres fit inside the macro sphere
        cap = 0.9 * Rm
        tot = np.cbrt(np.sum(r_g ** 3))
        if tot > cap:
            r_g *= cap / tot
        g_radius[members] = r_g
        med_m = members[is_med[members]]
        reg_m = members[~is_med[members]]
        # intra-macro pair edges among regular galaxies
        ridx = {int(g): i for i, g in enumerate(reg_m)}
        e_pairs = []
        for i in np.flatnonzero(same_macro & (mac_a == m)):
            ga, gb = int(gp_a[i]), int(gp_b[i])
            if ga in ridx and gb in ridx:
                e_pairs.append((ga, gb, float(gp_w[i])))
        deg = np.zeros(len(reg_m), np.int64)
        for ga, gb, _ in e_pairs:
            deg[ridx[ga]] += 1
            deg[ridx[gb]] += 1
        # LINKED galaxies: weighted FR; UNLINKED (no intra-macro pair = their
        # top-K links all leave the macro): bary centroid of link targets, or
        # fib-ball slots when no usable pairs -> no more planar pancakes
        conn = reg_m[deg > 0]
        unlinked = reg_m[deg == 0]
        cidx = {int(g): i for i, g in enumerate(conn)}
        e_local = [(cidx[ga], cidx[gb]) for ga, gb, _ in e_pairs]
        w_local = np.asarray([wv for _, _, wv in e_pairs], np.float64)
        if len(conn) == 0:
            coords3 = np.zeros((0, 3))
        elif len(conn) == 1:
            coords3 = np.zeros((1, 3))
        else:
            coords3 = fr_layout(len(conn), e_local, w_local, 3, a.seed + int(m))
        avail = max(Rm - (g_radius[conn].max() if len(conn) else 0.0), Rm * 0.3)
        loc = {int(g): coords3[i] * avail for i, g in enumerate(conn)}
        rim_u = max(Rm - (g_radius[unlinked].max() if len(unlinked) else 0.0),
                    Rm * 0.3)
        fb = _fib_ball_slots(len(unlinked), rim_u * 0.9, a.seed + int(m))
        for k, g in enumerate(unlinked):
            loc[int(g)] = medium_local(loc, pairs_of(int(g)), m_centers,
                                       macro_of, midx, int(m), Rm,
                                       float(g_radius[g]), a.seed + int(g),
                                       fallback=fb[k])
        reg_all = np.concatenate([conn, unlinked]) if len(reg_m) else reg_m
        P = np.stack([loc[int(g)] for g in reg_all]) if len(reg_all) \
            else np.zeros((0, 3))
        if len(P) >= 2:
            P = relax_disks(P, g_radius[reg_all], iters=120)
        # 3D radial clamp, applied UNCONDITIONALLY: a `len(P) >= 2` gate
        # would skip single-regular-galaxy macros, where a
        # pairless isolated galaxy on its fibonacci slot could stick out of
        # the macro sphere (measured (d3+r)/Rm up to 1.114). With the volume
        # cap (r <= 0.9 Rm) and lim = max(Rm-r, 0.05Rm), d3 + r <= Rm now
        # holds by construction => galaxy_spill_count == 0 for every macro.
        # Elementwise ops below are safe on the empty (len-0) P as well.
        lim = np.maximum(Rm - g_radius[reg_all], 0.05 * Rm)
        d3 = np.linalg.norm(P, axis=1)
        sc = np.where(d3 > lim, lim / np.maximum(d3, 1e-9), 1.0)
        P = P * sc[:, None]
        if len(reg_all):
            g_centers[reg_all] = m_centers[m_i] + P
            placed[reg_all] = True
        # ---- medium galaxies: same bary rule against the FINAL regular layout
        if len(med_m):
            reg_local = {int(g): P[i] for i, g in enumerate(reg_all)} \
                if len(reg_all) else {}
            M = np.zeros((len(med_m), 3))
            for k, g in enumerate(med_m):
                M[k] = medium_local(reg_local, pairs_of(int(g)), m_centers,
                                    macro_of, midx, int(m), Rm,
                                    float(g_radius[g]), a.seed + int(g))
            rm = g_radius[med_m]
            # push pass: mediums yield to placed regulars, then separate
            # among themselves; re-clamp inside the macro sphere each round
            reg_P = P if len(reg_all) else np.zeros((0, 3))
            lim_m = np.maximum(Rm - rm, 0.05 * Rm)
            for _ in range(30):
                moved = False
                if len(reg_P):
                    dv = M[:, None, :] - reg_P[None, :, :]
                    dist = np.linalg.norm(dv, axis=2)
                    need = (rm[:, None] + g_radius[reg_all][None, :]) * 1.02
                    bad = dist < need
                    zero = bad & (dist < 1e-9)
                    if zero.any():
                        # exact coincidence: deterministic direction
                        # (same rule as relax_disks; zero vectors can't push)
                        ki, kj = np.nonzero(zero)
                        ang = (ki * 7 + kj * 13 + 1) * 2.399963229728653
                        dv[ki, kj, 0] = np.cos(ang)
                        dv[ki, kj, 1] = np.sin(ang)
                        el = ((ki * 3 + kj * 5) % 89 + 0.5) / 89.0 * np.pi
                        dv[ki, kj, 2] = np.cos(el)
                        dist[ki, kj] = np.linalg.norm(dv[ki, kj], axis=1)
                    if bad.any():
                        diru = dv / np.maximum(dist, 1e-9)[:, :, None]
                        step = np.where(bad, need - dist, 0.0)[:, :, None] * diru
                        M = M + step.sum(axis=1)
                        moved = True
                if len(M) >= 2:
                    before = M.copy()
                    M = relax_disks(M, rm, iters=4)
                    moved |= not np.array_equal(before, M)
                dm = np.linalg.norm(M, axis=1)
                sc = np.where(dm > lim_m, lim_m / np.maximum(dm, 1e-9), 1.0)
                M = M * sc[:, None]
                if not moved:
                    break
            g_centers[med_m] = m_centers[m_i] + M
            placed[med_m] = True
            n_bary += len(med_m)
            all_local = np.concatenate([P, M]) if len(P) else M
            all_r = np.concatenate([g_radius[reg_all], rm]) if len(P) else rm
        else:
            all_local, all_r = P, g_radius[reg_all]
        if len(all_local):
            macro_clouds.append((all_local, all_r))
    print(f"[layout] galaxies placed: {int(placed.sum())} "
          f"(medium bary: {n_bary}) ({time.time()-t0:.1f}s)")

    # ---------- 4. dust shell ----------
    dust_idx = np.flatnonzero(dust_g | ~placed)
    if len(dust_idx):
        shell = np.linalg.norm(m_centers, axis=1).max() * 1.5 + R_TOTAL * 0.2
        k = np.arange(len(dust_idx))
        yv = 1 - 2 * (k + 0.5) / len(k)
        rr = np.sqrt(np.maximum(0, 1 - yv ** 2))
        phi = k * 2.399963229728653
        g_centers[dust_idx, 0] = shell * rr * np.cos(phi)
        g_centers[dust_idx, 1] = shell * rr * np.sin(phi)
        g_centers[dust_idx, 2] = shell * yv
        g_radius[dust_idx] = a.pack * R_TOTAL * np.sqrt(1 / max(1, n_art_m.sum()))

    # ---------- 4b. quality metrics ----------
    in_macro = np.flatnonzero(placed & ~dust_g)
    mrow = np.asarray([midx[int(macro_of[i])] for i in in_macro], np.int64) \
        if len(in_macro) else np.zeros(0, np.int64)
    if len(in_macro):
        # spill = 3D containment: galaxy sphere must fit inside its macro sphere
        dist_m = np.linalg.norm(g_centers[in_macro] - m_centers[mrow], axis=1)
        spill = dist_m + g_radius[in_macro] > R_m[mrow] * 1.02
        # WHO spilled, visible in every run without waiting for an audit
        # (top-20 by excess; galaxy_id == row index in this script's arrays)
        exc_m = dist_m + g_radius[in_macro] - R_m[mrow]
        sp_idx = np.flatnonzero(spill)
        sp_idx = sp_idx[np.argsort(-exc_m[sp_idx], kind="stable")]
        spill_ids = [int(in_macro[k]) for k in sp_idx[:20]]
    else:
        spill = np.zeros(0, bool)
        spill_ids = []
    spill_frac = float(spill.mean()) if len(spill) else 0.0
    # per-macro galaxy-cloud anisotropy (~1 = pancake); domain = macros with
    # FLAT_MIN_MEMBERS+ members (small-n eigenvalue bias; see the constant)
    flats = [flatness(P) for P, _ in macro_clouds if len(P) >= FLAT_MIN_MEMBERS]
    flat_mean = round(float(np.mean(flats)), 5) if flats else None
    flat_p90 = round(float(np.percentile(flats, 90)), 5) if flats else None
    # sphere overlap INSIDE macros (volume-weighted; relaxation quality)
    ov_num = ov_den = 0.0
    for P, R in macro_clouds:
        if len(P) >= 2 and float(np.sum(R)) > 0:
            V = (4.0 / 3.0) * np.pi * float(np.sum(R ** 3))
            ov_num += sphere_overlap_fraction(P, R) * V
            ov_den += V
    galaxy_overlap = round(ov_num / ov_den, 5) if ov_den > 0 else 0.0
    # semantic adjacency preservation of the macro layout
    adj = macro_adjacency(m_centers, m_edges, m_w, k=5)
    adj_recall = round(adj["recall"], 5) if adj["recall"] is not None else None
    adj_spear = round(adj["spearman"], 5) if adj["spearman"] is not None else None

    # ---------- 5. outputs ----------
    zcol = g_centers[:, 2]
    pq.write_table(pa.table({
        "galaxy_id": pa.array(np.arange(G, dtype=np.int32)),
        "macro_id": pa.array(macro_of.astype(np.int32)),
        "x": pa.array(g_centers[:, 0]), "y": pa.array(g_centers[:, 1]),
        "z": pa.array(zcol), "radius": pa.array(g_radius),
        "display_class": pa.array([str(c) for c in cls], type=pa.string()),
        "n_articles": pa.array(n_art_g.astype(np.int32)),
    }), os.path.join(out_dir, "galaxy_positions.parquet"), compression="zstd")

    mz = m_centers[:, 2]
    full_mx = np.zeros(Mac); full_my = np.zeros(Mac); full_mz = np.zeros(Mac); full_R = np.zeros(Mac)
    for i, m in enumerate(eff_m):
        full_mx[m], full_my[m], full_mz[m], full_R[m] = m_centers[i, 0], m_centers[i, 1], mz[i], R_m[i]
    pq.write_table(pa.table({
        "macro_id": pa.array(np.arange(Mac, dtype=np.int32)),
        "x": pa.array(full_mx), "y": pa.array(full_my), "z": pa.array(full_mz),
        "radius": pa.array(full_R),
        "n_articles": pa.array(n_art_m.astype(np.int32)),
        "n_galaxies": pa.array(np.asarray(mac["n_galaxies"], np.int32)),
        "is_dust": pa.array(dust_m),
    }), os.path.join(out_dir, "macro_positions.parquet"), compression="zstd")

    meta = {"generated_at": _now(), "run": a.run, "pack": a.pack,
            "r_spacing": a.r_spacing, "r_expo": a.r_expo,
            "seed": a.seed,
            "macro_w_power": a.macro_w_power,
            "pairs_source": a.pairs, "galaxy_tag": a.galaxy_tag,
            "n_galaxies": G, "n_macros_effective": int(len(eff_m)),
            "n_dust_shell": int(len(dust_idx)), "R_TOTAL": R_TOTAL,
            "mode": {"macro_dim": a.macro_dim, "macro_z_squash": a.macro_z_squash,
                     "canonical": a.macro_dim == 3 and a.macro_z_squash == 1.0,
                     "note": ("canonical policy: full 3D, macro-dim 3 "
                              "unsquashed, sphere-relaxed galaxies, NO baked lens "
                              "(map/hybrid views are VIEW-TIME z-compression in "
                              "the viewer); unlinked galaxies are bary-placed "
                              "(rim contributions cone-spread); containment clamp "
                              "is unconditional => spill == 0 by construction")
                     if (a.macro_dim == 3 and a.macro_z_squash == 1.0) else
                     "experimental arm; canonical = --macro-dim 3 "
                     "--macro-z-squash 1.0"},
            "quality": {"macro_native_overlap_frac": round(macro_overlap, 5),
                        "macro_proj_overlap": proj,
                        "galaxy_spill_frac": round(spill_frac, 5),
                        "galaxy_spill_count": int(spill.sum()),
                        "galaxy_spill_ids": spill_ids,
                        "galaxy_overlap_frac": galaxy_overlap,
                        "galaxy_flat_mean": flat_mean,
                        "galaxy_flat_p90": flat_p90,
                        "galaxy_flat_n_macros": len(flats),
                        "galaxy_flat_domain_min": FLAT_MIN_MEMBERS,
                        "macro_adj_recall_top5": adj_recall,
                        "macro_adj_spearman": adj_spear},
            "secs": round(time.time() - t0, 1)}
    print(f"[layout] quality: macro_overlap={macro_overlap:.4f} "
          f"galaxy_spill={spill_frac:.4f} ({int(spill.sum())}) "
          f"gal_overlap={galaxy_overlap:.4f} "
          f"flat_mean={flat_mean} adj_recall5={adj_recall} adj_spear={adj_spear}")
    write_json(os.path.join(out_dir, "layout_meta.json"), meta)

    # ---------- 6. preview: multi-view PNG + interactive HTML ----------
    mode_tag = (f"macro{a.macro_dim}d" +
                (f"_mz{a.macro_z_squash:g}" if a.macro_dim == 3 and a.macro_z_squash != 1.0 else "") +
                (f"_wp{a.macro_w_power:g}" if a.macro_w_power != 1.0 else ""))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        cmap = plt.get_cmap("tab20")
        colors_hex = [matplotlib.colors.to_hex(cmap(int(m) % 20)) for m in range(Mac)]
        idx_in = np.flatnonzero((~dust_g) & placed)
        colors_all = ["#888888" if cls[i] == "medium" else colors_hex[int(macro_of[i])]
                      for i in range(G)]
        colors = [colors_all[i] for i in idx_in]
        dust_i = np.flatnonzero(dust_g | ~placed)
        fig, axes = plt.subplots(2, 2, figsize=(16, 16), facecolor="black")
        panels = [("xy (map view)", 0, 1, None), ("xz", 0, 2, None),
                  ("yz", 1, 2, None), ("xy colored by z", 0, 1, "z")]
        top_m = eff_m[np.argsort(-n_art_m[eff_m])[:8]]
        for ax, (name, i1_, i2_, depth) in zip(axes.flat, panels):
            ax.set_facecolor("black")
            if depth is None:
                ax.scatter(g_centers[idx_in, i1_], g_centers[idx_in, i2_],
                           s=np.clip(g_radius[idx_in] * 0.8, 0.5, 300),
                           c=colors, alpha=0.85, linewidths=0)
            else:
                ax.scatter(g_centers[idx_in, i1_], g_centers[idx_in, i2_],
                           s=np.clip(g_radius[idx_in] * 0.8, 0.5, 300),
                           c=g_centers[idx_in, 2], cmap="coolwarm", alpha=0.9,
                           linewidths=0)
            if len(dust_i):
                ax.scatter(g_centers[dust_i, i1_], g_centers[dust_i, i2_],
                           s=0.4, c="#333333", alpha=0.5, linewidths=0)
            for k, m in enumerate(eff_m):
                ax.add_patch(plt.Circle((m_centers[k, i1_], m_centers[k, i2_]), R_m[k],
                                        fill=False, edgecolor="#444444", lw=0.6))
            if name.startswith("xy ("):
                for m in top_m:
                    k = list(eff_m).index(int(m))
                    lbl = str(mac["rep_titles"][int(m)]).split(",")[0][:14]
                    ax.text(m_centers[k, i1_], m_centers[k, i2_] + R_m[k] * 1.03, lbl,
                            color="#999999", fontsize=7, ha="center")
            ax.set_aspect("equal")
            ax.axis("off")
            ax.set_title(f"{name}  [mode={mode_tag}]", color="#777777", fontsize=9)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "preview.png"), dpi=110, facecolor="black")
        plt.close(fig)
        labels_top = {int(m): str(mac["rep_titles"][int(m)]).split(",")[0][:16]
                      for m in eff_m}
        info = (f"mode={mode_tag} seed={a.seed} G={G} macros={len(eff_m)}\n"
                f"native_overlap={macro_overlap:.4f} spill={spill_frac:.4f}\n"
                f"proj top/side/mean={proj['top_z']}/{proj['side_x']}/{proj['random_mean']}\n"
                f"drag=orbit wheel=zoom")
        hp = write_html_preview(os.path.join(out_dir, f"preview_{mode_tag}.html"),
                                g_centers, g_radius, colors_all,
                                dust_g | ~placed, m_centers, R_m, eff_m,
                                labels_top, mode_tag, info)
        print(f"[layout] preview -> {os.path.join(out_dir, 'preview.png')} + {hp}")
    except Exception as e:  # previews optional
        print(f"[layout] preview skipped: {e}")

    print(f"[layout] done ({meta['secs']}s) -> {out_dir}")

