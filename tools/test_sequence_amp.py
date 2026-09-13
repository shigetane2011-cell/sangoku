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


class AfterPrice(unittest.TestCase):
    """後半の値段は**待った秒数のぶん割り引く**（§7.200）。"""

    def test_curve_matches_the_measurement(self):
        from sim import design as DS
        for wait, want in ((0.0, 1.00), (15.0, 0.52), (30.0, 0.35), (45.0, 0.27)):
            self.assertAlmostEqual(DS.after_f(wait), want, places=2,
                                   msg="待ち {:g}秒".format(wait))

    def test_delayed_tail_is_cheaper_than_the_same_thing_up_front(self):
        """同じ量でも「その後」に書けば安い。**同時配りより高くなってはいけない。**"""
        from sim import design as DS
        tgt = "自分と右隣"
        both = F._parse_skill("防御力 +30%（30秒） + 攻撃力 +30%（20秒）", tgt)
        seq = F._parse_skill("防御力 +30%（30秒） → その後 攻撃力 +30%（20秒）", tgt)
        v_both = DS.effect_value(both, tgt, gauge_cost=150.0, gauge_init=60.0)
        v_seq = DS.effect_value(seq, tgt, gauge_cost=150.0, gauge_init=60.0)
        self.assertLess(v_seq, v_both)
        # 前半は同じなので、差は後半に掛かる掛け目のぶんだけ
        head = F._parse_skill("防御力 +30%（30秒）", tgt)
        v_head = DS.effect_value(head, tgt, gauge_cost=150.0, gauge_init=60.0)
        tail = (v_both - v_head)
        self.assertAlmostEqual(v_seq - v_head,
                               tail * DS.after_f(30.0 * F.SKILL_DUR_SCALE), places=3)


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
        self.assertEqual(F.AMPLIFY["insight"], ("stun", "all", 0.10, "army"))

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


class AmplifyScopeAndBurn(unittest.TestCase):
    """【§7.212】増幅に足した2つ — 範囲「自分だけ」と条件「延焼中」。

    盤面へ出した札は無い（満寵の作り替えは裁定待ち）ので、器そのものを
    合成の特性で試す。**器を先に作って測ったときのまま**動くことを守る。
    """

    def setUp(self):
        self.cards = _cards()
        names = ("龐統〔鳳雛〕", "曹仁〔堅守〕", "張嶷〔越巂〕",
                 "韓当〔老弓〕", "孫尚香〔弓腰姫〕", "張昭〔子布〕")
        self.army = F.Army([self.cards[n] for n in names], F.FORM_STANDARD)
        self.foe = F.build(F.Army([self.cards[n] for n in names], F.FORM_STANDARD), -1)
        self._saved = dict(F.AMPLIFY)

    def tearDown(self):
        F.AMPLIFY.clear(); F.AMPLIFY.update(self._saved)

    def _build(self, entry):
        """先頭の札にだけ試験用の特性を足して1軍を組む。"""
        from dataclasses import replace
        key = "_t212"
        F.AMPLIFY[key] = entry
        cs = list(self.army.cards)
        cs[0] = replace(cs[0], trait=(cs[0].trait + F.TRAIT_SEP + key).strip(F.TRAIT_SEP))
        us = F.build(F.Army(tuple(cs), self.army.form), +1)
        F._apply_amplify(us)
        return us

    def test_self_scope_stays_on_the_holder(self):
        us = self._build(("", "skill", 0.5, "self"))
        holder = [u for u in us if "_t212" in u.traits][0]
        others = [u for u in us if "_t212" not in u.traits]
        f = self.foe[0]
        self.assertAlmostEqual(F._amp_mult(holder, f, "skill"), 1.5)
        for u in others:
            self.assertAlmostEqual(F._amp_mult(u, f, "skill"), 1.0,
                                   msg="範囲 self が軍全体へ漏れている")

    def test_army_scope_still_reaches_everyone(self):
        us = self._build(("", "skill", 0.5, "army"))
        f = self.foe[0]
        for u in us:
            self.assertAlmostEqual(F._amp_mult(u, f, "skill"), 1.5)

    def test_burn_needs_a_dot_on_the_target(self):
        us = self._build(("burn", "skill", 0.5, "self"))
        holder = [u for u in us if "_t212" in u.traits][0]
        f = self.foe[0]
        f.overtime = []
        self.assertAlmostEqual(F._amp_mult(holder, f, "skill"), 1.0)
        f.overtime = [(99.0, "heal", 10.0, None)]          # 回復では点かない
        self.assertAlmostEqual(F._amp_mult(holder, f, "skill"), 1.0)
        f.overtime = [(99.0, "dot", 10.0, None)]
        self.assertAlmostEqual(F._amp_mult(holder, f, "skill"), 1.5)

    def test_kind_still_filters(self):
        us = self._build(("burn", "skill", 0.5, "self"))
        holder = [u for u in us if "_t212" in u.traits][0]
        f = self.foe[0]
        f.overtime = [(99.0, "dot", 10.0, None)]
        self.assertAlmostEqual(F._amp_mult(holder, f, "normal"), 1.0,
                               msg="兵法だけの増幅が通常攻撃にも乗っている")

    def test_loader_reads_the_self_verb(self):
        """自己増幅／すべて／延焼中 の語彙が読めること（CSV の1行と同じ形）。"""
        import re
        m = re.match(r"(自己)?増幅\s*(\S+?)の敵への(\S+?)\s*\+(\d+)%",
                     "自己増幅 延焼中の敵への兵法 +50%")
        self.assertIsNotNone(m)
        self.assertEqual(F.AMP_COND_JP.get(m.group(2)), "burn")
        self.assertEqual(F.AMP_KIND_JP.get(m.group(3)), "skill")
        self.assertEqual("self" if m.group(1) else "army", "self")
        self.assertEqual(F.AMP_COND_JP.get("すべて"), "")   # 無条件


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


