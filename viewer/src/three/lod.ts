// src/three/lod.ts
// 自己相似 LOD の閾値・α 法則・ズーム帯定義(全階層共通の単一情報源)。
//
// 責務:
// - 画面 px 半径の計算式
// - 塊←→子の受け渡し閾値と α 法則(ヒステリシスは α 平滑側で持つ)
// - エッジ tier / ラベルの距離・px 法則
//
// 注意:
// - 閾値は「親塊の画面 px 半径」基準で統一する(自己相似性の要)。
// - エッジ tier の帯はカメラ→target 距離 D で定義する(レイアウトスケール依存定数)。

import * as THREE from 'three'

// LOD 閾値(親塊の画面 px 半径)。
export const LOD = {
  // これ未満: 子は描画せず親塊スプライトのみ(描画コストの受け渡し点)。
  clump: 3.5,
  // 子(星/銀河)の出現開始。
  emerge: 9,
  // 子が完全出現し親塊がリムへ退く。
  resolve: 55,
  // 内部エッジ出現開始(resolve 比)。
  edgesFactor: 1.15,
  // 同時浮上(タイル保持)予算。
  budget: 24,
  // 銀河名ラベルの出現/消滅 px。
  galLabelIn: 14,
  galLabelOut: 900,
  // マクロ名ラベルの出現/消滅 px。
  macLabelIn: 10,
  macLabelOut: 520,
} as const

// エッジ tier の距離帯(カメラ→controls.target)。
export const BAND = {
  // これ以上遠い: マクロバンドルが主役。
  universe: 2600,
  // これ以下: 銀河内部エッジへ引き継ぐ。
  galaxy: 150,
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

// 親 px から親塊の残存 α(加算ブレンド乗数)を返す。
export function clumpAlpha(parentPx: number): number {
  return 1 - 0.88 * emergeAlpha(parentPx)
}

// 親 px から内部エッジの α を返す。
export function edgesAlpha(parentPx: number): number {
  return smoothstep(LOD.resolve * LOD.edgesFactor, LOD.resolve * 1.9, parentPx) * 0.32
}

// 親 px から実クロスリンクアークの α を返す(内部エッジより早く立ち上げる)。
export function crossAlpha(parentPx: number): number {
  return smoothstep(LOD.resolve * 0.7, LOD.resolve * 1.5, parentPx) * 0.5
}

// カメラ距離からマクロバンドルの α を返す(遠景で主役、接近で消える)。
export function macroBundleAlpha(D: number): number {
  return 0.55 * smoothstep(700, 1700, D)
}

// カメラ距離から銀河バンドルの α を返す(中景で主役)。
export function galaxyBundleAlpha(D: number): number {
  return 0.24 * smoothstep(120, 450, D)
}

// 銀河名ラベルの α(接近時のみ、近すぎても消える)。
export function galaxyLabelAlpha(px: number): number {
  return smoothstep(LOD.galLabelIn, LOD.galLabelIn * 2.6, px) * (1 - smoothstep(LOD.galLabelOut, LOD.galLabelOut * 1.7, px))
}

// マクロ名ラベルの α(遠〜中で表示、潜入時は消える)。
export function macroLabelAlpha(px: number): number {
  return smoothstep(LOD.macLabelIn, LOD.macLabelIn * 2.2, px) * (1 - smoothstep(LOD.macLabelOut, LOD.macLabelOut * 1.6, px)) * 0.85
}

// 星の px サイズ倍率(親 px 比例=自己相似)。
export const STAR_PX_FACTOR = 0.071

// カメラ距離からズーム帯ラベル(表示専用、モードではない)を返す。
export function zoomLabel(D: number): string {
  if (D < BAND.galaxy) return 'article'
  if (D < 800) return 'galaxy'
  if (D < BAND.universe) return 'cluster'
  return 'universe'
}
