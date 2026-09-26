"""E3: Body-text link extraction from pages-articles XML dumps.

A "body link" is a [[wikilink]] present in the article's OWN wikitext source.
Links contributed by transcluded templates/navboxes (which inflate the
pagelinks table to avg degree ~94) are NOT part of the source and are
therefore excluded automatically. The result is the "true article graph"
(expected avg degree ~30-50).

Pipeline per <page>:
  skip ns != 0 / redirect pages
  clean text: strip <!--comments-->, <nowiki>, <includeonly>
  extract [[Target|alias]] -> Target, drop #anchor, spaces->_, collapse _+,
    ucfirst ASCII first letter, drop known namespace/interwiki prefixes
  blake2b64 hash -> searchsorted join against parsed/ns0_all_hashes.npz
    (miss = red link), redirect resolution via rd_map, drop self loops,
    dedupe per article
  append int32 (src_page_id, dst_page_id) pairs -> graph/edges_body_directed.bin

Checkpoint/resume: records processed <page> count; on resume the stream is
fast-forwarded (skipped pages are still decompressed, not processed).
"""
from __future__ import annotations

import bz2
import os
import re
import time
import xml.etree.ElementTree as ET

import numpy as np

from .dumpio import read_json, write_json
from .sqlparse import h64

# ---- namespace prefix skip list (built programmatically; correctness comes
# ---- from the hash join anyway - unknown prefixed targets simply miss and
# ---- are counted as red links)
_NS_NAMES = [
    "Special", "特別", "Talk", "ノート", "User", "利用者", "Wikipedia",
    "File", "Image", "ファイル", "MediaWiki", "メディアウィキ",
    "Template", "テンプレート", "Help", "ヘルプ", "Category", "カテゴリ",
    "Portal", "ポータル", "Draft", "下書き", "Module", "モジュール",
    "Book", "TimedText", "Gadget", "Gadget definition", "Media", "メディア",
]
_TALK_SUFFIXES = ["‐トーク", "-トーク", "_トーク", "‐Talk", "-Talk", "_Talk", " talk"]
_INTERWIKI = ["en", "de", "fr", "es", "it", "pt", "ru", "zh", "ko", "simple",
              "commons", "wiktionary", "wikiquote", "ja"]
NS_PREFIXES: set[bytes] = set()
for _nm in _NS_NAMES:
    _b = _nm.encode("utf-8")
    NS_PREFIXES.add(_b)
    for _sfx in _TALK_SUFFIXES:
        NS_PREFIXES.add(_b + _sfx.encode("utf-8"))
for _iw in _INTERWIKI:
    NS_PREFIXES.add(_iw.encode("ascii"))

COMMENT_RE = re.compile(rb"<!--.*?-->", re.S)
REF_RE = re.compile(rb"<ref\b[^>]*/>|<ref\b[^>]*>.*?</ref>", re.S)
NOWIKI_RE = re.compile(rb"<nowiki\s*/>|<nowiki\b[^>]*>.*?</nowiki>", re.S)
INCLUDEONLY_RE = re.compile(rb"<includeonly\b[^>]*>.*?</includeonly>", re.S)
LINK_RE = re.compile(rb"\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]")
UNDERSCORES_RE = re.compile(rb"_+")


class CountingReader:
    """Counts decompressed bytes read (progress reporting)."""

    def __init__(self, f):
        self.f = f
        self.n = 0

    def read(self, size=-1):
        b = self.f.read(size)
        self.n += len(b)
        return b


def normalize_target(t: bytes) -> bytes | None:
    t = t.split(b"#", 1)[0]          # drop #anchor
    t = t.strip()
    if not t:
        return None
    while t.startswith(b":"):        # forced links :Category:...
        t = t[1:]
    t = t.replace(b" ", b"_")
    t = UNDERSCORES_RE.sub(b"_", t)
    t = t.strip(b"_")
    if not t:
        return None
    c0 = t[:1]
    if c0.isascii() and c0.islower():  # MediaWiki ucfirst
        t = c0.upper() + t[1:]
    return t


NS_PREFIXES_LOWER = {p.lower() for p in NS_PREFIXES}


def _prefix_skipped(t: bytes) -> bool:
    if b":" not in t:
        return False
    p = t.split(b":", 1)[0]
    if p in NS_PREFIXES:
        return True
    pl = p.lower()  # normalize_target may have uppercased the first letter (en: -> En:)
    return pl.isascii() and pl in NS_PREFIXES_LOWER


