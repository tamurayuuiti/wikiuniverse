# wu/stages - パイプラインのステージ実体(1 ステージ群 = 1 モジュール)
#
# 責務:
# - 各ステージを import 時にレジストリへ登録する(この __init__ を import する
#   だけで全登録が揃う = ランナー/CLI からの単一の入口)。
#
# 注意:
# - **import 順 = canonical グループのパイプライン実行順**(runner が "all" を
#   登録順で解決するため)。前段 → 後段の順に並べること。
# - ステージの追加手順: このディレクトリにモジュールを置き、@stage で登録し、
#   下の import 列の正しい位置(実行順)に追加する。入出力は必ず artifacts.py へ
#   成果物を登録してからキーで宣言する(パスの f-string 直組み禁止)。
# - すべての処理ステージはこのディレクトリの宣言を経由する(CLI・テスト・
#   将来の GUI からも同じ入口 = wu.pipeline.runner が唯一の実行系)。

from . import ingest  # noqa: F401  (download/parse/edges/categories/body_edges)
from . import stats  # noqa: F401  (stats)
from . import community  # noqa: F401  (dedup/detect/subdivide/metrics/cluster/export/prune)
from . import catalog  # noqa: F401  (purity/catalog)
from . import layout  # noqa: F401  (layout_global/layout_local/recompose)
from . import publish  # noqa: F401  (publish = spatial/ への出版)
from . import audit  # noqa: F401  (audit_tiles/audit_names/audit_layout)
