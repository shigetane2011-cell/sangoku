/* 三国志 卓上戦記 — 画面。サーバの /api/* を叩くだけで、規則の正本は持たない
   （検証の正は match.validate。ここでの表示はあくまで手元の目安）。 */
"use strict";

const DEBUG = typeof location !== "undefined"
  && new URLSearchParams(location.search).get("debug") === "1";

const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => [...(el || document).querySelectorAll(sel)];
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/* ── 地方日付と休戦時刻の表示 ─────────────────────
   YYYY-MM-DD を Date.parse へ直接渡すと UTC 扱いで前日にずれる環境があるため、
   数字に分けて地方日の Date を作る。規則の締切判定そのものはサーバーが正本。 */
const JP_WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

function localDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
  if (!m) return null;
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  return d.getFullYear() === +m[1] && d.getMonth() === +m[2] - 1
    && d.getDate() === +m[3] ? d : null;
}

function formatLocalDay(iso, suffix = "") {
  const d = localDay(iso);
  if (!d) return String(iso || "");
  return `${d.getMonth() + 1}/${d.getDate()}（${JP_WEEKDAYS[d.getDay()]}）${suffix}`;
}

function formatTruceDayLabel(iso, index) {
  return formatLocalDay(iso, index === 0 ? "今日" : (index === 1 ? "明日" : ""));
}

function formatHourRanges(hours) {
  const hs = [...new Set((hours || []).map(Number)
    .filter((h) => Number.isInteger(h) && h >= 0 && h < 24))].sort((a, b) => a - b);
  if (!hs.length) return "設定なし";
  const groups = [];
  for (const h of hs) {
    const last = groups[groups.length - 1];
    if (last && last[last.length - 1] + 1 === h) last.push(h);
    else groups.push([h]);
  }
  let wrap = null;
  if (groups.length > 1 && groups[0][0] === 0
      && groups[groups.length - 1].slice(-1)[0] === 23) {
    const first = groups.shift();
    const last = groups.pop();
    wrap = { start: last[0], end: first[first.length - 1] + 1 };
  }
  const labels = groups.map((g) => g.length === 1
    ? `${g[0]}時` : `${g[0]}〜${g[g.length - 1] + 1}時`);
  if (wrap) labels.push(`${wrap.start}〜翌${wrap.end}時`);
  return labels.join("・");
}

function truceDayCanMove(day) {
  if (!day) return false;
  const locked = new Set(day.locked || []);
  const picked = new Set(day.hours || []);
  return [...picked].some((h) => !locked.has(h))
    && Array.from({ length: 24 }, (_, h) => h)
      .some((h) => !locked.has(h) && !picked.has(h));
}

function initialTruceDayIndex(tr) {
  if (!tr || !tr.days || tr.days.length < 2) return 0;
  return truceDayCanMove(tr.days[0]) ? 0 : 1;
}

function truceHoursFor(tr, dayIndex, scope = "day") {
  const src = scope === "default" ? tr.default_hours
    : ((tr.days || [])[dayIndex] || {}).hours;
  return Array.isArray(src) ? [...src] : [...(tr.default_hours || [])];
}

function truceSummaryText(tr) {
  const today = tr && tr.days && tr.days[0];
  const hours = today ? today.hours : ((tr && tr.default_hours) || []);
  return `今日 ${formatHourRanges(hours)}休戦`
    + (today && today.source === "day" ? "（個別変更）" : "");
}

function tenkaDayStats(rows) {
  const out = { n: 0, w: 0, l: 0, d: 0 };
  for (const m of rows || []) {
    const marks = m.marks || "";
    const w = (marks.match(/○/g) || []).length;
    const l = (marks.match(/●/g) || []).length;
    out.n++;
    out[w > l ? "w" : (l > w ? "l" : "d")]++;
  }
  return out;
}

/* ── 兵種印・コスト印（§7.59）: sim/webui/icons/ の絵を出す。
   絵は差し替え式で、無ければ枠だけが出る（規則の表示は文字が正）。 ── */
const TYPE_FILE = { "歩兵": "typ-shield", "騎兵": "typ-cav",
                    "弓兵": "typ-bow", "槍": "typ-spear" };
const TYPE_CLS = { "歩兵": "t-inf", "騎兵": "t-cav", "弓兵": "t-arc", "槍": "t-spr" };
function icoTyp(typ, spear) {
  const one = (t) => TYPE_FILE[t]
    ? `<img class="tico ${TYPE_CLS[t]}" src="/icons/${TYPE_FILE[t]}.png" alt="${t}"
         title="${t === "槍" ? "槍持ち（後衛可）" : t}" loading="lazy">`
    : "";
  return one(typ) + (spear ? one("槍") : "");
}

/* コスト印。1〜10の環に数字を重ねる（環は位が上がるほど豪華になる）。 */
function icoCost(n) {
  const k = Math.min(10, Math.max(1, Math.round(n)));
  return `<span class="cost-ring" data-c="${k}"><i>${n}</i></span>`;
}

/* ── 実盤面（編成・戦記・リプレイ共用） ──────────────────
   盤面状態の正本は props.slots の6要素だけ。DOMの並びや表示位置から
   武将順を逆算しない。formation は公開契約どおり英字キーを受け取る。 */
const FORMATIONS = {
  kakuyoku: { label: "鶴翼", front: 4, rear: 2 },
  gyorin: { label: "魚鱗", front: 3, rear: 3 },
  gankou: { label: "雁行", front: 2, rear: 4 },
};
const FORM_KEY = { "鶴翼": "kakuyoku", "魚鱗": "gyorin", "雁行": "gankou" };
const FORM_JP = Object.fromEntries(Object.entries(FORM_KEY).map(([jp, key]) => [key, jp]));
const FACTION_CLASS = { gi: "gi", shoku: "shoku", go: "go", gunyu: "gunyu" };

function normalizeSlots(value) {
  return Array.from({ length: 6 }, (_, i) => {
    const v = Array.isArray(value) ? value[i] : null;
    return v === undefined || v === "" ? null : v;
  });
}

function swapOrMove(slots, from, to) {
  const next = normalizeSlots(slots);
  if (from === to || from < 0 || from > 5 || to < 0 || to > 5 || !next[from]) return next;
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

function rankPosition(index, count) {
  const labels = {
    1: ["中央"],
    2: ["左", "右"],
    3: ["左", "中央", "右"],
    4: ["左端", "中央左", "中央右", "右端"],
  };
  return (labels[count] || [])[index] || `${index + 1}番`;
}

class FormationBoard {
  constructor(root, props) {
    this.root = root;
    this.props = props;
    this.selectedIndex = null;
    this.keyboardTargetIndex = null;
    this.pointer = null;
    this.announcement = "";
    this.documentPointerMove = (e) => this.onPointerMove(e);
    this.documentPointerUp = (e) => this.onPointerUp(e);
    this.documentPointerCancel = (e) => this.onPointerCancel(e);
    this.outsidePointerDown = (e) => {
      if (this.shouldClearOnOutside(e.target)) this.clearSelection();
    };
    document.addEventListener("pointerdown", this.outsidePointerDown, true);
    document.addEventListener("pointermove", this.documentPointerMove, { passive: false });
    document.addEventListener("pointerup", this.documentPointerUp);
    document.addEventListener("pointercancel", this.documentPointerCancel);
    this.render();
  }

  /* 盤面の外を押したとき選択を解くか。**選択を保つ場所**（武将一覧など、
     [data-keep-selection] の内側）は「外」と見なさない — ここを捕捉フェーズで
     無条件に消していたせいで、駒を選んでから一覧の札を押す「交代」が成立して
     いなかった（押した瞬間に選択が消え、一覧側の click が走る頃には選択なし）。 */
  shouldClearOnOutside(target) {
    if (!this.props.interactive || this.selectedIndex === null) return false;
    if (this.root.contains(target)) return false;
    if (target && target.closest && target.closest("[data-keep-selection]")) return false;
    return true;
  }

  setProps(props) {
    this.props = props;
    if (!props.interactive) {
      this.selectedIndex = null;
      this.keyboardTargetIndex = null;
      this.cancelPointer(false);
    }
    this.render();
  }

  destroy() {
    this.cancelPointer(false);
    document.removeEventListener("pointerdown", this.outsidePointerDown, true);
    document.removeEventListener("pointermove", this.documentPointerMove);
    document.removeEventListener("pointerup", this.documentPointerUp);
    document.removeEventListener("pointercancel", this.documentPointerCancel);
    this.root.innerHTML = "";
  }

  layout() {
    return FORMATIONS[this.props.formation] || FORMATIONS.gyorin;
  }

  slotMeta(index) {
    const form = this.layout();
    const front = index < form.front;
    const rowIndex = front ? index : index - form.front;
    const count = front ? form.front : form.rear;
    return { front, rowIndex, count, row: front ? "前衛" : "後衛",
             position: rankPosition(rowIndex, count) };
  }

  ariaLabel(index, unit) {
    const pos = this.slotMeta(index);
    if (!unit) return `空きスロット ${pos.row}${pos.position}`;
    if (unit.unknown) return `${unit.name}（いまは使えない） ${pos.row}${pos.position}`;
    return `${unit.name} ${pos.row}${pos.position} ${unit.troopType}`
      + `${unit.spear ? "・槍" : ""} コスト${unit.cost}`;
  }

  slotHTML(index, id) {
    let unit = id ? this.props.units[id] : null;
    // 名簿に無い武将（登用が変わった後の古い登録など）を**空き枠として描かない**。
    // 空に見えるのに何も置けない枠になり、盤面が嘘をつく。誰か居ることは見せて、
    // ✕ で外せるようにする。
    if (id && !unit) unit = { name: id, unknown: true, cost: "？", troopType: "" };
    const selected = index === this.selectedIndex;
    const candidate = index === this.keyboardTargetIndex && selected === false;
    const faction = unit ? (FACTION_CLASS[unit.faction] || unit.faction || "gunyu") : "";
    const disabled = this.props.interactive ? "" : " disabled";
    const state = [unit ? "occupied" : "empty", unit && unit.unknown ? "unknown" : "",
                   selected ? "selected" : "",
                   candidate ? "key-target" : ""].filter(Boolean).join(" ");
    // 名前は**駒の中に敷かない**。72pxの中へ8.5pxで押し込むと、どの武将も
    // 1文字＋「…」になって読めない（誰がどこに居るかを見る画面でそれは本末転倒）。
    // 顔は正方形のまま残し、名前は枠の下へ2行で出す。
    const piece = `<button type="button" class="fb-piece ${state} ${faction}"
      data-slot-index="${index}" aria-label="${esc(this.ariaLabel(index, unit))}"
      aria-pressed="${selected ? "true" : "false"}"${disabled}>
      ${!unit ? '<span class="fb-empty-mark" aria-hidden="true">＋</span>'
        : unit.unknown ? '<span class="fb-empty-mark" aria-hidden="true">？</span>'
        : `<img class="fb-portrait" src="${esc(unit.portraitUrl)}" alt="">
        <span class="fb-troop" aria-hidden="true">${icoTyp(unit.troopType, unit.spear)}</span>
        <span class="fb-cost num" aria-hidden="true">${esc(unit.cost)}</span>`}
    </button>`;
    // 枠から外す（旧UIの ✕）。駒の中に入れ子の button は置けないので兄弟にする。
    const rm = unit && this.props.interactive
      ? `<button type="button" class="fb-remove" data-remove-index="${index}"
           aria-label="${esc(unit.name)} を枠から外す" title="枠から外す">✕</button>` : "";
    const cap = !unit ? `<span class="fb-name empty">空き枠</span>`
      : unit.unknown
        ? `<span class="fb-name unknown">${esc(unit.name)}</span>`
          + `<span class="fb-note">使えない</span>`
        : `<span class="fb-name">${esc(unit.name)}</span>`;
    return piece + rm + cap;
  }

  render() {
    const form = this.layout();
    const slots = normalizeSlots(this.props.slots);
    const row = (front) => {
      const start = front ? 0 : form.front;
      const count = front ? form.front : form.rear;
      const rank = front ? "前衛" : "後衛";
      return `<div class="fb-rank ${front ? "front" : "rear"}">
        <span class="fb-rank-label">${rank}<small>${count}枠</small></span>
        <div class="fb-rank-slots" style="--slot-count:${count}">
          ${Array.from({ length: count }, (_, k) => {
            const i = start + k;
            return `<span class="fb-slot" data-slot-index="${i}">${this.slotHTML(i, slots[i])}</span>`;
          }).join("")}
        </div>
      </div>`;
    };
    this.root.className = `formation-board ${this.props.interactive ? "interactive" : "readonly"}`;
    // 読み上げ欄は**作り直さない**。支援技術が拾うのは「既にある live 領域の
    // 中身が変わったとき」で、領域ごと差し替えると読まれない。行だけ描き替え、
    // 文言は textContent で入れる。
    if (!this.live || !this.root.contains(this.live)) {
      this.root.innerHTML = `<div class="fb-rows"></div>`;
      this.live = document.createElement("span");
      this.live.className = "sr-only";
      this.live.setAttribute("aria-live", "polite");
      this.live.setAttribute("aria-atomic", "true");
      this.root.appendChild(this.live);
      this.rows = this.root.querySelector(".fb-rows");
    }
    // 選んでいる間だけ出す操作の帯。**指で押せる大きさの「外す」**はここ
    // （駒の隅の ✕ は 22px しかなく、触りの目安 44 を割る・§7.92）。
    // 何を選んでいるかが文字で見えるようになる利もある。
    const picked = this.props.interactive && this.selectedIndex !== null
      ? (this.props.units[slots[this.selectedIndex]] || {}).name
        || slots[this.selectedIndex] : null;
    const bar = picked ? `<div class="fb-bar">
      <span class="fb-bar-who">選択中　<b>${esc(picked)}</b></span>
      <span class="fb-bar-hint">別の駒か一覧の札を選ぶと交代</span>
      <button type="button" class="fb-bar-btn" data-bar="remove">枠から外す</button>
      <button type="button" class="fb-bar-btn ghost" data-bar="cancel">やめる</button>
    </div>` : "";
    this.rows.innerHTML = `${row(true)}${row(false)}${bar}`;
    if (this.live.textContent !== this.announcement) this.live.textContent = this.announcement;
    this.root.querySelectorAll(".fb-piece").forEach((button) => {
      if (!this.props.interactive) return;
      button.addEventListener("pointerdown", (e) => this.onPointerDown(e));
      button.addEventListener("keydown", (e) => this.onKeyDown(e));
    });
    this.root.querySelectorAll(".fb-remove").forEach((button) => {
      button.addEventListener("click", () => this.removeSlot(+button.dataset.removeIndex));
    });
    this.root.querySelectorAll(".fb-bar-btn").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.dataset.bar === "remove") this.removeSlot(this.selectedIndex);
        else this.clearSelection();
      });
    });
  }

  removeSlot(index) {
    if (!Number.isInteger(index)) return;
    const before = normalizeSlots(this.props.slots);
    const id = before[index];
    if (!id) return;
    const next = normalizeSlots(before);
    next[index] = null;
    const unit = this.props.units[id];
    this.announcement = `${unit ? unit.name : id}を枠から外しました`;
    this.selectedIndex = null;
    this.keyboardTargetIndex = null;
    this.props.onSlotsChange(next);
  }

  onPointerDown(e) {
    if (this.pointer || !this.props.interactive || (e.button !== undefined && e.button !== 0)) return;
    const button = e.currentTarget;
    const index = +button.dataset.slotIndex;
    this.pointer = { id: e.pointerId, from: index, startX: e.clientX, startY: e.clientY,
                     x: e.clientX, y: e.clientY, button, dragging: false, target: null,
                     moved: false, touch: e.pointerType === "touch",
                     canDrag: !!normalizeSlots(this.props.slots)[index] };
  }

  /* 掴んだと見なすか。**指のときは横に振ったときだけ**（§7.92）。
     駒に touch-action:none を敷くと、駒の上から始めた縦スクロールが
     ページではなくドラッグになる。読むための縦振りを盤面が奪ってはいけない。
     指での前後衛の入れ替えは**タップ2回**が正路（そちらは常に効く）。
     マウス・ペンには touch-action は掛からないので今までどおり全方向。 */
  dragBegins(p, dx, dy) {
    const dist = Math.hypot(dx, dy);
    if (!p.touch) return dist >= 8;
    return Math.abs(dx) >= 12 && Math.abs(dx) > Math.abs(dy) * 1.2;
  }

  onPointerMove(e) {
    const p = this.pointer;
    if (!p || e.pointerId !== p.id) return;
    p.x = e.clientX; p.y = e.clientY;
    const dx = e.clientX - p.startX, dy = e.clientY - p.startY;
    const distance = Math.hypot(dx, dy);
    p.moved = distance >= 8;
    if (!p.dragging && p.canDrag && this.dragBegins(p, dx, dy)) this.startDrag(e);
    if (!p.dragging) return;
    e.preventDefault();
    this.moveProxy(e.clientX, e.clientY);
    const below = document.elementFromPoint(e.clientX, e.clientY);
    const slot = below && below.closest ? below.closest(".fb-slot") : null;
    const target = slot && this.root.contains(slot) ? +slot.dataset.slotIndex : null;
    p.target = Number.isInteger(target) ? target : null;
    this.root.querySelectorAll(".fb-slot").forEach((el) => {
      const active = +el.dataset.slotIndex === p.target;
      el.classList.toggle("drop-target", active);
      el.classList.toggle("swap-target", active && !!normalizeSlots(this.props.slots)[p.target]);
    });
  }

  startDrag(e) {
    const p = this.pointer;
    p.dragging = true;
    try { p.button.setPointerCapture(p.id); } catch (_err) { /* 古いWebViewは継続 */ }
    p.button.classList.add("drag-source");
    const rect = p.button.getBoundingClientRect();
    const proxy = p.button.cloneNode(true);
    proxy.classList.add("fb-drag-proxy");
    proxy.removeAttribute("aria-pressed");
    proxy.style.width = `${rect.width}px`;
    proxy.style.height = `${rect.height}px`;
    document.body.appendChild(proxy);
    p.proxy = proxy;
    this.moveProxy(e.clientX, e.clientY);
  }

  moveProxy(x, y) {
    const p = this.pointer;
    if (!p || !p.proxy) return;
    p.proxy.style.transform = `translate3d(${x}px,${y}px,0) translate(-50%,-50%) scale(1.08)`;
  }

  onPointerUp(e) {
    const p = this.pointer;
    if (!p || e.pointerId !== p.id) return;
    if (!p.dragging) {
      const index = p.from;
      const moved = p.moved;
      this.pointer = null;
      if (moved) return;
      this.tapSlot(index);
      return;
    }
    e.preventDefault();
    const to = p.target;
    if (Number.isInteger(to) && to !== p.from) {
      this.removeDragVisuals();
      const from = p.from;
      this.pointer = null;
      this.commit(from, to);
    } else {
      this.snapBack();
    }
  }

  onPointerCancel(e) {
    if (!this.pointer || e.pointerId !== this.pointer.id) return;
    this.snapBack();
  }

  removeDragVisuals() {
    const p = this.pointer;
    if (!p) return;
    if (p.proxy) p.proxy.remove();
    if (p.button) p.button.classList.remove("drag-source");
    this.root.querySelectorAll(".fb-slot").forEach((el) =>
      el.classList.remove("drop-target", "swap-target"));
  }

  snapBack() {
    const p = this.pointer;
    if (!p) return;
    const finish = () => {
      this.removeDragVisuals();
      this.pointer = null;
    };
    if (!p.proxy || !p.button) return finish();
    const rect = p.button.getBoundingClientRect();
    const anim = p.proxy.animate([
      { transform: p.proxy.style.transform },
      { transform: `translate3d(${rect.left + rect.width / 2}px,${rect.top + rect.height / 2}px,0) translate(-50%,-50%) scale(1)` },
    ], { duration: 180, easing: "ease-out" });
    anim.onfinish = finish;
    anim.oncancel = finish;
  }

  cancelPointer(remove) {
    if (!this.pointer) return;
    if (remove !== false) this.removeDragVisuals();
    else if (this.pointer.proxy) this.pointer.proxy.remove();
    this.pointer = null;
  }

  tapSlot(index) {
    const slots = normalizeSlots(this.props.slots);
    // 触れた駒を親へ知らせる（§7.119）。タッチではボタンに focus が来ない
    // 端末があり（iOS）、focus 頼みだと駒の詳細が出ない。選択や入れ替えの
    // 挙動はそのまま — 詳細表示は載せるだけで、何も奪わない。
    if (slots[index] && this.props.onPieceTap) this.props.onPieceTap(slots[index]);
    if (this.selectedIndex === null) {
      if (!slots[index]) return;
      this.selectedIndex = index;
      this.keyboardTargetIndex = index;
      this.render();
      return;
    }
    if (this.selectedIndex === index) return this.clearSelection();
    this.commit(this.selectedIndex, index);
  }

  clearSelection() {
    this.selectedIndex = null;
    this.keyboardTargetIndex = null;
    this.render();
  }

  keyboardNext(index, key) {
    const form = this.layout();
    const meta = this.slotMeta(index);
    if (key === "ArrowLeft" || key === "ArrowRight") {
      const delta = key === "ArrowLeft" ? -1 : 1;
      const k = Math.max(0, Math.min(meta.count - 1, meta.rowIndex + delta));
      return (meta.front ? 0 : form.front) + k;
    }
    const otherCount = meta.front ? form.rear : form.front;
    const sourceX = (meta.rowIndex + 0.5) / meta.count;
    let nearest = 0, gap = Infinity;
    for (let k = 0; k < otherCount; k++) {
      const d = Math.abs((k + 0.5) / otherCount - sourceX);
      if (d < gap) { gap = d; nearest = k; }
    }
    return meta.front ? form.front + nearest : nearest;
  }

  onKeyDown(e) {
    const index = +e.currentTarget.dataset.slotIndex;
    if (e.key === "Escape") { e.preventDefault(); this.clearSelection(); return; }
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault(); this.removeSlot(index); return;
    }
    if (["Enter", " "].includes(e.key)) {
      e.preventDefault();
      if (this.selectedIndex === null) {
        if (!normalizeSlots(this.props.slots)[index]) return;
        this.selectedIndex = index;
        this.keyboardTargetIndex = index;
        this.render();
        this.focusSlot(index);
      } else {
        const target = this.keyboardTargetIndex ?? index;
        if (target === this.selectedIndex) this.clearSelection();
        else this.commit(this.selectedIndex, target);
      }
      return;
    }
    if (!e.key.startsWith("Arrow") || this.selectedIndex === null) return;
    e.preventDefault();
    const current = this.keyboardTargetIndex ?? this.selectedIndex;
    this.keyboardTargetIndex = this.keyboardNext(current, e.key);
    this.render();
    this.focusSlot(this.keyboardTargetIndex);
  }

  focusSlot(index) {
    const el = this.root.querySelector(`.fb-piece[data-slot-index="${index}"]`);
    if (el) el.focus({ preventScroll: true });
  }

  commit(from, to) {
    const before = normalizeSlots(this.props.slots);
    const a = before[from], b = before[to];
    if (!a || from === to) return this.clearSelection();
    const oldRects = new Map();
    this.root.querySelectorAll(".fb-piece.occupied").forEach((el) => {
      const id = before[+el.dataset.slotIndex];
      if (id) oldRects.set(id, el.getBoundingClientRect());
    });
    const next = swapOrMove(before, from, to);
    const an = this.props.units[a] ? this.props.units[a].name : a;
    const bn = b && this.props.units[b] ? this.props.units[b].name : b;
    this.announcement = b ? `${an}と${bn}を入れ替えました` : `${an}を空き枠へ移動しました`;
    this.selectedIndex = null;
    this.keyboardTargetIndex = null;
    this.props.onSlotsChange(next);
    requestAnimationFrame(() => {
      this.root.querySelectorAll(".fb-piece.occupied").forEach((el) => {
        const id = normalizeSlots(this.props.slots)[+el.dataset.slotIndex];
        const old = oldRects.get(id);
        if (!old) return;
        const now = el.getBoundingClientRect();
        const dx = old.left - now.left, dy = old.top - now.top;
        if (Math.abs(dx) + Math.abs(dy) < 1) return;
        el.animate([{ transform: `translate(${dx}px,${dy}px)` }, { transform: "translate(0,0)" }],
                   { duration: 180, easing: "ease-out" });
      });
    });
  }
}

