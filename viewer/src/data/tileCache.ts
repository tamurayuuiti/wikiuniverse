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
  // エクスポート時刻をクエリに付け、再エクスポート後の stale キャッシュ読載を防ぐ。
  private vq = ''

  // bootstrap.meta.generated_at 等をバージョンとして設定する。
  setVersion(v: string): void {
    this.vq = `?v=${encodeURIComponent(v)}`
  }

  // 現在のキャッシュエントリ数(UI 統計向け)。
  get size(): number {
    return this.cache.size
  }

  // 取得済みのタイルを同期で返す(未取得なら null。hover 用)。
  peek(g: number): Tile | null {
    return this.cache.get(g) ?? null
  }

  // 銀河 g のタイルを返す。
  async get(g: number): Promise<Tile> {
    const hit = this.cache.get(g)
    if (hit) return hit
    const pad = String(g).padStart(6, '0')
    const [buf, tj] = await Promise.all([
      (await fetch(`${DATA_BASE}tiles/gal_${pad}.bin${this.vq}`)).arrayBuffer(),
      (await fetch(`${DATA_BASE}tiles/gal_${pad}.json${this.vq}`)).json() as Promise<{ t: string[] }>,
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
    // 索引が n 未満であることを検証する(越境は undefined 経由で NaN を生む)。
    // 配列別にカウントし、根因診断(負値ラップかゴミか)を可能にする。
    let badE = 0
    for (let i = 0; i < tile.edges.length; i++) {
      if (tile.edges[i] >= n) { tile.edges[i] = 0; badE++ }
    }
    let badX = 0
    for (let i = 0; i < tile.cross.length; i += 3) {
      if (tile.cross[i] >= n) { tile.cross[i] = 0; badX++ }
    }
    if (badE || badX) {
      console.warn(`[tile ${g}] clamped edges=${badE} cross=${badX} (n=${n})`)
    }
    if (this.cache.size >= CAP) this.cache.delete(this.cache.keys().next().value as number)
    this.cache.set(g, tile)
    return tile
  }
}
