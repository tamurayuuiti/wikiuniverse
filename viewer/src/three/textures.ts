// src/three/textures.ts
// 手焼きテクスチャ生成(塊スプライト用グロー・選択リング)。
//
// 責務:
// - 放射グラデーションスプライトの生成(塊グロー・選択リング)
//
// 注意:
// - 外部画像に依存しない(CanvasTexture のみ)。

import * as THREE from 'three'

// 放射グラデーションの星スプライトを生成する。
export function makeStarSprite(size = 256, coreExp = 1.9, fallExp = 3.4): THREE.CanvasTexture {
  const cv = document.createElement('canvas')
  cv.width = size
  cv.height = size
  const ctx = cv.getContext('2d')
  if (!ctx) return new THREE.CanvasTexture(cv)
  const c = size / 2
  const grad = ctx.createRadialGradient(c, c, 0, c, c, c)
  grad.addColorStop(0, 'rgba(255,255,255,1)')
  grad.addColorStop(0.18, 'rgba(255,250,240,0.85)')
  grad.addColorStop(0.45, 'rgba(255,240,220,0.28)')
  grad.addColorStop(1, 'rgba(255,235,210,0)')
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.arc(c, c, c, 0, Math.PI * 2)
  ctx.fill()
  // 中心コアを強調(冪で絞り込む)。
  const img = ctx.getImageData(0, 0, size, size)
  const d = img.data
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dx = (x - c) / c
      const dy = (y - c) / c
      const r = Math.min(1, Math.hypot(dx, dy))
      const core = Math.pow(Math.max(0, 1 - r), coreExp)
      const fall = Math.pow(Math.max(0, 1 - r), fallExp)
      const i = (y * size + x) * 4
      const a = Math.max(core * 0.9, fall)
      d[i + 3] = Math.min(255, Math.round(a * 255))
    }
  }
  ctx.putImageData(img, 0, 0)
  const tex = new THREE.CanvasTexture(cv)
  tex.needsUpdate = true
  return tex
}

// 選択記事のハイライトリング(円環の放射グラデーション)を生成する。
export function makeRingSprite(size = 128): THREE.CanvasTexture {
  const cv = document.createElement('canvas')
  cv.width = size
  cv.height = size
  const ctx = cv.getContext('2d')
  if (!ctx) return new THREE.CanvasTexture(cv)
  const c = size / 2
  const grad = ctx.createRadialGradient(c, c, 0, c, c, c)
  grad.addColorStop(0.0, 'rgba(255,240,205,0)')
  grad.addColorStop(0.58, 'rgba(255,240,205,0)')
  grad.addColorStop(0.70, 'rgba(255,240,205,0.55)')
  grad.addColorStop(0.76, 'rgba(255,250,230,1)')
  grad.addColorStop(0.82, 'rgba(255,240,205,0.5)')
  grad.addColorStop(0.92, 'rgba(255,235,195,0.10)')
  grad.addColorStop(1.0, 'rgba(255,235,195,0)')
  ctx.fillStyle = grad
  ctx.beginPath()
  ctx.arc(c, c, c, 0, Math.PI * 2)
  ctx.fill()
  const tex = new THREE.CanvasTexture(cv)
  tex.needsUpdate = true
  return tex
}
