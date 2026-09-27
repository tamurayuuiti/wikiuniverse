"""wu/paths.py unit test: data layout single source of truth.

Layout run directories are the coordinate pipeline's unit of work, so their
derivation and validation are checked here directly (no fixtures, no I/O).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from wu.paths import ACTIVE_LAYOUT_RUN, Dirs  # noqa: E402


def main():
    d = Dirs("data")

    # ---- stage directories are all derived from base
    assert str(d.base) == "data"
    assert str(d.dump) == os.path.join("data", "dump")
    assert str(d.parsed) == os.path.join("data", "parsed")
    assert str(d.graph) == os.path.join("data", "graph")
    assert str(d.subsets) == os.path.join("data", "graph", "subsets")
    assert str(d.community_full) == os.path.join("data", "community", "full")
    assert str(d.final) == os.path.join("data", "final")
    assert str(d.layout) == os.path.join("data", "layout")
    assert str(d.spatial) == os.path.join("data", "spatial")
    assert str(d.tiles) == os.path.join("data", "spatial", "tiles")

    # ---- absolute base propagates (Colab / external drive usage)
    a = Dirs("/tmp/wudata")
    assert str(a.tiles) == os.path.join("/tmp/wudata", "spatial", "tiles")
    assert str(a.layout_run("x")) == os.path.join("/tmp/wudata", "layout", "x")

    # ---- layout run resolution: explicit name wins, default is the active run
    assert str(d.layout_run("20260926_baseline")) == \
        os.path.join("data", "layout", "20260926_baseline")
    assert d.layout_run() == d.layout_run(None) == d.layout / ACTIVE_LAYOUT_RUN

    # ---- run names must stay a single path segment
    for bad in ("", ".", "..", "a/b", "a\\b", "/abs"):
        try:
            d.layout_run(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"run name not rejected: {bad!r}")

    # ---- the active run follows the <YYYYMMDD>_<slug> convention
    assert re.fullmatch(r"\d{8}_[a-z0-9][a-z0-9_]*", ACTIVE_LAYOUT_RUN), \
        ACTIVE_LAYOUT_RUN

    print("*** PATHS TEST PASSED ***")


if __name__ == "__main__":
    main()
