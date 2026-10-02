"""Canonical data-directory layout for wikiuniverse (run-based layout).

Everything produced by the pipeline lives under a single `--base` directory
(default `data/`), organized BY PIPELINE STAGE so intermediate artifacts are
separated instead of piled into one folder:

    data/
      dump/         raw Wikipedia dumps            input, re-downloadable
      parsed/       page/linktarget/redirect       intermediate (rebuilt by `parse`)
      graph/        edges + degree/stats artifacts intermediate (rebuilt by `edges`)
        subsets/    per-subset node/edge cuts      intermediate (Phase-1 PoC)
        local_prep/ per-tag prep for layout_local  intermediate (rebuilt by
          <tag>/      (bucketed internal edges,    `layout_local --prep`; shared
                       grouped cross pairs, ext_deg) across layout runs, B0)
      community/    Leiden outputs                 result
        full/       full-graph runs: membership_<tag>.npy, pairs, metrics, purity
        <subset>/   Phase-1 per-subset analyze runs
      final/        galaxy/macro catalog           product (layout + viewer contract)
      layout/       one directory per layout run   product of the coordinate stage
        <run>/      galaxy/macro/article positions + metas + previews
      spatial/      published viewer delivery      product, consumed over HTTP
        tiles/      per-galaxy streaming tiles

Layout run naming: `<YYYYMMDD>_<slug>` (e.g. `20260926_baseline`). The slug
says what the run is; exact parameters and timestamps live in that run's
`layout_meta.json`. `ACTIVE_LAYOUT_RUN` below names the run currently published
to `spatial/` and is the default of every layout script.

Artifact classes (used when deciding what may be deleted):
    raw          dump/                             re-downloadable input
    intermediate parsed/, graph/, subsets/,        mechanically rebuildable from
                 local_prep/                       upstream (local_prep is shared
                                                    by all layout runs: rebuild with
                                                    `layout_local --prep`)
    product      community/, final/, layout/<run>/*.parquet, spatial/
    cache        *_checkpoint.json, layout/<run>/article_shards/,
                 layout/<run>/parallel_jobs/       freely deletable

community/full tag convention: `res<R>` = one-shot macro partition,
`res<R>_sub` = two-stage (galaxy) partition, `*_body` = body-link graph variant,
`*_B40` etc. = pruned-graph variant. Canonical partition: galaxies = res1_sub,
macros = res1.

Retired layout runs are moved out of data/layout/ into local untracked storage
(e.g. an archive/ directory at the repo root); the adoption procedure for new
runs is documented in LOCAL_SETUP.md §6.

This module is the single source of truth for data paths: scripts must derive
paths from `Dirs` instead of assembling them by hand.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The published (canonical) layout run. Updating this one constant switches
# every layout script and the tile export to a different data/layout/<run>/.
ACTIVE_LAYOUT_RUN = "20261002_v16"


@dataclass
class Dirs:
    """All canonical paths, derived from one base directory.

    Pass an absolute base (e.g. /content/drive/MyDrive/wudata) when running on
    Colab so large artifacts persist; locally use the default ./data.
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
        """Create `path` (and parents) if missing and return it."""
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
        """Path of a dump file by local name (see wu.dumpio.FILES for keys)."""
        return self.dump / local_name

    def subset(self, name: str) -> str:
        """Per-subset cut directory under graph/subsets/ (str for os.path use)."""
        return str(self.subsets / name)

    def community_run(self, name: str) -> str:
        """Community-detection run directory under community/ (str)."""
        return str(self.community / name)

    def local_prep_dir(self, tag: str) -> Path:
        """Prep artifact directory for layout_local (B0 foundation), per galaxy tag.

        Holds run-independent heavy work (bucketed internal edges, grouped
        cross-galaxy pairs, ext_deg) shared by every layout run and every
        parallel job. Intermediate class: freely deletable, rebuilt by
        `layout_local --prep`.
        """
        return self.local_prep / tag

    def layout_run(self, run: str | None = None) -> Path:
        """Return data/layout/<run>, defaulting to ACTIVE_LAYOUT_RUN.

        The run name must be a single path segment: separators are rejected so
        a typo cannot escape the layout tree.
        """
        name = ACTIVE_LAYOUT_RUN if run is None else run
        if (not isinstance(name, str) or not name or name in (".", "..")
                or "/" in name or "\\" in name or Path(name).is_absolute()):
            raise ValueError(f"invalid layout run name: {name!r}")
        return self.layout / name
