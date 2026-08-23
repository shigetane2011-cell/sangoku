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
FORM_BY_NAME = {"魚鱗": F.FORM_STANDARD, "鶴翼": F.FORM_WIDE,
                "雁行": F.FORM_DEEP}


@dataclass(frozen=True)
class Persona:
    name: str
    typ_w: Dict[str, float]     # 兵種の重み
    role_w: Dict[str, float]    # 役割の重み
    form: str                   # 陣形の好み
    greed: float = 0.5          # 高コストへ寄せる度合い（0=薄く広く, 1=集中）


PERSONAS: Tuple[Persona, ...] = (
    # **陣形と兵種の好みを噛み合わせる。** 規則（後衛は弓兵だけ）が入ったので、
    # 陣形が弓兵の枚数を決める。弓兵を好む性格に「広く浅い（弓2）」を持たせると、
    # **好きな兵種を置けない編成**になる。実測で斉射の平均勝率が 16% と、他5性格
    # （43〜73%）から外れていた。陣形は「その性格が何を主役にしたいか」で選ぶ。
    #
    #   雁行（弓4） … 射撃が主役      魚鱗（弓3） … 近接と射撃が半々
    #   鶴翼（弓2） … 近接が主役
    Persona("鉄壁", {F.INF: 1.8, F.CAV: 0.9, F.ARC: 0.9},
            {F.TANK: 2.5, F.SUP: 1.5, F.BAL: 1.0, F.DPS: 0.6, F.BURST: 0.4},
            "魚鱗", 0.3),
    Persona("疾風", {F.CAV: 1.8, F.INF: 1.0, F.ARC: 0.7},
            {F.BURST: 2.5, F.DPS: 2.0, F.BAL: 1.0, F.TANK: 0.6, F.SUP: 0.4},
            "鶴翼", 0.7),
    Persona("斉射", {F.ARC: 1.8, F.INF: 1.1, F.CAV: 0.7},
            {F.DPS: 2.0, F.SUP: 2.0, F.BAL: 1.0, F.TANK: 0.9, F.BURST: 0.6},
            "雁行", 0.5),
    Persona("均衡", {F.INF: 1.0, F.CAV: 1.0, F.ARC: 1.0},
            {F.BAL: 2.0, F.TANK: 1.0, F.DPS: 1.0, F.SUP: 1.0, F.BURST: 1.0},
            "魚鱗", 0.5),
    Persona("軍師", {F.ARC: 1.5, F.INF: 1.2, F.CAV: 0.8},
            {F.SUP: 2.5, F.BAL: 1.5, F.TANK: 1.0, F.DPS: 0.9, F.BURST: 0.5},
            "雁行", 0.4),
    Persona("猛攻", {F.CAV: 1.4, F.INF: 1.4, F.ARC: 0.7},
            {F.DPS: 2.5, F.BURST: 2.0, F.BAL: 0.9, F.TANK: 0.5, F.SUP: 0.3},
            "鶴翼", 0.8),
    # §7.83 で追加。**新しいメタを稽古台に乗せる**（§7.74-77 で盤面が変わり、
    # 「強弓の束ね」と「厚い壁で受ける」が上位の型になったのに、在野は
    # 改設計前の6性格しか居なかった）。
    Persona("強弓", {F.ARC: 2.2, F.INF: 1.0, F.CAV: 0.5},
            {F.DPS: 2.4, F.BURST: 2.2, F.BAL: 1.0, F.SUP: 0.6, F.TANK: 0.4},
            "魚鱗", 0.6),      # 壁3＋強弓3。§7.72 の実測で最も強い形
    Persona("重装", {F.INF: 2.0, F.CAV: 1.2, F.ARC: 0.6},
            {F.TANK: 2.6, F.BAL: 1.4, F.SUP: 1.0, F.DPS: 0.7, F.BURST: 0.4},
            "鶴翼", 0.4),
)


def _score(card: F.Card, p: Persona, want: float) -> float:
    """性格から見た札の好ましさ。**コストの近さも重みに混ぜる。**"""
    w = p.typ_w.get(card.typ, 0.3) * p.role_w.get(card.role, 0.3)
    # greed が高いほど「目安より高コスト」を好む
    d = abs(card.cost - want) / max(want, 1e-6)
    return w * (1.0 + p.greed * (card.cost / max(want, 1e-6) - 1.0)) / (1.0 + d)


