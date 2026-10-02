# wu/pipeline/runner.py — 実行計画(plan)・実行(execute)・実行台帳(manifest)
#
# 責務:
# - ターゲット(ステージ名 or "all")を展開し、入出力契約から
#   「実行 / スキップ(生成物あり)/ 入力不足(fail-fast)」を計画する。
# - 実行し、ステージごとの解決パラメータ・所要時間・生成物・バージョンを
#   data/manifests/<ts>_<label>.json へ記録する(来歴の総台帳 = 問題 P11 の解決)。
# - 失敗時は「どの成果物が無いか、どのステージ(または旧来コマンド)で作れるか」を
#   示して即終了する(既存スクリプト群の fail-fast 方針を一般化する)。
#
# 注意:
# - スキップ判定は「宣言 outputs の存在」のみ(v1)。タイムスタンプ比較による
#   鮮度判定はしない — 作り直しは --force。存在しても中身の正しさは
#   各ステージの契約テスト/監査スクリプトの責務。
# - 実行は登録順(canonical)か指定順。依存ステージの暗黙実行はしない
#   (予測可能性優先。足りない入力は fail-fast が案内する)。
# - このモジュールはステージ実体を import しない(呼び出し側が wu.stages を
#   import して登録を済ませてから呼ぶ)。

from __future__ import annotations

import datetime
import json
import os
import platform
import subprocess
import sys
import time

from . import artifacts
from .config import shared_params, stage_params
from .stage import ORDER, REGISTRY, Ctx, Stage


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def versions() -> dict:
    """実行環境のバージョン(再現性の記録 = 既存 metrics の _versions 方針)。"""
    v = {"python": platform.python_version(), "platform": platform.platform()}
    for mod in ("numpy", "igraph", "pyarrow", "scipy", "leidenalg"):
        try:
            m = __import__(mod)
            v[mod] = getattr(m, "__version__", "?")
        except ImportError:
            v[mod] = None
    return v


def git_stamp(repo_root: str) -> dict:
    """リポジトリの現在コミットと dirty 状態(記録専用・失敗しても致命にしない)。"""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, timeout=10)
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=repo_root,
            capture_output=True, text=True, timeout=10)
        if head.returncode == 0:
            return {"head": head.stdout.strip(),
                    "dirty": bool(dirty.stdout.strip())}
    except Exception:
        pass
    return {"head": None, "dirty": None}


def expand_targets(targets) -> list[Stage]:
    """ターゲット名を展開する。"all" = canonical グループ(登録順)。"""
    out: list[Stage] = []
    for t in targets:
        if t == "all":
            out.extend(REGISTRY[n] for n in ORDER
                       if REGISTRY[n].group == "canonical")
        else:
            if t not in REGISTRY:
                known = ", ".join(ORDER) or "(なし)"
                raise KeyError(f"未登録のステージ: {t}(登録済み: {known})")
            out.append(REGISTRY[t])
    # 同一ステージの重複指定は 1 回に畳む(順序は初出を維持)
    seen, uniq = set(), []
    for st in out:
        if st.name not in seen:
            seen.add(st.name)
            uniq.append(st)
    return uniq


def plan(stages, dirs, cfg, shared: dict, force: bool = False) -> list[dict]:
    """各ステージの状態を解決する: run / skip / missing-input。"""
    entries = []
    for st in stages:
        params = stage_params(st, cfg)
        p_all = {**shared, **params}
        missing = [k for k in st.inputs
                   if not artifacts.exists(k, dirs, p_all)]
        outs = {k: str(artifacts.resolve(k, dirs, p_all)) for k in st.outputs}
        outputs_exist = bool(outs) and all(os.path.exists(v) for v in outs.values())
        if missing:
            status = "missing-input"
        elif outputs_exist and not force:
            status = "skip"
        else:
            status = "run"
        entries.append({"stage": st, "params": params, "shared_used": p_all,
                        "missing": missing, "outputs": outs, "status": status})
    return entries


