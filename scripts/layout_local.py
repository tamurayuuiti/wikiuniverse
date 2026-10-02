#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/layout_local.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/layout_local.py --base data --run <RUN> --galaxies all
正準実行: python -m wu run layout_local(並列ランチャ経由)
並列ランチャ(wu/layout_parallel.py)はこのラッパを subprocess 起動する。
test_catalog が private 名(_fr_unit 等)を spec ロードするため明示再エクスポートする。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.layout_local import (  # noqa: E402,F401
    _fr_unit, _prep_paths, _rank_quantile, build_anchor_dirs, build_anchors,
    cmd_merge, cmd_prep, galaxy_layout_quality, load_prep, main)

if __name__ == "__main__":
    main()
