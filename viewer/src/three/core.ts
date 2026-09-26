// src/three/core.ts
// 描画コア。シーン/カメラ/ループ/ picking / コマンド実装を統括する。
//
// 責務:
// - UniverseLayer / FocusLayer の生命周期とカメラ遷移の管理
// - pointer 入力(pick/hover)のレイキャストと store への反映
// - state/commands への実装登録(React 側からの命令受信)
//
// 注意:
// - React の描画パスへ一切依存しない(requestAnimationFrame ループは独立)。
// - タイル取得以外の重い処理をループ内で行わない。

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import type { Bootstrap } from '@/types/catalog'
import { loadBootstrap } from '@/data/bootstrap'
import { TileCache } from '@/data/tileCache'
import { registerCommands } from '@/state/commands'
import { getState, setState } from '@/state/store'
import { FocusLayer, FOCUS_R } from './focusLayer'
import { StarField } from './starField'
import { UniverseLayer } from './universeLayer'

// fly-to アニメーションの状態。
interface FlyAnim {
  t0: number
  dur: number
  p0: THREE.Vector3
  p1: THREE.Vector3
  q0: THREE.Vector3
  q1: THREE.Vector3
}

// 描画コア本体。
export class ViewerCore {
  private scene = new THREE.Scene()
  private camera: THREE.PerspectiveCamera
  private renderer: THREE.WebGLRenderer
  private controls: OrbitControls
  private uni: UniverseLayer | null = null
  private stars: StarField | null = null
  private foc = new FocusLayer()
  private tiles = new TileCache()
  private b: Bootstrap | null = null
  private ray = new THREE.Raycaster()
  private mouse = new THREE.Vector2()
  private anim: FlyAnim | null = null
  private hoverT = 0
  private frames = 0
  private fpsT = performance.now()
  private fadeEl: HTMLElement | null

