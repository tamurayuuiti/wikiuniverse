#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/layout_parallel.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/run_local_parallel.py --base data --run <RUN>
正準実行: python -m wu run layout_local(このランチャへ委譲する)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.layout_parallel import main  # noqa: E402,F401

if __name__ == "__main__":
    main()
