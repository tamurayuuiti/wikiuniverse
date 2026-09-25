"""Download + streaming decompression helpers for Wikimedia SQL dumps."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
import urllib.request

DEFAULT_UA = "WikiGraphResearch/0.1 (https://example.org; research@example.org) python-urllib"
DUMP_BASE = "https://dumps.wikimedia.org/jawiki/latest/"

FILES = {
    "page": "jawiki-latest-page.sql.gz",
    "redirect": "jawiki-latest-redirect.sql.gz",
    "linktarget": "jawiki-latest-linktarget.sql.gz",
    "pagelinks": "jawiki-latest-pagelinks.sql.gz",
    "categorylinks": "jawiki-latest-categorylinks.sql.gz",
}


def download_file(url: str, dest: str, ua: str = DEFAULT_UA, log=print) -> str:
    """Resumable single-file download."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    have = os.path.getsize(dest) if os.path.exists(dest) else 0
    headers = {"User-Agent": ua}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status == 416:  # already complete
            log(f"[skip] {os.path.basename(dest)} already complete")
            return dest
        mode = "ab" if have and r.status == 206 else "wb"
        if mode == "wb":
            have = 0
        total = r.headers.get("Content-Length")
        total = int(total) + have if total else None
        n = have
        last_log = t0
        with open(dest, "ab" if mode == "ab" else "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                n += len(chunk)
                now = time.time()
                if now - last_log > 20:
                    speed = (n - have) / (now - t0) / 1e6
                    pct = f"{n / total * 100:.1f}%" if total else "?"
                    log(f"  {os.path.basename(dest)}: {n/1e6:.0f}MB {pct} {speed:.1f}MB/s")
                    last_log = now
    log(f"[done] {os.path.basename(dest)} {n/1e6:.1f}MB in {time.time()-t0:.0f}s")
    return dest


def download_all(names, dump_dir: str, workers: int = 3, log=print):
    """Download several dump files in parallel threads (per-connection speed is limited)."""
    os.makedirs(dump_dir, exist_ok=True)
    errs = []

    def _one(key):
        try:
            download_file(DUMP_BASE + FILES[key], os.path.join(dump_dir, FILES[key]), log=log)
        except Exception as e:  # retry once from scratch on failure
            log(f"[retry] {key}: {e}")
            try:
                download_file(DUMP_BASE + FILES[key], os.path.join(dump_dir, FILES[key]), log=log)
            except Exception as e2:
                errs.append((key, e2))

    keys = list(names)
    threads = []
    sem = threading.Semaphore(workers)

    def _wrap(k):
        with sem:
            _one(k)

    for k in keys:
        t = threading.Thread(target=_wrap, args=(k,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    if errs:
        raise RuntimeError(f"download failures: {errs}")


def open_gz_counting(path: str):
    """Yield (decompressed_bytes_read_so_far_callable, fileobj)."""
    f = gzip.open(path, "rb")
    return f


def stream_decompressed(path: str, chunk_bytes: int = 1 << 26, skip_to: int = 0):
    """Yield (offset_after_chunk, chunk) of decompressed bytes.

    If skip_to > 0, fast-forwards (decompress & discard) to that decompressed
    offset before yielding. Used for checkpointed resume.
    """
    off = 0
    with gzip.open(path, "rb") as f:
        if skip_to:
            while off < skip_to:
                want = min(chunk_bytes, skip_to - off)
                data = f.read(want)
                if not data:
                    raise RuntimeError(f"cannot skip to {skip_to}; file ends at {off}")
                off += len(data)
        carry = b""
        while True:
            data = f.read(chunk_bytes)
            if not data:
                if carry:
                    off += len(carry)
                    yield off, carry
                break
            out = carry + data
            off += len(out)
            carry = b""
            yield off, out


def sha256_of(path: str, limit: int | None = None) -> str:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h.update(b)
            n += len(b)
            if limit and n >= limit:
                break
    return h.hexdigest()[:16]


def read_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
