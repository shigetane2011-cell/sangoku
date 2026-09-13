# -*- coding: utf-8 -*-
"""勢力で縛る号令（§7.258）の受け入れ試験。

テストプレイの決裁（提案④案A）から作った器。**編成の軸をひとつ増やす**のが狙いで、
「魏で固めると号令が全員に届く／混ぜると半分にしか届かない」を作る。

見張るのは5つ。

1. 届くのは**撃った本人と同じ勢力**だけ（本人は必ず入る）
2. 勢力の**無い**隊（合成カード・計器の盤面）は**本人だけ** ——
   空文字どうしを同じ勢力と見なすと「味方全体」と黙って同じ挙動になり、
   値付けの測定が全体の値になる
3. 「同じ勢力」は**「全体」より先に**読む（語が重なるため）
4. 値段は**味方全体と同額**（飽和する側を高く請求する＝安全な向き）
5. 画面に「届くのは同じ勢力の味方だけ」が出る（対象文の字だけでは読めない）
"""
import dataclasses
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import design as D            # noqa: E402
from sim import rosterdata as R        # noqa: E402

if not F.SKILL_INFO:
    R.load_skills_into_field()
R.load_traits_into_field()

TGT = "味方全体（同じ勢力）"


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _sides(factions):
    """勢力を指定した6枚（前3・後3）の自軍と、無地の敵軍。"""
    cards = [dataclasses.replace(F._synth(5.0, F.INF), faction=f) for f in factions]
    ua = F.build(_army(cards), 1)
    ub = F.build(_army([F._synth(5.0, F.INF) for _ in range(6)]), -1)
    return ua, ub


class Targets(unittest.TestCase):
    def test_only_the_same_faction_receives_it(self):
        ua, ub = _sides(["魏", "蜀", "魏", "呉", "魏", "蜀"])
        got = F._skill_targets(TGT, ua[0], ub, ua)
        self.assertEqual([u.faction for u in got], ["魏", "魏", "魏"])
        self.assertIn(ua[0], got, "撃った本人が入っていない")

    def test_a_mono_faction_army_reaches_everyone(self):
        ua, ub = _sides(["魏"] * 6)
        self.assertEqual(len(F._skill_targets(TGT, ua[0], ub, ua)), 6)

    def test_the_caster_decides_the_faction(self):
        ua, ub = _sides(["魏", "蜀", "魏", "蜀", "蜀", "蜀"])
        got = F._skill_targets(TGT, ua[1], ub, ua)   # 蜀の隊が撃つ
        self.assertEqual([u.faction for u in got], ["蜀"] * 4)

    def test_factionless_units_only_reach_themselves(self):
        """**合成カードの盤面で「味方全体」と同じにしない。** 値付けの測定が狂う。"""
        ua, ub = _sides([""] * 6)
        self.assertEqual(F._skill_targets(TGT, ua[0], ub, ua), [ua[0]])

    def test_the_dead_are_not_counted(self):
        ua, ub = _sides(["魏"] * 6)
        ua[1].men = 0.0
        self.assertEqual(len(F._skill_targets(TGT, ua[0], ub, ua)), 5)

    def test_a_lone_survivor_of_the_faction_still_gets_it(self):
        ua, ub = _sides(["魏", "蜀", "蜀", "蜀", "蜀", "蜀"])
        self.assertEqual(F._skill_targets(TGT, ua[0], ub, ua), [ua[0]])

    def test_plain_whole_army_is_untouched(self):
        ua, ub = _sides(["魏", "蜀", "魏", "呉", "魏", "蜀"])
        self.assertEqual(len(F._skill_targets("味方全体", ua[0], ub, ua)), 6)


class Applies(unittest.TestCase):
    def test_the_buff_lands_only_on_the_faction(self):
        ua, ub = _sides(["魏", "蜀", "魏", "蜀", "魏", "蜀"])
        sk = F._parse_skill("攻撃力 +14%（45秒）", TGT)
        F._apply_skill(ua[0], sk, TGT, ua, ub, 0.0,
                       src="＿号", name="＿号", kind_jp="兵法")
        for u in ua:
            F._recalc_mods(u)
        got = [round(u.atk_mult, 4) for u in ua]
        self.assertTrue(all(g > 1.0 for u, g in zip(ua, got) if u.faction == "魏"))
        self.assertTrue(all(g == 1.0 for u, g in zip(ua, got) if u.faction == "蜀"))


class Price(unittest.TestCase):
    def test_two_thirds_of_the_whole_army(self):
        """6枚中4枚ぶん。**満額で作って、測ってから割り引いた**（§7.258）。

        満額（3.528）では「勢力で固めても横ばい・混ぜると −8.33%」で、
        案A の狙い（固めたぶん効率が上がる）がどこにも出なかった。
        """
        self.assertAlmostEqual(D.target_fx(TGT), D.target_fx("味方全体") * 2.0 / 3.0,
                               places=6)

    def test_never_cheaper_than_a_single_row(self):
        """**床を割らない。** 1列ぶん（味方前衛）より安くすると、勢力で固めた編成が
        「列バフの値段で全体バフ」を買えてしまう。割引はここまで。"""
        self.assertGreater(D.target_fx(TGT), D.target_fx("味方前衛"))

    def test_the_discount_really_costs_less(self):
        gc, gi = D.GAUGE_TIER["標準"]
        args = dict(gauge_cost=gc, gauge_init=gi, cost=8.0, typ=F.CAV, tilt="中庸")
        eff = "攻撃力 +14%（45秒）"
        self.assertLess(
            D.effect_value(F._parse_skill(eff, TGT), TGT, **args),
            D.effect_value(F._parse_skill(eff, "味方全体"), "味方全体", **args))

    def test_it_fits_the_budget(self):
        gc, gi = D.GAUGE_TIER["標準"]
        v = D.effect_value(F._parse_skill("攻撃力 +14%（45秒）", TGT), TGT,
                           gauge_cost=gc, gauge_init=gi, cost=8.0, typ=F.CAV,
                           tilt="中庸")
        self.assertLess(v, D.EFFECT_CAP * 8.0)


class SimaZhao(unittest.TestCase):
    """司馬昭〔晋王〕が実際にその器で載っていること。"""

    def test_the_card_carries_it(self):
        self.assertEqual(F.SKILL_TARGET["晋王の号令"], TGT)
        sk = F.SKILL_INFO["晋王の号令"]
        self.assertEqual(len(sk.mods), 1)
        key, amt, _secs = sk.mods[0]
        self.assertEqual(key, "atk")
        self.assertAlmostEqual(amt, F.skill_mag(0.14))

    def test_it_no_longer_duplicates_cao_cao(self):
        """曹操〔魏王〕とは**対象が違う**（％違いだけ、ではなくなった）。"""
        self.assertEqual(F.SKILL_TARGET["唯才是挙"], "味方全体")
        self.assertNotEqual(F.SKILL_TARGET["晋王の号令"],
                            F.SKILL_TARGET["唯才是挙"])

    def test_the_display_says_who_receives_it(self):
        from sim import web as W
        g = {x["名前"]: x for x in R.generals()}["司馬昭〔晋王〕"]
        row = {x["兵法名"]: x for x in R.skills()}["晋王の号令"]
        txt = W._skill_display(g, row)
        self.assertIn("同じ勢力の味方だけ", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
