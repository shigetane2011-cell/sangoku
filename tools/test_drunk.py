# -*- coding: utf-8 -*-
"""酒乱（§7.146）の受け入れ試験。持ち手は開幕から常に DRUNK_CHAOS の混乱を抱え、計略の混乱が失効しても床へ戻る。"""
import os, sys, unittest
from dataclasses import replace
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sim import rosterdata as R, field as F, match as M

F.TRAITS.clear(); R.load_traits_into_field(); R.load_skills_into_field(); F.TRAITS_ON = True
CARDS = {c.name: c for c in M._roster_cards()}


def lubu_army(trait):
    card = replace(CARDS["呂布〔飛将〕"], trait=trait)
    a = F.Army((card, CARDS["曹仁〔堅守〕"], CARDS["郝昭〔陳倉〕"],
                CARDS["李典〔慎重〕"], CARDS["貂蝉〔傾国〕"], CARDS["満寵〔剛毅〕"]), F.FORM_STANDARD)
    assert not M.placement_errors(a), M.placement_errors(a)
    return a


def lubu_unit(trait):
    return F.build(lubu_army(trait), 1)[0]


class DrunkTest(unittest.TestCase):
    def test_roster_has_no_drunk_holders(self):
        """素の版は酒乱を持たない（杜康の酒を持ったときだけの隠し特性・§7.146）。"""
        self.assertEqual([c.name for c in CARDS.values() if "drunk" in c.trait], [])

    def test_treasure_gives_drunk_variant(self):
        from sim import play as PL
        base = CARDS["呂布〔飛将〕"]
        c = replace(base, trait=F.TRAIT_SEP.join([base.trait, "t_toko"]), hidden_trait="t_toko")
        v = PL.apply_treasure_card_mods(c)
        self.assertIn("drunk", F.trait_keys(v.trait)); self.assertIn("drunk", F.trait_keys(v.hidden_trait))
        self.assertIn("t_toko", F.trait_keys(v.trait)); self.assertIn("laststand", F.trait_keys(v.trait))
        self.assertGreater(v.stat_cost, base.stat_cost + 0.5, "酒乱の値段（負）が能力値へ返る")
        self.assertGreater(v.might, base.might); self.assertGreater(v.wits, base.wits)
        self.assertEqual(PL.apply_treasure_card_mods(v), v, "冪等")
        z = replace(CARDS["張飛〔当陽橋〕"], trait="vanguard、t_toko", hidden_trait="t_toko")
        vz = PL.apply_treasure_card_mods(z)
        self.assertIn("drunk", F.trait_keys(vz.trait)); self.assertGreater(vz.stat_cost, CARDS["張飛〔当陽橋〕"].stat_cost + 0.5)
        other = replace(CARDS["曹仁〔堅守〕"], trait="t_toko", hidden_trait="t_toko")
        vo = PL.apply_treasure_card_mods(other)
        self.assertNotIn("drunk", F.trait_keys(vo.trait)); self.assertEqual(vo.stat_cost, other.stat_cost)

    def test_variant_unit_is_drunk_and_replay_round_trip(self):
        from sim import play as PL
        base = CARDS["呂布〔飛将〕"]
        c = PL.apply_treasure_card_mods(replace(base, trait=base.trait + "、t_toko", hidden_trait="t_toko"))
        a = F.Army((c, CARDS["曹仁〔堅守〕"], CARDS["郝昭〔陳倉〕"],
                    CARDS["李典〔慎重〕"], CARDS["貂蝉〔傾国〕"], CARDS["満寵〔剛毅〕"]), F.FORM_STANDARD)
        u = F.build(a, 1)[0]
        self.assertAlmostEqual(u.chaos_floor, F.DRUNK_CHAOS, places=9)
        back = PL.army_from_snap(list(CARDS.values()), PL.snap_army(a))
        b = back.cards[0]
        self.assertEqual((b.stat_cost, b.might, b.wits, F.trait_keys(b.trait), F.trait_keys(b.hidden_trait)),
                         (c.stat_cost, c.might, c.wits, F.trait_keys(c.trait), F.trait_keys(c.hidden_trait)))

    def test_floor_from_start(self):
        on, off = lubu_unit("laststand、drunk"), lubu_unit("laststand")
        self.assertAlmostEqual(on.chaos, F.DRUNK_CHAOS, places=9)
        self.assertAlmostEqual(on.chaos_floor, F.DRUNK_CHAOS, places=9)
        self.assertEqual((off.chaos, off.chaos_floor), (0.0, 0.0))

    def test_stronger_chaos_stacks_then_returns_to_floor(self):
        u = lubu_unit("laststand、drunk")
        F._chaos_add(u, 0.25, 10.0)          # 計略の混乱は床の上に max で乗る
        self.assertAlmostEqual(u.chaos, 0.25, places=9)
        # 失効の判定（simulate と同じ式）で床へ戻る
        t, dt = 10.0, 0.25
        if u.chaos > u.chaos_floor and t + dt >= u.chaos_until:
            u.chaos = u.chaos_floor
        self.assertAlmostEqual(u.chaos, F.DRUNK_CHAOS, places=9)
        F._chaos_add(u, 0.10, 20.0)          # 床より弱い混乱は何も変えない
        self.assertAlmostEqual(u.chaos, F.DRUNK_CHAOS, places=9)

    def test_floor_survives_a_battle(self):
        """1戦を通して呂布の混乱が床を割らない（失効処理が 0 へ落とさない）。"""
        a = lubu_army("laststand、drunk")
        foe = lubu_army("laststand")
        seen = []
        orig = F.chaos_ff
        def spy(u):
            if "呂布" in u.name and u.side == 1 and u.men > 0.0:
                seen.append(u.chaos)
            return orig(u)
        F.chaos_ff = spy
        try:
            F.simulate(a, foe, dt=0.25, seed=7)
        finally:
            F.chaos_ff = orig
        self.assertTrue(seen, "同士討ちの判定が呂布に一度も来ない")
        self.assertGreaterEqual(min(seen), F.DRUNK_CHAOS - 1e-9)

    def test_traits_off_gate(self):
        F.TRAITS_ON = False
        try:
            self.assertEqual(lubu_unit("laststand、drunk").chaos, 0.0)
        finally:
            F.TRAITS_ON = True


if __name__ == "__main__":
    unittest.main(verbosity=1)
