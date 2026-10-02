# wu/publish.py — Viewer 公開面の生成(data/spatial/ への出版)
#
# 責務:
# - 座標 run(layout/<run>/)と銀河カタログ(final/)から、ビューアが読む公開面を
#   生成する: bootstrap.json(宇宙+銀河ビューの一括 fetch)+ tiles/gal_XXXXXX.bin|.json
#   (ズームした銀河のみ on-demand fetch)+ tiles_meta.json。
# - URL 契約は run 名と無縁に固定(出版元 run は bootstrap の meta.layout_run に記録)。
# - タイル bin 形式(契約): header u32×3(n, n_internal_edges, n_cross_links)+
#   pos f32 n×3 + edges u32 ne×2 + cross u32 nx×3、全配列 rank 順。deg はクライアント算出。
#   サイドカー json = タイトル(rank 順)。bin に文字列は入れない(数値のみ)。
#
# 注意:
# - main(a) は Namespace 注入でステージから呼ばれる(正準実行は python -m wu run publish)。
# - bootstrap の meta.schema_version がビューア側の許容チェックと対になる
#   (形式を変える場合は SCHEMA_VERSION を上げ、viewer/src/data/bootstrap.ts の
#   SUPPORTED_SCHEMA_VERSION と合わせて更新すること)。
# - 銀河団ラベルは final/macro_label_overrides.json(手動キュレーション・任意)が
#   既定導出(rep_titles 先頭 16 字)より優先される。

