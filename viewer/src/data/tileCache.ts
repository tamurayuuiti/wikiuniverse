// src/data/tileCache.ts
// タイルストリーミングキャッシュ(ディスク契約: bin + サイドカー json)。
//
// 責務:
// - gal_XXXXXX.bin の取得/デコード(rank 順、deg は算出、zBase 保存)
// - サイドカー gal_XXXXXX.json からのタイトル取得
// - LRU 追い出しと ?v=generated_at キャッシュバスティング
// - recomposeTiles: z-compress スライダの in-place 再構成
//
// 注意:
// - バイナリ布局は export_viewer_tiles.py と固定契約(変更時は両側同時改修):
//   header u32×3(n, n_edges, n_cross) + pos f32 n*3 + edges u32 ne*2 + cross u32 nx*3。
// - デコード時に範囲検証し、不正要素は捨てる(NaN/クラッシュ防御)。

import type { TileData, TileIndex } from '@/types/catalog'

// キャッシュ上限(タイル数)。
const CACHE_MAX = 48

let _index: TileIndex | null = null
let _version = ''
const _cache = new Map<number, TileData>()
const _inflight = new Map<number, Promise<TileData | null>>()
let _lru: number[] = []

// TileIndex とバージョンを設定する。
export function setIndex(index: TileIndex, version: string): void {
  _index = index
  _version = version
}

// キャッシュを覗く(未取得なら null)。
export function peekTile(gid: number): TileData | null {
  const t = _cache.get(gid)
  if (t) touch(gid)
  return t ?? null
}

// タイルを取得する(キャッシュ/進行中を優先)。
export function fetchTile(gid: number): Promise<TileData | null> {
  const hit = _cache.get(gid)
  if (hit) {
    touch(gid)
    return Promise.resolve(hit)
  }
  const inf = _inflight.get(gid)
  if (inf) return inf
  const p = load(gid).finally(() => _inflight.delete(gid))
  _inflight.set(gid, p)
  return p
}

// z-compress を全キャッシュタイルへ適用する(in-place、z = gz + (zBase - gz) * k)。
export function recomposeTiles(k: number): number {
  if (!_index) return 0
  let count = 0
  for (const [gid, tile] of _cache) {
    const meta = _index.galaxies.get(gid)
    if (!meta) continue
    const gz = meta.gz
    for (let i = 0; i < tile.n; i++) {
      tile.pos[i * 3 + 2] = gz + (tile.zBase[i] - gz) * k
    }
    count++
  }
  return count
}

// 全タイルを破棄する。
export function clearTiles(): void {
  _cache.clear()
  _lru = []
}

// キャッシュ数を返す。
export function cacheCount(): number {
  return _cache.size
}

// LRU を更新する。
function touch(gid: number): void {
  _lru = _lru.filter(x => x !== gid)
  _lru.push(gid)
}

// LRU 追い出し。
function evict(): void {
  while (_cache.size > CACHE_MAX && _lru.length > 0) {
    const old = _lru.shift()
    if (old != null) _cache.delete(old)
  }
}

// タイル URL を返す(バージョン付き)。
function tileUrl(gid: number, ext: string): string {
  const v = _version ? `?v=${encodeURIComponent(_version)}` : ''
  return `/data/spatial/tiles/gal_${String(gid).padStart(6, '0')}.${ext}${v}`
}

// タイルを読み込んでデコードする。
async function load(gid: number): Promise<TileData | null> {
  if (!_index) return null
  try {
    const [binRes, jsonRes] = await Promise.all([fetch(tileUrl(gid, 'bin')), fetch(tileUrl(gid, 'json'))])
    if (!binRes.ok) return null
    const buf = await binRes.arrayBuffer()
    const titles: string[] = jsonRes.ok ? ((await jsonRes.json()) as { t?: string[] }).t ?? [] : []
    const tile = decode(gid, buf, titles)
    if (tile) {
      _cache.set(gid, tile)
      touch(gid)
      evict()
    }
    return tile
  } catch {
    return null
  }
}

// バイナリをデコードする(範囲検証+deg 算出+zBase 保存)。
function decode(gid: number, buf: ArrayBuffer, titles: string[]): TileData | null {
  const meta = _index?.galaxies.get(gid)
  if (!meta) return null
  const head = new Uint32Array(buf, 0, 3)
  const n = head[0]
  const ne = head[1]
  const nx = head[2]
  if (n === 0 || n !== meta.n) {
    console.warn(`[tile ${gid}] n=${n} ≠ index n=${meta.n}(version skew?)`)
    if (n === 0) return null
  }
  const expected = 12 + n * 12 + ne * 8 + nx * 12
  if (buf.byteLength < expected) {
    console.warn(`[tile ${gid}] filesize ${buf.byteLength} < expected ${expected}`)
    return null
  }
  const pos = new Float32Array(buf.slice(12, 12 + n * 12))
  const zBase = new Float32Array(n)
  for (let i = 0; i < n; i++) zBase[i] = pos[i * 3 + 2]
  let off = 12 + n * 12
  const edgesRaw = new Uint32Array(buf.slice(off, off + ne * 8))
  off += ne * 8
  const crossRaw = new Uint32Array(buf.slice(off, off + nx * 12))
  // 範囲検証(不正要素は捨てる)。
  const eSrc: number[] = []
  const eDst: number[] = []
  for (let i = 0; i + 1 < edgesRaw.length; i += 2) {
    const a = edgesRaw[i]
    const b = edgesRaw[i + 1]
    if (a < n && b < n) {
      eSrc.push(a)
      eDst.push(b)
    }
  }
  const cross: number[] = []
  for (let i = 0; i + 2 < crossRaw.length; i += 3) {
    const u = crossRaw[i]
    const og = crossRaw[i + 1]
    const v = crossRaw[i + 2]
    const ometa = _index?.galaxies.get(og)
    if (u < n && ometa && v < ometa.n) cross.push(u, og, v)
  }
  // deg 算出(内部+クロス)。
  const deg = new Int32Array(n)
  for (let i = 0; i < eSrc.length; i++) {
    deg[eSrc[i]]++
    deg[eDst[i]]++
  }
  for (let i = 0; i < cross.length; i += 3) deg[cross[i]]++
  // NaN/非有限 pos のサニタイズ(防御)。
  let sanitized = 0
  for (let i = 0; i < pos.length; i++) {
    if (!Number.isFinite(pos[i])) {
      pos[i] = 0
      sanitized++
    }
  }
  if (sanitized > 0) console.warn(`[tile ${gid}] sanitized ${sanitized} non-finite coords`)
  const t = titles.length === n ? titles : padTitles(titles, n)
  return { n, pos, titles: t, deg, eSrc: Int32Array.from(eSrc), eDst: Int32Array.from(eDst), cross: Int32Array.from(cross), zBase }
}

// タイトル配列を n まで埋める。
function padTitles(titles: string[], n: number): string[] {
  const out = titles.slice(0, n)
  while (out.length < n) out.push(`local#${out.length}`)
  return out
}
