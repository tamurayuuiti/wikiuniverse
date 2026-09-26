// src/three/starField.ts
// 浮上(EMERGED)銀河の記事星を全球座標で描画する LOD ストリーム層。
//
// 責務:
// - 親 px が閾値を超えた銀河のタイルを取得して星点/内部エッジを生成する
// - 閾値割れで α を減衰させ、完了後に dispose する(描画コストの塊への返還)
// - 同時保持数予算(LRU 風: px 降順で予算超過分を退避)
//
// 注意:
// - 座標はタイル物理座標をそのまま使う(単一連続枠。float32 は ~1e4 単位で十分)。
// - 星の px サイズは親 px に比例(自己相似)。シェーダ uniform で毎フレーム更新。
// - 内部エッジは edgesAlpha>0 の銀河のうち px 上位 2 本のみに限定する。

import * as THREE from 'three'
import type { Bootstrap, Tile } from '@/types/catalog'
import type { TileCache } from '@/data/tileCache'
import { edgesAlpha, emergeAlpha, LOD, STAR_PX_FACTOR } from './lod'
import { macroColor } from './textures'

// 星点シェーダ。uPx(親 px 由来の px 倍率)と uAlpha(出現 α)を uniform で持つ。
const VS = `attribute float aSize; attribute vec3 aColor;
uniform float uPx; uniform float uPxMax; varying vec3 vC;
void main(){ vC=aColor; vec4 mv=modelViewMatrix*vec4(position,1.0);
  gl_Position=projectionMatrix*mv; gl_PointSize=min(aSize*uPx, uPxMax); }`
const FS = `uniform sampler2D uMap; uniform float uAlpha; varying vec3 vC;
void main(){ vec4 t=texture2D(uMap,gl_PointCoord); if(t.a<0.02) discard;
  gl_FragColor=vec4(vC*uAlpha,1.0)*t; }`

// position 系の配列から非有限値を除去(0 置換)し、件数を診断出力する。
// NaN は three の boundingSphere を壊し picking を不能にするため入口で防ぐ。
function sanitize(name: string, g: number, arr: Float32Array): void {
  let bad = 0
  for (let i = 0; i < arr.length; i++) {
    if (!Number.isFinite(arr[i])) { arr[i] = 0; bad++ }
  }
  if (bad) console.warn(`[starfield] galaxy ${g}: sanitized ${bad} non-finite values in ${name}`)
}

// 浮上銀河 1 件の描画エントリ。
interface Entry {
  g: number
  points: THREE.Points
  mat: THREE.ShaderMaterial
  lines: THREE.LineSegments | null
  alpha: number
  target: number
  px: number
}

// 星ストリーム層。
export class StarField {
  readonly group = new THREE.Group()
  private entries = new Map<number, Entry>()
  private fade = new Float32Array(0)
  private pending = new Set<number>()

  constructor(private tiles: TileCache, private b: Bootstrap) {
    this.fade = new Float32Array(b.galaxies.length)
  }

  // 銀河 g の現在 α を返す(宇宙層のハロー減衰用)。
  alphaOf(g: number): number {
    return this.entries.get(g)?.alpha ?? 0
  }

  // 全銀河の α 配列を返す(宇宙層へ渡す)。
  fadeArray(): Float32Array {
    return this.fade
  }

  // 毎フレームの LOD 同期。px/dist は宇宙層が計算した値を使う。
  sync(px: Float32Array, opts: {
    zK: number; pr: number; showEdges: boolean; enabled: boolean
  }): void {
    const G = this.b.galaxies.length
    // 候補選定: 出現閾値付近以上の px を持つ銀河を px 降順で予算内まで
    const cand: number[] = []
    for (let g = 0; g < G; g++) {
      const e = this.entries.get(g)
      const gate = e ? LOD.clump : LOD.emerge
      if (opts.enabled && px[g] > gate) cand.push(g)
    }
    cand.sort((a, b) => px[b] - px[a])
    const allowed = new Set(cand.slice(0, LOD.budget))
    for (let g = 0; g < G; g++) {
      const e = this.entries.get(g)
      const target = allowed.has(g) ? emergeAlpha(px[g]) : 0
      if (!e) {
        if (target > 0 && !this.pending.has(g)) {
          this.pending.add(g)
          void this.tiles.get(g).then(t => {
            this.pending.delete(g)
            this.create(g, t, opts)
          }).catch(() => this.pending.delete(g))
        }
        this.fade[g] = 0
        continue
      }
      e.px = px[g]
      e.target = target
      // フレーム平滑=ヒステリシス(急な出現/消滅とちらつきを防ぐ)。
      e.alpha += (target - e.alpha) * 0.18
      if (e.alpha < 0.02 && target === 0) { this.evict(g); this.fade[g] = 0; continue }
      this.fade[g] = e.alpha
      e.mat.uniforms.uAlpha.value = e.alpha
      e.mat.uniforms.uPx.value = opts.pr * Math.min(STAR_PX_FACTOR * px[g], 12)
      if (e.lines) (e.lines.material as THREE.LineBasicMaterial).opacity = edgesAlpha(px[g]) * e.alpha
    }
  }

