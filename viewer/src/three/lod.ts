// src/three/lod.ts
// 自己相似 LOD の閾値と関数群(全階層で共通の「塊→点群」遷移則)。
//
// 責務:
// - 画面 px 半径の計算式提供
// - 出現/解決閾値とスムーズステップによる α 法則
//
// 注意:
// - 閾値は「親塊の画面 px 半径」基準で統一する(自己相似性の要)。
// - ヒステリシスは α のフレーム平滑で実現し、別閾値を持たない。

import * as THREE from 'three'

// LOD 閾値(親塊の画面 px 半径)。
export const LOD = {
  // これ未満: 子は描画せず親塊スプライトのみ(描画コストの受け渡し点)。
  clump: 3.5,
  // 子の出現開始。
  emerge: 9,
  // 子が完全に出現し親塊がリムへ退く。
  resolve: 55,
  // 内部エッジ描画の開始倍率(resolve 比)。
  edgesFactor: 1.15,
  // 同時浮上(タイル保持)する銀河の上限。
  budget: 24,
} as const

// 世界半径と距離から画面 px 半径を返す。
export function screenPx(worldR: number, dist: number, innerH: number, fovDeg: number): number {
  const proj = innerH / (2 * Math.tan(THREE.MathUtils.degToRad(fovDeg / 2)))
  return (worldR * proj) / Math.max(dist, 1e-6)
}

// [a,b] のスムーズステップを返す。
export function smoothstep(a: number, b: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - a) / Math.max(b - a, 1e-9)))
  return t * t * (3 - 2 * t)
}

// 親 px から子の出現 α(0..1)を返す。
export function emergeAlpha(parentPx: number): number {
  return smoothstep(LOD.emerge, LOD.resolve, parentPx)
}

// 親 px から親塊の残存 α(加算ブレンド用の乗数)を返す。
export function clumpAlpha(parentPx: number): number {
  return 1 - 0.88 * emergeAlpha(parentPx)
}

// 親 px から内部エッジの α を返す。
export function edgesAlpha(parentPx: number): number {
  return smoothstep(LOD.resolve * LOD.edgesFactor, LOD.resolve * 1.9, parentPx) * 0.32
}

// 星の px サイズ倍率(親 px に比例=自己相似)。上限は uPxMax で clamp される。
export const STAR_PX_FACTOR = 0.071
