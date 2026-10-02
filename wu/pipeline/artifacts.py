# wu/pipeline/artifacts.py — 成果物レジストリ(ステージ間のファイル契約の単一の真実源)
#
# 責務:
# - パイプラインが読み書きする成果物にキー(例 "graph.edges_ns0")を割り当て、
#   パス解決(Dirs + パラメータ)を一箇所に集約する。
# - ステージの入出力宣言はこのキーで行い、ランナーが存在チェック・fail-fast
#   案内・実行台帳(manifest)に利用する。
#
# 注意:
# - ファイル名を f-string で各所に散らすのが旧構造の問題だった(契約が docstring
#   頼みになる)。新規ステージは必ずここに成果物を登録してから入出力を宣言する。
# - kind は data/ 配置の分類(運用ルール D10): raw=原始(再取得可)/
#   intermediate=中間(削除・再生成自由)/ product=成果(下流の契約)/
#   delivery=配信(Viewer 公開面)/ cache=キャッシュ。
# - tag/run を templating に使う成果物は params 経由で解決する
#   (params にキーが無いと KeyError = 宣言漏れとして顕在化させる)。

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..paths import Dirs


@dataclass(frozen=True)
class Artifact:
    """1 つの成果物(ファイルまたはディレクトリ)の宣言。"""

    key: str
    desc: str
    kind: str
    path_fn: Callable[[Dirs, dict], Path]


REGISTRY: dict[str, Artifact] = {}

# 未移行ステージの生成物について、fail-fast メッセージで案内する旧来コマンド。
# ステージ移行が進んだら producer_of() がステージ名を返すようになり、
# この表は縮小していく(移行期間専用の橋渡し)。
LEGACY_HINTS: dict[str, str] = {
    "parsed.articles": "python -m wu parse",
    "parsed.article_ids": "python -m wu parse",
    "graph.edges_ns0": "python -m wu edges",
    "graph.categories": "python -m wu categories",
    "graph.article_categories": "python -m wu categories",
    "graph.edges_body_directed": "python -m wu body-edges",
    "graph.edges_undirected": "python scripts/run_full_leiden.py dedup",
    "community.membership": "python scripts/run_full_leiden.py detect / subdivide",
    "community.per_community": "python scripts/run_full_leiden.py metrics",
    "community.clusters_node": "python scripts/run_full_leiden.py cluster",
    "community.purity": "python scripts/community_purity.py --tag <tag>",
    "final.galaxies": "python scripts/build_galaxy_catalog.py",
    "final.macros": "python scripts/build_galaxy_catalog.py",
    "final.galaxy_pairs": "python scripts/build_galaxy_catalog.py",
    "final.macro_pairs": "python scripts/build_galaxy_catalog.py",
    "layout.galaxy_positions": "python scripts/layout_global.py --run <run>",
    "layout.macro_positions": "python scripts/layout_global.py --run <run>",
    "layout.meta": "python scripts/layout_global.py --run <run>",
    "layout.article_positions": "python scripts/run_local_parallel.py --run <run>",
    "graph.local_prep": "python scripts/layout_local.py --prep --galaxy-tag <tag>",
}


def define(key: str, desc: str, kind: str,
           path_fn: Callable[[Dirs, dict], Path]) -> Artifact:
    """成果物を登録する。二重登録は契約の衝突なのでエラーにする。"""
    if key in REGISTRY:
        raise ValueError(f"成果物キーが二重登録されました: {key}")
    a = Artifact(key=key, desc=desc, kind=kind, path_fn=path_fn)
    REGISTRY[key] = a
    return a


def resolve(key: str, dirs: Dirs, params: dict) -> Path:
    """成果物キーを実パスへ解決する(params = tag/run 等の解決用パラメータ)。"""
    if key not in REGISTRY:
        raise KeyError(f"未登録の成果物キー: {key}(artifacts.py に登録してください)")
    return Path(REGISTRY[key].path_fn(dirs, params))


def exists(key: str, dirs: Dirs, params: dict) -> bool:
    return resolve(key, dirs, params).exists()


def producer_of(key: str, stages) -> str | None:
    """成果物 key を outputs に宣言するステージ名を返す(fail-fast 案内用)。"""
    for st in stages:
        if key in st.outputs:
            return st.name
    return None


def hint_for(key: str) -> str:
    """未移行成果物の旧来コマンド案内(無ければ空文字)。"""
    return LEGACY_HINTS.get(key, "")


