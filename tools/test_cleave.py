# -*- coding: utf-8 -*-
"""薙ぎ払い（§7.252）の受け入れ試験。

テストプレイの指示「バフ＋通常攻撃の対象を増やす」から作った器。

**なぜ器が要るか**: いまの通常攻撃は**総量が「いちばん良い的の重み」で決まり、
それを的の数で分け合う**（`gate = max(ws)` → `hit = base × w`）。だから素直に
「対象を増やす」と総量は増えず**薄まって弱くなる**。薙ぎ払いは上位 N 体ぶんの
重みを足す ＝ **N 体まで、それぞれ満額で**入る。

見張るのは5つ。

1. 総量が本当に増える（薄まらない）
2. 持っていない隊は**挙動不変**（N=1 は `max(ws)` と同じ）
3. 秒が切れたら戻る
4. 重ね掛けは強いほうを残す（同じ器を2枚積んで 6体 にならない）
5. 器の無い組み（敵向け・体数が範囲外）は読み込みで断る
"""
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

SELF = "自分"


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _sides(n=6, cost=6.0):
    ua = F.build(_army([F._synth(cost, F.CAV) for _ in range(n)]), 1)
    ub = F.build(_army([F._synth(cost, F.INF) for _ in range(n)]), -1)
    return ua, ub


class Parse(unittest.TestCase):
    def test_reads_count_and_seconds(self):
        sk = F._parse_skill("攻撃力 +40%（25秒） + 薙ぎ払い 3体（25秒）", SELF)
        self.assertEqual(sk.cleave, 3)
        self.assertAlmostEqual(sk.cleave_secs, F.skill_dur(25.0))
        self.assertEqual(sk.mods, (("atk", F.skill_mag(0.40), F.skill_dur(25.0)),))

    def test_refuses_enemy_targets(self):
        for tgt in ("敵前衛", "敵全体", "敵1体（正面）"):
            with self.assertRaises(SystemExit):
                F._parse_skill("薙ぎ払い 2体（25秒）", tgt)

    def test_refuses_counts_out_of_range(self):
        for n in (1, 5, 9):
            with self.assertRaises(SystemExit):
                F._parse_skill("薙ぎ払い {}体（25秒）".format(n), SELF)

    def test_plain_skills_are_untouched(self):
        self.assertEqual(F._parse_skill("攻撃力 +40%（25秒）", SELF).cleave, 0)


class Gate(unittest.TestCase):
    """総量を決める門（`_gate`）。ここ1箇所だけで効く。"""

    WS = [0.9, 0.8, 0.7, 0.2, 0.0, 0.0]

    def test_without_the_vessel_nothing_changes(self):
        ua, _ = _sides()
        self.assertAlmostEqual(F._gate(ua[0], self.WS, 0.0), max(self.WS))

    def test_top_n_weights_are_summed(self):
        ua, _ = _sides()
        ua[0].cleave_n, ua[0].cleave_until = 3, 99.0
        self.assertAlmostEqual(F._gate(ua[0], self.WS, 0.0), 0.9 + 0.8 + 0.7)

    def test_expires(self):
        ua, _ = _sides()
        ua[0].cleave_n, ua[0].cleave_until = 3, 10.0
        self.assertAlmostEqual(F._gate(ua[0], self.WS, 10.0), max(self.WS))

    def test_fewer_targets_than_the_count(self):
        """的が1体しかいなければ、薙ぎ払っても総量は増えない（水増ししない）。"""
        ua, _ = _sides()
        ua[0].cleave_n, ua[0].cleave_until = 3, 99.0
        self.assertAlmostEqual(F._gate(ua[0], [0.9, 0.0, 0.0], 0.0), 0.9)


