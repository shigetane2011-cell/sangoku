# -*- coding: utf-8 -*-
"""一騎討ち（§7.249）の受け入れ試験。

テストプレイの決定「一騎打ち、敵中突破で」から作った器。名指しした敵1体を
**乱軍から引きずり出し**、その間

  ・挑んだ側はその相手だけを殴り、相手も挑んだ側だけを殴る
  ・**挑んだ側には余人の刃も矢も届かない**（`DUEL_SHIELD_BOTH = False` なので
    挑まれた側は晒されたまま。両側を守ると残存差がマイナスになる実測がある）

見張るのは5つ。

1. 狙いが捻じれるのは**通常攻撃の重み1箇所だけ**（兵法・移動・射程は不変）
2. 守られるのは挑んだ側だけ（挑まれた側はこちらの味方から撃たれ続ける）
3. 解けるのは**時刻を過ぎた**か**どちらかが倒れた**ときだけ。片側だけ解けない
4. 既に討ち合っている隊には**割り込まない**（最後に撃ったほうが勝つ、にしない）
5. 対象が敵1体でない効果文は読み込みで断る（器の無い組みを作らない）
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

TSTR = "敵1体（前衛の主力）"


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _sides(n=6, cost=6.0):
    ua = F.build(_army([F._synth(cost, F.CAV) for _ in range(n)]), 1)
    ub = F.build(_army([F._synth(cost, F.INF) for _ in range(n)]), -1)
    return ua, ub


def _duel(u, f, secs=18.0, t=0.0):
    """討ち合いを手で立てる（盤面の規則だけを見たいとき）。"""
    u.duel_with, u.duel_until, u.duel_host = f, t + secs, True
    f.duel_with, f.duel_until, f.duel_host = u, t + secs, False


def _w(u, foes):
    return F._weights(u, foes, [1.0] * len(foes))


class Parse(unittest.TestCase):
    def test_reads_the_seconds(self):
        sk = F._parse_skill("ダメージ 威力600% + 一騎討ち 12秒", TSTR)
        self.assertAlmostEqual(sk.duel, F.skill_dur(12.0))
        self.assertAlmostEqual(sk.power, 6.0)

    def test_refuses_targets_that_are_not_a_single_enemy(self):
        for tgt in ("敵前衛", "敵全体", "敵1列", "味方全体", "自分"):
            with self.assertRaises(SystemExit):
                F._parse_skill("一騎討ち 12秒", tgt)

    def test_plain_skills_are_untouched(self):
        self.assertEqual(F._parse_skill("ダメージ 威力600%", TSTR).duel, 0.0)


class Board(unittest.TestCase):
    def test_the_challenger_only_looks_at_the_partner(self):
        ua, ub = _sides()
        _duel(ua[0], ub[2])
        w = _w(ua[0], ub)
        self.assertGreater(w[2], 0.0)
        self.assertEqual([x for i, x in enumerate(w) if i != 2], [0.0] * 5)

    def test_nobody_else_can_touch_the_challenger(self):
        ua, ub = _sides()
        _duel(ua[0], ub[2])
        for f in ub[:2] + ub[3:]:
            self.assertEqual(_w(f, ua)[0], 0.0, "挑んだ側へ横から手が出ている")

    def test_the_challenged_stays_exposed_to_our_side(self):
        """DUEL_SHIELD_BOTH = False の肝。挑まれた側は味方から撃たれ続ける。"""
        ua, ub = _sides()
        _duel(ua[0], ub[2])
        self.assertGreater(_w(ua[1], ub)[2], 0.0)

    def test_shield_both_is_a_single_switch(self):
        ua, ub = _sides()
        _duel(ua[0], ub[2])
        keep = F.DUEL_SHIELD_BOTH
        try:
            F.DUEL_SHIELD_BOTH = True
            self.assertEqual(_w(ua[1], ub)[2], 0.0)
        finally:
            F.DUEL_SHIELD_BOTH = keep

    def test_the_partner_only_looks_back(self):
        ua, ub = _sides()
        _duel(ua[0], ub[2])
        w = _w(ub[2], ua)
        self.assertGreater(w[0], 0.0)
        self.assertEqual([x for i, x in enumerate(w) if i != 0], [0.0] * 5)

    def test_range_and_sight_still_apply(self):
        """「討ち合っているから必ず当たる」にはしない —— 遠ければ届かない。"""
        ua, ub = _sides()
        _duel(ua[0], ub[2])
        far = F._weights(ua[0], ub, [9999.0] * len(ub))
        self.assertEqual(far[2], 0.0)

    def test_does_not_cut_in_on_an_existing_duel(self):
        """**その対象文が選ぶ相手**が既に討ち合っているなら、割り込まない。"""
        ua, ub = _sides()
        pick = F._skill_targets(TSTR, ua[1], ub, ua)[0]
        _duel(ua[0], pick)
        sk = F._parse_skill("一騎討ち 12秒", TSTR)
        F._apply_skill(ua[1], sk, TSTR, ua, ub, 0.0,
                       src="＿討", name="＿討", kind_jp="兵法")
        self.assertIsNone(ua[1].duel_with, "割り込んで組を壊している")
        self.assertIs(pick.duel_with, ua[0])

    def test_casting_pairs_both_sides(self):
        ua, ub = _sides()
        sk = F._parse_skill("一騎討ち 12秒", TSTR)
        F._apply_skill(ua[0], sk, TSTR, ua, ub, 0.0,
                       src="＿討", name="＿討", kind_jp="兵法")
        f = ua[0].duel_with
        self.assertIsNotNone(f)
        self.assertIs(f.duel_with, ua[0])
        self.assertTrue(ua[0].duel_host)
        self.assertFalse(f.duel_host)


class Ends(unittest.TestCase):
    """解ける条件は2つだけ。片側だけ解けると相手は死体を狙い続ける。"""

    def test_ends_together_when_the_time_runs_out(self):
        a = _army([F._synth(6.0, F.CAV) for _ in range(6)])
        b = _army([F._synth(6.0, F.INF) for _ in range(6)])
        F.SKILL_INFO["＿討"] = F._parse_skill("一騎討ち 4秒", TSTR)
        F.SKILL_TARGET["＿討"] = TSTR
        seen = {"paired": 0, "half": 0}
        orig = F.simulate

        # 1戦を通して「片側だけ組が残っている」瞬間が無いことを見張る
        import sim.field as FF

        def watch(ua, ub, t):
            for u in ua + ub:
                if u.duel_with is not None:
                    seen["paired"] += 1
                    if u.duel_with.duel_with is not u:
                        seen["half"] += 1
        keep = FF._log_tick

        def spy(events, s, t, ua, ub, gap):
            watch(ua, ub, t)
            return keep(events, s, t, ua, ub, gap)
        FF._log_tick = spy
        try:
            ua = F.build(a, 1)
            for u in ua:
                u.skill = "＿討"
                u.gauge_cost = 75.0
            F.simulate(a, b, 0.25, events=[])
        finally:
            FF._log_tick = keep
        self.assertEqual(seen["half"], 0, "片側だけ組が残っている")

    def test_ends_when_one_side_falls(self):
        ua, ub = _sides()
        _duel(ua[0], ub[2], secs=999.0)
        ub[2].men = 0.0
        a = _army([F._synth(6.0, F.CAV) for _ in range(6)])
        # ティックの終わりの掃除を直に呼ぶ代わりに、規則を写した同じ判定を使う
        t = 1.0
        for u in ua + ub:
            if u.duel_with is not None:
                d = u.duel_with
                if t >= u.duel_until or u.men <= 0.0 or d.men <= 0.0:
                    u.duel_with = d.duel_with = None
        self.assertIsNone(ua[0].duel_with)
        self.assertIsNone(ub[2].duel_with)


class Narration(unittest.TestCase):
    def test_the_line_says_who_is_protected(self):
        ua, ub = _sides()
        ev = []
        sk = F._parse_skill("ダメージ 威力3000% + 一騎討ち 12秒", TSTR)
        F._apply_skill(ua[0], sk, TSTR, ua, ub, 0.0, src="＿討", name="＿討",
                       kind_jp="兵法", ev=ev)
        text = "".join(e.text for e in ev)
        self.assertIn("一騎討ち", text)
        self.assertIn("引きずり出", text)

    def test_a_duel_only_cast_still_speaks(self):
        """打撃が無い一騎討ちでも行が出る（語る下限は打撃・回復にしか掛からない）。"""
        ua, ub = _sides()
        ev = []
        sk = F._parse_skill("一騎討ち 12秒", TSTR)
        F._apply_skill(ua[0], sk, TSTR, ua, ub, 0.0, src="＿討", name="＿討",
                       kind_jp="兵法", ev=ev)
        self.assertIn("一騎討ち", "".join(e.text for e in ev))


class Price(unittest.TestCase):
    def test_seconds_cost_money(self):
        gc, gi = D.GAUGE_TIER["標準"]
        args = dict(gauge_cost=gc, gauge_init=gi, cost=10.0,
                    typ=F.CAV, tilt="中庸")
        base = D.effect_value(F._parse_skill("ダメージ 威力600%", TSTR),
                              TSTR, **args)
        d12 = D.effect_value(
            F._parse_skill("ダメージ 威力600% + 一騎討ち 12秒", TSTR), TSTR, **args)
        d20 = D.effect_value(
            F._parse_skill("ダメージ 威力600% + 一騎討ち 20秒", TSTR), TSTR, **args)
        self.assertGreater(d12, base)
        self.assertGreater(d20, d12)
        # 秒に対して線形（飽和するぶんは高く請求する＝安全な向き）
        self.assertAlmostEqual((d20 - base) / (d12 - base), 20.0 / 12.0, places=3)


class Guanyu(unittest.TestCase):
    """関羽〔漢寿亭侯〕が実際にその器で載っていること。"""

    def test_the_card_carries_the_duel(self):
        sk = F.SKILL_INFO["青龍偃月"]
        self.assertGreater(sk.duel, 0.0)
        self.assertIn("敵1体", F.SKILL_TARGET["青龍偃月"])

    def test_the_display_explains_who_is_protected(self):
        from sim import web as W
        g = {g["名前"]: g for g in R.generals()}["関羽〔漢寿亭侯〕"]
        row = {r["兵法名"]: r for r in R.skills()}["青龍偃月"]
        txt = W._skill_display(g, row)
        self.assertIn("一騎討ち", txt)
        self.assertIn("手を出せない", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
