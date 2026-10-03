// src/three/universeLayer.ts
// 連続宇宙の骨格層: 銀河クラスタ(手続き的銀河テクスチャのスプライト+球殻)、
// 銀河団バンドル、銀河(クラスタ+内部ハブ星+名ラベル)、銀河間バンドルを描画する。
//
// 責務:
// - 自己相似 LOD の親側: 塊スプライトの px clamp(遠景で一点化)と
//   距離フェード、子出現時の α 減光(受け渡し)
// - 銀河の遠景ビジュアル: galaxyTextures の変奏(渦巻/もや/グロー)を
//   display_class(galaxy/medium/dust)と gid ハッシュ(回転・扁平=傾き表現)で割り当て
// - 潜入フォーカス測光: 非フォーカス実体のディミング(dim)と
//   焦点/包含実体の自塊ベール消灯(veil)
// - エッジ tier の α(マクロ/銀河バンドル)を距離帯から設定
// - 銀河名ラベルの距離 LOD(top-N プール)
// - 銀河/マクロのクリック・hover ピック
//
// 注意:
// - ラベルテクスチャは銀河単位で遅延生成しキャッシュする(上限あり)。
// - 球殻 = 選択インジケータ(選択中の銀河のみ表示。生成は shells.ts へ分離 =
//   将来の削除候補。マクロ球殻は廃止済み)。
// - 測光: 塊スプライトとバンドルは通常ブレンド(重なりが線色へ収束し白飛びしない)。
//   バンドルは次数由来のハブ抑制 ink を頂点色へ焼き、ハブ交差点の放射状白飛びを縛る。
// - ベール消灯はフォーカス重みと包含距離(containVeil)の max: カメラが実体内に
//   入れば同一性追跡の状態に関係なく自塊が必ず消える(画面覆われ防止)。

import * as THREE from 'three'
import type { BootstrapData, TileIndex } from '@/types/catalog'
import { LOD, EXPOSURE, screenPx, clumpAlpha, galaxyLabelAlpha, macroLabelAlpha, edgeInk } from './lod'
import { FOCUS, containVeil, dimOf, galaxyMember } from './focus'
import type { FocusState } from './focus'
import type { GalaxyTextureSet } from './galaxyTextures'
import { makeShellLines, SHELL_OPACITY } from './shells'

// ラベルプール上限。
const LABEL_POOL = 42
// ラベルテクスチャキャッシュ上限。
const LABEL_CACHE = 200

const tmpColor = new THREE.Color()
const tmpV = new THREE.Vector3()

// ペア列から各ノードの次数(出現数)と中央値を返す(バンドル ink の正規化基準)。
function pairDegrees(pairs: { a: number; b: number }[]): { deg: Map<number, number>; med: number } {
  const deg = new Map<number, number>()
  for (const p of pairs) {
    deg.set(p.a, (deg.get(p.a) ?? 0) + 1)
    deg.set(p.b, (deg.get(p.b) ?? 0) + 1)
  }
  const vals = [...deg.values()].sort((x, y) => x - y)
  return { deg, med: Math.max(1, vals[vals.length >> 1] ?? 1) }
}

// 宇宙の骨格(マクロ/銀河)を描画する。
export class UniverseLayer {
  readonly group = new THREE.Group()
  private boot: BootstrapData
  private index: TileIndex
  private camPos = new THREE.Vector3()
  private innerH = 1000
  private fov = 55
  private proj = 1000
  // マクロ描画体。
  private macroClumps: THREE.Sprite[] = []
  private macroGlows: THREE.Sprite[] = []
  private macroLabels: THREE.Sprite[] = []
  private macroBundleMat: THREE.LineBasicMaterial
  private macroBundleLines: THREE.LineSegments
  // 銀河描画体。
  private galClumps: THREE.Sprite[] = []
  // 球殻(選択インジケータ。生成と削除単位は shells.ts 参照)。
  private galShells: THREE.LineSegments[] = []
  private galHubs: THREE.Points[] = []
  // 銀河毎の視覚変奏(構築時に決定、update では参照のみ)。
  private galFlat: number[] = []
  private galSizeK: number[] = []
  private galOpK: number[] = []
  private gset: GalaxyTextureSet
  private galLabelPool: THREE.Sprite[] = []
  private labelTexCache = new Map<number, THREE.Texture>()
  private labelLru: number[] = []
  private galaxyBundleMat: THREE.LineBasicMaterial
  private galaxyBundleLines: THREE.LineSegments
  // ピック用。
  private pickRay = new THREE.Raycaster()
  private macroPickMeshes: THREE.Object3D[] = []

