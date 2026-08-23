/* 三国志 卓上戦記 — 画面。サーバの /api/* を叩くだけで、規則の正本は持たない
   （検証の正は match.validate。ここでの表示はあくまで手元の目安）。 */
"use strict";

const DEBUG = new URLSearchParams(location.search).get("debug") === "1";

const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => [...(el || document).querySelectorAll(sel)];
const esc = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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

/* ── 共通シェル ─────────────────────── */
function shell(state) {
  const view = document.body.dataset.view;
  const nav = [["/", "対戦"], ["/senki", "戦記"], ["/deck", "編成"],
               ["/replays", "戦歴"]]
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
  if (sw) sw.onclick = (e) => { e.preventDefault(); renderLogin(state, true); };
}

/* ── ログイン ─────────────────────── */
function renderLogin(state, force) {
  const app = $("#app");
  const opts = state.humans.map((h) =>
    `<option value="${h.id}">${esc(h.name)}</option>`).join("");
  app.innerHTML = `
    <div class="login-hero fade-in">
      ${logoHTML(true)}
      <div class="logo-sub">六将軍略オートバトル</div>
      <div class="logo-tag">知略を布き、乱世を制せ</div>
    </div>
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

async function viewHome(state) {
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
  const tenka = `
    <div class="tenka-chip fade-in">
      <b>天下</b>（三つの戦場一括のBO3）次回 ${fmtClock(t.at)}（${Math.ceil(t.in_sec / 60)}分後）
      ${state.me ? (t.auto ? '<span class="ok">自動参加</span>'
                           : '<span class="warn">3デッキ揃えると自動参加</span>') : ""}
      ${t.foe ? `　対戦相手: <b>${esc(t.foe)}</b> <span class="form-tag">${esc(t.forms || "?")}</span>
        ${t.battle_id ? `<a href="/replay?id=${t.battle_id}">前の戦いを観る</a>` : ""}` : ""}
      ${state.me ? '<button class="mini ghost dev-only" id="tenka-now" title="試験用">今すぐ開催</button>' : ""}
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
    </div>
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
  $$(".onsho-choice").forEach((b) => b.onclick = async () => {
    const r = await api("/api/onsho_pick", { key: b.dataset.k });
    if (r.ok) location.reload(); else alert((r.errors || ["受け取れなかった"])[0]);
  });
  $$("button.attack").forEach((b) => b.onclick = () => doAttack(b.dataset.reg));
  const rf = $("#refill");
  if (rf) rf.onclick = async () => { await api("/api/dev_heifu", {}); location.reload(); };
  const tn = $("#tenka-now");
  if (tn) tn.onclick = async () => { await api("/api/dev_tenka", {}); location.reload(); };
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
          cards: [...start.cards] };
  const foeSummary = armySummary(p.enemy.cards, p.enemy.form, p.enemy.cost, null, "foe");
  const foe = p.enemy.cards.map((c) => `
    <div class="foe-card f${c.faction}">
      <img src="/portrait/${encodeURIComponent(c.person)}" alt="">
      <div class="fc-body">
        <div class="fc-head"><b>${esc(c.name)}</b>
          <span class="cost num">${c.cost}点</span></div>
        <div class="muted num">${c.rear ? "後衛" : "前衛"}・<span class="unit-type ${TYPE_CLS[c.typ]}">${icoTyp(c.typ, c.spear)}${esc(c.typ)}${c.spear ? "・槍" : ""}</span>・${esc(c.role)}
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
        ${foe}
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
        <div class="slots" id="slots"></div>
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
    <div id="cardinfo" class="cardinfo muted">カードに触れると詳細が出る。</div>
    <div class="cards" id="roster"></div>`;
  drawFormTabs(); drawTypeTabs();
  $("#search").oninput = drawRoster;
  $("#prep-back").onclick = () => { location.href = "/senki"; };
  $("#prep-again").onclick = async () => {
    const r = await api("/api/senki_prep?i=" + i + "&n=" + Date.now());
    PREP.suggest = r.suggest;
    cur.cards = [...r.suggest.cards];
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
    cur.cards = [...src.cards];
    cur.form = src.form || cur.form;
    drawFormTabs(); drawPrep();
  };
  $("#prep-go").onclick = () =>
    doSenkiFight(i, PREP.title, { cards: cur.cards, form: cur.form });
  drawPrep();
}

