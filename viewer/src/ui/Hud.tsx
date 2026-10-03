// src/ui/Hud.tsx
// 没入型 HUD: 最小限のクローム(自動非表示)+ 下部ドック(統合検索・クイック・レイヤ)。
//
// 責務:
// - 左上: ブランドとズーム文脈(ズーム帯 + 潜入フォーカス読み取り)
// - 右上: コンパクト統計(fps・浮上・タイル・視界幅)
// - 下部ドック: 統合検索(銀河+記事)、ランダム銀河/記事、レイヤ popover(トグル+z)、home
// - 自動非表示は html[data-ui](App がアイドリング計測)-driven。このコンポーネントは
//   構造とインタラクションのみ担う
//
// 注意:
// - 検索はテレポートではなく fly-to(理想形=連続性)。
// - 記事検索はロード済みタイル内が対象(L1)。全体インデックス(publish 出版)への
//   差し替えは Commands.search の内部実装のみで完結する(UI 契約は SearchHit で安定)。
// - '/' または Ctrl+K で検索フォーカス。入力中の Esc はカメラ上昇へ伝播させない。

import { useEffect, useRef, useState } from 'react'
import { useStore } from '@/state/store'
import type { Commands, SearchHit } from '@/state/commands'

// HUD の props。
interface Props {
  commands: Commands | null
}

// ゾーム帯の日本語ラベル。
const ZOOM_JA: Record<string, string> = {
  universe: '宇宙',
  cluster: '銀河団',
  galaxy: '銀河',
  article: '記事',
}

// レイヤ トグルの定義(キーと表示名)。
const LAYERS = [
  ['stars', '星(記事)'],
  ['edges', 'バンドル'],
  ['cross', 'クロスリンク'],
  ['labels', 'ラベル'],
] as const

