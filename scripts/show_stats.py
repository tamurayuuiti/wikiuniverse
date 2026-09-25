import json
import os
import sys

base = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.environ.get("ARENA_WORKSPACE", "/home/user"), ".cache/wudata")
p = os.path.join(base, "graph", "full_stats.json")
if not os.path.exists(p):
    p = os.path.join(base, "artifacts/full_stats.json")  # legacy layout
d = json.load(open(p))
s = d["stats"]
for k, v in s.items():
    print(f"{k}: {v}")
print("\nTop 30 hubs:")
for h in d["top_hubs"][:30]:
    print(f'  {h["rank"]:3d}. {h["title"][:44]:46s} in={h["in"]:7,d} out={h["out"]:6,d} tot={h["total"]:7,d} {",".join(h["flags"])}')
