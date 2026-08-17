/* 三国志 卓上戦記 — 画面。サーバの /api/* を叩くだけで、規則の正本は持たない
   （検証の正は match.validate。ここでの表示はあくまで手元の目安）。 */
"use strict";

const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => [...(el || document).querySelectorAll(sel)];
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, body) {
  const opt = body === undefined ? {} :
    { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

/* ── 共通シェル ─────────────────────── */
function shell(state) {
  const view = document.body.dataset.view;
  const nav = [["/", "順位表"], ["/deck", "編成"], ["/replays", "リプレイ"]]
    .map(([p, t]) => `<a href="${p}" class="${location.pathname === p ? "on" : ""}">${t}</a>`)
    .join("");
  const chip = state.me
    ? `<span class="player-chip">遊んでいるのは <b>${esc(state.me.name)}</b>
       <a href="#" id="switch">替える</a></span>`
    : "";
  $("#app").insertAdjacentHTML("beforebegin", `
    <header>
      <h1><span class="tsuki">三国志</span>　卓上戦記</h1>
      <nav>${nav}</nav>${chip}
    </header>`);
  const sw = $("#switch");
  if (sw) sw.onclick = (e) => { e.preventDefault(); renderLogin(state, true); };
}

/* ── ログイン ─────────────────────── */
function renderLogin(state, force) {
  const app = $("#app");
  const opts = state.humans.map((h) =>
    `<option value="${h.id}">${esc(h.name)}</option>`).join("");
  app.innerHTML = `
    <div class="login-panel panel fade-in">
      <h2>名乗りを上げよ</h2>
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
async function viewHome(state) {
  const app = $("#app");
  const boards = state.boards.map((b) => {
    const rows = b.table.slice(0, 10).map((r) => `
      <tr class="${r.me ? "me" : ""} ${r.rank === 1 ? "top1" : ""}">
        <td class="rank num">${r.rank === 1 ? "①" : r.rank + "位"}</td>
        <td>${esc(r.name)}${r.kind === "dummy" ? '<span class="dummy">ダミー</span>' : ""}</td>
        <td class="rating num">${Math.round(r.rating)}点</td>
        <td class="games num">${r.games}戦</td>
      </tr>`).join("");
    return `<div class="panel">
      <h2>${esc(b.name)}<span class="sub">${b.round}巡</span></h2>
      <table class="std">${rows || "<tr><td class='muted'>まだ戦いがない</td></tr>"}</table>
    </div>`;
  }).join("");
  app.innerHTML = `
    <div class="cta">
      <button class="primary" id="fight" ${state.entry_ok ? "" : "disabled"}>出　陣</button>
      <span class="hint">${state.entry_ok
        ? "4つの順位表で1巡戦う（十数秒）"
        : '出陣には3部隊の登録が要る → <a href="/deck">編成へ</a>'}</span>
    </div>
    <div class="boards fade-in">${boards}</div>`;
  const btn = $("#fight");
  if (btn) btn.onclick = async () => {
    document.body.insertAdjacentHTML("beforeend", `
      <div id="overlay"><div class="box">
        <div class="march">出　陣</div>
        <p class="muted">軍を進めている……</p>
      </div></div>`);
    try {
      const r = await api("/api/round", {});
      showResults(r.results);
    } catch (e) {
      $("#overlay").remove(); alert(e.message);
    }
  };
}

function showResults(results) {
  const rows = results.map((r) => {
    const cls = r.verdict === "勝ち" ? "win" : (r.verdict === "負け" ? "lose" : "draw");
    const d = Math.round(r.delta);
    return `<div class="result-row fade-in">
      <span class="board">${esc(r.board)}</span>
      <span class="verdict ${cls}">${r.verdict}</span>
      <span class="muted">対 ${esc(r.foe)}${r.score ? "　" + r.score : ""}</span>
      <span class="delta num ${d >= 0 ? "up" : "down"}">${d >= 0 ? "+" : ""}${d}点</span>
      <span class="muted num">${Math.round(r.rating)}点・${r.rank}位</span>
      ${r.match_id ? `<a href="/replay?id=${r.match_id}">観る</a>` : ""}
    </div>`;
  }).join("");
  $("#overlay").innerHTML = `<div class="box" style="min-width:560px">
    <h2 class="serif" style="letter-spacing:.3em">戦　果</h2>
    <div class="results">${rows}</div>
    <button class="primary" onclick="location.href='/'">順位表へ</button>
  </div>`;
}

/* ── 編成 ──────────────────────── */
const FORMS = { "鶴翼": 4, "魚鱗": 3, "雁行": 2 };
let D = null;          // /api/deckdata
let cur = null;        // {reg, form, cards:[name,...]}

async function viewDeck(state) {
  D = await api("/api/deckdata");
  const reg = D.regs[0].name;
  const saved = D.decks[reg] || { form: "魚鱗", cards: [] };
  cur = { reg, form: saved.form, cards: [...saved.cards] };
  $("#app").innerHTML = `
    <div class="reg-tabs" id="regtabs"></div>
    <div class="deck-layout fade-in">
      <div>
        <div class="roster-tools">
          <div class="filter-tabs" id="typetabs"></div>
          <input id="search" placeholder="名で探す">
        </div>
        <div class="cards" id="roster"></div>
      </div>
      <div class="panel">
        <div class="form-tabs" id="formtabs"></div>
        <div class="cost-meter" id="meter"><div class="fill"></div><div class="label"></div></div>
        <div class="slots" id="slots"></div>
        <div class="deck-actions">
          <button class="primary" id="save">この編成を登録</button>
          <span id="deck-msg"></span>
        </div>
        <div class="entry-state" id="entrystate"></div>
      </div>
    </div>`;
  drawRegTabs(); drawFormTabs(); drawTypeTabs();
  $("#search").oninput = drawRoster;
  $("#save").onclick = saveDeck;
  drawAll();
}

function usedPersons(exceptReg) {
  const used = new Map();   // person -> reg
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
    cur = { reg: b.dataset.reg, form: saved.form, cards: [...saved.cards] };
    drawRegTabs(); drawFormTabs(); drawAll();
  });
}
function drawFormTabs() {
  $("#formtabs").innerHTML = Object.entries(FORMS).map(([f, n]) =>
    `<button class="${cur.form === f ? "on" : ""}" data-f="${f}">
      <b>${f}</b><small>前衛${n}</small></button>`).join("");
  $$("#formtabs button").forEach((b) => b.onclick = () => {
    cur.form = b.dataset.f; drawFormTabs(); drawAll();
  });
}
function drawTypeTabs() {
  const ts = ["すべて", "歩兵", "騎兵", "弓兵"];
  const el = $("#typetabs");
  el.dataset.on = el.dataset.on || "すべて";
  el.innerHTML = ts.map((t) =>
    `<button class="${el.dataset.on === t ? "on" : ""}" data-t="${t}">${t}</button>`).join("");
  $$("#typetabs button").forEach((b) => b.onclick = () => {
    el.dataset.on = b.dataset.t; drawTypeTabs(); drawRoster();
  });
}

function drawAll() { drawRoster(); drawSlots(); drawMeter(); drawEntryState(); }

function drawRoster() {
  const t = $("#typetabs").dataset.on;
  const q = $("#search").value.trim();
  const used = usedPersons(cur.reg);
  const inDeck = new Set(cur.cards);
  const cap = D.regs.find((r) => r.name === cur.reg).cap;
  const list = D.roster
    .filter((c) => (t === "すべて" || c.typ === t))
    .filter((c) => !q || c.name.includes(q) || c.person.includes(q))
    .sort((a, b) => b.cost - a.cost || a.name.localeCompare(b.name, "ja"));
  $("#roster").innerHTML = list.map((c) => {
    const u = used.get(c.person);
    const dup = inDeck.has(c.name) ||
      cur.cards.some((n) => { const x = D.roster.find((r) => r.name === n);
                              return x && x.person === c.person && x.name !== c.name; });
    const off = u || dup;
    return `<div class="card f${c.faction} ${off ? "used" : ""}" data-n="${esc(c.name)}"
                 title="${esc(c.skill)}：${esc(c.skill_desc)}${c.quote ? "\n「" + esc(c.quote) + "」" : ""}">
      ${u ? `<span class="usedby">${esc(u).slice(0, 1)}で使用</span>`
          : (inDeck.has(c.name) ? `<span class="usedby">編成中</span>` : "")}
      <div class="top"><span class="cost">${c.cost}</span>
        <span class="typ">${c.typ.slice(0, 1)}</span></div>
      <div class="name">${esc(c.name)}</div>
      <div class="skill">【${esc(c.skill)}】</div>
    </div>`;
  }).join("");
  $$("#roster .card:not(.used)").forEach((el) => el.onclick = () => {
    if (cur.cards.length >= 6) { flashMsg("6枚まで。どれかを外してから。", true); return; }
    cur.cards.push(el.dataset.n); drawAll();
  });
}

function drawSlots() {
  const nf = FORMS[cur.form];
  const rows = [];
  for (let i = 0; i < 6; i++) {
    const name = cur.cards[i];
    const c = name && D.roster.find((x) => x.name === name);
    const front = i < nf;
    let warn = "";
    if (c) {
      if (front && c.typ === "弓兵") warn = "弓兵は前衛に置けない";
      if (!front && c.typ !== "弓兵") warn = "後衛は弓兵だけ";
    }
    rows.push(`<div class="slot ${front ? "front" : "rear"} ${c ? "" : "empty"}">
      <span class="pos">${front ? "前衛" : "後衛"}${i + 1}</span>
      <span class="who">${c ? `<b>${esc(c.name)}</b> <small>${c.typ.slice(0, 1)}</small>
        ${warn ? `<span class="warn">⚠ ${warn}</span>` : ""}` : "（クリックで加える）"}</span>
      ${c ? `<span class="cost num">${c.cost}点</span>
        <button class="mini" data-i="${i}" data-a="up" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="mini" data-i="${i}" data-a="dn" ${i === cur.cards.length - 1 ? "disabled" : ""}>↓</button>
        <button class="mini" data-i="${i}" data-a="rm">✕</button>` : ""}
    </div>`);
  }
  $("#slots").innerHTML = rows.join("");
  $$("#slots button").forEach((b) => b.onclick = () => {
    const i = +b.dataset.i;
    if (b.dataset.a === "rm") cur.cards.splice(i, 1);
    if (b.dataset.a === "up") [cur.cards[i - 1], cur.cards[i]] = [cur.cards[i], cur.cards[i - 1]];
    if (b.dataset.a === "dn") [cur.cards[i + 1], cur.cards[i]] = [cur.cards[i], cur.cards[i + 1]];
    drawAll();
  });
}

function deckCost() {
  return cur.cards.reduce((s, n) => {
    const c = D.roster.find((x) => x.name === n); return s + (c ? c.cost : 0);
  }, 0);
}

function drawMeter() {
  const cap = D.regs.find((r) => r.name === cur.reg).cap;
  const cost = deckCost();
  const m = $("#meter");
  m.classList.toggle("over", cost > cap);
  m.querySelector(".fill").style.width = Math.min(100, cost / cap * 100) + "%";
  m.querySelector(".label").textContent =
    `${cost} ／ ${cap}点` + (cost > cap ? "　超過！" : (cost < cap ? `　余り${cap - cost}` : "　ぴったり"));
}

function drawEntryState() {
  const el = $("#entrystate");
  const missing = D.regs.filter((r) => !(D.decks[r.name] && D.decks[r.name].cards.length));
  el.innerHTML = D.entry_errors.length
    ? "<span class='muted'>登録全体の検証: </span>" +
      D.entry_errors.map((e) => `<div class="warn muted">・${esc(e)}</div>`).join("")
    : "<span style='color:var(--gold)'>3部隊とも出陣できる。</span>";
}

let msgTimer = null;
function flashMsg(text, isErr) {
  const el = $("#deck-msg");
  el.textContent = text; el.className = isErr ? "err" : "ok";
  clearTimeout(msgTimer);
  msgTimer = setTimeout(() => { el.textContent = ""; }, 4000);
}

async function saveDeck() {
  const r = await api("/api/deck", { reg: cur.reg, form: cur.form, cards: cur.cards });
  if (r.ok) {
    D.decks[cur.reg] = { form: cur.form, cards: [...cur.cards] };
    D.entry_errors = r.entry_errors;
    flashMsg("登録した。"); drawEntryState();
  } else {
    flashMsg(r.errors.join("／"), true);
  }
}

/* ── リプレイ一覧 ───────────────────── */
async function viewReplays(state) {
  const d = await api("/api/replays");
  $("#app").innerHTML = d.boards.map((b) => `
    <div class="panel fade-in" style="margin-bottom:16px">
      <h2>${esc(b.name)}</h2>
      <table class="std">${b.matches.map((m) => `
        <tr><td class="num">第${m.round + 1}巡</td>
            <td>${esc(m.a)} <small>対</small> ${esc(m.b)}</td>
            <td><a href="/replay?id=${m.id}">観る</a></td></tr>`).join("")
        || "<tr><td class='muted'>記録なし</td></tr>"}</table>
    </div>`).join("");
}

/* ── リプレイ再生 ───────────────────── */
async function viewReplay(state) {
  const id = new URLSearchParams(location.search).get("id");
  const d = await api("/api/replay?id=" + id);
  const tabs = d.games.length > 1
    ? `<div class="game-tabs">${d.games.map((g, i) =>
        `<button data-i="${i}" class="${i === 0 ? "on" : ""}">
          第${i + 1}戦 ${esc(g.label)} <b>${g.verdict}</b></button>`).join("")}</div>`
    : "";
  $("#app").innerHTML = `
    <div class="replay-head">
      <h2>${esc(d.title)}</h2>
      <span class="muted">${esc(d.board)}・第${d.round + 1}巡</span>
    </div>
    ${tabs}
    <div class="replay-controls">
      <button id="play" class="primary">▶ 再生</button>
      <button id="skip">全部表示</button>
      <label class="muted"><input type="checkbox" id="fast"> 速く</label>
    </div>
    <div class="replay-grid">
      <div class="log" id="log"></div>
      <div class="chart-panel">
        <h3>形勢<small class="muted">（上=${esc(d.mine_name)}優勢）</small></h3>
        <svg class="eval" id="chart" viewBox="0 0 340 180"></svg>
        <div class="report" id="report"></div>
      </div>
    </div>`;
  let gi = 0;
  let timer = null;
  const boot = () => loadGame(d.games[gi]);
  $$(".game-tabs button").forEach((b) => b.onclick = () => {
    gi = +b.dataset.i;
    $$(".game-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    boot();
  });
  boot();

  function loadGame(g) {
    clearInterval(timer);
    const log = $("#log");
    log.innerHTML = g.lines.map((ln) => fmtLine(ln)).join("");
    drawChart(g, -1);
    drawReport(g);
    startPlayback(g);
  }

  function lineTime(ln) {
    const m = ln.match(/【(\d+):(\d+)】/);
    return m ? (+m[1] - 8) * 60 + (+m[2]) : null;
  }

  function fmtLine(ln) {
    let cls = "line", body = esc(ln);
    if (/^━━/.test(ln)) cls += " band";
    else if (/「.+」$/.test(ln.trim()) && !ln.includes("【")) cls += " quote";
    if (ln.includes("◇戦況")) cls += " check";
    body = body.replace(/【([^】:]+)】/g, (m0, x) =>
      /^\d+$/.test(x) ? m0 : `【<span class="skillname">${x}</span>】`);
    body = body.replace(/^(◆)/, '<span class="art">◆</span>');
    body = body.replace(/【(\d+:\d+)】/, '<span class="t">$1</span>');
    return `<div class="${cls}" data-t="${lineTime(ln) ?? ""}">${body}</div>`;
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
      if (i === lines.length) drawChart(g, Infinity);
    };
    const start = () => {
      clearInterval(timer);
      timer = setInterval(step, $("#fast").checked ? 260 : 650);
    };
    $("#play").onclick = start;
    $("#skip").onclick = () => {
      clearInterval(timer);
      lines.forEach((el) => el.classList.add("show"));
      drawChart(g, Infinity);
    };
    // 自動で流し始める
    start();
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

  function drawReport(g) {
    const maxD = Math.max(1, ...g.mine.map((u) => u.dealt), ...g.foe.map((u) => u.dealt));
    const side = (label, us) => `<div class="side-label">${label}</div>` +
      us.map((u) => {
        const hp = u.men0 ? u.men / u.men0 : 0;
        return `<div class="unit-row ${hp <= 0.005 ? "dead" : ""}">
          <span class="uname">${esc(u.name)}<small>（${u.typ.slice(0, 1)}）</small></span>
          <span class="bars">
            <span class="bar dmg"><i style="width:${u.dealt / maxD * 100}%"></i></span>
            <span class="bar hp"><i style="width:${hp * 100}%"></i></span>
          </span>
          <span class="val">与${(u.dealt / 1000).toFixed(1)}千</span>
          <span class="val">${hp <= 0.005 ? "壊滅" : "残" + Math.round(hp * 100) + "%"}</span>
        </div>`;
      }).join("");
    $("#report").innerHTML =
      side("自軍（" + esc(d.mine_name) + "）", g.mine) + side("敵軍（" + esc(d.foe_name) + "）", g.foe);
  }
}

/* ── 起動 ──────────────────────── */
(async function boot() {
  const state = await api("/api/state");
  shell(state);
  const view = document.body.dataset.view;
  if (!state.me && view !== "replay") return renderLogin(state);
  if (view === "home") return viewHome(state);
  if (view === "deck") return viewDeck(state);
  if (view === "replays") return viewReplays(state);
  if (view === "replay") return viewReplay(state);
})();
