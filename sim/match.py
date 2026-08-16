# -*- coding: utf-8 -*-
"""sim/match.py -- 実戦（マッチ）の層

§4.1 の 3部隊・18人 と §8.3 の 3部隊戦 を組み立てる。**盤面（`field.simulate`）は
1部隊戦しか知らない。** ここはその上に乗るだけで、戦闘の中身には手を入れない。

================================================================================
 骨格
================================================================================
    登録 = 3部隊（低18 / 中30 / 高40）× 各6人 = 18人
      → 同一人物は3部隊を通じて1枚まで（別バージョンも不可）
      → 各部隊は合計コストがレギュレーション上限以下
      → 余剰コストは初期ゲージへ変わる（§4.5 案A）
    マッチ = 低 → 中 → 高 の順に3戦、独立に実行、2勝した側が勝者

**3戦は完全に独立**（§8.3）。兵力・ゲージ・状態効果を戦間で持ち越さない。ここでは
毎戦 `field.build` が新しい `Unit` を作るので、持ち越しは構造的に起こらない。

================================================================================
 なぜ「マッチの勝率」で測らないか
================================================================================
1部隊戦の勝敗は決定論で、総コスト +0.1点で 100% に飽和する（§5.3・実測）。3戦の
多数決はその飽和を**さらに急にする**だけなので、マッチの勝率は釣り合いの指標として
使えない。マッチ層で測るときも単位は**コスト点**（`field.cost_yardstick`）である。

ここで新しく測る価値があるのは、1部隊戦には無い軸だけ:

  - 3部隊への戦力の配り方（集中 対 分散）
  - 余剰コストの値段（ゲージへ変える案Aが、コスト点として何点ぶんか）
"""

from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from . import field as F

# レギュレーション（§4.1）。**実行順序は低 → 中 → 高で固定**（§8.3）。
REGULATIONS: Tuple[Tuple[str, float], ...] = (
    ("低コスト戦", 18.0),
    ("中コスト戦", 30.0),
    ("高コスト戦", 40.0),
)
UNIT_SIZE = 6

# 余剰コストの変換（§4.5 案A）。余り1点につき**その札のゲージ最大値の2%**を
# 初期ゲージへ足す。上限10%。固定量ではなく最大値に対する割合なので、消費ゲージが
# 3段（75/150/300）に分かれていても同じだけ前倒しになる。
SURPLUS_PER_COST = 0.02
SURPLUS_CAP = 0.10


@dataclass(frozen=True)
class Entry:
    """1人ぶんの登録。3部隊を低・中・高の順に持つ。"""
    units: Tuple[F.Army, F.Army, F.Army]
    name: str = ""

    def unit(self, i: int) -> F.Army:
        return self.units[i]


def person_of(card: F.Card) -> str:
    """同一人物の判定キー。**別バージョンも同じ人物として弾く**（§4.1）。

    カードの `name` は「関羽〔漢寿亭侯〕」のように人物＋字号なので、字号を落とす。
    CSV の「人物」列と同じ値になる。
    """
    n = card.name or ""
    return n.split("〔")[0] if "〔" in n else n


def validate(entry: Entry) -> List[str]:
    """登録の不備を全部返す。**空リストなら登録できる。**

    1つ見つけて止めない。3部隊を組んでいるプレイヤーに「まだ他にもある」を
    小出しにすると直しきれない。
    """
    errs: List[str] = []
    if len(entry.units) != len(REGULATIONS):
        errs.append("部隊は{}つ必要（いまは{}つ）".format(
            len(REGULATIONS), len(entry.units)))
        return errs
    seen: Dict[str, str] = {}
    for army, (label, cap) in zip(entry.units, REGULATIONS):
        if len(army.cards) != UNIT_SIZE:
            errs.append("{}: {}人必要（いまは{}人）".format(
                label, UNIT_SIZE, len(army.cards)))
        cost = army.total_cost()
        if cost > cap + 1e-9:
            errs.append("{}: 合計コスト {:g} が上限 {:g} を超えている".format(
                label, cost, cap))
        for c in army.cards:
            p = person_of(c)
            if p in seen:
                errs.append("{}: {} は {} と同一人物（別バージョンも不可）".format(
                    label, c.name, seen[p]))
            else:
                seen[p] = c.name
    return errs


