"""パイプライン基盤(成果物レジストリ/ステージ/設定/ランナー)の単体テスト。

チェーン: ダミーステージの登録 → plan(実行/スキップ/入力不足)→ execute →
manifest 検証 → 設定のマージ優先度と --set → CLI(stages/plan/run)のスモーク。
合成データ不要・ネットワーク不要(数秒)。既存ステージ(stats)の契約も検査する。

注意: ダミーステージ/成果物は本プロセスのレジストリに追加される(テスト専用の
名前 test_* を使い、canonical グループへは入れない = "all" の意味を汚さない)。
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wu.paths import Dirs  # noqa: E402
from wu.pipeline import artifacts, config as pcfg, runner  # noqa: E402
from wu.pipeline.stage import Param, stage  # noqa: E402


def main():
    tmp = tempfile.mkdtemp(prefix="wu_pipe_test_")
    dirs = Dirs(tmp)

    # ---- 1) 実ステージ(stats)の登録と契約
    import wu.stages  # noqa: F401  (登録トリガ)
    from wu.pipeline.stage import REGISTRY, get
    st = get("stats")
    assert st.group == "canonical"
    assert st.inputs == ("graph.edges_ns0", "parsed.article_ids")
    assert "graph.full_stats" in st.outputs
    p = artifacts.resolve("graph.full_stats", dirs, {})
    assert str(p).endswith(os.path.join("graph", "full_stats.json")), p
    # tag templating の解決(community.membership は tag 必須)
    mp = artifacts.resolve("community.membership", dirs, {"tag": "res9"})
    assert mp.name == "membership_res9.npy", mp
    try:
        artifacts.resolve("community.membership", dirs, {})
        raise AssertionError("tag 無しは KeyError であるべき")
    except KeyError:
        pass
    # 二重登録の検出
    try:
        artifacts.define("graph.indeg", "dup", "intermediate", lambda d, q: d.indeg)
        raise AssertionError("成果物の二重登録は ValueError であるべき")
    except ValueError:
        pass

    # ---- 2) 設定のマージ優先度: 既定 < defaults < stages < --set
    cfg = {"defaults": {"titles_needed": 100},
           "stages": {"stats": {"titles_needed": 50}}}
    assert pcfg.stage_params(st, cfg)["titles_needed"] == 50
    cfg2 = pcfg.apply_overrides(cfg, ["stats.titles_needed=7"])
    assert pcfg.stage_params(st, cfg2)["titles_needed"] == 7
    assert pcfg.stage_params(st, pcfg.EMPTY)["titles_needed"] == 200  # Param 既定
    # 未宣言キーはエラー(タイポの黙殺防止)
    try:
        pcfg.stage_params(st, {"stages": {"stats": {"no_such_key": 1}}})
        raise AssertionError("未宣言キーは KeyError であるべき")
    except KeyError:
        pass
    # --set の JSON リテラル解釈と形式エラー
    assert pcfg.parse_set_items(["s.k=5"])[0][2] == 5
    assert pcfg.parse_set_items(['s.k=0.6'])[0][2] == 0.6
    assert pcfg.parse_set_items(['s.k=ml'])[0][2] == "ml"
    try:
        pcfg.parse_set_items(["badform"])
        raise AssertionError("形式違反は ValueError であるべき")
    except ValueError:
        pass
    # shared: 設定 < CLI 明示、run 未指定 = ACTIVE_LAYOUT_RUN
    from wu.paths import ACTIVE_LAYOUT_RUN
    sh = pcfg.shared_params({"shared": {"galaxy_tag": "gt1"}},
                            {"run": None, "galaxy_tag": "cli_gt",
                             "macro_tag": None})
    assert sh["galaxy_tag"] == "cli_gt" and sh["run"] == ACTIVE_LAYOUT_RUN
    assert sh["tag"] == "cli_gt"  # community 系キー用の別名
    sh2 = pcfg.shared_params({"shared": {"galaxy_tag": "gt1"}}, None)
    assert sh2["galaxy_tag"] == "gt1"

    # ACTIVE_LAYOUT_RUN の真実源 = configs/publish.json(コード定数から移行)
    pub = json.load(open(os.path.join(ROOT, "configs", "publish.json"),
                         encoding="utf-8"))
    from wu.paths import ACTIVE_LAYOUT_RUN as ALR
    assert pub["active_layout_run"] == ALR, (pub, ALR)

    # ---- 3) ダミーステージで plan/execute/skip/force/manifest を検証
    artifacts.define("test.a_out", "テスト成果物A", "intermediate",
                     lambda d, q: d.base / "test_a.txt")
    artifacts.define("test.b_out", "テスト成果物B", "intermediate",
                     lambda d, q: d.base / f"test_b_{q['suffix']}.txt")

    calls = []

    @stage(name="test_a", title="テストA(入力なし)", group="experiment",
           params=(Param("n", int, 3, "書き込む行数"),),
           inputs=(), outputs=("test.a_out",))
    def _ta(ctx):
        calls.append("a")
        with open(ctx.path("test.a_out"), "w", encoding="utf-8") as f:
            f.write("a\n" * ctx.params["n"])

    @stage(name="test_b", title="テストB(A を消費)", group="experiment",
           params=(Param("suffix", str, "x", "出力名の接尾辞"),),
           inputs=("test.a_out",), outputs=("test.b_out",))
    def _tb(ctx):
        calls.append("b")
        with open(ctx.path("test.b_out"), "w", encoding="utf-8") as f:
            f.write("b\n")

    shared = {"run": "r_test", "galaxy_tag": "g", "macro_tag": "m", "tag": "g"}
    # 入力不足 → missing-input(fail-fast 案内に生成元ステージ名が出る)
    ent = runner.plan([get("test_b")], dirs, pcfg.EMPTY, shared)
    assert ent[0]["status"] == "missing-input"
    msg = runner._missing_message(ent[0], dirs)
    assert "test_a" in msg and "test.a_out" in msg, msg
    rc = runner.execute(ent, dirs, pcfg.EMPTY, shared, "t-missing")
    assert rc == 2, rc
    # A 実行 → run、再 plan → skip、force → run
    ent = runner.plan([get("test_a")], dirs, pcfg.EMPTY, shared)
    assert ent[0]["status"] == "run"
    rc = runner.execute(ent, dirs, pcfg.EMPTY, shared, "t-a")
    assert rc == 0 and calls == ["a"] and os.path.exists(
        os.path.join(tmp, "test_a.txt"))
    ent = runner.plan([get("test_a")], dirs, pcfg.EMPTY, shared)
    assert ent[0]["status"] == "skip"
    rc = runner.execute(ent, dirs, pcfg.EMPTY, shared, "t-a2")
    assert rc == 0 and calls == ["a"]  # 再実行されていない
    ent = runner.plan([get("test_a")], dirs, pcfg.EMPTY, shared, force=True)
    assert ent[0]["status"] == "run"  # force plan(実行はしない)
    # A→B の順次実行(A は生成物あり = skip、B は初回 = run)。
    # 注意: plan がパラメータを解決してエントリへ束縛するため、plan と execute は
    # 必ず同一 cfg で行う(CLI は常にそうなっている)。B の出力名は suffix=fin で解決。
    cfg_b = pcfg.apply_overrides(pcfg.EMPTY, ["test_b.suffix=fin"])
    ent = runner.plan([get("test_a"), get("test_b")], dirs, cfg_b, shared)
    assert [e["status"] for e in ent] == ["skip", "run"]
    assert ent[1]["params"]["suffix"] == "fin"
    rc = runner.execute(ent, dirs, cfg_b, shared, "t-ab")
    assert rc == 0 and calls[-2:] == ["a", "b"]
    assert os.path.exists(os.path.join(tmp, "test_b_fin.txt"))
    # manifest の検証(最後の t-ab)
    mdir = os.path.join(tmp, "manifests")
    mans = sorted(f for f in os.listdir(mdir) if f.endswith("_t-ab.json"))
    assert len(mans) == 1, os.listdir(mdir)
    man = json.load(open(os.path.join(mdir, mans[0]), encoding="utf-8"))
    assert man["label"] == "t-ab"
    assert [r["name"] for r in man["stages"]] == ["test_a", "test_b"]
    assert [r["status"] for r in man["stages"]] == ["skip", "ok"]
    assert man["stages"][1]["secs"] >= 0
    assert man["stages"][1]["params"]["suffix"] == "fin"
    assert "python" in man["versions"]
    # expand_targets: "all" = canonical のみ(ダミー experiment を含まない)
    names = [s.name for s in runner.expand_targets(["all"])]
    assert "stats" in names and "test_a" not in names
    try:
        runner.expand_targets(["no_such_stage"])
        raise AssertionError("未登録ターゲットは KeyError であるべき")
    except KeyError:
        pass

    # ---- 4) CLI スモーク(subprocess): stages / plan / fail-fast run
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "wu.cli", "stages"],
                       capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0 and "stats" in r.stdout, r.stderr[-500:]
    r = subprocess.run([sys.executable, "-m", "wu.cli", "--base", tmp,
                        "plan", "stats"], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    assert r.returncode == 0 and "入力不足" in r.stdout, r.stdout[-500:]
    r = subprocess.run([sys.executable, "-m", "wu.cli", "--base", tmp,
                        "run", "stats"], capture_output=True, text=True,
                       cwd=ROOT, env=env)
    assert r.returncode == 2, r.returncode  # edges_ns0 無し → fail-fast
    assert "graph.edges_ns0" in (r.stdout + r.stderr)
    # 生成元ステージ登録済み(ingest 移行後)→ 旧来コマンドでなくステージへ案内
    assert "wu run edges" in (r.stdout + r.stderr), (r.stdout + r.stderr)[-500:]
    # CLI 面 = 研究/実験コマンド + パイプライン(stages/plan/run)。
    # 取得・解析の旧サブコマンドは廃止済み(run <stage> へ一本化)。
    r = subprocess.run([sys.executable, "-m", "wu.cli", "--help"],
                       capture_output=True, text=True, cwd=ROOT, env=env)
    assert r.returncode == 0 and "run" in r.stdout and "stages" in r.stdout
    assert "download" not in r.stdout, r.stdout

    # ---- モジュール CLI とステージ宣言の既定値一致(ドリフト防止):
    #      layout_local の腕パラメータはモジュール CLI(バッチ/job-spec 起動)と
    #      ステージ(並列ランチャ経由)の二入口があるため、既定値を照合する。
    import wu.layout.layout_local as ll
    _ns = ll._build_parser().parse_args([])
    _pdefs = {q.name: q.default for q in get("layout_local").params}
    for _key in ("fr_mode", "ml_threshold", "ml_niter", "anchor_mode",
                 "fr_norm", "sector_ratio"):
        assert getattr(_ns, _key) == _pdefs[_key], \
            (_key, getattr(_ns, _key), _pdefs[_key])

    print("\n*** PIPELINE CORE TEST PASSED ***")
    print(f"stages={len(REGISTRY)} manifests={len(os.listdir(mdir))}")


if __name__ == "__main__":
    main()
