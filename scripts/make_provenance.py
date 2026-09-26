import hashlib
import json
import os

base = os.environ.get("ARENA_WORKSPACE", "/home/user") + "/.cache/wudata/dump"
out = {}
for f in sorted(os.listdir(base)):
    p = os.path.join(base, f)
    h = hashlib.sha256()
    n = 0
    with open(p, "rb") as fh:
        while True:
            b = fh.read(1 << 22)
            if not b:
                break
            h.update(b)
            n += len(b)
    out[f] = {"bytes": n, "sha256": h.hexdigest()}
    print(f"{f}: {n:,} bytes sha256={h.hexdigest()}", flush=True)

dest = os.environ.get("ARENA_WORKSPACE", "/home/user") + "/wikiuniverse/PROVENANCE.json"
json.dump({"source": "https://dumps.wikimedia.org/jawiki/latest/",
           "dump_date": "2026-09-02",
           "downloaded_at": "2026-09-25T13:08-13:15+09:00 (sandbox)",
           "files": out}, open(dest, "w", encoding="utf-8"), indent=2)
print("wrote", dest)
