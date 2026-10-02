# wu/stages — パイプラインのステージ実体(1 ステージ群 = 1 モジュール)
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
# - 既存スクリプトからの移行は strangler 方式(挙動を変えずに移し、旧入口は
#   ラッパ化する)。移行計画 = knowledge/work/pipeline-cleanup.md。

from . import ingest  # noqa: F401  (download/parse/edges/categories/body_edges)
from . import stats  # noqa: F401  (stats)
