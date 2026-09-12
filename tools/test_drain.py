# -*- coding: utf-8 -*-
"""吸収（兵ドレイン・§7.247）の受け入れ試験。

テストプレイの指示「司馬懿だけ、ほかの方針がいいな／知力差みて兵ドレインとか？」
「司馬懿は全体にしたいんだけどなし？」から作った器。打撃で**実際に奪った兵**の
`DRAIN_SHARE` ぶんが撃ち手の兵へ戻る。

ここで見張るのは5つ。

1. 戻る量は**盤面へ入った量**（頭打ちの上＝超過ダメージは吸えない）
2. 元の兵力を超えて増えない（`_ledger`/`_men_add` の両方が切る）
3. 帳簿の恒等式（§7.174）が保たれる — 吸収は「回復」として積む
4. 知力比（§7.67）が打撃そのものに掛かる（＝戻る量も同じ倍率で動く）
5. 対象を広げても**戻る総量は変わらない**（総ダメージが頭数で割られるため）。
   値付けが敵側の対象係数を使わない理由がここにある

あわせて、器の無い組み（継続ダメージ・威力なし）を読み込みで断ることと、
実況が吸収を必ず語ることも見張る。
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


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _sides(n=6, cost=4.0, typ=None):
    typ = typ or F.INF
    ua = F.build(_army([F._synth(cost, typ) for _ in range(n)]), 1)
    ub = F.build(_army([F._synth(cost, typ) for _ in range(n)]), -1)
    return ua, ub


def _cast(u, effect, target, own, foe, t=0.0, ev=None):
    sk = F._parse_skill(effect, target)
    F._apply_skill(u, sk, target, own, foe, t, src="＿吸", name="＿吸",
                   kind_jp="兵法", ev=ev)
    return sk


class Parse(unittest.TestCase):
    def test_reads_power_share_and_wits(self):
        sk = F._parse_skill("吸収 威力120%（知力比）", "敵全体")
        self.assertAlmostEqual(sk.power, 1.20)
        self.assertAlmostEqual(sk.drain, F.DRAIN_SHARE)
        self.assertTrue(sk.drain_wits)
        self.assertEqual(sk.dur, 0.0)

    def test_wits_ratio_is_opt_in(self):
        sk = F._parse_skill("吸収 威力120%", "敵全体")
        self.assertAlmostEqual(sk.drain, F.DRAIN_SHARE)
        self.assertFalse(sk.drain_wits)

    def test_knowledge_weighted_kind(self):
        """「ダメージ」の字が無いので計略＝知力寄りの係数になる（司馬懿の狙い）。"""
        sk = F._parse_skill("吸収 威力120%（知力比）", "敵全体")
        self.assertEqual(sk.kind, "scheme")

    def test_refuses_vessels_that_do_not_exist(self):
        """値段は請求するのに盤面が何もしない組みを、読み込みで落とす。"""
        for bad in ("吸収 継続ダメージ 威力40%（14秒）",
                    "吸収 攻撃力 -10%（30秒）"):
            with self.assertRaises(SystemExit):
                F._parse_skill(bad, "敵全体")

    def test_plain_skills_are_untouched(self):
        sk = F._parse_skill("ダメージ 威力600% + 畏怖 -12%（25秒）", "敵1体（正面）")
        self.assertEqual(sk.drain, 0.0)
        self.assertFalse(sk.drain_wits)


class Board(unittest.TestCase):
    def test_returns_the_share_of_what_landed(self):
        ua, ub = _sides()
        ub[0].men *= 0.5           # 撃ち手を半分にして戻る余地を作る
        before = ub[0].men
        taken = [u.men for u in ua]
        _cast(ub[0], "吸収 威力200%", "敵全体", ub, ua)
        got = sum(b - u.men for b, u in zip(taken, ua))
        self.assertGreater(got, 0.0)
        self.assertAlmostEqual(ub[0].men - before, got * F.DRAIN_SHARE, places=6)

    def test_overkill_is_not_absorbed(self):
        """残兵1の隊へ大技を当てても1しか戻らない（超過は吸えない）。"""
        ua, ub = _sides()
        ub[0].men *= 0.5
        before = ub[0].men
        for u in ua[1:]:
            u.men = 0.0            # 対象を1隊に絞る
        ua[0].men = 10.0
        _cast(ub[0], "吸収 威力2000%", "敵全体", ub, ua)
        self.assertEqual(ua[0].men, 0.0)
        self.assertAlmostEqual(ub[0].men - before, 10.0 * F.DRAIN_SHARE, places=6)

    def test_cannot_grow_past_full_strength(self):
        ua, ub = _sides()
        full = ub[0].men0
        self.assertAlmostEqual(ub[0].men, full, places=6)
        _cast(ub[0], "吸収 威力800%", "敵全体", ub, ua)
        self.assertLessEqual(ub[0].men, full + 1e-9)

    def test_no_drain_without_the_word(self):
        ua, ub = _sides()
        ub[0].men *= 0.5
        before = ub[0].men
        _cast(ub[0], "ダメージ 威力200%", "敵全体", ub, ua)
        self.assertAlmostEqual(ub[0].men, before, places=9)

    def test_wits_ratio_moves_the_damage_itself(self):
        """知力比は打撃に掛かる。**戻る量も同じ倍率で動く**（別勘定にしない）。"""
        out = {}
        for label, wits in (("dull", 100.0), ("sharp", 400.0)):
            ua, ub = _sides()
            ub[0].men *= 0.5
            ub[0].wits = 200.0
            for u in ua:
                u.wits = wits
            before = ub[0].men
            taken = [u.men for u in ua]
            _cast(ub[0], "吸収 威力200%（知力比）", "敵全体", ub, ua)
            out[label] = (sum(b - u.men for b, u in zip(taken, ua)),
                          ub[0].men - before)
        # 受け手の知力が低いほうが深く入り、戻りも同じ比で増える
        self.assertGreater(out["dull"][0], out["sharp"][0] * 1.3)
        for dmg, back in out.values():
            self.assertAlmostEqual(back, dmg * F.DRAIN_SHARE, places=6)

    def test_widening_the_target_keeps_the_same_return(self):
        """総ダメージは頭数で割るので、敵全体でも敵1体でも戻る総量は同じ。

        値付け（design.effect_value）が吸収を**自分への回復**として請求し、
        敵側の対象係数を使わない根拠がここにある（敵全体の打撃係数 0.992 は
        敵1体（正面）1.286 より安いので、広げるほど安く買えてしまう）。
        """
        got = {}
        for target in ("敵全体", "敵1体（正面）"):
            ua, ub = _sides()
            ub[0].men *= 0.5
            before = ub[0].men
            _cast(ub[0], "吸収 威力200%", target, ub, ua)
            got[target] = ub[0].men - before
        self.assertAlmostEqual(got["敵全体"], got["敵1体（正面）"],
                               delta=got["敵全体"] * 0.05)


class Ledger(unittest.TestCase):
    """§7.174 の恒等式: 失った兵 = 被ダメ + 代償 + 本陣崩壊 + 壊滅解隊 − 受けた回復。"""

    def test_drain_is_booked_as_healing(self):
        """吸収は「受けた回復」として積む。撃ち手の兵の増分と一致すること。"""
        ua, ub = _sides()
        ub[0].men *= 0.5
        before = ub[0].men
        _cast(ub[0], "吸収 威力200%", "敵全体", ub, ua)
        self.assertGreater(ub[0].heal_taken, 0.0)
        self.assertAlmostEqual(ub[0].heal_taken, ub[0].healed, places=6)
        self.assertEqual(ub[0].taken, 0.0)          # 撃ち手は殴られていない
        self.assertAlmostEqual(ub[0].men - before, ub[0].heal_taken, places=6)

    def test_identity_holds_through_a_real_battle(self):
        """司馬懿を入れた実カードの一戦で、§7.174 の恒等式が崩れないこと。"""
        import sim.match as M
        import sim.play as PL
        cards = M._roster_cards()
        a, ea = PL.parse_deck(cards, "司馬懿〔冢虎〕、荀彧〔王佐〕、賈詡〔毒士〕、"
                              "楽進〔先登〕、李典〔慎重〕、于禁〔毅重〕", "雁行")
        b, eb = PL.parse_deck(cards, "関羽〔漢寿亭侯〕、張飛〔当陽橋〕、趙雲〔長坂坡〕、"
                              "馬良〔白眉〕、陳到〔白毦〕、王平〔無当〕", "鶴翼")
        self.assertFalse(ea or eb, (ea, eb))
        for seed in (4242, 7):
            for dt in (0.5, 0.25):
                r = F.simulate(a, b, dt, seed=seed)
                for row in r["dealt_a"] + r["dealt_b"]:
                    n, m, m0 = row[0], row[3], row[4]
                    od, ot, fo, sc, ht, cl, wl = row[-9:-2]
                    tk = row[12]
                    self.assertAlmostEqual(m0 - m, tk + sc + cl + wl - ht,
                                           delta=1e-6 * max(m0, 1.0), msg=str(n))


class Narration(unittest.TestCase):
    def test_the_line_always_says_where_the_men_went(self):
        ua, ub = _sides()
        ub[0].men *= 0.5
        ev = []
        _cast(ub[0], "吸収 威力3000%", "敵全体", ub, ua, ev=ev)
        text = "".join(e.text for e in ev)
        self.assertIn("吸い込む", text)

    def test_nothing_absorbed_says_nothing(self):
        """満タンの隊が撃っても戻らない → 添え書きは出ない（嘘を書かない）。"""
        ua, ub = _sides()
        ev = []
        _cast(ub[0], "吸収 威力3000%", "敵全体", ub, ua, ev=ev)
        self.assertTrue([e for e in ev if e.text], "行そのものが出ていない")
        self.assertNotIn("吸い込む", "".join(e.text for e in ev))


class Price(unittest.TestCase):
    def test_return_is_charged_as_a_self_heal(self):
        """吸収ぶんの請求が、敵側の対象文で動かないこと。"""
        def val(target):
            sk = F._parse_skill("吸収 威力200%", target)
            plain = F._parse_skill("ダメージ 威力200%", target)
            gc, gi = D.GAUGE_TIER["標準"]
            args = dict(gauge_cost=gc, gauge_init=gi, cost=10.0,
                        typ=F.ARC, tilt="中庸")
            return (D.effect_value(sk, target, **args)
                    - D.effect_value(plain, target, **args))
        wide, one = val("敵全体"), val("敵1体（正面）")
        self.assertGreater(wide, 0.0)
        self.assertAlmostEqual(wide, one, places=6)

    def test_bigger_share_costs_more(self):
        gc, gi = D.GAUGE_TIER["標準"]
        args = dict(gauge_cost=gc, gauge_init=gi, cost=10.0,
                    typ=F.ARC, tilt="中庸")
        sk = F._parse_skill("吸収 威力200%", "敵全体")
        lo = D.effect_value(sk, "敵全体", **args)
        big = sk.__class__(**{**sk.__dict__, "drain": sk.drain * 2.0})
        self.assertGreater(D.effect_value(big, "敵全体", **args), lo)

    def test_wits_ratio_is_charged(self):
        gc, gi = D.GAUGE_TIER["標準"]
        args = dict(gauge_cost=gc, gauge_init=gi, cost=10.0, typ=F.ARC)
        sk = F._parse_skill("吸収 威力200%（知力比）", "敵全体")
        flat = F._parse_skill("吸収 威力200%", "敵全体")
        # 智将（傾き 1.90）が撃つなら、知力比のぶんだけ高い
        self.assertGreater(D.effect_value(sk, "敵全体", tilt="智将", **args),
                           D.effect_value(flat, "敵全体", tilt="智将", **args))
        # 中庸（傾き 1.00）なら同額（比が 1 なので伸びない）
        self.assertAlmostEqual(D.effect_value(sk, "敵全体", tilt="中庸", **args),
                               D.effect_value(flat, "敵全体", tilt="中庸", **args),
                               places=6)


class Shimai(unittest.TestCase):
    """司馬懿〔冢虎〕【堅忍】が実際にその器で載っていること。"""

    def test_the_card_carries_the_drain(self):
        sk = F.SKILL_INFO["堅忍"]
        self.assertAlmostEqual(sk.drain, F.DRAIN_SHARE)
        self.assertTrue(sk.drain_wits)
        self.assertEqual(F.SKILL_TARGET["堅忍"], "敵全体")

    def test_the_display_names_the_absorption(self):
        from sim import web as W
        g = {g["名前"]: g for g in R.generals()}["司馬懿〔冢虎〕"]
        row = {r["兵法名"]: r for r in R.skills()}["堅忍"]
        txt = W._skill_display(g, row)
        self.assertIn("吸収", txt)
        self.assertIn("知略", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
