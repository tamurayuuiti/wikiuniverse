// src/main.tsx
// エントリポイント。React ルートを mount する。
//
// 責務:
// - createRoot による App mount とグローバル CSS 適用
//
// 注意:
// - StrictMode は three ループの二重 mount を招くため意図的に無効化する。

import { createRoot } from 'react-dom/client'
import App from './App'
import './index.css'

createRoot(document.getElementById('root') as HTMLElement).render(<App />)
