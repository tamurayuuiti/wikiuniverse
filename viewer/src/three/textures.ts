// src/three/textures.ts
// 点/ラベル描画用のテクスチャとシェーダマテリアルを生成する。
//
// 責務:
// - 放射グラデーションのcanvasテクスチャ生成(星・ハロー)
// - ラベル用canvasテクスチャ生成
// - ピクセル単位サイズを支える星ポイント用ShaderMaterial
//
// 注意:
// - ShaderMaterial は gl_PointSize をピクセルで受け取る。devicePixelRatio は
//   呼び出し側が aSize に込みで渡すこと。

import * as THREE from 'three'

// 放射グラデーションのcanvasテクスチャを生成する。
export function radialTex(stops: [number, string][]): THREE.CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d') as CanvasRenderingContext2D
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32)
  stops.forEach(s => gr.addColorStop(s[0], s[1]))
  g.fillStyle = gr
  g.fillRect(0, 0, 64, 64)
  return new THREE.CanvasTexture(c)
}

// 星スプライト用の glow テクスチャ。
export const STAR_TEX = radialTex([
  [0, 'rgba(255,255,255,1)'],
  [0.25, 'rgba(255,255,255,.9)'],
  [0.55, 'rgba(210,230,255,.28)'],
  [1, 'rgba(210,230,255,0)'],
])

// 銀河/隣接ハロー用のソフトテクスチャ。
export const HALO_TEX = radialTex([
  [0, 'rgba(255,255,255,.9)'],
  [0.35, 'rgba(255,255,255,.35)'],
  [1, 'rgba(255,255,255,0)'],
])

// テキストラベルのテクスチャとアスペクト比を生成する。
export function labelTex(text: string): [THREE.CanvasTexture, number] {
  const c = document.createElement('canvas')
  let g = c.getContext('2d') as CanvasRenderingContext2D
  g.font = '28px monospace'
  const w = Math.ceil(g.measureText(text).width) + 16
  c.width = w
  c.height = 40
  g = c.getContext('2d') as CanvasRenderingContext2D
  g.font = '28px monospace'
  g.fillStyle = 'rgba(220,230,255,.95)'
  g.fillText(text, 8, 28)
  const t = new THREE.CanvasTexture(c)
  t.minFilter = THREE.LinearFilter
  return [t, w / 40]
}

// 星ポイント用シェーダの頂点側。aSize はピクセル単位。
const STAR_VS = `attribute float aSize; attribute vec3 aColor; varying vec3 vC;
void main(){ vC=aColor; vec4 mv=modelViewMatrix*vec4(position,1.0);
  gl_Position=projectionMatrix*mv; gl_PointSize=aSize; }`

// 星ポイント用シェーダのフラグメント側。テクスチャ.alpha で丸く抜く。
const STAR_FS = `uniform sampler2D uMap; varying vec3 vC;
void main(){ vec4 t=texture2D(uMap,gl_PointCoord); if(t.a<0.02) discard;
  gl_FragColor=vec4(vC,1.0)*t; }`

// 加算ブレンドの星ポイントマテリアルを生成する。
export function starMaterial(): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    uniforms: { uMap: { value: STAR_TEX } },
    vertexShader: STAR_VS,
    fragmentShader: STAR_FS,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  })
}

// マクロ id から識別色を導出する(golden angle 配色)。
export function macroColor(id: number): THREE.Color {
  return new THREE.Color().setHSL(((id * 137.508) % 360) / 360, 0.72, 0.6)
}
