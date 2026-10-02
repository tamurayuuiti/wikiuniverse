#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/purity.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/community_purity.py --base data --tag <tag>
正準実行: python -m wu run purity
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.purity import main  # noqa: E402,F401

if __name__ == "__main__":
    main()
