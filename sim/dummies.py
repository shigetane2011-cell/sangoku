# -*- coding: utf-8 -*-
"""sim/dummies.py -- ダミー登録者（足場）

プレイヤーが居ない間、ランク戦を回すための相手。**足場であって常設ではない**
（人が増えたら比率を下げて抜く）。抜く前提なので、ダミーに依存した仕組みを
ゲーム側へ作らないこと。

================================================================================
 性格を持たせる理由
================================================================================
同じ作り方で18人を選ぶと、ダミーが全部似た編成になる。それでは
**三すくみも陣形も試されない**ので、ラダーが「たまたま強い1つの型」だけを
映す表になる。兵種・役割・陣形の好みを性格として持たせ、偏りを作る。

強さを揃える必要はない。**弱い性格が下位に沈むのは正しい**（それが測れている
ことの証拠でもある）。揃えるのはコスト上限だけで、それは登録の検証が見る。

================================================================================
 編成を毎時変えるか
================================================================================
**変えない。** 当初は「変えないとラダーが固まる」と考えていたが、盤面へ乱数を
入れた（§7.26）ので同じ編成どうしでも結果がばらける。ダミーに編成変更まで
持たせると、**何がレートを動かしたのかが分からなくなる**（編成か、運か）。
足場としては固定でよい。偵察（§3.2）の読み合いを試したくなった時点で足す。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from . import field as F
from . import match as M

# 性格。兵種と役割の重み、陣形の好み。**強さは揃えない。**
FORM_BY_NAME = {"標準": F.FORM_STANDARD, "広く浅い": F.FORM_WIDE,
                "狭く深い": F.FORM_DEEP}


@dataclass(frozen=True)
class Persona:
    name: str
    typ_w: Dict[str, float]     # 兵種の重み
    role_w: Dict[str, float]    # 役割の重み
    form: str                   # 陣形の好み
    greed: float = 0.5          # 高コストへ寄せる度合い（0=薄く広く, 1=集中）


PERSONAS: Tuple[Persona, ...] = (
    Persona("鉄壁", {F.INF: 1.8, F.CAV: 0.9, F.ARC: 0.9},
            {F.TANK: 2.5, F.SUP: 1.5, F.BAL: 1.0, F.DPS: 0.6, F.BURST: 0.4},
            "狭く深い", 0.3),
    Persona("疾風", {F.CAV: 1.8, F.INF: 1.0, F.ARC: 0.7},
            {F.BURST: 2.5, F.DPS: 2.0, F.BAL: 1.0, F.TANK: 0.6, F.SUP: 0.4},
            "標準", 0.7),
    Persona("斉射", {F.ARC: 1.8, F.INF: 1.1, F.CAV: 0.7},
            {F.DPS: 2.0, F.SUP: 2.0, F.BAL: 1.0, F.TANK: 0.9, F.BURST: 0.6},
            "広く浅い", 0.5),
    Persona("均衡", {F.INF: 1.0, F.CAV: 1.0, F.ARC: 1.0},
            {F.BAL: 2.0, F.TANK: 1.0, F.DPS: 1.0, F.SUP: 1.0, F.BURST: 1.0},
            "標準", 0.5),
    Persona("軍師", {F.ARC: 1.5, F.INF: 1.2, F.CAV: 0.8},
            {F.SUP: 2.5, F.BAL: 1.5, F.TANK: 1.0, F.DPS: 0.9, F.BURST: 0.5},
            "狭く深い", 0.4),
    Persona("猛攻", {F.CAV: 1.4, F.INF: 1.4, F.ARC: 0.7},
            {F.DPS: 2.5, F.BURST: 2.0, F.BAL: 0.9, F.TANK: 0.5, F.SUP: 0.3},
            "広く浅い", 0.8),
)
# **兵種は「寄せる」程度にとどめる。** 最初は 3.5倍まで重みを付けていたが、
# それだと単一兵種に近い編成ができ、相性だけで 0%/100% に振り切れた。さらに
# 純兵種どうしは膠着して時間切れになる（騎6対弓6 で diff=-0.037・t=400、
# 1枚ずつ歩兵を混ぜると diff=+0.127・245で決着。**1枚でコスト7.7点ぶん動く**）。
# 乱数 σ=0.15 のばらつきは 0.0156 なので、±0.05 を超える差はもう覆らない。
# 乱数は僅差を面白くするものであって、大差を覆すものではない。


def _score(card: F.Card, p: Persona, want: float) -> float:
    """性格から見た札の好ましさ。**コストの近さも重みに混ぜる。**"""
    w = p.typ_w.get(card.typ, 0.3) * p.role_w.get(card.role, 0.3)
    # greed が高いほど「目安より高コスト」を好む
    d = abs(card.cost - want) / max(want, 1e-6)
    return w * (1.0 + p.greed * (card.cost / max(want, 1e-6) - 1.0)) / (1.0 + d)


def make_entry(cards: Sequence[F.Card], p: Persona, seed: int) -> M.Entry:
    """性格に沿って 3部隊18人を選ぶ。**コスト上限と別人物は必ず守る。**

    重み付きで選ぶが、選んだあとに上限を超えないかを必ず見る。超える候補は
    飛ばす。**「選んでから直す」ではなく「入るものだけ選ぶ」**にしておくと、
    検証が落ちる編成が出来上がらない。
    """
    rng = random.Random("{}/{}".format(p.name, seed))
    pool = sorted(cards, key=lambda c: (c.cost, c.name))
    used: set = set()
    units: List[F.Army] = []
    for label, cap in M.REGULATIONS:
        pick: List[F.Card] = []
        caps = _type_caps(FORM_BY_NAME[p.form].n_front)
        cand = [c for c in pool if M.person_of(c) not in used]
        cheap = sorted(c.cost for c in cand)
        while len(pick) < M.UNIT_SIZE and cand:
            left = M.UNIT_SIZE - len(pick) - 1
            spent = sum(x.cost for x in pick)
            # **残り予算を残り枠で割り直す。** 固定の目安（上限÷6）だと、性格の
            # 偏りが強い札から先に取ったときに安い側へ寄って予算が余る。
            want = (cap - spent) / max(M.UNIT_SIZE - len(pick), 1)
            have = {t: sum(1 for x in pick if x.typ == t) for t in (F.CAV, F.ARC)}
            ok = []
            for c in cand:
                if c.typ in caps and have[c.typ] >= caps[c.typ]:
                    continue        # あり得ない配置になる兵種は取らない
                # 自分を除いた安い順 left 枚を確保できるか（残り枠が埋まるか）
                rest = list(cheap)
                rest.remove(c.cost)
                if spent + c.cost + sum(rest[:left]) <= cap + 1e-9:
                    ok.append(c)
            if not ok:
                break
            w = [max(_score(c, p, want), 1e-6) for c in ok]
            c = rng.choices(ok, weights=w, k=1)[0]
            pick.append(c)
            used.add(M.person_of(c))
            cheap.remove(c.cost)
            cand = [x for x in cand if M.person_of(x) not in used]
        nf = FORM_BY_NAME[p.form].n_front
        pick = _spend_rest(pick, pool, used, p, cap, caps)
        units.append(F.Army(tuple(_order(pick, nf)), FORM_BY_NAME[p.form]))
    return M.Entry(tuple(units), name=p.name)


def _order(pick: List[F.Card], n_front: int) -> List[F.Card]:
    """部隊内の並び。**先頭から前衛に置かれる**ので、あり得ない配置を作らない。

    `_stations` は Army.cards の先頭 n_front 枚を前衛に置く。生成した順（性格の
    重み順）のまま渡すと**瞬発や支援が最前列に立ち**、弓兵が前、騎兵が後ろに
    並ぶ。実際のプレイヤーはそんな置き方をしない。

      前衛 … 騎兵と歩兵。硬い順（`field.ROLE_MEN`）
      後衛 … 弓兵と、前衛に入りきらなかった歩兵

    **弓を前に出さない**（射程を活かせない）。**騎兵を後ろに置かない**（突撃と
    迂回が死ぬ）。枚数の制約は `_type_caps` が生成の段階で保証する。
    """
    front = [c for c in pick if c.typ != F.ARC]
    rear = [c for c in pick if c.typ == F.ARC]
    front.sort(key=lambda c: (-F.ROLE_MEN[c.role], c.name))
    rear.sort(key=lambda c: (-F.ROLE_MEN[c.role], c.name))
    # 前衛が余ったら後ろへ回す（回すのは歩兵。騎兵は前に残す）
    while len(front) > n_front:
        spare = [c for c in front if c.typ == F.INF] or front
        c = spare[-1]
        front.remove(c)
        rear.insert(0, c)
    # 前衛が足りなければ後衛から歩兵を戻す
    while len(front) < n_front and rear:
        cand = [c for c in rear if c.typ != F.ARC] or rear
        c = cand[0]
        rear.remove(c)
        front.append(c)
    front.sort(key=lambda c: (-F.ROLE_MEN[c.role], c.name))
    return front + rear


def _type_caps(n_front: int) -> Dict[str, int]:
    """兵種の上限。**あり得ない配置が構造的に作れないようにする。**

    騎兵 ≤ 前衛の数、弓兵 ≤ 後衛の数。この2つを守れば、騎兵を全部前に、弓兵を
    全部後ろに置ける（残りは歩兵で埋まる）。片方だけでは足りない。
    """
    return {F.CAV: n_front, F.ARC: M.UNIT_SIZE - n_front}


def _spend_rest(pick: List[F.Card], pool: Sequence[F.Card], used: set,
                p: Persona, cap: float, caps: Dict[str, int]) -> List[F.Card]:
    """余った予算で札を入れ替える。**使い残しはラダーの測定を壊す。**

    実測で斉射が合計8点、軍師が4点を余らせていた。8点は兵種相性（0.85点）の
    9個ぶんで、これでは順位が「性格の強さ」ではなく「生成器の無駄」を映す。
    余剰は初期ゲージへ変わる（§4.5 案A）が上限が +10% なので、埋め合わせに
    ならない。

    安い札から順に、**同じ性格の好みで、より高くて枠に収まる札**へ入れ替える。
    改善が無くなるまで繰り返す。
    """
    for _ in range(M.UNIT_SIZE * 2):
        left = cap - sum(x.cost for x in pick)
        if left < 1.0:
            break
        i = min(range(len(pick)), key=lambda k: pick[k].cost)
        cur = pick[i]
        room = cur.cost + left
        have = {t: sum(1 for x in pick if x.typ == t and x is not cur)
                for t in (F.CAV, F.ARC)}
        cands = [c for c in pool
                 if M.person_of(c) not in used and c.cost <= room + 1e-9
                 and c.cost > cur.cost
                 and not (c.typ in caps and have[c.typ] >= caps[c.typ])]
        if not cands:
            break
        best = max(cands, key=lambda c: (c.cost, _score(c, p, room)))
        used.discard(M.person_of(cur))
        used.add(M.person_of(best))
        pick[i] = best
    return pick


def seed_ladder(cx, cards: Sequence[F.Card], n: int = 24
                ) -> List[Tuple[str, Persona, M.Entry]]:
    """ダミーを n 体登録し、それぞれの編成を作る。

    **メールは予約ドメイン**（`players.DUMMY_DOMAIN`）。足場を撤去し忘れても
    実在アドレスへは届かない。
    """
    from . import players as P
    out = []
    for i in range(n):
        p = PERSONAS[i % len(PERSONAS)]
        name = "{}{:02d}".format(p.name, i // len(PERSONAS) + 1)
        pl = P.register(cx, name, kind=P.DUMMY,
                        email="dummy{:03d}@{}".format(i, P.DUMMY_DOMAIN))
        out.append((pl.id, p, make_entry(cards, p, i)))
    return out