# 手練れの在野（§7.85）。**性格の好みではなく、測って分かった型で組む** —
# 「安い壁で受け、武/点の高い強弓を束ねる」（§7.71-72 の実測）。性格だけで
# 組むと役割の重みしか見ないので同じ役割の中の効率が野放しになり、上の帯
# （赤壁40点）ほど手練れのデッキに一方的に負けていた（実測: 24人中24人に敗北）。
META_PERSONAS = {"強弓", "重装"}


def _meta_entry(cards: Sequence[F.Card], p: Persona, seed: int,
                caps=None) -> M.Entry:
    """壁＋強弓で組む。強弓は後衛に厚く、重装は前衛に厚く配分する。"""
    rng = random.Random("meta/{}/{}".format(p.name, seed))
    form = FORM_BY_NAME[p.form]
    nf = form.n_front
    n_rear = M.UNIT_SIZE - nf
    rear_share = 0.50 if p.name == "強弓" else 0.38
    used: set = set()
    units: List[F.Army] = []
    arcs = [c for c in cards if c.typ == F.ARC and c.might > 0]
    mel = [c for c in cards if c.typ in (F.INF, F.CAV)]
    for _label, cap in (caps if caps is not None else M.REGULATIONS):
        pick_r, pick_f = [], []
        budget_r = cap * rear_share
        # 後衛: **予算内で武力の総和が大きくなるように**選ぶ。武/点で選ぶと
        # 1点の伝令（武77/1点＝77）が満寵（183/3点＝61）より上に来てしまい、
        # 兵力の薄い札ばかりの後衛になる（コスト曲線が下に凸なため）。
        for c in sorted(arcs, key=lambda c: -(c.might + rng.random() * 40)):
            if len(pick_r) == n_rear:
                break
            if M.person_of(c) in used:
                continue
            if c.cost + (n_rear - len(pick_r) - 1) <= budget_r:
                pick_r.append(c); used.add(M.person_of(c)); budget_r -= c.cost
        while len(pick_r) < n_rear:      # 予算が足りなければ安い弓で埋める
            c = next(x for x in sorted(arcs, key=lambda x: x.cost)
                     if M.person_of(x) not in used)
            pick_r.append(c); used.add(M.person_of(c))
        # 前衛: 残りを壁へ**均等に**配る。高い順に取ると最後の枠に1点札の穴が
        # 空き、そこが46秒で崩れて戦列が破れる（実測で赤壁の負け筋がこれ）。
        # 枠ごとに「残り予算÷残り枠」を狙って、それを超えない最も硬い札を選ぶ。
        left = cap - sum(c.cost for c in pick_r)
        for k in range(nf):
            slots_left = nf - k
            target = left / slots_left
            best = None
            for c in mel:
                if M.person_of(c) in used or c.cost > target + 1e-9:
                    continue
                key = (c.role == F.TANK, c.cost)
                if best is None or key > (best.role == F.TANK, best.cost):
                    best = c
            if best is None:
                continue
            pick_f.append(best); used.add(M.person_of(best)); left -= best.cost
        while len(pick_f) < nf:
            c = next(x for x in sorted(mel, key=lambda x: x.cost)
                     if M.person_of(x) not in used)
            pick_f.append(c); used.add(M.person_of(c))
        pick = _spend_rest(pick_f + pick_r, list(cards), used, p, cap)
        pick = _spend_last(pick, list(cards), used, cap)
        units.append(F.Army(tuple(_order(pick, nf)), form))
    return M.Entry(tuple(units), name=p.name)


