# -*- coding: utf-8 -*-
"""庇護（§7.144）の受け入れ試験。隣の前衛の矢を代わりに受ける常在特性。"""
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


def run(a, foe=ARCHERS, seed=22):
    ev = []
    r = F.simulate(a, foe, dt=0.25, seed=seed, events=ev)
    return r, ev


class CoverTest(unittest.TestCase):
    def test_front_neighbor_takes_arrows(self):
        r0, _ = run(army(guard_trait=""))
        r1, ev = run(army())
        me0, me1 = r0["dealt_a"][0], r1["dealt_a"][0]
        g1 = r1["dealt_a"][1]
        self.assertGreater(g1[-1], 0.0, "庇う側の covered が増える")
        self.assertEqual(r0["dealt_a"][1][-1], 0.0, "特性なしでは 0")
        self.assertGreater(me1[6] or 1e9, me0[6] or 0.0, "庇われた騎兵は長く立つ")
        mine = [e for e in ev if "【庇護】" in e.text and e.side == "A"]
        self.assertEqual(len(mine), 1, "自軍の庇護は1戦1回だけ語る（相手の典韋・許褚は別）")
        self.assertTrue(mine[0].text.startswith("曹仁〔堅守〕"))

    def test_rear_guard_does_nothing(self):
        r, ev = run(army(guard_trait="", rear_guard=True))
        self.assertEqual(r["dealt_a"][3][-1], 0.0, "後衛の庇い手は働かない")
        self.assertFalse(any("【庇護】" in e.text and e.side == "A" for e in ev))

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

    def test_tuple_tail_is_covered(self):
        r, _ = run(army())
        for e in r["dealt_a"] + r["dealt_b"]:
            self.assertIsInstance(e[-1], float, "dealt_* の末尾は covered")


if __name__ == "__main__":
    unittest.main(verbosity=1)
