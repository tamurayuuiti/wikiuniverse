// src/data/bootstrap.ts
// bootstrap.json を取得し、描画用のデコード済み構造へ変換する。
//
// 責務:
// - 宇宙ビューに必要な全メタデータ(銀河/マクロ/ペア)の一括取得
//
// 注意:
// - 生タプル順序は types/catalog.ts の契約に固定される。

import type { Bootstrap, RawBootstrap } from '@/types/catalog'
import { DATA_BASE } from './base'

// bootstrap.json を取得してデコードする。
export async function loadBootstrap(): Promise<Bootstrap> {
  // bootstrap は常に鮮なものを取る(タイル版本の版元)。
  const res = await fetch(DATA_BASE + 'bootstrap.json', { cache: 'no-store' })
  if (!res.ok) throw new Error(`bootstrap fetch failed: ${res.status}`)
  const raw = (await res.json()) as RawBootstrap
  return {
    meta: raw.meta,
    macros: raw.macros.map((m, i) => ({
      id: i, x: m[0], y: m[1], z: m[2], r: m[3], n: m[4], rep: m[5],
    })),
    galaxies: raw.galaxies.map((g, i) => ({
      id: i, x: g[0], y: g[1], z: g[2], r: g[3], macroId: g[4],
      cls: g[5] as 0 | 1 | 2, n: g[6], name: g[7], eIn: g[8] ?? 0, eOut: g[9] ?? 0,
    })),
    macroPairs: raw.macro_pairs.map(p => ({ a: p[0], b: p[1], w: p[2] })),
    galaxyPairs: raw.galaxy_pairs.map(p => ({ a: p[0], b: p[1], w: p[2] })),
  }
}