// 没入型 HUD(上部チップ 2 つ + 下部ドック)。
export function Hud({ commands }: Props) {
  const stats = useStore(s => s.stats)
  const zoom = useStore(s => s.zoomLabel)
  const focusInfo = useStore(s => s.focusInfo)
  const toggles = useStore(s => s.toggles)
  const setToggle = useStore(s => s.setToggle)
  const zSquash = useStore(s => s.zSquash)
  const setZSquash = useStore(s => s.setZSquash)
  const [q, setQ] = useState('')
  const [results, setResults] = useState<SearchHit[]>([])
  const [openSearch, setOpenSearch] = useState(false)
  const [openLayers, setOpenLayers] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const debounceRef = useRef<number | null>(null)

  // '/' または Ctrl+K で検索にフォーカス(入力中の再入は除外)。
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const tag = (e.target as HTMLElement | null)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === '/' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // 検索入力(デバウンス付き。記事検索はタイル走査のため毎キーストロークを避ける)。
  const onSearch = (v: string): void => {
    setQ(v)
    if (debounceRef.current !== null) window.clearTimeout(debounceRef.current)
    if (!commands || v.trim().length === 0) {
      setResults([])
      setOpenSearch(false)
      return
    }
    debounceRef.current = window.setTimeout(() => {
      setResults(commands.search(v))
      setOpenSearch(true)
    }, 140)
  }

  // 結果を選択してジャンプする。
  const pick = (hit: SearchHit): void => {
    if (hit.kind === 'galaxy') commands?.gotoGalaxy(hit.gid)
    else if (hit.local != null) commands?.gotoArticle(hit.gid, hit.local)
    setOpenSearch(false)
    setQ('')
    setResults([])
  }

  return (
    <>
      <div className="hud hud-tl">
        <div className="chip brand">
          <span className="brand-dot" />
          <span className="brand-name">WIKIUNIVERSE</span>
          <span className="chip-sep" />
          <span className="brand-zoom">{ZOOM_JA[zoom] ?? zoom}</span>
          {focusInfo && (
            <span className="brand-focus" title="潜入フォーカス(非フォーカス実体は減光中)">
              潜入{focusInfo.kind === 'galaxy' ? '銀河' : '銀河団'} {focusInfo.label} {Math.round(focusInfo.w * 100)}%
            </span>
          )}
        </div>
      </div>

      <div className="hud hud-tr">
        <div className="chip stats">
          <span>{stats.fps} fps</span>
          <span className="chip-sep">·</span>
          <span>浮上 {stats.emerged}</span>
          <span className="chip-sep">·</span>
          <span>タイル {stats.tiles}</span>
          <span className="chip-sep">·</span>
          <span>視界幅 {fmtU(stats.viewWidthU ?? 0)}</span>
        </div>
      </div>

      <div className="dock">
        {openSearch && results.length > 0 && (
          <div className="search-results">
            {results.map((r, i) => (
              <button key={`${r.kind}-${r.gid}-${r.local ?? ''}-${i}`} className="search-item" onMouseDown={() => pick(r)}>
                <span className={`search-kind ${r.kind}`}>{r.kind === 'galaxy' ? '銀河' : '記事'}</span>
                <span className="search-label">{r.label}</span>
                {r.sub && <span className="search-sub">{r.sub}</span>}
              </button>
            ))}
          </div>
        )}
        {openLayers && (
          <div className="layers-pop">
            <div className="layers-title">表示レイヤ</div>
            {LAYERS.map(([k, ja]) => (
              <label key={k} className="layer-row">
                <input type="checkbox" checked={toggles[k]} onChange={e => setToggle(k, e.target.checked)} />
                <span>{ja}</span>
              </label>
            ))}
            <div className="layer-row z-row">
              <span className="z-label">z 圧縮</span>
              <input
                type="range"
                min={0.4}
                max={1}
                step={0.01}
                value={zSquash}
                onChange={e => setZSquash(Number(e.target.value))}
              />
              <span className="z-val">{zSquash.toFixed(2)}</span>
            </div>
            <div className="layers-note">z 圧縮は地図ビュー(1.0 = 純 3D)。レイアウトは焼き直さない</div>
          </div>
        )}
        <div className="dock-bar">
          <div className="dock-search">
            <svg className="dock-search-icon" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true">
              <circle cx="7" cy="7" r="4.6" fill="none" stroke="currentColor" strokeWidth="1.6" />
              <line x1="10.6" y1="10.6" x2="14" y2="14" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            </svg>
            <input
              ref={inputRef}
              type="text"
              placeholder="銀河・記事を検索…  ( / )"
              value={q}
              onChange={e => onSearch(e.target.value)}
              onFocus={() => {
                if (q.trim().length > 0 && results.length > 0) setOpenSearch(true)
              }}
              onBlur={() => window.setTimeout(() => setOpenSearch(false), 150)}
              onKeyDown={e => {
                if (e.key === 'Escape') {
                  // カメラの Esc 上昇(core の window リスナ)へ伝播させない。
                  e.stopPropagation()
                  setOpenSearch(false)
                  inputRef.current?.blur()
                }
              }}
            />
          </div>
          <button className="dock-btn" onClick={() => commands?.randomGalaxy()} title="規模中位以上の銀河へランダムジャンプ">
            ランダム銀河
          </button>
          <button className="dock-btn" onClick={() => commands?.randomArticle()} title="記事へランダムジャンプ(接近+選択)">
            ランダム記事
          </button>
          <button
            className={`dock-btn ${openLayers ? 'active' : ''}`}
            onClick={() => setOpenLayers(v => !v)}
            title="表示レイヤと z 圧縮"
          >
            レイヤ
          </button>
          <button className="dock-btn icon" onClick={() => commands?.reset()} title="home(全体俯瞰)へ戻る">
            ⌂
          </button>
        </div>
      </div>
    </>
  )
}

// 視界幅を読みやすく整形する。
function fmtU(u: number): string {
  if (u >= 1000) return `${(u / 1000).toFixed(1)}k u`
  if (u >= 10) return `${Math.round(u)} u`
  return `${u.toFixed(1)} u`
}
