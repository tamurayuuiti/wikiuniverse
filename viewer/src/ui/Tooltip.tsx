// src/ui/Tooltip.tsx
// ホバー時のフロートチップを描画する。
//
// 責務:
// - store.hover のテキストと座標の表示
//
// 注意:
// - pointer-events なし( picking を妨げない)。

import { useViewer } from '@/state/store'

// ホバーチップコンポーネント。
export function Tooltip() {
  const s = useViewer()
  if (!s.hover) return null
  return (
    <div
      className="pointer-events-none absolute z-20 rounded border border-slate-700 bg-slate-950/90 px-2 py-0.5 font-mono text-[11px] text-slate-200"
      style={{ left: s.hover.x + 14, top: s.hover.y + 10 }}>
      {s.hover.text}
    </div>
  )
}
