// src/ui/InfoPanel.tsx
// 左下情報パネル(v6): コンテキスト依存(overview / galaxy / article)。
//
// 責務:
// - 選択なし: 全体統計と操作ガイド
// - 銀河選択: 銀河情報+代表ハブ記事
// - 記事選択: 記事情報+実クロスリンク先(クリックで対象銀河へ fly-to)
//
// 注意:
// - パネルは表示専用コンテキスト。描画・カメラは distance/px 駆動(core 側)。

import { useStore } from '@/state/store'
import type { Commands } from '@/state/commands'

// InfoPanel の props。
interface Props {
  commands: Commands | null
}

// 左下の情報パネル。
export function InfoPanel({ commands }: Props) {
  const panel = useStore(s => s.panel)

  return (
    <div className="panel info-panel">
      {panel.kind === 'overview' && (
        <>
          <h2>Wikipedia Universe</h2>
          <div className="panel-stats">
            <div>
              <span className="k">銀河</span>
              <span className="v">{panel.galaxies.toLocaleString()}</span>
            </div>
            <div>
              <span className="k">銀河団</span>
              <span className="v">{panel.macros}</span>
            </div>
          </div>
          <div className="panel-hint">
            wheel: ズーム(連続) / drag: 回転 / 銀河クリック: 接近(星が湧く) /
            星クリック: 記事選択+実リンク表示 / Esc: 一段上昇 / 検索・ランダムは右上
          </div>
        </>
      )}
      {panel.kind === 'loading' && <div className="panel-hint">{panel.label}…</div>}
      {panel.kind === 'galaxy' && (
        <>
          <div className="panel-eyebrow">銀河{panel.displayClass === 'medium' ? '(媒体)' : panel.displayClass === 'dust' ? '(塵)' : ''}</div>
          <h2>{panel.title}</h2>
          <div className="panel-stats">
            <div>
              <span className="k">記事</span>
              <span className="v">{panel.n.toLocaleString()}</span>
            </div>
            <div>
              <span className="k">外部リンク</span>
              <span className="v">{panel.nCross.toLocaleString()}</span>
            </div>
          </div>
          <div className="panel-eyebrow">所属: {panel.macroTitle}</div>
          {panel.hubTitles.length > 0 && (
            <>
              <div className="panel-eyebrow">代表記事(ハブ)</div>
              <ul className="panel-list">
                {panel.hubTitles.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            </>
          )}
          <div className="panel-hint">さらにズームインすると星(記事)が湧き、内部リンクと実クロスリンクが立ち上がります。</div>
        </>
      )}
      {panel.kind === 'article' && (
        <>
          <div className="panel-eyebrow">記事 @ {panel.galaxyTitle}</div>
          <h2>{panel.title}</h2>
          <div className="panel-stats">
            <div>
              <span className="k">タイル内次数</span>
              <span className="v">{panel.deg.toLocaleString()}</span>
            </div>
            {panel.macroTitle && (
              <div>
                <span className="k">銀河団</span>
                <span className="v">{panel.macroTitle}</span>
              </div>
            )}
          </div>
          {panel.crossTargets.length > 0 && (
            <>
              <div className="panel-eyebrow">実リンク先(他銀河)</div>
              <div className="link-row">
                {panel.crossTargets.map((t, i) => (
                  <button key={i} className="link-chip" onClick={() => commands?.gotoGalaxy(t.gid)} title={`${t.title} へ fly-to`}>
                    {t.title}
                  </button>
                ))}
              </div>
            </>
          )}
          <div className="panel-hint">明るいラインがこの記事の実リンク網(ego)です。Esc で選択解除。</div>
        </>
      )}
    </div>
  )
}
