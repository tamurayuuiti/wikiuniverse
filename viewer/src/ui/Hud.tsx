// src/ui/Hud.tsx
// 左上 HUD(状態表示/zスライダー/検索/トグル/back)を描画する。
//
// 責務:
// - ビューア状態の表示とコマンド発行(描画ロジックは持たない)
//
// 注意:
// - 毎フレーム値(fps)は 1Hz 更新の store 値のみを表示する。

import { useState } from 'react'
import { commands } from '@/state/commands'
import { useViewer } from '@/state/store'
import type { Toggles } from '@/state/store'

// トグルボタンの定義順。
const TOGGLE_DEFS: { key: keyof Toggles; label: string }[] = [
  { key: 'edges', label: 'edges' },
  { key: 'cross', label: 'cross' },
  { key: 'shells', label: 'shells' },
  { key: 'labels', label: 'labels' },
  { key: 'dust', label: 'dust' },
  { key: 'stars', label: 'stars' },
]

// 左上 HUD コンポーネント。
export function Hud() {
  const s = useViewer()
  const [q, setQ] = useState('')
  return (
    <div className="absolute left-3 top-2 z-10 font-mono text-[12px] leading-relaxed text-slate-400">
      <div>
        wikiuniverse <b className="text-slate-200">v4</b>(nested-scale)—{' '}
        <span className="text-sky-300">{s.stage === 'universe' ? 'universe' : `galaxy #${s.focus}`}</span>{' '}
        <span className="text-slate-600">{s.stats.fps}fps · tiles {s.stats.tiles}</span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <span>z-compress</span>
        <input
          type="range" min={0.4} max={1} step={0.05} value={s.zK}
          onChange={e => commands?.setZ(parseFloat(e.target.value))}
          className="w-36 accent-sky-400" />
        <span>{s.zK.toFixed(2)}</span>
        {s.stage === 'galaxy' && (
          <button onClick={() => commands?.leave()}
            className="rounded border border-slate-600 bg-slate-900 px-2 py-0.5 hover:border-sky-500">
            &larr; back (Esc)
          </button>
        )}
      </div>
      <div className="mt-1">
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { commands?.search(q); setQ('') } }}
          placeholder="search galaxy / macro… (Enter)"
          className="w-56 rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-slate-200 placeholder-slate-600" />
      </div>
      <div className="mt-1 flex gap-1">
        {TOGGLE_DEFS.map(t => (
          <button key={t.key}
            onClick={() => commands?.toggle(t.key)}
            className={(s.toggles[t.key]
              ? 'rounded border border-sky-500 bg-sky-900/60 px-2 py-0.5'
              : 'rounded border border-slate-700 bg-slate-900 px-2 py-0.5 opacity-60')}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="mt-1 text-slate-600">
        click halo=enter · click star=links · click ring=jump · drag=orbit · wheel=zoom
      </div>
    </div>
  )
}