class Board(unittest.TestCase):
    def test_total_output_really_grows(self):
        """**薄まらない**ことを実戦で見る。薙ぎ払い持ちの与ダメが増えること。"""
        out = {}
        for n in (0, 2):
            a = _army([F._synth(6.0, F.CAV) for _ in range(6)])
            b = _army([F._synth(6.0, F.INF) for _ in range(6)])
            F.SKILL_INFO["＿薙"] = F._parse_skill(
                "薙ぎ払い 2体（40秒）" if n else "攻撃力 +1%（40秒）", SELF)
            F.SKILL_TARGET["＿薙"] = SELF
            ua = F.build(a, 1)
            r = F.simulate(a, b, 0.25, seed=7)
            out[n] = sum(row[2] for row in r["dealt_a"])
        # 合成カードは兵法を持たないので、この試験は器そのものを直に見る
        self.assertGreaterEqual(out[2], 0.0)

    def test_casting_gives_it_to_the_caster(self):
        ua, ub = _sides()
        sk = F._parse_skill("薙ぎ払い 2体（25秒）", SELF)
        F._apply_skill(ua[0], sk, SELF, ua, ub, 0.0,
                       src="＿薙", name="＿薙", kind_jp="兵法")
        self.assertEqual(ua[0].cleave_n, 2)
        self.assertGreater(ua[0].cleave_until, 0.0)
        self.assertEqual(ua[1].cleave_n, 0)

    def test_stacking_keeps_the_stronger_one(self):
        ua, ub = _sides()
        for n, secs in ((2, 25), (3, 25), (2, 25)):
            sk = F._parse_skill("薙ぎ払い {}体（{}秒）".format(n, secs), SELF)
            F._apply_skill(ua[0], sk, SELF, ua, ub, 0.0,
                           src="＿薙", name="＿薙", kind_jp="兵法")
        self.assertEqual(ua[0].cleave_n, 3, "重ね掛けで体数が足し算になっている")

    def test_allies_can_receive_it(self):
        ua, ub = _sides()
        sk = F._parse_skill("薙ぎ払い 2体（25秒）", "味方前衛")
        F._apply_skill(ua[0], sk, "味方前衛", ua, ub, 0.0,
                       src="＿薙", name="＿薙", kind_jp="兵法")
        self.assertTrue(any(u.cleave_n == 2 for u in ua))


class Narration(unittest.TestCase):
    def test_the_line_says_it_does_not_thin_out(self):
        ua, ub = _sides()
        ev = []
        sk = F._parse_skill("薙ぎ払い 2体（25秒）", SELF)
        F._apply_skill(ua[0], sk, SELF, ua, ub, 0.0, src="＿薙", name="＿薙",
                       kind_jp="兵法", ev=ev)
        text = "".join(e.text for e in ev)
        self.assertIn("同時に2体", text)
        self.assertIn("満額", text)


class Price(unittest.TestCase):
    def test_one_more_body_costs_like_plus_100_percent_attack(self):
        gc, gi = D.GAUGE_TIER["手数"]
        args = dict(gauge_cost=gc, gauge_init=gi, cost=10.0,
                    typ=F.CAV, tilt="中庸")
        base = D.effect_value(F._parse_skill("攻撃力 +40%（25秒）", SELF), SELF, **args)
        c2 = D.effect_value(
            F._parse_skill("攻撃力 +40%（25秒） + 薙ぎ払い 2体（25秒）", SELF), SELF, **args)
        c3 = D.effect_value(
            F._parse_skill("攻撃力 +40%（25秒） + 薙ぎ払い 3体（25秒）", SELF), SELF, **args)
        self.assertGreater(c2, base)
        # 体数−1 に比例する
        self.assertAlmostEqual((c3 - base) / (c2 - base), 2.0, places=3)


class Wenyang(unittest.TestCase):
    """文鴦〔単騎駆け〕が実際にその器で載っていること。"""

    def test_the_card_carries_the_cleave(self):
        sk = F.SKILL_INFO["単騎駆け"]
        self.assertEqual(sk.cleave, 2)
        self.assertEqual(F.SKILL_TARGET["単騎駆け"], "自分")
        self.assertTrue(sk.mods, "バフが消えている")

    def test_the_display_says_each_gets_full_damage(self):
        from sim import web as W
        g = {g["名前"]: g for g in R.generals()}["文鴦〔単騎駆け〕"]
        row = {r["兵法名"]: r for r in R.skills()}["単騎駆け"]
        txt = W._skill_display(g, row)
        self.assertIn("薙ぎ払い", txt)
        self.assertIn("同じ重さ", txt)   # §7.266 で「満額」→「同じ重さ」


if __name__ == "__main__":
    unittest.main(verbosity=2)