  constructor(canvas: HTMLCanvasElement, fadeEl: HTMLElement | null) {
    this.fadeEl = fadeEl
    this.scene.fog = new THREE.FogExp2(0x000000, 0.00009)
    this.camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.2, 500000)
    this.camera.position.set(0, -3600, 2100)
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' })
    this.renderer.setSize(innerWidth, innerHeight)
    this.controls = new OrbitControls(this.camera, canvas)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.08
    this.scene.add(this.foc.group)
    this.bindPointer(canvas)
    registerCommands({
      enterGalaxy: g => { void this.enterGalaxy(g, true) },
      leave: () => this.leaveGalaxy(true),
      setZ: k => this.setZ(k),
      toggle: key => this.toggle(key),
      search: q => this.search(q),
      jumpToArticle: (g, local) => { void this.enterGalaxy(g, true).then(() => this.showArticle(local)) },
    })
  }

  // bootstrap 取得と宇宙層構築を行う。
  async init(): Promise<void> {
    this.b = await loadBootstrap()
    this.tiles.setVersion(this.b.meta.generated_at)
    this.uni = new UniverseLayer(this.b)
    this.scene.add(this.uni.group)
    this.stars = new StarField(this.tiles, this.b)
    this.scene.add(this.stars.group)
    this.addStarfield()
    setState({ ready: true })
    this.loop()
  }

  // 背景スターフィールドを追加する(深度の手がかり)。
  private addStarfield(): void {
    const N = 2600
    const p = new Float32Array(N * 3)
    const v = new THREE.Vector3()
    for (let i = 0; i < N; i++) {
      v.set(Math.random() * 2 - 1, Math.random() * 2 - 1, Math.random() * 2 - 1).normalize()
        .multiplyScalar(50000 + Math.random() * 50000)
      p.set([v.x, v.y, v.z], i * 3)
    }
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.BufferAttribute(p, 3))
    this.scene.add(new THREE.Points(g, new THREE.PointsMaterial({
      size: 2.2, sizeAttenuation: false, transparent: true, depthWrite: false,
      blending: THREE.AdditiveBlending, color: 0x8899bb, opacity: 0.5 })))
  }

  // 遷移時の瞬きを発生させる(離散遷移の知覚緩和)。
  private fade(): void {
    if (!this.fadeEl) return
    this.fadeEl.style.opacity = '1'
    setTimeout(() => { if (this.fadeEl) this.fadeEl.style.opacity = '0' }, 60)
  }

  // カメラの fly-to アニメーションを開始する。
  private flyTo(target: THREE.Vector3, dist: number): void {
    const dir = this.camera.position.clone().sub(this.controls.target).normalize()
    this.anim = {
      t0: performance.now(), dur: 800,
      p0: this.camera.position.clone(), p1: target.clone().addScaledVector(dir, dist),
      q0: this.controls.target.clone(), q1: target.clone(),
    }
  }

  // 銀河へ潜入する。タイル取得を伴う。
  async enterGalaxy(g: number, fly: boolean): Promise<void> {
    const b = this.b
    if (!b || getState().focus === g) return
    if (getState().focus >= 0) this.leaveGalaxy(false)
    this.fade()
    setState({ panel: { kind: 'loading', g }, stage: 'galaxy', focus: g })
    const tile = await this.tiles.get(g)
    const st = getState()
    const neighbors = this.foc.enter(g, tile, b, {
      zK: st.zK, showEdges: st.toggles.edges, showCross: st.toggles.cross,
      showShells: st.toggles.shells, showLabels: st.toggles.labels,
      pr: this.renderer.getPixelRatio(),
    })
    if (this.uni) this.uni.group.visible = false
    this.scene.fog = new THREE.FogExp2(0x000000, 1.1 / (FOCUS_R * 7))
    if (fly) {
      this.controls.target.set(0, 0, 0)
      this.camera.position.set(0, -FOCUS_R * 3.0, FOCUS_R * 1.7)
      this.anim = null
    }
    setState({ panel: { kind: 'galaxy', g, neighbors } })
  }

  // 宇宙ビューへ戻る。
  leaveGalaxy(back: boolean): void {
    if (getState().focus < 0) return
    this.fade()
    this.foc.leave()
    if (this.uni) this.uni.group.visible = true
    this.scene.fog = new THREE.FogExp2(0x000000, 0.00009)
    setState({ stage: 'universe', focus: -1, panel: { kind: 'universe' } })
    if (back) this.flyTo(new THREE.Vector3(0, 0, 0), 3600)
  }

  // 記事の実リンク強調と panel 表示を行う。
  async showArticle(local: number): Promise<void> {
    const st = getState()
    if (st.focus < 0) return
    this.foc.internalLinesOf(local, st.zK)
    const targets = await this.foc.crossArcsOf(local, st.zK, g => this.tiles.get(g))
    const t = await this.tiles.get(st.focus)
    const title = t.titles[local] ?? '?'
    let deg = 0
    for (let i = 0; i < t.ne; i++) if (t.edges[2 * i] === local || t.edges[2 * i + 1] === local) deg++
    setState({ panel: { kind: 'article', title, deg, targets } })
  }

  // z 圧縮率を変更する(全層へ反映、潜入中は再潜入)。
  private setZ(k: number): void {
    setState({ zK: k })
    this.uni?.setZ(k)
    this.stars?.clear()
    if (getState().focus >= 0) {
      const g = getState().focus
      this.leaveGalaxy(false)
      void this.enterGalaxy(g, false)
    }
  }

  // 表示トグルを切り替える。
  private toggle(key: 'edges' | 'cross' | 'shells' | 'labels' | 'dust' | 'stars'): void {
    const t = { ...getState().toggles }
    t[key] = !t[key]
    setState({ toggles: t })
    if (key === 'stars') return
    if (key !== 'edges' && key !== 'cross' && this.uni) this.uni.setToggle(key, t[key])
    if (key === 'cross' && this.uni) this.uni.setToggle('cross', t.cross)
    if (getState().focus >= 0) {
      const g = getState().focus
      this.leaveGalaxy(false)
      void this.enterGalaxy(g, false)
    }
  }

  // 銀河名/マクロ名の部分一致検索とジャンプを行う。
  private search(q: string): void {
    const b = this.b
    if (!b) return
    const qq = q.trim().toLowerCase()
    if (!qq) return
    const gi = b.galaxies.findIndex(g => g.name && g.name.toLowerCase().includes(qq))
    if (gi >= 0) { void this.enterGalaxy(gi, true); return }
    const mi = b.macros.findIndex(m => m.rep && m.rep.toLowerCase().includes(qq))
    if (mi >= 0) {
      const m = b.macros[mi]
      this.flyTo(new THREE.Vector3(m.x, m.y - m.r * 2.4, m.z * getState().zK), m.r * 2.4)
    }
  }

  // pointer イベントを bind する(hover は 90ms スロットル)。
  private bindPointer(canvas: HTMLCanvasElement): void {
    canvas.addEventListener('pointermove', ev => {
      const now = performance.now()
      if (now - this.hoverT < 90) return
      this.hoverT = now
      this.setMouse(ev)
      this.ray.setFromCamera(this.mouse, this.camera)
      let text = ''
      const st = getState()
      if (st.focus >= 0) {
        const rg = this.foc.pickRing(this.ray)
        if (rg !== null && this.b) text = `${this.b.galaxies[rg].name || '#' + rg}  (jump)`
        if (!text) {
          const ai = this.foc.pickArticle(this.ray)
          if (ai !== null) text = this.focTitle(ai)
        }
      } else if (this.uni) {
        const sp = this.stars?.pick(this.ray)
        if (sp) {
          const t = this.tiles.peek(sp.g)
          text = t ? (t.titles[sp.local] ?? '') : ''
        }
        if (!text) {
          const gi = this.uni.pick(this.ray)
          if (gi !== null && this.b) {
            const g = this.b.galaxies[gi]
            text = `${g.name || 'galaxy #' + gi}  (${g.n.toLocaleString()} articles)`
          }
        }
      }
      setState({ hover: text ? { text, x: ev.clientX, y: ev.clientY } : null })
    })
    canvas.addEventListener('click', ev => {
      this.setMouse(ev)
      this.ray.setFromCamera(this.mouse, this.camera)
      const st = getState()
      if (st.focus >= 0) {
        const rg = this.foc.pickRing(this.ray)
        if (rg !== null) { void this.enterGalaxy(rg, true); return }
        const ai = this.foc.pickArticle(this.ray)
        if (ai !== null) void this.showArticle(ai)
        return
      }
      const sp = this.stars?.pick(this.ray)
      if (sp) {
        void this.enterGalaxy(sp.g, true).then(() => this.showArticle(sp.local))
        return
      }
      const gi = this.uni?.pick(this.ray)
      if (gi !== null && gi !== undefined) void this.enterGalaxy(gi, true)
    })
  }

  // 現在フォーカス中のタイルから記事タイトルを引く。
  private focTitle(local: number): string {
    const t = this.foc.cur()
    return t ? (t.titles[local] ?? '') : ''
  }

  private setMouse(ev: PointerEvent | MouseEvent): void {
    this.mouse.x = (ev.clientX / innerWidth) * 2 - 1
    this.mouse.y = -(ev.clientY / innerHeight) * 2 + 1
  }

  // メインループ。fly-to / 動的サイズ / fps 統計を処理する。
  private loop = (): void => {
    requestAnimationFrame(this.loop)
    const now = performance.now()
    if (this.anim) {
      const k = Math.min(1, (now - this.anim.t0) / this.anim.dur)
      const e = k * k * (3 - 2 * k)
      this.camera.position.lerpVectors(this.anim.p0, this.anim.p1, e)
      this.controls.target.lerpVectors(this.anim.q0, this.anim.q1, e)
      if (k >= 1) this.anim = null
    }
    if (getState().stage === 'universe' && this.uni && this.stars) {
      const st = getState()
      // px/dist を計算し、前フレームの fade でハロー減衰を適用する。
      this.uni.updateDynamicSizes(this.camera, innerHeight, this.stars.fadeArray())
      // 当フレームの LOD 同期(浮上/退避/α)を行う。
      this.stars.sync(this.uni.px, {
        zK: st.zK, pr: this.renderer.getPixelRatio(),
        showEdges: st.toggles.edges, enabled: st.toggles.stars,
      })
    }
    this.controls.update()
    this.renderer.render(this.scene, this.camera)
    this.frames++
    if (now - this.fpsT > 1000) {
      setState({ stats: { fps: this.frames, tiles: this.tiles.size } })
      this.frames = 0
      this.fpsT = now
    }
  }

  // リサイズへ追従する。
  resize(): void {
    this.camera.aspect = innerWidth / innerHeight
    this.camera.updateProjectionMatrix()
    this.renderer.setSize(innerWidth, innerHeight)
  }
}