const FORMATION_BOARDS = new WeakMap();
function mountFormationBoard(root, props) {
  let board = FORMATION_BOARDS.get(root);
  if (!board) {
    board = new FormationBoard(root, props);
    FORMATION_BOARDS.set(root, board);
  } else {
    board.setProps(props);
  }
  return board;
}

async function api(path, body) {
  const opt = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

/* ── 題字（朱印の落款＋金の筆文字） ─────────────────── */
function logoHTML(big) {
  return `<span class="logo ${big ? "logo-big" : ""}">
    <span class="logo-seal" aria-hidden="true"><span>陣</span></span>
    <span class="logo-text">三国布陣</span>
  </span>`;
}

/* 参陣の画面の題字絵（キービジュアル）。**絵の中に題字が入っている**ので、
   出せたときは文字の題字を出さない（二重になる）。読み込めなければ onerror で
   絵を畳み、従来の文字の題字へ落ちる — 絵は差し替え式で、無くても読めなく
   ならないという顔絵と同じ約束（§7.59）。卓上は横長・携帯は縦長を出し分ける。 */
function heroHTML() {
  return `
    <div class="login-hero fade-in">
      <picture class="hero-art">
        <source media="(max-width: 620px)" srcset="/art/keyvisual-tall.webp">
        <img src="/art/keyvisual-wide.webp" alt="三国布陣"
             onerror="this.closest('.hero-art').remove();
                      document.querySelector('.hero-fallback').hidden = false;">
      </picture>
      <div class="hero-fallback" hidden>${logoHTML(true)}</div>
      <div class="logo-sub">六将軍略オートバトル</div>
      <div class="logo-tag">知略を布き、乱世を制せ</div>
    </div>`;
}

/* ── 共通シェル ─────────────────────── */
function shell(state) {
  const view = document.body.dataset.view;
  const nav = [["/", "対戦"], ["/senki", "戦記"], ["/deck", "編成"],
               ["/council", "軍議演習"], ["/replays", "戦歴"]]
    .map(([p, t]) => `<a href="${p}" class="${location.pathname === p ? "on" : ""}">${t}</a>`)
    .join("");
  const chip = state.me
    ? `<span class="player-chip">遊んでいるのは <b>${esc(state.me.name)}</b>
       <a href="#" id="switch">替える</a></span>`
    : "";
  $("#app").insertAdjacentHTML("beforebegin", `
    <header>
      <h1>${logoHTML(false)}</h1>
      <nav>${nav}</nav>${chip}
    </header>`);
  const sw = $("#switch");
  if (sw) sw.onclick = (e) => {
    e.preventDefault();
    if (state.auth && state.auth.mode === "oidc") location.href = "/auth/logout";
    else renderLogin(state, true);
  };
}

/* ── ログイン ─────────────────────── */
function renderLogin(state, force) {
  const app = $("#app");
  if (state.auth && state.auth.mode === "oidc") {
    // 公開モード（§7.118）: 外部ログインだけ。名乗りやpid選択の口は出さない。
    app.innerHTML = `
      ${heroHTML()}
      <div class="login-panel panel fade-in">
        <h2>参陣せよ、主公</h2>
        <div class="login-row">
          <a class="btn primary" href="/auth/login">Google で参陣</a>
        </div>
        <p class="muted">合言葉（パスワード）は預からない。Google の扉から入る。</p>
      </div>`;
    return;
  }
  const opts = state.humans.map((h) =>
    `<option value="${h.id}">${esc(h.name)}</option>`).join("");
  app.innerHTML = `
    ${heroHTML()}
    <div class="login-panel panel fade-in">
      <h2>名乗りを上げよ、主公</h2>
      ${state.humans.length ? `
        <div class="login-row">
          <select id="pick">${opts}</select>
          <button class="primary" id="go">この名で参陣</button>
        </div>
        <p class="muted">または</p>` : ""}
      <div class="login-row">
        <input id="newname" placeholder="新しい武号（表示名）">
        <button id="make">新しく登録</button>
      </div>
      <p class="muted">手元専用。メール等は要らない。</p>
    </div>`;
  if ($("#go")) $("#go").onclick = async () => {
    await api("/api/login", { pid: $("#pick").value }); location.href = location.pathname;
  };
  $("#make").onclick = async () => {
    const name = $("#newname").value.trim();
    if (!name) return;
    await api("/api/login", { new: name }); location.href = location.pathname;
  };
}

/* ── 順位表 ──────────────────────── */
function fmtClock(epoch) {
  const d = new Date(epoch * 1000);
  return `${d.getHours()}時${String(d.getMinutes()).padStart(2, "0")}分`;
}

/* 軍略の手引き（§7.121）。編成画面とホームの導入の両方から開くので、
   モーダルは #app ではなく body に1つだけ置く（画面を移っても作り直さない）。 */
const GUIDE_HTML = `
    <div class="guide-overlay" id="guide" hidden>
      <div class="guide-panel panel">
        <div class="guide-head"><h2>軍略の手引き</h2>
          <button class="mini ghost" id="guide-close">閉じる ✕</button></div>
        <dl class="guide-body">
          <dt>兵種の三すくみ</dt>
          <dd>歩は騎を受け止め、騎は弓を蹴散らし、弓は歩を射抜く。</dd>
          <dt>陣形は弓の数を決める</dt>
          <dd>鶴翼＝前4・弓2（近接が主役）／魚鱗＝前3・弓3（半々）／
            雁行＝前2・弓4（射撃が主役）。矢を増やすほど、壁は薄くなる。</dd>
          <dt>弓の弱点</dt>
          <dd>肉薄されると矢も兵法も鈍る。射手を守る壁を惜しむな。</dd>
          <dt>槍の使い道</dt>
          <dd>槍持ちの歩兵は後衛にも置ける。回り込む騎馬は、槍が突き止める。</dd>
          <dt>前衛の積み方</dt>
          <dd>高く積め——最も安い札が矢面に立ち、高い札が長く戦う。
            ただし一枚の柱に頼る軍は、柱を失えば崩れる。</dd>
          <dt>同じ兵法は重ならない</dt>
          <dd><b>同じ名の兵法の効果は積み上がらない</b>——強いほう一つだけが効く。
            同じ札を二度撃っても、同じ兵法を持つ二人が並んでも同じこと。
            <b>違う名の兵法どうしなら足し合わさる</b>。ただし一つの能力への合計は
            上下とも五割で頭打ちになる。守りを固めるなら、同じ兵法を重ねるより
            <b>別々の兵法を並べよ</b>。</dd>
          <dt>細かい得</dt>
          <dd>余った点は開戦の気勢に変わる。本陣を預かる将が崩れれば全軍が
            動揺する。兵法は消費が軽いほど数を撃ち、重いほど一撃に懸ける。</dd>
        </dl>
        <h3 class="guide-sub">用語</h3>
        <p class="guide-note">札の効果文と軍功帳・合戦詳録に出る語。
          <b>数字はすべて実際の戦の量</b>で、内部の係数ではない。</p>
        <dl class="guide-body terms">
          <dt>損害</dt>
          <dd>その一撃で減らす兵の数。<b>敵の守りで目減りする</b>ので、
            書かれた数がそのまま入るわけではない。</dd>
          <dt>延焼</dt>
          <dd>火や毒のように、しばらく毎分削り続ける損害。一度に来ない代わりに
            合計は大きい。</dd>
          <dt>回復</dt>
          <dd>減った兵を呼び戻す。<b>初めの兵力より上には戻らない。</b></dd>
          <dt>混乱</dt>
          <dd>隊列が乱れる。<b>成功率でも兵の割合でもなく、乱れの濃さ</b>。
            混乱20% なら相手の出力が 17% 落ち、与える損害の 8% が味方へ向く。
            効き目は<b>掛ける側と受ける側の知略の比</b>で伸び縮みする——
            賢い将ほど惑わされにくい。</dd>
          <dt>同士討ち</dt>
          <dd>混乱した隊が、味方へ向けてしまった損害。軍功帳では出た時だけ出る。</dd>
          <dt>足止め</dt>
          <dd><b>攻撃も前進も止まる。</b>混乱と違って割合ではなく、
            その時間まるごと何もできない。</dd>
          <dt>攻撃力・防御力・移動速度・気勢</dt>
          <dd>一定時間の増減。<b>同じ名の効果は重ならず、強いほう一つだけ</b>が効く。
            違う名どうしなら足し合わさるが、一つの能力への合計は上下とも
            五割で頭打ち。</dd>
          <dt>兵法防御・通常攻撃防御</dt>
          <dd>受ける損害そのものを減らす。減らした分は軍功帳に<b>軽減</b>として出る。</dd>
          <dt>兵法反射</dt>
          <dd>受けた兵法の一部を撃ち手へ返す。返した分は<b>反射</b>として出る。</dd>
          <dt>打消し</dt>
          <dd>構えた隊を狙う敵の兵法を<b>丸ごと無効</b>にする。軽減と違い、
            当たらなかったことになる。</dd>
          <dt>代償</dt>
          <dd>放つたびに<b>自分の隊の残り兵</b>を割合で失う。強い兵法ほど重い。</dd>
          <dt>ゲージ付与</dt>
          <dd>味方の兵法のたまり具合を進める。早く二撃目を出させる。</dd>
          <dt>気勢</dt>
          <dd>兵法のたまる速さ。編成で余った点はここに変わる。</dd>
          <dt>抑制</dt>
          <dd>弓兵は<b>肉薄されると矢も兵法も鈍る</b>。射手を守る壁が要る理由。</dd>
          <dt>迂回</dt>
          <dd>騎兵が敵陣の外を回り、前衛を素通りして後衛を襲う。
            <b>敵の削りの出どころが後ろに偏っているときほど回り込む</b>。
            道のりが伸びるので取り付くのは遅い。</dd>
          <dt>突撃</dt>
          <dd>騎兵は<b>取り付いた直後が最も強く</b>、乱戦が続くと落ちる。
            歩兵は増減しないので、長い揉み合いでは相対的に強い。</dd>
          <dt>後衛の槍</dt>
          <dd>槍持ちの歩兵は後衛にも置ける。<b>その場を動かず</b>前線越しに突く
            （威力は落ちる）代わりに、<b>回り込んできた騎馬を迎え撃つ</b>。
            前衛に置けば普通の歩兵。</dd>
          <dt>前衛突破・接敵抑制</dt>
          <dd>合戦詳録の語。前衛を抜かれたか、敵を前線に釘付けにできたか。</dd>
        </dl>
      </div>
    </div>`;

function ensureGuide() {
  let gd = $("#guide");
  if (!gd) {
    document.body.insertAdjacentHTML("beforeend", GUIDE_HTML);
    gd = $("#guide");
    $("#guide-close").onclick = () => { gd.hidden = true; };
    gd.onclick = (e) => { if (e.target === gd) gd.hidden = true; };
  }
  return gd;
}

function renderOnboard() {
  // 初回の導入（§7.121）。**この1枚だけを出す** — 順位表や兵符を同時に
  // 見せると、初手が「対戦」に見えてしまう。このゲームの入口は戦記で、
  // 本質は「負けながら自分の布陣を作る」こと。文言はテストプレイの指定。
  $("#app").innerHTML = `
    <div class="onboard panel fade-in">
      <h2>まずは戦記の初戦へ</h2>
      <ol class="onboard-steps">
        <li>軍師の草案をたたき台に、6人を布陣します</li>
        <li>札は1回押すと詳細、素早く2回押すと配置・解除できます</li>
        <li>負けたらリプレイの「軍師の見立て」と「軍功帳」を確認し、
          武将や陣形を変えて再挑戦してください</li>
      </ol>
      <p class="onboard-creed">草案は正解ではありません。<b>負けながら自分の布陣を作るゲーム</b>です。</p>
      <div class="onboard-actions">
        <button class="primary" id="ob-go">初陣へ</button>
        <button class="ghost" id="ob-guide">軍略の手引きを読む</button>
      </div>
    </div>`;
  $("#ob-go").onclick = async () => {
    try { await api("/api/seen", { key: "onboard" }); } catch (_e) { /* 進む */ }
    location.href = "/senki?i=0";
  };
  // 手引きは重ねて開くだけ。導入は「初陣へ」を押すまで残る（読む→戻る→出発）
  $("#ob-guide").onclick = () => { ensureGuide().hidden = false; };
}

async function viewHome(state, options = {}) {
  if (state.onboard) return renderOnboard();
  // 現在の武名（§7.86）。順位表は毎時の断面なので、自分の値は即時を出す。
  // **この画面（viewHome）の中で作る** — shell() に置いていたせいで別関数の
  // 変数を参照し、対戦画面が丸ごと落ちて「読み込み中」のまま止まっていた。
  const mr = state.my_rating || {};
  const myRating = state.me && Object.keys(mr).length
    ? `<div class="my-rating num">現在の武名　${Object.entries(mr)
        .map(([bn, r]) => `<span>${esc(bn)} <b>${Math.round(r.rating)}</b>` +
          `<small> ${(r.w || r.l) ? `${r.w}勝${r.l}敗` : `${r.games}戦`}</small></span>`)
        .join("")}
       <small class="muted">（順位表は毎時更新）</small></div>`
    : "";
  const app = $("#app");
  const ok = state.boards_ok || {};
  const boards = state.boards.map((b) => {
    const rows = b.table.slice(0, 10).map((r) => `
      <tr class="${r.me ? "me" : ""} ${r.rank === 1 ? "top1" : ""}">
        <td class="rank num">${r.rank === 1 ? "①" : r.rank + "位"}</td>
        <td>${esc(r.name)}${r.kind === "dummy" ? '<span class="dummy">在野</span>' : ""}</td>
        <td class="rating num">武名${Math.round(r.rating)}</td>
        <td class="games num">${r.games}戦</td>
      </tr>`).join("");
    const isBo1 = b.name !== "天下";
    const btn = isBo1
      ? `<button class="mini attack" data-reg="${b.name}"
           ${state.me && ok[b.name] ? "" : "disabled"}>出陣（兵符1）</button>`
      : "";
    return `<div class="panel">
      <h2>${esc(b.name)}${btn}</h2>
      <table class="std">${rows || "<tr><td class='muted'>まだ戦いがない</td></tr>"}</table>
    </div>`;
  }).join("");
  const h = state.heifu;
  const heifuGauge = h ? `
    <span class="heifu" title="兵符: 出陣1回で1枚。10分に1枚回復・上限${h.cap}">
      ${"❙".repeat(h.count)}<span class="empty">${"❙".repeat(h.cap - h.count)}</span>
      <b>${h.count}</b>/${h.cap}
      ${h.next_in ? `<small>次の1枚まで ${Math.ceil(h.next_in / 60)}分</small>` : ""}
      <button class="mini ghost dev-only" id="refill" title="試験用">＋補充</button>
    </span>` : "";
  const t = state.tenka || {};
  let tr = t.truce;
  const rep = t.report || { n: 0, w: 0, l: 0, d: 0 };
  const tenkaState = !state.me ? "" : (!t.eligible
    ? '<span class="warn">3デッキ揃えると自動参加</span>'
    : (t.resting
      ? `<span class="truce-state">休戦中</span>${t.next_active_at
          ? ` 次の参戦 ${fmtClock(t.next_active_at)}` : ""}`
      : '<span class="ok">自動参加</span>'));
  const tenka = `
    <div class="tenka-chip fade-in">
      <div class="tenka-line"><b>天下</b>（三戦一括のBO3・毎時00分）
        次回 ${fmtClock(t.at)}（${Math.max(1, Math.ceil(t.in_sec / 60))}分後）
        ${tenkaState}
        ${state.me ? '<button class="mini ghost dev-only" id="tenka-now" title="試験用">今すぐ開催</button>' : ""}
      </div>
      ${state.me ? `<div class="tenka-report">
        本日 ${rep.n}戦　<b>${rep.w}勝 ${rep.l}敗${rep.d ? ` ${rep.d}分` : ""}</b>
        ${rep.n ? '<a href="/replays">戦歴を見る</a>' : ""}
      </div>` : ""}
      ${tr ? `<details class="truce-box" id="truce-box">
        <summary><b>休戦令</b>　毎日8枚・開催2時間前に締切
          <span id="truce-summary">${esc(truceSummaryText(tr))}</span></summary>
        <p class="muted">休戦にした開催は組合せ対象外。武名・報酬・戦歴は動かない。8時間は分けて選べる。</p>
        <div id="truce-editor"></div>
      </details>` : ""}
    </div>`;
  const dummies = (state.dummies || []).map((d) =>
    `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("");
  const regs = ["汜水関", "官渡", "赤壁"].map((n) =>
    `<option>${n}</option>`).join("");
  const free = state.me ? `
    <div class="panel free-panel fade-in">
      <h2>フリー対戦<span class="sub">レートも兵符も動かない</span></h2>
      <div class="free-row">在野と:
        <select id="free-reg">${regs}</select>
        <select id="free-foe">${dummies}</select>
        <button class="mini" id="free-go">戦う</button>
      </div>
      <div class="free-row">友と（ルーム）:
        <select id="room-reg">${regs}</select>
        <button class="mini" id="room-make">番号を発行</button>
        <span id="room-code" class="num"></span>
        ／ <input id="room-in" placeholder="番号を入力" size="8">
        <button class="mini" id="room-join">入る</button>
      </div>
    </div>` : "";
  app.innerHTML = `
    <div class="cta">
      <span class="hint">${!state.me ? "" : (!state.entry_ok
        ? 'デッキを1つ登録すればその戦場に出陣できる → <a href="/deck">編成へ</a>'
        : "出陣すると同格の相手が選ばれる（相手は事前に分からない・同じ相手は1時間に1回まで）")}</span>
      ${heifuGauge}
      <span class="muted num">季節 ${esc(state.season || "")}・順位は毎時更新</span>
      ${state.me ? `<button class="mini ghost dev-only" id="reset-record"
        title="試験用: 自分の武名と戦績を白紙に戻す（デッキ・登用・恩賞は残る）"
        >戦績リセット</button>` : ""}
    </div>
    ${myRating}
    ${tenka}
    ${state.senki && state.senki.next ? `<div class="senki-chip fade-in">
      <b>戦記</b> ${state.senki.cleared}／${state.senki.total}戦
      　次は「<b>${esc(state.senki.next)}</b>」
      <a href="/senki">進む →</a></div>` : ""}
    ${state.onsho && state.onsho.choices ? `<div class="onsho-banner fade-in">
      <div class="onsho-pick-head">本日の恩賞 — 三つのうち一つを選んで賜る（日替わり）</div>
      <div class="onsho-pick">${state.onsho.choices.map((c) => `
        <button class="onsho-choice" data-k="${esc(c.key)}">
          <span class="oc-tier">${esc(c.tier)}</span>
          <span class="oc-name">【${esc(c.name)}】</span>
          <span class="oc-kou">${c.kou ? c.kou + "功" : "功いらず"}</span>
          <span class="oc-desc">${esc(c.desc || "")}</span>
        </button>`).join("")}</div>
      <div class="muted" style="font-size:11.5px">選んだものだけが手に入る。残り二つは流れる。
        セットは<a href="/deck">編成画面の軍功枠</a>で。</div>
    </div>` : ""}
    <div class="boards fade-in">${boards}${(state.banzuke || []).length ? `
      <div class="panel">
        <h2>戦記番付<span class="sub">リセットなし</span></h2>
        <table class="std">${state.banzuke.map((r, k) => `
          <tr class="${r.me ? "me" : ""} ${k === 0 ? "top1" : ""}">
            <td class="rank num">${k === 0 ? "①" : (k + 1) + "位"}</td>
            <td>${esc(r.name)}</td>
            <td class="num">周回${r.lap}</td>
            <td class="num">残兵${(r.zanhei / 1000).toFixed(1)}千</td>
          </tr>`).join("")}
        </table>
      </div>` : ""}</div>
    ${free}`;
  const rr = $("#reset-record");
  if (rr) rr.onclick = async () => {
    if (!confirm("武名と戦績を白紙に戻す。デッキ・登用・恩賞は残る。よい？")) return;
    try { await api("/api/dev_reset_record", {}); location.reload(); }
    catch (e) { alert("戻せなかった: " + e.message); }
  };
  $$(".onsho-choice").forEach((b) => b.onclick = async () => {
    const r = await api("/api/onsho_pick", { key: b.dataset.k });
    if (r.ok) location.reload(); else alert((r.errors || ["受け取れなかった"])[0]);
  });
  $$("button.attack").forEach((b) => b.onclick = () => doAttack(b.dataset.reg));
  const rf = $("#refill");
  if (rf) rf.onclick = async () => { await api("/api/dev_heifu", {}); location.reload(); };
  const tn = $("#tenka-now");
  if (tn) tn.onclick = async () => {
    // 試験用の口は手元起動でだけ開く（DEV_DOORS）。閉まっている時に
    // 黙って死んでいたので、失敗は言葉で返す。
    try { await api("/api/dev_tenka", {}); location.reload(); }
    catch (e) {
      let msg = e.message;
      try { msg = JSON.parse(msg).error || msg; } catch (_x) { /* 素通し */ }
      alert("今すぐ開催できなかった: " + msg
            + "\n試験用の口は localhost 起動でだけ開く。スマホ向けに"
            + " SANGOKU_HOST を立てて起動している時は、あわせて"
            + " SANGOKU_DEV=1 を付けると開く。");
    }
  };
  if (tr) {
    let scope = options.truceScope === "default" ? "default" : "day";
    // 今日がもう動かせない時間なら、最初から明日を開いて袋小路に見せない。
    const requestedDay = options.truceDay
      ? tr.days.findIndex((d) => d.day === options.truceDay) : -1;
    let dayIndex = requestedDay >= 0 ? requestedDay : initialTruceDayIndex(tr);
    let autoAdvanced = requestedDay < 0 && dayIndex === 1;
    // **表示中の日を正本にする**。以前は dayIndex=1 でも days[0] から取り、
    // 「明日」の見出しで今日の8枠を保存できてしまっていた。
    let picked = new Set(truceHoursFor(tr, dayIndex, scope));
    let notice = options.truceNotice || null;
    const parseApiError = (e) => {
      try { return JSON.parse(e.message).error || e.message; }
      catch (_x) { return e.message; }
    };
    const updateSummary = () => {
      const el = $("#truce-summary");
      if (el) el.textContent = truceSummaryText(tr);
    };
    const resetPicked = () => {
      picked = new Set(truceHoursFor(tr, dayIndex, scope));
    };
    const finishSave = async (saved, message) => {
      // 上の「次の参戦」も古くしないためstateを取り直し、ページ遷移なしで描き直す。
      try {
        const fresh = await api("/api/state");
        await viewHome(fresh, { truceOpen: true,
          truceNotice: { kind: "ok", text: message },
          truceScope: scope, truceDay: tr.days[dayIndex].day });
      } catch (_refreshError) {
        // 保存API自体は成功済み。再取得だけ失敗しても失敗扱いの文言にはしない。
        tr = saved.truce || tr;
        dayIndex = Math.min(dayIndex, Math.max(0, tr.days.length - 1));
        resetPicked();
        notice = { kind: "ok", text: message };
        updateSummary();
        renderTruce();
        const box = $("#truce-box");
        if (box) box.open = true;
      }
    };
    const renderTruce = () => {
      const ed = $("#truce-editor");
      if (!ed) return;
      const day = tr.days[dayIndex];
      const locked = new Set(scope === "day" ? day.locked : []);
      const past = new Set(scope === "day" ? (day.past || []) : []);
      const deadline = new Set(scope === "day" ? (day.deadline || []) : []);
      const hours = Array.from({ length: 24 }, (_, h) => {
        const on = picked.has(h);
        const lockKind = past.has(h) ? "済"
          : (deadline.has(h) || locked.has(h) ? "締切" : "");
        const label = lockKind
          ? `${h}時の天下、${on ? "休戦" : "参戦"}、${lockKind === "済" ? "開催済み" : "変更締切済み"}`
          : `${h}時の天下を${on ? "休戦" : "参戦"}にする`;
        return `
          <button type="button" class="truce-hour ${on ? "on" : ""} ${lockKind === "済" ? "past" : (lockKind ? "deadline" : "")}"
            data-hour="${h}" aria-pressed="${on ? "true" : "false"}"
            aria-label="${esc(label)}" ${locked.has(h) ? "disabled aria-disabled=\"true\"" : ""}
            title="${esc(label)}"><span class="truce-check" aria-hidden="true">${on ? "✓" : ""}</span>
            <span class="truce-hour-num">${h}</span>${lockKind
              ? `<small aria-hidden="true">${lockKind}</small>` : ""}</button>`;
      }).join("");
      ed.innerHTML = `
        <div class="truce-tabs">
          <button class="mini ${scope === "default" ? "primary" : "ghost"}" data-scope="default">通常設定</button>
          <button class="mini ${scope === "day" ? "primary" : "ghost"}" data-scope="day">日別変更</button>
          ${scope === "day" ? `<select id="truce-day">${tr.days.map((d, i) =>
            `<option value="${i}" ${i === dayIndex ? "selected" : ""}>${esc(formatTruceDayLabel(d.day, i))}${d.source === "day" ? "・個別変更" : ""}</option>`).join("")}</select>` : ""}
        </div>
        ${scope === "day" && autoAdvanced && dayIndex === 1
          ? '<p class="truce-auto-note">本日分は締切済みのため、明日分を表示しています</p>' : ""}
        <div class="truce-hours">${hours}</div>
        <div class="truce-actions"><span class="truce-count ${picked.size === 8 ? "ok" : "warn"}">休戦令 ${picked.size}/8</span>
          <button class="mini" id="truce-save" ${picked.size === 8 ? "" : "disabled"}>この設定で布告</button>
          ${scope === "day" && day.source === "day" ? '<button class="mini ghost" id="truce-reset">通常設定へ戻す</button>' : ""}
          <small class="muted">${scope === "default" ? "通常設定は締切済みの日を避けて反映"
            : "済＝開催済み　締切＝開催2時間前を過ぎて変更不可"}</small>
        </div>
        ${notice ? `<div class="truce-notice ${notice.kind === "error" ? "error" : "ok"}" role="status">${esc(notice.text)}</div>` : ""}`;
      $$("[data-scope]", ed).forEach((b) => b.onclick = () => {
        scope = b.dataset.scope; notice = null; resetPicked(); renderTruce();
      });
      const sel = $("#truce-day", ed);
      if (sel) sel.onchange = () => {
        dayIndex = +sel.value; autoAdvanced = false; notice = null;
        resetPicked(); renderTruce();
      };
      $$(".truce-hour", ed).forEach((b) => b.onclick = () => {
        const h2 = +b.dataset.hour;
        if (picked.has(h2)) picked.delete(h2); else picked.add(h2);
        notice = null;
        renderTruce();
      });
      $("#truce-save", ed).onclick = async () => {
        try {
          const saved = await api("/api/truce", scope === "default"
            ? { action: "default", hours: [...picked] }
            : { action: "day", day: day.day, hours: [...picked] });
          await finishSave(saved, "休戦令を布告しました");
        } catch (e) {
          notice = { kind: "error",
            text: "休戦令を改められませんでした: " + parseApiError(e) };
          renderTruce();
        }
      };
      const rb = $("#truce-reset", ed);
      if (rb) rb.onclick = async () => {
        try {
          const saved = await api("/api/truce", { action: "reset_day", day: day.day });
          await finishSave(saved, "通常設定へ戻しました");
        } catch (e) {
          notice = { kind: "error",
            text: "通常設定へ戻せませんでした: " + parseApiError(e) };
          renderTruce();
        }
      };
    };
    renderTruce();
    if (options.truceOpen) $("#truce-box").open = true;
  }
  const fg = $("#free-go");
  if (fg) fg.onclick = async () => {
    try {
      const r = await api("/api/free", { reg: $("#free-reg").value,
                                         foe: $("#free-foe").value });
      showBattleResult("フリー", r);
    } catch (e) { alert(e.message); }
  };
  const rm = $("#room-make");
  if (rm) rm.onclick = async () => {
    try {
      const r = await api("/api/room", { action: "create",
                                         reg: $("#room-reg").value });
      $("#room-code").innerHTML = `番号 <b>${esc(r.code)}</b>（相手に伝える）`;
    } catch (e) { alert(e.message); }
  };
  const rj = $("#room-join");
  if (rj) rj.onclick = async () => {
    try {
      const r = await api("/api/room", { action: "join",
                                         code: $("#room-in").value });
      showBattleResult("ルーム", r);
    } catch (e) { alert(e.message); }
  };
}

async function doAttack(reg) {
  document.body.insertAdjacentHTML("beforeend", `
    <div id="overlay"><div class="box">
      <div class="march">出　陣</div>
      <p class="muted">${esc(reg)} — 相手を探しております……</p>
    </div></div>`);
  try {
    const r = await api("/api/attack", { reg });
    if (r.battle_id) {
      // **結果より先に戦いを見せる**（§7.62 の判の流儀をラダーにも）。
      // 判はリプレイを見届けた後に出す。
      sessionStorage.setItem("fight:" + r.battle_id, JSON.stringify(
        { label: reg, result: r, kind: "ladder" }));
      location.href = "/replay?id=" + r.battle_id + "&from=fight";
      return;
    }
    showBattleResult(reg, r);
  } catch (e) {
    const ov = $("#overlay"); if (ov) ov.remove();
    alert(e.message);
  }
}

function showBattleResult(label, r, opts) {
  opts = opts || {};
  if (!$("#overlay")) {
    document.body.insertAdjacentHTML("beforeend",
      '<div id="overlay"><div class="box"></div></div>');
  }
  const cls = r.win === "勝ち" ? "win" : (r.win === "負け" ? "lose" : "draw");
  const stamp = r.win === "勝ち" ? "勝" : (r.win === "負け" ? "敗" : "分");
  const delta = (r.rating_new !== undefined)
    ? Math.round(r.rating_new - r.rating_old) : null;
  const isReg = ["汜水関", "官渡", "赤壁"].includes(label);
  const recruits = (r.recruits || []).map((g) => `
    <div class="recruit-item">
      <img src="/portrait/${encodeURIComponent(g.person)}" alt="">
      <div><span class="rec-label">登用</span> <b>${esc(g.name)}</b>が軍門に降った！
        ${g.quote ? `<div class="rec-quote">「${esc(g.quote)}」</div>` : ""}</div>
    </div>`).join("");
  $("#overlay").innerHTML = `<div class="box result-box fade-in">
    <div class="stamp ${cls}">${stamp}</div>
    <div class="result-sub">${esc(label)}${r.foe ? `　対 <b>${esc(r.foe)}</b>` : ""}</div>
    ${delta !== null ? `<div class="result-rate num">
      武名 ${Math.round(r.rating_new)}
      <span class="delta ${delta >= 0 ? "up" : "down"}">（${delta >= 0 ? "+" : ""}${delta}）</span>
    </div>` : ""}
    ${recruits ? `<div class="recruit-list">${recruits}</div>` : ""}
    ${r.gained ? `<div class="lap-gain num">残兵 <b>${(r.gained / 1000).toFixed(1)}千</b> を積んだ
      （周回${r.lap}・${r.lap_done ? 8 : r.stage}人抜き・計${((r.lap_done ? r.lap_done.zanhei : r.zanhei) / 1000).toFixed(1)}千）</div>` : ""}
    ${r.lap_done ? `<div class="lap-done">周回${r.lap_done.lap}を完走！
      総残兵 <b class="num">${(r.lap_done.zanhei / 1000).toFixed(1)}千</b> を番付に刻んだ。
      周回を重ねると敵の家来が強い実物へ入れ替わっていく。</div>` : ""}
    <div class="result-actions">
      ${opts.next ? `<button class="primary" id="nextstage">次の戦へ ▶</button>` : ""}
      ${(r.battle_id && !opts.hideReplay)
        ? `<a class="btn ${opts.next ? "" : "primary"}" href="/replay?id=${r.battle_id}">戦いを観る</a>` : ""}
      ${isReg ? `<button id="again">もう一度出陣</button>` : ""}
      ${opts.retry ? `<button id="reprep">${r.win === "勝ち" ? "編成を見直す" : "編成を直して再挑戦"}</button>` : ""}
      ${opts.review ? `<button id="reviewlog">ログを読み返す</button>` : ""}
      <button class="ghost" id="closeres">${opts.closeLabel || "閉じる"}</button>
    </div>
  </div>`;
  const ag = $("#again");
  if (ag) ag.onclick = () => { $("#overlay").remove(); doAttack(label); };
  const rp = $("#reprep");
  if (rp) rp.onclick = opts.retry;
  const nx = $("#nextstage");
  if (nx) nx.onclick = opts.next;
  const rv = $("#reviewlog");
  if (rv) rv.onclick = () => { $("#overlay").remove(); opts.review(); };
  $("#closeres").onclick = opts.close || (() => location.reload());
}

/* ── 戦記（討伐→登用・§7.60） ─────────────── */
const KANJI_NUM = "一二三四五六七八九十";

async function viewSenki(state) {
  const d = await api("/api/senki");
  const chap = d.chapters.map((c) => {
    const rows = c.battles.map((b) => {
      const st = b.state;
      const recruits = (b.recruits || []).map((g) => `
        <span class="rec-chip ${st === "cleared" ? "got" : ""}"
              title="${st === "cleared" ? "登用済み" : "勝てば登用"}">
          <img src="/portrait/${encodeURIComponent(g.person)}" alt="">${esc(g.person)}</span>`).join("");
      return `<div class="senki-row ${st}">
        <span class="s-ico">${st === "cleared" ? "✅" : (st === "next" ? "⚔️" : "🔒")}</span>
        <span class="s-no num">${c.ch}-${b.no}</span>
        <span class="s-title">${esc(b.title)}
          ${b.boss ? '<span class="s-boss">章ボス</span>' : ""}</span>
        <span class="s-board muted num">${esc(b.board)}</span>
        ${st !== "locked" && b.foe
          ? `<span class="s-foe muted">敵将 ${esc(b.foe)}</span>` : ""}
        <span class="s-recruits">${recruits}</span>
        ${st === "next"
          ? `<button class="primary s-go" data-i="${b.i}" data-t="${esc(b.title)}">挑む</button>`
          : (st === "cleared"
             ? `<button class="ghost mini s-go" data-i="${b.i}" data-t="${esc(b.title)}">再戦</button>`
             : "")}
      </div>
      ${st === "next" && b.intro ? `<div class="senki-intro">${esc(b.intro)}</div>` : ""}`;
    }).join("");
    return `<section class="senki-ch">
      <h2 class="s-ch-head"><span class="s-ch-num">第${KANJI_NUM[c.ch - 1]}章</span>
        ${esc(c.name)}<small class="muted">　${esc(c.note)}（戦場: ${esc(c.board)}）</small></h2>
      ${rows}
    </section>`;
  }).join("");
  const done = d.cleared >= d.total;
  const lap = d.lap ? `
    <div class="panel lap-panel fade-in">
      <h2>戦記番付 <small class="muted">章ボス8人抜き・${d.lap.step_every}周ごとに敵の家来が強い実物へ入れ替わる・記録は消えない</small></h2>
      <div class="lap-line num">周回 <b class="lap-n">${d.lap.lap}</b>
        <span class="muted">（敵の陣容 +${d.lap.plus_pts}点${d.lap.mult_pct ? "・兵+" + d.lap.mult_pct + "%" : ""}）</span>
        　${d.lap.stage}人抜き　積み残兵 <b>${(d.lap.zanhei / 1000).toFixed(1)}千</b>
        ${d.lap.best ? `　<span class="muted">自己最高: 周回${d.lap.best.lap}・残兵${(d.lap.best.zanhei / 1000).toFixed(1)}千</span>` : ""}
      </div>
      <div class="lap-bosses">${d.lap.bosses.map((b, k) => `
        <span class="lap-boss ${b.beaten ? "beaten" : (k === d.lap.stage ? "now" : "")}"
              title="戦場: ${esc(b.board)}">${b.beaten ? "✓" : ""}${esc(b.title)}</span>`).join("")}
      </div>
      <button class="primary" id="lap-go">
        ${esc(d.lap.bosses[d.lap.stage].title)} に挑む（周回${d.lap.lap}）</button>
      <span class="muted">　負けても積んだ残兵は消えない。デッキは番付に載らない</span>
    </div>` : "";
  const bz = (d.banzuke && d.banzuke.length) ? `
    <div class="panel fade-in">
      <h2>番付</h2>
      <table class="std"><tr><th></th><th>武名</th><th>周回</th><th>総残兵</th><th>版</th><th></th></tr>
      ${d.banzuke.map((r, k) => `
        <tr class="${r.me ? "me" : ""}">
          <td class="rank num">${k === 0 ? "①" : (k + 1) + "位"}</td>
          <td>${esc(r.name)}</td>
          <td class="num">周回${r.lap}</td>
          <td class="num">${(r.zanhei / 1000).toFixed(1)}千</td>
          <td class="num muted">${esc(r.version).slice(0, 6)}</td>
          <td class="num muted">${esc(r.at)}</td>
        </tr>`).join("")}
      </table>
    </div>` : "";
  $("#app").innerHTML = `
    <div class="senki-head fade-in">
      <h2>戦記 <small class="muted">倒した将を登用して、自軍を広げる</small></h2>
      <div class="senki-bar num"><i style="width:${d.cleared / d.total * 100}%"></i>
        <span>${d.cleared}／${d.total}戦</span></div>
    </div>
    ${lap}${bz}
    <div class="senki-list fade-in">${chap}</div>`;
  $$(".s-go").forEach((b) =>
    b.onclick = () => viewSenkiPrep(+b.dataset.i));
  const lg = $("#lap-go");
  if (lg) lg.onclick = () => doSenkiLap(d.lap);
}

async function doSenkiLap(lap) {
  const title = lap.bosses[lap.stage].title;
  document.body.insertAdjacentHTML("beforeend", `
    <div id="overlay"><div class="box">
      <div class="march">出　陣</div>
      <p class="muted">周回${lap.lap}・${esc(title)} — 布陣を整えております……</p>
    </div></div>`);
  try {
    const r = await api("/api/senki_lap", {});
    showBattleResult(r.title || title, r);
  } catch (e) {
    const ov = $("#overlay"); if (ov) ov.remove();
    let msg = e.message;
    try { msg = JSON.parse(e.message).error || msg; } catch (_x) { /* 素通し */ }
    alert(msg);
  }
}

async function doSenkiFight(i, title, deck) {
  document.body.insertAdjacentHTML("beforeend", `
    <div id="overlay"><div class="box">
      <div class="march">出　陣</div>
      <p class="muted">${esc(title)} — 布陣を整えております……</p>
    </div></div>`);
  try {
    const r = await api("/api/senki_fight",
                        Object.assign({ i }, deck || {}));
    // **結果より先に戦いを見せる**（§7.62）。判はリプレイを見届けた後に出す
    const nextI = (i + 1 < r.total && i + 1 <= r.cleared) ? i + 1 : null;
    sessionStorage.setItem("fight:" + r.battle_id, JSON.stringify(
      { label: title, result: r, i, next: nextI }));
    location.href = "/replay?id=" + r.battle_id + "&from=fight";
  } catch (e) {
    const ov = $("#overlay"); if (ov) ov.remove();
    let msg = e.message;
    try { msg = JSON.parse(e.message).error || msg; } catch (_x) { /* 素通し */ }
    if (PREP && $("#deck-msg")) drawPrep(msg); else alert(msg);
  }
}

/* ── 戦前の間（敵の顔ぶれを見てから編成する・§7.62） ───────── */
async function viewSenkiPrep(i) {
  const [p, d] = await Promise.all([api("/api/senki_prep?i=" + i),
                                    api("/api/deckdata")]);
  D = d;
  PREP = p;
  // 前回この戦へ持ち込んだ編成があればそれを初期値に（負けて挑み直すとき、
  // 直した編成が草案で上書きされるのを防ぐ・§7.62）
  const start = p.last || p.suggest;
  cur = { reg: p.board, form: start.form || p.enemy.form,
          slots: slotsFromCards(start.cards) };
  const foeSummary = armySummary(p.enemy.cards, p.enemy.form, p.enemy.cost, null, "foe");
  // 敵札の中身（兵力・攻勢・兵法・特性）。**盤面は配置しか語らない** —
  // 見立てが「重い1枚は壁で受けよ」と言っても、どれが重い1枚かはここでしか
  // 読めない（§7.62 の詰将棋の可読性）。盤面の下に畳んで置く。
  const foe = p.enemy.cards.map((c, k) => `
    <div class="foe-card f${c.faction}">
      <img src="${c.portraitUrl || `/portrait/${encodeURIComponent(c.person)}`}" alt="">
      <div class="fc-body">
        <div class="fc-head"><b>${esc(c.name)}</b>
          <span class="cost num">${c.cost}点</span></div>
        <div class="muted num">${(k >= p.enemy.front) ? "後衛" : "前衛"}・<span class="unit-type ${TYPE_CLS[c.typ]}">${icoTyp(c.typ, c.spear)}${esc(c.typ)}${c.spear ? "・槍" : ""}</span>・${esc(c.role)}
          ｜兵${(c.men / 1000).toFixed(1)}千　攻勢${c.atk_pm}</div>
        <div class="fc-skill">【${esc(c.skill)}】${(c.traits || []).length
          ? "　特性: " + c.traits.map((t) => esc(t.name)).join("・") : ""}</div>
      </div>
    </div>`).join("");
  const rewards = p.recruits.map((g) => `
    <span class="rec-chip"><img src="/portrait/${encodeURIComponent(g.person)}"
      alt="">${esc(g.name)}</span>`).join("");
  const sources = (p.last ? [`<option value="last">前回の編成</option>`] : [])
    .concat([`<option value="suggest">軍師の草案</option>`])
    .concat(p.registered ? [`<option value="reg">登録デッキ（${p.registered.cost}点）</option>`] : [])
    .concat(p.saved.map((s, k) => `<option value="s${k}">保存庫: ${esc(s.name)}（${s.cost}点）</option>`))
    .join("");
  $("#app").innerHTML = `
    <div class="prep-head fade-in">
      <button class="ghost mini" id="prep-back">← 戦記へ</button>
      <h2>${esc(p.chapter)}　${p.ch}-${p.no}「${esc(p.title)}」
        ${p.boss ? '<span class="s-boss">章ボス</span>' : ""}
        ${p.cleared ? '<span class="muted">（再戦・報酬なし）</span>' : ""}</h2>
    </div>
    <div class="senki-intro prep-intro fade-in">${esc(p.intro)}</div>
    ${p.hint ? `<div class="prep-hint fade-in"><b>軍師の見立て</b>　${esc(p.hint)}</div>` : ""}
    <div class="prep-grid fade-in">
      <div class="panel foe-panel side-panel">
        <h2 class="side-heading foe-heading">敵陣<span class="sub">相手の兵種と配置</span></h2>
        ${foeSummary}
        <section class="army-zone foe" aria-label="敵軍の盤面">
          <div class="army-zone-label"><b>敵軍</b><span>読み取り専用</span></div>
          <div id="foe-board"></div>
        </section>
        <details class="foe-detail" open>
          <summary>敵札の中身<span class="muted">　兵力・攻勢・兵法・特性</span></summary>
          ${foe}
        </details>
        ${p.enemy.taunt ? `<div class="ci-quote">「${esc(p.enemy.taunt)}」<span class="muted">— ${esc(p.enemy.lead)}</span></div>` : ""}
        ${rewards ? `<div class="prep-reward">勝てば登用 ${rewards}</div>` : ""}
      </div>
      <div class="panel mine-panel side-panel">
        <h2 class="side-heading mine-heading">自軍編成<span class="sub">上限 ${p.cap}点（敵より1点軽い）</span></h2>
        <div id="mine-summary"></div>
        <div class="prep-src">
          <select id="prep-src">${sources}</select>
          <button class="mini ghost" id="prep-again">草案を引き直す</button>
        </div>
        <div id="draft" class="draft"></div>
        <div class="form-tabs" id="formtabs"></div>
        <div class="cost-meter" id="meter"><div class="fill"></div><div class="label"></div></div>
        <section class="army-zone mine" aria-label="自軍の盤面">
          <div class="army-zone-label"><b>自軍</b><span>駒をタップして選ぶ（マウスは横へドラッグでも）</span></div>
          <div id="slots"></div>
        </section>
        <div id="placement-errors" class="placement-errors" aria-live="polite"></div>
        <div class="deck-actions">
          <button class="primary" id="prep-go">出　陣</button>
          <span id="deck-msg"></span>
        </div>
      </div>
    </div>
    <div class="roster-tools fade-in">
      <div class="filter-tabs" id="typetabs"></div>
      <div class="filter-tabs" id="bandtabs"></div>
      <div class="filter-tabs" id="factabs"></div>
      <select id="sortsel">
        <option value="cost-">コスト 高い順</option>
        <option value="cost+">コスト 低い順</option>
        <option value="men-">兵力 多い順</option>
        <option value="might-">武勇 高い順</option>
        <option value="wits-">知略 高い順</option>
        <option value="atk_pm-">攻勢 高い順</option>
        <option value="eff_men-">守勢 高い順</option>
      </select>
      <input id="search" placeholder="名で探す">
    </div>
    <div id="cardinfo" class="cardinfo muted">札に1回触れると詳細。素早く2回で布陣へ（編成中の札は素早く2回で外す）。</div>
    <div class="cards" id="roster" data-keep-selection></div>`;
  drawFormTabs(); drawTypeTabs();
  $("#search").oninput = drawRoster;
  $("#prep-back").onclick = () => { location.href = "/senki"; };
  $("#prep-again").onclick = async () => {
    const r = await api("/api/senki_prep?i=" + i + "&n=" + Date.now());
    PREP.suggest = r.suggest;
    cur.slots = slotsFromCards(r.suggest.cards);
    cur.form = r.suggest.form || cur.form;
    drawFormTabs(); drawPrep();
  };
  $("#prep-src").onchange = () => {
    const v = $("#prep-src").value;
    const src = v === "last" ? PREP.last
              : v === "suggest" ? PREP.suggest
              : v === "reg" ? PREP.registered
              : PREP.saved[+v.slice(1)];
    if (!src) return;
    cur.slots = slotsFromCards(src.cards);
    cur.form = src.form || cur.form;
    drawFormTabs(); drawPrep();
  };
  $("#prep-go").onclick = () =>
    doSenkiFight(i, PREP.title, { cards: occupiedSlotIds(), form: cur.form });
  mountFormationBoard($("#foe-board"), {
    formation: FORM_KEY[p.enemy.form] || "gyorin",
    slots: slotsFromCards(p.enemy.cards.map((c) => c.name)),
    units: unitsForBoard(p.enemy.cards),
    interactive: false,
    onSlotsChange: () => {},
  });
  drawPrep();
}

function drawPrep(msg) {
  for (const f of [drawRoster, drawSlots, drawMeter, drawArmySummary, drawDraft]) {
    try { f(); } catch (e) { console.error("drawPrep:", f.name, e); }
  }
  const over = deckCost() > PREP.cap + 1e-9;
  const n = occupiedSlotIds().length;
  const bad = placementErrors().length;
  const go = $("#prep-go");
  go.disabled = over || n !== 6 || bad > 0;
  // 予算を使い切っていて空き枠が埋まらない、という手詰まりは名指しで言う
  // （札が一斉に沈むだけだと「武将を制限されている」ように見えてしまう）
  const rest = PREP.cap - deckCost();
  const rist = D.roster.filter((c) => !occupiedSlotIds().includes(c.name))
    .reduce((m, c) => Math.min(m, c.cost), Infinity);
  const stuck = n < 6 && rest < rist;
  const el = $("#deck-msg");
  el.className = (over || bad || stuck) ? "err" : "";
  el.textContent = msg ? msg
    : over ? `上限 ${PREP.cap}点を ${(deckCost() - PREP.cap).toFixed(0)}点 超えている`
    : stuck ? `あと${6 - n}人だが、残り${rest}点では誰も足せない`
              + `（盤面の武将を選び、一覧から軽い武将へ交代する）`
    : (n !== 6 ? `あと${6 - n}人（6人で出陣）`
       : (bad ? "置けない兵がいる（⚠の武将を交換または交代する）"
          : "6人そろった。駒は2回タップで入れ替え。一覧の札は1回で詳細・素早く2回で配置と解除"));
}

/* ── 編成 ──────────────────────── */
const FORMS = { "鶴翼": 4, "魚鱗": 3, "雁行": 2 };
let D = null;          // /api/deckdata
let cur = null;        // {reg, form, slots:(name|null)[6]} — 盤面状態の唯一の正本
let PREP = null;       // 戦前の間（§7.62）。非nullの間は上限も規則もこちら

let STATE = null;

function armySummary(cards, form, cost, cap, side) {
  const counts = { "歩兵": 0, "騎兵": 0, "弓兵": 0, "槍": 0 };
  for (const c of cards || []) {
    if (counts[c.typ] !== undefined) counts[c.typ]++;
    if (c.spear) counts["槍"]++;
  }
  const nf = FORMS[form] || (cards || []).filter((c) => !c.rear).length;
  const costText = cap == null ? `${cost || 0}点` : `${cost || 0}／${cap}点`;
  return `<div class="army-summary ${side || ""}">
    <span class="side-pill ${side || ""}">${side === "foe" ? "敵軍" : "自軍"}</span>
    <span>${icoTyp("歩兵")}歩 ${counts["歩兵"]}</span>
    <span>${icoTyp("騎兵")}騎 ${counts["騎兵"]}</span>
    <span>${icoTyp("弓兵")}弓 ${counts["弓兵"]}</span>
    ${counts["槍"] ? `<span>${icoTyp("槍")}槍 ${counts["槍"]}</span>` : ""}
    <span class="formation-summary">${esc(form || "陣形未定")}・前${nf}／後${6 - nf}</span>
    <b class="summary-cost num">${costText}</b>
  </div>`;
}

function currentCards() {
  return occupiedSlotIds().map((name) => D.roster.find((c) => c.name === name)).filter(Boolean);
}

function occupiedSlotIds() {
  return normalizeSlots(cur && cur.slots).filter(Boolean);
}

function slotsFromCards(cards) {
  return normalizeSlots(cards || []);
}

function unitsForBoard(cards) {
  const fac = { "魏": "gi", "蜀": "shoku", "呉": "go", "群雄": "gunyu" };
  return Object.fromEntries((cards || []).map((c) => [c.name, {
    name: c.name,
    portraitUrl: c.portraitUrl || `/portrait/${encodeURIComponent(c.person)}`,
    troopType: c.troopType || c.typ,
    // 槍は**後衛に置けるかを決める当の属性**（後衛は弓兵か槍持ちだけ）。
    // 盤面から落とすと、置けない理由が盤面の上に見えなくなる。
    spear: !!c.spear,
    role: c.role,
    cost: c.cost,
    faction: fac[c.faction] || c.faction || "gunyu",
  }]));
}

function placementErrors() {
  if (!cur || !D) return [];
  const nf = FORMS[cur.form] || 3;
  return normalizeSlots(cur.slots).flatMap((name, i) => {
    if (!name) return [];
    const c = D.roster.find((x) => x.name === name);
    // 名簿に無い札はここで止める。放っておくと出陣してからサーバに
    // 「カードが見つからない」と言われるだけで、どこが悪いのか盤面から読めない。
    if (!c) return [`${name}: いまは使えない武将（✕で外して組み直す）`];
    if (i < nf && c.typ === "弓兵") return [`${c.name}: 弓兵は前衛に置けない`];
    if (i >= nf && c.typ !== "弓兵" && !c.spear) return [`${c.name}: 後衛は弓兵か槍持ちだけ`];
    return [];
  });
}

async function viewDeck(state) {
  STATE = state;
  PREP = null;
  D = await api("/api/deckdata");
  const reg = D.regs[0].name;
  const saved = D.decks[reg] || { form: "魚鱗", cards: [] };
  cur = { reg, form: saved.form, slots: slotsFromCards(saved.cards) };
  $("#app").innerHTML = `
    <div class="deck-cta action-bar">
      <div class="action-copy">
        <b>編成操作</b><span class="hint muted" id="fight2hint"></span>
        <span id="deck-msg"></span>
      </div>
      <button class="primary" id="save">この編成を登録</button>
      <button class="ghost" id="fight2">この編成で出陣</button>
    </div>
    <div class="reg-tabs" id="regtabs"></div>
    <div id="setpanel"></div>
    <div class="deck-layout fade-in">
      <div>
        <div class="roster-tools">
          <div class="filter-tabs" id="typetabs"></div>
          <div class="filter-tabs" id="bandtabs"></div>
          <div class="filter-tabs" id="factabs"></div>
          <select id="sortsel">
            <option value="cost-">コスト 高い順</option>
            <option value="cost+">コスト 低い順</option>
            <option value="men-">兵力 多い順</option>
            <option value="might-">武勇 高い順</option>
            <option value="wits-">知略 高い順</option>
            <option value="atk_pm-">攻勢 高い順</option>
            <option value="eff_men-">守勢 高い順</option>
          </select>
          <input id="search" placeholder="名で探す">
          ${D.pool ? `<span class="pool-note muted num"
            title="武将は戦記で登用して増える">登用 ${D.pool.unlocked}／${D.pool.total}</span>` : ""}
          ${D.pool && D.pool.unlocked < D.pool.total
            ? '<button class="mini ghost dev-only" id="dev-unlock" title="試験用: 戦記全クリア扱いで全員登用">全登用</button>' : ""}
        </div>
        <div class="type-legend muted num">
          ${icoTyp("歩兵")}歩兵＝近接・足は遅いが守り厚い　／　${icoTyp("騎兵")}騎兵＝最速・初撃に突撃+60%・回り込みも可　／　${icoTyp("弓兵")}弓兵＝後衛から遠射・守り薄く、詰められると乱れる　／　${icoTyp("槍")}槍持ち＝後衛にも置け、前線越しに突く（威力半減）
          <button class="mini ghost" id="guide-open" title="相性と布陣の勘どころ">軍略の手引き</button>
        </div>
        <div id="cardinfo" class="cardinfo muted">札に1回触れると詳細。素早く2回で布陣へ（編成中の札は素早く2回で外す）。</div>
        <div class="cards" id="roster" data-keep-selection></div>
      </div>
      <div class="panel mine-panel side-panel">
        <h2 class="side-heading mine-heading">自軍編成<span class="sub">6人を前衛・後衛へ配置</span></h2>
        <div id="scout"></div>
        <div id="mine-summary"></div>
        <div class="section-label">陣形</div>
        <div class="form-tabs" id="formtabs"></div>
        <div class="cost-meter" id="meter"><div class="fill"></div><div class="label"></div></div>
        <section class="army-zone mine" aria-label="自軍の盤面">
          <div class="army-zone-label"><b>自軍</b><span>駒をタップして選ぶ（マウスは横へドラッグでも）</span></div>
          <div id="slots"></div>
        </section>
        <div id="placement-errors" class="placement-errors" aria-live="polite"></div>
        <div class="entry-state" id="entrystate"></div>
        <div class="draft-panel" id="draft"></div>
        <div class="library" id="library"></div>
        <div class="onsho-panel" id="onsho"></div>
      </div>
    </div>
`;
  $("#guide-open").onclick = () => { ensureGuide().hidden = false; };
  drawRegTabs(); drawFormTabs(); drawTypeTabs();
  $("#search").oninput = drawRoster;
  $("#save").onclick = saveDeck;
  $("#fight2").onclick = () => doAttack(cur.reg);
  const du = $("#dev-unlock");
  if (du) du.onclick = async () => { await api("/api/dev_senki", {}); location.reload(); };
  drawAll();
}

function drawSortieBar() {
  const b = $("#fight2");
  if (!b || !STATE) return;
  const h = STATE.heifu || { count: 0, cap: 10 };
  const ok = D.boards_ok || {};
  const active = D.decks[cur.reg];
  const registered = !!(active && active.form === cur.form
    && active.cards.join("、") === occupiedSlotIds().join("、"));
  const save = $("#save");
  if (save) {
    save.disabled = registered;
    save.textContent = registered ? "登録済み" : "この編成を登録";
    save.classList.toggle("primary", !registered);
    save.classList.toggle("ghost", registered);
  }
  b.textContent = `この編成で ${cur.reg} に出陣`;
  b.disabled = !registered || !ok[cur.reg] || h.count < 1;
  b.classList.toggle("primary", registered && ok[cur.reg] && h.count >= 1);
  b.classList.toggle("ghost", !registered || !ok[cur.reg] || h.count < 1);
  $("#fight2hint").textContent = !registered
    ? "変更を登録すると出陣できる"
    : !ok[cur.reg]
    ? (STATE.senki && STATE.senki.gate && STATE.senki.gate[cur.reg] === false
       ? "戦記を進めるとこの戦場に挑めるようになる"
       : "この戦場のデッキを登録すれば出陣できる")
    : (h.count < 1 ? "兵符が無い（10分に1枚回復）"
       : `登録済み・兵符 ${h.count}/${h.cap}`);
}

function usedPersons(exceptReg) {
  const used = new Map();   // person -> reg
  // 戦記は PvE。登録デッキの配分に縛られず手持ちを自由に試せる場にする
  if (PREP) return used;
  for (const [reg, d] of Object.entries(D.decks)) {
    if (reg === exceptReg) continue;
    for (const n of d.cards) {
      const c = D.roster.find((x) => x.name === n);
      if (c) used.set(c.person, reg);
    }
  }
  return used;
}

function drawRegTabs() {
  $("#regtabs").innerHTML = D.regs.map((r) =>
    `<button class="${cur.reg === r.name ? "on" : ""}" data-reg="${r.name}">
      ${r.name}<small>　${r.cap}点</small></button>`).join("");
  $$("#regtabs button").forEach((b) => b.onclick = () => {
    const saved = D.decks[b.dataset.reg] || { form: "魚鱗", cards: [] };
    cur = { reg: b.dataset.reg, form: saved.form, slots: slotsFromCards(saved.cards) };
    drawRegTabs(); drawFormTabs(); drawAll();
  });
}
function drawFormTabs() {
  $("#formtabs").innerHTML = Object.entries(FORMS).map(([f, n]) =>
    `<button class="${cur.form === f ? "on" : ""}" data-f="${f}">
      <span><b>${f}</b><small>前${n}・後${6 - n}</small></span></button>`).join("");
  $$("#formtabs button").forEach((b) => b.onclick = () => {
    // 6要素を前衛左→右・後衛左→右としてそのまま新陣形へ割り当てる。
    // 空枠を詰めないため、武将の消失も暗黙の再順序化も起こらない。
    cur.form = b.dataset.f; drawFormTabs(); drawAll();
  });
}
const FILTER = { typ: "すべて", band: "全コスト", fac: "全勢力", sort: "cost-" };
const BANDS = { "全コスト": null, "低 1〜3": [1, 3], "中 4〜6": [4, 6], "高 7〜10": [7, 10] };

function chipTabs(sel, items, key) {
  const el = $(sel);
  el.innerHTML = items.map((t) =>
    `<button class="${FILTER[key] === t ? "on" : ""}" data-t="${t}">${icoTyp(t)}${t}</button>`).join("");
  $$(sel + " button").forEach((b) => b.onclick = () => {
    FILTER[key] = b.dataset.t; chipTabs(sel, items, key); drawRoster();
  });
}

function drawTypeTabs() {
  chipTabs("#typetabs", ["すべて", "歩兵", "騎兵", "弓兵"], "typ");
  chipTabs("#bandtabs", Object.keys(BANDS), "band");
  chipTabs("#factabs", ["全勢力", "魏", "蜀", "呉", "群雄"], "fac");
  $("#sortsel").value = FILTER.sort;
  $("#sortsel").onchange = () => { FILTER.sort = $("#sortsel").value; drawRoster(); };
}

function drawAll() {
  // 戦前の間（§7.62）では描き直しをそちらへ回す — 枠の増減・並べ替え・
  // 一覧クリックの経路が全部ここを通るので、出陣可否の判定を一本化できる
  if (PREP) return drawPrep();
  // 区画ごとに隔離して描く。1箇所の失敗（サーバとJSの版ずれ等）が
  // 後続の枠（軍師に相談・保存庫・軍功枠…）を巻き添えにしないように。
  for (const f of [drawRoster, drawSlots, drawMeter, drawArmySummary, drawEntryState, drawDraft,
                   drawLibrary, drawOnsho, drawSortieBar, drawScout, drawSetPanel]) {
    try { f(); } catch (e) { console.error("drawAll:", f.name, e); }
  }
}

/* ── 三面一覧（一括掲載・§7.128） ─────────────────────
   登録デッキ3面をひと目で。面間の人物取り合いで組み替えが詰むため、
   「全リセット」と「保存デッキから3面まとめて一斉登録」をここに置く。 */
const SETSEL = {};        // reg -> "keep" | "empty" | 保存デッキid
function drawSetPanel() {
  const el = $("#setpanel");
  if (!el || !D) return;
  const rows = D.regs.map((r) => {
    const d = D.decks[r.name];
    const ok = (D.boards_ok || {})[r.name];
    const state = !d ? '<span class="muted">未登録</span>'
      : ok ? '<span class="active-tag">出陣可</span>'
      : '<span class="warn-text">要確認</span>';
    const line = d
      ? `${d.form}　${d.cards.join("・")}　<span class="num muted">${d.cost != null ? d.cost + "／" + r.cap + "点" : ""}</span>`
      : '<span class="muted">—</span>';
    const opts = ['<option value="keep">いまの登録のまま</option>',
                  '<option value="empty">空にする</option>']
      .concat((D.saved || []).filter((s) => s.reg === r.name)
        .map((s) => `<option value="${s.id}">保存: ${s.name}（${s.form}・${s.cost != null ? s.cost + "点" : "?"}）</option>`));
    return `<div class="setrow">
      <button class="mini ghost" data-goreg="${r.name}">${r.name}</button>
      <span class="setrow-deck">${line}</span>${state}
      <select data-setsel="${r.name}">${opts.join("")}</select>
    </div>`;
  }).join("");
  el.innerHTML = `<details class="battle-detail set-summary" ${el.querySelector("details[open]") ? "open" : ""}>
    <summary>三面一覧<small class="muted">　登録デッキをまとめて見る・入れ替える</small></summary>
    ${rows}
    <div class="setrow set-actions">
      <button class="mini" id="set-all">選んだ組を一斉登録</button>
      <button class="mini ghost" id="set-reset">全リセット</button>
      <span class="muted" style="font-size:12px">一斉登録は3面まとめて検証してから置き換える（人物の取り合いも一度に解ける）。リセットは登録だけ消す — 保存庫は残る。</span>
    </div>
  </details>`;
  $$("#setpanel [data-goreg]").forEach((b) => b.onclick = () => {
    const saved = D.decks[b.dataset.goreg] || { form: "魚鱗", cards: [] };
    cur = { reg: b.dataset.goreg, form: saved.form, slots: slotsFromCards(saved.cards) };
    drawRegTabs(); drawFormTabs(); drawAll();
  });
  $$("#setpanel [data-setsel]").forEach((s) => {
    s.value = SETSEL[s.dataset.setsel] || "keep";
    s.onchange = () => { SETSEL[s.dataset.setsel] = s.value; };
  });
  $("#set-all").onclick = registerSet;
  $("#set-reset").onclick = resetAllDecks;
}

async function registerSet() {
  const boards = [];
  for (const r of D.regs) {
    const sel = SETSEL[r.name] || "keep";
    if (sel === "empty") continue;
    if (sel === "keep") {
      const d = D.decks[r.name];
      if (d) boards.push({ reg: r.name, form: d.form, cards: d.cards });
      continue;
    }
    const s = (D.saved || []).find((x) => String(x.id) === String(sel));
    if (s) boards.push({ reg: r.name, form: s.form, cards: s.cards });
  }
  if (!boards.length) { flashMsg("登録する面が無い（全部「空にする」なら全リセットを使う）", true); return; }
  const j = await api("/api/deck_all", { boards });
  if (j.ok) {
    D = await api("/api/deckdata");
    const saved = D.decks[cur.reg] || { form: cur.form, cards: [] };
    cur = { reg: cur.reg, form: saved.form, slots: slotsFromCards(saved.cards) };
    flashMsg("一斉登録した。次戦からこの組。"); drawRegTabs(); drawFormTabs(); drawAll();
  } else {
    const msg = Object.entries(j.errors || {})
      .map(([reg, es]) => `${reg}: ${es.join("・")}`).join("／");
    flashMsg(msg || "一斉登録できなかった", true);
  }
}

async function resetAllDecks() {
  if (!confirm("登録デッキを3面とも空にする（保存庫・登用・恩賞は残る）。よい？")) return;
  const j = await api("/api/deck_reset", {});
  if (j.ok) {
    D = await api("/api/deckdata");
    cur = { reg: cur.reg, form: "魚鱗", slots: slotsFromCards([]) };
    flashMsg("全リセットした。"); drawRegTabs(); drawFormTabs(); drawAll();
  }
}

/* ── たたき台（アンケート → 暫定デッキ・§7.54） ─────────── */
const DRAFT = { style: "おまかせ", typ: "おまかせ", faction: "おまかせ",
                form: "おまかせ", nonce: 0, note: "", open: false };

function drawDraft() {
  const el = $("#draft");
  if (!el) return;
  if (!DRAFT.open) {
    el.innerHTML = `<button id="draft-open" class="draft-toggle">
      軍師に相談する<small>（アンケートでたたき台を組む）</small></button>`;
    $("#draft-open").onclick = () => { DRAFT.open = true; drawDraft(); };
    return;
  }
  const q = (key, label, opts) => `<div class="draft-q"><span>${label}</span>
    <span class="filter-tabs">${opts.map((o) =>
      `<button class="${DRAFT[key] === o ? "on" : ""}" data-k="${key}" data-v="${o}">${o}</button>`
    ).join("")}</span></div>`;
  el.innerHTML = `
    <div class="side-label">─ 軍師に相談（たたき台） ─</div>
    ${q("style", "戦い方", ["力押し", "兵法", "守り", "おまかせ"])}
    ${q("typ", "主役", ["歩兵", "騎兵", "弓兵", "おまかせ"])}
    ${q("faction", "勢力", ["魏", "蜀", "呉", "群雄", "おまかせ"])}
    ${q("form", "陣形", ["鶴翼", "魚鱗", "雁行", "おまかせ"])}
    <div class="draft-q muted"><span></span><span>${DRAFT.form === "おまかせ"
      ? `いまの陣形タブ（${esc(cur.form)}）で組む。主役を指定したら軍師が選び直す`
      : `${esc(DRAFT.form)}のまま組む。主役の枠が足りなければ軍師がそう言う`}</span></div>
    <div class="deck-actions">
      <button id="draft-go">${DRAFT.nonce ? "引き直す" : "この方針で組む"}</button>
      <button id="draft-close" class="ghost">閉じる</button>
    </div>
    ${DRAFT.note ? `<div class="draft-note muted">${esc(DRAFT.note)}</div>` : ""}`;
  $$("#draft .filter-tabs button").forEach((b) => b.onclick = () => {
    DRAFT[b.dataset.k] = b.dataset.v; drawDraft();
  });
  $("#draft-close").onclick = () => { DRAFT.open = false; drawDraft(); };
  $("#draft-go").onclick = async () => {
    DRAFT.nonce += 1;
    const pin = DRAFT.form !== "おまかせ";
    const r = await api("/api/draft", {
      reg: cur.reg, form: pin ? DRAFT.form : cur.form,
      style: DRAFT.style, typ: DRAFT.typ, faction: DRAFT.faction,
      pin_form: pin, nonce: DRAFT.nonce,
      senki: PREP ? PREP.i : undefined });
    if (r.ok) {
      cur.slots = slotsFromCards(r.cards);
      if (r.form) cur.form = r.form;   // 主役指定なら軍師が陣形も選び直す
      DRAFT.note = r.note;
    } else {
      DRAFT.note = (r.errors && r.errors[0]) || r.note || "組めなかった";
    }
    DRAFT.open = true;
    drawFormTabs();
    if (PREP) drawPrep(); else drawAll();
    drawDraft();
  };
}

function drawScout() {
  // BO1も天下も対戦相手は事前に明かさない。天下は休戦/参戦状態だけ知らせる。
  const el = $("#scout");
  if (!el || !STATE) return;
  const t = STATE.tenka;
  el.innerHTML = t ? `<div class="next-chip">天下 ${fmtClock(t.at)}開催
    <b>${t.resting ? "休戦" : (t.eligible ? "自動参加" : "3デッキ未登録")}</b>
    <small class="muted">相手は開催時に決まる</small></div>` : "";
}

function drawLibrary() {
  const el = $("#library");
  const cap = D.regs.find((r) => r.name === cur.reg).cap;
  const mine = (D.saved || []).filter((s) => s.reg === cur.reg);
  const active = D.decks[cur.reg];
  const rows = mine.map((s) => {
    const isActive = active && active.cards.join("、") === s.cards.join("、")
      && active.form === s.form;
    const over = s.cost !== null && s.cost > cap;
    // 戦績（§7.120）: レート対象で同じ編成（札の並び＋陣形）で戦った記録。
    // 編成を1枚でも変えると別デッキ扱い（数字はその編成のものとして残る）。
    const rec = s.rec && s.rec.n
      ? `<span class="lib-rec num" title="レート対象戦のこの編成の戦績">出陣${s.rec.n}・${s.rec.w}勝${s.rec.l}敗（${Math.round(100 * s.rec.w / Math.max(1, s.rec.w + s.rec.l))}%）</span>`
      : '<span class="lib-rec muted">未出陣</span>';
    return `<div class="lib-row ${isActive ? "active" : ""}">
      <div class="lib-line1">
        <span class="lname">${esc(s.name)}</span>
        ${isActive ? '<span class="active-tag">登録中</span>' : ""}
        ${rec}
      </div>
      <div class="lib-line2">
        <span class="form-tag">${esc(s.form)}</span>
        <span class="val num ${over ? "warn" : ""}">${s.cost === null ? "?" : s.cost + "点"}</span>
        <span class="lib-btns">
          <button class="mini" data-a="load" data-id="${s.id}">呼び出す</button>
          <button class="mini" data-a="reg" data-id="${s.id}">登録</button>
          <button class="mini" data-a="del" data-id="${s.id}">✕</button>
        </span>
      </div>
    </div>`;
  }).join("");
  const slots = D.deck_slots || 10;
  el.innerHTML = `<div class="side-label">─ デッキ保存庫（${esc(cur.reg)}・${mine.length}/${slots}枠） ─</div>
    <div class="lib-save">
      <input id="savename" placeholder="この編成に名前を付けて保存" maxlength="24">
      <button class="mini" id="dosave">保存</button>
    </div>
    ${rows || "<p class='muted' style='font-size:12px'>まだ保存が無い。保存しておけば「登録」で一発で切り替えられる。</p>"}`;
  $("#dosave").onclick = async () => {
    const name = $("#savename").value.trim();
    if (!name) { flashMsg("名前を付ける", true); return; }
    const r = await api("/api/savedeck",
      { name, reg: cur.reg, form: cur.form, cards: occupiedSlotIds() });
    if (!r.ok) { flashMsg(r.errors.join("／"), true); return; }
    D = await api("/api/deckdata"); flashMsg("保存した。"); drawLibrary();
  };
  $$("#library .lib-btns button").forEach((b) => b.onclick = async () => {
    const s = mine.find((x) => x.id === +b.dataset.id);
    if (b.dataset.a === "load") {
      cur.form = s.form; cur.slots = slotsFromCards(s.cards); drawFormTabs(); drawAll();
      flashMsg(`「${s.name}」を編成台へ。登録するまで次戦には使われない。`);
    }
    if (b.dataset.a === "reg") {
      const r = await api("/api/deck", { reg: cur.reg, form: s.form, cards: s.cards });
      if (!r.ok) { flashMsg(r.errors.join("／"), true); return; }
      D = await api("/api/deckdata");
      cur.form = s.form; cur.slots = slotsFromCards(s.cards); drawFormTabs();
      flashMsg(`「${s.name}」を登録した。次戦からこの陣。`); drawAll();
    }
    if (b.dataset.a === "del") {
      await api("/api/deldeck", { id: s.id });
      D = await api("/api/deckdata"); drawLibrary();
    }
  });
}

function deckGenerals() {
  const set = new Set();
  for (const d of Object.values(D.decks)) for (const n of d.cards) set.add(n);
  for (const n of occupiedSlotIds()) set.add(n);
  return [...set];
}

function drawOnsho() {
  const el = $("#onsho");
  const budget = (D.onsho_budgets || {})[cur.reg] || 100;
  const head = `<div class='side-label'>─ 軍功枠（恩賞のセット）　${onshoKou()}／${budget}功
    <button class="mini ghost dev-only" id="dev-onsho" title="試験用: 全種の恩賞を1つずつ獲得">全恩賞</button> ─</div>`;
  const gens = deckGenerals();
  const rows = (D.onsho || []).map((o) => `
      <div class="onsho-row">
        <div class="onsho-line1">
          <span class="oname">【${esc(o.name)}】</span>
          <span class="val num">${o.kou}功</span>
          <span class="muted num">×${o.total}</span>
          ${o.sets.map((s) => `<span class="onsho-set num">${esc(s.general)}
            <button class="mini tiny" data-id="${s.id}" data-g="">✕</button></span>`).join("")}
          ${o.unset.length ? `
            <select data-id="${o.unset[0]}">
              <option value="">（セット先を選ぶ${o.unset.length > 1 ? `・残${o.unset.length}` : ""}）</option>
              ${gens.map((g) => `<option>${esc(g)}</option>`).join("")}
            </select>` : ""}
        </div>
        ${o.desc ? `<div class="onsho-desc muted">${esc(o.desc)}</div>` : ""}
      </div>`).join("");
  el.innerHTML = head +
    (rows || "<p class='muted'>まだ恩賞が無い。毎日1つ授かる。</p>") +
    ((D.onsho || []).length
      ? `<p class='muted' style='font-size:11.5px'>恩賞は軍功予算（この戦場は${budget}功・` +
        "全員一律）から払う。デッキ本体の点は食わない。</p>" : "");
  const refresh = async () => { D = await api("/api/deckdata"); drawAll(); };
  $$("#onsho select").forEach((sel) => sel.onchange = async () => {
    if (!sel.value) return;
    const r = await api("/api/onsho", { owned_id: +sel.dataset.id, general: sel.value });
    if (!r.ok) { flashMsg(r.errors.join("／"), true); }
    refresh();
  });
  $$("#onsho button[data-id]").forEach((b) => b.onclick = async () => {
    await api("/api/onsho", { owned_id: +b.dataset.id, general: "" });
    refresh();
  });
  const dv = $("#dev-onsho");
  if (dv) dv.onclick = async () => { await api("/api/dev_onsho", {}); refresh(); };
}

/* 一覧の札のシングル／ダブル判定（§7.119）。350ms は iOS のダブルタップ
   判定と同じ帯。シングルの動作（詳細表示）は無害なので**遅延させずに即発火**
   する — 2回目が来たら配置へ昇格するだけで、待ちのもたつきが無い。 */
const DOUBLE_TAP_MS = 350;
let rosterTap = { name: null, at: 0 };

function drawRoster() {
  const q = $("#search").value.trim();
  const used = usedPersons(cur.reg);
  const inDeck = new Set(occupiedSlotIds());
  const band = BANDS[FILTER.band];
  const key = FILTER.sort.slice(0, -1);
  const dir = FILTER.sort.endsWith("-") ? -1 : 1;
  const list = D.roster
    .filter((c) => (FILTER.typ === "すべて" || c.typ === FILTER.typ))
    .filter((c) => !band || (c.cost >= band[0] && c.cost <= band[1]))
    .filter((c) => (FILTER.fac === "全勢力" || c.faction === FILTER.fac))
    .filter((c) => !q || c.name.includes(q) || c.person.includes(q))
    .sort((a, b) => dir * (a[key] - b[key])
      || b.cost - a.cost || a.name.localeCompare(b.name, "ja"));
  $("#roster").innerHTML = list.map((c) => {
    const u = used.get(c.person);
    const selfInDeck = inDeck.has(c.name);
    // 同じ人物の別バージョンが盤面に居る札は従来どおり無効。**盤面に居る
    // その札自身は無効にしない**（§7.119）— 触れば詳細が出て、素早く2回で
    // 枠から外せる。無効のままだと「編成中の札の詳細が見られない」うえ、
    // 外す手段が盤面側の ✕ だけになる。
    const dup = occupiedSlotIds().some((n) => {
      const x = D.roster.find((r) => r.name === n);
      return x && x.person === c.person && x.name !== c.name; });
    // 戦前の間では、いまの残り予算で買えない札を沈める（詰将棋の可読性）。
    // ただし**枠が埋まっているときは沈めない** — 誰かを外せば買えるので、
    // 値段だけで沈めると「その武将は使えない」という嘘になる。
    const rest = PREP ? PREP.cap - deckCost() : Infinity;
    const pricey = PREP && occupiedSlotIds().length < 6
      && !inDeck.has(c.name) && c.cost > rest + 1e-9;
    const off = u || dup;
    return `<button type="button" class="card f${c.faction} ${off ? "used" : ""} ${selfInDeck ? "indeck" : ""} ${pricey ? "pricey" : ""}"
         data-n="${esc(c.name)}" ${off ? "disabled" : ""} aria-label="${esc(c.name)} ${esc(c.typ)} コスト${c.cost}${selfInDeck ? "（編成中・素早く2回押すと外す）" : ""}">
      <div class="face"><img src="${c.portraitUrl || `/portrait/${encodeURIComponent(c.person)}`}"
        loading="lazy" alt="">
        ${icoCost(c.cost)}
        <span class="typ">${icoTyp(c.typ, c.spear)}</span>
        ${u ? `<span class="usedby">${esc(u).slice(0, 1)}で使用</span>`
            : (selfInDeck ? `<span class="usedby">編成中</span>` : "")}
      </div>
      <div class="name">${esc(c.name)}${c.version > 1 ? `<span class="ver">v${c.version}</span>` : ""}<span class="role">${esc(c.role)}</span></div>
      <div class="stats num">武勇${c.might}　知略${c.wits}</div>
      <div class="stats num">攻勢${c.atk_pm}　守勢${(c.eff_men / 1000).toFixed(1)}千</div>
      <div class="skill">【${esc(c.skill)}】</div>
    </button>`;
  }).join("");
  $$("#roster .card:not([disabled])").forEach((el) => el.onclick = () => {
    const name = el.dataset.n;
    const board = FORMATION_BOARDS.get($("#slots"));
    const selected = board ? board.selectedIndex : null;
    const next = normalizeSlots(cur.slots);
    const inDeckNow = next.includes(name);
    // 盤面で駒を選択中の交代は従来どおり**シングルで確定**（§7.119）。
    // 「選択中○○」の帯が出ている状態の操作なので、誤爆の恐れが小さい。
    if (selected !== null && selected !== undefined && !inDeckNow) {
      const old = next[selected];
      next[selected] = name;
      board.announcement = `${old || "空き枠"}を${name}へ交代しました`;
      board.selectedIndex = null;
      board.keyboardTargetIndex = null;
      cur.slots = next;
      showCardInfo(name);
      drawAll();
      return;
    }
    // シングル＝詳細／素早く2回＝配置（編成中の札なら解除）（§7.119）。
    // 以前はシングルで即配置しており、「詳細を見たいだけなのに選ばれる」が
    // タッチ端末で避けられなかった（ホバーが無い）。
    const now = Date.now();
    const twice = rosterTap.name === name && now - rosterTap.at <= DOUBLE_TAP_MS;
    rosterTap = twice ? { name: null, at: 0 } : { name, at: now };
    if (!twice) { showCardInfo(name); return; }
    if (inDeckNow) {
      next[next.indexOf(name)] = null;
      if (board) {
        board.selectedIndex = null;
        board.keyboardTargetIndex = null;
        board.announcement = `${name}を枠から外しました`;
      }
      cur.slots = next;
      drawAll();
      return;
    }
    const empty = next.findIndex((x) => !x);
    if (empty < 0) {
      flashMsg("枠が埋まっている。交代する武将を盤面で選んでから、この札を押す（✕で外してもよい）。", true);
      return;
    }
    next[empty] = name;
    cur.slots = next;
    drawAll();
  });
  $$("#roster .card").forEach((el) => {
    el.onmouseenter = () => showCardInfo(el.dataset.n);
    el.onfocus = () => showCardInfo(el.dataset.n);
  });
}

function showCardInfo(name) {
  const c = D.roster.find((x) => x.name === name);
  if (!c) return;
  const traits = (c.traits || []).length ? (c.traits || []).map((t) => `
    <div class="ci-row">
      <span class="tag trait-tag">特性・${esc(t.kind)}</span>
      <b>【${esc(t.name)}】</b> ${esc(t.desc)}
      ${t.cond ? `<span class="muted">（${esc(t.cond)}）</span>` : ""}
    </div>`).join("")
    : `<div class="ci-row muted">
      <span class="tag trait-tag">特性</span>
      生まれつきの特性は持たない（軍功枠の恩賞は付けられる）
    </div>`;
  $("#cardinfo").innerHTML = `
    <img class="ci-face" src="${c.portraitUrl || `/portrait/${encodeURIComponent(c.person)}`}" alt="">
    <div class="ci-body">
    <div class="ci-head">
      ${icoCost(c.cost)}
      <span class="ci-name">${esc(c.name)}${c.version > 1 ? `<span class="ver">v${c.version}</span>` : ""}</span>
      <span class="muted">${esc(c.faction)}・${icoTyp(c.typ, c.spear)}${esc(c.typ)}${c.spear ? "（槍・後衛可）" : ""}・${esc(c.role)}</span>
    </div>
    <div class="ci-stats num">武勇 ${c.might}　知略 ${c.wits}</div>
    <div class="ci-stats num muted">兵力 ${c.men.toLocaleString()}
      　攻勢 毎分約${c.atk_pm.toLocaleString()}人を削る
      　守勢 実効${c.eff_men.toLocaleString()}人ぶんを受ける</div>
    <div class="ci-row">
      <span class="tag skill-tag">兵法</span>
      <b>【${esc(c.skill)}】</b> <span class="muted">対象 ${esc(c.skill_target)}｜</span>${esc(c.skill_desc)}${
        /損害|延焼/.test(c.skill_desc) ? '<span class="muted">　※損害は敵の守りで目減りする</span>' : ""}
    </div>
    ${c.cadence ? `<div class="ci-row">
      <span class="tag skill-tag">兵法の巡り</span> <b>${esc(c.cadence.tier_jp)}</b>
      <span class="muted">　初動：${esc(c.cadence.first_label)}（自然蓄積 約${c.cadence.first_m}分）
      　再発：${esc(c.cadence.repeat_label)}（自然蓄積 約${c.cadence.repeat_m}分）</span>
      <div class="muted" style="font-size:.85em">※自然蓄積の目安。攻撃・被弾により早まります
        <details style="display:inline-block"><summary style="display:inline;cursor:pointer">内部値</summary>
        消費${esc(c.gauge_cost)}%・上昇${esc(c.gauge_rate)}・初期${esc(c.gauge_init)}</details></div>
    </div>` : ""}
    ${traits}
    ${c.quote ? `<div class="ci-quote">「${esc(c.quote)}」</div>` : ""}
    </div>`;
}

function drawSlots() {
  hideTip();
  const root = $("#slots");
  if (!root) return;
  mountFormationBoard(root, {
    formation: FORM_KEY[cur.form] || "gyorin",
    slots: normalizeSlots(cur.slots),
    units: unitsForBoard(D.roster),
    interactive: true,
    onPieceTap: (name) => showCardInfo(name),
    onSlotsChange: (next) => {
      cur.slots = normalizeSlots(next);
      drawAll();
    },
  });
  root.querySelectorAll(".fb-piece.occupied").forEach((piece) => {
    const name = normalizeSlots(cur.slots)[+piece.dataset.slotIndex];
    piece.addEventListener("mouseenter", (e) => showTip(e, name));
    piece.addEventListener("mousemove", moveTip);
    piece.addEventListener("mouseleave", hideTip);
    piece.addEventListener("focus", () => showCardInfo(name));
  });
  const errors = placementErrors();
  const errorBox = $("#placement-errors");
  if (errorBox) errorBox.innerHTML = errors.map((x) => `<span>⚠ ${esc(x)}</span>`).join("");
}

/* ── 概要チップ（枠の武将のマウスオン・§7.59） ─────────── */
function showTip(e, name) {
  const c = D.roster.find((x) => x.name === name);
  if (!c || window.innerWidth <= 760) return;   // タッチ環境はクリックで詳細
  let tip = $("#tip");
  if (!tip) {
    document.body.insertAdjacentHTML("beforeend", '<div id="tip"></div>');
    tip = $("#tip");
  }
  tip.innerHTML = `
    <b>${esc(c.name)}</b>
    <span class="muted">${esc(c.faction)}・${icoTyp(c.typ, c.spear)}${esc(c.typ)}${c.spear ? "（槍）" : ""}・${esc(c.role)}・${c.cost}点</span><br>
    <span class="num">兵${(c.men / 1000).toFixed(1)}千　攻勢${c.atk_pm}　守勢${(c.eff_men / 1000).toFixed(1)}千</span><br>
    <span class="muted">【${esc(c.skill)}】${c.cadence ? "　兵法の巡り " + esc(c.cadence.tier_jp) : ""}　特性: ${c.traits.length
      ? c.traits.map((t) => t.name).join("・") : "─（持たない）"}</span><br>
    <span class="tip-hint">クリックで詳細</span>`;
  tip.style.display = "block";
  moveTip(e);
}

function moveTip(e) {
  const tip = $("#tip");
  if (!tip || tip.style.display === "none") return;
  const pad = 14;
  let x = e.clientX + pad, y = e.clientY + pad;
  const r = tip.getBoundingClientRect();
  if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}

function hideTip() {
  const tip = $("#tip");
  if (tip) tip.style.display = "none";
}
// チップは fixed なのでスクロールすると場所が嘘になる上、要素が
// カーソルの下から滑り出ても mouseleave が来ないことがある。保険で消す。
if (typeof document !== "undefined") {
  document.addEventListener("scroll", hideTip, { passive: true, capture: true });
}

function deckCost() {
  return occupiedSlotIds().reduce((s, n) => {
    const c = D.roster.find((x) => x.name === n); return s + (c ? c.cost : 0);
  }, 0);
}

function drawArmySummary() {
  const el = $("#mine-summary");
  if (!el || !D || !cur) return;
  const cap = PREP ? PREP.cap : D.regs.find((r) => r.name === cur.reg).cap;
  el.innerHTML = armySummary(currentCards(), cur.form, deckCost(), cap, "mine");
}

function onshoKou() {
  // このデッキに乗っている軍功（功）。予算は別枠（§7.61）— 本体の点を食わない
  if (!D.onsho) return 0;
  const inDeck = new Set(occupiedSlotIds());
  return D.onsho.reduce((s, o) =>
    s + o.kou * o.sets.filter((x) => inDeck.has(x.general)).length, 0);
}

function drawMeter() {
  const cap = PREP ? PREP.cap : D.regs.find((r) => r.name === cur.reg).cap;
  const cost = deckCost();
  const m = $("#meter");
  m.classList.toggle("over", cost > cap + 1e-9);
  m.querySelector(".fill").style.width = Math.min(100, cost / cap * 100) + "%";
  const kou = onshoKou();
  const budget = (D.onsho_budgets || {})[cur.reg] || 100;
  const ex = kou ? `　軍功 ${kou}／${budget}功${kou > budget ? "（超過！）" : ""}` : "";
  m.querySelector(".label").textContent =
    `${cost} ／ ${cap}点` +
    (cost > cap + 1e-9 ? "　超過！" : (cost < cap ? `　余り${cap - cost}` : "　ぴったり")) + ex;
}

function drawEntryState() {
  const el = $("#entrystate");
  const ok = D.boards_ok || {};
  const marks = [...D.regs.map((r) => r.name), "天下"].map((n) =>
    `<span class="${ok[n] ? "ok-b" : "ng-b"}">${ok[n] ? "○" : "―"} ${n}</span>`
  ).join("　");
  el.innerHTML = `<div class="num">${marks}</div>` +
    D.entry_errors.map((e) => `<div class="warn muted">・${esc(e)}</div>`).join("");
}

let msgTimer = null;
function flashMsg(text, isErr) {
  const el = $("#deck-msg");
  el.textContent = text; el.className = isErr ? "err" : "ok";
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { el.textContent = ""; }, 4000);
}

async function saveDeck() {
  const cards = occupiedSlotIds();
  const r = await api("/api/deck", { reg: cur.reg, form: cur.form, cards });
  if (r.ok) {
    D.decks[cur.reg] = { form: cur.form, cards: [...cards] };
    D.entry_errors = r.entry_errors;
    D.boards_ok = r.boards_ok;
    flashMsg("登録した。"); drawEntryState(); drawSortieBar();
  } else {
    flashMsg(r.errors.join("／"), true);
  }
}

/* ── 軍議演習（過去の敵魚拓 × 現在の登録デッキ） ───────── */
async function viewCouncil(_state) {
  const d = await api("/api/council");
  const t = d.ticket;
  const chosen = +(new URLSearchParams(location.search).get("source") || 0);
  const marks = (s) => s
    ? `<span class="council-marks num">${[...s].map((x) =>
        `<b class="${x === "○" ? "win" : (x === "●" ? "lose" : "draw")}">${x}</b>`
      ).join("")}</span>` : '<span class="muted">—</span>';
  const tickets = Array.from({ length: t.cap }, (_, i) =>
    `<i class="${i < t.count ? "full" : ""}" aria-hidden="true"></i>`).join("");
  const rows = d.targets.map((x) => `
    <tr class="${x.id === chosen ? "selected" : ""}" id="source-${x.id}">
      <td class="num">${esc(x.at)}</td><td><span class="mode-tag">${esc(x.mode)}</span></td>
      <td>${esc(x.board)}</td><td><b>${esc(x.foe)}</b></td><td>${marks(x.marks)}</td>
      <td><button class="council-go primary mini" data-source="${x.id}"
          data-board="${esc(x.board)}" data-foe="${esc(x.foe)}"
          ${!x.ready || t.count <= 0 ? "disabled" : ""}>この布陣と演習</button>
          ${!x.ready ? '<small class="warn">登録デッキ要確認</small>' : ""}</td>
    </tr>`).join("");
  $("#app").innerHTML = `
    <section class="panel council-hero fade-in">
      <div>
        <p class="eyebrow">過去を盤上へ呼び戻す</p>
        <h2>軍議演習</h2>
        <p>過去に戦った敵の布陣を魚拓として再現し、<b>現在登録中の自軍デッキ</b>をぶつける。
        勝敗を研究する場なので、武名・通常戦績・報酬は動かない。</p>
      </div>
      <div class="enshu-box">
        <div><b>${esc(t.name)}</b> <strong class="num" id="enshu-count">${t.count}／${t.cap}</strong></div>
        <div class="enshu-pips">${tickets}</div>
        <small class="muted" id="enshu-wait">${t.count >= t.cap ? "満タン" : "次の1枚まで計算中"}</small>
        <button id="enshu-max" class="ghost mini dev-only">無料でMAX</button>
      </div>
    </section>
    <section class="panel fade-in">
      <h2>対戦魚拓を選ぶ<span class="sub">1戦につき演習令1枚・同じ魚拓へ何度でも挑戦可</span></h2>
      <div class="table-scroll"><table class="std council-table">
        <thead><tr><td>日時</td><td>種別</td><td>戦場</td><td>相手</td><td>当時</td><td></td></tr></thead>
        <tbody>${rows || '<tr><td colspan="6" class="muted">まず対戦か在野戦を行うと、敵布陣の魚拓がここへ残ります。</td></tr>'}</tbody>
      </table></div>
      <p class="muted council-note">編成を変えるときは「編成」で登録し直してから戻る。敵側はこの対戦時点の布陣のまま変わらない。</p>
    </section>`;

  let wait = t.next_in;
  const drawWait = () => {
    const el = $("#enshu-wait");
    if (!el) return;
    if (t.count >= t.cap) { el.textContent = "満タン"; return; }
    const mm = Math.floor(Math.max(0, wait) / 60);
    const ss = Math.max(0, wait) % 60;
    el.textContent = `次の1枚まで ${mm}:${String(ss).padStart(2, "0")}（10分で1枚）`;
    if (wait > 0) wait--;
  };
  drawWait();
  const clock = setInterval(drawWait, 1000);
  window.addEventListener("pagehide", () => clearInterval(clock), { once: true });

  $$(".council-go").forEach((b) => b.onclick = () =>
    doCouncil(+b.dataset.source, b.dataset.board, b.dataset.foe));
  $("#enshu-max").onclick = async () => {
    try { await api("/api/dev_enshu", {}); location.reload(); }
    catch (e) { alert("無料MAXは手元の試験用起動でだけ使える: " + e.message); }
  };
  if (chosen) setTimeout(() => {
    const el = $("#source-" + chosen);
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }, 80);
}

async function doCouncil(sourceId, board, foe) {
  document.body.insertAdjacentHTML("beforeend", `
    <div id="overlay"><div class="box"><div class="march">軍議演習</div>
      <p class="muted">${esc(foe)}の対戦魚拓を盤上へ再現しております……</p></div></div>`);
  try {
    const r = await api("/api/council_fight", { source_id: sourceId });
    sessionStorage.setItem("fight:" + r.battle_id, JSON.stringify(
      { label: `軍議演習・${board}`, result: r, kind: "council",
        source_id: sourceId, board, foe }));
    location.href = "/replay?id=" + r.battle_id + "&from=fight";
  } catch (e) {
    const ov = $("#overlay"); if (ov) ov.remove();
    let msg = e.message;
    try { msg = JSON.parse(e.message).error || msg; } catch (_x) { /* 素通し */ }
    alert(msg);
  }
}

/* ── リプレイ一覧 ───────────────────── */
async function viewReplays(state) {
  const d = await api("/api/replays");
  // 勝敗の刻み（§7.81）。marks は「その行の視点」（自分の戦歴なら自分、
  // 他家の戦いなら a 側）から見た ○●△。天下は3戦ぶん並ぶ。
  const verdictCell = (m) => {
    if (!m.marks) return '<td class="muted">—</td>';
    const w = (m.marks.match(/○/g) || []).length;
    const l = (m.marks.match(/●/g) || []).length;
    const v = w > l ? "勝" : (l > w ? "敗" : "分");
    const cls = w > l ? "win" : (l > w ? "lose" : "draw");
    const boards = ["汜", "官", "赤"];
    const detail = m.marks.length > 1
      ? `<small class="num">${[...m.marks].map((c, k) =>
          `${boards[k] || ""}${c}`).join(" ")}</small>` : "";
    return `<td class="verdict-cell"><b class="verdict ${cls}">${v}</b> ${detail}</td>`;
  };
  const row = (m) => `
    <tr class="${m.mine ? "me" : ""}">
      <td class="num">${esc(m.at)}</td>
      <td><span class="mode-tag">${esc(m.mode)}${m.role === "防" ? "・防衛" : ""}</span></td>
      <td>${esc(m.board)}</td>
      <td>${esc(m.a)} <small>対</small> ${esc(m.b)}</td>
      ${verdictCell(m)}
      <td><a href="/replay?id=${m.id}">観る</a>
        ${m.can_council ? `<a class="btn ghost mini" href="/council?source=${m.id}">演習</a>` : ""}</td></tr>`;
  const mine = d.battles.filter((m) => m.mine);
  const rest = d.battles.filter((m) => !m.mine);
  const modeKey = (m) => m.mode_key || ({ "天下": "tenka", "挑戦": "ranked",
    "軍議": "council" }[m.mode] || m.mode);
  const tenkaByDay = new Map();
  for (const m of mine) {
    if (modeKey(m) !== "tenka") continue;
    const day = m.day || "";
    if (!tenkaByDay.has(day)) tenkaByDay.set(day, []);
    tenkaByDay.get(day).push(m);
  }
  const tenkaGroup = (day, rows) => {
    const s = tenkaDayStats(rows);
    return `<tr class="tenka-day-row"><td colspan="6">
      <details class="tenka-day-details">
        <summary><b>${esc(formatLocalDay(day))}</b>
          <span>天下 ${s.w}勝 ${s.l}敗${s.d ? ` ${s.d}分` : ""}</span>
          <small>詳細を見る（${s.n}戦）</small></summary>
        <div class="table-scroll tenka-day-table"><table class="std"><tbody>
          ${rows.map(row).join("")}
        </tbody></table></div>
      </details></td></tr>`;
  };
  const filters = [
    ["all", "すべて"], ["tenka", "天下"], ["ranked", "挑戦"],
    ["council", "軍議"],
  ];
  const filterCount = (key) => key === "all" ? mine.length
    : mine.filter((m) => modeKey(m) === key).length;
  $("#app").innerHTML = `
    <div class="panel fade-in" style="margin-bottom:16px">
      <h2>自分の戦歴<span class="sub">防衛戦（挑まれた側）も残る</span></h2>
      <div class="replay-filters" role="group" aria-label="戦歴の種別">
        ${filters.map(([key, label]) => `<button type="button" class="mini ghost"
          data-replay-filter="${key}" aria-pressed="false">${label}<small>${filterCount(key)}</small></button>`).join("")}
      </div>
      <div class="table-scroll"><table class="std"><tbody id="my-replay-rows"></tbody></table></div>
    </div>
    <div class="panel fade-in">
      <h2>近ごろの合戦<span class="sub">他家の戦いも観て研究できる</span></h2>
      <div class="table-scroll"><table class="std">${rest.map(row).join("")
        || "<tr><td class='muted'>記録なし</td></tr>"}</table></div>
    </div>`;
  let active = "all";
  const renderMine = () => {
    const target = $("#my-replay-rows");
    const seenDays = new Set();
    const rows = [];
    for (const m of mine) {
      const key = modeKey(m);
      if (active !== "all" && key !== active) continue;
      if (key === "tenka") {
        const day = m.day || "";
        if (seenDays.has(day)) continue;
        seenDays.add(day);
        rows.push(tenkaGroup(day, tenkaByDay.get(day) || [m]));
      } else {
        rows.push(row(m));
      }
    }
    target.innerHTML = rows.join("")
      || '<tr><td colspan="6" class="muted">この種別の記録はまだありません</td></tr>';
    $$('[data-replay-filter]').forEach((b) => {
      const on = b.dataset.replayFilter === active;
      b.classList.toggle("primary", on);
      b.classList.toggle("ghost", !on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  };
  $$('[data-replay-filter]').forEach((b) => b.onclick = () => {
    active = b.dataset.replayFilter;
    renderMine();
  });
  renderMine();
}

/* ── リプレイ再生 ───────────────────── */
/* 軍中の心得（§7.79）: 敗北の下に1つずつ出す小話。**規則の正本ではなく
   読み物** — 中身は全部エンジンの実測に基づく（三すくみ・抑制・槍後衛・
   避雷針・余剰ゲージ・本陣）。ローディング画面が無い（速すぎる）ので、
   負けて悔しい時にだけ届く形にした。 */
const WAR_TIPS = [
  "三すくみを覚えておけ——歩は騎を受け止め、騎は弓を蹴散らし、弓は歩を射抜く。",
  "陣形は弓の数を決める。鶴翼は弓二・魚鱗は弓三・雁行は弓四。矢を増やすほど、壁は薄くなる。",
  "弓は肉薄されると矢も兵法も鈍る。射手を守る壁を惜しむな。",
  "槍持ちの歩兵は後衛にも置ける。回り込む騎馬は、槍が突き止める。",
  "前衛は高く積め。最も安い札が矢面に立ち、高い札が長く戦う。",
  "一枚の柱に頼る軍は、柱を失えば崩れる。敵の柱は壁で受け、脇の小勢から崩せ。",
  "使い切れなかった点は、開戦の気勢に変わる。無駄にはならぬが、兵にもならぬ。",
  "本陣を預かる将が崩れれば、全軍が動揺する。本陣は置き所が肝心よ。",
  "兵法は消費が軽いほど数を撃ち、重いほど一撃に懸ける。兵法の巡りも編成のうち。",
  "敗れた戦こそ実況を読み返せ。どの隊が先に崩れたかに、次の布陣の答えがある。",
  "同じ名の兵法は重ならぬ。強いほう一つだけが効く。守りを固めるなら、別々の兵法を並べよ。",
  "違う名の兵法どうしなら効果は足し合わさる。ただし一つの能力への合計は、上下とも五割で頭打ちよ。",
];

function replayOutcome(g, d, isParticipant) {
  const remain = (units) => {
    const men0 = (units || []).reduce((s, u) => s + (u.men0 || 0), 0);
    const men = (units || []).reduce((s, u) => s + (u.men || 0), 0);
    return men0 ? Math.round(men / men0 * 100) : 0;
  };
  const mineRemain = remain(g.mine), foeRemain = remain(g.foe);
  const clocks = (g.lines || []).map((ln) => ln.match(/【(\d+:\d+)】/))
    .filter(Boolean).map((m) => m[1]);
  const endClock = clocks[clocks.length - 1] || "終戦";
  const ending = (g.lines || []).slice(-3).join(" ");
  const cls = g.verdict === "勝ち" ? "win" : (g.verdict === "負け" ? "lose" : "draw");
  const verdict = g.verdict === "勝ち" ? "勝利" : (g.verdict === "負け" ? "敗北" : "引き分け");
  const reason = /日没|日が暮れ/.test(ending) ? "日没時の判定"
    : (/総崩れ|本陣/.test(ending) ? "潰走による決着" : "戦闘終了");
  const subject = isParticipant ? "自軍" : esc(d.mine_name);
  const tip = (cls === "lose" && isParticipant)
    ? `<div class="battle-tip"><b>軍中の心得</b>　${esc(
        WAR_TIPS[Math.floor(Math.random() * WAR_TIPS.length)])}</div>` : "";
  return `<div class="battle-summary ${cls}">
    <div class="summary-verdict">${verdict}</div>
    <div class="summary-body">
      <b>${subject}の${verdict}</b>
      <span>${endClock}・${reason}</span>
      <span class="remain num">残存　${subject} ${mineRemain}% ／ ${isParticipant ? "敵軍" : esc(d.foe_name)} ${foeRemain}%</span>
    </div>
  </div>${tip}`;
}

async function viewReplay(state) {
  const qs = new URLSearchParams(location.search);
  const id = qs.get("id");
  const d = await api("/api/replay?id=" + id);
  const isParticipant = !!(state.me
    && (state.me.name === d.mine_name || state.me.name === d.foe_name));
  // 出陣からそのまま来た場合は「実況を見届けてから判」（結果→履歴の順だと
  // 先に勝敗を知ってしまい、戦いを観る意味が薄れる・テストプレイの指摘）
  let FIGHT = null;
  const fromFight = qs.get("from") === "fight";
  if (fromFight) {
    try { FIGHT = JSON.parse(sessionStorage.getItem("fight:" + id) || "null"); }
    catch (_e) { FIGHT = null; }
  }
  if (FIGHT) document.body.classList.add("suspense");
  const wins = d.games.filter((g) => g.verdict === "勝ち").length;
  const losses = d.games.filter((g) => g.verdict === "負け").length;
  const overall = wins > losses ? "勝利" : (losses > wins ? "敗北" : "引き分け");
  const mineSide = isParticipant ? "自軍" : "A軍";
  const foeSide = isParticipant ? "敵軍" : "B軍";
  const tabs = d.games.length > 1
    ? `<div class="game-tabs">${d.games.map((g, i) =>
        `<button data-i="${i}" class="${i === 0 ? "on" : ""}">
          第${i + 1}戦 ${esc(g.label)} <b>${g.verdict}</b></button>`).join("")}</div>`
    : "";
  const fightBack = FIGHT && FIGHT.kind === "council" ? "/council"
    : (FIGHT && FIGHT.kind === "ladder" ? "/" : (FIGHT ? "/senki" : "/replays"));
  const fightBackLabel = FIGHT && FIGHT.kind === "council" ? "← 軍議演習へ"
    : (FIGHT && FIGHT.kind === "ladder" ? "← 対戦へ" : (FIGHT ? "← 戦記へ" : "← 戦歴へ"));
  $("#app").innerHTML = `
    <div class="replay-head">
      <a class="btn ghost mini" href="${fightBack}">${fightBackLabel}</a>
      <div><h2>${FIGHT ? esc(FIGHT.label) : esc(d.board)}</h2>
      <span class="muted">${FIGHT ? "戦況を見届けよ" : esc(d.when || "")}
        ${d.games.length > 1 && !FIGHT ? `・${wins}勝${losses}敗 ${overall}` : ""}
        ${d.rule_version ? `<span class="rule-ver" title="対戦当時の戦闘ルール版（§7.135）">ルール${esc(d.rule_version)}</span>` : ""}</span></div>
      ${d.can_council ? `<a class="btn council-shortcut" href="/council?source=${d.battle_id}">軍議演習で再戦</a>` : ""}
    </div>
    <div class="battle-card">
      <div class="battle-side mine"><span>${mineSide}</span><b>${esc(d.mine_name)}</b></div>
      <div class="battle-vs">対</div>
      <div class="battle-side foe"><span>${foeSide}</span><b>${esc(d.foe_name)}</b></div>
    </div>
    ${tabs}
    <div class="replay-armies" aria-label="両軍の布陣">
      <section class="army-zone mine replay-army" aria-label="${mineSide}の盤面">
        <div class="army-zone-label"><b>${mineSide}</b><span>${esc(d.mine_name)}・読み取り専用</span></div>
        <div id="replay-mine-board"></div>
      </section>
      <section class="army-zone foe replay-army" aria-label="${foeSide}の盤面">
        <div class="army-zone-label"><b>${foeSide}</b><span>${esc(d.foe_name)}・読み取り専用</span></div>
        <div id="replay-foe-board"></div>
      </section>
    </div>
    <div class="replay-controls">
      <button id="play" class="primary">▶ 再生</button>
      <button id="skip">${FIGHT ? "結末まで飛ばす" : "全部表示"}</button>
      <label class="muted"><input type="checkbox" id="fast"> 速く</label>
    </div>
    <div id="fight-actions" class="fight-actions"></div>
    <div id="battle-summary"></div>
    <div class="replay-grid">
      <div class="log" id="log"></div>
      <div class="chart-panel">
        <h3>戦況図<small class="muted">（上=${esc(d.mine_name)}優勢）</small></h3>
        <svg class="eval" id="chart" viewBox="0 0 340 180"></svg>
        <div class="notes" id="notes"></div>
        <div class="report" id="report"></div>
        <div id="detail"></div>
      </div>
    </div>`;
  let gi = 0;
  let timer = null;
  let sideMap = null;
  let mineNames = [], foeNames = [], dupNames = new Set(), lineSides = [];
  const boot = () => loadGame(d.games[gi]);
  $$(".game-tabs button").forEach((b) => b.onclick = () => {
    gi = +b.dataset.i;
    $$(".game-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    boot();
  });
  boot();

  function revealResult() {
    if (!FIGHT || FIGHT.shown) return;
    FIGHT.shown = true;
    sessionStorage.removeItem("fight:" + id);
    document.body.classList.remove("suspense");   // 見立てと軍功帳を開く
    const r = FIGHT.result;
    const to = (href) => () => { location.href = href; };
    if (FIGHT.kind === "ladder") {
      // ラダーの判。次の戦・編成直しは戦記の動線なので出さない
      drawFightBar(r, null);
      showBattleResult(FIGHT.label, r, {
        hideReplay: true, closeLabel: "対戦へ戻る", close: to("/"),
        review: () => {
          const bar = $("#fight-actions");
          if (bar) bar.scrollIntoView({ block: "start", behavior: "smooth" });
        },
      });
      return;
    }
    if (FIGHT.kind === "council") {
      drawFightBar(r, null);
      showBattleResult(FIGHT.label, r, {
        hideReplay: true, closeLabel: "軍議演習へ戻る", close: to("/council"),
        review: () => {
          const bar = $("#fight-actions");
          if (bar) bar.scrollIntoView({ block: "start", behavior: "smooth" });
        },
      });
      return;
    }
    const next = FIGHT.next === null ? null : to("/senki?i=" + FIGHT.next);
    // 判の窓を閉じても行き先が消えないよう、同じ動線を画面にも残す。
    // 実況・戦況図・軍師の見立て・軍功帳は、判のあとが**読みどころ**なので、
    // 「戦記へ戻る」しか無いと読み返せずに流れてしまっていた。
    drawFightBar(r, next);
    showBattleResult(FIGHT.label, r, {
      hideReplay: true,
      closeLabel: "戦記へ戻る",
      close: to("/senki"),
      next: next,
      retry: to("/senki?i=" + FIGHT.i),
      review: () => {
        const bar = $("#fight-actions");
        if (bar) bar.scrollIntoView({ block: "start", behavior: "smooth" });
      },
    });
  }

  function drawFightBar(r, next) {
    const el = $("#fight-actions");
    if (!el) return;
    const cls = r.win === "勝ち" ? "win" : (r.win === "負け" ? "lose" : "draw");
    el.className = "fight-actions " + cls;
    if (FIGHT.kind === "ladder") {
      el.innerHTML = `
        <span class="fa-verdict">${esc(r.win)}</span>
        <span class="muted">実況・戦況図・軍功帳をこのまま読み返せる</span>
        <button class="primary" id="fa-again">もう一度出陣</button>
        <a class="btn ghost" href="/council?source=${d.battle_id}">この相手を軍議演習</a>
        <a class="btn ghost" href="/">対戦へ戻る</a>`;
      $("#fa-again").onclick = () => doAttack(FIGHT.label);
      return;
    }
    if (FIGHT.kind === "council") {
      el.innerHTML = `
        <span class="fa-verdict">${esc(r.win)}</span>
        <span class="muted">敵の魚拓は固定。編成を変えれば同じ条件で研究できる</span>
        <button class="primary" id="fa-council-again">同じ魚拓でもう一度</button>
        <a class="btn ghost" href="/deck">編成を見直す</a>
        <a class="btn ghost" href="/council?source=${FIGHT.source_id}">軍議演習へ戻る</a>`;
      $("#fa-council-again").onclick = () =>
        doCouncil(FIGHT.source_id, FIGHT.board, FIGHT.foe);
      return;
    }
    el.innerHTML = `
      ${r.first_defeat ? `<div class="fa-guide">敗因がリプレイに記されています。まず「軍師の見立て」を読み、武将か陣形を一つ変えてみましょう。</div>` : ""}
      <span class="fa-verdict">${esc(r.win)}</span>
      <span class="muted">実況・戦況図・軍師の見立て・軍功帳をこのまま読み返せる</span>
      ${next ? `<button class="primary" id="fa-next">次の戦へ ▶</button>` : ""}
      <button id="fa-retry">${r.win === "勝ち" ? "編成を見直す" : "編成を直して再挑戦"}</button>
      <a class="btn ghost" href="/senki">戦記へ戻る</a>`;
    if (next) $("#fa-next").onclick = next;
    $("#fa-retry").onclick = () => { location.href = "/senki?i=" + FIGHT.i; };
  }

  function loadGame(g) {
    clearInterval(timer);
    if (g.mine_board) mountFormationBoard($("#replay-mine-board"), {
      ...g.mine_board, interactive: false, onSlotsChange: () => {},
    });
    if (g.foe_board) mountFormationBoard($("#replay-foe-board"), {
      ...g.foe_board, interactive: false, onSlotsChange: () => {},
    });
    mineNames = g.mine_names || [];
    foeNames = g.foe_names || [];
    sideMap = [...(g.foe_names || []).map((n) => [n, "foe-name"]),
               ...(g.mine_names || []).map((n) => [n, "mine-name"])];
    dupNames = new Set(mineNames.filter((n) => foeNames.includes(n)));
    lineSides = g.line_sides || [];
    const log = $("#log");
    log.innerHTML = g.lines.map((ln, i) => fmtLine(ln, i)).join("");
    $("#battle-summary").innerHTML = replayOutcome(g, d, isParticipant);
    drawChart(g, -1);
    drawNotes(g);
    drawReport(g);
    drawDetail(g);
    startPlayback(g);
  }

  function lineTime(ln) {
    const m = ln.match(/【(\d+):(\d+)】/);
    return m ? (+m[1] - 8) * 60 + (+m[2]) : null;
  }

  function markNames(html_, lineSide) {
    if (!sideMap) return html_;
    for (const [name, side] of sideMap) {
      // 両軍にいる武将は名前だけでは色を決められない。**行の主体**で塗る
      // （分からない行では塗らない — 嘘の色を置くより無色のほうがまし）。
      const cls = dupNames.has(name)
        ? (lineSide === "mine" ? "mine-name"
           : lineSide === "foe" ? "foe-name" : null)
        : side;
      if (!cls) continue;
      html_ = html_.split(esc(name)).join(
        `<span class="${cls}">${esc(name)}</span>`);
    }
    return html_;
  }

  function fmtLine(ln, idx) {
    let cls = "line", body = esc(ln);
    if (/^━━/.test(ln)) cls += " band";
    else if (/「.+」$/.test(ln.trim()) && !ln.includes("【")) cls += " quote";
    if (ln.includes("◇戦況")) cls += " check";
    // 自軍・敵軍の札は**盤面が決めたもの**をそのまま使う（§7.92）。
    // 以前は文章から武将名を拾って当てていたが、同じ武将が両軍にいると
    // 必ず取り違えた（両軍の曹仁〔堅守〕の行が2本とも「自軍」になった）。
    // 軍名の照合も「曹軍／孫軍」決め打ちで、実際の勢力名（蜀軍・呉軍）とは
    // 噛み合っていなかった。語りを読んで当てるのはやめる。
    const mark = lineSides[idx];
    let side = "system-event", sideText = "戦況";
    if (mark === "mine") { side = "mine-event"; sideText = mineSide; }
    else if (mark === "foe") { side = "foe-event"; sideText = foeSide; }
    cls += " " + side;
    body = body.replace(/【([^】:]+)】/g, (m0, x) =>
      /^\d+$/.test(x) ? m0 : `【<span class="skillname">${x}</span>】`);
    body = body.replace(/^(◆)/, '<span class="art">◆</span>');
    body = body.replace(/【(\d+:\d+)】/, '<span class="t">$1</span>');
    body = markNames(body, mark);
    return `<div class="${cls}" data-t="${lineTime(ln) ?? ""}"><span class="side-mark">${sideText}</span>${body}</div>`;
  }

  function startPlayback(g) {
    const lines = $$("#log .line");
    let i = 0;
    const step = () => {
      if (i >= lines.length) { clearInterval(timer); return; }
      const el = lines[i++];
      el.classList.add("show");
      const box = $("#log");
      box.scrollTop = el.offsetTop - box.clientHeight + el.offsetHeight + 20;
      const t = el.dataset.t;
      if (t !== "") drawChart(g, +t);
      if (i === lines.length) { drawChart(g, Infinity); revealResult(); }
    };
    const start = () => {
      clearInterval(timer);
      timer = setInterval(step, $("#fast").checked ? 260 : 650);
    };
    const showAll = () => {
      clearInterval(timer);
      lines.forEach((el) => el.classList.add("show"));
      i = lines.length;
      drawChart(g, Infinity);
      revealResult();
    };
    // ▶再生は頭から流し直す（全文表示の後でも実況として観られるように）
    $("#play").onclick = () => {
      clearInterval(timer);
      lines.forEach((el) => el.classList.remove("show"));
      i = 0;
      drawChart(g, -1);
      start();
    };
    $("#skip").onclick = showAll;
    // 出陣直後（見届け）だけ自動で流す。**戦歴からの読み返しは全文を即出す**
    // — 1行ずつの滴りは臨場感のための演出で、天下3戦ぶんを読み返す時には
    // ただ待たされるだけだった（テストプレイの指摘・§7.81）。
    if (FIGHT) start(); else showAll();
  }

  function drawChart(g, upto) {
    const svg = $("#chart");
    const W = 340, H = 180, mid = H / 2, R = 0.35;   // ±35%
    const xs = g.series.map((p) => p[0]);
    const xmax = Math.max(60, xs[xs.length - 1] || 60);
    const X = (t) => 8 + (t / xmax) * (W - 16);
    const Y = (d) => mid - Math.max(-R, Math.min(R, d)) / R * (mid - 10);
    let up = "", down = "";
    const pts = g.series.filter((p) => p[0] <= upto);
    const path = pts.map((p, i) => (i ? "L" : "M") + X(p[0]).toFixed(1) + " " + Y(p[1]).toFixed(1)).join(" ");
    const hours = [];
    for (let t = 0; t <= xmax; t += 120) hours.push(t);
    svg.innerHTML = `
      <line x1="0" y1="${mid}" x2="${W}" y2="${mid}" stroke="#4a4033" stroke-dasharray="3 4"/>
      ${hours.map((t) => `<text x="${X(t)}" y="${H - 4}" fill="#6e6250" font-size="9"
          text-anchor="middle">${8 + t / 60}時</text>`).join("")}
      <text x="4" y="14" fill="#6e6250" font-size="9">+35%</text>
      <text x="4" y="${H - 14}" fill="#6e6250" font-size="9">-35%</text>
      ${path ? `<path d="${path}" fill="none" stroke="#c9a24b" stroke-width="1.8"/>` : ""}
      ${pts.length && upto !== Infinity ? (() => { const p = pts[pts.length - 1];
        return `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="3.4" fill="#c8442a"/>`; })() : ""}
    `;
  }

  function drawNotes(g) {
    const box = $("#notes");
    if (!g.notes || !g.notes.length) { box.innerHTML = ""; return; }
    box.innerHTML = '<div class="side-label">─ この戦いの要点 ─</div>' +
      g.notes.map((n) => `<div class="note-line">${markNames(esc(n))
        .replace(/【(\d+:\d+)】/g, '<span class="t">$1</span>')}</div>`).join("");
  }

  function drawReport(g) {
    const maxD = Math.max(1, ...g.mine.map((u) => u.dealt), ...g.foe.map((u) => u.dealt));
    const side = (label, us) => `<div class="side-label">${label}</div>` +
      us.map((u) => {
        const hp = u.men0 ? u.men / u.men0 : 0;
        const sk = u.skill_dealt || 0;
        // 見えにくい効き（§7.88）: 軽減・反射・同士討ち。**出た時だけ**添える
        const k = (v) => (v / 1000).toFixed(1) + "千";
        const marks = [
          (u.cut || 0) >= 300 ? `<span class="fx cut" title="兵法防御・通常攻撃防御で減らした被害">軽減 ${k(u.cut)}</span>` : "",
          (u.refl || 0) >= 300 ? `<span class="fx refl" title="兵法反射で撃ち手へ返した被害">反射 ${k(u.refl)}</span>` : "",
          (u.ff || 0) >= 300 ? `<span class="fx ff" title="混乱で味方へ回してしまった被害">同士討ち ${k(u.ff)}</span>` : "",
          (u.heal || 0) >= 300 ? `<span class="fx heal" title="味方へ入れた回復の総量">癒し ${k(u.heal)}</span>` : "",
          (u.lost || 0) >= 300 ? `<span class="fx lost" title="弱体を受けて出せなかった火力">封じられ ${k(u.lost)}</span>` : "",
        ].join("");
        return `<div class="unit-row ${hp <= 0.005 ? "dead" : ""}">
          <span class="uname">${esc(u.name)} ${icoTyp(u.typ)}</span>
          <span class="bars">
            <span class="bar dmg"><i class="skillpart" style="width:${sk / maxD * 100}%"></i><i style="width:${(u.dealt - sk) / maxD * 100}%"></i></span>
            <span class="bar hp"><i style="width:${hp * 100}%"></i></span>
            ${marks ? `<span class="fx-row">${marks}</span>` : ""}
          </span>
          <span class="val">与${(u.dealt / 1000).toFixed(1)}千<small>（兵法${(sk / 1000).toFixed(1)}）</small></span>
          <span class="val">${hp <= 0.005 ? "壊滅" : "残" + Math.round(hp * 100) + "%"}${
            u.wiped ? ` <span class="wiped">・${u.wiped}壊</span>` : ""}</span>
        </div>`;
      }).join("");
    $("#report").innerHTML = '<div class="side-label">─ 軍功帳（朱=兵法・橙=通常／軽減・反射・同士討ちは出た時だけ） ─</div>' +
      side("自軍（" + esc(d.mine_name) + "）", g.mine) + side("敵軍（" + esc(d.foe_name) + "）", g.foe);
  }

  /* ── 合戦詳録（§7.94）: 軍師の見立て → 軍功帳 → 詳録 の三段目。
     読み物の下に畳んで置く。開かない人の画面は1ミリも変えない。 ── */
  function drawDetail(g) {
    const box = $("#detail");
    if (!g.mine || !g.mine.length) { box.innerHTML = ""; return; }
    const k = (v) => (v / 1000).toFixed(1);
    const table = (label, us) => `
      <div class="side-label">${label}</div>
      <div class="detail-scroll"><table class="detail-table num">
        <thead><tr><th>武将</th><th>与ダメ</th><th>うち兵法</th><th>被ダメ</th>
          <th>軽減</th><th>癒し</th><th>発動</th><th>阻害</th>
          <th title="・壊＝真に壊滅した時刻（残0.5%割れ）。表示のみ">残存</th></tr></thead>
        <tbody>${us.map((u) => `<tr class="${u.men / u.men0 <= 0.005 ? "dead" : ""}">
          <td class="uname">${esc(u.name)}</td>
          <td>${k(u.dealt)}千</td><td>${k(u.skill_dealt)}千</td>
          <td>${k(u.taken)}千</td>
          <td>${u.cut >= 50 ? k(u.cut) + "千" : "—"}</td>
          <td>${u.heal >= 50 ? k(u.heal) + "千" : "—"}</td>
          <td>${(u.fire_times || []).length
            ? `<span title="発動: ${u.fire_times.map(esc).join("、")}">初回${esc(u.fire_times[0])}／計${u.fires}回</span>`
            : (u.fires ? u.fires + "回" : "—")}</td>
          <td>${u.stun ? u.stun + "分" : "—"}</td>
          <td>${u.men / u.men0 <= 0.005 ? "壊滅" : "残" + Math.round(100 * u.men / u.men0) + "%"}${
            u.wiped ? `<span class="wiped">・${u.wiped}壊</span>` : ""}</td>
        </tr>`).join("")}</tbody>
      </table></div>`;
    // 矛先: 誰が誰を削ったか（与えた量の大きい順・上位3）
    const spears = (us) => us.filter((u) => (u.targets || []).length)
      .map((u) => `<div class="detail-line"><b>${esc(u.name)}</b> → ${
        u.targets.slice(0, 3).map(([n2, v]) => `${esc(n2)} ${k(v)}千`).join("・")}${
        u.targets.length > 3 ? "・…" : ""}</div>`).join("");
    // 機動と抑制: 出た札だけ名指しする（空騒ぎの行を作らない）
    const moves = [...g.mine, ...g.foe].flatMap((u) => {
      const out = [];
      if (u.detour !== null && u.detour !== undefined)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　敵陣の外へ迂回（道のりの${u.detour}%まで）</div>`);
      if ((u.sup || 0) >= 300)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　接敵抑制で矢 ${k(u.sup)}千ぶんを失う</div>`);
      if ((u.ff || 0) >= 300)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　混乱し、味方へ ${k(u.ff)}千の流れ矢</div>`);
      if ((u.refl || 0) >= 300)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　兵法を跳ね返し ${k(u.refl)}千</div>`);
      if ((u.lost || 0) >= 300)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　弱体で ${k(u.lost)}千ぶんの火力を封じられる</div>`);
      /* 構えの帳簿（§7.126）: 打ち消した数と兵法名・兵法防御の軽減・空振り */
      if ((u.null_blocked || 0) > 0)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　構えで敵の兵法を ${u.null_blocked}回 打ち消す（${(u.null_names || []).map(esc).join("・")}）</div>`);
      if ((u.scut_saved || 0) >= 300)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　兵法防御で ${k(u.scut_saved)}千を軽減</div>`);
      if ((u.guard_idle || 0) > 0)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　構えを${u.guard_casts}回張ったが、${u.guard_idle}回は敵の兵法が来なかった</div>`);
      /* 余勢の帳簿（§7.76 後記）: 討ち取りの余りが隣へ抜けた分 */
      if ((u.spill_n || 0) > 0)
        out.push(`<div class="detail-line"><b>${esc(u.name)}</b>　余勢 ${k(u.spill_dealt)}千（${u.spill_n}回・超過 ${k(u.spill_over)}千）</div>`);
      return out;
    }).join("");
    box.innerHTML = `<details class="battle-detail">
      <summary>合戦詳録を開く<small class="muted">　数字で振り返る（矛先・被害・機動）</small></summary>
      ${table("自軍（" + esc(d.mine_name) + "）", g.mine)}
      ${table("敵軍（" + esc(d.foe_name) + "）", g.foe)}
      <div class="side-label">─ 矛先（誰が誰を削ったか・上位3） ─</div>
      ${spears(g.mine)}${spears(g.foe)}
      ${moves ? `<div class="side-label">─ 機動と乱れ ─</div>${moves}` : ""}
    </details>`;
  }
}

/* Nodeの軽量テストから純粋関数とコンポーネント契約を検証できるようにする。 */
if (typeof module !== "undefined" && module.exports) {
  module.exports = { FORMATIONS, normalizeSlots, swapOrMove, rankPosition, FormationBoard,
    localDay, formatLocalDay, formatTruceDayLabel, formatHourRanges,
    truceDayCanMove, initialTruceDayIndex, truceHoursFor, truceSummaryText,
    tenkaDayStats };
}

/* ── 起動 ──────────────────────── */
if (typeof document !== "undefined") (async function boot() {
  document.body.classList.toggle("debug", DEBUG);
  const state = await api("/api/state");
  shell(state);
  if (state.stale_server) {
    $("#app").insertAdjacentHTML("beforebegin", `
      <div class="stale-banner">⚠ ゲームのファイルが更新されているのに、
      サーバが古いまま動いている。<b>python -m sim.web を止めて起動し直し、
      ページを再読み込みして</b>（新旧混在は表示が壊れる）。</div>`);
  }
  const view = document.body.dataset.view;
  if (!state.me && view !== "replay") return renderLogin(state);
  if (view === "home") return viewHome(state);
  if (view === "senki") {
    const qi = new URLSearchParams(location.search).get("i");
    return qi === null ? viewSenki(state) : viewSenkiPrep(+qi);
  }
  if (view === "deck") return viewDeck(state);
  if (view === "council") return viewCouncil(state);
  if (view === "replays") return viewReplays(state);
  if (view === "replay") return viewReplay(state);
})();
