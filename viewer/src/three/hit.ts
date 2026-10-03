// src/three/hit.ts
// hover/選択記事のパッキングキー(銀河 id × ローカル index)。
//
// 責務:
// - (gid, local) を単一の整数キーへ安全に pack/unpack する(純関数)。
//
// 注意:
// - 旧実装のビットシフト方式((g << 24) | local + 算術シフト復元)は
//   gid >= 128 で符号により破綻し、gid >= 256 では >>> でも 8bit に
//   切り捨てられて破綻した(実データの gid は 0..1970 = 選択強調が
//   ~6.5% の銀河でしか機能していなかった根因)。乗算方式へ置換済み。
// - 上限: gid < 2^32、local < 2^21 = 2,097,152(実測の最大銀河 9,828 記事・
//   1,971 銀河に対し十分な余裕)。key は Number.MAX_SAFE_INTEGER を超えない。
// - 境界の回帰テスト = tests/hit.test.ts(npm run test:unit)。

/** local index に割り当てるビット数(2^21 = 2,097,152 記事/銀河まで)。 */
export const LOCAL_BITS = 21

const LOCAL_SPAN = 2 ** LOCAL_BITS

/** 「なし」を表すキー値(負 = pack 結果と衝突しない)。 */
export const HIT_NONE = -1

/** (gid, local) をキーへ pack する。 */
export function packHit(g: number, local: number): number {
  return g * LOCAL_SPAN + local
}

/** キーから gid を復元する(HIT_NONE → -1)。 */
export function unpackG(key: number): number {
  return key < 0 ? -1 : Math.floor(key / LOCAL_SPAN)
}

/** キーから local index を復元する(HIT_NONE → -1)。 */
export function unpackLocal(key: number): number {
  return key < 0 ? -1 : key % LOCAL_SPAN
}
