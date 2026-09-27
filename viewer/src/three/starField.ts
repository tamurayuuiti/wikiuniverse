// src/three/starField.ts
// 連続宇宙の記事(星)層: 浮上銀河のタイルをストリームし、点群・内部エッジ・
// 実クロスリンク(銀河間アーク)・ego 網をグローバル座標で描画する。
//
// 責務:
// - 出現 α のヒステリシス平滑と LRU 追い出し(budget)によるコスト受け渡し
// - 点群シェーダ(色温度・瞬き・px サイズは親塊 px 比例)
// - 内部エッジ(px 則 α)/ 実クロスアーク(隣接タイルが揃った分だけ進歩的に追加)
// - 潜入フォーカス測光: 非フォーカス銀河の星・エッジ・アークのディミング
// - hover/click ピックと ego 網ハイライト
//
// 注意:
// - 点のワールドサイズは「親塊 px 比例 × STAR_PX_FACTOR」で与え、シェーダで
//   dist/proj 倍率を掛ける(自己相似性の要)。px 上限のみ持たせる。
// - クロスアークは実記事間リンク(隣接銀河タイルの位置を使用)。
//   隣接タイル未着の分は描かない(タイル到着時に進歩的に増える)。
// - 測光: エッジ類は通常ブレンド+露出(1/n)+ハブ抑制 ink(vertex color)で
//   累積光束を有界化する(加算ブレンドは本数に比例して白飛びするため廃止)。
//   加算は星点と ego 網(本数有界)のみ残す。
// - フォーカス dim はエントリ単位の乗数(所属銀河の member 度から算出)。
//   出現ライフサイクル(shown)とは独立で、dim が小さくてもタイルは保持する。

import * as THREE from 'three'
import type { TileIndex } from '@/types/catalog'
import { peekTile, fetchTile } from '@/data/tileCache'
import { LOD, STAR_PX_FACTOR, EXPOSURE, emergeAlpha, edgesAlpha, crossAlpha, edgeExposure, crossExposure, edgeInk } from './lod'
import { dimOf, galaxyMember } from './focus'
import type { FocusState } from './focus'

// 1 銀河あたりクロスアークの描画上限(サンプリング)。
const CROSS_ARC_CAP = 700
// 1 銀河あたり ego 網のエッジ上限。
const EGO_CAP = 64
// 内部エッジのサンプリング上限(表示密度用)。
const INTERNAL_CAP = 30000

interface Entry {
  g: number
  alpha: number // 目標出現 α
  shown: number // 表示 α(ヒステリシス平滑)
  dim: number // フォーカス減光乗数(0..1、毎フレーム更新)
  points: THREE.Points
  edges: THREE.LineSegments | null
  cross: THREE.LineSegments | null
  crossRequested: Set<number>
  drawnCross: number // 構築済みクロスアーク本数(露出 1/n の基準)
  n: number
  maxDeg: number
}

// スターフィールドの VS(色温度・瞬き・px サイズ=親塊 px 比例)。
const STAR_VS = `
uniform float uPx;
uniform float uTime;
uniform float uPxMax;
attribute float aSize;
attribute float aBright;
attribute float aPhase;
varying float vBright;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  float twinkle = 1.0 + 0.14 * sin(uTime * 2.2 + aPhase);
  gl_PointSize = min(aSize * uPx * twinkle, uPxMax);
  vBright = aBright;
}
`

// スターフィールドの FS(丸グロー)。
// 注意: ShaderMaterial は material.opacity が FS へ届かないため uOpacity で自前乗算する。
const STAR_FS = `
uniform vec3 uColorA;
uniform vec3 uColorB;
uniform float uOpacity;
varying float vBright;
void main() {
  vec2 d = gl_PointCoord - vec2(0.5);
  float r = length(d) * 2.0;
  if (r > 1.0) discard;
  float a = (1.0 - r);
  float core = pow(max(1.0 - r, 0.0), 2.4);
  vec3 c = mix(uColorA, uColorB, vBright);
  gl_FragColor = vec4(c * (${EXPOSURE.starBase} + ${EXPOSURE.starCore} * core), uOpacity * a * a);
}
`

