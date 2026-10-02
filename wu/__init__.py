"""wikiuniverse (wu): jawiki 記事グラフのコミュニティ解析 PoC ツールキット。

設計上の制約:
- 低 RAM(ストリーミングパーサ、memmap、searchsorted によるソート済み配列 join)。
- 決定性(固定シード、blake2b64 タイトルハッシュ、メタデータの記録)。
- 成果物はディスク上: parquet / npy / 生 int32 バイナリのエッジリスト。
"""
__version__ = "0.1.0"
