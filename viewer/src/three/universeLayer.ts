// src/three/universeLayer.ts
// 連続宇宙の骨格層: 銀河クラスタ(塊スプライト二層+球殻)、銀河団バンドル、
// 銀河(クラスタ+球殻+内部ハブ星+名ラベル)、銀河間バンドルを描画する。
//
// 責務:
// - 自己相似 LOD の親側: 塊スプライトの px clamp(遠景で一点化)と
//   距離フェード、子出現時の α 減光(受け渡し)
// - エッジ tier の α(マクロ/銀河バンドル)を距離帯から設定
// - 銀河名ラベルの距離 LOD(top-N プール)
// - 銀河/マクロのクリック・hover ピック
//
// 注意:
// - ラベルテクスチャは銀河単位で遅延生成しキャッシュする(上限あり)。
// - 球殻は近距離のみ表示(fade)、遠景ではクラスタだけ。
// - 測光: 塊スプライトとバンドルは通常ブレンド(重なりが線色へ収束し白飛びしない)。
//   バンドルは次数由来のハブ抑制 ink を頂点色へ焼き、ハブ交差点の放射状白飛びを縛る。

import * as THREE from 'three'
import type { BootstrapData, TileIndex } from '@/types/catalog'
import { LOD, EXPOSURE, screenPx, clumpAlpha, galaxyLabelAlpha, macroLabelAlpha, edgeInk } from './lod'

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
  private macroShells: THREE.LineSegments[] = []
  private macroLabels: THREE.Sprite[] = []
  private macroBundleMat: THREE.LineBasicMaterial
  private macroBundleLines: THREE.LineSegments
  // 銀河描画体。
  private galClumps: THREE.Sprite[] = []
  private galShells: THREE.LineSegments[] = []
  private galHubs: THREE.Points[] = []
  private galLabelPool: THREE.Sprite[] = []
  private labelTexCache = new Map<number, THREE.Texture>()
  private labelLru: number[] = []
  private galaxyBundleMat: THREE.LineBasicMaterial
  private galaxyBundleLines: THREE.LineSegments
  // ピック用。
  private pickRay = new THREE.Raycaster()
  private macroPickMeshes: THREE.Object3D[] = []

  // bootstrap + index からレイヤを構築する。
  constructor(boot: BootstrapData, index: TileIndex, clumpTex: THREE.Texture, labelFont: string) {
    this.boot = boot
    this.index = index
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
      const shell = this.makeShell(m.x, m.y, m.z, m.r, m.hue, 0.7, 0.22, 3)
      shell.visible = false
      this.group.add(shell)
      this.macroShells.push(shell)
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
    // 銀河: クラスタ+球殻+ハブ星。
    for (const g of boot.galaxies) {
      const meta = this.index.galaxies.get(g.gid)
      if (!meta) continue
      tmpColor.setHSL(meta.hue, 0.5, 0.6)
      const mat = new THREE.SpriteMaterial({
        map: clumpTex,
        color: tmpColor,
        transparent: true,
        opacity: 0.85,
        depthWrite: false,
        blending: THREE.NormalBlending,
      })
      const s = new THREE.Sprite(mat)
      s.position.set(g.x, g.y, g.z)
      s.scale.setScalar(g.r * 2.05)
      s.renderOrder = 4
      s.userData = { galaxy: g.gid }
      this.group.add(s)
      this.galClumps.push(s)
      const shell = this.makeShell(g.x, g.y, g.z, g.r, meta.hue, 0.75, 0.26, 2)
      shell.visible = false
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

  // 毎フレーム更新(塊の自己相似 px・距離フェード・ラベル LOD)。
  update(camera: THREE.PerspectiveCamera, tiles: Map<number, number>, shellsOn: boolean, labelsOn: boolean): void {
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
      const clumpPx = Math.max(px, 4.5)
      const worldSize = (clumpPx * d) / this.proj
      this.macroClumps[i].scale.setScalar(worldSize * 1.25)
      this.macroGlows[i].scale.setScalar(worldSize * 2.4)
      ;(this.macroClumps[i].material as THREE.SpriteMaterial).opacity = 0.92 * fade
      ;(this.macroGlows[i].material as THREE.SpriteMaterial).opacity = 0.16 * fade
      this.macroShells[i].visible = shellsOn && fade > 0.85 && d < m.r * 26
      const la = labelsOn ? macroLabelAlpha(px) * fade : 0
      const lmat = this.macroLabels[i].material as THREE.SpriteMaterial
      lmat.opacity = la
      this.macroLabels[i].visible = la > 0.02
      this.macroLabels[i].scale.set(worldSize * 1.7, worldSize * 1.7 * 0.13, 1)
    }
    // 銀河。
    const labelCand: { g: number; i: number; px: number; d: number }[] = []
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
      const alpha = emerge === 1 ? clumpAlpha(tilePx) : fade
      const clumpPx = Math.max(effPx, 3.2)
      const worldSize = (clumpPx * d) / this.proj
      s.scale.setScalar(worldSize * 1.45)
      ;(s.material as THREE.SpriteMaterial).opacity = 0.9 * alpha
      s.visible = alpha > 0.02
      this.galShells[i].visible = shellsOn && emerge === 1 && tilePx > LOD.resolve * 0.8
      const hub = this.galHubs[i]
      const hmat = hub.material as THREE.ShaderMaterial
      hub.visible = emerge === 1 && tilePx > LOD.emerge
      if (hub.visible) {
        hmat.uniforms.uSize.value = Math.max(1.2, (worldSize * 1.45 * 0.075 * this.proj) / Math.max(d, 1))
        hmat.uniforms.uOpacity.value = Math.min(1, alpha * 0.55)
      }
      if (labelsOn && alpha > 0.05) {
        labelCand.push({ g: g.gid, i, px: effPx, d })
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

  // ワイヤー球殻を生成する。
  private makeShell(x: number, y: number, z: number, r: number, hue: number, sat: number, light: number, renderOrder: number): THREE.LineSegments {
    const pts: number[] = []
    const rings = 7
    const segs = 48
    for (let i = 1; i < rings; i++) {
      const phi = (i / rings) * Math.PI
      const rr = r * Math.sin(phi)
      const yy = r * Math.cos(phi) * 0.6
      for (let j = 0; j < segs; j++) {
        const t0 = (j / segs) * Math.PI * 2
        const t1 = ((j + 1) / segs) * Math.PI * 2
        pts.push(x + rr * Math.cos(t0), y + yy, z + rr * Math.sin(t0))
        pts.push(x + rr * Math.cos(t1), y + yy, z + rr * Math.sin(t1))
      }
    }
    const meridians = 9
    for (let k = 0; k < meridians; k++) {
      const th = (k / meridians) * Math.PI * 2
      for (let j = 0; j < 24; j++) {
        const p0 = (j / 24) * Math.PI
        const p1 = ((j + 1) / 24) * Math.PI
        pts.push(x + r * Math.sin(p0) * Math.cos(th), y + r * Math.cos(p0) * 0.6, z + r * Math.sin(p0) * Math.sin(th))
        pts.push(x + r * Math.sin(p1) * Math.cos(th), y + r * Math.cos(p1) * 0.6, z + r * Math.sin(p1) * Math.sin(th))
      }
    }
    const geo = new THREE.BufferGeometry()
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
    tmpColor.setHSL(hue, sat, light)
    const mat = new THREE.LineBasicMaterial({ color: tmpColor, transparent: true, opacity: 0.16, depthWrite: false })
    const ls = new THREE.LineSegments(geo, mat)
    ls.frustumCulled = false
    ls.renderOrder = renderOrder
    return ls
  }

  // 銀河名ラベルプールを更新する(px 上位 N、距離 LOD α)。
  private updateGalaxyLabels(cand: { g: number; i: number; px: number; d: number }[], labelsOn: boolean): void {
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
      const a = galaxyLabelAlpha(c.px)
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
    for (const tex of this.labelTexCache.values()) tex.dispose()
    this.labelTexCache.clear()
  }
}