class TriggerWording(unittest.TestCase):
    """発動条件の和訳は**表に全部そろっていること**（§7.201）。

    `web._TRAIT_CONDS` に無い条件は、例外にならず**黙って空欄**で出る。
    実際 `ally_low_hp` が抜けていて、救護8枚の条件が画面にも API にも
    出ていなかった。表と CSV がずれたらここで落とす。
    """

    def test_every_condition_has_japanese(self):
        import re
        from sim import web as W
        miss = []
        for t in R.traits():
            if t["型"] != "誘発":
                continue
            m = re.search(r"(\w+) で発動", t["備考"] or "")
            k = m.group(1) if m else ""
            if k not in W._TRAIT_CONDS or not W._TRAIT_CONDS[k]:
                miss.append("{}（{}）".format(t["キー"], k or "条件なし"))
        self.assertEqual(miss, [], "発動条件の和訳が無い: " + "、".join(miss))

    def test_relief_condition_is_not_the_rout_one(self):
        """救護は**崩れる前**に効く。`ally_retreat` と同じ文にしない（§7.190）。"""
        from sim import web as W
        tr = {t["キー"]: t for t in R.traits()}
        for k in ("relief", "relief1"):
            _desc, cond = W._trait_brief(None, k, tr[k])
            self.assertIn(W._TRAIT_CONDS["ally_low_hp"], cond, k)
            self.assertNotIn(W._TRAIT_CONDS["ally_retreat"], cond, k)
        self.assertNotIn("崩れた", tr["relief"].get("説明", ""),
                         "補足説明が古い条件のまま（APIにも渡る）")


class SameTick(unittest.TestCase):
    """同じ刻に複数の敵が撃った時（§7.201・仕様として明示）。"""

    def test_one_trigger_covers_every_caster(self):
        """**1回分の誘発で発動者全員が対象**になり、回数は1回だけ減る。

        特性は1ティック1回しか発動しない（`_fire_traits`）ので、同じ刻に
        2枚の敵が撃っても引き金は1回。その1回で「その発動者」を全員名指しする。
        """
        cards = _cards()
        names = ("郭嘉〔鬼才〕", "曹仁〔堅守〕", "張嶷〔越巂〕",
                 "韓当〔老弓〕", "孫尚香〔弓腰姫〕", "張昭〔子布〕")
        ua = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), +1)
        ub = F.build(F.Army([cards[n] for n in names], F.FORM_STANDARD), -1)
        me = ua[0]
        two = [ub[0], ub[1]]           # 同じ刻に2枚が撃った、という盤面
        retired = {"new": [], "dead": []}
        F._fire_traits(ua, ub, 10.0, retired, [], set(), fired_skill=set(),
                       offense=set(two))
        self.assertEqual(me.fired.get("foresight"), 1, "回数は1回だけ減る")
        self.assertTrue(all(x.glock for x in two),
                        "同じ刻に撃った全員が対象にならない")
        self.assertFalse(any(x.glock for x in ub[2:]), "撃っていない隊まで巻き込む")


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
