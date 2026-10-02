# wu/pipeline - パイプライン基盤(ステージレジストリ・設定・ランナー)
#
# 責務:
# - 「取得 → 前処理 → グラフ/コミュニティ → 座標 → Viewer データ」の各処理を
#   ステージ(Stage)として登録し、入出力契約(Artifact)・設定(Config)・
#   実行制御(Runner)を分離して提供する。
# - CLI(python -m wu run …)も将来の GUI も、同じランナー API を呼ぶだけでよい
#   (実行方法から処理本体を独立させる = 設計決定 D-j)。
#
# 注意:
# - ステージ実体は wu/stages/ に 1 ステージ = 1 モジュールで置く。
#   import 時にレジストリへ登録されるため、wu.stages を import すること。
# - 実行系はこの基盤(runner)へ一本化されている: ステージ宣言(wu/stages/)が
#   入出力契約とパラメータの単一の真実源で、CLI・テスト・将来の GUI からも
#   同じ plan/execute 経路を使う。
#   移行計画は knowledge/work/pipeline-cleanup.md 参照)。

from . import artifacts, config, runner  # noqa: F401
from .stage import Ctx, Param, Stage, all_stages, by_group, get, stage  # noqa: F401
