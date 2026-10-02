#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/publish.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/export_viewer_tiles.py --base data [--run RUN]
正準実行: python -m wu run publish
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.publish import SCHEMA_VERSION, main  # noqa: E402,F401

if __name__ == "__main__":
    main()
