# -*- coding: utf-8 -*-
"""馬前（§7.144・旧名 庇護）の受け入れ試験。隣の前衛の騎兵の矢を代わりに受ける常在特性。"""
import os, sys, unittest
from dataclasses import replace
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sim import rosterdata as R, field as F, match as M, dummies as D

F.TRAITS.clear(); R.load_traits_into_field(); R.load_skills_into_field(); F.TRAITS_ON = True
ROSTER = M._roster_cards()
CARDS = {c.name: c for c in ROSTER}
ARCHERS = D.make_entry(ROSTER, [p for p in D.PERSONAS if p.name == "強弓"][0], 3,
                       caps=(("赤壁", 40.0),)).units[0]


def army(guard_trait="cover", rear_guard=False):
    guard = replace(CARDS["曹仁〔堅守〕"], trait=guard_trait)
    rear = replace(CARDS["文聘〔江夏〕"], trait="cover" if rear_guard else "")
    order = [CARDS["呂布〔飛将〕"], guard, CARDS["郝昭〔陳倉〕"], rear, CARDS["李典〔慎重〕"], CARDS["貂蝉〔傾国〕"]]
    return F.Army(tuple(order), F.FORM_STANDARD)


def run(a, foe=ARCHERS, seed=22, t_max=400.0):
    ev = []
    r = F.simulate(a, foe, dt=0.25, seed=seed, events=ev, t_max=t_max)
    return r, ev


class CoverTest(unittest.TestCase):
    def test_front_neighbor_takes_arrows(self):
        r0, _ = run(army(guard_trait=""))
        r1, ev = run(army())
        me0, me1 = r0["dealt_a"][0], r1["dealt_a"][0]
        g1 = r1["dealt_a"][1]
        self.assertGreater(g1[-1], 0.0, "庇う側の covered が増える")
        self.assertEqual(r0["dealt_a"][1][-1], 0.0, "特性なしでは 0")
        self.assertGreaterEqual(me1[6] or 1e9, me0[6] or 0.0, "崩れは早くならない")
        # **「長く立つ」を崩れの時刻で測らない**（§7.151 以降）。この盤では
        # 前衛の騎兵が受ける被害の 14% しか矢ではなく（残りは白兵）、庇える
        # 3割は全体の 4.2% にしかならない。壊滅までやると被害は必ず men0 に
        # 届くので、時刻の差はティックの刻みに埋もれる。**途中で切って残兵で
        # 測る**（決定論なので刻みに依らない）。庇い自体は効いている。
        s0, _ = run(army(guard_trait=""), t_max=30.0)
        s1, _ = run(army(), t_max=30.0)
        self.assertGreater(s1["dealt_a"][0][3], s0["dealt_a"][0][3],
                           "庇われた騎兵は同じ時刻で兵が多く残る")
        mine = [e for e in ev if ("【" + F.COVER_NAME + "】") in e.text and e.side == "A"]
        self.assertEqual(len(mine), 1, "自軍の庇護は1戦1回だけ語る（相手の典韋・許褚は別）")
        self.assertTrue(mine[0].text.startswith("曹仁〔堅守〕"))

    def test_rear_guard_does_nothing(self):
        r, ev = run(army(guard_trait="", rear_guard=True))
        self.assertEqual(r["dealt_a"][3][-1], 0.0, "後衛の庇い手は働かない")
        self.assertFalse(any(("【" + F.COVER_NAME + "】") in e.text and e.side == "A" for e in ev))

    def test_infantry_neighbor_is_not_covered(self):
        """B案: 庇うのは隣の騎兵だけ。歩兵の隣では何も起きない（特性なしと同じ戦い）。"""
        def inf_army(trait):
            return F.Army(tuple([CARDS["張飛〔当陽橋〕"], replace(CARDS["曹仁〔堅守〕"], trait=trait), CARDS["郝昭〔陳倉〕"],
                                 CARDS["文聘〔江夏〕"], CARDS["李典〔慎重〕"], CARDS["貂蝉〔傾国〕"]]), F.FORM_STANDARD)
        r1, ev = run(inf_army("cover")); r0, _ = run(inf_army(""))
        self.assertEqual(r1["dealt_a"][1][-1], 0.0)
        self.assertEqual(r1["diff"], r0["diff"], "歩兵の隣では特性なしと同じ戦い")
        self.assertFalse(any(("【" + F.COVER_NAME + "】") in e.text and e.side == "A" for e in ev))

    def test_no_cover_against_melee_only(self):
        melee = F.Army(tuple(CARDS[n] for n in ("張飛〔当陽橋〕", "許褚〔虎痴〕", "曹仁〔堅守〕")), F.FORM_STANDARD)
        r, _ = run(army(), foe=melee)
        self.assertEqual(r["dealt_a"][1][-1], 0.0, "射程を持たない相手の被害は庇わない")

    def test_share_zero_equals_no_trait(self):
        # 相手側にも庇護持ち（典韋・許褚）が入りうるので、割合 0 は両方の戦いに掛ける
        keep = F.COVER_SHARE
        try:
            F.COVER_SHARE = 0.0
            r_off, _ = run(army())
            r_none, _ = run(army(guard_trait=""))
        finally:
            F.COVER_SHARE = keep
        self.assertEqual(r_off["diff"], r_none["diff"], "割合 0 なら特性の有無で戦いは変わらない")
        self.assertEqual(r_off["dealt_a"][1][-1], 0.0)

    def test_traits_off_disables(self):
        F.TRAITS_ON = False
        try:
            r, _ = run(army())
        finally:
            F.TRAITS_ON = True
        self.assertEqual(r["dealt_a"][1][-1], 0.0, "零点の経路（TRAITS_ON=False）では働かない")

    def test_cav_cover_reduces_far_arrows(self):
        """馬上回避（§7.144・射手が遠いあいだ）: 弓型の相手に対して騎兵の被ダメが減る。"""
        keep = F.CAV_COVER
        try:
            F.CAV_COVER = 0.0
            r0, _ = run(army(guard_trait=""))
            F.CAV_COVER = 0.30
            r1, _ = run(army(guard_trait=""))
        finally:
            F.CAV_COVER = keep
        me0, me1 = r0["dealt_a"][0], r1["dealt_a"][0]
        self.assertGreater(me1[6] or 1e9, me0[6] or 0.0, "矢を避ける騎兵は長く立つ")

    def test_tuple_tail_is_covered(self):
        r, _ = run(army())
        for e in r["dealt_a"] + r["dealt_b"]:
            self.assertIsInstance(e[-1], float, "dealt_* の末尾は covered")


if __name__ == "__main__":
    unittest.main(verbosity=1)