const tmpColor = new THREE.Color()

// 次数中央値の近似(サンプリング)を返す(ハブ抑制の正規化基準)。
function medianDeg(deg: ArrayLike<number>): number {
  const n = deg.length
  if (n === 0) return 1
  const s: number[] = []
  const step = Math.max(1, Math.floor(n / 128))
  for (let i = 0; i < n; i += step) s.push(deg[i] ?? 0)
  s.sort((a, b) => a - b)
  return Math.max(1, s[s.length >> 1] ?? 1)
}

// 出現銀河の星・内部エッジ・クロスアーク・ego を描画する。
export class StarField {
  readonly group = new THREE.Group()
  private entries = new Map<number, Entry>()
  private order: number[] = []
  private index: TileIndex
  private time = 0
  private hoverKey = -1
  private egoKey = -1
  private egoLines: THREE.LineSegments | null = null
  private pickRay = new THREE.Raycaster()
  onHover: (hit: { g: number; local: number; title: string } | null) => void = () => {}
  onClick: (hit: { g: number; local: number; title: string }) => void = () => {}

  // index からスターフィールドを生成する。
  constructor(index: TileIndex) {
    this.index = index
  }

  // 出現集合をタイル px から再計算する(フォーカス減光を適用)。
  sync(tiles: Map<number, number>, dt: number, crossOn: boolean, focus: FocusState): void {
    this.time += dt
    const cand: number[] = []
    for (const g of tiles.keys()) cand.push(g)
    cand.sort((a, b) => (tiles.get(b) ?? 0) - (tiles.get(a) ?? 0))
    const emerged = new Set(cand.slice(0, LOD.budget).filter(g => (tiles.get(g) ?? 0) >= LOD.clump))
    // 追い出し(hover 中は除外、平滑 α が消えてから)。
    for (const [g, e] of this.entries) {
      if (!emerged.has(g)) {
        const held = this.hoverKey >> 24 === g || this.egoKey >> 24 === g
        if (!held) e.alpha = 0
        if (!held && e.shown < 0.02) this.remove(g)
      } else {
        e.alpha = emergeAlpha(tiles.get(g) ?? 0)
        if (e.alpha > 0.02) this.order[this.order.indexOf(g)] = g
      }
    }
    // 追加(px 降順、budget まで)。
    for (const g of cand) {
      if (!emerged.has(g) || this.entries.has(g)) continue
      if (this.entries.size >= LOD.budget) break
      const e = this.create(g)
      if (e) {
        this.entries.set(g, e)
        this.order.push(g)
        e.alpha = emergeAlpha(tiles.get(g) ?? 0)
      }
    }
    if (this.order.length > LOD.budget) this.order = this.order.slice(-LOD.budget)
    // 表示 α のヒステリシス平滑と各層の更新。
    for (const [g, e] of this.entries) {
      const px = tiles.get(g) ?? 0
      const rate = e.shown < e.alpha ? 1 - Math.exp(-dt * 4.5) : 1 - Math.exp(-dt * 2.2)
      e.shown += (e.alpha - e.shown) * rate
      // フォーカス減光(所属銀河の member 度から。ライフサイクルには影響させない)。
      const meta = this.index.galaxies.get(e.g)
      e.dim = dimOf(focus, galaxyMember(focus, e.g, meta?.mid ?? -1))
      const visible = e.shown > 0.01
      e.points.visible = visible
      if (!visible) {
        e.edges?.geometry.dispose()
        e.edges = null
        e.cross?.geometry.dispose()
        e.cross = null
        continue
      }
      const mat = e.points.material as THREE.ShaderMaterial
      mat.uniforms.uPx.value = px * STAR_PX_FACTOR
      mat.uniforms.uTime.value = this.time
      mat.uniforms.uOpacity.value = e.shown * e.dim
      mat.opacity = e.shown * e.dim
      // 内部エッジ(px 則×露出 1/n: 本数に依らず画面インクを一定化)。
      const tile = peekTile(e.g)
      const drawn = tile ? Math.min(tile.eSrc.length, INTERNAL_CAP) : INTERNAL_CAP
      const ea = edgesAlpha(px) * e.shown * e.dim * edgeExposure(drawn)
      if (ea > 0.004) {
        if (!e.edges) e.edges = this.buildEdges(e)
        if (e.edges) {
          e.edges.visible = true
          ;(e.edges.material as THREE.LineBasicMaterial).opacity = ea
        }
      } else if (e.edges) {
        e.edges.visible = false
      }
      // 実クロスアーク(隣接タイルを要求しつつ進歩的に構築、露出 1/n)。
      const ca = crossOn ? crossAlpha(px) * e.shown * e.dim * crossExposure(e.drawnCross || CROSS_ARC_CAP) : 0
      if (ca > 0.008) {
        this.buildCross(e, ca, crossAlpha(px) * e.shown > 0.1)
      } else if (e.cross) {
        e.cross.visible = false
      }
    }
    this.syncEgo()
  }