# ---------------------------------------------------------------------------
# 登録(移行済み/移行予定の主チェーン成果物。パスは paths.py の Dirs が正典で、
# ここは「キー → Dirs のどのパスか」の対応表を機械可読にする層)
# ---------------------------------------------------------------------------

define("parsed.articles", "page/linktarget/redirect 解析 → 記事表(page_id, title)",
       "intermediate", lambda d, p: d.articles)
define("parsed.article_ids", "記事 page_id の昇順配列(compact idx の基準)",
       "intermediate", lambda d, p: d.article_ids)
define("parsed.article_hashes", "記事タイトルの blake2b64 ハッシュ(page_id 昇順)",
       "intermediate", lambda d, p: d.article_hashes)
define("parsed.ns0_hashes", "ns0 全タイトルのハッシュ表(エッジ解決・本文リンク用)",
       "intermediate", lambda d, p: d.parsed / "ns0_all_hashes.npz")
define("parsed.rd_map", "リダイレクト解決表(rd_from → rd_final)",
       "intermediate", lambda d, p: d.parsed / "rd_map.npz")
define("parsed.meta", "解析メタデータ(dump_date・ファイル台帳・配置ポリシー)",
       "intermediate", lambda d, p: d.meta)
define("graph.edges_ns0", "解決済み ns0 有向エッジ(int32 ペアの生バイナリ)",
       "intermediate", lambda d, p: d.edges_bin)
define("graph.categories", "記事×カテゴリ対(parquet)",
       "intermediate", lambda d, p: d.graph / "categories.parquet")
define("graph.article_categories", "記事→カテゴリの生バイナリ(純度計算用)",
       "intermediate", lambda d, p: d.graph / "article_categories.bin")
define("graph.edges_body", "本文リンクの解決済み有向エッジ(out_name で名前解決)",
       "intermediate",
       lambda d, p: d.graph / p.get("out_name", "edges_body_directed.bin"))
define("graph.indeg", "記事ごとの in 次数(compact idx 順)",
       "intermediate", lambda d, p: d.indeg)
define("graph.outdeg", "記事ごとの out 次数(compact idx 順)",
       "intermediate", lambda d, p: d.outdeg)
define("graph.full_stats", "全グラフ次数統計・ハブ検出の結果 JSON",
       "product", lambda d, p: d.full_stats)
define("graph.local_prep", "銀河内レイアウトの run 非依存事前計算ディレクトリ",
       "cache", lambda d, p: d.local_prep_dir(p["tag"]))
define("graph.edges_undirected", "無向一意エッジ(dedup の出力。コミュニティ検出の入力)",
       "intermediate",
       lambda d, p: d.graph / p.get("out_name", "edges_undirected_unique.bin"))
define("community.membership", "ノード → コミュニティ ID(compact idx 順、tag 別)",
       "product", lambda d, p: d.community_full / f"membership_{p['tag']}.npy")
define("community.membership_out", "subdivide の出力 membership(out_tag で解決)",
       "product", lambda d, p: d.community_full / f"membership_{p['out_tag']}.npy")
define("community.subdivide_meta", "マクロ別分割の診断 JSON(out_tag 別)",
       "intermediate",
       lambda d, p: d.community_full / f"subdivide_meta_{p['out_tag']}.json")
define("community.membership_macro", "マクロ tag の membership(catalog の必須入力)",
       "product",
       lambda d, p: d.community_full / f"membership_{p['macro_tag']}.npy")
define("community.metrics_json", "コミュニティ指標の全体 JSON(tag 別)",
       "product", lambda d, p: d.community_full / f"metrics_{p['tag']}.json")
define("community.per_community", "コミュニティ別指標 + 代表記事(tag 別)",
       "product",
       lambda d, p: d.community_full / f"per_community_{p['tag']}.parquet")
define("community.per_community_macro", "マクロ tag の per_community(catalog の任意入力)",
       "product",
       lambda d, p: d.community_full / f"per_community_{p['macro_tag']}.parquet")
define("community.top_pairs", "コミュニティ間エッジの top ペア JSON(tag 別)",
       "intermediate",
       lambda d, p: d.community_full / f"top_pairs_{p['tag']}.json")
define("community.pairs", "コミュニティ間重み(pair)の npz(tag 別、L2 と台帳の入力)",
       "intermediate", lambda d, p: d.community_full / f"pairs_{p['tag']}.npz")
define("community.clusters_node", "L2 銀河団: ノード → クラスタ(tag 別)",
       "product",
       lambda d, p: d.community_full / f"clusters_node_{p['tag']}.npy")
