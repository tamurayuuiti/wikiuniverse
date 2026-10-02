"""前段ステージ(parse/edges/stats/categories)のランナー経由契約テスト。

チェーン: 合成ダンプ fixture(test_synthetic.build_fixtures)→ runner.plan/execute
→ 成果物の実体・内容・resume(skip)・manifest・パラメータ流通・fail-fast 案内を検証。
ネットワーク不要(download ステージは実行せず、skip 判定のみ確認)。数秒。
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

from test_synthetic import BASE, build_fixtures  # noqa: E402

import wu.stages  # noqa: E402,F401  (ステージ登録)
from wu.paths import Dirs  # noqa: E402
from wu.pipeline import artifacts, config as pcfg, runner  # noqa: E402
from wu.pipeline.stage import get  # noqa: E402


def main():
    if os.path.exists(BASE):
        shutil.rmtree(BASE)
    build_fixtures()
    dirs = Dirs(BASE)
    shared = {"run": "r_x", "galaxy_tag": "res1_s", "macro_tag": "res1",
              "tag": "res1_s"}

    # ---- 1) 前段チェーンを runner で実行(parse → edges → stats → categories)
    names = ("parse", "edges", "stats", "categories")
    stages = [get(n) for n in names]
    ent = runner.plan(stages, dirs, pcfg.EMPTY, shared)
    assert [e["status"] for e in ent] == ["run"] * 4, \
        [e["status"] for e in ent]
    rc = runner.execute(ent, dirs, pcfg.EMPTY, shared, "ingest")
    assert rc == 0, rc

    # 成果物の実体と内容(fixture は 60 記事 / 64 有向エッジ / catlinks 5 対)
    assert os.path.exists(dirs.articles) and os.path.exists(dirs.article_ids)
    assert len(np.load(dirs.article_ids)) == 60
    assert os.path.getsize(dirs.edges_bin) == 64 * 8
    assert len(np.load(dirs.indeg)) == 60
    st = json.load(open(dirs.full_stats, encoding="utf-8"))
    assert isinstance(st, dict) and st
    cats = os.path.join(str(dirs.graph), "categories.parquet")
    assert os.path.exists(cats)
    assert os.path.exists(os.path.join(str(dirs.graph),
                                       "article_categories.bin"))
    meta = json.load(open(dirs.meta, encoding="utf-8"))
    assert meta.get("dump_date") and "files" in meta

    # ---- 2) resume 意味論: 再 plan は全て skip、manifest も記録済み
    ent2 = runner.plan(stages, dirs, pcfg.EMPTY, shared)
    assert [e["status"] for e in ent2] == ["skip"] * 4
    assert any(f.endswith("_ingest.json") for f in os.listdir(dirs.manifests))
    # download はダンプ fixture が揃っているので skip(= 手動取得済みの尊重)
    ent_d = runner.plan([get("download")], dirs, pcfg.EMPTY, shared)
    assert ent_d[0]["status"] == "skip", ent_d[0]["status"]

    # ---- 3) パラメータ流通: --set 相当で parse.dump_date を上書きして再実行
    cfg = pcfg.apply_overrides(pcfg.EMPTY, ["parse.dump_date=2099-01-01"])
    ent3 = runner.plan([get("parse")], dirs, cfg, shared, force=True)
    assert ent3[0]["params"]["dump_date"] == "2099-01-01"
    assert runner.execute(ent3, dirs, cfg, shared, "parse-date") == 0
    meta = json.load(open(dirs.meta, encoding="utf-8"))
    assert meta["dump_date"] == "2099-01-01", meta["dump_date"]

    # ---- 4) fail-fast 案内: 空 base で edges → 生成元ステージ名を指す
    import tempfile
    tmp2 = tempfile.mkdtemp(prefix="wu_ingest_ff_")
    ent4 = runner.plan([get("edges")], Dirs(tmp2), pcfg.EMPTY, shared)
    assert ent4[0]["status"] == "missing-input"
    msg = runner._missing_message(ent4[0], Dirs(tmp2))
    assert "parse" in msg and "download" in msg, msg
    rc = runner.execute(ent4, Dirs(tmp2), pcfg.EMPTY, shared, "ff")
    assert rc == 2

    # ---- 5) body_edges: XML 不在の fail-fast(例外型)と出力名の templating
    be = get("body_edges")
    assert be.group == "optional"
    p = artifacts.resolve("graph.edges_body", dirs, {"out_name": "eb_test.bin"})
    assert p.name == "eb_test.bin"
    ent5 = runner.plan([be], dirs, pcfg.EMPTY, shared)
    assert ent5[0]["status"] == "run"  # ns0_hashes はある(XML は実行時チェック)
    rc = runner.execute(ent5, dirs, pcfg.EMPTY, shared, "body-fail")
    assert rc == 1  # FileNotFoundError → failed 記録(SystemExit で殺さない)
    mans = [f for f in os.listdir(dirs.manifests) if f.endswith("_body-fail.json")]
    man = json.load(open(os.path.join(str(dirs.manifests), mans[0]),
                         encoding="utf-8"))
    assert man["stages"][0]["status"] == "failed"
    assert "FileNotFoundError" in man["stages"][0]["error"]

    print("\n*** INGEST STAGES TEST PASSED ***")
    print(f"stages={len(runner.expand_targets(['all']))} canonical: "
          f"{[s.name for s in runner.expand_targets(['all'])]}")


if __name__ == "__main__":
    main()
