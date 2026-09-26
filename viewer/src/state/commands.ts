// src/state/commands.ts
// React 側から描画コアへ命令を渡すための手続き接口を定義する。
//
// 責務:
// - UI コンポーネントが three 実装に直接依存せず命令を発行できるようにする
//
// 注意:
// - 実体は ViewerCore が起動時に登録する。登録前は全て no-op。

// UI から描画コアへ発行可能な命令の一覧。
export interface ViewerCommands {
  enterGalaxy(g: number): void
  leave(): void
  setZ(k: number): void
  toggle(key: 'edges' | 'cross' | 'shells' | 'labels' | 'dust' | 'stars'): void
  search(q: string): void
  jumpToArticle(g: number, local: number): void
}

// 現在登録されている命令実装。起動前は null。
export let commands: ViewerCommands | null = null

// 命令実装を描画コアから登録する。
export function registerCommands(c: ViewerCommands): void {
  commands = c
}