"""Export viewer tiles + bootstrap JSON (LOD streaming prototype, §8).

Per-galaxy self-contained tile (data/spatial/tiles/gal_XXXXXX.bin, little-endian):
  uint32  n_articles
  uint32  n_internal_edges
  uint32  n_cross_links
  float32 n*3            article positions (canonical global coords)
  uint32  n_internal*2   internal edges (local idx pairs)
  uint32  n_cross*3      cross links (local_u, other_galaxy, local_v)
                         per-article cap: first 4 cross links (prototype)
Sidecar gal_XXXXXX.json : {"t": [title, ...]} (local idx order)

bootstrap.json (few hundred KB): macros / galaxies / macro_pairs / galaxy_pairs
so the universe+galaxy views need a single fetch; article data streams per tile.

Macro labels default to macros.rep_titles[0][:16]; an optional hand-curated
final/macro_label_overrides.json ({"macros": [{"macro_id": N, "label": "..."}]})
takes precedence for non-empty labels. Template generation:
  python -m wu run audit_names --base data  # --set audit_names.dump_macro_labels=true

Usage:
  python -m wu run publish --base data  # 既定で正典 run(configs/publish.json)
      [--run <layout run>] [--cross-cap 8]
(--run default = wu.paths.ACTIVE_LAYOUT_RUN. data/spatial/ is the published,
run-name-independent viewer contract; the source run is recorded in bootstrap.json.)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np


from .dumpio import write_json  # noqa: E402
from .paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402
from .stats import load_edges_mmap, load_titles  # noqa: E402


# 公開面(bootstrap/tiles)の契約バージョン。bin のフィールド構成や bootstrap の
# 位置配列の意味を変える場合は +1 し、viewer 側の SUPPORTED_SCHEMA_VERSION と
# 許容範囲を合わせて更新する(相互運用の要 = バージョン無し契約の反省)。
SCHEMA_VERSION = 1


def main(a=None):
    # a=None のときだけ CLI 引数を解析する(ステージは Namespace 注入で呼ぶ)。
    if a is None:
        ap = argparse.ArgumentParser()
        ap.add_argument("--base", default="data")
        ap.add_argument("--galaxy-tag", default="res1_sub")
        ap.add_argument("--run", default=ACTIVE_LAYOUT_RUN,
                        help="source layout run under data/layout "
                             "(default: wu.paths.ACTIVE_LAYOUT_RUN)")
        ap.add_argument("--cross-cap", type=int, default=8)
        a = ap.parse_args()
    dirs = Dirs(a.base)
    t0 = time.time()
    lay = str(dirs.layout_run(a.run))
    spatial = str(dirs.spatial)
    tiles = str(dirs.tiles)
    os.makedirs(tiles, exist_ok=True)

    import pyarrow.parquet as pq

    memb = np.load(dirs.community_full / f"membership_{a.galaxy_tag}.npy")
    n = len(memb)
    G = int(memb.max()) + 1
    gpos = pq.read_table(os.path.join(lay, "galaxy_positions.parquet")).to_pydict()
    mpos = pq.read_table(os.path.join(lay, "macro_positions.parquet")).to_pydict()
    cat = pq.read_table(dirs.final / "galaxies.parquet").to_pydict()
    apq = pq.read_table(os.path.join(lay, "article_positions.parquet"))
    P = np.stack([apq.column("x").to_numpy(), apq.column("y").to_numpy(),
                  apq.column("z").to_numpy()], axis=1).astype(np.float32)
    del apq
    pid_col, title_col = load_titles(dirs.parsed)
    titles_all = [title_col[i].as_py() for i in
                  np.clip(np.searchsorted(pid_col, np.load(dirs.article_ids)),
                          0, len(pid_col) - 1)]
    E = load_edges_mmap(os.path.join(dirs.graph, "edges_undirected_unique.bin"))
    print(f"[tiles] loaded: {n:,} articles, {G:,} galaxies, {len(E):,} edges "
          f"({time.time()-t0:.0f}s)")

    order = np.argsort(memb, kind="stable")
    rank = np.empty(n, np.int64)
    rank[order] = np.arange(n)
    starts = np.searchsorted(memb[order], np.arange(G), side="left")
    ends = np.searchsorted(memb[order], np.arange(G), side="right")
    starts_arr = starts

    # ---- pass 1: counts (internal edges per galaxy; cross links per galaxy with cap)
    cnt_e = np.zeros(G, np.int64)
    cnt_x = np.zeros(G, np.int64)
    slot = np.full(n, a.cross_cap, np.int8)
    CH = 4_000_000

    def cross_mask(u, v):
        gu, gv = memb[u], memb[v]
        cr = gu != gv
        return cr, gu, gv

    for i in range(0, len(E), CH):
        blk = np.asarray(E[i : i + CH]).astype(np.int64)
        u, v = blk[:, 0], blk[:, 1]
        gu, gv = memb[u], memb[v]
        same = gu == gv
        cnt_e += np.bincount(gu[same], minlength=G)
        cr = ~same
        uu, vv, guu, gvv = u[cr], v[cr], gu[cr], gv[cr]
        ok_u = slot[uu] > 0
        ok_v = slot[vv] > 0
        # entry in u's tile requires slot[u]; entry in v's tile requires slot[v]
        su, sv = slot[uu].copy(), slot[vv].copy()
        # emulate sequential acceptance: sort by u to apply caps deterministically
        for arr_u, arr_g in ((uu, gvv),):
            pass
        # vectorized approximate cap: accept if slot>0 then decrement (order-free)
        au = ok_u
        av = ok_v
        np.add.at(slot, uu[au], -1)
        np.add.at(slot, vv[av], -1)
        cnt_x += np.bincount(guu[au], minlength=G)
        cnt_x += np.bincount(gvv[av], minlength=G)
    # ---- pass 2: fill (same acceptance logic, same order)
    slot = np.full(n, a.cross_cap, np.int8)
    e_off = np.zeros(G + 1, np.int64); e_off[1:] = np.cumsum(cnt_e)
    x_off = np.zeros(G + 1, np.int64); x_off[1:] = np.cumsum(cnt_x)
    e_buf = np.empty((int(cnt_e.sum()), 2), np.uint32)
    x_buf = np.empty((int(cnt_x.sum()), 3), np.uint32)
    e_fill, x_fill = e_off[:-1].copy(), x_off[:-1].copy()
    for i in range(0, len(E), CH):
        blk = np.asarray(E[i : i + CH]).astype(np.int64)
        u, v = blk[:, 0], blk[:, 1]
        gu, gv = memb[u], memb[v]
        same = gu == gv
        if same.any():
            c = gu[same]
            oc = np.argsort(c, kind="stable")
            cs = c[oc]
            uniq, first = np.unique(cs, return_index=True)
            ui = np.searchsorted(uniq, cs)
            rnk = np.arange(len(cs)) - first[ui]
            pos = e_fill[uniq][ui] + rnk
            us, vs = u[same][oc], v[same][oc]
            e_buf[pos, 0] = local_idx(rank, us, gu[same][oc], starts_arr)
            e_buf[pos, 1] = local_idx(rank, vs, gu[same][oc], starts_arr)
            e_fill[uniq] += np.diff(np.append(first, len(cs)))
        cr = ~same
        if cr.any():
            uu, vv, guu, gvv = u[cr], v[cr], gu[cr], gv[cr]
            au = slot[uu] > 0
            av = slot[vv] > 0
            np.add.at(slot, uu[au], -1)
            np.add.at(slot, vv[av], -1)
            # rank-based scatter (duplicate-safe; mirrors pass1 bincount exactly)
            for src_g, dst_g, src_p, dst_p in (
                    (guu[au], gvv[au], uu[au], vv[au]),
                    (gvv[av], guu[av], vv[av], uu[av])):
                if len(src_g) == 0:
                    continue
                ocx = np.argsort(src_g, kind="stable")
                gs = src_g[ocx]
                # cnt_ux (NOT cnt_x): rebinding the outer per-galaxy count
                # array here used to leak out of the loop and tiles_meta then
                # reported the LAST chunk's side count as the total (838 vs
                # 18.6M on real data; bins themselves were always correct)
                uniq_x, first_x, cnt_ux = np.unique(gs, return_index=True,
                                                    return_counts=True)
                ui_x = np.searchsorted(uniq_x, gs)
                rank_x = np.arange(len(gs)) - first_x[ui_x]
                posx = x_fill[uniq_x][ui_x] + rank_x
                x_buf[posx, 0] = local_idx(rank, src_p[ocx], gs, starts_arr)
                x_buf[posx, 1] = dst_g[ocx].astype(np.uint32)
                x_buf[posx, 2] = local_idx(rank, dst_p[ocx], dst_g[ocx], starts_arr)
                x_fill[uniq_x] += cnt_ux
        if (i // CH) % 5 == 0:
            print(f"  tiles pass2 {i + len(blk):,}/{len(E):,} ({time.time()-t0:.0f}s)", flush=True)
    assert np.array_equal(e_fill, e_off[1:]) and np.array_equal(x_fill, x_off[1:])

    # ---- write tiles (with per-galaxy index validation; 2026-09-27 NaN 事象の防御)
    sizes = np.bincount(memb, minlength=G)
    cls_code = {"galaxy": 0, "medium": 1, "dust": 2}
    for g in range(G):
        members = order[starts[g] : ends[g]]
        ng = len(members)
        if ng == 0:
            continue
        pos_g = P[members]
        eg = e_buf[e_off[g] : e_off[g + 1]]
        xg = x_buf[x_off[g] : x_off[g + 1]]
        if ng:
            bad_e = (eg[:, 0] >= ng) | (eg[:, 1] >= ng)
            bad_x0 = xg[:, 0] >= ng
            bad_x2 = xg[:, 2] >= sizes[np.minimum(xg[:, 1], G - 1)]
            if bad_e.any() or bad_x0.any() or bad_x2.any():
                print(f"[tiles][warn] galaxy {g}: bad indices edges={int(bad_e.sum())} "
                      f"cross_local={int(bad_x0.sum())} cross_remote={int(bad_x2.sum())} "
                      f"(clamped at export; investigate local_idx path)")
                eg = eg[~bad_e]
                keep_x = ~(bad_x0 | bad_x2)
                xg = xg[keep_x]
        hdr = np.array([ng, len(eg), len(xg)], dtype=np.uint32)
        with open(os.path.join(tiles, f"gal_{g:06d}.bin"), "wb") as f:
            f.write(hdr.tobytes())
            f.write(pos_g.astype("<f4").tobytes())
            f.write(eg.astype("<u4").tobytes())
            f.write(xg.astype("<u4").tobytes())
        with open(os.path.join(tiles, f"gal_{g:06d}.json"), "w", encoding="utf-8") as f:
            json.dump({"t": [titles_all[i] for i in members]}, f, ensure_ascii=False)
    print(f"[tiles] wrote {G:,} tiles ({time.time()-t0:.0f}s)")

    # ---- bootstrap
    mac = pq.read_table(dirs.final / "macros.parquet").to_pydict()
    mpq = pq.read_table(dirs.final / "macro_pairs.parquet").to_pydict()
    gpq = pq.read_table(dirs.final / "galaxy_pairs_topK.parquet").to_pydict()
    # Macro label overrides (optional hand curation; template is generated by
    # audit_names.py --dump-macro-labels). Non-empty "label" wins over the
    # default derivation (rep_titles first entry, truncated to 16 chars).
    mac_labels = {}
    ovr_path = os.path.join(str(dirs.final), "macro_label_overrides.json")
    if os.path.exists(ovr_path):
        with open(ovr_path, encoding="utf-8") as f:
            for ent in json.load(f).get("macros", []):
                lbl = str(ent.get("label") or "").strip()
                if lbl:
                    mac_labels[int(ent["macro_id"])] = lbl
        print(f"[boot] macro label overrides: {len(mac_labels)} applied "
              f"({os.path.basename(ovr_path)})")
    boot = {
        "meta": {"schema_version": SCHEMA_VERSION,
                 "galaxy_tag": a.galaxy_tag, "layout_run": a.run,
                 "n_articles": n, "n_galaxies": G,
                 "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "cross_cap": a.cross_cap},
        "macros": [[round(mpos["x"][i], 2), round(mpos["y"][i], 2), round(mpos["z"][i], 2),
                    round(mpos["radius"][i], 2), int(mpos["n_articles"][i]),
                    mac_labels.get(int(mac["macro_id"][i]),
                                   str(mac["rep_titles"][i]).split(",")[0][:16])]
                   for i in range(len(mpos["macro_id"]))],  # ALL ids: pairs index raw ids
        "galaxies": [[round(gpos["x"][i], 2), round(gpos["y"][i], 2), round(gpos["z"][i], 2),
                      round(max(gpos["radius"][i], 0.5), 2), int(gpos["macro_id"][i]),
                      cls_code.get(str(cat["display_class"][i]), 0),
                      int(cat["n_articles"][i]), str(cat["name"][i] or ""),
                      int(cat["e_in"][i]), int(cat["e_out"][i])]
                     for i in range(G)],
        "macro_pairs": [[int(mpq["macro_a"][i]), int(mpq["macro_b"][i]), int(mpq["w"][i])]
                        for i in range(min(400, len(mpq["w"])))],
        "galaxy_pairs": [[int(gpq["a"][i]), int(gpq["b"][i]), int(gpq["w"][i])]
                         for i in range(min(5000, len(gpq["w"])))],
    }
    write_json(os.path.join(spatial, "bootstrap.json"), boot)
    meta = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "n_tiles": int(G), "internal_edges": int(cnt_e.sum()),
            "cross_links": int(cnt_x.sum()),
            "tile_bytes": sum(os.path.getsize(os.path.join(tiles, f))
                              for f in os.listdir(tiles) if f.endswith(".bin")),
            "secs": round(time.time() - t0, 1)}
    write_json(os.path.join(spatial, "tiles_meta.json"), meta)
    print(f"[tiles] bootstrap.json + tiles_meta.json: {meta}")


def local_idx(rank, pids, gids, starts_arr):
    """記事 pid の銀河内ローカル索引。rank は順列 order の逆写像であること
    (order 自体は非単調なので searchsorted は使えない — 2026-09-27 バグの根因)。"""
    return (rank[pids] - starts_arr[gids]).astype(np.uint32)


if __name__ == "__main__":
    main()
