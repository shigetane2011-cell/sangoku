#!/usr/bin/env python3
"""1戦を記録し、ブラウザで再生できる自己完結HTMLを書き出す。

**シミュレータはバランスを測れるが「見ていて面白いか」は測れない。**
このゲームは非同期オートバトルで、プレイヤーは60秒のあいだ何も操作しない。
勝敗が読み取れる形で提示できているか、リプレイを見返す気になるかは、
指標を何周回しても答えが出ない。実際に見て確かめるしかない。

決定論（§8.4）があるので、(編成, 配置, 戦場条件, シード) が同じなら何度でも
同じ戦闘を再生できる。この道具はその契約の上に乗っている。バランス調整で
数値が変わっても、再生の仕組みそのものは作り直しにならない。

usage:
  python3 sim/replay.py                      # 既定の対戦を replay.html へ
  python3 sim/replay.py --seed 7 --out x.html
  python3 sim/replay.py --battlefield rain
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import (BACK_OFFSET, CARDS, GAUGE_MAX, LANE_DEPTH, Battle,  # noqa: E402
                    MAX_TICKS, make_team)

SAMPLE = 2          # 何ティックごとに姿勢を記録するか（0.2秒刻み）

# 既定の対戦。高コスト戦（上限40）で、名前で分かる札を並べる。
# 陣形を左右で変えて、盤面の形の違いが見えるようにする。
SHU = ["kanu_10", "chouun_9", "kochu_8", "gien_7", "batai_3", "chinto_3"]
WEI = ["shibai_10", "choryo_9", "kyocho_8", "tougai_6", "manchou_3", "sokou_3"]


def record(team_a, team_b, seed, battlefield):
    """1戦を再生できる形で記録する。"""
    b = Battle([team_a, team_b], seed=seed, battlefield=battlefield, log=True)
    units = [{
        "name": u.card["name"],
        "side": u.side,
        "lane": u.lane,
        "row": u.row,
        "troop": u.card["troop"],
        "cost": u.card["cost"],
        "maxhp": u.max_hp,
        "cmdr": u.is_commander,
        "skill": u.card["skill"]["name"],
        "gcost": u.card["skill"].get("gauge", 100),
    } for u in b.units]

    frames, prev_skills = [], [0] * len(b.units)
    result = None
    while b.tick < MAX_TICKS and result is None:
        result = b.step()
        if b.tick % SAMPLE and result is None:
            continue
        frame = []
        for i, u in enumerate(b.units):
            flags = (1 if u.retreated else 0) | (2 if u.flanking else 0)
            flags |= 4 if u.stunned() else 0
            flags |= 8 if u.skills > prev_skills[i] else 0
            prev_skills[i] = u.skills
            frame.append([u.pos, max(0, u.hp), min(GAUGE_MAX, u.gauge), flags])
        frames.append(frame)

    return {
        "meta": {
            "seed": seed,
            "battlefield": b.field.get("label", battlefield),
            "winner": result["winner"],
            "reason": result["reason"],
            "ticks": result["ticks"],
            "sample": SAMPLE,
            "gaugeMax": GAUGE_MAX,
            "xmin": -BACK_OFFSET,
            "xmax": LANE_DEPTH + BACK_OFFSET,
        },
        "units": units,
        "frames": frames,
        "events": [[t, s] for t, s in b.events],
    }


PAGE = """<title>作戦盤</title>
<style>
  /* 色は中国の軍図に倣う。両軍を青（左）と朱（右）で分け、地は青みを持たせた
     墨色。必殺技だけ真鍮色で光らせ、他の色数を絞る。
     淡色・暗色・OS任せの3状態すべてでトークンを定義する。 */
  :root {
    --ground: #f4f4f2; --panel: #ffffff; --line: #d8d8d4;
    --text: #191c21; --dim: #6b7078; --faint: #9aa0a8;
    --blue: #2f6f96; --blue-soft: #cfe0ea;
    --red: #a8433a; --red-soft: #f0d5d1;
    --gold: #9a7420; --grid: #e6e6e2;
    --shadow: 0 1px 2px rgba(20,24,30,.08);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --ground: #0e1116; --panel: #161a20; --line: #262c35;
      --text: #e8eaed; --dim: #98a0aa; --faint: #666e79;
      --blue: #6db2da; --blue-soft: #1d3a4d;
      --red: #e0736a; --red-soft: #45211d;
      --gold: #d8ae52; --grid: #1c2129;
      --shadow: 0 1px 2px rgba(0,0,0,.4);
    }
  }
  :root[data-theme="dark"] {
    --ground: #0e1116; --panel: #161a20; --line: #262c35;
    --text: #e8eaed; --dim: #98a0aa; --faint: #666e79;
    --blue: #6db2da; --blue-soft: #1d3a4d;
    --red: #e0736a; --red-soft: #45211d;
    --gold: #d8ae52; --grid: #1c2129;
    --shadow: 0 1px 2px rgba(0,0,0,.4);
  }

  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--ground); color: var(--text);
    /* 日本語のため webfont は埋め込めない（容量とCSP）。
       システムスタックを明示し、階層は太さ・字間・等幅数字で作る。 */
    font-family: "Hiragino Sans", "Noto Sans JP", "Yu Gothic Medium",
                 "Meiryo", system-ui, sans-serif;
    font-size: 14px; line-height: 1.5;
    font-variant-numeric: tabular-nums;
  }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 24px 20px 40px; }

  header { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
           border-bottom: 1px solid var(--line); padding-bottom: 12px; }
  h1 { font-size: 19px; font-weight: 700; letter-spacing: .14em; margin: 0; }
  .meta { color: var(--dim); font-size: 12px; letter-spacing: .06em;
          display: flex; gap: 14px; flex-wrap: wrap; }
  .meta b { color: var(--text); font-weight: 600; }

  /* --- 盤面 ----------------------------------------------------------
     **1体につき1本の軌道を与える。** 同じレーンの武将が同じ位置に来ると
     重なって読めなくなるため、縦に分ける。レーンは背景の帯でまとめる。
     兵種は形で区別する（歩兵=角・騎兵=菱・弓兵=丸）。 */
  .board { margin-top: 18px; background: var(--panel); border: 1px solid var(--line);
           border-radius: 3px; padding: 6px 0 4px; box-shadow: var(--shadow);
           overflow: hidden; }
  .lane { padding: 5px 0; border-bottom: 1px solid var(--grid); }
  .lane:last-child { border-bottom: 0; }
  .lane.alt { background: linear-gradient(var(--grid), var(--grid)); }
  .lane-tag { font-size: 10px; letter-spacing: .18em; color: var(--faint);
              padding: 0 0 2px 14px; }
  .track { position: relative; height: 26px; }
  .gutter { position: absolute; left: 14px; top: 50%; transform: translateY(-50%);
            width: 116px; font-size: 11px; font-weight: 600; white-space: nowrap;
            overflow: hidden; text-overflow: ellipsis; }
  .gutter em { font-style: normal; color: var(--faint); font-size: 10px;
               margin-left: 4px; }
  .rail { position: absolute; left: 138px; right: 14px; top: 0; bottom: 0; }
  .home { position: absolute; top: 3px; bottom: 3px; width: 1px; background: var(--grid); }
  .mk { position: absolute; top: 50%; width: 20px; height: 20px;
        transform: translate(-50%, -50%); transition: left .12s linear;
        display: grid; place-items: center; }
  .mk .sh { position: absolute; inset: 0; border: 1.5px solid currentColor;
            background: linear-gradient(to top, currentColor var(--hp,100%),
                                        transparent var(--hp,100%)); }
  .mk.inf .sh { border-radius: 3px; }
  .mk.cav .sh { border-radius: 3px; transform: rotate(45deg) scale(.82); }
  .mk.arc .sh { border-radius: 50%; }
  .mk .g { position: relative; font-size: 11px; font-weight: 700; line-height: 1;
           color: var(--panel); }
  .mk.s0 { color: var(--blue); }
  .mk.s1 { color: var(--red); }
  .mk.low .g { color: var(--text); }
  .mk .gg { position: absolute; left: -2px; right: -2px; bottom: -6px; height: 2px;
            background: var(--grid); }
  .mk .gg i { display: block; height: 100%; background: var(--gold); }
  .mk.dead { opacity: .2; }
  .mk.dead .sh { background: none; }
  .mk.flank .sh { border-style: dashed; }
  .mk.stun { filter: grayscale(1) brightness(.8); }
  .mk.cast { box-shadow: 0 0 0 3px var(--gold); border-radius: 50%; }
  .cmdr-dot { position: absolute; top: -7px; left: 50%; transform: translateX(-50%);
              font-size: 8px; color: var(--gold); font-weight: 700; }

  /* --- 操作 ---------------------------------------------------------- */
  .ctl { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
  button { font: inherit; font-size: 13px; font-weight: 600; letter-spacing: .1em;
           padding: 7px 16px; border: 1px solid var(--line); border-radius: 2px;
           background: var(--panel); color: var(--text); cursor: pointer; }
  button:hover { border-color: var(--blue); color: var(--blue); }
  button:focus-visible { outline: 2px solid var(--gold); outline-offset: 2px; }
  input[type=range] { flex: 1; accent-color: var(--blue); }
  .clock { font-size: 13px; color: var(--dim); min-width: 92px; }
  .clock b { color: var(--text); font-size: 15px; font-weight: 700; }

  /* --- 下段 ---------------------------------------------------------- */
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 18px; }
  @media (max-width: 780px) { .cols { grid-template-columns: 1fr; } }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 3px;
          box-shadow: var(--shadow); overflow: hidden; }
  .card h2 { font-size: 11px; letter-spacing: .18em; color: var(--dim);
             margin: 0; padding: 10px 14px; border-bottom: 1px solid var(--line);
             font-weight: 600; }
  .roster { padding: 6px 0; }
  .row { display: grid; grid-template-columns: 1fr 46px; gap: 8px; align-items: center;
         padding: 5px 14px; }
  .row .who { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
  .row .who span { font-size: 12px; font-weight: 600; white-space: nowrap;
                   overflow: hidden; text-overflow: ellipsis; }
  .row .who em { font-style: normal; font-size: 10px; color: var(--faint); }
  .row .stack { display: flex; flex-direction: column; gap: 2px; }
  .row .pct { font-size: 11px; color: var(--dim); text-align: right; }
  .row.gone .who span { text-decoration: line-through; color: var(--faint); }
  .logbox { height: 268px; overflow-y: auto; padding: 8px 0; }
  .ev { display: grid; grid-template-columns: 44px 1fr; gap: 8px;
        padding: 3px 14px; font-size: 12px; color: var(--dim); }
  .ev time { color: var(--faint); font-size: 11px; }
  .ev.now { background: var(--grid); color: var(--text); }
  .ev.skill { color: var(--gold); font-weight: 600; }
  .note { margin-top: 18px; color: var(--dim); font-size: 12px; line-height: 1.7; }
  .note b { color: var(--text); }
  @media (prefers-reduced-motion: reduce) { .chip { transition: none; } }
</style>

<div class="wrap">
<header>
  <h1>作戦盤</h1>
  <div class="meta" id="meta"></div>
</header>

<div class="board" id="board"></div>

<div class="ctl">
  <button id="play">再生</button>
  <input type="range" id="scrub" min="0" value="0" step="1" aria-label="時刻">
  <div class="clock"><b id="t">0.0</b> 秒</div>
</div>

<div class="cols">
  <div class="card">
    <h2>両軍の兵力とゲージ</h2>
    <div class="roster" id="roster"></div>
  </div>
  <div class="card">
    <h2>実況</h2>
    <div class="logbox" id="log"></div>
  </div>
</div>

<p class="note" id="note"></p>
</div>

<script>
const R = __DATA__;
const M = R.meta, U = R.units, F = R.frames;
const span = M.xmax - M.xmin;
const tickOf = i => i * M.sample;
const secOf = i => (tickOf(i) / 10).toFixed(1);

// 盤面。**1体につき1本の軌道**を与えるので、重なることがない。
// レーン内の並びは 青の前衛→青の後衛→朱の後衛→朱の前衛。向かい合う形になる。
const GLYPH = { inf: '歩', cav: '騎', arc: '弓' };
const board = document.getElementById('board');
const marks = [];
for (let L = 0; L < 3; L++) {
  const lane = document.createElement('div');
  lane.className = 'lane' + (L % 2 ? ' alt' : '');
  lane.innerHTML = '<div class="lane-tag">レーン' + (L + 1) + '</div>';
  const order = U.map((u, i) => [u, i]).filter(([u]) => u.lane === L)
    .sort((a, b) => (a[0].side - b[0].side)
      || ((a[0].side ? 1 : -1) * ((a[0].row === 'back' ? 1 : 0) - (b[0].row === 'back' ? 1 : 0))));
  for (const [u, i] of order) {
    const tr = document.createElement('div');
    tr.className = 'track';
    tr.innerHTML = '<div class="gutter" style="color:var(--' + (u.side ? 'red' : 'blue')
      + ')">' + u.name.replace(/〔.*/, '')
      + '<em>' + (u.row === 'front' ? '前' : '後') + '・' + u.cost + '</em></div>'
      + '<div class="rail"></div>';
    const rail = tr.querySelector('.rail');
    for (const p of [0, 1]) {
      const h = document.createElement('div');
      h.className = 'home';
      h.style.left = ((p ? M.xmax - 150 : 150 - M.xmin) / span * 100) + '%';
      rail.appendChild(h);
    }
    const mk = document.createElement('div');
    mk.className = 'mk s' + u.side + ' ' + u.troop;
    mk.innerHTML = '<div class="sh"></div><div class="g">' + GLYPH[u.troop] + '</div>'
      + '<div class="gg"><i></i></div>'
      + (u.cmdr ? '<div class="cmdr-dot">将</div>' : '');
    mk.title = u.name + '　' + u.skill + '（消費' + u.gcost + '%）';
    rail.appendChild(mk);
    lane.appendChild(tr);
    marks[i] = mk;
  }
  board.appendChild(lane);
}

// 兵力とゲージの一覧。両軍を分けて並べる。
const roster = document.getElementById('roster');
const rows = [];
[0, 1].forEach(side => {
  U.forEach((u, i) => {
    if (u.side !== side) return;
    const r = document.createElement('div');
    r.className = 'row';
    r.innerHTML = '<div class="who"><span style="color:var(--' + (side ? 'red' : 'blue')
      + ')">' + u.name + '</span><em>' + u.cost + '　' + u.skill + '</em></div>'
      + '<div class="stack"><div class="bar hp"><i></i></div>'
      + '<div class="bar gg"><i></i></div></div>';
    r.classList.add('s' + side);
    roster.appendChild(r);
    rows[i] = r;
  });
});

// 実況。時刻で引けるように行を先に作っておく。
const logbox = document.getElementById('log');
const evs = R.events.map(([t, s]) => {
  const d = document.createElement('div');
  d.className = 'ev' + (s.includes('必殺技') ? ' skill' : '');
  d.innerHTML = '<time>' + (t / 10).toFixed(1) + '</time><span>' + s + '</span>';
  logbox.appendChild(d);
  return { t, el: d };
});

document.getElementById('meta').innerHTML =
  '<span>戦場 <b>' + M.battlefield + '</b></span>'
  + '<span>シード <b>' + M.seed + '</b></span>'
  + '<span>決着 <b>' + (M.ticks / 10).toFixed(1) + '秒</b></span>'
  + '<span>結果 <b>' + (M.winner === null ? '引き分け'
      : (M.winner === 0 ? '青の勝ち' : '朱の勝ち')) + '（' + M.reason + '）</b></span>';

document.getElementById('note').innerHTML =
  '形は兵種（角＝<b>歩兵</b>／菱＝<b>騎兵</b>／丸＝<b>弓兵</b>）。'
  + '塗りの高さが<b>残兵力</b>、下の金の帯が<b>必殺技ゲージ</b>で、消費量は武将ごとに違う。'
  + '破線の縁は騎兵の<b>迂回中</b>（回り込むあいだは攻撃できない）、'
  + '灰色は<b>行動阻害</b>、金の輪は<b>必殺技の発動</b>。'
  + '1体につき1本の軌道を与えているので、重なって見えなくなることはない。'
  + '同じ<b>編成・配置・戦場・シード</b>なら何度でも同じ戦闘になる（§8.4）。';

const scrub = document.getElementById('scrub');
scrub.max = F.length - 1;

function draw(k) {
  const f = F[k];
  U.forEach((u, i) => {
    const [pos, hp, gauge, flags] = f[i];
    const hpPct = Math.max(0, hp / u.maxhp * 100);
    const ggPct = gauge / M.gaugeMax * 100;
    const mk = marks[i];
    mk.style.left = ((pos - M.xmin) / span * 100) + '%';
    mk.style.setProperty('--hp', hpPct + '%');
    mk.className = 'mk s' + u.side + ' ' + u.troop
      + (hpPct < 55 ? ' low' : '')
      + (flags & 1 ? ' dead' : '') + (flags & 2 ? ' flank' : '')
      + (flags & 4 ? ' stun' : '') + (flags & 8 ? ' cast' : '');
    mk.querySelector('.gg i').style.width = ggPct + '%';
    const r = rows[i];
    r.classList.toggle('gone', !!(flags & 1));
    r.querySelector('.hp i').style.width = hpPct + '%';
    r.querySelector('.gg i').style.width = ggPct + '%';
  });
  const tick = tickOf(k);
  document.getElementById('t').textContent = secOf(k);
  let last = null;
  evs.forEach(e => {
    const on = e.t <= tick;
    e.el.classList.toggle('now', on && e.t > tick - M.sample * 6);
    if (on) last = e.el;
  });
  if (last) {
    const top = last.offsetTop - logbox.clientHeight + 40;
    logbox.scrollTop = Math.max(0, top);
  }
}

let playing = false, timer = null, k = 0;
const btn = document.getElementById('play');
function stop() { playing = false; btn.textContent = '再生'; clearInterval(timer); }
function start() {
  if (k >= F.length - 1) k = 0;
  playing = true; btn.textContent = '停止';
  timer = setInterval(() => {
    k++;
    if (k >= F.length - 1) { k = F.length - 1; draw(k); scrub.value = k; stop(); return; }
    draw(k); scrub.value = k;
  }, M.sample * 100);
}
btn.onclick = () => (playing ? stop() : start());
scrub.oninput = () => { stop(); k = +scrub.value; draw(k); };
draw(0);
</script>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--battlefield", default="clear")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__),
                                                  "..", "replay.html"))
    args = ap.parse_args()

    missing = [c for c in SHU + WEI if c not in CARDS]
    if missing:
        sys.exit(f"cards.json に無い武将ID: {missing}")

    a = make_team(SHU, commander=4, formation="kakuyoku")
    b = make_team(WEI, commander=4, formation="gyorin")
    data = record(a, b, args.seed, args.battlefield)

    html = PAGE.replace("__DATA__", json.dumps(data, ensure_ascii=False,
                                               separators=(",", ":")))
    out = os.path.abspath(args.out)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    m = data["meta"]
    print(f"wrote {out} ({len(html):,} bytes)")
    print(f"  {m['ticks']/10:.1f}秒 / {m['reason']} / "
          f"{'引き分け' if m['winner'] is None else ('蜀' if m['winner']==0 else '魏')}の勝ち"
          f" / 実況{len(data['events'])}行")


if __name__ == "__main__":
    main()
