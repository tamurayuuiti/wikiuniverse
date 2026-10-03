// src/three/galaxyTextures.ts
// 手続き的銀河テクスチャ(遠景の塊スプライト用)。
//
// 責務:
// - 「いわゆる銀河」のテクスチャ変奏セットの生成: 対数渦巻腕(腕数・巻き込み・
//   棒・粒度の変奏)、媒介銀河用の淡いもや、dust/非構造用の放射グロー
// - 出力はグレースケール(白系の α)。色づけは SpriteMaterial の color(=銀河 hue)の
//   乗算で行う(universeLayer 側)
//
// 注意:
// - 銀河毎(1,971 個)の個別生成はメモリ・時間の双方で不可のため、少数の変奏 ×
//   銀河毎の画面内回転・扁平(=傾き表現)・hue の組合せで個体差を出す。
// - 決定的 PRNG(mulberry32、固定シード)= セッションを跨いで同一の見た目。
// - 外部画像に依存しない(CanvasTexture のみ、textures.ts と同じ方針)。
// - データ駆動方式(実レイアウトのオフライン・プリレンダ)は将来の選択肢で、
//   本モジュールの手続き的方式とは排他ではない(差し替えは universeLayer の
//   テクスチャ供給点 1 箇所)。

import * as THREE from 'three'

// テクスチャ辺長(px)。512 = 近接時の拡大に耐える解像度(変奏 7 枚で ~7MB)。
const SIZE = 512

// 決定的 PRNG(mulberry32)。
function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// 銀河テクスチャの変奏セット。
export interface GalaxyTextureSet {
  /** 渦巻変奏(通常銀河。gid ハッシュで選択)。 */
  spirals: THREE.CanvasTexture[]
  /** 媒介銀河(medium)用の構造の乏しいもや。 */
  haze: THREE.CanvasTexture
  /** dust/非構造用の放射グロー。 */
  glow: THREE.CanvasTexture
  /** 全テクスチャを破棄する。 */
  dispose: () => void
}

// キャンバスと 2D コンテキストを用意する(取得失敗時は空キャンバスで安全側)。
function makeCanvas(): { cv: HTMLCanvasElement; ctx: CanvasRenderingContext2D | null } {
  const cv = document.createElement('canvas')
  cv.width = SIZE
  cv.height = SIZE
  return { cv, ctx: cv.getContext('2d') }
}

// 柔らかい円形ブロブを加算で stamped する(腕・粒・バルジの共通プリミティブ)。
function blob(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, a: number): void {
  if (r <= 0.5 || a <= 0.004) return
  const g = ctx.createRadialGradient(x, y, 0, x, y, r)
  g.addColorStop(0, `rgba(255,251,242,${a.toFixed(3)})`)
  g.addColorStop(0.5, `rgba(255,249,238,${(a * 0.42).toFixed(3)})`)
  g.addColorStop(1, 'rgba(255,246,232,0)')
  ctx.fillStyle = g
  ctx.beginPath()
  ctx.arc(x, y, r, 0, Math.PI * 2)
  ctx.fill()
}

// 円形にフェードアウトするマスクを掛ける(角・縁の透明化)。
function edgeFade(ctx: CanvasRenderingContext2D, inner: number): void {
  const c = SIZE / 2
  const R = c
  ctx.globalCompositeOperation = 'destination-in'
  const g = ctx.createRadialGradient(c, c, R * inner, c, c, R)
  g.addColorStop(0, 'rgba(255,255,255,1)')
  g.addColorStop(1, 'rgba(255,255,255,0)')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, SIZE, SIZE)
  ctx.globalCompositeOperation = 'source-over'
}