function drawPrep(msg) {
  for (const f of [drawRoster, drawSlots, drawMeter, drawArmySummary, drawDraft]) {
    try { f(); } catch (e) { console.error("drawPrep:", f.name, e); }
  }
  const over = deckCost() > PREP.cap + 1e-9;
  const n = cur.cards.length;
  // 配置の不備（弓でない後衛など）は枠に⚠が出る。出陣はそれごと止める
  const bad = $$("#slots .warn").length;
  const go = $("#prep-go");
  go.disabled = over || n !== 6 || bad > 0;
  // 予算を使い切っていて空き枠が埋まらない、という手詰まりは名指しで言う
  // （札が一斉に沈むだけだと「武将を制限されている」ように見えてしまう）
  const rest = PREP.cap - deckCost();
  const rist = D.roster.filter((c) => !cur.cards.includes(c.name))
    .reduce((m, c) => Math.min(m, c.cost), Infinity);
  const stuck = n < 6 && rest < rist;
  const el = $("#deck-msg");
  el.className = (over || bad || stuck) ? "err" : "";
  el.textContent = msg ? msg
    : over ? `上限 ${PREP.cap}点を ${(deckCost() - PREP.cap).toFixed(0)}点 超えている`
    : stuck ? `あと${6 - n}人だが、残り${rest}点では誰も足せない`
              + `（枠の ✕ で誰かを外して組み直す）`
    : (n !== 6 ? `あと${6 - n}人（6人で出陣）`
       : (bad ? "置けない兵がいる（⚠の枠を直す）"
          : "6人そろった。入れ替えるときは枠の ✕ で外す"));
}

/* ── 編成 ──────────────────────── */
const FORMS = { "鶴翼": 4, "魚鱗": 3, "雁行": 2 };
let D = null;          // /api/deckdata
let cur = null;        // {reg, form, cards:[name,...]}
let PREP = null;       // 戦前の間（§7.62）。非nullの間は上限も規則もこちら

let STATE = null;

