// src/types/catalog.ts
// ビューアが消費するカタログ/タイル形式の型定義(データ契約)を行う。
//
// 責務:
// - bootstrap.json のスキーマを静的に型付ける
// - 銀河タイルバイナリのデコード後表現を静的に型付ける
//
// 注意:
// - フィール드変更時は生成側 scripts/export_viewer_tiles.py と必ず同期する。
// - Raw 系のタプル順序は JSON/バイナリの物理配置を反映しており、並べ替え禁止。

// マクロ行の生タプル。[x, y, z, radius, n_articles, rep_title]
export type RawMacro = [number, number, number, number, number, string]

// 銀河行の生タプル。[x, y, z, radius, macroId, cls, n_articles, name, e_in, e_out]
export type RawGalaxy = [number, number, number, number, number, number, number, string, number, number]

// ペア行の生タプル。[a, b, weight]
export type RawPair = [number, number, number]

// bootstrap.json の生スキーマ。
export interface RawBootstrap {
  meta: {
    galaxy_tag: string
    layout_sub: string
    n_articles: number
    n_galaxies: number
    generated_at: string
    cross_cap: number
  }
  macros: RawMacro[]
  galaxies: RawGalaxy[]
  macro_pairs: RawPair[]
  galaxy_pairs: RawPair[]
}

// 表示クラス。0=galaxy / 1=medium(銀河間物質)/ 2=dust(孤立記事)。
export type DisplayClass = 0 | 1 | 2

// デコード済みマクロ。
export interface Macro {
  id: number
  x: number
  y: number
  z: number
  r: number
  n: number
  rep: string
}

// デコード済み銀河。
export interface Galaxy {
  id: number
  x: number
  y: number
  z: number
  r: number
  macroId: number
  cls: DisplayClass
  n: number
  name: string
  eIn: number
  eOut: number
}

// 重み付きペア。
export interface Pair {
  a: number
  b: number
  w: number
}

// デコード済み bootstrap。
export interface Bootstrap {
  meta: RawBootstrap['meta']
  macros: Macro[]
  galaxies: Galaxy[]
  macroPairs: Pair[]
  galaxyPairs: Pair[]
}

// 銀河タイルのメモリ内表現。pos は canon  global 座標(float32)。
export interface Tile {
  g: number
  n: number
  ne: number
  nx: number
  pos: Float32Array
  edges: Uint32Array
  cross: Uint32Array
  titles: string[]
}
