# -*- coding: utf-8 -*-
"""陸抗「節制」の安価な回帰。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F
from sim import match as M


class RestraintTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cards = {c.name: c for c in M._roster_cards()}
        cls.defense = F.Army(tuple(cards[n] for n in (
            "陸抗〔羊陸之交〕", "賀斉〔山越討伐〕", "華雄〔汜水関〕",
            "郭淮〔雍涼〕", "周瑜〔赤壁〕", "荀彧〔王佐〕")), F.FORM_WIDE)
        cls.rapid = F.Army(tuple(cards[n] for n in (
            "夏侯淵〔神速〕", "趙雲〔長坂坡〕", "関平〔麒麟児〕",
            "韓当〔老弓〕", "馬謖〔幼常〕", "諸葛恪〔元遜〕")), F.FORM_STANDARD)

    def test_first_cast_then_reduces_later_casts(self):
        keep = F.RESTRAINT_NATURAL_MULT
        try:
            F.RESTRAINT_NATURAL_MULT = 1.0
            off = F.simulate(self.defense, self.rapid, dt=0.5, seed=42)
            F.RESTRAINT_NATURAL_MULT = 0.40
            on = F.simulate(self.defense, self.rapid, dt=0.5, seed=42)
        finally:
            F.RESTRAINT_NATURAL_MULT = keep
        self.assertGreater(dict(on["fires_a"])["陸抗〔羊陸之交〕"], 0)
        self.assertLess(sum(n for _name, n in on["fires_b"]),
                        sum(n for _name, n in off["fires_b"]))

    def test_each_unit_keeps_normal_rate_until_its_first_cast(self):
        class Stub:
            def __init__(self, traits=(), fires=0):
                self.traits = traits
                self.fires = fires

        keeper = Stub(("restraint",), 1)
        uncast_big = Stub((), 0)
        cast_rapid = Stub((), 1)
        self.assertEqual(F.natural_gauge_mult(uncast_big, [keeper, uncast_big]), 1.0)
        self.assertEqual(F.natural_gauge_mult(cast_rapid, [keeper, cast_rapid]),
                         F.RESTRAINT_NATURAL_MULT)

    def test_same_army_remains_symmetric(self):
        result = F.simulate(self.defense, self.defense, dt=0.5, seed=None)
        self.assertAlmostEqual(result["diff"], 0.0, places=12)


if __name__ == "__main__":
    unittest.main()
