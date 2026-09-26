// src/data/base.ts
// データサーバの基底 URL を解決する。
//
// 責務:
// - 開発時(vite proxy)と本番(同一オリジン/CDN)の差分を環境変数に吸収する
//
// 注意:
// - 末尾スラッシュを必ず保持する。

// データ取得の基底パス。VITE_DATA_BASE で上書き可能。
export const DATA_BASE: string =
  (import.meta.env.VITE_DATA_BASE as string | undefined) ?? '/data/spatial/'
