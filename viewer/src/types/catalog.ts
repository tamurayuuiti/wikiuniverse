// src/types/catalog.ts
// データ契約の型定義(export_viewer_tiles.py のディスク契約と完全一致)。
//
// 責務:
// - bootstrap.json(compact)/ タイルバイナリ+サイドカーの TS 型表現
// - ローダとビューアの共有契約
//
// 注意:
// - フィールド変更時は生成側 scripts/export_viewer_tiles.py と必ず同期する。
// - タイル内配列は rank 順(pos/titles/edges/cross すべて同一順)。
// - deg はタイル内に存在しない → デコード時にエッジから算出する。

/** bootstrap.json のマクロ行: [x, y, z, r, n_articles, label]。 */
export type RawMacro = [number, number, number, number, number, string]

/** bootstrap.json の銀河行: [x, y, z, r, mid, display_class, n, name, e_in, e_out]。 */
export type RawGalaxy = [number, number, number, number, number, number, number, string, number, number]

/** bootstrap.json のペア行: [a, b, w]。 */
export type RawPair = [number, number, number]

/** bootstrap.json 生フォーマット。 */
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

/** 表示用マクロ(銀河団)。 */
export interface Macro {
  mid: number
  label: string
  n: number
  n_galaxies: number
  x: number
  y: number
  z: number
  r: number
  hue: number
}

/** 表示用銀河。 */
export interface GalaxyBoot {
  gid: number
  label: string
  n: number
  n_cross: number
  display_class: number
  x: number
  y: number
  z: number
  r: number
  macro: number
  hue: number
}

/** バンドル(pair)。 */
export interface Pair {
  a: number
  b: number
  w: number
}

/** ビューア用 bootstrap(解析後)。 */
export interface BootstrapData {
  version: string
  generated_at: string
  nArticles: number
  macros: Macro[]
  galaxies: GalaxyBoot[]
  macroBundles: Pair[]
  galaxyBundles: Pair[]
}

/** 銀河メタ(TileIndex 値)。 */
export interface GalaxyMeta {
  gid: number
  mid: number
  n: number
  n_cross: number
  display_class: number
  x: number
  y: number
  z: number
  r: number
  hue: number
  /** z 再構成用: アンカー z(=マクロ z、canon 配置の規約)。 */
  gz: number
}

/** タイルインデックス。 */
export interface TileIndex {
  count: number
  version: string
  generated_at: string
  galaxies: Map<number, GalaxyMeta>
}

/** デコード済みタイル(全て rank 順)。 */
export interface TileData {
  n: number
  /** 記事のグローバル座標(n×3)。 */
  pos: Float32Array
  /** 記事タイトル(サイドカー gal_XXXXXX.json)。 */
  titles: string[]
  /** タイル内次数(内部エッジ+クロスから算出)。 */
  deg: Int32Array
  /** 内部エッジ端点(rank 空間)。 */
  eSrc: Int32Array
  eDst: Int32Array
  /** クロスエッジ triple [local_u, other_g, local_v](rank 空間)。 */
  cross: Int32Array
  /** z 再構成の基準(canonical k=1 の z 値コピー)。 */
  zBase: Float32Array
}
