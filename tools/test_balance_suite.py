# -*- coding: utf-8 -*-
"""Cheap acceptance tests for the balance fixtures and instruments."""
from __future__ import annotations

import os
import sys
import unittest

# 他の受け入れ試験と同じく `python3 tools/test_balance_suite.py` で走らせる
# （リポジトリの作法）。直接起動では tools/ が sys.path[0] になり `tools`
# パッケージが見えないので、ルートを足す。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import balance_common as C
from tools import balance_suite as B
from sim import match as M


class BalanceSuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = C.load_fixtures()
        cls.cards = C.roster()
        cls.index = C.card_index(cls.cards)

    def test_fixed_pool_sizes_and_status(self):
        self.assertEqual(len(self.data["pools"]["official24"]["entries"]), 24)
        self.assertEqual(len(self.data["pools"]["special48"]["entries"]), 48)
        # §7.195 で中身を今の名簿から組み直したので、破陣の最終選定で汚れていた
        # retired_validation は解けた。ただし基線が毎回当てるので**盲検ではない**
        # （盲検は final_blind だけ）。
        self.assertEqual(self.data["pools"]["special48"]["status"], "validation")
        self.assertEqual(self.data["pools"]["final_blind"]["entries"], [])

    def test_every_frozen_entry_is_currently_legal(self):
        seen = 0
        for _where, _name, entry in C.all_fixture_entries(self.data, self.index):
            self.assertEqual(M.validate(entry), [])
            seen += 1
        self.assertEqual(seen, 80)  # eight named sets (chappy, counter, chappy_prev_20260905, red2_20260906, cav_20260905, chappy_prev_20260907, red2_20260907, chappy_7194) + 24 + 48（§7.196）

    def test_distribution_reproduces_saved_sample_shape(self):
        report = B.distribution_report(self.data, self.cards)
        special = report["pools"]["special48"]
        self.assertEqual(special["entries"], 48)
        self.assertEqual(special["slots"], 864)
        self.assertEqual(special["coverage"], 136)   # §7.195 の組み直しで 120 → 136
        self.assertGreater(special["effective_cards"], 80)
        # 手数の段の枚数（名簿の形の見張り）。§7.155 で 顔良〔河北の驍〕 を大技から
        # 手数へ移して 8 → 9、§7.171 で 呂布〔飛将〕（轅門射戟）が手数へ 9 → 10。
        # 段を動かしたらここも直す。
        self.assertEqual(sum(C.cadence(c) == "手数" for c in self.cards), 10)

    def test_cadence_builder_keeps_cost_count_and_placement(self):
        army = B._cadence_army(self.cards, 0, "鶴翼", 6, 0, "test")
        self.assertIsNotNone(army)
        self.assertAlmostEqual(army.total_cost(), 18.0)
        self.assertEqual(sum(C.cadence(c) == "手数" for c in army.cards), 6)
        self.assertEqual(M.placement_errors(army), [])

    def test_restraint_multiplier_is_recorded_in_manifest(self):
        self.assertEqual(C.balance_constants()["RESTRAINT_NATURAL_MULT"], 0.40)


if __name__ == "__main__":
    unittest.main()
