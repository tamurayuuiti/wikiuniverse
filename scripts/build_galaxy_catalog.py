#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/catalog.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/build_galaxy_catalog.py --base data
             --galaxy-tag res1_sub --macro-tag res1
正準実行: python -m wu run catalog
テストが _stem_name 等を spec ロードするため private 名も再エクスポートする。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.catalog import (_blacklisted, _parse_top3, _stem_name,  # noqa: E402,F401
                        main)

if __name__ == "__main__":
    main()
