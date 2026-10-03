// src/App.tsx
// 連続宇宙ビューア(v6)のルート: bootstrap ロード → ViewerCore 生成 → HUD/パネル配置。
//
// 責務:
// - bootstrap.json の取得と TileIndex 構築
// - canvas と ViewerCore のライフサイクル管理
// - Commands の生成と HUD/InfoPanel への受け渡し
//
// 注意:
// - StrictMode の二重マウントに備え、dispose で確実に破棄する。
// - ロード失敗はエラー表示(旧 fade 機構は廃止、連続宇宙に切替はない)。

import { useEffect, useRef, useState } from 'react'
import { loadBootstrap, buildIndex } from '@/data/bootstrap'
import { ViewerCore } from '@/three/core'
import { Commands } from '@/state/commands'
import { useStore } from '@/state/store'
import { Hud } from '@/ui/Hud'
import { InfoPanel } from '@/ui/InfoPanel'
import { Tooltip } from '@/ui/Tooltip'
import type { BootstrapData, TileIndex } from '@/types/catalog'

// アプリのルートコンポーネント。
export default function App() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const coreRef = useRef<ViewerCore | null>(null)
  const [cmds, setCmds] = useState<Commands | null>(null)
  const [boot, setBoot] = useState<BootstrapData | null>(null)
  const [index, setIndex] = useState<TileIndex | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ready = useStore(s => s.ready)

  // 没入モード: アイドリング計測(html[data-ui] を CSS が参照して UI をフェード)。
  useEffect(() => {
    const root = document.documentElement
    let timer = window.setTimeout(() => root.setAttribute('data-ui', 'idle'), 3600)
    const wake = (): void => {
      root.setAttribute('data-ui', 'awake')
      window.clearTimeout(timer)
      timer = window.setTimeout(() => root.setAttribute('data-ui', 'idle'), 3600)
    }
    root.setAttribute('data-ui', 'awake')
    const events = ['pointermove', 'pointerdown', 'keydown', 'wheel'] as const
    for (const ev of events) window.addEventListener(ev, wake, { passive: true })
    return () => {
      window.clearTimeout(timer)
      for (const ev of events) window.removeEventListener(ev, wake)
    }
  }, [])

  // bootstrap をロードし、core を生成する。
  useEffect(() => {
    let cancelled = false
    let core: ViewerCore | null = null
    ;(async () => {
      try {
        const boot = await loadBootstrap()
        const index = buildIndex(boot)
        if (cancelled) return
        setBoot(boot)
        setIndex(index)
        setCmds(new Commands(boot, index))
        if (canvasRef.current) {
          core = new ViewerCore(canvasRef.current, boot, index)
          coreRef.current = core
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => {
      cancelled = true
      core?.dispose()
      coreRef.current?.dispose()
      coreRef.current = null
    }
  }, [])

  // canvas 準備後に core が未生成なら生成する(初回マウント順序の保険)。
  useEffect(() => {
    if (boot && index && canvasRef.current && !coreRef.current) {
      const core = new ViewerCore(canvasRef.current, boot, index)
      coreRef.current = core
    }
  }, [boot, index])

  return (
    <div className="app">
      <div id="viewer-root" className="viewer-root">
        <canvas ref={canvasRef} className="viewer-canvas" />
      </div>
      {!ready && !error && (
        <div className="loading-overlay">
          <div className="loading-text">loading universe…</div>
        </div>
      )}
      {error && (
        <div className="loading-overlay">
          <div className="loading-text error">
            bootstrap の読み込みに失敗しました。
            <br />
            リポジトリルートで <code>python -m http.server 8000</code> を起動し、
            <code>viewer/</code> で <code>npm run dev</code> を実行してください。
            <br />
            <small>{error}</small>
          </div>
        </div>
      )}
      <Hud commands={cmds} />
      <InfoPanel commands={cmds} />
      <Tooltip />
    </div>
  )
}