  // bootstrap + index からレイヤを構築する。
  constructor(boot: BootstrapData, index: TileIndex, clumpTex: THREE.Texture, gset: GalaxyTextureSet, labelFont: string) {
    this.boot = boot
    this.index = index
    this.gset = gset
    // マクロ: クラスタ(高不透明度)+広域グロー(淡)+球殻+ラベル。
    for (let i = 0; i < boot.macros.length; i++) {
      const m = boot.macros[i]
      tmpColor.setHSL(m.hue, 0.55, 0.62)
      const mat = new THREE.SpriteMaterial({
        map: clumpTex,
        color: tmpColor,
        transparent: true,
        opacity: 0.9,
        depthWrite: false,
        blending: THREE.NormalBlending,
      })
      const s = new THREE.Sprite(mat)
      s.position.set(m.x, m.y, m.z)
      s.scale.setScalar(m.r * 1.9)
      s.renderOrder = 3
      s.userData = { macro: i }
      this.group.add(s)
      this.macroClumps.push(s)
      this.macroPickMeshes.push(s)
      const gmat = new THREE.SpriteMaterial({
        map: clumpTex,
        color: tmpColor.clone().multiplyScalar(0.7),
        transparent: true,
        opacity: 0.22,
        depthWrite: false,
        blending: THREE.NormalBlending,
      })
      const gs = new THREE.Sprite(gmat)
      gs.position.copy(s.position)
      gs.scale.setScalar(m.r * 4.2)
      gs.renderOrder = 2
      this.group.add(gs)
      this.macroGlows.push(gs)
      const lm = new THREE.SpriteMaterial({
        map: this.makeLabelTexture(m.label, labelFont, '#fde8c9', 512),
        transparent: true,
        opacity: 0,
        depthWrite: false,
      })
      const ls = new THREE.Sprite(lm)
      ls.position.set(m.x, m.y + m.r * 1.25, m.z)
      ls.scale.set(m.r * 1.9, m.r * 1.9 * 0.14, 1)
      ls.renderOrder = 9
      this.group.add(ls)
      this.macroLabels.push(ls)
    }
    // マクロバンドル(帯状: 上辺+下辺+対角、ハブ抑制 ink を頂点色へ焼く)。
    {
      const pos: number[] = []
      const col: number[] = []
      const { deg, med } = pairDegrees(boot.macroBundles)
      const base = new THREE.Color(0xaad4ff)
      // 1 セグメントを位置+ink 付き頂点色で積む。
      const seg = (x0: number, y0: number, z0: number, x1: number, y1: number, z1: number, ink: number): void => {
        pos.push(x0, y0, z0, x1, y1, z1)
        col.push(base.r * ink, base.g * ink, base.b * ink, base.r * ink, base.g * ink, base.b * ink)
      }
      for (const b of boot.macroBundles) {
        const A = boot.macros[b.a]
        const B = boot.macros[b.b]
        const ink = edgeInk((deg.get(b.a) ?? 1) / med, (deg.get(b.b) ?? 1) / med, EXPOSURE.macroBundleInk)
        const ax = A.x
        const ay = A.y
        const az = A.z
        const bx = B.x
        const by = B.y
        const bz = B.z
        const dx = bx - ax
        const dy = by - ay
        const dz = bz - az
        const len = Math.hypot(dx, dy, dz) || 1
        const upx = -dy / len
        const upy = dx / len
        const upz = 0
        const nl = Math.hypot(upx, upy, upz) || 1
        const ux = upx / nl
        const uy = upy / nl
        const hA = A.r * 0.32
        const hB = B.r * 0.32
        const P = (t: number, side: number) => {
          const cx = ax + dx * t
          const cy = ay + dy * t
          const cz = az + dz * t
          const bend = Math.sin(t * Math.PI) * len * 0.075
          const half = (hA + (hB - hA) * t) * side
          return [cx + ux * (half) , cy + uy * (half) + bend, cz]
        }
        for (const side of [1, -1]) {
          let prev = P(0, side)
          for (let s = 1; s <= 8; s++) {
            const cur = P(s / 8, side)
            seg(prev[0], prev[1], prev[2], cur[0], cur[1], cur[2], ink)
            prev = cur
          }
        }
        const p0 = P(0, 1)
        const p1 = P(1, -1)
        seg(p0[0], p0[1], p0[2], p1[0], p1[1], p1[2], ink)
      }
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
      geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
      this.macroBundleMat = new THREE.LineBasicMaterial({
        color: 0xffffff,
        vertexColors: true,
        transparent: true,
        opacity: 0.5,
        depthWrite: false,
        blending: THREE.NormalBlending,
      })
      this.macroBundleLines = new THREE.LineSegments(geo, this.macroBundleMat)
      this.macroBundleLines.frustumCulled = false
      this.macroBundleLines.renderOrder = 1
      this.group.add(this.macroBundleLines)
    }
    // 銀河: クラスタ(手続き的銀河テクスチャ、display_class 別の変奏)+球殻+ハブ星。
    for (const g of boot.galaxies) {
      const meta = this.index.galaxies.get(g.gid)
      if (!meta) continue
      // 銀河毎の視覚変奏(決定的ハッシュ): 変奏選択・傾き(扁平)・画面内回転。
      const h1 = this.ghash(g.gid, 1)
      const h2 = this.ghash(g.gid, 2)
      const h3 = this.ghash(g.gid, 3)
      const cls = meta.display_class
      let map: THREE.Texture
      let sat: number
      let light: number
      let flat: number
      let sizeK: number
      let opK: number
      let rot = 0
      if (cls === 2) {
        // dust(孤立記事群): 構造のない淡いグロー。
        map = gset.glow
        sat = 0.35
        light = 0.58
        flat = 1
        sizeK = 0.85
        opK = 0.55
      } else if (cls === 1) {
        // medium(媒介銀河 = 銀河間物質): 構造の乏しい薄いもやで「橋渡し」の質感。
        map = gset.haze
        sat = 0.28
        light = 0.64
        flat = 0.55 + 0.45 * h2
        sizeK = 1.0
        opK = 0.62
        rot = h3 * Math.PI * 2
      } else {
        // 通常銀河: 渦巻変奏 + 傾き表現(扁平 0.36–1.0 × 画面内回転)。
        map = gset.spirals[Math.floor(h1 * gset.spirals.length) % gset.spirals.length]
        sat = 0.5
        light = 0.6
        flat = 0.36 + 0.64 * h2
        sizeK = 1.12
        opK = 1
        rot = h3 * Math.PI * 2
      }
      tmpColor.setHSL(meta.hue, sat, light)
      const mat = new THREE.SpriteMaterial({
        map,
        color: tmpColor,
        transparent: true,
        opacity: 0.85,
        depthWrite: false,
        blending: THREE.NormalBlending,
        rotation: rot,
      })
      const s = new THREE.Sprite(mat)
      s.position.set(g.x, g.y, g.z)
      s.scale.set(g.r * 2.05 * sizeK, g.r * 2.05 * sizeK * flat, 1)
      s.renderOrder = 4
      s.userData = { galaxy: g.gid }
      this.group.add(s)
      this.galClumps.push(s)
      this.galFlat.push(flat)
      this.galSizeK.push(sizeK)
      this.galOpK.push(opK)
      const shell = makeShellLines(g.x, g.y, g.z, g.r, meta.hue, 0.75, 0.3, 2)
      this.group.add(shell)
      this.galShells.push(shell)
      const hub = this.makeGalaxyHubs(g)
      this.group.add(hub)
      this.galHubs.push(hub)
    }
    // 銀河バンドル(3 本束、ベジェ 12 分割、ハブ抑制 ink を頂点色へ焼く)。
    {
      const pos: number[] = []
      const col: number[] = []
      const { deg, med } = pairDegrees(boot.galaxyBundles)
      const base = new THREE.Color(0x9fc4f0)
      // 1 セグメントを位置+ink 付き頂点色で積む。
      const seg = (p0: THREE.Vector3, p1: THREE.Vector3, ink: number): void => {
        pos.push(p0.x, p0.y, p0.z, p1.x, p1.y, p1.z)
        col.push(base.r * ink, base.g * ink, base.b * ink, base.r * ink, base.g * ink, base.b * ink)
      }
      for (const b of boot.galaxyBundles) {
        const ga = boot.galaxies.find(x => x.gid === b.a)
        const gb = boot.galaxies.find(x => x.gid === b.b)
        if (!ga || !gb) continue
        const ink = edgeInk((deg.get(b.a) ?? 1) / med, (deg.get(b.b) ?? 1) / med, EXPOSURE.galaxyBundleInk)
        const a = new THREE.Vector3(ga.x, ga.y, ga.z)
        const c = new THREE.Vector3(gb.x, gb.y, gb.z)
        const mid = a.clone().add(c).multiplyScalar(0.5)
        mid.y += a.distanceTo(c) * 0.1
        const curve = new THREE.QuadraticBezierCurve3(a, mid, c)
        const pts = curve.getPoints(12)
        const dir = c.clone().sub(a).normalize()
        const up = new THREE.Vector3(0, 1, 0)
        const perp = up.clone().cross(dir)
        if (perp.lengthSq() < 1e-6) perp.set(1, 0, 0)
        perp.normalize()
        for (const off of [-0.14, 0, 0.14]) {
          for (let i = 0; i < pts.length - 1; i++) {
            const p0 = pts[i].clone().addScaledVector(perp, off * 1.4)
            const p1 = pts[i + 1].clone().addScaledVector(perp, off * 1.4)
            seg(p0, p1, ink)
          }
        }
      }
      const geo = new THREE.BufferGeometry()
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
      geo.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
      this.galaxyBundleMat = new THREE.LineBasicMaterial({
        color: 0xffffff,
        vertexColors: true,
        transparent: true,
        opacity: 0.2,
        depthWrite: false,
        blending: THREE.NormalBlending,
      })
      this.galaxyBundleLines = new THREE.LineSegments(geo, this.galaxyBundleMat)
      this.galaxyBundleLines.frustumCulled = false
      this.galaxyBundleLines.renderOrder = 2
      this.group.add(this.galaxyBundleLines)
    }
    // 銀河名ラベルプール。
    for (let i = 0; i < LABEL_POOL; i++) {
      const m = new THREE.SpriteMaterial({ transparent: true, opacity: 0, depthWrite: false })
      const s = new THREE.Sprite(m)
      s.visible = false
      s.renderOrder = 9
      this.group.add(s)
      this.galLabelPool.push(s)
    }
  }

