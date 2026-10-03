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
// - ゾーム帯ラベルは focus.ts(focusZoomLabel)が包含状態から導く。BAND はその距離帯定数。
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
  // 2800: 通常時のインク量を旧値 2200 から ~2割減(選択強調との対比も狙い)。
  edgeRef: 2800,
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
  // 選択強調(ego 網): 線幅 px・輝度と、選択中の自銀河の相対減光
  // (ego の対象星と接続エッジを際立たせるため、背景側を落とす)。
  egoWidthPx: 2.4,
  egoOpacity: 0.92,
  egoDimEdges: 0.4,
  egoDimStars: 0.72,
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

// エッジ色の知覚輝度の目標値(sRGB 相対輝度の近似 0.2126R+0.7152G+0.0722B)。
// HSL の同一 lightness は色相によって知覚輝度が大きく異なる(黄緑は明るく
// 青は暗い)= 「黄色い銀河のエッジだけ目立つ」の根因。目標輝度へスケーリング
// して色相間の見え方を均す。目標 0.50 ≈ 基準色相 0.6(青系, Y≈0.47)と
// 黄色(Y≈0.64)の中間。
export const EDGE_LUMA_TARGET = 0.5

// エッジ色(生成後の THREE.Color)を知覚輝度 target へ均す(sRGB 値上の近似補正)。
export function equalizeLuma(color: THREE.Color, target = EDGE_LUMA_TARGET, floor = 0.6, ceil = 1.5): THREE.Color {
  const y = 0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b
  if (y <= 1e-4) return color
  const k = Math.min(ceil, Math.max(floor, target / y))
  color.multiplyScalar(k)
  return color
}

// 星の px サイズ上限(自己相似の成長を許す距離連動キャップ)。
// 旧実装の固定 9px は接近しても星が大きくならず「近づけない」体感の主因だった。
// cap = clamp(tilePx * k, min, max): 自然サイズ(aSize*uPx ~ 0.172*tilePx)を常に上回り、
// 近接時は gl_PointSize の実装上限(通常 255+px)に対し安全な 120px まで成長する。
export const STAR_PX_CAP = {
  min: 9,
  max: 120,
  k: 0.22,
} as const