function formDiagram(form) {
  const nf = FORMS[form] || 0;
  return `<span class="form-diagram" aria-hidden="true">${[0, 1, 2, 3, 4, 5]
    .map((i) => `<i class="${i < nf ? "front" : "rear"}"></i>`).join("")}</span>`;
}

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
    <span class="formation-summary">${formDiagram(form)}${esc(form || "陣形未定")}・前${nf}／後${6 - nf}</span>
    <b class="summary-cost num">${costText}</b>
  </div>`;
}

function currentCards() {
  return (cur.cards || []).map((name) => D.roster.find((c) => c.name === name)).filter(Boolean);
}

async function viewDeck(state) {
  STATE = state;
  PREP = null;
  D = await api("/api/deckdata");
  const reg = D.regs[0].name;
  const saved = D.decks[reg] || { form: "魚鱗", cards: [] };
  cur = { reg, form: saved.form, cards: [...saved.cards] };
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
        <div id="cardinfo" class="cardinfo muted">カードに触れると詳細が出る。</div>
        <div class="cards" id="roster"></div>
      </div>
      <div class="panel mine-panel side-panel">
        <h2 class="side-heading mine-heading">自軍編成<span class="sub">6人を前衛・後衛へ配置</span></h2>
        <div id="scout"></div>
        <div id="mine-summary"></div>
        <div class="section-label">陣形</div>
        <div class="form-tabs" id="formtabs"></div>
        <div class="cost-meter" id="meter"><div class="fill"></div><div class="label"></div></div>
        <div class="slots" id="slots"></div>
        <div class="entry-state" id="entrystate"></div>
        <div class="draft-panel" id="draft"></div>
        <div class="library" id="library"></div>
        <div class="onsho-panel" id="onsho"></div>
      </div>
    </div>
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
          <dd>肉薄されると矢も必殺技も鈍る。射手を守る壁を惜しむな。</dd>
          <dt>槍の使い道</dt>
          <dd>槍持ちの歩兵は後衛にも置ける。回り込む騎馬は、槍が突き止める。</dd>
          <dt>前衛の積み方</dt>
          <dd>高く積め——最も安い札が矢面に立ち、高い札が長く戦う。
            ただし一枚の柱に頼る軍は、柱を失えば崩れる。</dd>
          <dt>細かい得</dt>
          <dd>余った点は開戦の気勢に変わる。本陣を預かる将が崩れれば全軍が
            動揺する。必殺技は消費が軽いほど数を撃ち、重いほど一撃に懸ける。</dd>
        </dl>
      </div>
    </div>`;
  const gd = $("#guide");
  $("#guide-open").onclick = () => { gd.hidden = false; };
  $("#guide-close").onclick = () => { gd.hidden = true; };
  gd.onclick = (e) => { if (e.target === gd) gd.hidden = true; };
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
    && active.cards.join("、") === cur.cards.join("、"));
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
    cur = { reg: b.dataset.reg, form: saved.form, cards: [...saved.cards] };
    drawRegTabs(); drawFormTabs(); drawAll();
  });
}
function drawFormTabs() {
  $("#formtabs").innerHTML = Object.entries(FORMS).map(([f, n]) =>
    `<button class="${cur.form === f ? "on" : ""}" data-f="${f}">
      ${formDiagram(f)}<span><b>${f}</b><small>前${n}・後${6 - n}</small></span></button>`).join("");
  $$("#formtabs button").forEach((b) => b.onclick = () => {
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
                   drawLibrary, drawOnsho, drawSortieBar, drawScout]) {
    try { f(); } catch (e) { console.error("drawAll:", f.name, e); }
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
    ${q("style", "戦い方", ["力押し", "必殺技", "守り", "おまかせ"])}
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
      cur.cards = r.cards;
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
  // BO1は相手が事前に分からない（挑戦ラダー・§7.58）。偵察の窓は天下だけ:
  // 開催1時間前に組合せが出たら、ここで相手と陣形を見せて編成調整を促す。
  const el = $("#scout");
  if (!el || !STATE) return;
  const t = STATE.tenka;
  el.innerHTML = (t && t.foe)
    ? `<div class="next-chip">天下 ${fmtClock(t.at)}開催: 対 <b>${esc(t.foe)}</b>
       <span class="form-tag">${esc(t.forms || "?")}</span>
       ${t.battle_id ? `<a href="/replay?id=${t.battle_id}">前の戦いを観る</a>` : ""}
       <small class="muted">開催まで編成を調整できる</small></div>`
    : "";
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
    return `<div class="lib-row ${isActive ? "active" : ""}">
      <div class="lib-line1">
        <span class="lname">${esc(s.name)}</span>
        ${isActive ? '<span class="active-tag">登録中</span>' : ""}
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
  el.innerHTML = `<div class="side-label">─ デッキ保存庫（${esc(cur.reg)}） ─</div>
    <div class="lib-save">
      <input id="savename" placeholder="この編成に名前を付けて保存" maxlength="24">
      <button class="mini" id="dosave">保存</button>
    </div>
    ${rows || "<p class='muted' style='font-size:12px'>まだ保存が無い。</p>"}`;
  $("#dosave").onclick = async () => {
    const name = $("#savename").value.trim();
    if (!name) { flashMsg("名前を付ける", true); return; }
    const r = await api("/api/savedeck",
      { name, reg: cur.reg, form: cur.form, cards: cur.cards });
    if (!r.ok) { flashMsg(r.errors.join("／"), true); return; }
    D = await api("/api/deckdata"); flashMsg("保存した。"); drawLibrary();
  };
  $$("#library .lib-btns button").forEach((b) => b.onclick = async () => {
    const s = mine.find((x) => x.id === +b.dataset.id);
    if (b.dataset.a === "load") {
      cur.form = s.form; cur.cards = [...s.cards]; drawFormTabs(); drawAll();
      flashMsg(`「${s.name}」を編成台へ。登録するまで次戦には使われない。`);
    }
    if (b.dataset.a === "reg") {
      const r = await api("/api/deck", { reg: cur.reg, form: s.form, cards: s.cards });
      if (!r.ok) { flashMsg(r.errors.join("／"), true); return; }
      D = await api("/api/deckdata");
      cur.form = s.form; cur.cards = [...s.cards]; drawFormTabs();
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
  for (const n of cur.cards) set.add(n);
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

function drawRoster() {
  const q = $("#search").value.trim();
  const used = usedPersons(cur.reg);
  const inDeck = new Set(cur.cards);
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
    const dup = inDeck.has(c.name) ||
      cur.cards.some((n) => { const x = D.roster.find((r) => r.name === n);
                              return x && x.person === c.person && x.name !== c.name; });
    // 戦前の間では、いまの残り予算で買えない札を沈める（詰将棋の可読性）。
    // ただし**枠が埋まっているときは沈めない** — 誰かを外せば買えるので、
    // 値段だけで沈めると「その武将は使えない」という嘘になる。
    const rest = PREP ? PREP.cap - deckCost() : Infinity;
    const pricey = PREP && cur.cards.length < 6
      && !inDeck.has(c.name) && c.cost > rest + 1e-9;
    const off = u || dup;
    return `<div class="card f${c.faction} ${off ? "used" : ""} ${pricey ? "pricey" : ""}"
         data-n="${esc(c.name)}">
      <div class="face"><img src="/portrait/${encodeURIComponent(c.person)}"
        loading="lazy" alt="">
        ${icoCost(c.cost)}
        <span class="typ">${icoTyp(c.typ, c.spear)}</span>
        ${u ? `<span class="usedby">${esc(u).slice(0, 1)}で使用</span>`
            : (inDeck.has(c.name) ? `<span class="usedby">編成中</span>` : "")}
      </div>
      <div class="name">${esc(c.name)}<span class="role">${esc(c.role)}</span></div>
      <div class="stats num">武勇${c.might}　知略${c.wits}</div>
      <div class="stats num">攻勢${c.atk_pm}　守勢${(c.eff_men / 1000).toFixed(1)}千</div>
      <div class="skill">【${esc(c.skill)}】</div>
    </div>`;
  }).join("");
  $$("#roster .card:not(.used)").forEach((el) => el.onclick = () => {
    if (cur.cards.length >= 6) { flashMsg("6枚まで。どれかを外してから。", true); return; }
    cur.cards.push(el.dataset.n); drawAll();
  });
  $$("#roster .card").forEach((el) => {
    el.onmouseenter = () => showCardInfo(el.dataset.n);
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
    <img class="ci-face" src="/portrait/${encodeURIComponent(c.person)}" alt="">
    <div class="ci-body">
    <div class="ci-head">
      ${icoCost(c.cost)}
      <span class="ci-name">${esc(c.name)}</span>
      <span class="muted">${esc(c.faction)}・${icoTyp(c.typ, c.spear)}${esc(c.typ)}${c.spear ? "（槍・後衛可）" : ""}・${esc(c.role)}</span>
    </div>
    <div class="ci-stats num">武勇 ${c.might}　知略 ${c.wits}</div>
    <div class="ci-stats num muted">兵力 ${c.men.toLocaleString()}
      　攻勢 毎分約${c.atk_pm.toLocaleString()}人を削る
      　守勢 実効${c.eff_men.toLocaleString()}人ぶんを受ける</div>
    <div class="ci-row">
      <span class="tag skill-tag">必殺技</span>
      <b>【${esc(c.skill)}】</b> <span class="muted">対象 ${esc(c.skill_target)}｜</span>${esc(c.skill_desc)}${
        /損害|延焼/.test(c.skill_desc) ? '<span class="muted">　※損害は敵の守りで目減りする</span>' : ""}
      <span class="muted">ゲージ: 消費${esc(c.gauge_cost)}%・上昇${esc(c.gauge_rate)}・初期${esc(c.gauge_init)}</span>
    </div>
    ${traits}
    ${c.quote ? `<div class="ci-quote">「${esc(c.quote)}」</div>` : ""}
    </div>`;
}

function drawSlots() {
  // 再描画でホバー中の枠ごと差し替わると mouseleave が永遠に来ず、
  // 概要チップが取り残される（↑↓✕・ドラッグ経由で実際に起きた）。
  // 描き直す前に必ず消す。
  hideTip();
  const nf = FORMS[cur.form];
  const rows = [];
  for (let i = 0; i < 6; i++) {
    const name = cur.cards[i];
    const c = name && D.roster.find((x) => x.name === name);
    const front = i < nf;
    let warn = "";
    if (c) {
      if (front && c.typ === "弓兵") warn = "弓兵は前衛に置けない";
      if (!front && c.typ !== "弓兵" && !c.spear) warn = "後衛は弓兵か槍持ちだけ";
    }
    rows.push(`<div class="slot ${front ? "front" : "rear"} ${c ? "" : "empty"}"
         data-i="${i}" ${c ? 'draggable="true"' : ""}>
      <span class="pos">${front ? "前衛" : "後衛"}${i + 1}</span>
      ${c ? `<img class="mini-face" src="/portrait/${encodeURIComponent(c.person)}" alt="">` : ""}
      <span class="who">${c ? `<b>${esc(c.name)}</b><span class="unit-type ${TYPE_CLS[c.typ]}">${icoTyp(c.typ, c.spear)}${esc(c.typ)}${c.spear ? "・槍" : ""}</span>
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
  // 枠の武将: マウスオンで概要チップ、クリックで上の詳細パネルへ固定表示
  $$("#slots .slot:not(.empty)").forEach((sl) => {
    const name = cur.cards[+sl.dataset.i];
    sl.onmouseenter = (e) => showTip(e, name);
    sl.onmousemove = (e) => moveTip(e);
    sl.onmouseleave = hideTip;
    sl.onclick = (e) => {
      if (e.target.closest("button")) return;   // ↑↓✕は並べ替え操作
      hideTip();
      showCardInfo(name);
      $$("#slots .slot").forEach((x) => x.classList.remove("selected"));
      sl.classList.add("selected");
      const ci = $("#cardinfo");
      if (ci && window.innerWidth <= 760) ci.scrollIntoView({ behavior: "smooth" });
    };
  });
  // ドラッグ＆ドロップで並べ替え（↑↓はタッチ環境用に残す）
  let dragFrom = null;
  $$("#slots .slot").forEach((sl) => {
    sl.ondragstart = (e) => {
      dragFrom = +sl.dataset.i;
      sl.classList.add("dragging");
      hideTip();
      e.dataTransfer.effectAllowed = "move";
    };
    sl.ondragend = () => {
      dragFrom = null;
      $$("#slots .slot").forEach((x) => x.classList.remove("dragging", "dragover"));
    };
    sl.ondragover = (e) => {
      if (dragFrom === null) return;
      e.preventDefault();
      $$("#slots .slot").forEach((x) => x.classList.remove("dragover"));
      sl.classList.add("dragover");
    };
    sl.ondrop = (e) => {
      e.preventDefault();
      if (dragFrom === null) return;
      let to = Math.min(+sl.dataset.i, cur.cards.length - 1);
      const [card] = cur.cards.splice(dragFrom, 1);
      cur.cards.splice(to, 0, card);
      drawAll();
    };
  });
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
    <span class="muted">【${esc(c.skill)}】　特性: ${c.traits.length
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
document.addEventListener("scroll", hideTip, { passive: true, capture: true });

function deckCost() {
  return cur.cards.reduce((s, n) => {
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
  const inDeck = new Set(cur.cards);
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
  const r = await api("/api/deck", { reg: cur.reg, form: cur.form, cards: cur.cards });
  if (r.ok) {
    D.decks[cur.reg] = { form: cur.form, cards: [...cur.cards] };
    D.entry_errors = r.entry_errors;
    D.boards_ok = r.boards_ok;
    flashMsg("登録した。"); drawEntryState(); drawSortieBar();
  } else {
    flashMsg(r.errors.join("／"), true);
  }
}

/* ── リプレイ一覧 ───────────────────── */
async function viewReplays(state) {
  const d = await api("/api/replays");
  const row = (m) => `
    <tr class="${m.mine ? "me" : ""}">
      <td class="num">${esc(m.at)}</td>
      <td><span class="mode-tag">${esc(m.mode)}${m.role === "防" ? "・防衛" : ""}</span></td>
      <td>${esc(m.board)}</td>
      <td>${esc(m.a)} <small>対</small> ${esc(m.b)}</td>
      <td><a href="/replay?id=${m.id}">観る</a></td></tr>`;
  const mine = d.battles.filter((m) => m.mine);
  const rest = d.battles.filter((m) => !m.mine);
  $("#app").innerHTML = `
    <div class="panel fade-in" style="margin-bottom:16px">
      <h2>自分の戦歴<span class="sub">防衛戦（挑まれた側）も残る</span></h2>
      <table class="std">${mine.map(row).join("")
        || "<tr><td class='muted'>記録なし</td></tr>"}</table>
    </div>
    <div class="panel fade-in">
      <h2>近ごろの合戦<span class="sub">他家の戦いも観て研究できる</span></h2>
      <table class="std">${rest.map(row).join("")
        || "<tr><td class='muted'>記録なし</td></tr>"}</table>
    </div>`;
}

/* ── リプレイ再生 ───────────────────── */
/* 軍中の心得（§7.79）: 敗北の下に1つずつ出す小話。**規則の正本ではなく
   読み物** — 中身は全部エンジンの実測に基づく（三すくみ・抑制・槍後衛・
   避雷針・余剰ゲージ・本陣）。ローディング画面が無い（速すぎる）ので、
   負けて悔しい時にだけ届く形にした。 */
const WAR_TIPS = [
  "三すくみを覚えておけ——歩は騎を受け止め、騎は弓を蹴散らし、弓は歩を射抜く。",
  "陣形は弓の数を決める。鶴翼は弓二・魚鱗は弓三・雁行は弓四。矢を増やすほど、壁は薄くなる。",
  "弓は肉薄されると矢も技も鈍る。射手を守る壁を惜しむな。",
  "槍持ちの歩兵は後衛にも置ける。回り込む騎馬は、槍が突き止める。",
  "前衛は高く積め。最も安い札が矢面に立ち、高い札が長く戦う。",
  "一枚の柱に頼る軍は、柱を失えば崩れる。敵の柱は壁で受け、脇の小勢から崩せ。",
  "使い切れなかった点は、開戦の気勢に変わる。無駄にはならぬが、兵にもならぬ。",
  "本陣を預かる将が崩れれば、全軍が動揺する。本陣は置き所が肝心よ。",
  "必殺技は消費が軽いほど数を撃ち、重いほど一撃に懸ける。技の巡りも編成のうち。",
  "敗れた戦こそ実況を読み返せ。どの隊が先に崩れたかに、次の布陣の答えがある。",
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
  $("#app").innerHTML = `
    <div class="replay-head">
      <a class="btn ghost mini" href="${fromFight ? "/senki" : "/replays"}">${
        fromFight ? "← 戦記へ" : "← 戦歴へ"}</a>
      <div><h2>${FIGHT ? esc(FIGHT.label) : esc(d.board)}</h2>
      <span class="muted">${FIGHT ? "戦況を見届けよ" : esc(d.when || "")}
        ${d.games.length > 1 && !FIGHT ? `・${wins}勝${losses}敗 ${overall}` : ""}</span></div>
    </div>
    <div class="battle-card">
      <div class="battle-side mine"><span>${mineSide}</span><b>${esc(d.mine_name)}</b></div>
      <div class="battle-vs">対</div>
      <div class="battle-side foe"><span>${foeSide}</span><b>${esc(d.foe_name)}</b></div>
    </div>
    ${tabs}
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
      </div>
    </div>`;
  let gi = 0;
  let timer = null;
  let sideMap = null;
  let mineNames = [], foeNames = [];
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
    el.innerHTML = `
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
    mineNames = g.mine_names || [];
    foeNames = g.foe_names || [];
    sideMap = [...(g.foe_names || []).map((n) => [n, "foe-name"]),
               ...(g.mine_names || []).map((n) => [n, "mine-name"])];
    const log = $("#log");
    log.innerHTML = g.lines.map((ln) => fmtLine(ln)).join("");
    $("#battle-summary").innerHTML = replayOutcome(g, d, isParticipant);
    drawChart(g, -1);
    drawNotes(g);
    drawReport(g);
    startPlayback(g);
  }

  function lineTime(ln) {
    const m = ln.match(/【(\d+):(\d+)】/);
    return m ? (+m[1] - 8) * 60 + (+m[2]) : null;
  }

  function markNames(html_) {
    if (!sideMap) return html_;
    for (const [name, side] of sideMap) {
      html_ = html_.split(esc(name)).join(
        `<span class="${side}">${esc(name)}</span>`);
    }
    return html_;
  }

  function fmtLine(ln) {
    let cls = "line", body = esc(ln);
    if (/^━━/.test(ln)) cls += " band";
    else if (/「.+」$/.test(ln.trim()) && !ln.includes("【")) cls += " quote";
    if (ln.includes("◇戦況")) cls += " check";
    let side = "system-event", sideText = "戦況";
    if (!ln.includes("◇戦況") && !/^━━/.test(ln)) {
      const mineAt = Math.min(...(mineNames.map((n) => ln.indexOf(n)).filter((i) => i >= 0)
        .concat(ln.indexOf(d.me_first ? "曹軍" : "孫軍")).filter((i) => i >= 0)));
      const foeAt = Math.min(...(foeNames.map((n) => ln.indexOf(n)).filter((i) => i >= 0)
        .concat(ln.indexOf(d.me_first ? "孫軍" : "曹軍")).filter((i) => i >= 0)));
      if (Number.isFinite(mineAt) && (!Number.isFinite(foeAt) || mineAt <= foeAt)) {
        side = "mine-event"; sideText = mineSide;
      } else if (Number.isFinite(foeAt)) {
        side = "foe-event"; sideText = foeSide;
      }
    }
    cls += " " + side;
    body = body.replace(/【([^】:]+)】/g, (m0, x) =>
      /^\d+$/.test(x) ? m0 : `【<span class="skillname">${x}</span>】`);
    body = body.replace(/^(◆)/, '<span class="art">◆</span>');
    body = body.replace(/【(\d+:\d+)】/, '<span class="t">$1</span>');
    body = markNames(body);
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
    $("#play").onclick = start;
    $("#skip").onclick = () => {
      clearInterval(timer);
      lines.forEach((el) => el.classList.add("show"));
      drawChart(g, Infinity);
      revealResult();
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
        return `<div class="unit-row ${hp <= 0.005 ? "dead" : ""}">
          <span class="uname">${esc(u.name)} ${icoTyp(u.typ)}</span>
          <span class="bars">
            <span class="bar dmg"><i class="skillpart" style="width:${sk / maxD * 100}%"></i><i style="width:${(u.dealt - sk) / maxD * 100}%"></i></span>
            <span class="bar hp"><i style="width:${hp * 100}%"></i></span>
          </span>
          <span class="val">与${(u.dealt / 1000).toFixed(1)}千<small>（技${(sk / 1000).toFixed(1)}）</small></span>
          <span class="val">${u.fell ? `<span class="fell">${u.fell}崩</span>`
            : (hp <= 0.005 ? "壊滅" : "残" + Math.round(hp * 100) + "%")}</span>
        </div>`;
      }).join("");
    $("#report").innerHTML = '<div class="side-label">─ 軍功帳（朱=必殺技・橙=通常） ─</div>' +
      side("自軍（" + esc(d.mine_name) + "）", g.mine) + side("敵軍（" + esc(d.foe_name) + "）", g.foe);
  }
}

/* ── 起動 ──────────────────────── */
(async function boot() {
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
  if (view === "replays") return viewReplays(state);
  if (view === "replay") return viewReplay(state);
})();
