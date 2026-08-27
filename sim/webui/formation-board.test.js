"use strict";

/* 盤面の**挙動**を測る。ソース文字列への正規表現（「そう書いたか」）は
   置かない — 書いてあることは動くことの証拠にならない。実際、初版の
   「一覧の札で交代できない」「名前が1文字＋…で読めない」はどちらも
   文字列検査を全部通り抜けた（§7.91）。画面まで通す検査は
   tools/ui_smoke.py（実ブラウザ）が受け持つ。 */

const assert = require("node:assert/strict");
const { FORMATIONS, normalizeSlots, swapOrMove, rankPosition,
        FormationBoard } = require("./app.js");

// ── 陣形＝前後衛の枠数 ────────────────────────────
assert.deepEqual(
  Object.fromEntries(Object.entries(FORMATIONS).map(([k, v]) => [k, [v.front, v.rear]])),
  { kakuyoku: [4, 2], gyorin: [3, 3], gankou: [2, 4] }
);

// ── 6要素へ正規化・入れ替え・空枠への移動 ────────────
assert.deepEqual(normalizeSlots(["a", null, "c"]), ["a", null, "c", null, null, null]);
assert.deepEqual(swapOrMove(["a", "b", "c", "d", "e", "f"], 1, 4),
                 ["a", "e", "c", "d", "b", "f"]);
assert.deepEqual(swapOrMove(["a", null, "c", null, null, null], 0, 1),
                 [null, "a", "c", null, null, null]);
// 入れ替えで武将が増えも減りもしない
assert.equal(swapOrMove(["a", "b", "c", "d", "e", "f"], 1, 4).filter(Boolean).length, 6);
// 空枠を掴んでも何も起きない
assert.deepEqual(swapOrMove([null, "b", "c", null, null, null], 0, 1),
                 [null, "b", "c", null, null, null]);
assert.deepEqual([0, 1, 2, 3].map((i) => rankPosition(i, 4)),
                 ["左端", "中央左", "中央右", "右端"]);

// ── 盤面の操作は onSlotsChange を1回だけ呼ぶ ─────────
function stubBoard(start) {
  const board = Object.create(FormationBoard.prototype);
  board.root = { querySelectorAll: () => [] };
  board.selectedIndex = null;
  board.keyboardTargetIndex = null;
  board.announcement = "";
  board.calls = 0;
  board.props = {
    interactive: true,
    slots: normalizeSlots(start),
    units: Object.fromEntries(start.filter(Boolean).map((id) => [id, { name: id }])),
    onSlotsChange(next) { board.calls++; board.props.slots = next; },
  };
  global.requestAnimationFrame = (fn) => fn();
  return board;
}

let b = stubBoard(["a", "b", "c", "d", "e", "f"]);
b.selectedIndex = 0; b.keyboardTargetIndex = 5;
b.commit(0, 5);
assert.deepEqual(b.props.slots, ["f", "b", "c", "d", "e", "a"]);
assert.equal(b.calls, 1);
assert.equal(b.selectedIndex, null, "確定したら選択は解ける");

b = stubBoard(["a", null, "c", null, null, null]);
b.commit(0, 1);
assert.deepEqual(b.props.slots, [null, "a", "c", null, null, null]);
assert.equal(b.calls, 1);

// ── 枠から外せる（旧UIの ✕。無くすと満枠の編成が編集不能になる）──
b = stubBoard(["a", "b", "c", "d", "e", "f"]);
b.removeSlot(2);
assert.deepEqual(b.props.slots, ["a", "b", null, "d", "e", "f"]);
assert.equal(b.calls, 1);
assert.match(b.announcement, /外しました/);
// 空枠を外そうとしても呼ばない
b.removeSlot(2);
assert.equal(b.calls, 1);

// ── 選択を保つ場所（武将一覧）で押しても選択は消えない ────
//    ここを無条件に消していたせいで「駒を選ぶ→一覧の札を押す」交代が
//    成立しなかった。押した先が [data-keep-selection] の内側なら残す。
function fakeTarget(keep) {
  return { closest: (sel) => (keep && sel === "[data-keep-selection]" ? {} : null) };
}
b = stubBoard(["a", "b", "c", "d", "e", "f"]);
b.selectedIndex = 1;
b.root.contains = () => false;                       // 盤面の外で押した
b.render = () => {};
assert.equal(b.shouldClearOnOutside(fakeTarget(true)), false, "一覧の上で押したら選択を保つ");
assert.equal(b.shouldClearOnOutside(fakeTarget(false)), true, "本当の外なら解除する");
b.root.contains = () => true;
assert.equal(b.shouldClearOnOutside(fakeTarget(false)), false, "盤面の中は外ではない");
b.root.contains = () => false;
b.selectedIndex = null;
assert.equal(b.shouldClearOnOutside(fakeTarget(false)), false, "選択が無ければ何もしない");

// ── 名簿に無い札は「空き枠」ではなく「使えない駒」として描く ──
//    空に見えるのに何も置けない枠は、盤面が嘘をつくのと同じ。
b = stubBoard(["a", "b", "c", "d", "e", "f"]);
b.props.formation = "gyorin";
delete b.props.units.c;                                // 名簿から消えた札
const html = b.slotHTML(2, "c");
assert.match(html, /fb-piece[^"]*occupied/, "居ることは見せる");
assert.match(html, /unknown/, "使えない印を付ける");
assert.match(html, /fb-remove/, "✕ で外せる");
assert.doesNotMatch(b.slotHTML(2, "c"), /fb-empty-mark[^>]*>＋/, "空き枠として描かない");
assert.match(b.slotHTML(3, null), /fb-empty-mark/, "本当の空き枠は空き枠");

// ── 駒に触れたら onPieceTap が鳴る（詳細欄への接続・§7.119）─────
b = stubBoard(["a", null, "c", null, null, null]);
b.render = () => {};
b.taps = [];
b.props.onPieceTap = (id) => b.taps.push(id);
b.tapSlot(0);
assert.deepEqual(b.taps, ["a"], "駒に触れたら知らせる");
assert.equal(b.selectedIndex, 0, "選択の挙動はそのまま");
b.tapSlot(1);
assert.deepEqual(b.taps, ["a"], "空き枠では鳴らない");
b.selectedIndex = null;
b.tapSlot(2);
assert.deepEqual(b.taps, ["a", "c"]);
// onPieceTap を渡していない盤面（リプレイ等）は今までどおり
b = stubBoard(["a", null, null, null, null, null]);
b.render = () => {};
b.tapSlot(0);
assert.equal(b.selectedIndex, 0);

console.log("FormationBoard logic: ok");
