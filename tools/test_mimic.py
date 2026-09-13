# -*- coding: utf-8 -*-
"""写し取り（§7.260）と、段を盤面の規則にした件の受け入れ試験。

テストプレイの案「次に味方武将の兵法が発動した時それをコピーする／タイミング普通に
して、大技コピー狙い」と、その決裁「提案通りでいいが、**大技の定義を書く必要が
あるね**」から作った。

**定義の話がこの試験の半分である。** 段（連発型／標準型／決戦型）はこれまで
設計の内部ラベルだったが、馬良の効果文が段を指すので**盤面の規則**になった。
規則である以上、

1. 解決の道が2つある（`tier_for` と `design.GAUGE_TIER_NAME`）のに**食い違わない**
2. 札の文面は**画面に出ている呼び名**を使う（内部語の「大技」は出さない）

が守られていなければならない。

器のほうで見張るのは6つ。

3. 写すのは**段が一致したときだけ**
4. **1回だけ**（写したら構えは解ける）／窓が切れたら写さない
5. **写しから写しは生まれない**
6. 写しは**写した本人の能力で解ける**（本家の丸写しではない）
7. 写しは「味方が兵法を放った時」の特性を**二度鳴らさない**
8. 器の無い組み（味方対象）は読み込みで断る
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

ME = "自分"


def _sides(n=6, cost=6.0):
    a = F.Army(tuple(F._synth(cost, F.INF) for _ in range(n)), F.FORM_STANDARD)
    b = F.Army(tuple(F._synth(cost, F.INF) for _ in range(n)), F.FORM_STANDARD)
    return F.build(a, 1), F.build(b, -1)


def _register(name, eff, tgt, tier):
    F.SKILL_INFO[name] = F._parse_skill(eff, tgt)
    F.SKILL_TARGET[name] = tgt
    F.SKILL_TIER[name] = tier
    return F.SKILL_INFO[name]


class TheDefinition(unittest.TestCase):
    """段が盤面の規則であること（§7.260 — テストプレイの指摘）。"""

    def test_the_two_resolutions_agree_on_every_card(self):
        R._check_tiers()          # 食い違えば SystemExit

    def test_the_guard_actually_catches_a_mismatch(self):
        """見張りが**本当に落ちる**こと。通らない見張りは無いのと同じ。"""
        orig = R.skills

        def fake():
            out = []
            for r in orig():
                r = dict(r)
                if r["兵法名"] == "青龍偃月":     # 消費150（標準）を手数と主張
                    r["発動型"] = "手数"
                out.append(r)
            return out
        R.skills = fake
        try:
            with self.assertRaises(SystemExit):
                R._check_tiers()
        finally:
            R.skills = orig

    def test_the_player_facing_names(self):
        self.assertEqual(R.TIER_JP, {"手数": "連発型", "標準": "標準型",
                                     "大技": "決戦型"})

    def test_the_board_and_the_roster_use_the_same_names(self):
        """呼び名の表が2箇所にある（`field` と `rosterdata`）。**ずれたら死ぬ。**"""
        self.assertEqual(F.TIER_BY_JP, R.TIER_BY_JP)

    def test_the_internal_word_is_not_printed_on_cards(self):
        """効果文に内部語の「大技」を書かない（画面に無い語では確かめようがない）。"""
        for row in R.skills():
            self.assertNotIn("大技", row["効果"] or "",
                             "{} の効果文に内部語が出ている".format(row["兵法名"]))

    def test_every_skill_has_a_tier(self):
        for row in R.skills():
            self.assertIn(F.SKILL_TIER.get(row["兵法名"]), R.TIER_NAMES)


class Parse(unittest.TestCase):
    def test_reads_the_tier_and_the_window(self):
        sk = F._parse_skill("写し取り 決戦型（35秒）", ME)
        self.assertEqual(sk.mimic_tier, "大技")
        self.assertAlmostEqual(sk.mimic_secs, F.skill_dur(35.0))

    def test_refuses_targets_other_than_self(self):
        for tgt in ("味方全体", "味方前衛", "敵前衛"):
            with self.assertRaises(SystemExit):
                F._parse_skill("写し取り 決戦型（35秒）", tgt)

    def test_a_lone_mimic_is_a_readable_effect(self):
        """`rosterdata` の「1つも読めていない」の見張りを通ること（落とし穴79）。"""
        R.load_skills_into_field()

    def test_plain_skills_are_untouched(self):
        self.assertEqual(F._parse_skill("ダメージ 威力600%", "敵前衛").mimic_secs, 0.0)


class Board(unittest.TestCase):
    def setUp(self):
        _register("＿大", "ダメージ 威力1500%", "敵前衛", "大技")
        _register("＿並", "ダメージ 威力1500%", "敵前衛", "標準")
        _register("＿写", "写し取り 決戦型（35秒）", ME, "標準")

    def _armed(self):
        ua, ub = _sides()
        ua[1].skill = "＿写"
        ua[1].mimic_until = 99.0
        return ua, ub

    def test_casting_only_arms_the_window(self):
        ua, ub = _sides()
        before = sum(x.men for x in ub)
        F._apply_skill(ua[0], F.SKILL_INFO["＿写"], ME, ua, ub, 0.0,
                       src="＿写", name="＿写", kind_jp="兵法")
        self.assertGreater(ua[0].mimic_until, 0.0)
        self.assertAlmostEqual(sum(x.men for x in ub), before, places=6)

    def test_it_copies_a_matching_tier(self):
        ua, ub = self._armed()
        before = sum(x.men for x in ub)
        F._mimic_after(ua[0], F.SKILL_INFO["＿大"], ua, ub, 1.0, [], set(), None, "＿大")
        self.assertLess(sum(x.men for x in ub), before)

    def test_it_ignores_other_tiers(self):
        ua, ub = self._armed()
        before = sum(x.men for x in ub)
        F._mimic_after(ua[0], F.SKILL_INFO["＿並"], ua, ub, 1.0, [], set(), None, "＿並")
        self.assertAlmostEqual(sum(x.men for x in ub), before, places=6)
        self.assertEqual(ua[1].mimic_until, 99.0, "段違いで構えが解けている")

    def test_only_once(self):
        ua, ub = self._armed()
        F._mimic_after(ua[0], F.SKILL_INFO["＿大"], ua, ub, 1.0, [], set(), None, "＿大")
        self.assertEqual(ua[1].mimic_until, 0.0)
        mid = sum(x.men for x in ub)
        F._mimic_after(ua[0], F.SKILL_INFO["＿大"], ua, ub, 2.0, [], set(), None, "＿大")
        self.assertAlmostEqual(sum(x.men for x in ub), mid, places=6, msg="2回写した")

    def test_an_expired_window_does_not_copy(self):
        ua, ub = self._armed()
        ua[1].mimic_until = 0.5
        before = sum(x.men for x in ub)
        F._mimic_after(ua[0], F.SKILL_INFO["＿大"], ua, ub, 1.0, [], set(), None, "＿大")
        self.assertAlmostEqual(sum(x.men for x in ub), before, places=6)

    def test_the_caster_does_not_copy_itself(self):
        ua, ub = _sides()
        ua[0].skill = "＿写"
        ua[0].mimic_until = 99.0
        before = sum(x.men for x in ub)
        F._mimic_after(ua[0], F.SKILL_INFO["＿大"], ua, ub, 1.0, [], set(), None, "＿大")
        self.assertAlmostEqual(sum(x.men for x in ub), before, places=6)

    def test_a_copy_never_spawns_a_copy(self):
        """写し持ちを2枚積んでも、1回の本物から出る写しは**それぞれ1つずつ**。"""
        ua, ub = _sides()
        for i in (1, 2):
            ua[i].skill = "＿写"
            ua[i].mimic_until = 99.0
        F._mimic_after(ua[0], F.SKILL_INFO["＿大"], ua, ub, 1.0, [], set(), None, "＿大")
        self.assertEqual([ua[1].mimic_until, ua[2].mimic_until], [0.0, 0.0])

    def test_the_copy_uses_the_copier_s_own_strength(self):
        """**丸写しではない。** 写した本人の武力・知力で解ける。"""
        import dataclasses
        ua, ub = _sides()
        ua[1].skill = "＿写"
        ua[1].mimic_until = 99.0
        ua[1].might, ua[1].wits = ua[0].might * 0.5, ua[0].wits * 0.5
        b0 = sum(x.men for x in ub)
        F._mimic_after(ua[0], F.SKILL_INFO["＿大"], ua, ub, 1.0, [], set(), None, "＿大")
        weak = b0 - sum(x.men for x in ub)
        ua2, ub2 = _sides()
        ua2[1].skill = "＿写"
        ua2[1].mimic_until = 99.0
        b1 = sum(x.men for x in ub2)
        F._mimic_after(ua2[0], F.SKILL_INFO["＿大"], ua2, ub2, 1.0, [], set(), None, "＿大")
        full = b1 - sum(x.men for x in ub2)
        self.assertLess(weak, full, "能力を半分にしても写しの威力が変わらない")


class Price(unittest.TestCase):
    def test_proportional_to_the_window(self):
        gc, gi = D.GAUGE_TIER["標準"]
        args = dict(gauge_cost=gc, gauge_init=gi, cost=4.0, typ=F.ARC, tilt="中庸")
        a = D.effect_value(F._parse_skill("写し取り 決戦型（20秒）", ME), ME, **args)
        b = D.effect_value(F._parse_skill("写し取り 決戦型（40秒）", ME), ME, **args)
        self.assertAlmostEqual(b / a, 2.0, places=3)

    def test_anchored_on_a_grand_skill_s_budget(self):
        """35秒の窓は**決戦型の効果予算の中央**（1.163）で請求する。"""
        gc, gi = D.GAUGE_TIER["標準"]
        v = D.effect_value(F._parse_skill("写し取り 決戦型（35秒）", ME), ME,
                           gauge_cost=gc, gauge_init=gi, cost=4.0, typ=F.ARC,
                           tilt="中庸")
        self.assertAlmostEqual(v, 1.163, places=2)

    def test_it_fits_ma_liang(self):
        import csv
        g = {r["名前"]: r for r in R.generals()}["馬良〔白眉〕"]
        self.assertLess(float(g["効果予算"]), D.EFFECT_CAP * float(g["コスト"]))


class MaLiang(unittest.TestCase):
    def test_the_card_carries_it(self):
        sk = F.SKILL_INFO["白眉の進言"]
        self.assertEqual(sk.mimic_tier, "大技")
        self.assertEqual(F.SKILL_TARGET["白眉の進言"], ME)

    def test_it_is_no_longer_a_worse_jiang_qin(self):
        """蒋欽〔射術〕の下位互換だったのを解いた（同じ器・同じ秒・狭い対象）。"""
        self.assertEqual(F.SKILL_INFO["白眉の進言"].mods, ())
        self.assertTrue(F.SKILL_INFO["濡須の督戦"].mods)

    def test_the_display_uses_the_player_facing_name(self):
        from sim import web as W
        g = {x["名前"]: x for x in R.generals()}["馬良〔白眉〕"]
        row = {x["兵法名"]: x for x in R.skills()}["白眉の進言"]
        txt = W._skill_display(g, row)
        self.assertIn("決戦型", txt)
        self.assertNotIn("大技", txt)
        self.assertIn("1回だけ", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