  // エッジ tier の α を距離帯から設定する。
  setBundleAlphas(macroA: number, galaxyA: number, visible: boolean): void {
    this.macroBundleMat.opacity = macroA
    this.macroBundleLines.visible = visible && macroA > 0.01
    this.galaxyBundleMat.opacity = galaxyA
    this.galaxyBundleLines.visible = visible && galaxyA > 0.01
  }

  // 毎フレーム更新(塊の自己相似 px・距離フェード・フォーカス測光・ラベル LOD)。
  update(camera: THREE.PerspectiveCamera, tiles: Map<number, number>, selectedGid: number, labelsOn: boolean, focus: FocusState): void {
    this.camPos.copy(camera.position)
    const rect = this.getSize()
    this.innerH = rect.h
    this.fov = camera.fov
    this.proj = this.innerH / (2 * Math.tan(THREE.MathUtils.degToRad(this.fov / 2)))
    // マクロ。
    for (let i = 0; i < this.boot.macros.length; i++) {
      const m = this.boot.macros[i]
      tmpV.set(m.x, m.y, m.z)
      const d = this.camPos.distanceTo(tmpV)
      const px = screenPx(m.r, d, this.innerH, this.fov)
      const fade = px < 2 ? 0 : px < 10 ? (px - 2) / 8 : 1
      // フォーカス測光: 非焦点団はディミング、焦点/包含団は自塊ベールを消灯。
      const dim = dimOf(focus, i === focus.mac ? 1 : 0)
      const veil = Math.max(i === focus.mac ? focus.macW : 0, containVeil(d, m.r, FOCUS.macVeilIn, FOCUS.macVeilOut))
      const vis = dim * (1 - veil)
      const clumpPx = Math.max(px, 4.5)
      const worldSize = (clumpPx * d) / this.proj
      this.macroClumps[i].scale.setScalar(worldSize * 1.25)
      this.macroGlows[i].scale.setScalar(worldSize * 2.4)
      ;(this.macroClumps[i].material as THREE.SpriteMaterial).opacity = 0.92 * fade * vis
      ;(this.macroGlows[i].material as THREE.SpriteMaterial).opacity = 0.16 * fade * vis
      const la = labelsOn ? macroLabelAlpha(px) * fade * dim : 0
      const lmat = this.macroLabels[i].material as THREE.SpriteMaterial
      lmat.opacity = la
      this.macroLabels[i].visible = la > 0.02
      this.macroLabels[i].scale.set(worldSize * 1.7, worldSize * 1.7 * 0.13, 1)
    }
    // 銀河。
    const labelCand: { g: number; i: number; px: number; d: number; dim: number }[] = []
    for (let i = 0; i < this.galClumps.length; i++) {
      const s = this.galClumps[i]
      const g = this.boot.galaxies[i]
      tmpV.set(g.x, g.y, g.z)
      const d = this.camPos.distanceTo(tmpV)
      const px = screenPx(g.r, d, this.innerH, this.fov)
      const tilePx = tiles.get(g.gid) ?? 0
      const effPx = Math.max(px, tilePx)
      const emerge = tilePx >= LOD.clump ? 1 : 0
      // 距離フェード(子がない状態で遠すぎる銀河は淡く、近すぎてデカい場合は常時)。
      const fade = emerge === 1 ? 1 : px < 1.4 ? Math.max(0, px / 1.4) : 1
      // フォーカス測光: 銀河潜入時は同胞銀河も保護を失い、焦点銀河の自塊は消灯。
      const dim = dimOf(focus, galaxyMember(focus, g.gid, g.macro))
      const veil = Math.max(g.gid === focus.gal ? focus.galW : 0, containVeil(d, g.r, FOCUS.galVeilIn, FOCUS.galVeilOut))
      const alpha = (emerge === 1 ? clumpAlpha(tilePx) : fade) * dim * (1 - veil)
      const clumpPx = Math.max(effPx, 3.2)
      const worldSize = (clumpPx * d) / this.proj
      const ws = worldSize * 1.45 * this.galSizeK[i]
      s.scale.set(ws, ws * this.galFlat[i], 1)
      ;(s.material as THREE.SpriteMaterial).opacity = 0.9 * alpha * this.galOpK[i]
      s.visible = alpha > 0.02
      // 球殻 = 選択インジケータ: 選択中の銀河のみ表示(潜入時は veil で消灯)。
      const gshMat = this.galShells[i].material as THREE.LineBasicMaterial
      gshMat.opacity = SHELL_OPACITY * dim * (1 - veil)
      this.galShells[i].visible = g.gid === selectedGid && gshMat.opacity > 0.012
      const hub = this.galHubs[i]
      const hmat = hub.material as THREE.ShaderMaterial
      hub.visible = emerge === 1 && tilePx > LOD.emerge
      if (hub.visible) {
        hmat.uniforms.uSize.value = Math.max(1.2, (worldSize * 1.45 * 0.075 * this.proj) / Math.max(d, 1))
        hmat.uniforms.uOpacity.value = Math.min(1, alpha * 0.55)
      }
      if (labelsOn && alpha > 0.05) {
        labelCand.push({ g: g.gid, i, px: effPx, d, dim })
      }
    }
    this.updateGalaxyLabels(labelCand, labelsOn)
  }

