// src/three/core.ts
// 連続宇宙ビューアのコア(v6): 単一空間+距離駆動 LOD。離散 focus モードなし。
//
// 責務:
// - renderer(対数深度)/ scene(fog)/ camera / OrbitControls(慣性)の初期化
// - 測光パイプライン: ACES トーンマッピング+OutputPass、bloom は星コア専業
// - 毎フレーム: StarField 同期・塊の自己相似 px・エッジ tier α・ラベル LOD
// - 操作: hover(star>galaxy>macro)、click=fly-to または記事選択、Esc=親レベルへ上昇
// - z-compress スライダの再構成(recomposeArticles)
//
// 注意:
// - クリックによるテレポート/段階切替は行わない(理想形=連続性)。
// - selection は表示専用コンテキストであり、描画は全て距離/px 駆動。
// - 測光: エッジ/塊は通常ブレンド+露出(1/n)+ハブ抑制 ink(lod.ts EXPOSURE)で
//   有界化済み。bloom threshold はエッジ飽和輝度と星コア輝度の間に置き、
//   星コアのみ bloom させる。ACES がハイライトをロールオフさせ白飛びを最後に縛る。

import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js'
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js'
import type { BootstrapData, Macro, TileIndex } from '@/types/catalog'
import { useStore } from '@/state/store'
import { peekTile, fetchTile, setIndex, recomposeTiles } from '@/data/tileCache'
import { StarField } from './starField'
import { UniverseLayer } from './universeLayer'
import { makeStarSprite } from './textures'
import { macroBundleAlpha, galaxyBundleAlpha, zoomLabel } from './lod'

// display_class コードを日本語名へ変換する。
function displayClassName(code: number): string {
  return code === 1 ? 'medium' : code === 2 ? 'dust' : 'galaxy'
}

// home 視点(全体俯瞰)。
const HOME_POS = new THREE.Vector3(0, 620, 2400)
const HOME_TARGET = new THREE.Vector3(0, 0, 0)
// fly-to の所要時間(秒)。
const FLY_TIME = 1.35

// 共有一時ベクトル。
const tmpVec = new THREE.Vector3()

// 3D ビューアのコア。
export class ViewerCore {
  private renderer: THREE.WebGLRenderer
  private scene = new THREE.Scene()
  private camera: THREE.PerspectiveCamera
  private controls: OrbitControls
  private composer: EffectComposer
  private uni: UniverseLayer
  private stars: StarField
  private boot: BootstrapData
  private index: TileIndex
  private clock = new THREE.Clock()
  private time = 0
  private raf = 0
  private disposed = false
  private ndc = new THREE.Vector2()
  private pointer = { x: 0, y: 0, downX: 0, downY: 0, down: false }
  private flying: { t: number; fromPos: THREE.Vector3; toPos: THREE.Vector3; fromTgt: THREE.Vector3; toTgt: THREE.Vector3 } | null = null
  private fpsSmooth = 60
  private statThrottle = 0
  private zK = 1
  private onResizeBound = () => this.onResize()
  private onWheelBound = (e: WheelEvent) => this.onWheel(e)
  private onPointerDownBound = (e: PointerEvent) => this.onPointerDown(e)
  private onPointerMoveBound = (e: PointerEvent) => this.onPointerMove(e)
  private onPointerUpBound = (e: PointerEvent) => this.onPointerUp(e)
  private onKeyDownBound = (e: KeyboardEvent) => this.onKeyDown(e)
  private canvas: HTMLCanvasElement
  private unsub: (() => void) | null = null

