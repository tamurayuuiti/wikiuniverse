#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/layout_global.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/layout_global.py --base data --run <RUN> …
正準実行: python -m wu run layout_global
audit_layout と test_catalog が flatness 等を import/spec ロードするため、
公開名・private 名を明示的に再エクスポートする。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.layout_global import (  # noqa: E402,F401
    FLAT_MIN_MEMBERS, R_TOTAL, _CONE_MAX, _cone_spread, _fib_ball_slots,
    _fib_dir, _now, disk_overlap_fraction, flatness, fr_layout,
    macro_adjacency, main, medium_local, projection_overlap_stats,
    relax_disks, sphere_overlap_fraction, write_html_preview)

if __name__ == "__main__":
    main()