  // hover/click ピックを実行する。
  pick(ndc: THREE.Vector2, camera: THREE.PerspectiveCamera, kind: 'hover' | 'click'): void {
    const active = [...this.entries.values()].filter(e => e.shown > 0.3 && e.dim > 0.25 && e.points.visible)
    if (active.length === 0) {
      if (kind === 'hover') {
        this.hoverKey = -1
        this.onHover(null)
      }
      return
    }
    this.pickRay.setFromCamera(ndc, camera)
    this.pickRay.params.Points.threshold = Math.max(2, camera.position.length() * 0.002)
    const hits = this.pickRay.intersectObjects(active.map(e => e.points), false)
    if (hits.length === 0) {
      if (kind === 'hover') {
        this.hoverKey = -1
        this.onHover(null)
      }
      return
    }
    hits.sort((a, b) => (a.distanceToRay ?? 0) - (b.distanceToRay ?? 0))
    const h = hits[0]
    const e = active.find(x => x.points === h.object)
    if (!e || h.index == null) return
    const tile = peekTile(e.g)
    const title = tile && h.index < tile.n ? tile.titles[h.index] : `local#${h.index}`
    const key = (e.g << 24) | (h.index >>> 0)
    if (kind === 'hover') {
      if (this.hoverKey !== key) {
        this.hoverKey = key
        this.onHover({ g: e.g, local: h.index, title })
      }
    } else {
      this.onClick({ g: e.g, local: h.index, title })
    }
  }

  // ego 網(記事ハイライト+実リンク)を表示する(local<0 で解除)。
  showEgo(g: number, local: number): void {
    this.egoKey = local >= 0 ? (g << 24) | (local >>> 0) : -1
    this.syncEgo()
  }

  // ego 網をクリアする。
  clearEgo(): void {
    this.egoKey = -1
    if (this.egoLines) {
      this.group.remove(this.egoLines)
      this.egoLines.geometry.dispose()
      this.egoLines = null
    }
  }

  // z 再構成後に position を更新し、派生ラインを再構築させる。
  refreshPositions(): void {
    for (const e of this.entries.values()) {
      const attr = e.points.geometry.getAttribute('position') as THREE.BufferAttribute
      attr.needsUpdate = true
      if (e.edges) {
        this.group.remove(e.edges)
        e.edges.geometry.dispose()
        e.edges = null
      }
      if (e.cross) {
        this.group.remove(e.cross)
        e.cross.geometry.dispose()
        e.cross = null
      }
      e.crossRequested.clear()
    }
  }

  // タイルを解放する。
  dispose(): void {
    for (const g of [...this.entries.keys()]) this.remove(g)
    this.clearEgo()
  }

