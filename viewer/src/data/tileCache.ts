// src/data/tileCache.ts
// 銀河タイル(bin+json)の取得・デコード・LRU キャッシュを行う。
//
// 責務:
// - タイルバイナリのヘッダ解析と typed array 化
// - メモリ上限のための LRU 退避
//
// 注意:
// - バイナリ布局は export_viewer_tiles.py と固定契約(変更時は両側同時改修)。
// - Float32Array は buffer _OFFSET 依存のため、アライメントは 4byte 保証済みであること。

import type { Tile } from '@/types/catalog'
import { DATA_BASE } from './base'

// LRU キャッシュの最大エントリ数。
const CAP = 48

// タイルバイナリのレイアウトオフセット(バイト)。
const HDR = 12

// 銀河タイルを取得してデコードする。キャッシュhit時はネットワークアクセスを行わない。
export class TileCache {
  private cache = new Map<number, Tile>()

  // 現在のキャッシュエントリ数(UI 統計向け)。
  get size(): number {
    return this.cache.size
  }

  // 銀河 g のタイルを返す。
  async get(g: number): Promise<Tile> {
    const hit = this.cache.get(g)
    if (hit) return hit
    const pad = String(g).padStart(6, '0')
    const [buf, tj] = await Promise.all([
      (await fetch(`${DATA_BASE}tiles/gal_${pad}.bin`)).arrayBuffer(),
      (await fetch(`${DATA_BASE}tiles/gal_${pad}.json`)).json() as Promise<{ t: string[] }>,
    ])
    const dv = new DataView(buf)
    const n = dv.getUint32(0, true)
    const ne = dv.getUint32(4, true)
    const nx = dv.getUint32(8, true)
    const tile: Tile = {
      g, n, ne, nx,
      pos: new Float32Array(buf, HDR, n * 3),
      edges: new Uint32Array(buf, HDR + n * 12, ne * 2),
      cross: new Uint32Array(buf, HDR + n * 12 + ne * 8, nx * 3),
      titles: tj.t,
    }
    if (this.cache.size >= CAP) this.cache.delete(this.cache.keys().next().value as number)
    this.cache.set(g, tile)
    return tile
  }
}
