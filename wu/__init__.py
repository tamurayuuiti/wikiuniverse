"""wikiuniverse (wu): PoC toolkit for jawiki article-graph community analysis.

Design constraints:
- Low RAM (streaming parsers, memmap, sorted-array joins via searchsorted).
- Deterministic (fixed seeds, blake2b64 title hashes, recorded metadata).
- Artifacts on disk: parquet / npy / raw int32 binary edge lists.
"""
__version__ = "0.1.0"