  // 銀河クリック/hover ピックを実行する(クラスタスプライト基準)。
  pickGalaxy(ndc: THREE.Vector2, camera: THREE.PerspectiveCamera): { gid: number; dist: number } | null {
    this.pickRay.setFromCamera(ndc, camera)
    const hits = this.pickRay.intersectObjects(this.galClumps, false)
    let best: { gid: number; dist: number } | null = null
    for (const h of hits) {
      const gid = h.object.userData.galaxy as number | undefined
      if (gid == null) continue
      const spr = h.object as THREE.Sprite
      if ((spr.material as THREE.SpriteMaterial).opacity < 0.05) continue
      if (!best || h.distance < best.dist) best = { gid, dist: h.distance }
    }
    return best
  }

  // マクロクリック/hover ピックを実行する。
  pickMacro(ndc: THREE.Vector2, camera: THREE.PerspectiveCamera): { mid: number; dist: number } | null {
    this.pickRay.setFromCamera(ndc, camera)
    const hits = this.pickRay.intersectObjects(this.macroPickMeshes, false)
    let best: { mid: number; dist: number } | null = null
    for (const h of hits) {
      const mid = h.object.userData.macro as number | undefined
      if (mid == null) continue
      if (!best || h.distance < best.dist) best = { mid, dist: h.distance }
    }
    return best
  }

