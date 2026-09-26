// src/three/universeLayer.ts
// 宇宙ビュー(マクロ塊+銀河ハロー+バンドル+dust)の描画層を管理する。
//
// 責務:
// - 毎フレームの画面 px 計算(px/dist 配列の公開=LOD 制御器への入力)
// - 銀河ハロー/コアの最小角サイズ clamp と StarField α 連動の減衰
// - マクロ塊スプライト(遠景でマクロを「一つの塊」へ受け渡す役)
// - 銀河ハローへのレイキャスト提供
//
// 注意:
// - 加算ブレンドでは色乗算が α 乗算と等価であるため、フェードは setColorAt で行う。
// - z 圧縮は物理座標を変更せず描画時の z 乗算のみで行う。

import * as THREE from 'three'
import type { Bootstrap } from '@/types/catalog'
import { curvedLines } from './curves'
import { screenPx, smoothstep, LOD } from './lod'
import { HALO_TEX, labelTex, macroColor } from './textures'

// 銀河ハローの最小ピクセルサイズ(視認性保証)。
const HALO_MIN_PX = 4.5
// dust ハローの最小ピクセルサイズ。
const DUST_MIN_PX = 1.2
// マクロ殻の最小ピクセルサイズ。
const MACRO_MIN_PX = 9
// マクロ塊スプライトの最小ピクセルサイズ。
const MACRO_CLUMP_MIN_PX = 7

// 宇宙ビュー描画層。
export class UniverseLayer {
  readonly group = new THREE.Group()
  // 毎フレーム計算する銀河の画面 px 半径とカメラ距離(LOD 制御器への入力)。
  readonly px: Float32Array
  readonly dist: Float32Array
  private halo: THREE.InstancedMesh
  private coreI: THREE.InstancedMesh
  private haloR: Float32Array
  private coreR: Float32Array
  private haloCol: Float32Array
  private macroGroup = new THREE.Group()
  private labelGroup = new THREE.Group()
  private bundleGroup = new THREE.Group()
  private macroObjs: { obj: THREE.LineSegments; r: number }[] = []
  private macroClumps: { sp: THREE.Sprite; r: number }[] = []
  private dust: THREE.Points | null = null
  private b: Bootstrap
  private zK = 1
  private m4 = new THREE.Matrix4()
  private v = new THREE.Vector3()
  private c = new THREE.Color()