def make_entry(cards: Sequence[F.Card], p: Persona, seed: int,
               caps=None) -> M.Entry:
    """性格に沿って 3部隊18人を選ぶ。**規則を破る編成は作らない。**

    守るもの:
      - 各部隊6人、合計コストが上限以下、18人が別人物
      - **弓兵はちょうど 6 - 前衛の数**（後衛は弓兵だけ・前衛は近接だけ）

    枚数が兵種ごとに決まっているので、残り枠の最小コストも**兵種別に**見ないと
    詰む（近接だけ安く残って弓兵が買えない、が起きる）。

    caps を渡すと上限を差し替えられる（既定は M.REGULATIONS）。たたき台
    生成（§7.54）が「上限の9割で組んで伸びしろを残す」ために使う。
    """
    if p.name in META_PERSONAS:
        return _meta_entry(cards, p, seed, caps)
    rng = random.Random("{}/{}".format(p.name, seed))
    pool = sorted(cards, key=lambda c: (c.cost, c.name))
    used: set = set()
    units: List[F.Army] = []
    for label, cap in (caps if caps is not None else M.REGULATIONS):
        nf = FORM_BY_NAME[p.form].n_front
        want_arc = M.UNIT_SIZE - nf
        pick: List[F.Card] = []
        for _ in range(M.UNIT_SIZE):
            spent = sum(x.cost for x in pick)
            have_arc = sum(1 for x in pick if x.typ == F.ARC)
            need_arc = want_arc - have_arc
            need_mel = (nf) - (len(pick) - have_arc)
            avail = [c for c in pool if M.person_of(c) not in used]
            arc = sorted(c.cost for c in avail if c.typ == F.ARC)
            mel = sorted(c.cost for c in avail if c.typ != F.ARC)
            ok = []
            for c in avail:
                is_arc = c.typ == F.ARC
                if is_arc and need_arc <= 0:
                    continue
                if not is_arc and need_mel <= 0:
                    continue
                a2 = list(arc); m2 = list(mel)
                (a2 if is_arc else m2).remove(c.cost)
                floor = (sum(a2[:need_arc - (1 if is_arc else 0)])
                         + sum(m2[:need_mel - (0 if is_arc else 1)]))
                if spent + c.cost + floor <= cap + 1e-9:
                    ok.append(c)
            if not ok:
                break
            want = (cap - spent) / max(M.UNIT_SIZE - len(pick), 1)
            w = [max(_score(c, p, want), 1e-6) for c in ok]
            c = rng.choices(ok, weights=w, k=1)[0]
            pick.append(c)
            used.add(M.person_of(c))
        pick = _spend_rest(pick, pool, used, p, cap)
        pick = _spend_last(pick, pool, used, cap)
        units.append(F.Army(tuple(_order(pick, nf)), FORM_BY_NAME[p.form]))
    return M.Entry(tuple(units), name=p.name)


def _spend_last(pick: List[F.Card], pool: Sequence[F.Card], used: set,
                cap: float) -> List[F.Card]:
    """最後の端数を使い切る。**2枚の入れ替えまで許す。**

    1枚ずつの入れ替え（`_spend_rest`）だと、同じ群に「ちょうどよい1段上」が
    残っていないときに端数が残る（実測で144部隊中5部隊が1点余った）。上限に対して
    2.5〜5%あり、ラダーの測定に効くので潰す。安い2枚を高い2枚へ替える。
    """
    left = cap - sum(x.cost for x in pick)
    if left < 1.0:
        return pick
    order = sorted(range(len(pick)), key=lambda k: pick[k].cost)
    for i in order:
        cur = pick[i]
        grp = (lambda t: t == F.ARC) if cur.typ == F.ARC else (lambda t: t != F.ARC)
        room = cur.cost + left
        best = None
        for c in pool:
            if M.person_of(c) in used or not grp(c.typ):
                continue
            if cur.cost < c.cost <= room + 1e-9:
                if best is None or c.cost > best.cost:
                    best = c
        if best is not None:
            used.discard(M.person_of(cur))
            used.add(M.person_of(best))
            pick[i] = best
            left = cap - sum(x.cost for x in pick)
            if left < 1.0:
                break
    return pick


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
    """兵種の**枚数はちょうど**決まる（§4.1 の前衛・後衛の規則）。

    後衛は弓兵だけ、前衛は歩兵と騎兵だけ。つまり **弓兵は必ず 6 - 前衛の数**。
    陣形を選ぶことが弓兵の枚数を選ぶことになる。
    """
    return {F.ARC: M.UNIT_SIZE - n_front, F.INF: n_front, F.CAV: n_front}


def _spend_rest(pick: List[F.Card], pool: Sequence[F.Card], used: set,
                p: Persona, cap: float) -> List[F.Card]:
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
        # 入れ替えは**同じ兵種の中だけ**にする。枚数が規則で決まっているので、
        # 兵種をまたぐと配置が壊れる。
        # 入れ替えは**同じ群の中だけ**（近接どうし／弓どうし）。兵種の枚数は
        # 規則で決まっているので、群をまたぐと配置が壊れる。
        same = (lambda t: t == F.ARC) if cur.typ == F.ARC else (lambda t: t != F.ARC)
        cands = [c for c in pool
                 if M.person_of(c) not in used and c.cost <= room + 1e-9
                 and c.cost > cur.cost and same(c.typ)]
        if not cands:
            break
        best = max(cands, key=lambda c: (c.cost, _score(c, p, room)))
        used.discard(M.person_of(cur))
        used.add(M.person_of(best))
        pick[i] = best
    return pick