def surplus_ratio(army: F.Army, cap: float) -> float:
    """余剰コストが初期ゲージへ変わる割合（ゲージ最大値に対する率）。"""
    left = max(cap - army.total_cost(), 0.0)
    return min(left * SURPLUS_PER_COST, SURPLUS_CAP)


def with_surplus(army: F.Army, cap: float) -> F.Army:
    """余剰コストを初期ゲージへ入れた軍を返す（§4.5 案A）。

    **元の Army は書き換えない。** 同じ登録で何度も対戦させるので、その場で
    足すと2戦目以降にゲージが積み増される（回復の実装で踏んだのと同じ形）。
    """
    r = surplus_ratio(army, cap)
    if r <= 0.0:
        return army
    cards = tuple(
        dataclasses.replace(c, gauge_init=min(
            c.gauge_init + r * c.gauge_cost, c.gauge_cost))
        for c in army.cards)
    return dataclasses.replace(army, cards=cards)


def play(a: Entry, b: Entry, dt: float = 0.5) -> Dict:
    """1マッチ。3戦を独立に走らせ、2勝した側を勝者にする（§8.3）。"""
    games = []
    wa = wb = 0.0
    for i, (label, cap) in enumerate(REGULATIONS):
        ua = with_surplus(a.unit(i), cap)
        ub = with_surplus(b.unit(i), cap)
        r = F.simulate(ua, ub, dt)
        wa += r["score"]
        wb += 1.0 - r["score"]
        games.append({
            "規定": label, "上限": cap, "結果": r,
            "コストA": a.unit(i).total_cost(), "コストB": b.unit(i).total_cost(),
            "余剰A": surplus_ratio(a.unit(i), cap),
            "余剰B": surplus_ratio(b.unit(i), cap),
        })
    return {"games": games, "wins_a": wa, "wins_b": wb,
            "winner": "A" if wa > wb else ("B" if wb > wa else "引き分け"),
            # 3戦の差の合計。**勝敗の数より先にこちらを見る**（勝率は飽和する）。
            "diff": sum(g["結果"]["diff"] for g in games)}


def match_yardstick(entry: Entry, dt: float = 0.5) -> float:
    """マッチ全体で「総コスト1点」が差の合計に何点ぶん効くかを測る。

    1部隊戦の `field.cost_yardstick` と同じ考え方だが、**3戦ぶんを足した量**に
    対する物差しなので別に測る。これを使わずに1部隊戦の物差しで割ると、3倍ずれる。
    """
    base = play(entry, entry, dt)["diff"]
    up = dataclasses.replace(entry, units=tuple(
        dataclasses.replace(u, cards=tuple(
            dataclasses.replace(c, cost=c.cost + 1.0 / UNIT_SIZE,
                                stat_cost=(c.stat_cost or c.cost) + 1.0 / UNIT_SIZE)
            for c in u.cards)) for u in entry.units))
    return play(up, entry, dt)["diff"] - base


# ============================================================================
# 登録を組む（測定用）
# ============================================================================

