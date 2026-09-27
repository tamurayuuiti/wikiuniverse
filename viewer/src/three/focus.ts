// src/three/focus.ts
// 潜入フォーカスの連続検知: カメラがどの銀河団/銀河に入ったかを判定し、
// 測光減光(フォーカス外ディミング)用の重み(0..1)を返す。
//
// 責務:
// - カメラ位置と orbit target からマクロ/銀河の潜入重みを計算(連続・ズーム可逆)
// - 同一性のヒステリシス(隣接実体間の焦点ちらつき防止)と重みの指数平滑(ポップ防止)
// - 非フォーカス実体を減光する member/dim ヘルパと、包含状態由来のズーム帯ラベル
//
// 注意:
// - 離散モードは持たない(v6 哲学: 描画は連続幾何駆動)。Esc 上昇もこの状態を参照する。
// - 距離帯は実体の世界半径 r 基準で正規化する(自己相似: 大小の銀河で潜入感が等価)。
// - 重みは「カメラが物理的に深く入る(deep)」と「カメラも target も近い(aim)」の max。
//   aim にはさらに D(カメラ→target)が遠景バンドのとき 0 へ絞る aimGate を掛ける
//   (home 俯瞰で target が偶然どこかのマクロ内にある場合の誤フォーカス防止)。

import * as THREE from 'three'
import type { BootstrapData } from '@/types/catalog'
import { BAND, smoothstep } from './lod'

// フォーカス検知定数(距離帯は実体半径 r の倍数、D 帯のみ絶対値)。
export const FOCUS = {
  // 銀河: 物理潜入帯(カメラ→銀河中心)。
  galDeepIn: 0.8,
  galDeepOut: 2.0,
  // 銀河: aim 帯(カメラ距離 × target 距離)。
  galAimCamIn: 2.2,
  galAimCamOut: 6.5,
  galAimTgtIn: 0.5,
  galAimTgtOut: 2.0,
  // 銀河団: 物理潜入帯。
  macDeepIn: 0.9,
  macDeepOut: 2.4,
  // 銀河団: aim 帯。
  macAimCamIn: 1.6,
  macAimCamOut: 4.6,
  macAimTgtIn: 0.6,
  macAimTgtOut: 2.2,
  // aim 分岐の D ゲート帯(BAND.universe 比)。home 俯瞰(D≈2478)では 0。
  aimGateIn: 0.62,
  aimGateOut: 0.95,
  // 同一性切替のヒステリシスマージン(挑戦者が現行をこの幅だけ上回る必要)。
  margin: 0.06,
  // 重みの平滑レート(上昇/下降、1/秒)。
  rateUp: 6.0,
  rateDown: 4.5,
  // 重みがこれ未満の同一性は「潜入なし」とみなす。
  eps: 0.015,
  // 非フォーカス実体の最低輝度(ディミングの床)。完全には消さず文脈を残す。
  dimFloor: 0.1,
  // ベール(自塊スプライト)消灯の包含帯: カメラがこの帯に入ると
  // フォーカス重みに関係なく当該実体の塊/グローが消える(画面覆い防止)。
  macVeilIn: 0.9,
  macVeilOut: 1.8,
  galVeilIn: 0.7,
  galVeilOut: 1.5,
} as const

// 潜入フォーカス状態(平滑済み)。
export interface FocusState {
  /** フォーカス銀河団 id(-1: なし)。 */
  mac: number
  /** フォーカス銀河 id(-1: なし)。 */
  gal: number
  /** 銀河団潜入重み(0..1)。 */
  macW: number
  /** 銀河潜入重み(0..1)。 */
  galW: number
}

// 距離 2 要素から潜入重み(0..1)を返す(deep と aim の max)。
function weight(
  dCam: number,
  dTgt: number,
  r: number,
  deepIn: number,
  deepOut: number,
  aimCamIn: number,
  aimCamOut: number,
  aimTgtIn: number,
  aimTgtOut: number,
  aimGate: number,
): number {
  const deep = 1 - smoothstep(r * deepIn, r * deepOut, dCam)
  const aim =
    (1 - smoothstep(r * aimCamIn, r * aimCamOut, dCam)) *
    (1 - smoothstep(r * aimTgtIn, r * aimTgtOut, dTgt)) *
    aimGate
  return Math.max(deep, aim)
}

// 潜入フォーカスのトラッカー(ヒステリシス+指数平滑)。
export class FocusTracker {
  private st: FocusState = { mac: -1, gal: -1, macW: 0, galW: 0 }
  private tmp = new THREE.Vector3()

  // 現在の平滑済み状態を返す(イベントハンドラ用、更新は update のみ)。
  get current(): FocusState {
    return this.st
  }

