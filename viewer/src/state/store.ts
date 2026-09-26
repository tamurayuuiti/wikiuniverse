// src/state/store.ts
// Zustand ストア: v6 連続宇宙の表示コンテキスト(選択・パネル・トグル・stats)。
//
// 責務:
// - 選択(selection: none/galaxy/article)と hover の保持
// - パネル表示モデル(kind: overview/loading/galaxy/article)
// - 表示トグル・z スライダ・ズーム帯ラベル・ジャンプ要求
//
// 注意:
// - 段階(stage)/離散 focus は存在しない。zoomLabel は表示専用読み取り値。
// - jumpRequest は検索など UI 起点の fly-to 要求(core が購読して消費)。

import { create } from 'zustand'

// 選択コンテキスト。
export type Selection =
  | { kind: 'none' }
  | { kind: 'galaxy'; gid: number }
  | { kind: 'article'; gid: number; local: number }

// hover 情報。
export interface HoverInfo {
  kind: 'galaxy' | 'macro' | 'article'
  gid: number
  local?: number
  title: string
  x: number
  y: number
  info?: string
}

// パネル表示モデル。
export type PanelModel =
  | { kind: 'overview'; galaxies: number; macros: number }
  | { kind: 'loading'; label: string }
  | {
      kind: 'galaxy'
      gid: number
      title: string
      macroTitle: string
      n: number
      nCross: number
      displayClass: string
      hubTitles: string[]
    }
  | {
      kind: 'article'
      gid: number
      local: number
      galaxyTitle: string
      title: string
      deg: number
      crossTargets: { gid: number; title: string }[]
      macroTitle?: string
    }

// 表示トグル。
export interface Toggles {
  stars: boolean
  edges: boolean
  cross: boolean
  labels: boolean
  shells: boolean
  dust: boolean
}

// パフォーマンス統計。
export interface Stats {
  fps: number
  emerged: number
  tiles: number
  zK: number
  viewWidthU?: number
}

// ストアの状態。
export interface ViewerState {
  ready: boolean
  selection: Selection
  hover: HoverInfo | null
  panel: PanelModel
  toggles: Toggles
  zSquash: number
  zoomLabel: string
  jumpRequest: { pos: [number, number, number]; dist: number; seq: number } | null
  stats: Stats
}

// ストアの状態とアクション。
interface ViewerActions {
  setHover: (h: HoverInfo | null) => void
  setPanel: (p: PanelModel) => void
  setToggle: (k: keyof Toggles, v: boolean) => void
  setZSquash: (k: number) => void
  requestJump: (pos: [number, number, number], dist: number) => void
}

let jumpSeq = 0

// アプリ全体のストア。
export const useStore = create<ViewerState & ViewerActions>((set, get) => ({
  ready: false,
  selection: { kind: 'none' },
  hover: null,
  panel: { kind: 'overview', galaxies: 0, macros: 0 },
  toggles: { stars: true, edges: true, cross: true, labels: true, shells: true, dust: true },
  zSquash: 1,
  zoomLabel: 'universe',
  jumpRequest: null,
  stats: { fps: 0, emerged: 0, tiles: 0, zK: 1 },
  setHover: (h) => set({ hover: h }),
  setPanel: (p) => set({ panel: p }),
  setToggle: (k, v) => set({ toggles: { ...get().toggles, [k]: v } }),
  setZSquash: (k) => set({ zSquash: k }),
  requestJump: (pos, dist) => set({ jumpRequest: { pos, dist, seq: ++jumpSeq } }),
}))
