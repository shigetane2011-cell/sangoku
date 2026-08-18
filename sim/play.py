# -*- coding: utf-8 -*-
"""sim/play.py -- 実プレイヤーの導線（まず1人で回す・§7.40）

登録 → デッキを組む → 検証 → ランク戦の巡へ参加 → リプレイと順位を見る、を
CLI で通す。盤面・マッチ・順位表の実装は field/match/ladder をそのまま使い、
ここは**配線だけ**を持つ（同じ量の定義を2箇所に持たない）。

  python3 -m sim.play register --name 自分 --email you@example.com
  python3 -m sim.play roster --reg 汜水関
  python3 -m sim.play deck --player <id> --reg 汜水関 \\
      --cards "張飛、許褚、趙雲、黄忠、夏侯淵" --form 狭く深い
  python3 -m sim.play status --player <id>
  python3 -m sim.play round --player <id> [--board 汜水関] [--replay]
  python3 -m sim.play standings [--board 汜水関]

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


def _apply_onsho(cx, player_id: str, army: F.Army) -> Tuple[F.Army, float]:
    """セット済みの恩賞（軍功枠・§7.43）を札へ合流し、値段の合計を返す。

    盤面は card.trait の「、」区切りを複数特性として読む（§7.37）。値段は
    実測表 `design.trait_value` — **恩賞はコスト外のただ足しにはしない**。
    デッキの上限判定にこの値段を加える（生まれつきの特性は効果予算で支払い
    済みなので数えない）。"""
    import dataclasses
    from . import design as D
    extra = 0.0
    out = []
    for c in army.cards:
        keys = [k for k in P.traits_on(cx, player_id, c.name)
                if k not in F.trait_keys(c.trait)]
        if keys:
            extra += sum(D.trait_value(k) for k in keys)
            c = dataclasses.replace(
                c, trait=F.TRAIT_SEP.join(list(F.trait_keys(c.trait)) + keys))
        out.append(c)
    return dataclasses.replace(army, cards=tuple(out)), extra


class BoardEntry:
    """レギュレーションごとの部隊の入れ物（§7.48）。

    M.Entry は3部隊固定（BO3 の登録の器）だが、**1デッキでも BO1 には
    出られる**ようにする。unit(i) だけ互換にしてあり、盤面の解決・リプレイは
    どちらの器でも動く。天下(BO3) は3つ揃った時だけ。
    """

    def __init__(self, units: Dict[int, F.Army], name: str = ""):
        self.units_map = units
        self.name = name

    def unit(self, i: int) -> F.Army:
        return self.units_map[i]

    @property
    def units(self):
        return tuple(self.units_map[i] for i in range(len(M.REGULATIONS)))


def entry_of(cx, cards, player_id: str, name: str
             ) -> Tuple[BoardEntry, Dict[str, bool], List[str]]:
    """登録から (部隊の入れ物, 盤面ごとの可否, 不備一覧) を作る。

    デッキごとに独立して検証し、valid なデッキの盤面だけ出られる。
    **同一人物は登録デッキ全体で1枚**（§4.1）— BO1 どうしでも共有しない。
    関羽をどの帯で使うかという配分が編成の骨になっているため。
    """
    decks = P.decks_of(cx, player_id)
    units: Dict[int, F.Army] = {}
    reg_errs: Dict[int, List[str]] = {}
    errs: List[str] = []
    for i, (reg, cap) in enumerate(M.REGULATIONS):
        if reg not in decks:
            continue
        raw, form_name = decks[reg]
        army, es = parse_deck(cards, raw, form_name)
        if army is not None and not es:
            army, extra = _apply_onsho(cx, player_id, army)
            if len(army.cards) != M.UNIT_SIZE:
                es.append("{}人必要（いまは{}人）".format(
                    M.UNIT_SIZE, len(army.cards)))
            base = sum(c.cost for c in army.cards)
            if base > cap + 1e-9:
                es.append("合計コスト {:g} が上限 {:g} を超えている".format(base, cap))
            elif extra > 0.0 and base + extra > cap + 1e-9:
                es.append("恩賞の重み {:.2f} を足すと上限 {:g} を超える"
                          "（素 {:g} + 恩賞 {:.2f}）".format(extra, cap, base, extra))
            es += M.placement_errors(army)
            # 本陣（§7.52）はデッキに1人まで。生まれつき＋恩賞の合流後に数える。
            honjin = [c for c in army.cards
                      if "command" in F.trait_keys(c.trait)]
            if len(honjin) > 1:
                es.append("本陣は1部隊に1人まで（いまは {}）"
                          .format("、".join(c.name for c in honjin)))
            # 本陣は弓兵（＝後衛）専用。前衛の本陣は実測でほぼ必ず討たれる
            # （§7.52・戦死74〜89%）ので、恩賞でのセットも含めて registration
            # で弾く。生まれつきの3人（袁紹・費禕・劉表）は全員弓兵。
            jp_typ = {F.INF: "歩兵", F.CAV: "騎兵", F.ARC: "弓兵"}
            for c in honjin:
                if c.typ != F.ARC:
                    es.append("本陣は弓兵にだけ授けられる（{} は{}）"
                              .format(c.name, jp_typ.get(c.typ, c.typ)))
        if es:
            reg_errs[i] = es
            errs += ["{}: {}".format(reg, e) for e in es]
        elif army is not None:
            units[i] = army
    # 同一人物の重複は、関わる盤面をどちらも塞ぐ（登録レベルの規則）
    seen: Dict[str, Tuple[int, str]] = {}
    dup_regs: set = set()
    for i, army in units.items():
        for c in army.cards:
            p = M.person_of(c)
            if p in seen and seen[p][0] != i:
                errs.append("{}: {} は {} の {} と同一人物（別バージョンも不可）"
                            .format(M.REGULATIONS[i][0], c.name,
                                    M.REGULATIONS[seen[p][0]][0], seen[p][1]))
                dup_regs |= {i, seen[p][0]}
            else:
                seen[p] = (i, c.name)
    ok = {}
    for i, (reg, _) in enumerate(M.REGULATIONS):
        ok[reg] = i in units and i not in dup_regs
    ok["天下"] = all(ok[r] for r, _ in M.REGULATIONS)
    playable = {i: a for i, a in units.items() if i not in dup_regs}
    return BoardEntry(playable, name=name), ok, errs


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


def _canon_reg(name):
    return M.REG_ALIAS.get(name, name) if name else name


def cmd_roster(args) -> None:
    gs = R.generals()
    args.reg = _canon_reg(args.reg)
    if args.reg:
        cap = dict(zip(REG_NAMES, (c for _, c in M.REGULATIONS)))[args.reg]
        print("{}（上限 {:g}点・6枚・弓は後衛だけ・同一人物は3部隊で1枚）"
              .format(args.reg, cap))
    for g in sorted(gs, key=lambda g: (-float(g["コスト"]), g["名前"])):
        print("  {:>2}点 {:<3} {:<14} 技:{} 特性:{}".format(
            g["コスト"], g["兵種"], g["名前"], g["必殺技"], g["固有特性"] or "-"))


def cmd_deck(args) -> None:
    cx = P.connect(args.db)
    args.reg = _canon_reg(args.reg)
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
    entry, ok, errs = entry_of(cx, cards, pl.id, pl.display_name)
    for e in errs:
        print("  - " + e)
    print("出られる順位表: " + ("・".join(b for b in L.BOARDS if ok.get(b)) or "なし"))
    for name in L.BOARDS:
        r = P.board_ratings(cx, name)
        if pl.id in r:
            b = load_board(cx, name)
            pids = list(r)
            rank = b.order(pids).index(pl.id) + 1
            print("  {:<10} 武名{:.0f}  {}位/{}人  {}戦".format(
                name, r[pl.id][0], rank, len(pids), r[pl.id][1]))


def all_human_entries(cx, cards) -> Dict[str, Tuple["BoardEntry", Dict[str, bool]]]:
    """人間全員の (部隊, 盤面可否)。告知は**資格のある全員**で組む（§7.48）。

    その巡の参加者だけで次巡を告知すると、一度あぶれた人間が告知に入れず
    **恒久的にはぶられ続ける**（実測）。"""
    out = {}
    for p in P.all_players(cx, kind=P.HUMAN):
        e, ok, _ = entry_of(cx, cards, p.id, p.display_name)
        if any(ok.values()):
            out[p.id] = (e, ok)
    return out


def full_board_entries(cx, dummies, humans, board_name: str):
    """その盤面の完全な参加者集合（在野 + 資格のある人間全員・偶数化つき）。"""
    ent = dict(dummies)
    for pid, (e, ok) in humans.items():
        if ok.get(board_name):
            ent[pid] = e
    if len(ent) % 2 == 1:
        b = load_board(cx, board_name)
        hum = set(humans)
        low = min((p for p in ent if p not in hum), key=lambda p: b.get(p),
                  default=None)
        if low is not None:
            del ent[low]
    return ent


def board_entries(cx, dummies, entry, ok, board_name: str, me_id: str):
    """その盤面の参加者を組む。**奇数なら最下位の在野を今巡休みにする**（§7.48）。

    奇数のまま組むと毎巡1人あぶれ、人間があぶれ得る（実測: 「出陣したのに
    自分だけ居ない」）。休むのは在野から選ぶ — 足場は人間のためにある。
    """
    ent = dict(dummies)
    if ok.get(board_name):
        ent[me_id] = entry
    if len(ent) % 2 == 1:
        b = load_board(cx, board_name)
        low = min((p for p in ent if p != me_id), key=lambda p: b.get(p),
                  default=None)
        if low is not None:
            del ent[low]
    return ent


def announce(cx, entries: Dict[str, M.Entry], board_name: str
             ) -> Tuple[int, List[Tuple[str, str]]]:
    """次の巡の組を告知する（§3: 組む→告知→編成期間→戦う）。

    既に告知済みならそれを返す。**告知後のデッキ変更は自由**（それがこの
    2段階の目的。相手の陣形を見て自陣を差し替える駆け引き）。組は変わらない。
    """
    rnd = P.board_round(cx, board_name)
    pairs = P.load_pairs(cx, board_name, rnd)
    if not pairs:
        b = load_board(cx, board_name)
        pairs = L.plan_round(b, list(entries), rnd)
        P.save_pairs(cx, board_name, rnd, pairs)
    return rnd, pairs


def run_round(cx, cards, entries: Dict[str, M.Entry], board_name: str,
              dt: float = 0.5) -> Tuple[L.Board, int, List[Tuple[str, str]]]:
    """1つの順位表で1巡回し、全対戦を記録する（CLI と Web の共通部）。

    告知済みの組があれば**その組で**戦う（組み直さない）。終わったらすぐ
    次の巡を告知するので、戦い終えた瞬間から次の相手が見える。
    """
    b = load_board(cx, board_name)
    rnd, pairs = announce(cx, entries, board_name)
    pairs = [(x, y) for x, y in pairs if x in entries and y in entries]
    L.resolve_round(b, pairs, entries, rnd, dt=dt)
    save_board(cx, b)
    P.bump_board_round(cx, board_name)
    P.clear_pairs(cx, board_name, rnd)
    for x, y in pairs:
        P.record_match(cx, board_name, rnd, x, y,
                       L.battle_seed(board_name, rnd, x, y))
    announce(cx, entries, board_name)      # 次の巡をすぐ告知
    return b, rnd, pairs


def cmd_round(args) -> None:
    cx = P.connect(args.db)
    cards = M._roster_cards()
    pl = P.get(cx, args.player)
    if pl is None:
        print("その id の登録者が居ない"); sys.exit(1)
    entry, ok, errs = entry_of(cx, cards, pl.id, pl.display_name)
    for e in errs:
        print("  - " + e)
    if not any(ok.values()):
        print("出られる順位表が無い（先にデッキを登録する）")
        sys.exit(1)
    dummies = ensure_dummies(cx, cards)
    humans_e = all_human_entries(cx, cards)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    boards = [_canon_reg(args.board)] if args.board else list(L.BOARDS)
    for name in boards:
        entries = full_board_entries(cx, dummies, humans_e, name)
        if not ok.get(name) and pl.id in entries:
            entries = {k: v for k, v in entries.items() if k != pl.id}
        b, rnd, pairs = run_round(cx, cards, entries, name, dt=args.dt)
        pids = list(entries)
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
        print("{:<10} 第{}巡: 対 {:<8} {} {}  → 武名{:.0f} {}位/{}人".format(
            name, rnd + 1, names.get(foe, foe), verdict, score,
            b.get(pl.id), rank, len(pids)))
        if args.replay and b.reg is not None:
            cap = M.REGULATIONS[b.reg][1]
            ua = M.with_surplus(entries[mine[0]].unit(b.reg), cap)
            ub = M.with_surplus(entries[mine[1]].unit(b.reg), cap)
            replay_one(ua, ub, args.dt, seed, me_first)
        elif args.replay:
            # BO3: 3戦を順に再生する。**種は play() と同じ導出**（seed*3+i・§8.3）。
            # ずらし方を変えると「リプレイが実際に起きた戦いと別物」になる。
            wa = wb = 0.0
            for i, (label, cap) in enumerate(M.REGULATIONS):
                ua = M.with_surplus(entries[mine[0]].unit(i), cap)
                ub = M.with_surplus(entries[mine[1]].unit(i), cap)
                g = F.simulate(ua, ub, args.dt, seed=seed * 3 + i)
                sc = g["score"] if me_first else 1.0 - g["score"]
                wa += sc
                wb += 1.0 - sc
                v = "勝ち" if sc > 0.5 else ("負け" if sc < 0.5 else "引き分け")
                print("    ▼ 第{}戦（{}） {}　累計 {:g}-{:g}".format(
                    i + 1, label, v, wa, wb))
                replay_one(ua, ub, args.dt, seed * 3 + i, me_first)


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


def draft_deck(cards, reg_name: str, form_name: str, style: str, typ: str,
               faction: str, seed: int, exclude_persons=()) -> Tuple[List[str], str]:
    """アンケートの回答から**たたき台**のデッキを組む（§7.54）。

    ダミーの編成器（dummies.make_entry）をそのまま使う — 回答を性格
    （役割・兵種の重み）へ写すだけで、規則（前衛は近接・後衛は弓・上限・
    同一人物）を破らない編成が出る。同じ量の定義を2箇所に持たない。

    **わざと少し弱く作る**: 上限の9割で組む。最強の答えを渡すと編成の探索が
    死ぬ（§7.47 の開示設計と同じ理由）。余った1割が「入れ替えて仕上げる」
    余白になる。seed を変えると引き直せる。
    """
    from . import dummies as DM
    role_w = {
        "力押し":  {F.TANK: 1.6, F.BAL: 1.6, F.DPS: 2.0, F.BURST: 0.5, F.SUP: 0.6},
        "必殺技":  {F.TANK: 0.6, F.BAL: 0.8, F.DPS: 1.3, F.BURST: 2.4, F.SUP: 1.4},
        "守り":    {F.TANK: 2.6, F.BAL: 1.0, F.DPS: 0.5, F.BURST: 0.4, F.SUP: 1.8},
    }.get(style, {F.TANK: 1.0, F.BAL: 1.2, F.DPS: 1.0, F.BURST: 0.8, F.SUP: 0.8})
    typ_w = ({t: 1.0 for t in (F.INF, F.CAV, F.ARC)} if typ not in F.TYPE_JP.values()
             else {t: (2.2 if F.TYPE_JP[t] == typ else 0.8)
                   for t in (F.INF, F.CAV, F.ARC)})
    greed = {"力押し": 0.6, "守り": 0.3}.get(style, 0.5)
    p = DM.Persona("たたき台", typ_w, role_w, form_name, greed)
    reg_i = next(i for i, (n, _) in enumerate(M.REGULATIONS) if n == reg_name)
    caps = tuple((n, round(c * 0.9)) for n, c in M.REGULATIONS)
    note = "上限の9割で組んだたたき台。入れ替えと段位上げで仕上げよう"

    def build(pool):
        try:
            e = DM.make_entry(pool, p, seed, caps=caps)
        except ValueError:
            # 候補が尽きて編成が立たない（勢力しばり等で池が痩せた）
            return None
        u = e.unit(reg_i)
        ok = (len(u.cards) == M.UNIT_SIZE
              and u.total_cost() <= M.REGULATIONS[reg_i][1] + 1e-9)
        return [c.name for c in u.cards] if ok else None

    pool = [c for c in cards if M.person_of(c) not in set(exclude_persons)]
    if faction in ("魏", "蜀", "呉", "群雄"):
        names = build([c for c in pool if c.faction == faction])
        if names is not None:
            return names, note
        note = "その勢力だけでは埋まらず、他勢力も混ぜた。" + note
    names = build(pool)
    if names is None:
        return [], "たたき台を組めなかった（他のデッキと人物が重なりすぎている）"
    return names, note


def battle_notes(ua, ub, r, series, me_first: bool) -> List[str]:
    """軍師の見立て（§9.5）。**記録からの読み取り専用**で、勝敗にも測定にも
    触れない（§9.3 の原則。実況・戦況図と同じ側）。

    出すのは最大3行: 山場（形勢が入れ替わった・差がいちばん開いた時刻と、
    そのとき崩れた隊）、それに勝因・敗因を高々2つ。原因は盤面が実際に持つ
    量からだけ引く — 本陣の陥落（cmd_fell）、兵種相性（type_edge・コスト点）、
    陣形の三すくみ（§7.39 実測の向き）、与ダメの内訳（通常/必殺）、早い崩れ。
    推測で語らない: どのしきい値も「言い切れる大きさ」にだけ反応させる。
    """
    mine_a, foe_a = (ua, ub) if me_first else (ub, ua)
    mine_r, foe_r = ((r["dealt_a"], r["dealt_b"]) if me_first
                     else (r["dealt_b"], r["dealt_a"]))
    sc = r["score"] if me_first else 1.0 - r["score"]
    won, lost = sc > 0.5, sc < 0.5
    gap = [(t, (ra - rb) if me_first else (rb - ra)) for t, ra, rb in series]
    notes: List[str] = []

    def who(row):
        return M.person_of(F.Card(0, row[1], name=row[0])) or F.TYPE_JP[row[1]]

    # ── 山場 ──────────────────────────────────────────────
    # 最後に符号が変わった時刻（逆転）。序盤のゆらぎは見ない（振れ幅で足切り）。
    if gap and (won or lost):
        sign = 1.0 if won else -1.0
        flip = None
        swing = max(abs(g) for _, g in gap)
        for (t0, g0), (t1, g1) in zip(gap, gap[1:]):
            if g0 * sign < 0.0 <= g1 * sign and abs(g0) > 0.02 and swing > 0.05:
                flip = t1
        # 差がいちばん動いた15秒（きっかけの時刻）。逆転が無ければこちら。
        peak, peak_t = 0.0, None
        j = 0
        for i, (t0, g0) in enumerate(gap):
            while j < len(gap) - 1 and gap[j][0] - t0 < 15.0:
                j += 1
            d = (gap[j][1] - g0) * sign
            if d > peak:
                peak, peak_t = d, (t0 + gap[j][0]) / 2.0
        t_key = flip if flip is not None else (peak_t if peak > 0.04 else None)
        if t_key is not None:
            # そのとき崩れた隊があれば名指しする（±12秒）。**向きに合う側を
            # 優先** — 勝ちへ振れた山場で自軍の崩れを名指しすると、崩れが
            # 勝因に読めてしまう。
            rows_jp = ((foe_r, "敵の") if won else (mine_r, ""),)
            near = [(jp, row) for rows, jp in rows_jp for row in rows
                    if row[6] is not None and abs(row[6] - t_key) < 12.0]
            hint = ""
            if near:
                side_jp, row = min(near, key=lambda x: abs(x[1][6] - t_key))
                hint = "。{}{}の隊が崩れたあたりである".format(side_jp, who(row))
            notes.append("山場は【{}】{}{}".format(
                F.clock(t_key), "、ここで形勢が入れ替わった" if flip is not None
                else "ごろ、ここでいちばん差が開いた", hint))

    # ── 勝因・敗因（高々2つ） ────────────────────────────────
    causes: List[str] = []
    mine_fell, foe_fell = ((r["cmd_fell"][0], r["cmd_fell"][1]) if me_first
                           else (r["cmd_fell"][1], r["cmd_fell"][0]))
    if mine_fell and lost:
        causes.append("敗因は本陣の陥落。これがすべてで、崩れるまでの優劣は関係が無い")
    elif foe_fell and won:
        causes.append("勝因は敵本陣の討ち取り。総崩れを誘って決めた")
    if len(causes) < 2 and (won or lost):
        te = F.type_edge(mine_a, foe_a)   # コスト点。正なら自軍が有利
        if abs(te) >= 0.7 and (te > 0) == won:
            causes.append("兵種の噛み合わせが{}効いた（コスト{:.1f}点ぶんの{}）".format(
                "こちらに" if won else "敵に", abs(te), "得" if won else "不利"))
    if len(causes) < 2 and (won or lost):
        beats = {3: 4, 4: 2, 2: 3}    # 魚鱗>鶴翼>雁行>魚鱗（§7.39 実測の向き）
        mf, ff = mine_a.form.n_front, foe_a.form.n_front
        if mf != ff:
            if beats.get(ff) == mf and lost:
                causes.append("陣形は敵の{}がこちらの{}に強い並びだった（三すくみ）".format(
                    F.FORM_NAME[ff], F.FORM_NAME[mf]))
            elif beats.get(mf) == ff and won:
                causes.append("陣形は{}が{}を食う並びを取れていた（三すくみ）".format(
                    F.FORM_NAME[mf], F.FORM_NAME[ff]))
    if len(causes) < 2 and (won or lost):
        m_n = sum(x[2] - x[5] for x in mine_r); f_n = sum(x[2] - x[5] for x in foe_r)
        m_s = sum(x[5] for x in mine_r); f_s = sum(x[5] for x in foe_r)
        dn, ds = (f_n - m_n) if lost else (m_n - f_n), (f_s - m_s) if lost else (m_s - f_s)
        base_n, base_s = max(m_n, f_n, 1.0), max(m_s, f_s, 1.0)
        if dn / base_n > 0.15 and dn >= ds:
            causes.append("通常攻撃の打ち合いで{}（{:.1f}万 対 {:.1f}万）".format(
                "押し負けた" if lost else "押し切った",
                m_n / 1e4, f_n / 1e4))
        elif ds / base_s > 0.15:
            causes.append("必殺技の応酬で{}（技の与ダメ {:.1f}万 対 {:.1f}万）".format(
                "撃ち負けた" if lost else "撃ち勝った", m_s / 1e4, f_s / 1e4))
    if len(causes) < 2 and lost and series:
        early = [row for row in mine_r
                 if row[6] is not None and row[6] < series[-1][0] * 0.45]
        if early:
            row = min(early, key=lambda x: x[6])
            causes.append("{}が【{}】と早くに崩れ、戦列が痩せたのも痛い".format(
                who(row), F.clock(row[6])))
    if not causes and r["reason"] == "time" and gap and abs(gap[-1][1]) < 0.05:
        causes.append("時間いっぱいの判定までもつれた際どい勝負で、明確な敗着は無い"
                      if lost else "時間いっぱいの判定までもつれた際どい勝負だった")
    notes += causes[:2]
    if not notes:
        notes.append("痛み分け。決め手を欠いたまま終わった" if sc == 0.5
                     else "終始おおきな山場のないまま決した")
    return notes


def replay_data(ua, ub, dt: float, seed: int, me_first: bool) -> dict:
    """1戦ぶんのリプレイを構造化して返す（Web用・§7.42）。

    実況行・形勢の時系列・戦果表・勝敗。CLI の replay_one と同じ素材から作る
    （同じ量の定義を2箇所に持たない: 行は narrate、数字は simulate の診断出力）。
    """
    lines = F.narrate(ua, ub, dt, seed=seed)
    series = []
    r = F.simulate(ua, ub, dt, seed=seed, series=series)
    step = max(1, len(series) // 240)
    mine, foe = (r["dealt_a"], r["dealt_b"]) if me_first else (r["dealt_b"], r["dealt_a"])
    def rows(xs):
        return [{"name": M.person_of(F.Card(0, t, name=n)) or F.TYPE_JP[t],
                 "typ": F.TYPE_JP[t], "dealt": round(d), "men": round(m),
                 "men0": round(m0), "skill_dealt": round(sd),
                 "fell": F.clock(fa) if fa is not None else None}
                for n, t, d, m, m0, sd, fa in xs]
    sc = r["score"] if me_first else 1.0 - r["score"]
    return {"lines": lines,
            "notes": battle_notes(ua, ub, r, series, me_first),
            "mine_names": [u.name for u in (ua if me_first else ub).cards if u.name],
            "foe_names": [u.name for u in (ub if me_first else ua).cards if u.name],
            "series": [[round(F.mins(t), 1),
                        round((ra - rb) if me_first else (rb - ra), 4)]
                       for t, ra, rb in series[::step]],
            "mine": rows(mine), "foe": rows(foe),
            "verdict": "勝ち" if sc > 0.5 else ("負け" if sc < 0.5 else "引き分け")}


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
    print("    ── 軍功帳（与ダメ=千人・残兵%） ──")
    for tag, rows in (("自軍", mine), ("敵軍", foe)):
        cells = []
        for name, typ, dealt, men, men0, sd, fa in rows:
            pct = 100.0 * men / men0 if men0 > 0 else 0.0
            state = "壊滅" if pct <= 0.5 else "{:.0f}%".format(pct)
            when = "・{}崩".format(F.clock(fa)) if fa is not None else ""
            cells.append("{}({}) 与{:.1f}(技{:.1f}) 残{}{}".format(
                M.person_of(F.Card(0, typ, name=name)) or F.TYPE_JP[typ],
                F.TYPE_JP[typ][0], dealt / 1000.0, sd / 1000.0, state, when))
        print("    {}: {}".format(tag, " / ".join(cells[:3])))
        if len(cells) > 3:
            print("          {}".format(" / ".join(cells[3:])))
    print("    ── 軍師の見立て ──")
    for n in battle_notes(ua, ub, r, series, me_first):
        print("    ・" + n)


def replay_one(ua, ub, dt: float, seed: int, me_first: bool) -> None:
    """1戦ぶんのリプレイ一式（実況・形勢グラフ・戦果表）。"""
    for line in F.narrate(ua, ub, dt, seed=seed):
        print("    " + line)
    print_report(ua, ub, dt, seed, me_first)


def cmd_next(args) -> None:
    """告知された次の対戦相手（と相手の陣形）を見る。"""
    cx = P.connect(args.db)
    cards = M._roster_cards()
    pl = P.get(cx, args.player)
    if pl is None:
        print("その id の登録者が居ない"); sys.exit(1)
    entry, ok, errs = entry_of(cx, cards, pl.id, pl.display_name)
    dummies = ensure_dummies(cx, cards)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    for bn in L.BOARDS:
        entries = dict(dummies)
        if ok.get(bn):
            entries[pl.id] = entry
        rnd, pairs = announce(cx, entries, bn)
        mine = next((pr for pr in pairs if pl.id in pr), None)
        if mine is None:
            print("{:<6} 第{}巡: （組に入っていない）".format(bn, rnd + 1))
            continue
        foe = mine[1] if mine[0] == pl.id else mine[0]
        fe = entries.get(foe)
        reg = REG_NAMES.index(bn) if bn in REG_NAMES else None
        if fe is None:
            forms = "?"
        elif reg is not None:
            forms = F.FORM_NAME.get(fe.unit(reg).form.n_front, "?")
        else:
            forms = "・".join(F.FORM_NAME.get(u.form.n_front, "?")
                              for u in fe.units)
        print("{:<6} 第{}巡: 対 {}（陣: {}）".format(
            bn, rnd + 1, names.get(foe, foe), forms))


def cmd_standings(args) -> None:
    cx = P.connect(args.db)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    kinds = {p.id: p.kind for p in P.all_players(cx)}
    for name in ([_canon_reg(args.board)] if args.board else list(L.BOARDS)):
        r = P.board_ratings(cx, name)
        if not r:
            print("{:<10} まだ誰も戦っていない".format(name))
            continue
        b = load_board(cx, name)
        pids = b.order(list(r))
        print("{}（{}人・{}巡）".format(name, len(pids), P.board_round(cx, name)))
        for i, pid in enumerate(pids[:args.limit], 1):
            tag = "" if kinds.get(pid) == P.HUMAN else "（在野）"
            print("  {:>2}位 {:<10}{} 武名{:.0f} {}戦".format(
                i, names.get(pid, pid), tag, b.get(pid), b.games.get(pid, 0)))


def main() -> None:
    p = argparse.ArgumentParser(description="実プレイヤーの導線")
    p.add_argument("--db", default=P.DB_PATH)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("register"); s.add_argument("--name", required=True)
    s.add_argument("--email", required=True); s.set_defaults(fn=cmd_register)
    s = sub.add_parser("roster"); s.add_argument("--reg", choices=REG_NAMES + tuple(M.REG_ALIAS))
    s.set_defaults(fn=cmd_roster)
    s = sub.add_parser("deck"); s.add_argument("--player", required=True)
    s.add_argument("--reg", choices=REG_NAMES + tuple(M.REG_ALIAS), required=True)
    s.add_argument("--cards", required=True)
    s.add_argument("--form", choices=tuple(FORM_BY_NAME), required=True)
    s.set_defaults(fn=cmd_deck)
    s = sub.add_parser("status"); s.add_argument("--player", required=True)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("round"); s.add_argument("--player", required=True)
    s.add_argument("--board", choices=L.BOARDS + tuple(M.REG_ALIAS))
    s.add_argument("--replay", action="store_true")
    s.add_argument("--dt", type=float, default=0.5)
    s.set_defaults(fn=cmd_round)
    s = sub.add_parser("next"); s.add_argument("--player", required=True)
    s.set_defaults(fn=cmd_next)
    s = sub.add_parser("standings"); s.add_argument("--board", choices=L.BOARDS + tuple(M.REG_ALIAS))
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_standings)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
