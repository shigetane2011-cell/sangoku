# -*- coding: utf-8 -*-
"""Cheap acceptance tests for the balance fixtures and instruments."""
from __future__ import annotations

import os
import sys
import unittest

# 他の受け入れ試験と同じく `python3 tools/test_balance_suite.py` で走らせる
# （リポジトリの作法）。直接起動では tools/ が sys.path[0] になり `tools`
# パッケージが見えないので、ルートを足す。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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
        # 名前つきの登録 10本 + official24 + special48。9本目は testplay_20260908
        # ＝**テストプレイ本人が画面で組んだ登録**（§7.204。探索の産物ではないので
        # 盤面が動いても取り直さない）。10本目 counter_testplay_20260908 は
        # **その登録に勝つために探した相手**（§7.205・12シリーズ 12勝0敗）。
        self.assertEqual(seen, 82)

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

    # ---- 値付けの控えの鍵（§7.207・落とし穴52）--------------------------------
    # board_fingerprint は「盤面が同じなら同じ・違えば違う」でなければ控えが働かない。
    # 番地（`<lambda at 0x7f...>`）や読み込み順が混ざると**毎回変わり**、控えが一度も
    # 当たらないまま「守れている」ように見える。ここが見張り。
    def test_price_cache_key_is_stable_for_the_same_board(self):
        import subprocess
        got = [C.board_fingerprint()]
        # 別プロセスでも同じか（番地は プロセスごとに変わるので、混ざっていれば落ちる）
        for _ in range(2):
            out = subprocess.run(
                [sys.executable, "-c",
                 "import sys; sys.path.insert(0, %r)\n"
                 "from tools import balance_common as C\n"
                 "print(C.board_fingerprint())" % ROOT],
                capture_output=True, text=True, cwd=ROOT)
            self.assertEqual(out.returncode, 0, out.stderr)
            got.append(out.stdout.strip())
        self.assertEqual(len(set(got)), 1,
                         "盤面が同じなのに指紋が割れた: {}".format(got))

    def test_price_cache_key_is_independent_of_load_order(self):
        from sim import rosterdata as R
        first = C.board_fingerprint()
        C._FP.clear()
        R.load_traits_into_field()
        C.roster()
        self.assertEqual(C.board_fingerprint(), first,
                         "読み込みの前と後で指紋が変わった（呼ぶ場所で鍵が違ってしまう）")

    def test_panel_writes_and_reads_the_same_cache_key(self):
        """§7.210・落とし穴53。書き込みと読み戻しで鍵が食い違うと、**古い控えに項目が
        ある札は古い値が表に出て**、無い札は KeyError で落ちる。§7.208 で書き込み側に
        だけ指紋を足して読み戻しを直し忘れ、実際にそうなった。**空の札名で走らせる
        煙検査では捕まらない**（表の行が1つも作られないため）ので、鍵そのものを見る。"""
        import inspect
        from tools import skill_panel as SP
        src = inspect.getsource(SP.main)
        # 鍵は _panel_key に一本化されていること（書式文字列の直書きが残っていない）
        self.assertNotIn('"{}|{}|{}|{}|{}|{}"', src,
                         "読み戻し側が鍵を直書きしている（_panel_key を通すこと）")
        self.assertNotIn('"{}|{}|{}|{}|{}|{}|{}"', src,
                         "書き込み側が鍵を直書きしている（_panel_key を通すこと）")
        self.assertGreaterEqual(src.count("_panel_key("), 2,
                                "書き込みと読み戻しの両方が _panel_key を通ること")
        # 同じ引数なら同じ鍵・盤面が違えば違う鍵
        a = SP._panel_key("札", "兵法", "on", 0.0, 240, "c2/s1.1", "fp1")
        b = SP._panel_key("札", "兵法", "on", 0.0, 240, "c2/s1.1", "fp1")
        c = SP._panel_key("札", "兵法", "on", 0.0, 240, "c2/s1.1", "fp2")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c, "盤面の指紋が鍵に効いていない")

    def test_price_cache_key_moves_when_the_board_moves(self):
        from sim import field as F
        base = C.board_fingerprint()
        ta = dict(F.TYPE_ATK)
        k0 = sorted(ta)[0]
        ta[k0] = ta[k0] + 0.001
        saved, F.TYPE_ATK = F.TYPE_ATK, ta
        C._FP.clear()
        try:
            self.assertNotEqual(C.board_fingerprint(), base,
                                "相性表を動かしたのに指紋が同じ（古い控えを掴む）")
        finally:
            F.TYPE_ATK = saved
            C._FP.clear()


if __name__ == "__main__":
    unittest.main()
