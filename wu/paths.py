"""wikiuniverse の正準データディレクトリ配置(run ベースのレイアウト)。

パイプラインが生み出すものはすべて単一の `--base` ディレクトリ(既定 `data/`)配下に、
**工程別**に整理して置く(中間生成物を 1 フォルダに山積みにせず分離する):

    data/
      dump/         Wikipedia の生ダンプ              入力・再ダウンロード可能
      parsed/       page/linktarget/redirect          中間(`parse` が再構築)
      graph/        エッジ + 次数/統計の成果物        中間(`edges` が再構築)
        subsets/    サブセット毎のノード/エッジ切り出し 中間(第1期 PoC)
        local_prep/ layout_local の tag 毎 prep        中間(`layout_local --prep`
          <tag>/      (バケット化した内部エッジ、      が再構築。レイアウト run を
                       グループ化クロスペア、ext_deg)   跨いで共有)
      community/    Leiden 出力                       成果
        full/       フルグラフ run: membership_<tag>.npy, pairs, metrics, purity
        <subset>/   第1期のサブセット別 analyze run
      final/        銀河/マクロ台帳                   製品(レイアウト + viewer の契約)
      layout/       レイアウト run 毎に 1 ディレクトリ 座標ステージの製品
        <run>/      銀河/マクロ/記事の座標 + meta + プレビュー
      spatial/      出版されたビューア配信用          製品。HTTP 経由で消費される
        tiles/      銀河毎のストリーミングタイル
      manifests/    ランナーの実行台帳                中間(追加専用。`wu run` 1 回毎に
                    1 JSON: ステージ・解決パラメータ・所要時間・バージョン・
                    git スタンプ。削除可能でデータに影響しない)

レイアウト run の命名: `<YYYYMMDD>_<slug>`(例 `20260926_baseline`)。slug は
その run が何であるかを表し、正確なパラメータとタイムスタンプはその run の
`layout_meta.json` に入る。出版中(正典)の run 名は `configs/publish.json`
(下の `ACTIVE_LAYOUT_RUN` へ読み込まれる)にあり、すべてのレイアウト系コマンドの
既定値である。

成果物の分類(削除可否の判断に使う):
    raw          dump/                             再ダウンロード可能な入力
    intermediate parsed/, graph/, subsets/,        上流から機械的に再構築可能
                 local_prep/                       (local_prep は全レイアウト run の
                                                    共有: `layout_local --prep` で再構築)
    product      community/, final/, layout/<run>/*.parquet, spatial/
    cache        *_checkpoint.json, layout/<run>/article_shards/,
                 layout/<run>/parallel_jobs/       自由に削除可能

community/full の tag 規約: `res<R>` = 一括マクロ分割、
`res<R>_sub` = 2 段階(銀河)分割、`*_body` = 本文リンクグラフ変種、
`*_B40` 等 = 剪定グラフ変種。正準分割: 銀河 = res1_sub、マクロ = res1。

引退したレイアウト run は data/layout/ からローカルの追跡外ストレージ
(例: リポジトリ直下の archive/ ディレクトリ)へ移す。新 run の採用手順は
LOCAL_SETUP.md §6 に記載されている。

このモジュールはデータパスの単一の真実源である: スクリプトはパスを手組みせず
`Dirs` から導出しなければならない。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# 出版中(正典)の座標 run。**真実源は configs/publish.json**(追跡ファイル。
# 採用切替は同ファイルの編集 + commit で行う = 正典 run 名の単一の真実源)。
# このファイルは wu パッケージのどのモジュールよりも先に読まれるため、
# json 読み込みはここで一度だけ行い、失敗時のみフォールバック定数を使う。
def _load_active_layout_run() -> str:
    import json as _json
    cfg = Path(__file__).resolve().parent.parent / "configs" / "publish.json"
    try:
        with open(cfg, encoding="utf-8") as f:
            v = _json.load(f).get("active_layout_run")
        if isinstance(v, str) and v:
            return v
        print(f"[paths][warn] {cfg} に active_layout_run がありません"
              f"(フォールバックを使用)", flush=True)
    except FileNotFoundError:
        print(f"[paths][warn] {cfg} がありません(フォールバックを使用)",
              flush=True)
    return "20261002_v16"


ACTIVE_LAYOUT_RUN = _load_active_layout_run()


@dataclass
class Dirs:
    """単一の base ディレクトリから導出される、すべての正準パス。

    Colab で実行するときは大きな成果物を永続化するため絶対パスの base
    (例 /content/drive/MyDrive/wudata)を渡す。ローカルでは既定の ./data を使う。
    """

    base: Path

    def __post_init__(self) -> None:
        self.base = Path(self.base)
        # --- stage directories
        self.dump = self.base / "dump"
        self.parsed = self.base / "parsed"
        self.graph = self.base / "graph"
        self.subsets = self.graph / "subsets"
        self.local_prep = self.graph / "local_prep"
        self.community = self.base / "community"
        self.community_full = self.community / "full"
        self.final = self.base / "final"
        self.layout = self.base / "layout"
        self.spatial = self.base / "spatial"
        self.tiles = self.spatial / "tiles"
        # 実行台帳(pipeline runner がステージ実行の来歴を記録する。中間生成物:
        # 削除自由・パイプラインの動作には影響しない)
        self.manifests = self.base / "manifests"
        # --- frequently-used file paths
        self.meta = self.parsed / "meta.json"
        self.articles = self.parsed / "articles.parquet"
        self.article_ids = self.parsed / "article_ids.npy"
        self.article_hashes = self.parsed / "article_hashes_sorted_by_id.npy"
        self.edges_bin = self.graph / "edges_ns0.bin"
        self.edges_ckpt = self.graph / "edges_checkpoint.json"
        self.indeg = self.graph / "indeg.npy"
        self.outdeg = self.graph / "outdeg.npy"
        self.full_stats = self.graph / "full_stats.json"

    # --- creation ----------------------------------------------------------
    @staticmethod
    def ensure(path) -> Path:
        """`path`(と親ディレクトリ)が無ければ作成して返す。"""
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def ensure_core(self) -> "Dirs":
        for d in (self.base, self.dump, self.parsed, self.graph,
                  self.subsets, self.community):
            d.mkdir(parents=True, exist_ok=True)
        return self

    # --- derived names -----------------------------------------------------
    def dump_file(self, local_name: str) -> Path:
        """ローカル名によるダンプファイルのパス(キーは wu.dumpio.FILES 参照)。"""
        return self.dump / local_name

    def subset(self, name: str) -> str:
        """graph/subsets/ 配下のサブセット毎の切り出しディレクトリ(os.path 用に str)。"""
        return str(self.subsets / name)

    def community_run(self, name: str) -> str:
        """community/ 配下のコミュニティ検出 run ディレクトリ(str)。"""
        return str(self.community / name)

    def local_prep_dir(self, tag: str) -> Path:
        """layout_local の prep 成果物ディレクトリ(run 非依存の共有 prep)、銀河 tag 毎。

        run 非依存の重い前処理(バケット化した内部エッジ、グループ化した
        銀河間クロスペア、ext_deg)を保持し、すべてのレイアウト run とすべての
        並列ジョブで共有される。中間クラス: 自由に削除可能で、
        `layout_local --prep` が再構築する。
        """
        return self.local_prep / tag

    def layout_run(self, run: str | None = None) -> Path:
        """data/layout/<run> を返す。既定は ACTIVE_LAYOUT_RUN。

        run 名は単一のパスセグメントでなければならない: タイプミスがレイアウト
        ツリーの外へ逃げられないよう、セパレータは拒否する。
        """
        name = ACTIVE_LAYOUT_RUN if run is None else run
        if (not isinstance(name, str) or not name or name in (".", "..")
                or "/" in name or "\\" in name or Path(name).is_absolute()):
            raise ValueError(f"invalid layout run name: {name!r}")
        return self.layout / name
