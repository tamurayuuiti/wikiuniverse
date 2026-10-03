// src/ui/Hud.tsx
// 上部 HUD(v6): 統計・ズーム帯読み取り・視界幅・トグル・z スライダ・検索・ジャンプ。
//
// 責務:
// - stats(fps/浮上数/タイル数/視界幅)とズーム帯ラベル・潜入フォーカスの表示
// - 表示トグル(星/銀河内エッジ/クロスリンク/ラベル/球殻/星屑)
// - z-compress スライダ・検索(連続 fly-to)・ランダム/ホーム
//
// 注意:
// - 検索はテレポートではなく fly-to(理想形=連続性)。

import { useState } from 'react'
import { useStore } from '@/state/store'
import type { Commands } from '@/state/commands'

// HUD の props。
interface Props {
  commands: Commands | null
}

// ゾーム帯の日本語ラベル。
const ZOOM_JA: Record<string, string> = {
  universe: '宇宙(銀河団スケール)',
  cluster: '銀河団(銀河スケール)',
  galaxy: '銀河(星スケール)',
  article: '記事(ego スケール)',
}

// 上部 HUD。
export function Hud({ commands }: Props) {
  const stats = useStore(s => s.stats)
  const zoom = useStore(s => s.zoomLabel)
  const focusInfo = useStore(s => s.focusInfo)
  const toggles = useStore(s => s.toggles)
  const setToggle = useStore(s => s.setToggle)
  const zSquash = useStore(s => s.zSquash)
  const setZSquash = useStore(s => s.setZSquash)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<{ gid: number; label: string; n: number }[]>([])
  const [open, setOpen] = useState(false)

  // 検索入力の変化を処理する。
  const onSearch = (v: string) => {
    setQ(v)
    if (!commands) return
    setResults(v.trim().length > 0 ? commands.searchGalaxies(v) : [])
    setOpen(v.trim().length > 0)
  }

  // 結果を選択してジャンプする。
  const pick = (gid: number) => {
    commands?.gotoGalaxy(gid)
    setOpen(false)
    setQ('')
    setResults([])
  }

  return (
    <div className="hud">
      <div className="hud-group">
        <span className="hud-title">WikiUniverse</span>
        <span className="hud-stat zoom">{ZOOM_JA[zoom] ?? zoom}</span>
        {focusInfo && (
          <span className="hud-stat zoom" title="潜入フォーカス(非フォーカスは減光中)">
            潜入{focusInfo.kind === 'galaxy' ? '銀河' : '銀河団'}: {focusInfo.label} {Math.round(focusInfo.w * 100)}%
          </span>
        )}
      </div>
      <div className="hud-group">
        <span className="hud-stat">fps {stats.fps}</span>
        <span className="hud-stat">浮上 {stats.emerged}</span>
        <span className="hud-stat">タイル {stats.tiles}</span>
        <span className="hud-stat">視界幅 {fmtU(stats.viewWidthU ?? 0)}</span>
      </div>
      <div className="hud-group">
        <label className="hud-check">
          <input type="checkbox" checked={toggles.stars} onChange={e => setToggle('stars', e.target.checked)} />
          星
        </label>
        <label className="hud-check">
          <input type="checkbox" checked={toggles.edges} onChange={e => setToggle('edges', e.target.checked)} />
          バンドル
        </label>
        <label className="hud-check">
          <input type="checkbox" checked={toggles.cross} onChange={e => setToggle('cross', e.target.checked)} />
          クロス
        </label>
        <label className="hud-check">
          <input type="checkbox" checked={toggles.labels} onChange={e => setToggle('labels', e.target.checked)} />
          ラベル
        </label>
      </div>
      <div className="hud-group">
        <span className="hud-stat">z {zSquash.toFixed(2)}</span>
        <input
          className="hud-slider"
          type="range"
          min={0.4}
          max={1}
          step={0.01}
          value={zSquash}
          onChange={e => setZSquash(Number(e.target.value))}
        />
      </div>
      <div className="hud-group search-wrap">
        <input
          className="hud-search"
          type="text"
          placeholder="銀河検索…"
          value={q}
          onChange={e => onSearch(e.target.value)}
          onFocus={() => q.trim().length > 0 && setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
        />
        {open && results.length > 0 && (
          <div className="search-results">
            {results.map(r => (
              <button key={r.gid} className="search-item" onMouseDown={() => pick(r.gid)}>
                <span className="search-label">{r.label}</span>
                <span className="search-n">{r.n}</span>
              </button>
            ))}
          </div>
        )}
      </div>
      <div className="hud-group">
        <button className="hud-btn" onClick={() => commands?.randomGalaxy()}>
          ランダム銀河
        </button>
        <button className="hud-btn" onClick={() => commands?.randomArticle()}>
          ランダム記事
        </button>
        <button className="hud-btn" onClick={() => commands?.reset()}>
          home
        </button>
      </div>
    </div>
  )
}

// 視界幅を読みやすく整形する。
function fmtU(u: number): string {
  if (u >= 1000) return `${(u / 1000).toFixed(1)}k u`
  if (u >= 10) return `${Math.round(u)} u`
  return `${u.toFixed(1)} u`
}
