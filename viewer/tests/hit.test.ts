// tests/hit.test.ts — hit.ts(パッキングキー)のスクリプト式回帰テスト。
// 実行: npm run test:unit(esbuild でバンドルし node で実行。ランナー依存なし)。
//
// 旧実装((g << 24) | local + 算術シフト)は gid >= 128 で破綻していた。
// 境界値(127/128/255/256/1024/1970)と極値の往復・一意性・HIT_NONE を検査する。

import { packHit, unpackG, unpackLocal, HIT_NONE } from '../src/three/hit.js'

let failures = 0
function check(cond: boolean, msg: string): void {
  if (!cond) {
    failures++
    console.error(`FAIL: ${msg}`)
  }
}

// 往復(旧バグの境界 + 実データ上限 + 理論極値)。
for (const g of [0, 1, 127, 128, 255, 256, 1000, 1024, 1970, 65535, 4_000_000]) {
  for (const local of [0, 1, 42, 9827, 2_097_151]) {
    const k = packHit(g, local)
    check(Number.isSafeInteger(k), `key が安全整数でない: g=${g} local=${local}`)
    check(unpackG(k) === g, `gid 復元失敗: g=${g} local=${local} → ${unpackG(k)}`)
    check(unpackLocal(k) === local, `local 復元失敗: g=${g} local=${local} → ${unpackLocal(k)}`)
  }
}

// 一意性(異なる記事が衝突しない)。
const seen = new Set<number>()
for (const g of [127, 128, 255, 256, 1970]) {
  for (const local of [0, 1, 9827]) {
    const k = packHit(g, local)
    check(!seen.has(k), `キー衝突: g=${g} local=${local} key=${k}`)
    seen.add(k)
  }
}

// 単調性(gid 昇順 → キー昇順 = 範囲走査の前提)。
check(packHit(128, 0) > packHit(127, 2_097_151), 'gid 境界での単調性')

// HIT_NONE の意味(復元は -1)。
check(unpackG(HIT_NONE) === -1, 'HIT_NONE → g=-1')
check(unpackLocal(HIT_NONE) === -1, 'HIT_NONE → local=-1')

if (failures > 0) {
  console.error(`*** HIT KEY TEST FAILED (${failures} failures) ***`)
  throw new Error('hit key test failed')
}
console.log('*** HIT KEY TEST PASSED ***')