// 対数渦巻銀河を 1 枚描く。
// arms: 腕数 / wind: 内周から外周までの巻込み角(rad) / grain: 粒度乗数 / bar: 棒の有無。
function drawSpiral(seed: number, arms: number, wind: number, grain: number, bar: boolean): THREE.CanvasTexture {
  const { cv, ctx } = makeCanvas()
  if (!ctx) return new THREE.CanvasTexture(cv)
  const c = SIZE / 2
  const R = c * 0.96
  const rnd = mulberry32(seed)
  // 円盤全体の薄い下地。
  blob(ctx, c, c, R, 0.14)
  ctx.globalCompositeOperation = 'lighter'
  // 渦巻腕: 対数螺線 r = r0 * k^t 沿いのブロブ列(腕の幅は外側で広げる)。
  const rStart = R * (bar ? 0.2 : 0.12)
  const rEnd = R * 0.92
  for (let k = 0; k < arms; k++) {
    const th0 = (k / arms) * Math.PI * 2 + rnd() * 0.3
    const steps = 190
    for (let s = 0; s < steps; s++) {
      const t = s / (steps - 1)
      const r = rStart * Math.pow(rEnd / rStart, t)
      const th = th0 + wind * t
      const cw = (0.028 + 0.055 * t) * R
      // 1 ステップあたり 3 列(腕の密度感)。
      for (let q = 0; q < 3; q++) {
        const off = (q - 1) * cw * (0.7 + 0.5 * rnd())
        const jx = (rnd() - 0.5) * cw * 1.15
        const jy = (rnd() - 0.5) * cw * 1.15
        const px = c + r * Math.cos(th) - Math.sin(th) * off + jx
        const py = c + r * Math.sin(th) + Math.cos(th) * off + jy
        const br = R * (0.072 - 0.036 * t) * (0.65 + 0.7 * rnd())
        const a = (0.3 - 0.19 * t) * grain * (0.6 + 0.7 * rnd())
        blob(ctx, px, py, Math.max(br, 2), Math.min(a, 0.4))
      }
    }
  }
  // 細粒(円盤全体のまばらな輝点 = 星の粒状感)。
  const grains = Math.round(400 * grain)
  for (let i = 0; i < grains; i++) {
    const rr = R * 0.95 * Math.sqrt(rnd())
    const tt = rnd() * Math.PI * 2
    blob(ctx, c + rr * Math.cos(tt), c + rr * Math.sin(tt), R * 0.011 * (0.5 + rnd()), 0.09 + 0.15 * rnd())
  }
  // 棒(棒渦巻: 中心の伸びた輝帯)。
  if (bar) {
    ctx.save()
    ctx.translate(c, c)
    ctx.rotate(rnd() * Math.PI)
    const bg = ctx.createLinearGradient(-R * 0.3, 0, R * 0.3, 0)
    bg.addColorStop(0, 'rgba(255,248,235,0)')
    bg.addColorStop(0.5, 'rgba(255,250,240,0.4)')
    bg.addColorStop(1, 'rgba(255,248,235,0)')
    ctx.fillStyle = bg
    ctx.beginPath()
    ctx.ellipse(0, 0, R * 0.3, R * 0.075, 0, 0, Math.PI * 2)
    ctx.fill()
    ctx.restore()
  }
  // バルジ(中心核: 銀河の視覚的重心)。
  blob(ctx, c, c, R * (bar ? 0.17 : 0.2), 0.8)
  blob(ctx, c, c, R * 0.09, 0.95)
  ctx.globalCompositeOperation = 'source-over'
  edgeFade(ctx, 0.55)
  const tex = new THREE.CanvasTexture(cv)
  tex.needsUpdate = true
  return tex
}

// 媒介銀河(medium)用: 構造の乏しい楕円状のもや。
function drawHaze(seed: number): THREE.CanvasTexture {
  const { cv, ctx } = makeCanvas()
  if (!ctx) return new THREE.CanvasTexture(cv)
  const c = SIZE / 2
  const R = c * 0.9
  const rnd = mulberry32(seed)
  ctx.globalCompositeOperation = 'lighter'
  blob(ctx, c, c, R * 0.85, 0.3)
  blob(ctx, c, c, R * 0.4, 0.4)
  // 淡い非対称のWisps(2 弧)= 「銀河間物質」らしい不定形さ。
  for (let k = 0; k < 2; k++) {
    const th = rnd() * Math.PI * 2
    for (let s = 0; s < 40; s++) {
      const t = s / 39
      const rr = R * (0.25 + 0.55 * t)
      const a = 0.1 * (1 - t * 0.7)
      blob(ctx, c + rr * Math.cos(th + t * 1.2), c + rr * Math.sin(th + t * 1.2) * 0.7, R * 0.09, a)
    }
  }
  ctx.globalCompositeOperation = 'source-over'
  edgeFade(ctx, 0.4)
  const tex = new THREE.CanvasTexture(cv)
  tex.needsUpdate = true
  return tex
}

// dust/非構造用: 柔らかい放射グロー。
function drawGlow(): THREE.CanvasTexture {
  const { cv, ctx } = makeCanvas()
  if (!ctx) return new THREE.CanvasTexture(cv)
  const c = SIZE / 2
  ctx.globalCompositeOperation = 'lighter'
  blob(ctx, c, c, c * 0.92, 0.5)
  blob(ctx, c, c, c * 0.34, 0.6)
  ctx.globalCompositeOperation = 'source-over'
  edgeFade(ctx, 0.2)
  const tex = new THREE.CanvasTexture(cv)
  tex.needsUpdate = true
  return tex
}

// 銀河テクスチャ変奏セットを生成する(呼び出し側が dispose の責務を持つ)。
export function makeGalaxyTextureSet(): GalaxyTextureSet {
  // 変奏: 腕数 2/2/3/4/2、巻き込み・粒度・棒を変えた 5 種(実在の渦巻銀河の多様性の抽象化)。
  const spirals = [
    drawSpiral(9001, 2, 3.4, 1.0, false),
    drawSpiral(9002, 2, 4.6, 0.85, true),
    drawSpiral(9003, 3, 3.9, 1.05, false),
    drawSpiral(9004, 4, 4.3, 0.95, false),
    drawSpiral(9005, 2, 5.4, 1.15, false),
  ]
  const haze = drawHaze(9101)
  const glow = drawGlow()
  return {
    spirals,
    haze,
    glow,
    dispose: () => {
      for (const t of spirals) t.dispose()
      haze.dispose()
      glow.dispose()
    },
  }
}
