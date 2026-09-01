# -*- coding: utf-8 -*-
"""宝物で足したキーは、対戦相手にも名指しされないことの回帰（§7.136・§7.138）。

生まれつきの特性はこれまで通り実況に名前が出て、宝物（旧・恩賞）で後から
足したキーだけ名前を伏せる — その境目を、誘発型・常在型（陣頭・本陣）の
3経路それぞれで確かめる。数値・勝敗が変わらないことまでは見ない（狙いは
表示だけで、そちらは既存の回帰が担保する）。
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F        # noqa: E402
from sim import match as M        # noqa: E402
from sim import play as PL        # noqa: E402
from sim import players as P      # noqa: E402
from sim import rosterdata as R   # noqa: E402


class TreasurePrivacyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not F.TRAITS:
            R.load_traits_into_field()
        if not F.SKILL_INFO:
            R.load_skills_into_field()
        cls._traits_on = F.TRAITS_ON
        F.TRAITS_ON = True   # 陣頭・本陣の数値・実況判定はこれが立っていないと通らない

    @classmethod
    def tearDownClass(cls):
        F.TRAITS_ON = cls._traits_on

    # ---- 誘発型（背水など）: _fire_traits と同じ経路を直接叩く ----

    def _fire_laststand(self, hidden: bool) -> str:
        cond, target, cap, sk, jp = F.TRAITS["laststand"]
        card = F._synth(4.0, F.INF)
        if hidden:
            card = dataclasses.replace(card, trait="laststand",
                                        hidden_trait="laststand")
        cards = (card,) + tuple(F._synth(4.0, F.INF) for _ in range(5))
        ua = F.build(F.Army(cards, F.FORM_STANDARD), 1)
        ub = F.build(F.flat_army(cost=4.0, typ=F.INF), -1)
        events = []
        F._apply_skill(ua[0], sk, target, ua, ub, 0.0, src="laststand",
                        ev=events, seen=set(), name=jp, kind_jp="誘発")
        self.assertTrue(events, "buff should have produced a narration line")
        return events[0].text

    def test_hidden_triggered_trait_hides_its_name(self):
        text = self._fire_laststand(hidden=True)
        self.assertNotIn("背水", text)
        self.assertIn("秘策", text)

    def test_native_triggered_trait_still_shows_its_name(self):
        text = self._fire_laststand(hidden=False)
        self.assertIn("背水", text)
        self.assertNotIn("秘策", text)

    # ---- 常在型: 陣頭（vanguard）----

    def _vanguard_army(self, hidden: bool) -> F.Army:
        card = dataclasses.replace(
            F._synth(4.0, F.INF), name="陣頭花子", trait="vanguard",
            hidden_trait="vanguard" if hidden else "")
        cards = (card,) + tuple(F._synth(4.0, F.INF) for _ in range(5))
        return F.Army(cards, F.FORM_STANDARD)

    def test_hidden_vanguard_omitted_from_opening_line(self):
        lines = F.narrate(self._vanguard_army(True),
                          F.flat_army(cost=4.0, typ=F.INF), dt=0.25, seed=1)
        open_line = next(l for l in lines if "の陣を布く" in l)
        # 名前が他の行（通常の戦況実況）に出るのは構わない — 消すのは
        # 「陣頭に立つ」という**この特性の名指し**だけ。
        self.assertNotIn("陣頭花子", open_line)
        self.assertNotIn("陣頭に立つ", open_line)

    def test_native_vanguard_named_in_opening_line(self):
        lines = F.narrate(self._vanguard_army(False),
                          F.flat_army(cost=4.0, typ=F.INF), dt=0.25, seed=1)
        open_line = next(l for l in lines if "の陣を布く" in l)
        self.assertIn("陣頭花子", open_line)
        self.assertIn("陣頭に立つ", open_line)

    # ---- 常在型: 本陣（command）----

    def _command_battle(self, hidden: bool) -> str:
        card = dataclasses.replace(
            F._synth(2.0, F.INF), name="本陣次郎", trait="command",
            hidden_trait="command" if hidden else "")
        weak = F.Army((card,) + tuple(F._synth(2.0, F.INF) for _ in range(5)),
                      F.FORM_STANDARD)
        strong = F.flat_army(cost=20.0, typ=F.INF)
        events = []
        F.simulate(weak, strong, dt=0.25, seed=1, events=events)
        return "\n".join(e.text for e in events)

    def test_hidden_command_omits_name_on_collapse(self):
        text = self._command_battle(True)
        line = next(l for l in text.split("\n") if "本陣、破られる" in l)
        # 名前が他の行（崩れ・壊滅などの通常実況）に出るのは構わない —
        # 消すのは「本陣、破られる」という**この特性の名指し**だけ。
        self.assertNotIn("本陣次郎", line)
        self.assertIn("本陣の将", line)

    def test_native_command_names_general_on_collapse(self):
        text = self._command_battle(False)
        line = next(l for l in text.split("\n") if "本陣、破られる" in l)
        self.assertIn("本陣次郎", line)

    # ---- _apply_treasures（sim/play.py）・陣容の保存/復元 ----

    def _treasured_army(self):
        """貂蝉に的盧（t_teki）を持たせた状態を、実際の宝物DBを通して作る。

        _apply_treasures は DB の実データを読むので、ここだけは players.py の
        使い捨てDBを本物どおりに経由する。貂蝉〔傾国〕は生まれつきの特性を
        持たない — 宝物で足した分だけを見たいので、その前提を assert で守る。
        """
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        cx = P.connect(os.path.join(d.name, "t.db"))
        self.addCleanup(cx.close)
        pid = P.register(cx, "テスト太郎").id
        self.assertTrue(P.grant_treasure(cx, pid, "t_teki"))
        general = "貂蝉〔傾国〕"
        roster = {c.name: c for c in M._roster_cards()}
        assert roster[general].trait == "", "この武将は生まれつきの特性を前提にした選択"
        P.set_treasure(cx, pid, "t_teki", general)
        base = F.Army((roster[general],), F.FORM_STANDARD)
        return PL._apply_treasures(cx, pid, base)[0], roster

    def test_apply_treasures_marks_key_hidden(self):
        army, _ = self._treasured_army()
        c = army.cards[0]
        self.assertIn("t_teki", F.trait_keys(c.trait))
        self.assertEqual(F.trait_keys(c.hidden_trait), ("t_teki",))

    def test_snapshot_round_trip_preserves_hidden_trait(self):
        # リプレイの実況は保存済みの陣容から**組み直す**ので、宝物の隠し
        # キーが陣容の往復を生き延びないと、対戦直後は隠れていても
        # リプレイを開き直すと名前が漏れる（§7.136）。
        army, roster = self._treasured_army()
        snap = PL.snap_army(army)
        self.assertEqual(snap["cards"][0].get("h"), "t_teki")
        rebuilt = PL.army_from_snap(list(roster.values()), snap)
        c = rebuilt.cards[0]
        self.assertIn("t_teki", F.trait_keys(c.trait))
        self.assertEqual(F.trait_keys(c.hidden_trait), ("t_teki",))

    def test_snapshot_of_native_card_has_no_hidden_key(self):
        roster = {c.name: c for c in M._roster_cards()}
        army = F.Army((roster["呂布〔飛将〕"],), F.FORM_STANDARD)
        snap = PL.snap_army(army)
        self.assertNotIn("h", snap["cards"][0])


if __name__ == "__main__":
    unittest.main()