  constructor(b: Bootstrap) {
    this.b = b
    const G = b.galaxies.length
    this.px = new Float32Array(G)
    this.dist = new Float32Array(G)
    this.haloR = new Float32Array(G)
    this.coreR = new Float32Array(G)
    this.haloCol = new Float32Array(G * 3)
    this.halo = new THREE.InstancedMesh(
      new THREE.SphereGeometry(1, 12, 9),
      new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.14, blending: THREE.AdditiveBlending, depthWrite: false }),
      G)
    this.coreI = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 10, 8), new THREE.MeshBasicMaterial(), G)
    this.group.add(this.halo, this.coreI, this.macroGroup, this.labelGroup, this.bundleGroup)
    this.paintGalaxies()
    this.buildMacros()
    this.buildBundles()
    this.buildDust()
  }

  // 銀河ハロー/コアの基底サイズと色を設定する(基底色はフェード計算用に保持)。
  private paintGalaxies(): void {
    this.b.galaxies.forEach((g, i) => {
      const r = Math.max(g.r, 0.8)
      this.haloR[i] = r * 1.55
      this.coreR[i] = r * 0.3
      const c = g.cls === 1 ? new THREE.Color(0x666666) : g.cls === 2 ? new THREE.Color(0x222222) : macroColor(g.macroId)
      this.haloCol.set([c.r, c.g, c.b], i * 3)
      this.m4.makeScale(this.haloR[i], this.haloR[i], this.haloR[i])
      this.m4.setPosition(g.x, g.y, g.z * this.zK)
      this.halo.setMatrixAt(i, this.m4)
      this.halo.setColorAt(i, c)
      this.m4.makeScale(this.coreR[i], this.coreR[i], this.coreR[i])
      this.m4.setPosition(g.x, g.y, g.z * this.zK)
      this.coreI.setMatrixAt(i, this.m4)
      this.coreI.setColorAt(i, g.cls === 1 ? new THREE.Color(0x999999) : g.cls === 2 ? new THREE.Color(0x333333) : c)
    })
    this.halo.instanceMatrix.needsUpdate = true
    this.coreI.instanceMatrix.needsUpdate = true
    if (this.halo.instanceColor) this.halo.instanceColor.needsUpdate = true
    if (this.coreI.instanceColor) this.coreI.instanceColor.needsUpdate = true
  }

  // マクロ殻・塊スプライト・上位ラベルを生成する。
  private buildMacros(): void {
    this.macroGroup.clear()
    this.labelGroup.clear()
    this.macroObjs = []
    this.macroClumps = []
    this.b.macros.forEach(m => {
      if (m.n <= 1) return
      const w = new THREE.LineSegments(
        new THREE.WireframeGeometry(new THREE.SphereGeometry(m.r, 12, 8)),
        new THREE.LineBasicMaterial({ color: 0x334455, transparent: true, opacity: 0.07 }))
      w.position.set(m.x, m.y, m.z * this.zK)
      this.macroGroup.add(w)
      this.macroObjs.push({ obj: w, r: m.r })
      // 遠景でマクロを「一つの塊」へ受け渡す加算スプライト。
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({
        map: HALO_TEX, color: 0x6688aa, transparent: true, opacity: 0.4,
        depthWrite: false, blending: THREE.AdditiveBlending }))
      sp.position.set(m.x, m.y, m.z * this.zK)
      this.macroGroup.add(sp)
      this.macroClumps.push({ sp, r: m.r })
    })
    this.b.macros.slice().sort((a, b2) => b2.n - a.n).slice(0, 10).forEach(m => {
      const [t, ar] = labelTex((m.rep || `macro#${m.id}`).slice(0, 14))
      const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, transparent: true, opacity: 0.7, depthWrite: false }))
      sp.scale.set(40 * ar, 40, 1)
      sp.position.set(m.x, m.y + m.r * this.zK * 1.05, m.z * this.zK)
      this.labelGroup.add(sp)
    })
  }

  // マクロ/銀河ペアの曲線バンドルを生成する。
  private buildBundles(): void {
    this.bundleGroup.clear()
    const mv = (i: number) => new THREE.Vector3(this.b.macros[i].x, this.b.macros[i].y, this.b.macros[i].z * this.zK)
    const gv = (i: number) => new THREE.Vector3(this.b.galaxies[i].x, this.b.galaxies[i].y, this.b.galaxies[i].z * this.zK)
    this.bundleGroup.add(curvedLines(this.b.macroPairs.slice(0, 400).map(p => ({ a: mv(p.a), b: mv(p.b), w: p.w })), 0x557799, 0.5))
    this.bundleGroup.add(curvedLines(this.b.galaxyPairs.slice(0, 6000).map(p => ({ a: gv(p.a), b: gv(p.b), w: p.w })), 0x335577, 0.15))
  }

  // dust(孤立記事銀河)の点群を生成する。
  private buildDust(): void {
    const idx = this.b.galaxies.map((g, i) => (g.cls === 2 ? i : -1)).filter(i => i >= 0)
    const p = new Float32Array(idx.length * 3)
    idx.forEach((gi, k) => {
      const g = this.b.galaxies[gi]
      p.set([g.x, g.y, g.z * this.zK], k * 3)
    })
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(p, 3))
    this.dust = new THREE.Points(geo, new THREE.PointsMaterial({
      size: 1.6, sizeAttenuation: false, transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending, color: 0x445566, opacity: 0.55, map: HALO_TEX }))
    this.group.add(this.dust)
  }

  // z 圧縮率を変更して全オブジェクトへ反映する。
  setZ(k: number): void {
    this.zK = k
    this.paintGalaxies()
    this.buildMacros()
    this.buildBundles()
    if (this.dust) {
      const attr = this.dust.geometry.attributes.position as THREE.BufferAttribute
      let k2 = 0
      this.b.galaxies.forEach(g => {
        if (g.cls !== 2) return
        attr.array[k2 * 3 + 2] = g.z * k
        k2++
      })
      attr.needsUpdate = true
    }
  }

  // トグルを反映する。
  setToggle(key: 'cross' | 'shells' | 'labels' | 'dust', on: boolean): void {
    if (key === 'cross') this.bundleGroup.visible = on
    if (key === 'shells') this.macroGroup.visible = on
    if (key === 'labels') this.labelGroup.visible = on
    if (key === 'dust' && this.dust) this.dust.visible = on
  }

  // px/dist を計算し、fade(星の出現 α)を連動させて見かけを更新する。
  updateDynamicSizes(camera: THREE.Camera, innerH: number, fade: Float32Array): void {
    const fov = (camera as THREE.PerspectiveCamera).fov
    let upd = false
    for (let i = 0; i < this.b.galaxies.length; i++) {
      const g = this.b.galaxies[i]
      this.v.set(g.x, g.y, g.z * this.zK)
      const d = camera.position.distanceTo(this.v)
      this.dist[i] = d
      this.px[i] = screenPx(this.haloR[i], d, innerH, fov)
      // 塊←→星の受け渡し: 星が出た分だけハローをリムへ減衰する。
      const f = 1 - 0.88 * fade[i]
      this.c.setRGB(this.haloCol[i * 3] * f, this.haloCol[i * 3 + 1] * f, this.haloCol[i * 3 + 2] * f)
      this.halo.setColorAt(i, this.c)
      const minH = screenPx(1, d, innerH, fov) * (g.cls === 2 ? DUST_MIN_PX : HALO_MIN_PX)
      const eh = Math.max(this.haloR[i], minH)
      const ec = Math.max(this.coreR[i], minH * 0.45)
      this.m4.makeScale(eh, eh, eh); this.m4.setPosition(this.v); this.halo.setMatrixAt(i, this.m4)
      this.m4.makeScale(ec, ec, ec); this.m4.setPosition(this.v); this.coreI.setMatrixAt(i, this.m4)
      upd = true
    }
    if (upd) {
      this.halo.instanceMatrix.needsUpdate = true
      this.coreI.instanceMatrix.needsUpdate = true
      if (this.halo.instanceColor) this.halo.instanceColor.needsUpdate = true
    }
    this.macroObjs.forEach(({ obj, r }) => {
      const d = camera.position.distanceTo(obj.position)
      const s = Math.max(r, screenPx(1, d, innerH, fov) * MACRO_MIN_PX) / r
      obj.scale.set(s, s, s)
    })
    // マクロ塊スプライト: 銀河が塊へ溶ける距離でマクロ塊が主役になる(自己相似)。
    this.macroClumps.forEach(({ sp, r }) => {
      const d = camera.position.distanceTo(sp.position)
      const pxM = screenPx(r, d, innerH, fov)
      const a = (1 - smoothstep(LOD.emerge, LOD.resolve, pxM)) * 0.5 + 0.06
      ;(sp.material as THREE.SpriteMaterial).opacity = a
      const sc = Math.max(r * 2.0, screenPx(1, d, innerH, fov) * MACRO_CLUMP_MIN_PX * 2.2)
      sp.scale.set(sc, sc, 1)
    })
  }

  // 銀河ハローへのレイキャスト。hit した銀河 id を返す。
  pick(ray: THREE.Raycaster): number | null {
    const hit = ray.intersectObject(this.halo)
    return hit.length ? hit[0].instanceId ?? null : null
  }
}