  // エントリを生成する(失敗時は null)。
  private create(g: number): Entry | null {
    const meta = this.index.galaxies.get(g)
    const tile = peekTile(g)
    if (!tile || !meta) {
      fetchTile(g).catch(() => {})
      return null
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(tile.pos, 3))
    const bright = new Float32Array(tile.n)
    const phase = new Float32Array(tile.n)
    let maxDeg = 1
    for (let i = 0; i < tile.n; i++) {
      const d = tile.deg[i] ?? 0
      if (d > maxDeg) maxDeg = d
      bright[i] = Math.min(1, Math.log1p(d) / Math.log1p(Math.max(maxDeg, 2)) + 0.25)
      phase[i] = ((i * 2654435761) % 6283) / 1000
    }
    geo.setAttribute('aBright', new THREE.BufferAttribute(bright, 1))
    geo.setAttribute('aPhase', new THREE.BufferAttribute(phase, 1))
    const size = new Float32Array(tile.n)
    for (let i = 0; i < tile.n; i++) size[i] = 0.62 + Math.pow((tile.deg[i] ?? 1) / maxDeg, 0.42) * 1.5
    geo.setAttribute('aSize', new THREE.BufferAttribute(size, 1))
    // 色温度: 銀河色相を基準に、高次数=青白/低次数=暖色+個体揺らぎ。
    tmpColor.setHSL(meta.hue, 0.32, 0.58)
    const colorA = tmpColor.clone()
    tmpColor.setHSL((meta.hue + 0.08) % 1, 0.12, 0.92)
    const colorB = tmpColor.clone()
    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uPx: { value: 1 },
        uTime: { value: 0 },
        uPxMax: { value: 9 },
        uOpacity: { value: 0 },
        uColorA: { value: colorA },
        uColorB: { value: colorB },
      },
      vertexShader: STAR_VS,
      fragmentShader: STAR_FS,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    mat.opacity = 0
    const points = new THREE.Points(geo, mat)
    points.frustumCulled = false
    points.renderOrder = 6
    points.userData.galaxy = g
    this.group.add(points)
    return { g, alpha: 0, shown: 0, dim: 1, points, edges: null, cross: null, crossRequested: new Set(), drawnCross: 0, n: tile.n, maxDeg }
  }

  // エントリを破棄する。
  private remove(g: number): void {
    const e = this.entries.get(g)
    if (!e) return
    this.group.remove(e.points)
    e.points.geometry.dispose()
    ;(e.points.material as THREE.Material).dispose()
    if (e.edges) {
      this.group.remove(e.edges)
      e.edges.geometry.dispose()
    }
    if (e.cross) {
      this.group.remove(e.cross)
      e.cross.geometry.dispose()
    }
    this.entries.delete(g)
    this.order = this.order.filter(x => x !== g)
  }

  // 内部エッジを構築する(サンプリング)。
  private buildEdges(e: Entry): THREE.LineSegments | null {
    const tile = peekTile(e.g)
    const meta = this.index.galaxies.get(e.g)
    if (!tile || !meta) return null
    const total = tile.eSrc.length
    const stride = Math.max(1, Math.floor(total / INTERNAL_CAP))
    const cap = Math.min(total, INTERNAL_CAP)
    const pos = new Float32Array(cap * 6)
    const col = new Float32Array(cap * 6)
    const med = medianDeg(tile.deg)
    tmpColor.setHSL(meta.hue, 0.35, 0.52)
    const br = tmpColor.r
    const bg = tmpColor.g
    const bb = tmpColor.b
    let k = 0
    for (let i = 0; i < total && k < cap; i += stride) {
      const a = tile.eSrc[i]
      const b = tile.eDst[i]
      if (a >= tile.n || b >= tile.n) continue
      pos[k * 6] = tile.pos[a * 3]
      pos[k * 6 + 1] = tile.pos[a * 3 + 1]
      pos[k * 6 + 2] = tile.pos[a * 3 + 2]
      pos[k * 6 + 3] = tile.pos[b * 3]
      pos[k * 6 + 4] = tile.pos[b * 3 + 1]
      pos[k * 6 + 5] = tile.pos[b * 3 + 2]
      // ハブ抑制 ink(両端正規次数): ハブ発射線のみ暗く、典型線は残す。
      const ink = edgeInk((tile.deg[a] ?? 0) / med, (tile.deg[b] ?? 0) / med, EXPOSURE.edgeInk)
      col[k * 6] = br * ink
      col[k * 6 + 1] = bg * ink
      col[k * 6 + 2] = bb * ink
      col[k * 6 + 3] = br * ink
      col[k * 6 + 4] = bg * ink
      col[k * 6 + 5] = bb * ink
      k++
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos.subarray(0, k * 6), 3))
    geo.setAttribute('color', new THREE.BufferAttribute(col.subarray(0, k * 6), 3))
    const mat = new THREE.LineBasicMaterial({ color: 0xffffff, vertexColors: true, transparent: true, opacity: 0.1, depthWrite: false, blending: THREE.NormalBlending })
    const lines = new THREE.LineSegments(geo, mat)
    lines.frustumCulled = false
    lines.renderOrder = 5
    this.group.add(lines)
    return lines
  }

  // 実クロスアークを進歩的に構築する(隣接タイルがあれば実記事間、なければ銀河中心へのビーム)。
  private buildCross(e: Entry, alpha: number, wantNeighbors: boolean): void {
    const tile = peekTile(e.g)
    const meta = this.index.galaxies.get(e.g)
    if (!tile || !meta) return
    if (e.cross) {
      ;(e.cross.material as THREE.LineBasicMaterial).opacity = alpha
      e.cross.visible = true
      if (!wantNeighbors) return
    }
    // 隣接タイルの要求(上位クロス先)。
    if (wantNeighbors && tile.cross.length > 0 && e.crossRequested.size < 8) {
      const counts = new Map<number, number>()
      for (let i = 0; i < tile.cross.length; i += 3) {
        const o = tile.cross[i + 1]
        counts.set(o, (counts.get(o) ?? 0) + 1)
      }
      const tops = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).map(x => x[0])
      let requested = false
      for (const o of tops) {
        if (e.crossRequested.has(o)) continue
        e.crossRequested.add(o)
        if (!peekTile(o)) {
          fetchTile(o).catch(() => {})
          requested = true
        }
      }
      if (requested && e.cross) return // 到着後に再構築
    }
    // アーク構築(サンプリング、実位置優先、ハブ抑制 ink を頂点色へ焼く)。
    const total = tile.cross.length / 3
    const stride = Math.max(1, Math.floor(total / CROSS_ARC_CAP))
    const cap = Math.min(total, CROSS_ARC_CAP)
    const pos: number[] = []
    const col: number[] = []
    const med = medianDeg(tile.deg)
    const otherMed = new Map<number, number>()
    tmpColor.setHSL((meta.hue + 0.5) % 1, 0.5, 0.6)
    const br = tmpColor.r
    const bg = tmpColor.g
    const bb = tmpColor.b
    const pushInk = (ink: number): void => {
      col.push(br * ink, bg * ink, bb * ink, br * ink, bg * ink, bb * ink)
    }
    for (let i = 0; i < total && pos.length < cap * 6; i += stride) {
      const u = tile.cross[i * 3]
      const og = tile.cross[i * 3 + 1]
      const v = tile.cross[i * 3 + 2]
      if (u >= tile.n) continue
      const ux = tile.pos[u * 3]
      const uy = tile.pos[u * 3 + 1]
      const uz = tile.pos[u * 3 + 2]
      const dnU = (tile.deg[u] ?? 0) / med
      const other = peekTile(og)
      if (other?.pos && v < other.n) {
        // 実記事間アーク(相手次数が既知なら両端で抑制)。
        let m = otherMed.get(og)
        if (m == null) {
          m = medianDeg(other.deg)
          otherMed.set(og, m)
        }
        pushInk(edgeInk(dnU, (other.deg[v] ?? 0) / m, EXPOSURE.crossInk))
        pos.push(ux, uy, uz, other.pos[v * 3], other.pos[v * 3 + 1], other.pos[v * 3 + 2])
      } else {
        // 隣接タイル未着: 隣接銀河中心への淡いビーム(相手次数は典型扱い)。
        const om = this.index.galaxies.get(og)
        if (!om) continue
        pushInk(edgeInk(dnU, 1, EXPOSURE.crossInk) * 0.5)
        pos.push(ux, uy, uz, ux + (om.x - ux) * 0.55, uy + (om.y - uy) * 0.55, uz + (om.z - uz) * 0.55)
      }
    }
    if (pos.length === 0) return
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
    const mat = new THREE.LineBasicMaterial({ color: 0xffffff, vertexColors: true, transparent: true, opacity: alpha, depthWrite: false, blending: THREE.NormalBlending })
    if (e.cross) {
      this.group.remove(e.cross)
      e.cross.geometry.dispose()
    }
    e.drawnCross = pos.length / 6
    e.cross = new THREE.LineSegments(geo, mat)
    e.cross.frustumCulled = false
    e.cross.renderOrder = 6
    this.group.add(e.cross)
  }

  // ego 網を同期する(選択記事のハイライトリング+実リンク)。
  private syncEgo(): void {
    const key = this.egoKey
    if (key < 0) return
    const g = key >> 24
    const local = key & 0xffffff
    const e = this.entries.get(g)
    const tile = peekTile(g)
    if (!e || !tile?.pos || local >= tile.n) {
      this.clearEgo()
      return
    }
    if (this.egoLines) {
      this.group.remove(this.egoLines)
      this.egoLines.geometry.dispose()
      this.egoLines = null
    }
    const pos: number[] = []
    const ux = tile.pos[local * 3]
    const uy = tile.pos[local * 3 + 1]
    const uz = tile.pos[local * 3 + 2]
    let added = 0
    for (let i = 0; i < tile.eSrc.length && added < EGO_CAP; i++) {
      if (tile.eSrc[i] !== local && tile.eDst[i] !== local) continue
      const o = tile.eSrc[i] === local ? tile.eDst[i] : tile.eSrc[i]
      if (o >= tile.n) continue
      pos.push(ux, uy, uz, tile.pos[o * 3], tile.pos[o * 3 + 1], tile.pos[o * 3 + 2])
      added++
    }
    for (let i = 0; i < tile.cross.length && added < EGO_CAP; i += 3) {
      if (tile.cross[i] !== local) continue
      const og = tile.cross[i + 1]
      const v = tile.cross[i + 2]
      const other = peekTile(og)
      if (other?.pos && v < other.n) {
        pos.push(ux, uy, uz, other.pos[v * 3], other.pos[v * 3 + 1], other.pos[v * 3 + 2])
        added++
      } else {
        const om = this.index.galaxies.get(og)
        if (om) {
          pos.push(ux, uy, uz, ux + (om.x - ux) * 0.5, uy + (om.y - uy) * 0.5, uz + (om.z - uz) * 0.5)
          added++
        }
      }
    }
    if (pos.length === 0) return
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
    // ego は本数有界(EGO_CAP)のため加算を維持しつつ、輝度は抑える。
    const mat = new THREE.LineBasicMaterial({ color: 0xfff3b0, transparent: true, opacity: 0.6, depthWrite: false, blending: THREE.AdditiveBlending })
    this.egoLines = new THREE.LineSegments(geo, mat)
    this.egoLines.frustumCulled = false
    this.egoLines.renderOrder = 8
    this.group.add(this.egoLines)
  }

  // 現在の保持エントリ数を返す。
  get entryCount(): number {
    return this.entries.size
  }
}
