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
    Persona("鉄壁", {F.INF: 3.0, F.CAV: 1.0, F.ARC: 1.0},
            {F.TANK: 3.0, F.SUP: 1.5, F.BAL: 1.0, F.DPS: 0.5, F.BURST: 0.3},
            "狭く深い", 0.3),
    Persona("疾風", {F.CAV: 3.5, F.INF: 1.0, F.ARC: 0.5},
            {F.BURST: 3.0, F.DPS: 2.0, F.BAL: 1.0, F.TANK: 0.5, F.SUP: 0.3},
            "標準", 0.7),
    Persona("斉射", {F.ARC: 3.5, F.INF: 1.2, F.CAV: 0.4},
            {F.DPS: 2.5, F.SUP: 2.0, F.BAL: 1.0, F.TANK: 0.8, F.BURST: 0.5},
            "広く浅い", 0.5),
    Persona("均衡", {F.INF: 1.0, F.CAV: 1.0, F.ARC: 1.0},
            {F.BAL: 2.0, F.TANK: 1.0, F.DPS: 1.0, F.SUP: 1.0, F.BURST: 1.0},
            "標準", 0.5),
    Persona("軍師", {F.ARC: 2.5, F.INF: 1.5, F.CAV: 0.6},
            {F.SUP: 3.0, F.BAL: 1.5, F.TANK: 1.0, F.DPS: 0.8, F.BURST: 0.4},
            "狭く深い", 0.4),
    Persona("猛攻", {F.CAV: 2.0, F.INF: 2.0, F.ARC: 0.5},
            {F.DPS: 3.0, F.BURST: 2.5, F.BAL: 0.8, F.TANK: 0.4, F.SUP: 0.2},
            "広く浅い", 0.8),
)


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
        want = cap / M.UNIT_SIZE
        pick: List[F.Card] = []
        cand = [c for c in pool if M.person_of(c) not in used]
        cheap = sorted(c.cost for c in cand)
        while len(pick) < M.UNIT_SIZE and cand:
            left = M.UNIT_SIZE - len(pick) - 1
            spent = sum(x.cost for x in pick)
            ok = []
            for c in cand:
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
        units.append(F.Army(tuple(pick), FORM_BY_NAME[p.form]))
    return M.Entry(tuple(units), name=p.name)


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
