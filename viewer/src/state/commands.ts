// src/state/commands.ts
// UI 起点コマンド: 統合検索・ジャンプ・リセット(全て連続 fly-to)。
//
// 責務:
// - 統合検索(銀河 = 全件 / 記事 = ロード済みタイル内。前方一致優先+部分一致)
// - 銀河・記事へのジャンプ(記事は core への選択要求 = ego 網・パネルと一括駆動)
// - ランダム銀河/記事、home リセット
//
// 注意:
// - テレポートはしない。jumpRequest / selectRequest 経由で core が fly-to する。
// - 銀河検索は O(N) 線形(1,971 件なので実用上十分)。
// - 記事検索の対象は「タイルキャッシュにロード済みの銀河」のみ(L1 の制限)。
//   将来の全体インデックス(publish が出版する検索用データ = 公開面 schema の
//   拡張世代)へ差し替えても UI 契約(SearchHit)が変わらない形にしてある。

import type { BootstrapData, TileIndex } from '@/types/catalog'
import { useStore } from './store'
import { peekTile, fetchTile, cachedTiles } from '@/data/tileCache'

// 統合検索の 1 件。
export interface SearchHit {
  kind: 'galaxy' | 'article'
  gid: number
  /** 記事のみのタイル内 rank。 */
  local?: number
  label: string
  /** 補足(銀河 = 記事数、記事 = 所属銀河名)。 */
  sub?: string
}

// UI 起点コマンド。
export class Commands {
  private boot: BootstrapData
  private index: TileIndex

  // bootstrap/index からコマンドを生成する。
  constructor(boot: BootstrapData, index: TileIndex) {
    this.boot = boot
    this.index = index
  }

  // 統合検索(銀河 → 記事の順、前方一致優先。各 max 件まで)。
  search(q: string, maxGal = 8, maxArt = 8): SearchHit[] {
    const qq = q.trim().toLowerCase()
    if (qq.length === 0) return []
    const out: SearchHit[] = []
    // 銀河(bootstrap 全件)。
    const gPre: SearchHit[] = []
    const gPart: SearchHit[] = []
    for (const g of this.boot.galaxies) {
      const l = g.label.toLowerCase()
      const hit: SearchHit = { kind: 'galaxy', gid: g.gid, label: g.label, sub: `${g.n} 記事` }
      if (l.startsWith(qq)) gPre.push(hit)
      else if (l.includes(qq)) gPart.push(hit)
      if (gPre.length >= maxGal) break
    }
    out.push(...gPre.slice(0, maxGal), ...gPart.slice(0, Math.max(0, maxGal - gPre.length)))
    // 記事(ロード済みタイルのみ。デバウンスは呼び出し側)。
    const aPre: SearchHit[] = []
    const aPart: SearchHit[] = []
    for (const [gid, tile] of cachedTiles()) {
      const glabel = this.boot.galaxies[gid]?.label ?? `galaxy#${gid}`
      for (let i = 0; i < tile.n; i++) {
        const t = tile.titles[i]
        if (!t || t.startsWith('local#')) continue
        const tl = t.toLowerCase()
        const hit: SearchHit = { kind: 'article', gid, local: i, label: t, sub: glabel }
        if (tl.startsWith(qq)) aPre.push(hit)
        else if (tl.includes(qq)) aPart.push(hit)
        if (aPre.length >= maxArt && aPart.length >= maxArt) break
      }
      if (aPre.length >= maxArt && aPart.length >= maxArt) break
    }
    out.push(...aPre.slice(0, maxArt), ...aPart.slice(0, Math.max(0, maxArt - aPre.length)))
    return out
  }

  // 銀河へジャンプする(銀河が星として解像する距離へ fly-to)。
  gotoGalaxy(gid: number): void {
    // gid = boot.galaxies の添字(bootstrap.ts の規約)なので直接参照できる。
    const g = this.boot.galaxies[gid]
    if (!g) return
    useStore.setState({ selection: { kind: 'galaxy', gid } })
    useStore.getState().requestJump([g.x, g.y, g.z], Math.max(g.r * 5, 12))
  }

  // 記事へジャンプし選択する(fly-to + ego 網 + パネルは core が一括駆動)。
  gotoArticle(gid: number, local: number): void {
    fetchTile(gid)
      .then(tile => {
        if (!tile || local >= tile.n) return
        useStore.getState().requestSelectArticle(gid, local)
      })
      .catch(() => {})
  }

  // ランダムな銀河へジャンプする(規模中位以上から抽選)。
  randomGalaxy(): void {
    const cands = this.boot.galaxies.filter(g => g.n >= 200)
    const list = cands.length > 0 ? cands : this.boot.galaxies
    const g = list[Math.floor(Math.random() * list.length)]
    this.gotoGalaxy(g.gid)
  }

  // ランダムな記事へジャンプする(銀河へ接近 → 記事を 1 つ選択)。
  randomArticle(): void {
    const cands = this.boot.galaxies.filter(g => g.n >= 100)
    const list = cands.length > 0 ? cands : this.boot.galaxies
    const g = list[Math.floor(Math.random() * list.length)]
    // 記事スケールまで接近(星が解像する距離)。
    useStore.getState().requestJump([g.x, g.y, g.z], Math.max(g.r * 1.2, 4))
    fetchTile(g.gid)
      .then(tile => {
        if (!tile || tile.n === 0) return
        const local = Math.floor(Math.random() * tile.n)
        // 選択は core 経由(ego 網・パネル・記事位置への fly-to と一貫させる)。
        useStore.getState().requestSelectArticle(g.gid, local)
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
