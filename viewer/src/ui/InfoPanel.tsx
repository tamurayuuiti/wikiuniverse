// src/ui/InfoPanel.tsx
// 右側の情報パネル(宇宙/銀河/記事の 3 モデル)を描画する。
//
// 責務:
// - store.panel モデルの表示とジャンプ系コマンドの発行
//
// 注意:
// - 記事 target のジャンプは潜入+showArticle を連鎖させる(jumpToArticle)。

import { commands } from '@/state/commands'
import { useViewer } from '@/state/store'

// 右パネルコンポーネント。
export function InfoPanel() {
  const s = useViewer()
  const p = s.panel
  return (
    <div className="absolute right-3 top-2 z-10 max-h-[80vh] w-[340px] overflow-auto rounded-lg border border-slate-800 bg-slate-950/85 p-3 font-mono text-[12px] leading-relaxed text-slate-300">
      {p.kind === 'universe' && (
        <>
          <h3 className="mb-1 text-[14px] text-white">wikiuniverse</h3>
          <div>1,516,326 articles / 1,971 galaxies / 63 macros(+dust)</div>
          <div className="mt-2 text-slate-500">
            nested-scale viewer: a focused galaxy is drawn at comfort scale and its
            neighbours / parent macro / universe appear as context shells outside.
            click a halo to enter.
          </div>
        </>
      )}
      {p.kind === 'loading' && (
        <>
          <h3 className="mb-1 text-[14px] text-white">galaxy #{p.g}</h3>
          <div className="text-slate-500">loading tile…</div>
        </>
      )}
      {p.kind === 'galaxy' && (
        <>
          <h3 className="mb-1 text-[14px] text-white">galaxy #{p.g}</h3>
          <div className="mb-2">
            {p.neighbors.slice(0, 8).map(nb => (
              <span key={nb.g} className="mr-2 cursor-pointer text-sky-300 underline"
                onClick={() => commands?.enterGalaxy(nb.g)}>
                {nb.g}({nb.cnt})
              </span>
            ))}
          </div>
          <div className="text-slate-500">ring sprites = neighbours (click to jump) · click a star for its links</div>
        </>
      )}
      {p.kind === 'article' && (
        <>
          <h3 className="mb-1 text-[14px] text-white">{p.title}</h3>
          <div>internal deg {p.deg} · cross {p.targets.length}</div>
          <div className="mt-2">
            {p.targets.map((t, i) => (
              <div key={i}>
                <span className="cursor-pointer text-fuchsia-300 underline"
                  onClick={() => commands?.jumpToArticle(t.g, t.local)}>
                  {(t.title || '?').slice(0, 34)} → #{t.g}
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
