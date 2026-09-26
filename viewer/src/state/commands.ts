// src/state/commands.ts
// UI 起点コマンド: 検索・ランダムジャンプ・リセット(全て連続 fly-to)。
//
// 責務:
// - テキスト検索(前方一致+部分一致)と結果からのジャンプ要求
// - ランダム銀河/記事へのジャンプ
// - home へのリセット
//
// 注意:
// - テレポートはしない。jumpRequest 経由で core が fly-to する。
// - 検索は O(N) 線形(1,535 件なので実用上十分)。

import type { BootstrapData, TileIndex } from '@/types/catalog'
import { useStore } from './store'
import { peekTile, fetchTile } from '@/data/tileCache'

// 検索コマンド。
export class Commands {
  private boot: BootstrapData
  private index: TileIndex

  // bootstrap/index からコマンドを生成する。
  constructor(boot: BootstrapData, index: TileIndex) {
    this.boot = boot
    this.index = index
  }

  // 銀河を検索する(前方一致優先、最大 max 件)。
  searchGalaxies(q: string, max = 12): { gid: number; label: string; n: number }[] {
    const qq = q.trim().toLowerCase()
    if (qq.length === 0) return []
    const pre: { gid: number; label: string; n: number }[] = []
    const part: { gid: number; label: string; n: number }[] = []
    for (const g of this.boot.galaxies) {
      const l = g.label.toLowerCase()
      if (l.startsWith(qq)) pre.push({ gid: g.gid, label: g.label, n: g.n })
      else if (l.includes(qq)) part.push({ gid: g.gid, label: g.label, n: g.n })
      if (pre.length >= max) break
    }
    return [...pre, ...part].slice(0, max)
  }

  // 銀河へジャンプする(銀河が星として解像する距離へ fly-to)。
  gotoGalaxy(gid: number): void {
    const g = this.boot.galaxies.find(x => x.gid === gid)
    if (!g) return
    useStore.setState({ selection: { kind: 'galaxy', gid } })
    useStore.getState().requestJump([g.x, g.y, g.z], Math.max(g.r * 5, 12))
  }

  // ランダムな銀河へジャンプする(規模中位以上から抽選)。
  randomGalaxy(): void {
    const cands = this.boot.galaxies.filter(g => g.n >= 200)
    const list = cands.length > 0 ? cands : this.boot.galaxies
    const g = list[Math.floor(Math.random() * list.length)]
    this.gotoGalaxy(g.gid)
  }

  // ランダムな記事へジャンプする(銀河へ接近→星を1つ選ぶ)。
  randomArticle(): void {
    const cands = this.boot.galaxies.filter(g => g.n >= 100)
    const list = cands.length > 0 ? cands : this.boot.galaxies
    const g = list[Math.floor(Math.random() * list.length)]
    // 記事スケールまで接近(星が解像する距離)。
    useStore.getState().requestJump([g.x, g.y, g.z], Math.max(g.r * 1.2, 4))
    // タイルがあれば記事パネルを試みる(非同期)。
    fetchTile(g.gid)
      .then(tile => {
        if (!tile || tile.n === 0) return
        const local = Math.floor(Math.random() * tile.n)
        const pos = tile.pos ? [tile.pos[local * 3], tile.pos[local * 3 + 1], tile.pos[local * 3 + 2]] as [number, number, number] : null
        if (pos) useStore.getState().requestJump(pos, Math.max(g.r * 0.06, 1.5))
        useStore.setState({
          selection: { kind: 'article', gid: g.gid, local },
          panel: {
            kind: 'article',
            gid: g.gid,
            local,
            galaxyTitle: g.label,
            title: tile.titles[local] ?? `local#${local}`,
            deg: tile.deg[local] ?? 0,
            crossTargets: [],
          },
        })
      })
      .catch(() => {})
  }

  // home(全体俯瞰)へリセットする。
  reset(): void {
    useStore.setState({ selection: { kind: 'none' }, panel: { kind: 'overview', galaxies: this.index.count, macros: this.boot.macros.length } })
    useStore.getState().requestJump([0, 0, 0], 2478)
  }

  // 記事の現在位置を取得する(キャッシュ済みタイルから)。
  articlePos(gid: number, local: number): [number, number, number] | null {
    const tile = peekTile(gid)
    if (!tile?.pos || local >= tile.n) return null
    return [tile.pos[local * 3], tile.pos[local * 3 + 1], tile.pos[local * 3 + 2]]
  }
}
