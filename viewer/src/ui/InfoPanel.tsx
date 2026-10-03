// src/ui/InfoPanel.tsx
// 左下情報パネル(没入型): コンテキスト依存(overview / galaxy / article)。
//
// 責務:
// - 選択なし: compact な概況と操作ヒント
// - 銀河選択: 統計 + 代表記事(クリックで記事へ = gotoArticle)
// - 記事選択: 統計 + Wikipedia 外部リンク + 実クロスリンク先(クリックで fly-to)
//
// 注意:
// - パネルは表示専用コンテキスト。描画・カメラは distance/px 駆動(core 側)。
// - Wikipedia リンクは local#NN(タイトル未解決のフォールバック)では表示しない。

import { useStore } from '@/state/store'
import type { Commands } from '@/state/commands'

// InfoPanel の props。
interface Props {
  commands: Commands | null
}

// 記事タイトルから Wikipedia(ja)の URL を作る(空白はアンダースコア化)。
function wikiUrl(title: string): string {
  return `https://ja.wikipedia.org/wiki/${encodeURIComponent(title.replace(/ /g, '_'))}`
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
            wheel ズーム(連続)· 銀河クリックで接近 · 星クリックで記事選択 · Esc で一段上昇 ·{' '}
            <kbd>/</kbd> 検索
          </div>
        </>
      )}
      {panel.kind === 'loading' && <div className="panel-hint">{panel.label}…</div>}
      {panel.kind === 'galaxy' && (
        <>
          <div className="panel-eyebrow">
            銀河{panel.displayClass === 'medium' ? '(媒介)' : panel.displayClass === 'dust' ? '(塵)' : ''}
          </div>
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
              <div className="panel-eyebrow">代表記事(ハブ)— クリックで記事へ</div>
              <div className="link-row">
                {panel.hubTitles.map(h => (
                  <button
                    key={h.local}
                    className="link-chip"
                    onClick={() => commands?.gotoArticle(panel.gid, h.local)}
                    title={`${h.title} へ fly-to + 選択`}
                  >
                    {h.title}
                  </button>
                ))}
              </div>
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
          {!panel.title.startsWith('local#') && (
            <a className="wiki-link" href={wikiUrl(panel.title)} target="_blank" rel="noopener noreferrer">
              Wikipedia で開く <span aria-hidden="true">↗</span>
            </a>
          )}
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
          <div className="panel-hint">明るい太線がこの記事の実リンク網(ego)です。Esc で選択解除。</div>
        </>
      )}
    </div>
  )
}
