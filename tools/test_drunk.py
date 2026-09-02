# -*- coding: utf-8 -*-
"""酒乱（§7.146）の受け入れ試験。持ち手が受ける混乱の量が DRUNK_CHAOS ぶん増える負の常在特性。"""
import os, sys, unittest
from dataclasses import replace
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sim import rosterdata as R, field as F, match as M

F.TRAITS.clear(); R.load_traits_into_field(); R.load_skills_into_field(); F.TRAITS_ON = True
CARDS = {c.name: c for c in M._roster_cards()}


def lubu_unit(trait):
    """呂布を先頭に置いた合法な6枚を build して、呂布の隊だけ返す。"""
    card = replace(CARDS["呂布〔飛将〕"], trait=trait)
    a = F.Army((card, CARDS["曹仁〔堅守〕"], CARDS["郝昭〔陳倉〕"],
                CARDS["李典〔慎重〕"], CARDS["貂蝉〔傾国〕"], CARDS["満寵〔剛毅〕"]), F.FORM_STANDARD)
    assert not M.placement_errors(a), M.placement_errors(a)
    return F.build(a, 0)[0]


def chaos_after(trait, amt=0.25):
    u = lubu_unit(trait)
    F._chaos_add(u, amt, 10.0)     # 兵法のフェーズ外なので即時に乗る
    return u.chaos


class DrunkTest(unittest.TestCase):
    def test_roster_holders(self):
        holders = [c.name for c in CARDS.values() if "drunk" in c.trait]
        self.assertEqual(sorted(holders), ["呂布〔虓虎〕", "呂布〔飛将〕"])

    def test_holder_takes_more_chaos(self):
        on, off = chaos_after("laststand、drunk"), chaos_after("laststand")
        self.assertAlmostEqual(off, 0.25, places=9)
        self.assertAlmostEqual(on, 0.25 * (1.0 + F.DRUNK_CHAOS), places=9)

    def test_traits_off_gate(self):
        F.TRAITS_ON = False
        try:
            self.assertAlmostEqual(chaos_after("laststand、drunk"), 0.25, places=9)
        finally:
            F.TRAITS_ON = True


if __name__ == "__main__":
    unittest.main(verbosity=1)
