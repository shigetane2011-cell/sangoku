# -*- coding: utf-8 -*-
"""戦記の登用の配り直し（§7.170）: 章へ後から足した登用が、すでにその戦を越えている
プレイヤーにも届く。何度呼んでも増えない。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import play as PL          # noqa: E402
from sim import players as P        # noqa: E402
from sim import senki as SK         # noqa: E402
from sim import rosterdata as R     # noqa: E402


class RecruitBackfillTest(unittest.TestCase):
    def setUp(self):
        self.cx = P.connect(os.path.join(tempfile.mkdtemp(prefix="sangoku-recruit-"), "players.db"))
        self.me = P.register(self.cx, "試験", kind=P.HUMAN)

    def _index(self, ch, no):
        return next(b["i"] for b in SK.battles() if (b["ch"], b["no"]) == (ch, no))

    def test_start_set_only_when_nothing_cleared(self):
        unl = PL.ensure_unlocks(self.cx, self.me.id)
        self.assertEqual(unl, set(R.senki_start()))
        self.assertNotIn("華佗", unl)

    def test_cleared_battles_recruits_are_backfilled(self):
        PL.ensure_unlocks(self.cx, self.me.id)
        SK.set_cleared(self.cx, self.me.id, self._index(4, 7) + 1)   # 乱世の奸雄まで越えた
        unl = PL.ensure_unlocks(self.cx, self.me.id)
        for b in SK.battles():
            for p in b["recruits"]:
                if b["i"] <= self._index(4, 7):
                    self.assertIn(p, unl, p)
                else:
                    self.assertNotIn(p, unl, p)
        self.assertIn("華佗", unl)          # 4-7 に後から足した登用
        self.assertNotIn("文鴦", unl)       # 8-5 はまだ
        # 二度目は増えない
        self.assertEqual(SK.backfill_recruits(self.cx, self.me.id), 0)
        self.assertEqual(PL.ensure_unlocks(self.cx, self.me.id), unl)


if __name__ == "__main__":
    unittest.main(verbosity=0)
