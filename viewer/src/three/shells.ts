// src/three/shells.ts
// 選択銀河の球殻インジケータ(ワイヤー球殻)。
//
// 責務:
// - 銀河の広がり(レイアウト球 r)を示す扁平ワイヤー球の線分生成
// - 表示は「選択中の銀河のみ」(2026-10-03 のユーザー方針: 常時装飾だった球殻を
//   選択範囲インジケータへ転用。デフォルト非表示)
//
// 注意:
// - **将来の削除候補としてこのファイルへ分離している**。削除手順 = 本ファイル削除 +
//   universeLayer の「球殻」箇所(galShells の構築配列・update の表示制御・remove)+
//   core.ts の selectedGid 受け渡し(計 3 ファイル・数十行)で完結する。
// - 球殻の半径 = 銀河のレイアウト球 r。座標側の包含クランプにより全記事が
//   球内に収まるため、「クリック/選択対象の広がりの可視化」という意味を持つ。
// - 潜入時(veil)は消灯する(画面が線で覆われるのを防ぐ = focus.ts の設計に追従)。

import * as THREE from 'three'

// 球殻の基本不透明度(選択表示時のピーク値。dim/veil が乗る)。
export const SHELL_OPACITY = 0.2

// ワイヤー球殻の線分を生成する(緯線リング + 経線、y×0.6 の扁平球)。
export function makeShellLines(
  x: number, y: number, z: number, r: number,
  hue: number, sat: number, light: number, renderOrder: number,
): THREE.LineSegments {
  const pts: number[] = []
  const rings = 7
  const segs = 48
  for (let i = 1; i < rings; i++) {
    const phi = (i / rings) * Math.PI
    const rr = r * Math.sin(phi)
    const yy = r * Math.cos(phi) * 0.6
    for (let j = 0; j < segs; j++) {
      const t0 = (j / segs) * Math.PI * 2
      const t1 = ((j + 1) / segs) * Math.PI * 2
      pts.push(x + rr * Math.cos(t0), y + yy, z + rr * Math.sin(t0))
      pts.push(x + rr * Math.cos(t1), y + yy, z + rr * Math.sin(t1))
    }
  }
  const meridians = 9
  for (let k = 0; k < meridians; k++) {
    const th = (k / meridians) * Math.PI * 2
    for (let j = 0; j < 24; j++) {
      const p0 = (j / 24) * Math.PI
      const p1 = ((j + 1) / 24) * Math.PI
      pts.push(x + r * Math.sin(p0) * Math.cos(th), y + r * Math.cos(p0) * 0.6, z + r * Math.sin(p0) * Math.sin(th))
      pts.push(x + r * Math.sin(p1) * Math.cos(th), y + r * Math.cos(p1) * 0.6, z + r * Math.sin(p1) * Math.sin(th))
    }
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  const color = new THREE.Color().setHSL(hue, sat, light)
  const mat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0, depthWrite: false })
  const ls = new THREE.LineSegments(geo, mat)
  ls.frustumCulled = false
  ls.renderOrder = renderOrder
  ls.visible = false
  return ls
}
