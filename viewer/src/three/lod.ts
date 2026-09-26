// src/three/lod.ts
// 自己相似 LOD の閾値・α 法則・ズーム帯定義(全階層共通の単一情報源)。
//
// 責務:
// - 画面 px 半径の計算式
// - 塊←→子の受け渡し閾値と α 法則(ヒステリシスは α 平滑側で持つ)
// - エッジ tier / ラベルの距離・px 法則
// - 測光: 露出(1/n 法則)とハブ抑制 ink(2/(d_u+d_v))でエッジ累積光束を有界化
//
// 注意:
// - 閾値は「親塊の画面 px 半径」基準で統一する(自己相似性の要)。
// - エッジ tier の帯はカメラ→target 距離 D で定義する(レイアウトスケール依存定数)。
// - エッジ類は全て通常ブレンド(線色へ収束し白飛びしない)。加算は星・ego のみ。
// - 露出は「描画本数 n に対し α ∝ 1/n」。重なり層数が n に比例するため、
//   画面インク総量が n に依らず一定になる(稠密銀河でもベール化しない)。
// - ハブ抑制 ink はノードあたり入射 ink 総和を O(1) に縛る(放射状白飛び防止)。

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

// 測光定数(ライティング制御の単一情報源)。
export const EXPOSURE = {
  // 内部エッジ露出の基準本数(α = edgeRef / drawn)。
  edgeRef: 2200,
  // 内部エッジ露出の下限(極小銀河が消えないように)。
  edgeFloor: 0.12,
  // クロスアーク露出の基準本数。
  crossRef: 150,
  // クロスアーク露出の下限。
  crossFloor: 0.2,
  // 典型次数(中央値=1)同士のエッジ ink。
  edgeInk: 0.85,
  crossInk: 0.85,
  macroBundleInk: 0.9,
  galaxyBundleInk: 0.9,
  // ハブ抑制の残置 ink(超高次数でも完全には消さない)。
  inkFloor: 0.04,
  // 星コア輝度の FS 乗数(ベース+コア)。
  starBase: 0.24,
  starCore: 0.62,
  // ハブ飾り星の輝度乗数(ベース+コア)。
  hubBase: 0.26,
  hubCore: 0.68,
} as const

// 描画本数から内部エッジの露出(α 乗数)を返す(1/n 法則)。
export function edgeExposure(drawn: number): number {
  return Math.min(1, Math.max(EXPOSURE.edgeFloor, EXPOSURE.edgeRef / Math.max(drawn, 1)))
}

// 描画本数からクロスアークの露出(α 乗数)を返す(1/n 法則)。
export function crossExposure(drawn: number): number {
  return Math.min(1, Math.max(EXPOSURE.crossFloor, EXPOSURE.crossRef / Math.max(drawn, 1)))
}

// 正規化次数(中央値=1)の両端からハブ抑制 ink を返す(入射総和 O(1) の縛り)。
export function edgeInk(dnA: number, dnB: number, k: number): number {
  return Math.min(1, Math.max(EXPOSURE.inkFloor, (k * 2) / (dnA + dnB)))
}

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

// 親 px から親塊の残存 α(通常ブレンド乗数)を返す。
export function clumpAlpha(parentPx: number): number {
  return 1 - 0.88 * emergeAlpha(parentPx)
}

// 親 px から内部エッジの α を返す(露出乗数は呼び出し側で掛ける)。
export function edgesAlpha(parentPx: number): number {
  return smoothstep(LOD.resolve * LOD.edgesFactor, LOD.resolve * 1.9, parentPx) * 0.3
}

// 親 px から実クロスリンクアークの α を返す(内部エッジより早く立ち上げる)。
export function crossAlpha(parentPx: number): number {
  return smoothstep(LOD.resolve * 0.7, LOD.resolve * 1.5, parentPx) * 0.34
}

// カメラ距離からマクロバンドルの α を返す(遠景で主役、接近で消える)。
export function macroBundleAlpha(D: number): number {
  return 0.34 * smoothstep(700, 1700, D)
}

// カメラ距離から銀河バンドルの α を返す(中景で主役)。
export function galaxyBundleAlpha(D: number): number {
  return 0.13 * smoothstep(120, 450, D)
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
