# -*- coding: utf-8 -*-
"""散兵（§7.261）の受け入れ試験。

テストプレイの決裁「散兵→攻撃力下げて防御力あげる／残兵力が50%切ったとき」。
**新しい器は作っていない** —— 既存の誘発特性（`self_low_hp`）と**反動**だけで組んだ。

見張るのは5つ。

1. **自分への不利は「反動」で書く。** 素の負号（`攻撃力 -25%`）だと、対象が
   「自分」の節は**丸ごと落ちる** —— 盤面の符号の約束が「プラスは自分／味方、
   マイナスは対象」なので、味方対象の負号は行き先が無い。実際に踏んだ。
2. 掛かるのは**本人だけ**（隣の味方にも敵にも飛ばない）
3. 閾値は**既存の 40%**（`LOW_HP`）。50% を別に足さない —— 「残兵力が減った時」が
   ゲーム内で2つの意味を持ってしまう
4. 値段は**席で倍ちがう**（反動の重さが車台の攻撃力で変わる）。高いほうを採る
5. 王平が実際にその特性を持ち、対魏が外れていること
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import design as D            # noqa: E402
from sim import rosterdata as R        # noqa: E402

R.load_skills_into_field()
R.load_traits_into_field()

EFF = "防御力 +45%（90秒） + 反動 攻撃力 -25%（90秒）"


def _sides():
    a = F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)), F.FORM_STANDARD)
    b = F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)), F.FORM_STANDARD)
    return F.build(a, 1), F.build(b, -1)


class TheRecoilIsTheOnlyWayToWriteIt(unittest.TestCase):
    """**素の負号では落ちる。** ここが設計の落とし穴だった（§7.261 ①）。"""

    def test_a_bare_negative_on_a_self_target_is_dropped(self):
        ua, ub = _sides()
        sk = F._parse_skill("攻撃力 -25%（90秒） + 防御力 +45%（90秒）", "自分")
        F._apply_skill(ua[0], sk, "自分", ua, ub, 0.0,
                       src="＿素", name="＿素", kind_jp="固有")
        F._recalc_mods(ua[0])
        self.assertAlmostEqual(ua[0].atk_mult, 1.0, places=6,
                               msg="素の負号が効いてしまっている（約束が変わった）")
        self.assertGreater(ua[0].def_mult, 1.0)

    def test_the_recoil_form_lands(self):
        ua, ub = _sides()
        sk = F._parse_skill(EFF, "自分")
        # ここは**兵法として**読んだので予算の縮尺（§7.151）が掛かる。
        # 固有特性は掛からない（下の `test_traits_are_not_scaled`）。
        self.assertEqual(sk.mods, (("def", 0.45, F.skill_dur(90.0)),))
        self.assertEqual(sk.self_mods, (("atk", -0.25, F.skill_dur(90.0)),))
        F._apply_skill(ua[0], sk, "自分", ua, ub, 0.0,
                       src="＿散", name="＿散", kind_jp="固有")
        F._recalc_mods(ua[0])
        self.assertAlmostEqual(ua[0].atk_mult, 0.75, places=6)
        self.assertAlmostEqual(ua[0].def_mult, 1.45, places=6)


    def test_traits_are_not_scaled(self):
        """**固有特性に予算の縮尺は掛からない**（§7.152 の裁定）。

        掛けてしまうと、画面だけ 1.5倍 長い秒数が出る。読み込みの口が
        `unscaled()` を通していることの見張り。
        """
        sk = F.TRAITS["sanpei"][3]
        self.assertEqual(sk.mods, (("def", 0.45, 90.0),))
        self.assertEqual(sk.self_mods, (("atk", -0.25, 90.0),))


class OnlyTheHolder(unittest.TestCase):
    def test_neighbours_and_enemies_are_untouched(self):
        ua, ub = _sides()
        sk = F._parse_skill(EFF, "自分")
        F._apply_skill(ua[0], sk, "自分", ua, ub, 0.0,
                       src="＿散", name="＿散", kind_jp="固有")
        for u in ua[1:] + ub:
            F._recalc_mods(u)
            self.assertAlmostEqual(u.atk_mult, 1.0, places=6)
            self.assertAlmostEqual(u.def_mult, 1.0, places=6)


class TheThreshold(unittest.TestCase):
    def test_it_reuses_the_existing_forty_percent(self):
        """50% を別に足さない（「残兵力が減った時」の意味を1つに保つ）。"""
        self.assertAlmostEqual(F.LOW_HP, 0.40)
        cond, target, cap, _sk, name = F.TRAITS["sanpei"]
        self.assertEqual(cond, "self_low_hp")
        self.assertEqual(target, "自分")
        self.assertEqual(cap, 1)
        self.assertEqual(name, "散兵")

    def test_every_low_hp_trait_shares_the_same_threshold(self):
        low = [k for k, v in F.TRAITS.items() if v[0] == "self_low_hp"]
        self.assertIn("sanpei", low)
        self.assertGreater(len(low), 3, "self_low_hp の仲間が減っている")


class Price(unittest.TestCase):
    def test_measured_and_on_the_safe_side(self):
        """席で倍ちがう（前衛0.089／後衛0.044）ので**高いほう**を採った。"""
        self.assertAlmostEqual(D.TRAIT_PRICE["sanpei"], 0.0890, places=4)

    def test_the_recoil_really_discounts_it(self):
        """反動なしの死守（防御+40%）より**ずっと安い**こと。"""
        self.assertLess(D.trait_value("sanpei"), D.trait_value("diehard") * 0.5)


class WangPing(unittest.TestCase):
    def test_the_card_carries_it(self):
        g = {r["名前"]: r for r in R.generals()}["王平〔無当〕"]
        self.assertEqual(g["固有特性"], "sanpei")

    def test_anti_wei_is_gone(self):
        g = {r["名前"]: r for r in R.generals()}["王平〔無当〕"]
        self.assertNotIn("vs_wei", g["固有特性"])

    def test_it_is_the_only_holder(self):
        """1枚だけが持つ器＝そのまま個性（5コスト歩兵の散らし・§7.259 の続き）。"""
        holders = [r["名前"] for r in R.generals() if "sanpei" in (r["固有特性"] or "")]
        self.assertEqual(holders, ["王平〔無当〕"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
