#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/audit_tiles.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/audit_tiles.py --base data [--limit N]
正準実行: python -m wu run audit_tiles
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.audit_tiles import RECORDED, main  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(main())