  // canvas にビューアを構築する。
  constructor(canvas: HTMLCanvasElement, boot: BootstrapData, index: TileIndex) {
    this.canvas = canvas
    this.boot = boot
    this.index = index
    setIndex(index, boot.version)
    const el = document.getElementById('viewer-root')
    const w = el?.clientWidth ?? window.innerWidth
    const h = el?.clientHeight ?? window.innerHeight
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, logarithmicDepthBuffer: true, powerPreference: 'high-performance' })
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    this.renderer.setSize(w, h, false)
    this.renderer.setClearColor(0x03040a, 1)
    // 測光: ACES でハイライトをロールオフ(重ね描きの白飛びを最後の段で縛る)。
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping
    this.renderer.toneMappingExposure = 1.05
    this.scene.fog = new THREE.FogExp2(0x03040a, 0.00016)
    this.camera = new THREE.PerspectiveCamera(55, w / Math.max(h, 1), 0.1, 600000)
    this.camera.position.copy(HOME_POS)
    this.controls = new OrbitControls(this.camera, this.renderer.domElement)
    this.controls.enableDamping = true
    this.controls.dampingFactor = 0.075
    this.controls.rotateSpeed = 0.55
    this.controls.zoomSpeed = 0.9
    this.controls.minDistance = 1.5
    this.controls.maxDistance = 12000
    this.controls.target.copy(HOME_TARGET)
    // bloom(星コア専業: threshold はエッジ飽和輝度より上、強度は控えめ)。
    this.composer = new EffectComposer(this.renderer)
    this.composer.addPass(new RenderPass(this.scene, this.camera))
    this.composer.addPass(new UnrealBloomPass(new THREE.Vector2(w, h), 0.38, 0.42, 0.55))
    // 終端: トーンマッピング+sRGB 変換(これがないと線形値がそのまま出て白飛びする)。
    this.composer.addPass(new OutputPass())
    // レイヤ。
    const clumpTex = makeStarSprite(256, 1.9, 3.4)
    this.uni = new UniverseLayer(boot, index, clumpTex, '600 34px system-ui, sans-serif')
    this.scene.add(this.uni.group)
    this.stars = new StarField(index)
    this.scene.add(this.stars.group)
    // StarField コールバック。
    this.stars.onHover = hit => {
      if (hit) {
        useStore.setState({
          hover: { kind: 'article', gid: hit.g, local: hit.local, title: hit.title, x: this.pointer.x, y: this.pointer.y },
        })
      } else {
        const s = useStore.getState()
        if (s.hover?.kind === 'article') useStore.setState({ hover: null })
      }
    }
    // 初期パネル。
    useStore.setState({
      ready: true,
      zoomLabel: 'universe',
      panel: { kind: 'overview', galaxies: index.count, macros: boot.macros.length },
    })
    // イベント。
    window.addEventListener('resize', this.onResizeBound)
    canvas.addEventListener('wheel', this.onWheelBound, { passive: false })
    canvas.addEventListener('pointerdown', this.onPointerDownBound)
    canvas.addEventListener('pointermove', this.onPointerMoveBound)
    canvas.addEventListener('pointerup', this.onPointerUpBound)
    window.addEventListener('keydown', this.onKeyDownBound)
    // store 購読(z スライダ・ジャンプ要求)。
    this.unsub = useStore.subscribe((s, prev) => {
      if (s.zSquash !== prev.zSquash) this.applyZ(s.zSquash)
      if (s.jumpRequest && s.jumpRequest.seq !== prev.jumpRequest?.seq) {
        this.flyTo(new THREE.Vector3(...s.jumpRequest.pos), s.jumpRequest.dist)
      }
    })
    this.loop()
  }

  // メインループ。
  private loop = (): void => {
    if (this.disposed) return
    this.raf = requestAnimationFrame(this.loop)
    const dt = Math.min(this.clock.getDelta(), 0.05)
    this.time += dt
    if (dt > 0) this.fpsSmooth += (1 / dt - this.fpsSmooth) * 0.05
    const s = useStore.getState()
    // fly-to アニメーション(cubic ease in-out)。
    if (this.flying) {
      this.flying.t += dt / FLY_TIME
      const t = Math.min(this.flying.t, 1)
      const k = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
      this.camera.position.lerpVectors(this.flying.fromPos, this.flying.toPos, k)
      this.controls.target.lerpVectors(this.flying.fromTgt, this.flying.toTgt, k)
      if (t >= 1) this.flying = null
    }
    this.controls.update()
    // 距離 D からの表示コンテキスト(読み取り専用)。
    const D = this.camera.position.distanceTo(this.controls.target)
    const zl = zoomLabel(D)
    if (zl !== s.zoomLabel) useStore.setState({ zoomLabel: zl })
    // タイル px 集合(銀河クラスタ実 px)。
    const proj = this.projFactor()
    const tiles = new Map<number, number>()
    for (const g of this.boot.galaxies) {
      const d = this.camera.position.distanceTo(tmpVec.set(g.x, g.y, g.z))
      tiles.set(g.gid, (g.r * proj) / Math.max(d, 1e-6))
    }
    // StarField 同期(星・内部エッジ・クロスアーク・ego)。
    this.stars.group.visible = s.toggles.stars
    if (s.toggles.stars) this.stars.sync(tiles, dt, s.toggles.cross)
    // 骨格更新(塊 px・ラベル・フェード)。
    this.uni.update(this.camera, tiles, s.toggles.shells, s.toggles.labels)
    this.uni.tick(this.time)
    // エッジ tier α(距離帯)。
    this.uni.setBundleAlphas(macroBundleAlpha(D), galaxyBundleAlpha(D), s.toggles.edges)
    // HUD stats(250ms 間引き)。
    this.statThrottle += dt
    if (this.statThrottle > 0.25) {
      this.statThrottle = 0
      let emerged = 0
      for (const px of tiles.values()) if (px >= 9) emerged++
      const viewW = 2 * D * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)) * this.camera.aspect
      useStore.setState({
        stats: { fps: Math.round(this.fpsSmooth), emerged, tiles: this.stars.entryCount, zK: this.zK, viewWidthU: Math.round(viewW) },
      })
    }
    this.composer.render()
  }

  // proj 係数(px 計算用)。
  private projFactor(): number {
    const el = document.getElementById('viewer-root')
    const h = el?.clientHeight ?? window.innerHeight
    return h / (2 * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)))
  }

  // リサイズ処理。
  private onResize(): void {
    if (this.disposed) return
    const el = document.getElementById('viewer-root')
    const w = el?.clientWidth ?? window.innerWidth
    const h = el?.clientHeight ?? window.innerHeight
    this.renderer.setSize(w, h, false)
    this.composer.setSize(w, h)
    this.camera.aspect = w / Math.max(h, 1)
    this.camera.updateProjectionMatrix()
  }

  // wheel: fly-to を中断(ズーム自体は OrbitControls の乗算ドリー=対数的)。
  private onWheel(e: WheelEvent): void {
    e.preventDefault()
    this.flying = null
  }

  // z-compress スライダを適用する(キャッシュタイルの z を in-place 再構成)。
  private applyZ(k: number): void {
    this.zK = k
    recomposeTiles(k)
    this.stars.refreshPositions()
  }

  // pointerdown(クリック判定用)。
  private onPointerDown(e: PointerEvent): void {
    this.pointer.down = true
    this.pointer.downX = e.clientX
    this.pointer.downY = e.clientY
  }

  // pointermove(hover ピック: star > galaxy > macro)。
  private onPointerMove(e: PointerEvent): void {
    const rect = this.canvas.getBoundingClientRect()
    this.pointer.x = e.clientX - rect.left
    this.pointer.y = e.clientY - rect.top
    this.ndc.set((this.pointer.x / rect.width) * 2 - 1, -(this.pointer.y / rect.height) * 2 + 1)
    const s = useStore.getState()
    if (s.toggles.stars) {
      this.stars.pick(this.ndc, this.camera, 'hover')
      if (useStore.getState().hover?.kind === 'article') return
    }
    this.uniPickHover()
  }

  // 骨格の hover ピック(銀河→マクロ)。
  private uniPickHover(): void {
    const gal = this.uni.pickGalaxy(this.ndc, this.camera)
    if (gal) {
      const meta = this.index.galaxies.get(gal.gid)
      const bootG = this.boot.galaxies.find(x => x.gid === gal.gid)
      if (meta && bootG) {
        useStore.setState({
          hover: {
            kind: 'galaxy',
            gid: gal.gid,
            title: bootG.label,
            x: this.pointer.x,
            y: this.pointer.y,
            info: `n=${meta.n} cross=${meta.n_cross} macro=${meta.mid + 1}`,
          },
        })
        return
      }
    }
    const mac = this.uni.pickMacro(this.ndc, this.camera)
    if (mac) {
      const m = this.boot.macros[mac.mid]
      useStore.setState({
        hover: { kind: 'macro', gid: mac.mid, title: m.label, x: this.pointer.x, y: this.pointer.y, info: `銀河 ${m.n_galaxies} / 記事 ${m.n}` },
      })
      return
    }
    useStore.setState({ hover: null })
  }

  // pointerup(クリック=選択/fly-to)。
  private onPointerUp(e: PointerEvent): void {
    if (!this.pointer.down) return
    this.pointer.down = false
    const moved = Math.hypot(e.clientX - this.pointer.downX, e.clientY - this.pointer.downY)
    if (moved > 6) return
    const rect = this.canvas.getBoundingClientRect()
    this.ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1)
    // 1) 星(浮上済み記事)。クロージャ代入は TS が追跡しないためホルダ経由。
    if (useStore.getState().toggles.stars) {
      const starHit: { v: { g: number; local: number; title: string } | null } = { v: null }
      this.stars.onClick = hit => {
        starHit.v = hit
      }
      this.stars.pick(this.ndc, this.camera, 'click')
      if (starHit.v) {
        this.selectArticle(starHit.v.g, starHit.v.local, starHit.v.title)
        return
      }
    }
    // 2) 銀河クラスタ → その銀河へ fly-to(連続接近、到着時に星が湧く)。
    const gal = this.uni.pickGalaxy(this.ndc, this.camera)
    if (gal) {
      const meta = this.index.galaxies.get(gal.gid)
      const g = this.boot.galaxies.find(x => x.gid === gal.gid)
      if (meta && g) {
        this.stars.clearEgo()
        useStore.setState({ selection: { kind: 'galaxy', gid: gal.gid } })
        this.showGalaxyPanel(gal.gid)
        this.flyTo(new THREE.Vector3(g.x, g.y, g.z), Math.max(g.r * 5, 12))
        return
      }
    }
    // 3) マクロクラスタ → その銀河団へ fly-to。
    const mac = this.uni.pickMacro(this.ndc, this.camera)
    if (mac) {
      const m = this.boot.macros[mac.mid]
      this.flyTo(new THREE.Vector3(m.x, m.y, m.z), Math.max(m.r * 2.4, 60))
      return
    }
    // 4) 空 → 選択解除。
    this.stars.clearEgo()
    useStore.setState({
      selection: { kind: 'none' },
      panel: { kind: 'overview', galaxies: this.index.count, macros: this.boot.macros.length },
    })
  }

  // 記事を選択する(ego 網+パネル)。
  private selectArticle(g: number, local: number, title: string): void {
    this.stars.showEgo(g, local)
    const meta = this.index.galaxies.get(g)
    const bootG = this.boot.galaxies.find(x => x.gid === g)
    const tile = peekTile(g)
    let deg = 0
    const targets: { gid: number; title: string }[] = []
    if (tile && local < tile.n) {
      for (let i = 0; i < tile.eSrc.length; i++) {
        if (tile.eSrc[i] === local || tile.eDst[i] === local) deg++
      }
      const seen = new Set<number>()
      for (let i = 0; i < tile.cross.length && targets.length < 12; i += 3) {
        if (tile.cross[i] !== local) continue
        const og = tile.cross[i + 1]
        if (seen.has(og)) continue
        seen.add(og)
        const ob = this.boot.galaxies.find(x => x.gid === og)
        const ometa = this.index.galaxies.get(og)
        targets.push({ gid: og, title: ob?.label ?? (ometa ? `galaxy#${og}` : `?${og}`) })
      }
    }
    useStore.setState({
      selection: { kind: 'article', gid: g, local },
      panel: {
        kind: 'article',
        gid: g,
        local,
        galaxyTitle: bootG?.label ?? `galaxy#${g}`,
        title,
        deg,
        crossTargets: targets,
        macroTitle: meta ? this.boot.macros[meta.mid]?.label : undefined,
      },
    })
  }

  // 銀河パネルを表示する(ハブ記事はタイル到着後に非同期で補充)。
  private showGalaxyPanel(gid: number): void {
    const meta = this.index.galaxies.get(gid)
    const g = this.boot.galaxies.find(x => x.gid === gid)
    if (!meta || !g) return
    const macro = this.boot.macros[meta.mid]
    const base = {
      kind: 'galaxy' as const,
      gid,
      title: g.label,
      macroTitle: macro?.label ?? '',
      n: meta.n,
      nCross: meta.n_cross,
      displayClass: displayClassName(meta.display_class),
      hubTitles: [] as string[],
    }
    useStore.setState({ panel: base })
    fetchTile(gid)
      .then(tile => {
        if (!tile) return
        const st = useStore.getState()
        if (st.panel.kind !== 'galaxy' || st.panel.gid !== gid) return
        const order = [...Array(tile.n).keys()].sort((a, b) => tile.deg[b] - tile.deg[a]).slice(0, 14)
        useStore.setState({ panel: { ...base, hubTitles: order.map(i => tile.titles[i] ?? `local#${i}`) } })
      })
      .catch(() => {})
  }

  // 指定位置/距離へ fly-to する(連続飛行)。
  flyTo(pos: THREE.Vector3, dist: number): void {
    const dir = this.camera.position.clone().sub(this.controls.target)
    if (dir.lengthSq() < 1e-6) dir.set(0, 0.35, 1)
    dir.normalize()
    dir.y = Math.max(dir.y, 0.22)
    dir.normalize()
    this.flying = {
      t: 0,
      fromPos: this.camera.position.clone(),
      toPos: pos.clone().addScaledVector(dir, dist),
      fromTgt: this.controls.target.clone(),
      toTgt: pos.clone(),
    }
  }

  // Esc: ego 解除 → 親マクロ俯瞰 → home へ段階的上昇(全て連続飛行)。
  private onKeyDown(e: KeyboardEvent): void {
    if (e.key !== 'Escape') return
    const s = useStore.getState()
    if (s.selection.kind === 'article') {
      this.stars.clearEgo()
      useStore.setState({
        selection: { kind: 'none' },
        panel: { kind: 'overview', galaxies: this.index.count, macros: this.boot.macros.length },
      })
      return
    }
    const D = this.camera.position.distanceTo(this.controls.target)
    if (D < 2200) {
      let best: { m: Macro; d: number } | null = null
      for (const m of this.boot.macros) {
        const d = this.controls.target.distanceTo(tmpVec.set(m.x, m.y, m.z))
        if (!best || d < best.d) best = { m, d }
      }
      if (best && best.d < best.m.r * 3.2) {
        this.flyTo(new THREE.Vector3(best.m.x, best.m.y, best.m.z), Math.max(best.m.r * 2.6, 120))
        return
      }
    }
    this.flyTo(HOME_TARGET.clone(), HOME_POS.length())
  }

  // 破棄する。
  dispose(): void {
    this.disposed = true
    cancelAnimationFrame(this.raf)
    this.unsub?.()
    window.removeEventListener('resize', this.onResizeBound)
    this.canvas.removeEventListener('wheel', this.onWheelBound)
    this.canvas.removeEventListener('pointerdown', this.onPointerDownBound)
    this.canvas.removeEventListener('pointermove', this.onPointerMoveBound)
    this.canvas.removeEventListener('pointerup', this.onPointerUpBound)
    window.removeEventListener('keydown', this.onKeyDownBound)
    this.stars.dispose()
    this.uni.dispose()
    this.controls.dispose()
    this.composer.dispose()
    this.renderer.dispose()
  }
}
