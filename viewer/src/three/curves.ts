// src/three/curves.ts
// 銀河間/マクロ間のバンドルをベジエ曲線として描画する補助を行う。
//
// 責務:
// - 端点列から曲線 LineSegments を生成する
//
// 注意:
// - z 圧縮(zK)は呼び出し側が端点に込みで渡す。

import * as THREE from 'three'

// 曲線バンドルの 1 セグメント。
export interface ArcInput {
  a: THREE.Vector3
  b: THREE.Vector3
  w: number
}

// 曲線バンドル群を 1 つの LineSegments にまとめて生成する。
export function curvedLines(
  arcs: ArcInput[],
  color: number,
  opacity: number,
  bow = 0.18,
): THREE.LineSegments {
  const pos: number[] = []
  arcs.forEach(({ a, b, w }) => {
    // 非有限端点は NaN ジオメトリとなるため描画対象から除外する。
    if (!Number.isFinite(a.x + a.y + a.z + b.x + b.y + b.z)) return
    const mid = a.clone().add(b).multiplyScalar(0.5)
    const dir = b.clone().sub(a)
    const len = dir.length()
    const perp = new THREE.Vector3().crossVectors(dir, new THREE.Vector3(0, 0, 1)).normalize()
    if (perp.lengthSq() < 1e-6) perp.set(1, 0, 0)
    // 同一経路の完全重なりを避けるため重み下位ビットで弓なり方向を散らす。
    mid.addScaledVector(perp, len * bow * (((w % 7)) - 3) / 3)
    const curve = new THREE.QuadraticBezierCurve3(a, mid, b)
    const pts = curve.getPoints(10)
    for (let i = 1; i < pts.length; i++) {
      pos.push(pts[i - 1].x, pts[i - 1].y, pts[i - 1].z, pts[i].x, pts[i].y, pts[i].z)
    }
  })
  const geo = new THREE.BufferGeometry()
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(pos), 3))
  return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({
    color, transparent: true, opacity,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }))
}
