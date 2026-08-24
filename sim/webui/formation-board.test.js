"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { FORMATIONS, normalizeSlots, swapOrMove, rankPosition, FormationBoard } = require("./app.js");

assert.deepEqual(
  Object.fromEntries(Object.entries(FORMATIONS).map(([k, v]) => [k, [v.front, v.rear]])),
  { kakuyoku: [4, 2], gyorin: [3, 3], gankou: [2, 4] }
);

assert.deepEqual(normalizeSlots(["a", null, "c"]), ["a", null, "c", null, null, null]);
assert.deepEqual(swapOrMove(["a", "b", "c", "d", "e", "f"], 1, 4),
                 ["a", "e", "c", "d", "b", "f"]);
assert.deepEqual(swapOrMove(["a", null, "c", null, null, null], 0, 1),
                 [null, "a", "c", null, null, null]);
assert.equal(swapOrMove(["a", "b", "c", "d", "e", "f"], 1, 4).filter(Boolean).length, 6);
assert.deepEqual([0, 1, 2, 3].map((i) => rankPosition(i, 4)),
                 ["左端", "中央左", "中央右", "右端"]);

function commitTest(start, from, to) {
  let calls = 0;
  const board = Object.create(FormationBoard.prototype);
  board.root = { querySelectorAll: () => [] };
  board.selectedIndex = from;
  board.keyboardTargetIndex = to;
  board.announcement = "";
  board.props = {
    slots: normalizeSlots(start),
    units: Object.fromEntries(start.filter(Boolean).map((id) => [id, { name: id }])),
    onSlotsChange(next) { calls++; board.props.slots = next; },
  };
  global.requestAnimationFrame = (fn) => fn();
  board.commit(from, to);
  return { calls, slots: board.props.slots };
}

assert.deepEqual(commitTest(["a", "b", "c", "d", "e", "f"], 0, 5),
                 { calls: 1, slots: ["f", "b", "c", "d", "e", "a"] });
assert.deepEqual(commitTest(["a", null, "c", null, null, null], 0, 1),
                 { calls: 1, slots: [null, "a", "c", null, null, null] });

const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "app.css"), "utf8");
assert.doesNotMatch(js, /\bdraggable\b|dragstart|dragend|ondrop|ondragover/);
assert.match(js, /<button type="button" class="fb-piece/);
assert.equal((js.match(/this\.props\.onSlotsChange\(/g) || []).length, 1);
assert.match(js, /if \(!this\.props\.interactive\) return;/);
assert.match(js, /unit\.name.*pos\.row.*pos\.position.*unit\.troopType.*unit\.cost/s);
assert.match(css, /\.fb-piece:focus-visible/);
assert.match(css, /\.fb-piece\s*\{[^}]*min-width:\s*64px[^}]*min-height:\s*64px/s);
assert.match(css, /\.fb-piece\.occupied\s*\{\s*touch-action:\s*none/);
assert.match(css, /\.army-zone\.foe\s*\{[^}]*border:\s*2px dashed/s);
assert.match(css, /\.fb-piece\.gi\s*\{\s*border-color:\s*#46689c/);
assert.match(js, /<div class="army-zone-label"><b>敵軍<\/b>/);

console.log("FormationBoard logic: ok");
