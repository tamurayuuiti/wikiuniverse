// src/three/focusLayer.ts
// 潜入銀河のスケール分離描画層(記事星+内部リンク+隣接リング+殻)を管理する。
//
// 責務:
// - 銀河内部を FOCUS_R 基準へ正規化して描画する(nested-scale の中核)
// - 銀河の外側へ隣接銀河リング/親マクロ殻/宇宙背景をコンテキスト配置する
// - 記事点/リングスプライトのレイキャスト提供
//
// 注意:
// - 座標はタイルの物理座標を「銀河中心-origin・s_in 倍」したローカル枠であり、
//   他レベルの座標系と連続ではない(スケール分離の設計意図)。
// - enter は非同期(タイル取得)。leave は同期で全リソースを破棄する。

import * as THREE from 'three'
import type { Bootstrap, Tile } from '@/types/catalog'
import { HALO_TEX, labelTex, macroColor, starMaterial } from './textures'

// 潜入銀河ボールの表示半径(ビュー単位)。
export const FOCUS_R = 320
// 隣接リングの半径。
export const R_CTX = FOCUS_R * 2.35
// 描画する実クロスリンクアークの上限。
const CROSS_ARC_CAP = 1500
// 内部リンク描画の上限本数(超えると間引く)。
const EDGE_CAP = 150000

// position 配列の非有限値を 0 置換して報告する(NaN は boundingSphere/picking を壊す)。
function sanitizePos(name: string, g: number, arr: Float32Array): void {
  let bad = 0
  for (let i = 0; i < arr.length; i++) {
    if (!Number.isFinite(arr[i])) { arr[i] = 0; bad++ }
  }
  if (bad) console.warn(`[focus] galaxy ${g}: sanitized ${bad} non-finite values in ${name}`)
}

// 潜入オプション。
export interface FocusOptions {
  zK: number
  showEdges: boolean
  showCross: boolean
  showShells: boolean
  showLabels: boolean
  pr: number
}

// 潜入層。
export class FocusLayer {
  readonly group = new THREE.Group()
  private objs: THREE.Object3D[] = []
  private ringSprites: THREE.Sprite[] = []
  private artPts: THREE.Points | null = null
  private tile: Tile | null = null
  private sIn = 1
  private centerRaw = new THREE.Vector3()
  visible = false

  constructor() {
    this.group.visible = false
  }

