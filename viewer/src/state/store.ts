// src/state/store.ts
// ビューア全体の UI 状態を保持する最小ストアを定義する。
//
// 責務:
// - three 側(描画コア)と React 側(HUD)の間の状態共有
// - useSyncExternalStore による購読提供
//
// 注意:
// - 毎フレーム変化する値(camera 等)はここへ入れない。React の再描画を誘発するため。
// - fps 等の統計は 1 秒間隔の更新に限定する。

import { useSyncExternalStore } from 'react'

// 表示トグルの一覧。
export interface Toggles {
  edges: boolean
  cross: boolean
  shells: boolean
  labels: boolean
  dust: boolean
  stars: boolean
}

// 右パネルの表示モデル。
export type PanelModel =
  | { kind: 'universe' }
  | { kind: 'loading'; g: number }
  | { kind: 'galaxy'; g: number; neighbors: { g: number; cnt: number }[] }
  | { kind: 'article'; title: string; deg: number; targets: { g: number; local: number; title: string }[] }

// ビューア UI 状態。
export interface ViewerState {
  ready: boolean
  stage: 'universe' | 'galaxy'
  focus: number
  hover: { text: string; x: number; y: number } | null
  toggles: Toggles
  zK: number
  panel: PanelModel
  stats: { fps: number; tiles: number }
}

let state: ViewerState = {
  ready: false,
  stage: 'universe',
  focus: -1,
  hover: null,
  toggles: { edges: true, cross: true, shells: true, labels: true, dust: true, stars: true },
  zK: 1.0,
  panel: { kind: 'universe' },
  stats: { fps: 0, tiles: 0 },
}

const listeners = new Set<() => void>()

// 現在状態のスナップショットを返す。
export function getState(): ViewerState {
  return state
}

// 状態を部分更新して購読者へ通知する。
export function setState(patch: Partial<ViewerState>): void {
  state = { ...state, ...patch }
  listeners.forEach(l => l())
}

// ストアの購読登録(useSyncExternalStore 用)。
export function subscribe(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

// React から状態を購読するフック。
export function useViewer(): ViewerState {
  return useSyncExternalStore(subscribe, getState)
}
