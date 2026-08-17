# -*- coding: utf-8 -*-
"""sim/play.py -- 実プレイヤーの導線（まず1人で回す・§7.40）

登録 → デッキを組む → 検証 → ランク戦の巡へ参加 → リプレイと順位を見る、を
CLI で通す。盤面・マッチ・順位表の実装は field/match/ladder をそのまま使い、
ここは**配線だけ**を持つ（同じ量の定義を2箇所に持たない）。

  python3 -m sim.play register --name 自分 --email you@example.com
  python3 -m sim.play roster --reg 低コスト戦
  python3 -m sim.play deck --player <id> --reg 低コスト戦 \\
      --cards "張飛、許褚、趙雲、黄忠、夏侯淵" --form 狭く深い
  python3 -m sim.play status --player <id>
  python3 -m sim.play round --player <id> [--board 低コスト戦] [--replay]
  python3 -m sim.play standings [--board 低コスト戦]

設計メモ:
- **デッキの並び順がそのまま配置**（前衛から）。陣形が前衛の枚数を決めるので、
  「雁行」なら先頭2枚が前衛（近接）、残り4枚が後衛（弓）になる。
- round は本来「組む→告知→編成期間→戦う」（§3）だが、CLI では告知と戦いを
  続けて行う。組み方は plan_round のまま決定的なので、サーバー化しても同じ組になる。
- ダミーの編成は (性格, 通し番号) から決定的に再構成できるので DB には持たない。
  レートと巡カウンタだけを持つ（players.ratings / boards）。
- 対戦の乱数種は ladder.battle_seed（CRC32）。**プロセスが変わっても同じ巡は
  同じ戦いになる**（§8.4。Python の hash() は塩が変わるので使えない）。
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Optional, Tuple

from . import field as F
from . import ladder as L
from . import match as M
from . import players as P
from . import dummies as D
from . import rosterdata as R

FORM_BY_NAME = D.FORM_BY_NAME
REG_NAMES = tuple(label for label, _ in M.REGULATIONS)
MIN_DUMMIES = 15    # 最初の足場。自分を足して16人 = 検算した規模


# ----------------------------------------------------------------------------
# カードとデッキ
# ----------------------------------------------------------------------------

def _card_index(cards) -> Dict[str, F.Card]:
    """武将名（字号つき）と人物名の両方から引ける索引。人物名は一意のときだけ。"""
    idx: Dict[str, F.Card] = {}
    by_person: Dict[str, List[F.Card]] = {}
    for c in cards:
        idx[c.name] = c
        by_person.setdefault(M.person_of(c), []).append(c)
    for person, cs in by_person.items():
        if len(cs) == 1 and person not in idx:
            idx[person] = cs[0]
    return idx


def parse_deck(cards, names_raw: str, form_name: str
               ) -> Tuple[Optional[F.Army], List[str]]:
    """「、」区切りの武将名と陣形名から Army を作る。errs が空なら成功。

    陣形の旧名（広く浅い/標準/狭く深い）は読み替えて受ける（DB に残っていても
    壊れないように）。"""
    errs: List[str] = []
    form = FORM_BY_NAME.get(F.FORM_ALIAS.get(form_name, form_name))
    if form is None:
        return None, ["陣形は {} のどれか".format("・".join(FORM_BY_NAME))]
    idx = _card_index(cards)
    picked: List[F.Card] = []
    for raw in re.split(r"[、,]", names_raw):
        n = raw.strip()
        if not n:
            continue
        c = idx.get(n)
        if c is None:
            errs.append("{} というカードが見つからない（同名複数なら字号まで書く）"
                        .format(n))
            continue
        picked.append(c)
    if errs:
        return None, errs
    return F.Army(tuple(picked), form), errs


def entry_of(cx, cards, player_id: str, name: str
             ) -> Tuple[Optional[M.Entry], List[str]]:
    """DB のデッキ3つから Entry を組んで検証する。"""
    decks = P.decks_of(cx, player_id)
    missing = [r for r in REG_NAMES if r not in decks]
    if missing:
        return None, ["デッキ未登録: " + "、".join(missing)]
    units = []
    errs: List[str] = []
    for reg in REG_NAMES:
        raw, form_name = decks[reg]
        army, es = parse_deck(cards, raw, form_name)
        if es:
            errs += ["{}: {}".format(reg, e) for e in es]
        else:
            units.append(army)
    if errs:
        return None, errs
    entry = M.Entry(tuple(units), name=name)
    return entry, M.validate(entry)


# ----------------------------------------------------------------------------
# ダミーの再構成と順位表
# ----------------------------------------------------------------------------

def dummy_entries(cx, cards) -> Dict[str, M.Entry]:
    """登録済みダミーの編成を (性格, 通し番号) から決定的に再構成する。"""
    pidx = {p.name: i for i, p in enumerate(D.PERSONAS)}
    out: Dict[str, M.Entry] = {}
    for pl in P.all_players(cx, kind=P.DUMMY):
        m = re.match(r"^(.+?)(\d+)$", pl.display_name)
        if not m or m.group(1) not in pidx:
            continue
        k = pidx[m.group(1)]
        i = (int(m.group(2)) - 1) * len(D.PERSONAS) + k
        out[pl.id] = D.make_entry(cards, D.PERSONAS[k], i)
    return out


def load_board(cx, name: str) -> L.Board:
    b = L.Board(name, REG_NAMES.index(name) if name in REG_NAMES else None)
    for pid, (r, g) in P.board_ratings(cx, name).items():
        b.rating[pid] = r
        b.games[pid] = g
    return b


def save_board(cx, b: L.Board) -> None:
    P.save_board_ratings(cx, b.name, {
        pid: (b.rating[pid], b.games.get(pid, 0)) for pid in b.rating})


def ensure_dummies(cx, cards) -> Dict[str, M.Entry]:
    ents = dummy_entries(cx, cards)
    if len(ents) < MIN_DUMMIES:
        D.seed_ladder(cx, cards, MIN_DUMMIES - len(ents))
        ents = dummy_entries(cx, cards)
    return ents


# ----------------------------------------------------------------------------
# コマンド
# ----------------------------------------------------------------------------

def cmd_register(args) -> None:
    cx = P.connect(args.db)
    pl = P.register(cx, args.name, kind=P.HUMAN, email=args.email)
    print("登録した: {}  id={}".format(pl.display_name, pl.id))
    print("次: python3 -m sim.play roster でカードを見て、deck でデッキを組む")


def cmd_roster(args) -> None:
    gs = R.generals()
    if args.reg:
        cap = dict(zip(REG_NAMES, (c for _, c in M.REGULATIONS)))[args.reg]
        print("{}（上限 {:g}点・6枚・弓は後衛だけ・同一人物は3部隊で1枚）"
              .format(args.reg, cap))
    for g in sorted(gs, key=lambda g: (-float(g["コスト"]), g["名前"])):
        print("  {:>2}点 {:<3} {:<14} 技:{} 特性:{}".format(
            g["コスト"], g["兵種"], g["名前"], g["必殺技"], g["固有特性"] or "-"))


def cmd_deck(args) -> None:
    cx = P.connect(args.db)
    cards = M._roster_cards()
    army, errs = parse_deck(cards, args.cards, args.form)
    if not errs:
        cap = dict(zip(REG_NAMES, (c for _, c in M.REGULATIONS)))[args.reg]
        errs += ["{}".format(e) for e in M.placement_errors(army)]
        cost = army.total_cost()
        if cost > cap + 1e-9:
            errs.append("合計コスト {:g} が上限 {:g} を超えている".format(cost, cap))
        if len(army.cards) != M.UNIT_SIZE:
            errs.append("{}人必要（いまは{}人）".format(M.UNIT_SIZE, len(army.cards)))
    if errs:
        print("登録できない:")
        for e in errs:
            print("  - " + e)
        sys.exit(1)
    P.set_deck(cx, args.player, args.reg, args.cards, args.form)
    left = cap - cost
    print("{} に登録した（合計 {:g}点 / 上限 {:g}点・余り {:g}点は初期ゲージ"
          "{:.0%}に変わる）".format(args.reg, cost, cap,
                                    left, M.surplus_ratio(army, cap)))


def cmd_status(args) -> None:
    cx = P.connect(args.db)
    cards = M._roster_cards()
    pl = P.get(cx, args.player)
    if pl is None:
        print("その id の登録者が居ない"); sys.exit(1)
    print("{}  ({})".format(pl.display_name, pl.id))
    decks = P.decks_of(cx, pl.id)
    for reg in REG_NAMES:
        if reg in decks:
            raw, fm = decks[reg]
            army, es = parse_deck(cards, raw, fm)
            cost = army.total_cost() if army else 0.0
            print("  {:<6} [{}] {:g}点  {}".format(reg, fm, cost, raw))
        else:
            print("  {:<6} 未登録".format(reg))
    entry, errs = entry_of(cx, cards, pl.id, pl.display_name)
    if errs:
        print("検証:")
        for e in errs:
            print("  - " + e)
    else:
        print("検証: 3部隊とも登録できる")
    for name in L.BOARDS:
        r = P.board_ratings(cx, name)
        if pl.id in r:
            b = load_board(cx, name)
            pids = list(r)
            rank = b.order(pids).index(pl.id) + 1
            print("  {:<10} {:.0f}点  {}位/{}人  {}戦".format(
                name, r[pl.id][0], rank, len(pids), r[pl.id][1]))


def cmd_round(args) -> None:
    cx = P.connect(args.db)
    cards = M._roster_cards()
    pl = P.get(cx, args.player)
    if pl is None:
        print("その id の登録者が居ない"); sys.exit(1)
    entry, errs = entry_of(cx, cards, pl.id, pl.display_name)
    if errs:
        print("先にデッキを直す:")
        for e in errs:
            print("  - " + e)
        sys.exit(1)
    entries = ensure_dummies(cx, cards)
    entries[pl.id] = entry
    names = {p.id: p.display_name for p in P.all_players(cx)}
    boards = [args.board] if args.board else list(L.BOARDS)
    for name in boards:
        b = load_board(cx, name)
        rnd = P.board_round(cx, name)
        pids = list(entries)
        pairs = L.plan_round(b, pids, rnd)
        L.resolve_round(b, pairs, entries, rnd, dt=args.dt)
        save_board(cx, b)
        P.bump_board_round(cx, name)
        mine = next((pr for pr in pairs if pl.id in pr), None)
        if mine is None:
            print("{:<10} 第{}巡: 相手なし（人数が奇数）".format(name, rnd + 1))
            continue
        foe = mine[1] if mine[0] == pl.id else mine[0]
        # 同じ種で読み直す（resolve と同一の戦い）
        seed = L.battle_seed(b.name, rnd, mine[0], mine[1])
        me_first = mine[0] == pl.id
        if b.reg is None:
            r = M.play(entries[mine[0]], entries[mine[1]], args.dt, seed=seed)
            sa = r["winner"]
            won = (sa == "A") == me_first if sa != "引き分け" else None
            score = "{:g}-{:g}".format(r["wins_a"] if me_first else r["wins_b"],
                                       r["wins_b"] if me_first else r["wins_a"])
        else:
            r = M.play_one(entries[mine[0]], entries[mine[1]], b.reg,
                           args.dt, seed=seed)
            won = (r["winner"] == "A") == me_first if r["winner"] != "引き分け" else None
            score = ""
        verdict = "勝ち" if won else ("負け" if won is not None else "引き分け")
        rank = b.order(pids).index(pl.id) + 1
        print("{:<10} 第{}巡: 対 {:<8} {} {}  → {:.0f}点 {}位/{}人".format(
            name, rnd + 1, names.get(foe, foe), verdict, score,
            b.get(pl.id), rank, len(pids)))
        if args.replay and b.reg is not None:
            cap = M.REGULATIONS[b.reg][1]
            ua = M.with_surplus(entries[mine[0]].unit(b.reg), cap)
            ub = M.with_surplus(entries[mine[1]].unit(b.reg), cap)
            for line in F.narrate(ua, ub, args.dt, seed=seed):
                print("    " + line)
            print_report(ua, ub, args.dt, seed, me_first)


def eval_chart(series, me_first: bool, width: int = 60) -> List[str]:
    """形勢グラフ（将棋AIの評価値グラフの型・§9.4）。

    縦軸は残存率の差（自軍 − 敵軍）。上にいれば自軍優勢。横軸は戦場の時刻。
    """
    if not series:
        return []
    step = max(1, len(series) // width)
    pts = series[::step]
    diffs = [(ra - rb) if me_first else (rb - ra) for _, ra, rb in pts]
    bands = (0.25, 0.15, 0.05, -0.05, -0.15, -0.25)   # 行の下限（上から）
    def row_of(d):
        for i, lo in enumerate(bands):
            if d >= lo:
                return i
        return len(bands)
    # 全セルを全角で組む（半角を混ぜると列がずれる）
    rows = [["　"] * len(pts) for _ in range(len(bands) + 1)]
    for x in range(len(pts)):
        rows[3][x] = "・"                 # ±0 の点線
    for x, d in enumerate(diffs):
        rows[row_of(d)][x] = "●"
    labels = ("+25│", "   │", "   │", " ±0│", "   │", "   │", "-25│")
    out = ["── 形勢（上=自軍優勢・縦軸は残存率差%） ──"]
    for lab, cells in zip(labels, rows):
        out.append(lab + "".join(cells))
    # 横軸: 2時間ごとの目盛り（全角数字で列を揃える）
    zen = str.maketrans("0123456789", "０１２３４５６７８９")
    axis = ["　"] * len(pts)
    marks = []
    for x, (t, _, _) in enumerate(pts):
        h = int((F.BATTLE_START_H * 60 + F.mins(t)) // 60)
        if not marks or h >= marks[-1] + 2:
            marks.append(h)
            lab = "{}時".format(h).translate(zen)
            for j, ch in enumerate(lab):
                if x + j < len(axis):
                    axis[x + j] = ch
    out.append("   └" + "".join(axis))
    return out


def print_report(ua, ub, dt: float, seed: int, me_first: bool) -> None:
    """戦果表と形勢グラフ。実況は流れを語り、こちらは数字で振り返る。

    攻略の糸口はここにある: 与ダメが小さい札は「働く前に落ちた」か「射程・対面が
    悪い」、残兵が多いのに負けたなら「前衛だけ削られて後衛が余った」。
    """
    series = []
    r = F.simulate(ua, ub, dt, seed=seed, series=series)
    for line in eval_chart(series, me_first):
        print("    " + line)
    mine, foe = (r["dealt_a"], r["dealt_b"]) if me_first else (r["dealt_b"], r["dealt_a"])
    print("    ── 戦果表（与ダメ=千人・残兵%） ──")
    for tag, rows in (("自軍", mine), ("敵軍", foe)):
        cells = []
        for name, typ, dealt, men, men0 in rows:
            pct = 100.0 * men / men0 if men0 > 0 else 0.0
            state = "壊滅" if pct <= 0.5 else "{:.0f}%".format(pct)
            cells.append("{}({}) 与{:.1f} 残{}".format(
                M.person_of(F.Card(0, typ, name=name)) or F.TYPE_JP[typ],
                F.TYPE_JP[typ][0], dealt / 1000.0, state))
        print("    {}: {}".format(tag, " / ".join(cells[:3])))
        if len(cells) > 3:
            print("          {}".format(" / ".join(cells[3:])))


def cmd_standings(args) -> None:
    cx = P.connect(args.db)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    kinds = {p.id: p.kind for p in P.all_players(cx)}
    for name in ([args.board] if args.board else list(L.BOARDS)):
        r = P.board_ratings(cx, name)
        if not r:
            print("{:<10} まだ誰も戦っていない".format(name))
            continue
        b = load_board(cx, name)
        pids = b.order(list(r))
        print("{}（{}人・{}巡）".format(name, len(pids), P.board_round(cx, name)))
        for i, pid in enumerate(pids[:args.limit], 1):
            tag = "" if kinds.get(pid) == P.HUMAN else "（ダミー）"
            print("  {:>2}位 {:<10}{} {:.0f}点 {}戦".format(
                i, names.get(pid, pid), tag, b.get(pid), b.games.get(pid, 0)))


def main() -> None:
    p = argparse.ArgumentParser(description="実プレイヤーの導線")
    p.add_argument("--db", default=P.DB_PATH)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("register"); s.add_argument("--name", required=True)
    s.add_argument("--email", required=True); s.set_defaults(fn=cmd_register)
    s = sub.add_parser("roster"); s.add_argument("--reg", choices=REG_NAMES)
    s.set_defaults(fn=cmd_roster)
    s = sub.add_parser("deck"); s.add_argument("--player", required=True)
    s.add_argument("--reg", choices=REG_NAMES, required=True)
    s.add_argument("--cards", required=True)
    s.add_argument("--form", choices=tuple(FORM_BY_NAME), required=True)
    s.set_defaults(fn=cmd_deck)
    s = sub.add_parser("status"); s.add_argument("--player", required=True)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("round"); s.add_argument("--player", required=True)
    s.add_argument("--board", choices=L.BOARDS)
    s.add_argument("--replay", action="store_true")
    s.add_argument("--dt", type=float, default=0.5)
    s.set_defaults(fn=cmd_round)
    s = sub.add_parser("standings"); s.add_argument("--board", choices=L.BOARDS)
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_standings)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
