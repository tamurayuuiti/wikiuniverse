# wu/stages/ingest.py — 取得・前処理ステージ(download / parse / edges /
#                        categories / body_edges)
#
# 責務:
# - Wikipedia ダンプの取得から「解析済み記事表 + 解決済みエッジ」までの前段を
#   ステージとして登録する(実体は wu.dumpio / sqlparse / buildedges / catparse /
#   xmlparse 内の既存関数へ委譲 = 挙動は旧 `python -m wu.cli <cmd>` と同一)。
#
# 注意:
# - download 以外のステージはネットワークを必要としない。
# - body_edges の XML ダンプ(4.7GB)は契約 inputs に含めない(source/xml パラメータ
#   で解決されるため)。無い場合は実行時に案内付きの FileNotFoundError で fail-fast
#   する(SystemExit を上げない = ランナーが failed として台帳に記録できる)。
# - parse の出力は 6 成果物(articles/article_ids/article_hashes/ns0_hashes/
#   rd_map/meta)。skip 判定は宣言 outputs の存在で行うため、途中失敗後は
#   --force で再実行する(チェックポイント resume は build_edges 側が担う)。

from __future__ import annotations

import datetime
import os

from ..dumpio import FILES, download_all, read_json, write_json
from ..pipeline.stage import Param, stage


@stage(
    name="download",
    title="Wikipedia ダンプの取得(resume/並列対応)",
    group="canonical",
    params=(
        Param("files", str, "page,redirect,linktarget,pagelinks",
              "取得するダンプのキー(カンマ区切り。dumpio.FILES 参照)"),
        Param("workers", int, 3, "並列ダウンロード数"),
    ),
    inputs=(),
    outputs=("dump.page", "dump.redirect", "dump.linktarget", "dump.pagelinks"),
)
def run_download(ctx) -> None:
    ctx.dirs.ensure(ctx.dirs.dump)
    download_all(ctx.params["files"].split(","), ctx.dirs.dump,
                 workers=ctx.params["workers"])


@stage(
    name="parse",
    title="page/linktarget/redirect のストリーミング解析 → parsed/ 一式",
    group="canonical",
    params=(
        Param("dump_date", str, None,
              "ダンプ日付(meta.json に記録。省略時は既存値/unknown)"),
    ),
    inputs=("dump.page", "dump.redirect", "dump.linktarget"),
    outputs=("parsed.articles", "parsed.article_ids", "parsed.article_hashes",
             "parsed.ns0_hashes", "parsed.rd_map", "parsed.meta"),
)
def run_parse(ctx) -> None:
    # 旧 wu.cli cmd_parse と同一の手順(チャンク逐次解析・チェックポイント無し)。
    from ..sqlparse import (build_linktarget_artifacts, build_page_artifacts,
                            build_redirect_artifacts)
    dirs = ctx.dirs
    dirs.ensure(dirs.parsed)
    build_page_artifacts(dirs.dump_file(FILES["page"]), dirs.parsed, dirs.meta)
    build_linktarget_artifacts(dirs.dump_file(FILES["linktarget"]), dirs.parsed,
                               dirs.meta)
    build_redirect_artifacts(dirs.dump_file(FILES["redirect"]), dirs.parsed,
                             dirs.meta)
    meta = read_json(dirs.meta, {}) or {}
    meta["dump_date"] = (ctx.params.get("dump_date") or meta.get("dump_date")
                         or "unknown")
    meta["parsed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    meta.setdefault("files", {})
    for k in ("page", "redirect", "linktarget", "pagelinks"):
        f = dirs.dump_file(FILES[k])
        if os.path.exists(f):
            meta["files"][k] = {"bytes": os.path.getsize(f), "name": FILES[k]}
    meta.setdefault("layout", {
        "policy": "2026-09-25",
        "dirs": {"dump": dirs.dump, "parsed": dirs.parsed,
                 "graph": dirs.graph, "community": dirs.community}})
    write_json(dirs.meta, meta)


@stage(
    name="edges",
    title="pagelinks 全行のストリーム解析 → 解決済み ns0 エッジ(チェックポイント付き)",
    group="canonical",
    params=(
        Param("chunk_bytes", int, 1 << 24, "解析チャンクサイズ(バイト)"),
    ),
    inputs=("dump.pagelinks", "parsed.ns0_hashes"),
    outputs=("graph.edges_ns0",),
)
def run_edges(ctx) -> None:
    from ..buildedges import build_edges
    dirs = ctx.dirs
    dirs.ensure(dirs.graph)
    build_edges(dirs.dump_file(FILES["pagelinks"]), dirs.parsed,
                dirs.edges_bin, dirs.edges_ckpt,
                chunk_bytes=ctx.params["chunk_bytes"])


@stage(
    name="categories",
    title="categorylinks → 記事×カテゴリ対(銀河の命名・純度計算の材料)",
    group="canonical",
    params=(),
    inputs=("dump.categorylinks", "dump.linktarget", "parsed.ns0_hashes"),
    outputs=("graph.categories", "graph.article_categories"),
)
def run_categories(ctx) -> None:
    from ..catparse import build_category_artifacts
    dirs = ctx.dirs
    dirs.ensure(dirs.graph)
    build_category_artifacts(dirs.dump_file(FILES["categorylinks"]),
                             dirs.dump_file(FILES["linktarget"]),
                             dirs.parsed, dirs.graph, meta_path=dirs.meta)


@stage(
    name="body_edges",
    title="pages-articles XML → 本文 [[リンク]] の解決済みエッジ(テンプレート除去グラフ)",
    group="optional",
    params=(
        Param("source", str, "pages-articles",
              "XML ダンプのキー(pages-articles|pages-articles1)",
              choices=("pages-articles", "pages-articles1")),
        Param("xml", str, None, "XML ファイルの明示パス(source より優先)"),
        Param("out_name", str, "edges_body_directed.bin", "出力ファイル名"),
        Param("limit_pages", int, 0, "処理ページ数の上限(0=全て。スモーク用)"),
        Param("strip_refs", bool, False, "引用(<ref>)内のリンクも除去する"),
    ),
    inputs=("parsed.ns0_hashes",),
    outputs=("graph.edges_body",),
)
def run_body_edges(ctx) -> None:
    from ..xmlparse import build_body_edges
    dirs = ctx.dirs
    dirs.ensure(dirs.graph)
    xml = ctx.params.get("xml") or str(dirs.dump_file(
        FILES[ctx.params["source"]]))
    if not os.path.exists(xml):
        # SystemExit ではなく例外にする(ランナーが failed として記録・案内できる)
        raise FileNotFoundError(
            f"XML ダンプがありません: {xml}\n"
            f"  取得: python -m wu run download --set "
            f"download.files={ctx.params['source']}")
    build_body_edges(xml, dirs.parsed,
                     os.path.join(str(dirs.graph), ctx.params["out_name"]),
                     os.path.join(str(dirs.graph), "body_checkpoint.json"),
                     limit_pages=ctx.params["limit_pages"],
                     strip_refs=ctx.params["strip_refs"])
