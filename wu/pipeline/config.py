# wu/pipeline/config.py - 設定の読み込み・マージ・検証
#
# 責務:
# - 「ステージの Param 既定 < 設定ファイル defaults < 設定ファイル stages.<name>
#   < CLI --set」の優先度でステージパラメータを解決する。
# - 共有設定(shared: run/galaxy_tag/macro_tag 等)も同じファイルから供給し、
#   コード内に散らばった既定値を configs/canonical.json へ集約していく
#   (既定値の分散・二重定義・正準運用との乖離を防ぐ)。
#
# 注意:
# - JSON を採用する理由: stdlib のみで読み書きでき、将来 GUI が dict を直接
#   渡せる(TOML は書出しに他ライブラリが必要で、requirements は固定方針)。
# - ステージが宣言していないキーが stages.<name> に混入したらエラーにする
#   (タイポによる「効かない設定」の黙殺を防ぐ)。
# - --set の値は JSON リテラルとして解釈する(5 → int、0.6 → float、
#   '"ml"' → str)。解釈できなければ文字列として扱う。

from __future__ import annotations

import copy
import json
import os

from .stage import Stage

EMPTY: dict = {"shared": {}, "defaults": {}, "stages": {}}


def load(path: str | None = None) -> dict:
    """設定ファイル(JSON)を読む。path=None なら空設定(既定値のみで動作)。"""
    cfg = copy.deepcopy(EMPTY)
    if path is None:
        return cfg
    if not os.path.exists(path):
        raise FileNotFoundError(f"設定ファイルがありません: {path}")
    with open(path, encoding="utf-8") as f:
        user = json.load(f)
    for sect in ("shared", "defaults", "stages"):
        if sect in user:
            cfg[sect] = user[sect]
    # 未知セクションはタイポの可能性として警告(致命にしない: _comment 等の慣用)
    unknown = [k for k in user if k not in ("shared", "defaults", "stages")
               and not k.startswith("_")]
    if unknown:
        print(f"[wu][warn] 設定に未知のセクション: {unknown}(無視します)",
              flush=True)
    return cfg


def parse_set_items(items) -> list:
    """--set "stage.key=value" のリストを (stage, key, value) へ変換する。"""
    out = []
    for it in items or []:
        if "=" not in it or "." not in it.split("=", 1)[0]:
            raise ValueError(
                f"--set の形式は <stage>.<key>=<value> です(受信: {it!r})")
        loc, raw = it.split("=", 1)
        st_name, key = loc.split(".", 1)
        try:
            val = json.loads(raw)
        except json.JSONDecodeError:
            val = raw
        out.append((st_name, key, val))
    return out


def apply_overrides(cfg: dict, items) -> dict:
    """--set 相当の上書きを cfg(複製)へ適用する。"""
    cfg = copy.deepcopy(cfg)
    for st_name, key, val in parse_set_items(items):
        cfg["stages"].setdefault(st_name, {})[key] = val
    return cfg


def _coerce(p, val, where: str):
    """Param 宣言に沿った軽い型変換と choices 検証(エラーは発生箇所付き)。"""
    if p.choices is not None and val not in p.choices:
        raise ValueError(
            f"{where}: {p.name}={val!r} は候補 {list(p.choices)} の外です")
    if p.type is int and isinstance(val, float) and float(val).is_integer():
        val = int(val)
    if val is not None and not isinstance(val, p.type):
        try:
            val = p.type(val)
        except (TypeError, ValueError):
            raise ValueError(
                f"{where}: {p.name}={val!r} を {p.type.__name__} に変換できません")
    return val


def stage_params(st: Stage, cfg: dict) -> dict:
    """ステージのパラメータを解決する(優先度: 既定 < defaults < stages < --set)。

    --set は apply_overrides で cfg へ織り込み済みの前提。defaults は
    「ステージが宣言している名前」のキーのみ採用する(seed 等の共通パラメータを
    全ステージへ配るため。未宣言キーの混入は stages と同様にエラー)。
    """
    declared = {p.name: p for p in st.params}
    vals = {name: p.default for name, p in declared.items()}
    for k, v in (cfg.get("defaults") or {}).items():
        if k in declared:
            vals[k] = _coerce(declared[k], v, f"defaults.{k}")
    sect = (cfg.get("stages") or {}).get(st.name) or {}
    for k, v in sect.items():
        if k not in declared:
            raise KeyError(
                f"stages.{st.name}.{k} は未宣言のパラメータです"
                f"(宣言: {sorted(declared)}。タイポか、ステージ側の追加漏れ)")
        vals[k] = _coerce(declared[k], v, f"stages.{st.name}.{k}")
    return vals


def shared_params(cfg: dict, overrides: dict | None = None) -> dict:
    """共有パラメータ(設定ファイルの shared + CLI 明示)をマージする。

    CLI 明示(run/galaxy_tag/macro_tag)が設定ファイルより優先する。
    run 未指定のときは paths.ACTIVE_LAYOUT_RUN(正典 run)を採用する
    (将来的には設定ファイル側へ移す = 移行計画チェックポイント6)。
    """
    from ..paths import ACTIVE_LAYOUT_RUN
    shared = dict(cfg.get("shared") or {})
    shared.setdefault("run", None)
    if shared["run"] is None:
        shared["run"] = ACTIVE_LAYOUT_RUN
    shared.setdefault("galaxy_tag", "res1_sub")
    shared.setdefault("macro_tag", "res1")
    for k, v in (overrides or {}).items():
        if v is not None:
            shared[k] = v
    # 成果物解決では "tag" = galaxy_tag の別名としても引けるようにする
    # (community 系の成果物キーは tag で templating されているため)
    shared.setdefault("tag", shared["galaxy_tag"])
    return shared
