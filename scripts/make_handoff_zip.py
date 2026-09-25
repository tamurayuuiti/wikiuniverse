import os
import zipfile

WS = os.environ.get("ARENA_WORKSPACE", "/home/user")
root = os.path.join(WS, "wikiuniverse")
out = os.path.join(WS, "wikiuniverse_handoff.zip")
EXCL_DIRS = {"__pycache__", "synth_data", ".pytest_cache"}
n = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCL_DIRS]
        for f in sorted(filenames):
            if f.endswith(".pyc"):
                continue
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, os.path.dirname(root))
            z.write(p, rel)
            n += 1
print(f"zipped {n} files -> {out} ({os.path.getsize(out)/1e6:.1f} MB)")
with zipfile.ZipFile(out) as z:
    bad = z.testzip()
    print("integrity:", "OK" if bad is None else f"BAD: {bad}")
    names = z.namelist()
    print("entries:", len(names))
    for k in ("wikiuniverse/README.md", "wikiuniverse/LOCAL_SETUP.md", "wikiuniverse/PROVENANCE.json",
              "wikiuniverse/results/SUMMARY.md", "wikiuniverse/notebooks/poc_colab.ipynb",
              "wikiuniverse/wu/cli.py", "wikiuniverse/tests/test_synthetic.py"):
        print(" ", k, "OK" if k in names else "MISSING")