  // 銀河毎の決定的ハッシュ [0,1)(視覚変奏用。bootstrap の黄金比ハッシュと同系)。
  private ghash(gid: number, salt: number): number {
    const x = Math.sin((gid + 1) * 12.9898 + salt * 78.233) * 43758.5453
    return x - Math.floor(x)
  }

  // 銀河ハブ星を構築する(次数上位)。
  private makeGalaxyHubs(g: { gid: number; x: number; y: number; z: number; r: number }): THREE.Points {
    const meta = this.index.galaxies.get(g.gid)
    const seed = g.gid * 2654435761
    // ハブ星は装飾: 個数は規模から、明るさはハッシュから決める(データ非依存)。
    const count = Math.max(4, Math.min(40, Math.round(Math.sqrt(Math.max(meta?.n ?? 16, 1)))))
    const pos = new Float32Array(count * 3)
    const size = new Float32Array(count)
    const bright = new Float32Array(count)
    const phase = new Float32Array(count)
    for (let i = 0; i < count; i++) {
      const rr = g.r * (0.35 + 0.6 * Math.cbrt(((seed + i * 40503) % 1000) / 1000))
      const th = (((seed + i * 7919) % 6283) / 6283) * Math.PI * 2
      const ph = Math.acos(1 - 2 * (((seed + i * 104729) % 1000) / 1000)) * 0.6 + Math.PI * 0.2
      pos[i * 3] = g.x + rr * Math.sin(ph) * Math.cos(th)
      pos[i * 3 + 1] = g.y + rr * Math.cos(ph) * 0.55
      pos[i * 3 + 2] = g.z + rr * Math.sin(ph) * Math.sin(th)
      const h = (((seed + i * 7727) % 1000) / 1000)
      size[i] = 0.9 + Math.pow(h, 0.45) * 2.1
      bright[i] = Math.min(1, 0.3 + h * 0.7)
      phase[i] = ((i * 6151) % 6283) / 1000
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    geo.setAttribute('aSize', new THREE.BufferAttribute(size, 1))
    geo.setAttribute('aBright', new THREE.BufferAttribute(bright, 1))
    geo.setAttribute('aPhase', new THREE.BufferAttribute(phase, 1))
    tmpColor.setHSL(meta?.hue ?? 0.6, 0.25, 0.6)
    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uSize: { value: 2 },
        uTime: { value: 0 },
        uPxMax: { value: 12 },
        uOpacity: { value: 0 },
        uColorA: { value: tmpColor.clone() },
        uColorB: { value: new THREE.Color(0xfff6e0) },
      },
      vertexShader: `
uniform float uSize;
uniform float uTime;
uniform float uPxMax;
attribute float aSize;
attribute float aBright;
attribute float aPhase;
varying float vBright;
void main() {
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  float twinkle = 1.0 + 0.12 * sin(uTime * 1.7 + aPhase);
  gl_PointSize = min(aSize * uSize * twinkle, uPxMax);
  vBright = aBright;
}`,
      fragmentShader: `
uniform vec3 uColorA;
uniform vec3 uColorB;
uniform float uOpacity;
varying float vBright;
void main() {
  vec2 d = gl_PointCoord - vec2(0.5);
  float r = length(d) * 2.0;
  if (r > 1.0) discard;
  float core = pow(max(1.0 - r, 0.0), 2.0);
  vec3 c = mix(uColorA, uColorB, vBright);
  gl_FragColor = vec4(c * (${EXPOSURE.hubBase} + ${EXPOSURE.hubCore} * core), uOpacity * (1.0 - r) * (0.5 + 0.5 * core));
}`,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    })
    mat.opacity = 0
    const pts = new THREE.Points(geo, mat)
    pts.frustumCulled = false
    pts.renderOrder = 5
    return pts
  }

  // 銀河名ラベルプールを更新する(px 上位 N、距離 LOD α × フォーカス dim)。
  private updateGalaxyLabels(cand: { g: number; i: number; px: number; d: number; dim: number }[], labelsOn: boolean): void {
    if (!labelsOn) {
      for (const s of this.galLabelPool) s.visible = false
      return
    }
    cand.sort((a, b) => b.px - a.px)
    const chosen = cand.slice(0, LABEL_POOL).filter(c => c.px > LOD.galLabelIn)
    for (let k = 0; k < this.galLabelPool.length; k++) {
      const spr = this.galLabelPool[k]
      const c = chosen[k]
      if (!c) {
        spr.visible = false
        continue
      }
      const g = this.boot.galaxies[c.i]
      if ((spr.userData.g as number | undefined) !== c.g) {
        const tex = this.getLabelTexture(c.g, g.label)
        if (!tex) {
          spr.visible = false
          continue
        }
        const m = spr.material as THREE.SpriteMaterial
        m.map?.dispose?.()
        m.map = tex
        m.needsUpdate = true
        spr.userData.g = c.g
      }
      const worldSize = (Math.max(c.px, 3.2) * c.d) / this.proj
      spr.position.set(g.x, g.y + worldSize * 0.9, g.z)
      spr.scale.set(worldSize * 1.5, worldSize * 1.5 * 0.16, 1)
      const a = galaxyLabelAlpha(c.px) * c.dim
      ;(spr.material as THREE.SpriteMaterial).opacity = a
      spr.visible = a > 0.03
    }
  }

  // ラベルテクスチャを取得/生成する(LRU キャッシュ)。
  private getLabelTexture(g: number, label: string): THREE.Texture | null {
    const hit = this.labelTexCache.get(g)
    if (hit) return hit
    if (this.labelTexCache.size >= LABEL_CACHE) {
      const old = this.labelLru.shift()
      if (old != null) {
        this.labelTexCache.get(old)?.dispose()
        this.labelTexCache.delete(old)
      }
    }
    const tex = this.makeLabelTexture(label, '600 26px system-ui, sans-serif', '#eaf3ff', 512)
    this.labelTexCache.set(g, tex)
    this.labelLru.push(g)
    return tex
  }

  // ラベル用 CanvasTexture を生成する。
  private makeLabelTexture(text: string, font: string, fill: string, width: number): THREE.Texture {
    const cv = document.createElement('canvas')
    cv.width = width
    cv.height = 64
    const ctx = cv.getContext('2d')
    if (!ctx) return new THREE.Texture()
    ctx.clearRect(0, 0, width, 64)
    ctx.font = font
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.shadowColor = 'rgba(0,0,0,0.9)'
    ctx.shadowBlur = 6
    ctx.fillStyle = fill
    ctx.fillText(text, width / 2, 34)
    const tex = new THREE.CanvasTexture(cv)
    tex.needsUpdate = true
    return tex
  }

  // 描画サイズを取得する。
  private getSize(): { w: number; h: number } {
    const el = document.getElementById('viewer-root')
    if (el) return { w: el.clientWidth || 1, h: el.clientHeight || 1 }
    return { w: window.innerWidth, h: window.innerHeight }
  }

  // 時間を進める(ハブ星の瞬き用)。
  tick(t: number): void {
    for (const h of this.galHubs) {
      const m = h.material as THREE.ShaderMaterial
      m.uniforms.uTime.value = t
    }
  }

  // 全リソースを破棄する。
  dispose(): void {
    this.group.traverse(o => {
      const any = o as THREE.Mesh & { geometry?: THREE.BufferGeometry; material?: THREE.Material | THREE.Material[] }
      any.geometry?.dispose?.()
      const m = any.material
      if (Array.isArray(m)) m.forEach(x => x.dispose())
      else m?.dispose?.()
    })
    this.gset.dispose()
    for (const tex of this.labelTexCache.values()) tex.dispose()
    this.labelTexCache.clear()
  }
}
