#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/audit_layout.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/audit_layout.py --base data [--run RUN]
正準実行: python -m wu run audit_layout
test_catalog が audit/load_run/load_catalog/print_report/RunMissing/_null_flat を
spec ロードするため、明示的に再エクスポートする。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.audit_layout import (  # noqa: E402,F401
    SPILL_TOL, TOP_IDS, RunMissing, _null_flat, _strings, audit, load_catalog,
    load_run, main, print_report)

if __name__ == "__main__":
    sys.exit(main())
