"""Canonical data layout (policy agreed 2026-09-25).

    <base>/dump/        元の Wikipedia ダンプ(.sql.gz)
    <base>/parsed/      ダンプから抽出・変換した中間データ
                        (記事表・ハッシュ表・リダイレクト解決表・meta)
    <base>/graph/       全 ns0 グラフ(解決済みエッジ・次数・統計)と subsets/
    <base>/community/   Leiden・階層コミュニティの出力(必要になった時点で作成)

将来の予約名(必要に応じて追加、今は作らない):
    <base>/layout/      グローバル・ローカル 3D 座標
    <base>/spatial/     Morton/Octree・空間チャンク・LOD
    <base>/final/       Web 配信用の最終データ

コード・レポート等は data/ 外の既存構成(wu/ scripts/ tests/ notebooks/ results/)を維持。
"""
from __future__ import annotations

import os

FUTURE_RESERVED = ("layout", "spatial", "final")


class Dirs:
    """All canonical artifact paths in one place (single source of truth)."""

    def __init__(self, base: str = "data"):
        self.base = base
        self.dump = os.path.join(base, "dump")
        self.parsed = os.path.join(base, "parsed")
        self.graph = os.path.join(base, "graph")
        self.community = os.path.join(base, "community")
        self.subsets = os.path.join(self.graph, "subsets")

    # ---- parsed/ (dump → intermediate) ----
    @property
    def meta(self) -> str:
        return os.path.join(self.parsed, "meta.json")

    @property
    def articles_parquet(self) -> str:
        return os.path.join(self.parsed, "articles.parquet")

    @property
    def article_ids(self) -> str:
        return os.path.join(self.parsed, "article_ids.npy")

    @property
    def article_hashes(self) -> str:
        return os.path.join(self.parsed, "article_hashes_sorted_by_id.npy")

    @property
    def ns0_hashes(self) -> str:
        return os.path.join(self.parsed, "ns0_all_hashes.npz")

    @property
    def lt_hash(self) -> str:
        return os.path.join(self.parsed, "lt_hash_ns0.npy")

    @property
    def rd_map(self) -> str:
        return os.path.join(self.parsed, "rd_map.npz")

    # ---- graph/ (full ns0 graph + subsets) ----
    @property
    def edges_bin(self) -> str:
        return os.path.join(self.graph, "edges_ns0.bin")

    @property
    def edges_ckpt(self) -> str:
        return os.path.join(self.graph, "edges_checkpoint.json")

    @property
    def indeg(self) -> str:
        return os.path.join(self.graph, "indeg.npy")

    @property
    def outdeg(self) -> str:
        return os.path.join(self.graph, "outdeg.npy")

    @property
    def full_stats(self) -> str:
        return os.path.join(self.graph, "full_stats.json")

    # ---- helpers ----
    def dump_file(self, name: str) -> str:
        return os.path.join(self.dump, name)

    def subset(self, name: str) -> str:
        return os.path.join(self.subsets, name)

    def community_run(self, name: str) -> str:
        return os.path.join(self.community, name)

    def ensure(self, *dirs: str):
        for d in dirs:
            os.makedirs(d, exist_ok=True)

    def ensure_core(self):
        self.ensure(self.parsed, self.graph)