define("community.clusters_comm", "L2 銀河団: コミュニティ → クラスタ(tag 別)",
       "product",
       lambda d, p: d.community_full / f"clusters_comm_{p['tag']}.npy")
define("community.clusters_meta", "L2 銀河団のメタ JSON(tag 別)",
       "intermediate",
       lambda d, p: d.community_full / f"clusters_meta_{p['tag']}.json")
define("community.membership_parquet", "page_id+title+comm+cluster の結合表(tag 別)",
       "product",
       lambda d, p: d.community_full / f"membership_{p['tag']}.parquet")
define("community.purity", "カテゴリ純度の parquet(name 列 = 銀河名の材料)",
       "product", lambda d, p: d.community_full / f"purity_{p['tag']}.parquet")
define("community.purity_json", "カテゴリ純度の集計 JSON",
       "product", lambda d, p: d.community_full / f"purity_{p['tag']}.json")
define("graph.edges_pruned", "次数予算剪定グラフ(実験腕。mode/budget で名前解決)",
       "intermediate",
       lambda d, p: d.graph / f"edges_pruned_{p.get('mode', 'smart')}{p.get('budget', 40)}.bin")
define("final.galaxies", "銀河台帳(galaxy_id/macro_id/n_articles/name/display_class 他)",
       "product", lambda d, p: d.final / "galaxies.parquet")
define("final.macros", "マクロ台帳(銀河団。n_articles/n_galaxies/rep_titles 他)",
       "product", lambda d, p: d.final / "macros.parquet")
define("final.galaxy_pairs", "銀河間ペア top-K(中景バンドル・レイアウトの入力)",
       "product", lambda d, p: d.final / "galaxy_pairs_topK.parquet")
define("final.macro_pairs", "マクロ間集約重み(遠景の数本・マクロ配置の入力)",
       "product", lambda d, p: d.final / "macro_pairs.parquet")
define("final.catalog_meta", "カタログ来歴(件数・包含チェック)",
       "product", lambda d, p: d.final / "catalog_meta.json")

# ---- 座標 run(layout/<run>/。run 名は共有パラメータで解決)----
define("layout.galaxy_positions", "銀河の中心・半径・display_class(run 別)",
       "product", lambda d, p: d.layout_run(p["run"]) / "galaxy_positions.parquet")
define("layout.macro_positions", "マクロの中心・半径(run 別)",
       "product", lambda d, p: d.layout_run(p["run"]) / "macro_positions.parquet")
define("layout.meta", "レイアウト来歴 + 品質指標 quality(run 別)",
       "product", lambda d, p: d.layout_run(p["run"]) / "layout_meta.json")
define("layout.article_positions", "記事座標(page_id, galaxy_id, x,y,z。マージ済み)",
       "product", lambda d, p: d.layout_run(p["run"]) / "article_positions.parquet")
define("layout.local_meta", "銀河内レイアウトのメタ + fr_quality(run 別)",
       "product", lambda d, p: d.layout_run(p["run"]) / "layout_local_meta.json")
define("layout.parallel_meta", "並列ランチャの実測テレメトリ(run 別)",
       "intermediate", lambda d, p: d.layout_run(p["run"]) / "parallel_meta.json")

# ---- 公開面(data/spatial/。run 名と無縁の固定 URL 契約 = delivery 分類)----
define("spatial.bootstrap", "ビューアの一括 fetch(宇宙+銀河ビューのデータ)",
       "delivery", lambda d, p: d.spatial / "bootstrap.json")
define("spatial.tiles", "銀河タイル(gal_XXXXXX.bin + sidecar json)のディレクトリ",
       "delivery", lambda d, p: d.tiles)
define("spatial.tiles_meta", "出版の集計メタ(件数・サイズ・契約バージョン)",
       "delivery", lambda d, p: d.spatial / "tiles_meta.json")

# ---- 原始データ(Wikipedia ダンプ。再取得可能 = raw 分類)----
for _k, _fn in (("page", "jawiki-latest-page.sql.gz"),
                ("redirect", "jawiki-latest-redirect.sql.gz"),
                ("linktarget", "jawiki-latest-linktarget.sql.gz"),
                ("pagelinks", "jawiki-latest-pagelinks.sql.gz"),
                ("categorylinks", "jawiki-latest-categorylinks.sql.gz"),
                ("pages_articles", "jawiki-latest-pages-articles.xml.bz2")):
    define(f"dump.{_k}", f"Wikipedia ダンプ: {_fn}", "raw",
           (lambda name: (lambda d, p: d.dump / name))(_fn))
