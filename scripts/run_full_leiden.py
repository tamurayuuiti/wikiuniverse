#!/usr/bin/env python3
"""互換エントリポイント — 実体は wu/fullgraph.py へ移設済み(2026-10-02)。

使い方(不変): python scripts/run_full_leiden.py --base data <dedup|detect|...>
正準実行: python -m wu run <stage>(wu/stages/community.py 参照)。
テスト/既存手順が本ファイルを spec ロード・subprocess 起動するため、
公開名(cmd_* / SEED / SHIFT / _full_dir / main)を明示的に再エクスポートする。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wu.fullgraph import (SEED, SHIFT, _full_dir, _res_tag, _tag_from_args,  # noqa: E402,F401
                          cmd_all, cmd_cluster, cmd_dedup, cmd_detect,
                          cmd_export, cmd_metrics, cmd_prune, cmd_subdivide,
                          main)

if __name__ == "__main__":
    main()