def seed_ladder(cx, cards: Sequence[F.Card], n: int = 24, start: int = 0
                ) -> List[Tuple[str, Persona, M.Entry]]:
    """ダミーを n 体登録し、それぞれの編成を作る。

    **メールは予約ドメイン**（`players.DUMMY_DOMAIN`）。足場を撤去し忘れても
    実在アドレスへは届かない。

    start は**既に居るダミーの数**（§7.83）。0 から振り直すと名前も
    メールも既存と衝突し、在野を増やそうとした瞬間に IntegrityError で
    落ちていた（増員の道が塞がっていた）。
    """
    from . import players as P
    out = []
    for k in range(n):
        i = start + k
        pi = i % len(PERSONAS)
        p = PERSONAS[pi]
        num = i // len(PERSONAS) + 1
        name = "{}{:02d}".format(p.name, num)
        pl = P.register(cx, name, kind=P.DUMMY,
                        email="dummy{:03d}@{}".format(i, P.DUMMY_DOMAIN))
        out.append((pl.id, p, make_entry(cards, p, deck_seed(pi, num))))
    return out


# 編成の種は (性格の番号, 通し番号) から**性格の総数に依らずに**決める
# （§7.83）。i = 通し番号 × 性格数 + 番号 にすると、性格を1つ足すたびに
# 既存の在野の編成が全部振り直される。SLOT は将来の追加ぶんの余白。
DECK_SLOT = 16


def deck_seed(persona_i: int, num: int) -> int:
    return (num - 1) * DECK_SLOT + persona_i


def form_table(dt: float = 0.5, seeds: int = 40) -> None:
    """陣形の三すくみ・ゲーム層（実カード・技と特性あり・乱数あり）。

    【⑧で判明した限界】この計器は各陣形を**性格2つ×固定シード**でしか見ない
    ので、性格のデッキの当たり外れが陣形の読み値に混ざる。かつての
    「健全な三すくみ 73/63/58%」はその混ざりが半分作っていた（デッキを
    陣形ごとに4種へ広げると、旧プールでも魚鱗対鶴翼はほぼ五分だった）。
    **釣り合いの判定はデッキを広げた計器で行うこと**（§7.53。同じ round-robin
    を「2性格 × シード違い」の4デッキ/陣形で回す）。ここは早見として残す。

    **カードを増やしたら測り直し、FORM_PAIR を合わせ直す。**
    """
    import statistics as st
    from collections import defaultdict
    from . import rosterdata as R
    R.load_skills_into_field()
    R.load_traits_into_field()
    F.SKILLS_ON = F.TRAITS_ON = True
    cards = M._roster_cards()
    es = {p.name: make_entry(cards, p, i) for i, p in enumerate(PERSONAS)}
    formname = {p.name: p.form for p in PERSONAS}
    names = list(es)
    cells = defaultdict(list)
    for x in names:
        for y in names:
            if x == y:
                continue
            w = [M.play_one(es[x], es[y], reg, dt, seed=s)["winner"] == "A"
                 for reg in range(3) for s in range(seeds)]
            cells[(formname[x], formname[y])].append(100.0 * st.mean(w))
    print("陣形の三すくみ・ゲーム層（BO1・全レギュ・乱数{}種・性格2つの平均）".format(seeds))
    print("  健全の目安: 各辺 55〜75%。同陣形のマスは性格差なので見ない。")
    for (fx, fy), vs in sorted(cells.items()):
        if fx == fy:
            continue
        print("  {:<6} 対 {:<6} {:>6.1f}%  ({}マス)".format(fx, fy, st.mean(vs), len(vs)))


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="ダミー（性格つき自動編成）")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("forms", help="陣形の三すくみをゲーム層で測る")
    s.add_argument("--dt", type=float, default=0.5)
    s.add_argument("--seeds", type=int, default=40)
    s.set_defaults(fn=lambda a: form_table(a.dt, a.seeds))
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