  // 銀河 g へ潜入する描画オブジェクトを構築する。隣接集計を返す。
  enter(g: number, tile: Tile, b: Bootstrap, opts: FocusOptions): { g: number; cnt: number }[] {
    this.leave()
    this.tile = tile
    this.visible = true
    this.group.visible = true
    const bg = b.galaxies[g]
    const rg = Math.max(bg.r, 0.8)
    this.sIn = FOCUS_R / rg
    // 生(非圧縮)中心を保持し、z 圧縮は local 変換時にのみ適用する。
    this.centerRaw.set(bg.x, bg.y, bg.z)

    // 記事星: ピクセルサイズ+次数連動の明度
    const deg = new Uint16Array(tile.n)
    for (let i = 0; i < tile.ne; i++) { deg[tile.edges[2 * i]]++; deg[tile.edges[2 * i + 1]]++ }
    const posA = new Float32Array(tile.n * 3)
    const szA = new Float32Array(tile.n)
    const colA = new Float32Array(tile.n * 3)
    const base = macroColor(bg.macroId)
    const c2 = new THREE.Color()
    for (let i = 0; i < tile.n; i++) {
      posA.set(this.local(i, opts.zK), i * 3)
      szA[i] = opts.pr * Math.min(2.6 + deg[i] * 0.35, 9)
      c2.copy(base).offsetHSL(0, 0, Math.min(0.35, deg[i] / 60))
      colA.set([c2.r, c2.g, c2.b], i * 3)
    }
    sanitizePos('article-pos', g, posA)
    const pg = new THREE.BufferGeometry()
    pg.setAttribute('position', new THREE.BufferAttribute(posA, 3))
    pg.setAttribute('aSize', new THREE.BufferAttribute(szA, 1))
    pg.setAttribute('aColor', new THREE.BufferAttribute(colA, 3))
    this.artPts = new THREE.Points(pg, starMaterial())
    this.add(this.artPts)

    // 内部リンク(上限間引き)
    if (opts.showEdges && tile.ne) {
      const stride = Math.max(1, Math.floor(tile.ne / EDGE_CAP))
      const lp: number[] = []
      for (let i = 0; i < tile.ne; i += stride) {
        lp.push(...this.local(tile.edges[2 * i], opts.zK), ...this.local(tile.edges[2 * i + 1], opts.zK))
      }
      const larr = new Float32Array(lp)
      sanitizePos('internal-lines', g, larr)
      const lg = new THREE.BufferGeometry()
      lg.setAttribute('position', new THREE.BufferAttribute(larr, 3))
      this.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({
        color: 0x2a4a66, transparent: true, opacity: 0.3,
        blending: THREE.AdditiveBlending, depthWrite: false })))
    }

    // 隣接リング(コンテキスト)
    const nb = new Map<number, number>()
    for (let i = 0; i < tile.nx; i += 3) nb.set(tile.cross[i + 1], (nb.get(tile.cross[i + 1]) || 0) + 1)
    const nbs = [...nb.entries()].sort((a, b2) => b2[1] - a[1]).slice(0, 16)
    const ringPos = new Map<number, THREE.Vector3>()
    nbs.forEach(([og, cnt], k) => {
      const o = b.galaxies[og]
      const d = new THREE.Vector3(o.x - this.centerRaw.x, o.y - this.centerRaw.y, (o.z - this.centerRaw.z) * opts.zK)
      if (d.lengthSq() < 1e-9) d.set(Math.cos(k * 2.4), Math.sin(k * 2.4), 0)
      d.normalize().multiplyScalar(R_CTX)
      ringPos.set(og, d)
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({
        map: HALO_TEX, color: macroColor(o.macroId), transparent: true, opacity: 0.85,
        depthWrite: false, blending: THREE.AdditiveBlending }))
      const sz = 26 + Math.log1p(cnt) * 16
      sp.scale.set(sz, sz, 1)
      sp.position.copy(d)
      sp.userData.g = og
      this.add(sp)
      this.ringSprites.push(sp)
      if (opts.showLabels) {
        const [t, ar] = labelTex((o.name || `#${og}`).slice(0, 16))
        const lb = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, opacity: 0.8, depthWrite: false }))
        lb.scale.set(15 * ar, 15, 1)
        lb.position.copy(d).multiplyScalar(1.12)
        this.add(lb)
      }
    })

    // 実クロスリンクの曲線アーク(記事→リング点)
    if (opts.showCross) {
      const xp: number[] = []
      let cnt2 = 0
      for (let i = 0; i < tile.nx && cnt2 < CROSS_ARC_CAP; i += 3) {
        const rp = ringPos.get(tile.cross[i + 1])
        if (!rp) continue
        const a = new THREE.Vector3(...this.local(tile.cross[i], opts.zK))
        const mid = a.clone().add(rp).multiplyScalar(0.5).multiplyScalar(1.12)
        const pts = new THREE.QuadraticBezierCurve3(a, mid, rp).getPoints(8)
        for (let k2 = 1; k2 < pts.length; k2++) {
          xp.push(pts[k2 - 1].x, pts[k2 - 1].y, pts[k2 - 1].z, pts[k2].x, pts[k2].y, pts[k2].z)
        }
        cnt2++
      }
      if (xp.length) {
        const xarr = new Float32Array(xp)
        sanitizePos('cross-arcs', g, xarr)
        const xg = new THREE.BufferGeometry()
        xg.setAttribute('position', new THREE.BufferAttribute(xarr, 3))
        this.add(new THREE.LineSegments(xg, new THREE.LineBasicMaterial({
          color: 0xbb55bb, transparent: true, opacity: 0.34,
          blending: THREE.AdditiveBlending, depthWrite: false })))
      }
    }

    // 親マクロ殻+宇宙背景殻
    if (opts.showShells) {
      this.add(new THREE.LineSegments(
        new THREE.WireframeGeometry(new THREE.SphereGeometry(R_CTX * 1.45, 18, 12)),
        new THREE.LineBasicMaterial({ color: 0x334455, transparent: true, opacity: 0.1 })))
      this.add(new THREE.LineSegments(
        new THREE.WireframeGeometry(new THREE.SphereGeometry(R_CTX * 2.6, 14, 10)),
        new THREE.LineBasicMaterial({ color: 0x222233, transparent: true, opacity: 0.05 })))
    }
    return nbs.map(([og, cnt]) => ({ g: og, cnt }))
  }

  // 記事 i のローカル座標を返す(z 圧縮はここで一貫適用)。
  private local(i: number, zK: number): [number, number, number] {
    const t = this.tile as Tile
    return [
      (t.pos[3 * i] - this.centerRaw.x) * this.sIn,
      (t.pos[3 * i + 1] - this.centerRaw.y) * this.sIn,
      (t.pos[3 * i + 2] - this.centerRaw.z) * zK * this.sIn,
    ]
  }

  // 全描画オブジェクトを破棄して宇宙ビューへ戻す。
  leave(): void {
    this.objs.forEach(o => {
      const anyO = o as THREE.Mesh
      anyO.geometry?.dispose()
      const mat = anyO.material as THREE.Material | undefined
      mat?.dispose()
      this.group.remove(o)
    })
    this.objs = []
    this.ringSprites = []
    this.artPts = null
    this.tile = null
    this.visible = false
    this.group.visible = false
  }

  private add(o: THREE.Object3D): void {
    this.group.add(o)
    this.objs.push(o)
  }

  // 現在潜入中のタイルを返す(未潜入なら null)。
  cur(): Tile | null {
    return this.tile
  }

  // 記事点へのレイキャスト。local index を返す。
  pickArticle(ray: THREE.Raycaster): number | null {
    if (!this.artPts) return null
    ray.params.Points = { threshold: FOCUS_R / 90 }
    const h = ray.intersectObject(this.artPts)
    return h.length ? (h[0].index ?? null) : null
  }

  // リング隣接スプライトへのレイキャスト。銀河 id を返す。
  pickRing(ray: THREE.Raycaster): number | null {
    const h = ray.intersectObjects(this.ringSprites)
    return h.length ? (h[0].object.userData.g as number) : null
  }

  // 記事 local の位置ベクトル(ローカル枠)を返す(記事リンク強調用)。
  articlePoint(local: number, zK: number): THREE.Vector3 {
    return new THREE.Vector3(...this.local(local, zK))
  }

  // 内部リンクのうち local に接続するものを曲線なし線分で返す。
  internalLinesOf(local: number, zK: number): THREE.LineSegments | null {
    const t = this.tile
    if (!t) return null
    const lp: number[] = []
    for (let i = 0; i < t.ne; i++) {
      const a = t.edges[2 * i], b = t.edges[2 * i + 1]
      if (a === local || b === local) lp.push(...this.local(a, zK), ...this.local(b, zK))
    }
    if (!lp.length) return null
    const larr = new Float32Array(lp)
    sanitizePos('ego-internal', 0, larr)
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(larr, 3))
    const ls = new THREE.LineSegments(g, new THREE.LineBasicMaterial({
      color: 0x88ddff, transparent: true, opacity: 0.95,
      blending: THREE.AdditiveBlending, depthWrite: false }))
    this.add(ls)
    return ls
  }

  // 記事 local のクロスリンク先アークを描画し、target 一覧を返す。
  async crossArcsOf(local: number, zK: number,
    tiles: (g: number) => Promise<Tile>): Promise<{ g: number; local: number; title: string }[]> {
    const t = this.tile
    if (!t) return []
    const targets: { g: number; local: number; title: string }[] = []
    for (let i = 0; i < t.nx; i += 3) {
      if (t.cross[i] !== local) continue
      const og = t.cross[i + 1], ov = t.cross[i + 2]
      let ot: Tile
      try { ot = await tiles(og) } catch { continue }
      targets.push({ g: og, local: ov, title: ot.titles[ov] })
    }
    const rp = new Map<number, THREE.Vector3>()
    this.ringSprites.forEach(sp => rp.set(sp.userData.g as number, sp.position))
    const xp: number[] = []
    targets.forEach(tg => {
      const r = rp.get(tg.g)
      if (!r) return
      const a = this.articlePoint(local, zK)
      const mid = a.clone().add(r).multiplyScalar(0.5).multiplyScalar(1.1)
      const pts = new THREE.QuadraticBezierCurve3(a, mid, r.clone()).getPoints(8)
      for (let k = 1; k < pts.length; k++) {
        xp.push(pts[k - 1].x, pts[k - 1].y, pts[k - 1].z, pts[k].x, pts[k].y, pts[k].z)
      }
    })
    if (xp.length) {
      const xarr = new Float32Array(xp)
      sanitizePos('ego-cross', 0, xarr)
      const g3 = new THREE.BufferGeometry()
      g3.setAttribute('position', new THREE.BufferAttribute(xarr, 3))
      this.add(new THREE.LineSegments(g3, new THREE.LineBasicMaterial({
        color: 0xff66cc, transparent: true, opacity: 0.95,
        blending: THREE.AdditiveBlending, depthWrite: false })))
    }
    return targets
  }
}