  // カメラ/target から焦点同一性を選び、重みを平滑して返す。
  update(camPos: THREE.Vector3, target: THREE.Vector3, dt: number, boot: BootstrapData): FocusState {
    const D = camPos.distanceTo(target)
    const aimGate = 1 - smoothstep(BAND.universe * FOCUS.aimGateIn, BAND.universe * FOCUS.aimGateOut, D)
    this.track(dt, 'mac', boot.macros.length, i => {
      const m = boot.macros[i]
      return weight(
        this.dCam(camPos, m.x, m.y, m.z),
        this.dTgt(target),
        m.r,
        FOCUS.macDeepIn,
        FOCUS.macDeepOut,
        FOCUS.macAimCamIn,
        FOCUS.macAimCamOut,
        FOCUS.macAimTgtIn,
        FOCUS.macAimTgtOut,
        aimGate,
      )
    })
    this.track(dt, 'gal', boot.galaxies.length, i => {
      const g = boot.galaxies[i]
      return weight(
        this.dCam(camPos, g.x, g.y, g.z),
        this.dTgt(target),
        g.r,
        FOCUS.galDeepIn,
        FOCUS.galDeepOut,
        FOCUS.galAimCamIn,
        FOCUS.galAimCamOut,
        FOCUS.galAimTgtIn,
        FOCUS.galAimTgtOut,
        aimGate,
      )
    })
    return this.st
  }

  // 1 階層分の焦点追跡(argmax+ヒステリシス+平滑)を行う。
  private track(dt: number, kind: 'mac' | 'gal', n: number, weightOf: (i: number) => number): void {
    const st = this.st
    const cur = kind === 'mac' ? st.mac : st.gal
    const curW = kind === 'mac' ? st.macW : st.galW
    // 現行同一性の重み(保持候補)。
    let best = -1
    let bestW: number = FOCUS.eps
    if (cur >= 0 && cur < n) {
      const w = weightOf(cur)
      if (w > FOCUS.eps) {
        best = cur
        bestW = w
      }
    }
    // 挑戦者を走査(現行保持中は margin 上乗せ=ヒステリシス)。
    for (let i = 0; i < n; i++) {
      if (i === cur) continue
      const w = weightOf(i)
      const need = best === cur ? FOCUS.margin : 0
      if (w > bestW + need) {
        bestW = w
        best = i
      }
    }
    // 重みの指数平滑(同一性は減衰中も保持し、eps 未満で解放)。
    const tgtW = best >= 0 ? bestW : 0
    const rate = tgtW > curW ? FOCUS.rateUp : FOCUS.rateDown
    let w = curW + (tgtW - curW) * (1 - Math.exp(-dt * rate))
    let id = best >= 0 ? best : cur
    if (w < FOCUS.eps) {
      w = 0
      id = -1
    }
    if (kind === 'mac') {
      st.mac = id
      st.macW = w
    } else {
      st.gal = id
      st.galW = w
    }
  }

  // カメラ→実体中心距離を返す(tmp 更新の副作用あり、dTgt と必ずこの順で呼ぶ)。
  private dCam(camPos: THREE.Vector3, x: number, y: number, z: number): number {
    return camPos.distanceTo(this.tmp.set(x, y, z))
  }

  // target→実体中心距離を返す(dCam の直後に呼ぶ)。
  private dTgt(target: THREE.Vector3): number {
    return target.distanceTo(this.tmp)
  }
}

// 全体のフォーカス強度 W(0..1)を返す。
export function focusW(f: FocusState): number {
  return Math.max(f.macW, f.galW)
}

// 銀河のフォーカス所属度(0..1)を返す: 焦点銀河=1、焦点銀河団の同胞=1-galW、他=0。
export function galaxyMember(f: FocusState, gid: number, mid: number): number {
  if (f.gal >= 0 && gid === f.gal) return 1
  if (f.mac >= 0 && mid === f.mac) return 1 - f.galW
  return 0
}

// 非フォーカス実体の輝度乗数(0..1)を返す: W=0 → 1、完全潜入の非所属 → dimFloor。
export function dimOf(f: FocusState, member: number): number {
  return 1 - focusW(f) * (1 - FOCUS.dimFloor) * (1 - member)
}

// 包含距離ベール(自塊消灯)を返す: カメラが実体に入ると焦点重みに関係なく 1 へ。
export function containVeil(dCam: number, r: number, kIn: number, kOut: number): number {
  return 1 - smoothstep(r * kIn, r * kOut, dCam)
}

// フォーカス状態と距離 D からズーム帯ラベル(表示専用)を導く。
export function focusZoomLabel(f: FocusState, D: number): string {
  if (D < BAND.galaxy) return 'article'
  if (f.galW > 0.8) return 'article'
  if (f.galW > 0.25) return 'galaxy'
  if (f.macW > 0.25) return 'cluster'
  return 'universe'
}