  // 銀河 g の星点/内部エッジを生成する。
  private create(g: number, t: Tile, opts: { zK: number; showEdges: boolean }): void {
    const bg = this.b.galaxies[g]
    const deg = new Uint16Array(t.n)
    for (let i = 0; i < t.ne; i++) { deg[t.edges[2 * i]]++; deg[t.edges[2 * i + 1]]++ }
    const pos = new Float32Array(t.n * 3)
    for (let i = 0; i < t.n; i++) {
      pos[3 * i] = t.pos[3 * i]
      pos[3 * i + 1] = t.pos[3 * i + 1]
      pos[3 * i + 2] = t.pos[3 * i + 2] * opts.zK
    }
    const sz = new Float32Array(t.n)
    const colA = new Float32Array(t.n * 3)
    const base = macroColor(bg.macroId)
    const c2 = new THREE.Color()
    for (let i = 0; i < t.n; i++) {
      sz[i] = 0.7 + Math.min(deg[i], 40) / 40 * 1.7
      c2.copy(base).offsetHSL(0, 0, Math.min(0.35, deg[i] / 60))
      colA.set([c2.r, c2.g, c2.b], i * 3)
    }
    sanitize('pos', g, pos)
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    geo.setAttribute('aSize', new THREE.BufferAttribute(sz, 1))
    geo.setAttribute('aColor', new THREE.BufferAttribute(colA, 3))
    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uMap: { value: starTex() }, uPx: { value: 4 }, uPxMax: { value: 12 * optsPr() },
        uAlpha: { value: 0 },
      },
      vertexShader: VS, fragmentShader: FS,
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    })
    const points = new THREE.Points(geo, mat)
    this.group.add(points)
    let lines: THREE.LineSegments | null = null
    if (opts.showEdges && t.ne) {
      const stride = Math.max(1, Math.floor(t.ne / 60000))
      const lp: number[] = []
      for (let i = 0; i < t.ne; i += stride) {
        const a = t.edges[2 * i], b = t.edges[2 * i + 1]
        lp.push(pos[3 * a], pos[3 * a + 1], pos[3 * a + 2], pos[3 * b], pos[3 * b + 1], pos[3 * b + 2])
      }
      const larr = new Float32Array(lp)
      sanitize('internal-lines', g, larr)
      const lg = new THREE.BufferGeometry()
      lg.setAttribute('position', new THREE.BufferAttribute(larr, 3))
      lines = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({
        color: 0x2a4a66, transparent: true, opacity: 0,
        blending: THREE.AdditiveBlending, depthWrite: false }))
      this.group.add(lines)
    }
    this.entries.set(g, { g, points, mat, lines, alpha: 0, target: 0, px: 0 })
  }

  // エントリを破棄して描画コストを返還する。
  private evict(g: number): void {
    const e = this.entries.get(g)
    if (!e) return
    e.points.geometry.dispose()
    e.mat.dispose()
    if (e.lines) { e.lines.geometry.dispose(); (e.lines.material as THREE.Material).dispose() }
    this.group.remove(e.points)
    if (e.lines) this.group.remove(e.lines)
    this.entries.delete(g)
  }

  // 浮上銀河の星へのレイキャスト。{g, local} を返す。
  pick(ray: THREE.Raycaster): { g: number; local: number } | null {
    let best: { g: number; local: number; d: number } | null = null
    for (const e of this.entries.values()) {
      if (e.alpha < 0.4) continue
      ray.params.Points = { threshold: this.b.galaxies[e.g].r / 12 }
      const h = ray.intersectObject(e.points)
      if (h.length && h[0].index !== undefined && (!best || h[0].distance < best.d)) {
        best = { g: e.g, local: h[0].index, d: h[0].distance }
      }
    }
    return best ? { g: best.g, local: best.local } : null
  }

  // 全エントリを破棄する(z 変更時等)。
  clear(): void {
    for (const g of [...this.entries.keys()]) this.evict(g)
    this.fade.fill(0)
  }
}

// 星テクスチャの遅延生成(循環 import 回避のためここで生成)。
let _starTex: THREE.CanvasTexture | null = null
function starTex(): THREE.CanvasTexture {
  if (_starTex) return _starTex
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d') as CanvasRenderingContext2D
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32)
  gr.addColorStop(0, 'rgba(255,255,255,1)')
  gr.addColorStop(0.25, 'rgba(255,255,255,.9)')
  gr.addColorStop(0.55, 'rgba(210,230,255,.28)')
  gr.addColorStop(1, 'rgba(210,230,255,0)')
  g.fillStyle = gr
  g.fillRect(0, 0, 64, 64)
  _starTex = new THREE.CanvasTexture(c)
  return _starTex
}

// devicePixelRatio( uniforme uPxMax 用)。
function optsPr(): number {
  return Math.min(devicePixelRatio || 1, 2)
}