def build_body_edges(xml_bz2_path: str, parsed_dir: str, out_bin: str,
                     ckpt_path: str, limit_pages: int = 0,
                     flush_every: int = 20_000, log_every: float = 30.0,
                     strip_refs: bool = False):
    t0 = time.time()
    z = np.load(os.path.join(parsed_dir, "ns0_all_hashes.npz"))
    all_h, all_pid = z["hashes"], z["page_ids"]
    rd = np.load(os.path.join(parsed_dir, "rd_map.npz"))
    rd_from, rd_final = rd["rd_from"], rd["rd_final"]

    counters = {
        "pages_seen": 0, "pages_processed": 0, "ns_nonzero_skipped": 0,
        "redirect_pages_skipped": 0, "articles": 0, "links_found": 0,
        "links_prefixed_skipped": 0, "links_redlink": 0,
        "links_dangling_redirect": 0, "links_self": 0,
        "links_dup_within_article": 0, "edges_kept": 0,
    }
    ck = read_json(ckpt_path, None)
    skip_pages = 0
    if ck and ck.get("state") not in (None, "done"):
        skip_pages = int(ck.get("pages_processed", 0))
        counters.update(ck.get("counters", {}))
        print(f"[body] resume: fast-forwarding past first {skip_pages:,} pages")
    elif ck and ck.get("state") == "done":
        print("[body] already done; delete checkpoint to redo")
        counters.update(ck.get("counters", {}))
        return counters

    os.makedirs(os.path.dirname(out_bin), exist_ok=True)
    fout = open(out_bin, "r+b" if (skip_pages and os.path.exists(out_bin)) else "wb")
    if skip_pages:
        fout.seek(counters["edges_kept"] * 8)
        fout.truncate()

    CAP = 1 << 20
    buf_src = np.empty(CAP, dtype=np.int64)
    buf_dst = np.empty(CAP, dtype=np.int64)
    buf_n = 0
    last_log = t0
    stop = False

    def flush():
        nonlocal buf_n
        if buf_n:
            out = np.empty(buf_n * 2, dtype=np.int32)
            out[0::2] = buf_src[:buf_n].astype(np.int32)
            out[1::2] = buf_dst[:buf_n].astype(np.int32)
            out.tofile(fout)
            buf_n = 0

    reader = CountingReader(bz2.open(xml_bz2_path, "rb"))
    it = ET.iterparse(reader, events=("start", "end"))
    _, root = next(it)  # <mediawiki> (may carry a default xmlns)
    rt = root.tag
    ns_uri = rt[1 : rt.index("}")] if rt.startswith("{") else ""
    P = f"{{{ns_uri}}}" if ns_uri else ""
    PAGE, NS_T, ID_T = P + "page", P + "ns", P + "id"
    REV_TEXT = f"{P}revision/{P}text"

    for ev, elem in it:
        if ev != "end" or elem.tag != PAGE:
            continue
        counters["pages_seen"] += 1
        page_no = counters["pages_seen"]
        try:
            if page_no <= skip_pages:
                continue
            if limit_pages and counters["pages_processed"] >= limit_pages:
                stop = True
                break
            ns_txt = elem.findtext(NS_T)
            if ns_txt is None or ns_txt.strip() != "0":
                counters["ns_nonzero_skipped"] += 1
                continue
            if elem.find(P + "redirect") is not None:
                counters["redirect_pages_skipped"] += 1
                continue
            pid = int(elem.findtext(ID_T))
            text = elem.findtext(REV_TEXT)
            counters["pages_processed"] += 1
            counters["articles"] += 1
            if not text:
                continue
            tb = text.encode("utf-8")
            tb = INCLUDEONLY_RE.sub(b"", COMMENT_RE.sub(b"", NOWIKI_RE.sub(b"", tb)))
            if strip_refs:
                tb = REF_RE.sub(b"", tb)
            targets = LINK_RE.findall(tb)
            if not targets:
                continue
            counters["links_found"] += len(targets)
            hashes = []
            for raw in targets:
                t = normalize_target(raw)
                if t is None:
                    continue
                if _prefix_skipped(t):
                    counters["links_prefixed_skipped"] += 1
                    continue
                hashes.append(h64(t))
            if not hashes:
                continue
            hv = np.fromiter(hashes, dtype=np.uint64, count=len(hashes))
            pos = np.searchsorted(all_h, hv)
            pc = np.clip(pos, 0, len(all_h) - 1)
            found = all_h[pc] == hv
            counters["links_redlink"] += int((~found).sum())
            dst = all_pid[pc[found]]
            if len(rd_from) and len(dst):
                p2 = np.searchsorted(rd_from, dst)
                p2c = np.clip(p2, 0, len(rd_from) - 1)
                hit = rd_from[p2c] == dst
                if hit.any():
                    fin = rd_final[p2c]
                    dst = np.where(hit, fin, dst)
                    bad = hit & (fin == 0)
                    counters["links_dangling_redirect"] += int(bad.sum())
                    dst = dst[~bad]
            sl = dst == pid
            counters["links_self"] += int(sl.sum())
            dst = dst[~sl]
            if len(dst):
                udst = np.unique(dst)
                counters["links_dup_within_article"] += int(len(dst) - len(udst))
                k = len(udst)
                if buf_n + k > CAP:
                    flush()
                buf_src[buf_n : buf_n + k] = pid
                buf_dst[buf_n : buf_n + k] = udst
                buf_n += k
                counters["edges_kept"] += k
        finally:
            elem.clear()
            root.clear()

        if counters["pages_seen"] % flush_every == 0:
            flush()
            now = time.time()
            if now - last_log > log_every:
                fout.flush()
                write_json(ckpt_path, {"pages_processed": counters["pages_processed"],
                                       "counters": counters, "state": "running"})
                rate = counters["pages_processed"] / max(now - t0, 1e-9)
                print(f"  body: pages={counters['pages_processed']:,} "
                      f"edges={counters['edges_kept']:,} ({rate:.0f} pages/s, "
                      f"{reader.n/1e9:.2f}GB decomp, {now-t0:.0f}s)", flush=True)
                last_log = now
        if stop:
            break

    flush()
    fout.flush()
    fout.close()
    state = "limit_reached" if stop else "done"
    counters["pages_processed"] = counters["pages_processed"]
    write_json(ckpt_path, {"pages_processed": counters["pages_processed"],
                           "counters": counters, "state": state,
                           "secs": round(time.time() - t0, 1),
                           "decomp_bytes": reader.n,
                           "out_bytes": os.path.getsize(out_bin),
                           "source": os.path.basename(xml_bz2_path),
                           "strip_refs": strip_refs})
    avg = counters["edges_kept"] / max(1, counters["articles"])
    print(f"[body] {state}: articles={counters['articles']:,} "
          f"edges={counters['edges_kept']:,} avg_body_outdeg={avg:.1f} "
          f"redlinks={counters['links_redlink']:,} "
          f"prefixed_skipped={counters['links_prefixed_skipped']:,} "
          f"({time.time()-t0:.0f}s)")
    return counters