def sample_entry(cards: Sequence[F.Card], offset: int = 0,
                 forms: Sequence[F.Formation] = ()) -> Entry:
    """コスト上限を守って 18人 を選ぶ。**測定用の組み立てで、強さは狙わない。**

    低い上限から順に埋める。上限の緩い部隊から埋めると高コスト札を先に取られて、
    低コスト戦が組めなくなる（実際に組めなくなった）。
    """
    pool = sorted(cards, key=lambda c: (c.cost, c.name))
    used: set = set()
    units: List[F.Army] = []
    forms = tuple(forms) or (F.FORM_STANDARD,) * len(REGULATIONS)
    for (label, cap), form in zip(REGULATIONS, forms):
        pick: List[F.Card] = []
        # 平均でならした目安に近い札から取り、残り枠ぶんの最小コストを確保する
        want = cap / UNIT_SIZE
        cand = [c for c in pool if person_of(c) not in used]
        cand.sort(key=lambda c: (abs(c.cost - want), c.cost, c.name))
        if cand and offset:
            offset %= len(cand)
            cand = cand[offset:] + cand[:offset]
        # **残り枠の最小コストは1回だけ作る。** ここは候補ごとに pool 全体を
        # sort し直していて、札が増えると枚数の2乗で効いた。カードは増える前提
        # なので、安い順の配列を1本持って先頭から数える形にする。
        cheap = [c.cost for c in pool if person_of(c) not in used]
        cheap.sort()
        def floor_for(c: F.Card, left: int) -> float:
            """自分を除いた安い順 left 枚の合計。cheap は昇順なので線形で足りる。"""
            if left <= 0:
                return 0.0
            tot, n, skipped = 0.0, 0, False
            for x in cheap:
                if not skipped and x == c.cost:
                    skipped = True
                    continue
                tot += x
                n += 1
                if n >= left:
                    break
            return tot
        for c in cand:
            if len(pick) >= UNIT_SIZE:
                break
            if person_of(c) in used:
                continue
            left = UNIT_SIZE - len(pick) - 1
            if sum(p.cost for p in pick) + c.cost + floor_for(c, left) > cap + 1e-9:
                continue
            pick.append(c)
            used.add(person_of(c))
            cheap.remove(c.cost)
        units.append(F.Army(tuple(pick), form))
    return Entry(tuple(units))


def _roster_cards() -> List[F.Card]:
    from . import rosterdata as R
    R.load_skills_into_field()
    R.load_traits_into_field()
    F.SKILLS_ON = F.TRAITS_ON = True
    return list(R.to_cards())


def cmd_zero(args) -> None:
    """マッチの零点。**同じ登録どうしなら差はぴったり 0 でなければならない。**

    1部隊戦の零点（`field zero`）が 0 でも、マッチ層で 0 になるとは限らない。
    余剰コストの適用や部隊の並べ方を左右で別々に触っていれば、ここで出る。
    """
    cards = _roster_cards()
    print("マッチの零点（同じ登録どうし。0.00 でなければ左右非対称）")
    print()
    print("  {:<10}".format("刻み") + "".join("{:>12}".format(n)
                                              for n, _ in REGULATIONS)
          + "{:>12}{:>10}".format("合計の差", "勝敗"))
    for dt in (1.0, 0.5, 0.25):
        for off in (0, 5):
            e = sample_entry(cards, off)
            r = play(e, e, dt)
            row = "  dt={:<7g}".format(dt)
            row += "".join("{:>+12.4f}".format(g["結果"]["diff"])
                           for g in r["games"])
            print(row + "{:>+12.4f}{:>10}".format(
                r["diff"], "{:g}-{:g}".format(r["wins_a"], r["wins_b"])))


def cmd_entry(args) -> None:
    """登録の中身と検証。**組めることを目で見るための出力。**"""
    cards = _roster_cards()
    e = sample_entry(cards, args.offset)
    errs = validate(e)
    print("登録の例（offset={}）".format(args.offset))
    for army, (label, cap) in zip(e.units, REGULATIONS):
        r = surplus_ratio(army, cap)
        print()
        print("  {}（上限 {:g}）  合計 {:g}  余剰 {:g} → 初期ゲージ +{:.0f}%".format(
            label, cap, army.total_cost(), cap - army.total_cost(), r * 100))
        for c in army.cards:
            print("    {:<22}コスト{:<4g}{:<6}{}".format(
                c.name, c.cost, F.TYPE_JP[c.typ], c.skill))
    print()
    print("  検証: " + ("OK（登録できる）" if not errs else "NG"))
    for m in errs:
        print("    " + m)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="実戦（マッチ）の層")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("zero"); s.set_defaults(fn=cmd_zero)
    s = sub.add_parser("entry"); s.add_argument("--offset", type=int, default=0)
    s.set_defaults(fn=cmd_entry)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    import sys as _sys
    _sys.modules.setdefault("sim.match", _sys.modules[__name__])
    main()
