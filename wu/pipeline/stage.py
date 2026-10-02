# wu/pipeline/stage.py - ステージ定義とレジストリ
#
# 責務:
# - パイプラインの 1 処理 = 1 ステージとして、名前・分類・パラメータ仕様・
#   入出力契約(成果物キー)・実行関数を宣言的に登録する。
# - 登録順を canonical グループの実行順(= パイプライン順)として保持する。
#
# 注意:
# - ステージ実体は wu/stages/<name>.py に置き、import 時に @stage で登録される。
# - run(ctx) は副作用(ファイル生成)のみを行い、戻り値で状態を返さない
#   (成否は例外、生成物は outputs 宣言で検査する = ランナーの契約)。
# - params は Param で宣言し、既定値の「単一の真実源」にする。実運用値は
#   configs/canonical.json → CLI --set の順で上書きされる(config.py 参照)。

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..paths import Dirs
from . import artifacts


@dataclass(frozen=True)
class Param:
    """ステージパラメータ 1 つの仕様(名前・型・既定値・説明・選択候補)。"""

    name: str
    type: type = str
    default: Any = None
    help: str = ""
    choices: tuple | None = None


@dataclass
class Ctx:
    """ステージ実行コンテキスト(ランナーが構築して run(ctx) へ渡す)。

    params: このステージ用に解決済みのパラメータ(既定 < 設定 < --set)。
    shared: 実行全体で共有する解決キー(run/galaxy_tag/macro_tag/tag/dump_date 等)。
            成果物キーの path 解決は {**shared, **params} を使う。
    """

    dirs: Dirs
    params: dict
    shared: dict
    force: bool = False

    def resolve(self, key: str):
        """成果物キーを実パスへ解決する(shared + params で templating)。

        params の None 値は「共有パラメータへフォールバック」なので上書きしない
        (runner.plan と同一のマージ規則)。
        """
        merged = {**self.shared,
                  **{k: v for k, v in self.params.items() if v is not None}}
        return artifacts.resolve(key, self.dirs, merged)

    def path(self, key: str) -> str:
        """resolve の str 版(既存実装が os.path 前提のため)。"""
        return str(self.resolve(key))

    def log(self, msg: str) -> None:
        print(msg, flush=True)


@dataclass(frozen=True)
class Stage:
    """登録された 1 ステージの完全な宣言。"""

    name: str                 # CLI ターゲット名(一意)
    title: str                # 日本語の 1 行責務説明
    group: str                # canonical | optional | audit | experiment
    params: tuple[Param, ...]
    inputs: tuple[str, ...]   # 必須入力の成果物キー
    outputs: tuple[str, ...]  # 生成を宣言する成果物キー
    run: Callable[[Ctx], None]
    # True = 「生成物あり → skip」にしない再実行可能ステージ。
    # 座標・出版は同一 run の作り直し/腕違いの実験/正典の再出版が日常操作で、
    # skip がそれを阻害する(重い再計算は run 名・内部 checkpoint が管理する)。
    always_run: bool = False


REGISTRY: dict[str, Stage] = {}
ORDER: list[str] = []  # 登録順 = canonical グループのパイプライン順


def stage(name: str, title: str, group: str = "canonical",
          params: tuple = (), inputs: tuple = (), outputs: tuple = (),
          always_run: bool = False):
    """ステージ登録デコレータ(関数を run として Stage をレジストリへ追加)。"""

    def deco(fn: Callable[[Ctx], None]):
        if name in REGISTRY:
            raise ValueError(f"ステージ名が二重登録されました: {name}")
        REGISTRY[name] = Stage(name=name, title=title, group=group,
                               params=tuple(params), inputs=tuple(inputs),
                               outputs=tuple(outputs), run=fn,
                               always_run=always_run)
        ORDER.append(name)
        return fn

    return deco


def get(name: str) -> Stage:
    if name not in REGISTRY:
        known = ", ".join(ORDER) or "(なし)"
        raise KeyError(f"未登録のステージ: {name}(登録済み: {known})")
    return REGISTRY[name]


def all_stages() -> list[Stage]:
    return [REGISTRY[n] for n in ORDER]


def by_group(group: str) -> list[Stage]:
    return [s for s in all_stages() if s.group == group]
