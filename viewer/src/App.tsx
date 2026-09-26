// src/App.tsx
// ビューアのルートコンポーネント。canvas と HUD 群を合成する。
//
// 責務:
// - ViewerCore の生成/破棄とリサイズ追従
// - グローバルキー(Esc)の bind
//
// 注意:
// - three の描画ループは React 管理外。ここは mount/unmount のみを行う。

import { useEffect, useRef } from 'react'
import { commands } from '@/state/commands'
import { ViewerCore } from '@/three/core'
import { Hud } from './ui/Hud'
import { InfoPanel } from './ui/InfoPanel'
import { Tooltip } from './ui/Tooltip'

// ルートコンポーネント。
export default function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fadeRef = useRef<HTMLDivElement>(null)

  // 描画コアの生命周期管理。
  useEffect(() => {
    const core = new ViewerCore(canvasRef.current as HTMLCanvasElement, fadeRef.current)
    void core.init()
    const onResize = () => core.resize()
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') commands?.leave() }
    addEventListener('resize', onResize)
    addEventListener('keydown', onKey)
    return () => {
      removeEventListener('resize', onResize)
      removeEventListener('keydown', onKey)
    }
  }, [])

  return (
    <div className="relative h-full w-full bg-black">
      <canvas ref={canvasRef} className="block h-full w-full" />
      <Hud />
      <InfoPanel />
      <Tooltip />
      <div ref={fadeRef}
        className="pointer-events-none absolute inset-0 bg-black opacity-0 transition-opacity duration-300" />
    </div>
  )
}
