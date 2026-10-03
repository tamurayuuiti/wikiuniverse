// src/data/bootstrap.ts
// bootstrap.json の取得と解析(compact → 表示用 + TileIndex)。
//
// 責務:
// - fetch(no-store、?v=generated_at でキャッシュ回避)
// - マクロ/銀河の型付け、色相の決定論的割り当て、アンカー z の復元
// - バンドル(pair)の整形
//
// 注意:
// - gid は galaxies 配列の添字(生成側の規約)。
// - hue はデータにないため黄金比ハッシュで決定論的に生成する。
// - 現行グローバル配置では銀河アンカー z = マクロ z(dir_z=0)→ gz に利用。

import type { BootstrapData, GalaxyMeta, RawBootstrap, TileIndex } from '@/types/catalog'

// このビューアが解釈できる公開面契約のバージョン(wu/publish.py の
// SCHEMA_VERSION と対になる。形式変更時は両方を合わせて更新する)。
const SUPPORTED_SCHEMA_VERSION = 1

// 黄金比ハッシュで [0,1) を返す。
function hash01(i: number, salt = 0): number {
  const x = Math.sin((i + 1) * 12.9898 + salt * 78.233) * 43758.5453
  return x - Math.floor(x)
}

// bootstrap.json を取得する。
export async function loadBootstrap(): Promise<BootstrapData> {
  const res = await fetch('/data/spatial/bootstrap.json', { cache: 'no-store' })
  if (!res.ok) throw new Error(`bootstrap.json ${res.status}`)
  const raw: RawBootstrap = await res.json()
  return parseBootstrap(raw)
}

// compact 形式を解析して表示用データへ変換する。
export function parseBootstrap(raw: RawBootstrap): BootstrapData {
  // 契約バージョンの許容チェック: 生成側(wu/publish.py の SCHEMA_VERSION)が
  // このビューアの既知バージョンを超えていたら、位置配列の意味がずれて
  // 静かに壊れ得るので明示的に警告する(読み込みは続ける = 前方互容の努力)。
  // schema_version 無しの旧 bootstrap は version 1 として扱う。
  const schemaVersion = raw.meta.schema_version ?? 1
  if (schemaVersion > SUPPORTED_SCHEMA_VERSION) {
    console.warn(
      `[bootstrap] schema_version=${schemaVersion} はこのビューアの既知上限 ` +
      `${SUPPORTED_SCHEMA_VERSION} を超えます(生成側が新しい。表示が壊れる可能性)`)
  }
  const macros = raw.macros.map((m, i) => ({
    mid: i,
    x: m[0],
    y: m[1],
    z: m[2],
    r: Math.max(m[3], 0.5),
    n: m[4],
    label: m[5] || `銀河団${i + 1}`,
    n_galaxies: 0,
    hue: (i * 0.6180339887) % 1,
  }))
  const galaxies = raw.galaxies.map((g, i) => {
    const mid = g[4]
    const macroHue = macros[mid]?.hue ?? 0.6
    return {
      gid: i,
      x: g[0],
      y: g[1],
      z: g[2],
      r: Math.max(g[3], 0.5),
      macro: mid,
      display_class: g[5],
      n: g[6],
      label: g[7] || `galaxy#${i}`,
      n_cross: g[9],
      hue: (macroHue + (hash01(i) - 0.5) * 0.07 + 1) % 1,
    }
  })
  for (const g of galaxies) {
    if (macros[g.macro]) macros[g.macro].n_galaxies++
  }
  return {
    version: raw.meta.generated_at,
    generated_at: raw.meta.generated_at,
    nArticles: raw.meta.n_articles,
    macros,
    galaxies,
    macroBundles: raw.macro_pairs.map(p => ({ a: p[0], b: p[1], w: p[2] })),
    galaxyBundles: raw.galaxy_pairs.map(p => ({ a: p[0], b: p[1], w: p[2] })),
  }
}

// 表示用データから TileIndex を構築する。
export function buildIndex(boot: BootstrapData): TileIndex {
  const galaxies = new Map<number, GalaxyMeta>()
  for (const g of boot.galaxies) {
    const macro = boot.macros[g.macro]
    galaxies.set(g.gid, {
      gid: g.gid,
      mid: g.macro,
      n: g.n,
      n_cross: g.n_cross,
      display_class: g.display_class,
      x: g.x,
      y: g.y,
      z: g.z,
      r: g.r,
      hue: g.hue,
      gz: macro ? macro.z : 0,
    })
  }
  return {
    count: galaxies.size,
    version: boot.version,
    generated_at: boot.generated_at,
    galaxies,
  }
}
