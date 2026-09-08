# -*- coding: utf-8 -*-
"""§7.199 で足した3つの器の受け入れ試験。

  ① 逐次（「→ その後」）  … 後半は前半が切れてから始まる
  ② 増幅                 … ＜的の状態＞の敵への損害が全部の入口で増える
  ③ その発動者           … 誘発が「引き金を引いた敵」を狙い返す
  ④ 自分と右隣           … 枠の順で次の1体・右端なら自分だけ

**器の試験であって、値段や強さの試験ではない。** 4枚（廖化・于禁・郭嘉・龐統）の
釣り合いは skill_panel で別に測る（§7.199 の記録）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import field as F        # noqa: E402
from sim import match as M        # noqa: E402
from sim import rosterdata as R   # noqa: E402


def _cards():
    R.load_skills_into_field()
    R.load_traits_into_field()
    F.SKILLS_ON = F.TRAITS_ON = True
    return {c.name: c for c in M._roster_cards()}


class Sequence(unittest.TestCase):
    """① 逐次。「A（n秒） → その後 B（m秒）」を書いたとおりに動かす。"""

    def test_parse_splits_head_and_tail(self):
        sk = F._parse_skill("防御力 +30%（30秒） → その後 攻撃力 +30%（20秒）", "自分")
        self.assertEqual([k for k, _a, _s in sk.mods], ["def"])
        self.assertEqual([k for k, _a, _s in sk.after_mods], ["atk"])
        # 秒数は縮尺（SKILL_DUR_SCALE）を通ったあとの盤面の秒
        self.assertAlmostEqual(sk.mods[0][2], 30.0 * F.SKILL_DUR_SCALE)
        self.assertAlmostEqual(sk.after_mods[0][2], 20.0 * F.SKILL_DUR_SCALE)

    def test_tail_starts_after_head_expires(self):
        cards = _cards()
        names = ("于禁〔毅重〕", "曹仁〔堅守〕", "張嶷〔越巂〕",
                 "韓当〔老弓〕", "孫尚香〔弓腰姫〕", "張昭〔子布〕")
        ua = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), +1)
        ub = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), -1)
        u = ua[0]
        F._apply_skill(u, F.SKILL_INFO["毅重"], "自分と右隣", ua, ub, 0.0,
                       src="毅重", name="毅重")
        wait = 30.0 * F.SKILL_DUR_SCALE
        F._expire(ua, wait * 0.3)
        self.assertGreater(u.def_mult, 1.2, "前半の守りが立っていない")
        self.assertAlmostEqual(u.atk_mult, 1.0, msg="後半が早く始まっている")
        F._expire(ua, wait + 1.0)
        self.assertAlmostEqual(u.def_mult, 1.0, msg="前半が切れていない")
        self.assertGreater(u.atk_mult, 1.2, "後半が始まっていない")
        F._expire(ua, wait + 20.0 * F.SKILL_DUR_SCALE + 1.0)
        self.assertAlmostEqual(u.atk_mult, 1.0, msg="後半が切れていない")

    def test_no_arrow_means_no_tail(self):
        sk = F._parse_skill("防御力 +30%（30秒）", "自分")
        self.assertEqual(sk.after_mods, ())

    def test_tail_points_the_same_way_as_the_head(self):
        """向き先は前半と同じ規則（`ally` と符号）で決まる。

        後半だけ「対象へ配る」にしてしまうと、敵を狙う兵法の「その後 自分の
        攻撃力 +N%」が**敵を強化する**。廖化・于禁ではたまたま同じ答になるので、
        取り違えたままでも気づけない。ここで釘を打っておく。
        """
        cards = _cards()
        names = ("廖化〔老将〕", "曹仁〔堅守〕", "張嶷〔越巂〕",
                 "韓当〔老弓〕", "孫尚香〔弓腰姫〕", "張昭〔子布〕")
        ua = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), +1)
        ub = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), -1)
        u = ua[0]
        # 敵を狙う兵法の後半が**プラス** → 前半と同じで、上がるのは撃った本人
        sk = F._parse_skill("行動阻害 3秒 → その後 攻撃力 +30%（20秒）", "敵1体（正面）")
        F._apply_skill(u, sk, "敵1体（正面）", ua, ub, 0.0, src="試", name="試")
        F._expire(ua, 3.0 * F.SKILL_DUR_SCALE + 1.0)
        F._expire(ub, 3.0 * F.SKILL_DUR_SCALE + 1.0)
        self.assertGreater(u.atk_mult, 1.2, "撃った本人が強くなっていない")
        self.assertTrue(all(abs(x.atk_mult - 1.0) < 1e-9 for x in ub),
                        "敵を強化してしまっている")

    def test_tail_refuses_what_it_cannot_carry(self):
        """待ち行列は「効果の山」へ移すだけなので、山の外に器を持つものは断る。"""
        for eff in ("防御力 +30%（30秒） → その後 混乱 20%（20秒）",
                    "防御力 +30%（30秒） → その後 兵法打消し 1発（20秒）"):
            with self.assertRaises(SystemExit):
                F._parse_skill(eff, "自分")


class RightNeighbour(unittest.TestCase):
    """④ 自分と右隣。**枠の順**なので同距離の曖昧さが無い。"""

    def setUp(self):
        cards = _cards()
        names = ("于禁〔毅重〕", "曹仁〔堅守〕", "張嶷〔越巂〕",
                 "韓当〔老弓〕", "孫尚香〔弓腰姫〕", "張昭〔子布〕")
        self.ua = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), +1)
        self.ub = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), -1)

    def _t(self, i):
        return [x.name for x in F._skill_targets("自分と右隣", self.ua[i], self.ub, self.ua)]

    def test_takes_the_next_slot_in_the_same_row(self):
        self.assertEqual(self._t(0), ["于禁〔毅重〕", "曹仁〔堅守〕"])
        self.assertEqual(self._t(3), ["韓当〔老弓〕", "孫尚香〔弓腰姫〕"])

    def test_right_edge_is_self_only(self):
        # 魚鱗は前3・後3。前衛の右端（枠2）と後衛の右端（枠5）は自分だけ
        self.assertEqual(self._t(2), ["張嶷〔越巂〕"])
        self.assertEqual(self._t(5), ["張昭〔子布〕"])

    def test_does_not_cross_rows(self):
        self.assertNotIn("韓当〔老弓〕", self._t(2))


class Amplify(unittest.TestCase):
    """② 増幅。損害の入口が複数あるので、**器を1つ**にしてある。"""

    def setUp(self):
        self.cards = _cards()
        names = ("龐統〔鳳雛〕", "曹仁〔堅守〕", "張嶷〔越巂〕",
                 "韓当〔老弓〕", "孫尚香〔弓腰姫〕", "張昭〔子布〕")
        self.us = F.build(F.Army([self.cards[n] for n in names], F.FORM_STANDARD), +1)
        F._apply_amplify(self.us)
        self.foe = F.build(F.Army([self.cards[n] for n in names], F.FORM_STANDARD), -1)

    def test_table_is_read_from_csv(self):
        self.assertIn("insight", F.AMPLIFY)
        self.assertEqual(F.AMPLIFY["insight"], ("stun", "all", 0.10))

    def test_only_while_the_target_is_stunned(self):
        f = self.foe[0]
        f.stunned = False
        self.assertAlmostEqual(F._amp_mult(self.us[0], f, "normal"), 1.0)
        f.stunned = True
        for kind in ("normal", "skill", "dot"):
            self.assertAlmostEqual(F._amp_mult(self.us[0], f, kind), 1.10,
                                   msg="{} の入口に増幅が通っていない".format(kind))

    def test_whole_army_carries_it(self):
        self.assertTrue(all(u.amp for u in self.us), "軍全体に配られていない")

    def test_off_without_traits(self):
        f = self.foe[0]
        f.stunned = True
        F.TRAITS_ON = False
        try:
            self.assertAlmostEqual(F._amp_mult(self.us[0], f, "normal"), 1.0)
        finally:
            F.TRAITS_ON = True


class TriggerCaster(unittest.TestCase):
    """③ その発動者。対象表は「誰が撃ったか」を知らないので名指しで渡す。"""

    def test_trait_is_loaded(self):
        _cards()
        cond, target, cap, _sk, jp = F.TRAITS["foresight"]
        self.assertEqual((cond, target, cap, jp),
                         ("foe_skill", "その発動者", 3, "機先"))

    def test_fires_at_the_caster_in_a_real_battle(self):
        cards = _cards()
        a = F.Army([cards[n] for n in ("郭嘉〔鬼才〕", "龐統〔鳳雛〕", "廖化〔老将〕",
                                       "曹仁〔堅守〕", "張嶷〔越巂〕", "韓当〔老弓〕")],
                   F.FORM_STANDARD)
        b = F.Army([cards[n] for n in ("周瑜〔赤壁〕", "陸遜〔夷陵〕", "張角〔太平道〕",
                                       "許褚〔虎痴〕", "張飛〔当陽橋〕", "王平〔無当〕")],
                   F.FORM_STANDARD)
        ev = []
        F.simulate(a, b, dt=0.25, events=ev, seed=7)
        lines = [e.text for e in ev if "機先" in e.text]
        self.assertTrue(lines, "機先が一度も発動していない")
        self.assertLessEqual(len(lines), 3, "1戦3回までを超えている")
        # 狙った相手は**そのとき兵法を撃った敵**（敵の名前が文に入る）
        foes = {c.name for c in b.cards}
        self.assertTrue(any(any(n in ln for n in foes) for ln in lines),
                        "発動者を狙っていない: {}".format(lines))


class ZeroPoint(unittest.TestCase):
    """器を足しても同じ編成どうしは 0.00 のまま（§7.199）。"""

    def test_mirror_is_zero(self):
        cards = _cards()
        names = ("于禁〔毅重〕", "曹仁〔堅守〕", "張嶷〔越巂〕",
                 "韓当〔老弓〕", "孫尚香〔弓腰姫〕", "張昭〔子布〕")
        army = F.Army([cards[n] for n in names], F.FORM_STANDARD)
        r = F.simulate(army, army, dt=0.25)
        self.assertAlmostEqual(r["diff"], 0.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=1)
