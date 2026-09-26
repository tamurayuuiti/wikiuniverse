// src/ui/Tooltip.tsx
// ホバー時のフロートチップ(v6): 記事/銀河/マクロの名称と補足。
//
// 責務:
// - store.hover の表示(タイトル+info)
//
// 注意:
// - pointer-events なし(picking を妨げない)。

import { useStore } from '@/state/store'

// ホバーチップコンポーネント。
export function Tooltip() {
  const hover = useStore(s => s.hover)
  if (!hover) return null
  const kindJa = hover.kind === 'article' ? '記事' : hover.kind === 'galaxy' ? '銀河' : '銀河団'
  return (
    <div className="tooltip" style={{ left: hover.x + 14, top: hover.y + 10 }}>
      <span className="tooltip-kind">{kindJa}</span>
      <span className="tooltip-title">{hover.title}</span>
      {hover.info && <span className="tooltip-info">{hover.info}</span>}
    </div>
  )
}