def _missing_message(e: dict, dirs) -> str:
    """fail-fast 案内: 何が無いのか、どう作るのか(移行状況に応じて)。"""
    lines = []
    for key in e["missing"]:
        art = artifacts.REGISTRY.get(key)
        desc = art.desc if art else "(未登録)"
        path = artifacts.resolve(key, dirs, e["shared_used"]) if art else "?"
        producer = artifacts.producer_of(key, REGISTRY.values())
        how = (f"ステージ `{producer}`(python -m wu run {producer})で生成"
               if producer else
               f"旧来コマンドで生成: {artifacts.hint_for(key) or '(手段不明)'}")
        lines.append(f"  - {key}: {desc}\n    パス: {path}\n    {how}")
    return (f"[wu] ステージ `{e['stage'].name}` の必須入力がありません:\n"
            + "\n".join(lines))


def execute(entries, dirs, cfg, shared: dict, label: str,
            repo_root: str | None = None) -> int:
    """計画を実行し、台帳を書いて終了コードを返す(0=成功/完結、2=入力不足)。"""
    t_start = time.time()
    records = []
    rc = 0
    for e in entries:
        st: Stage = e["stage"]
        rec = {"name": st.name, "title": st.title, "group": st.group,
               "status": e["status"], "params": e["params"],
               "outputs": e["outputs"]}
        if e["status"] == "missing-input":
            print(_missing_message(e, dirs), file=sys.stderr, flush=True)
            rec["status"] = "missing-input"
            rec["missing"] = e["missing"]
            records.append(rec)
            rc = 2
            break
        if e["status"] == "skip":
            print(f"[wu] {st.name}: スキップ(生成物あり。作り直しは --force)",
                  flush=True)
            records.append(rec)
            continue
        print(f"[wu] {st.name}: 実行 — {st.title}", flush=True)
        ctx = Ctx(dirs=dirs, params=e["params"], shared=e["shared_used"],
                  force=False)
        t0 = time.time()
        try:
            st.run(ctx)
        except Exception as exc:
            rec["status"] = "failed"
            rec["secs"] = round(time.time() - t0, 1)
            rec["error"] = f"{type(exc).__name__}: {exc}"
            records.append(rec)
            rc = 1
            print(f"[wu] {st.name}: 失敗({rec['error']})", file=sys.stderr,
                  flush=True)
            break
        rec["status"] = "ok"
        rec["secs"] = round(time.time() - t0, 1)
        records.append(rec)
        print(f"[wu] {st.name}: 完了({rec['secs']}s)", flush=True)

    _write_manifest(dirs, label, shared, records, t_start, repo_root)
    return rc


def _write_manifest(dirs, label: str, shared: dict, records: list,
                    t_start: float, repo_root: str | None) -> None:
    """実行台帳を data/manifests/ へ書く(読み取り専用監査の対象データ)。"""
    try:
        mdir = dirs.ensure(dirs.manifests)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:40]
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(str(mdir), f"{ts}_{safe or 'run'}.json")
        doc = {
            "generated_at": _now(),
            "label": label,
            "secs_total": round(time.time() - t_start, 1),
            "shared": {k: v for k, v in shared.items()},
            "stages": records,
            "versions": versions(),
        }
        if repo_root:
            doc["git"] = git_stamp(repo_root)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print(f"[wu] manifest -> {path}", flush=True)
    except Exception as exc:  # 台帳の失敗で本体の成功を帳消しにしない
        print(f"[wu][warn] manifest 記録に失敗: {exc}", flush=True)


def format_plan(entries) -> str:
    """plan の人間可読表示(dry-run / plan サブコマンド)。"""
    lines = ["[wu] 実行計画(登録順):"]
    for e in entries:
        st = e["stage"]
        mark = {"run": "実行", "skip": "スキップ(生成物あり)",
                "missing-input": "入力不足"}[e["status"]]
        lines.append(f"  {st.name:<16} [{st.group}] {mark} — {st.title}")
        if e["status"] == "missing-input":
            for key in e["missing"]:
                lines.append(f"      入力なし: {key}")
        diff = {k: v for k, v in e["params"].items()
                if v != next((p.default for p in st.params if p.name == k), None)}
        if diff:
            lines.append(f"      params(既定外): {diff}")
    return "\n".join(lines)
