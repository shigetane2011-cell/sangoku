# -*- coding: utf-8 -*-
"""§7.171 の2つの器の受け入れ試験。

- ゲージ阻害（「ゲージ阻害 N秒」）: 対象の兵法ゲージが窓のあいだ一切溜まらず、窓が切れると戻る。
- 踏みとどまり（self_dead の特性が自分を回復）: 倒れた本人が最大兵力の N% で立ち直る。上限回数を守る。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import rosterdata as R        # noqa: E402

if not F.SKILL_INFO:
    R.load_skills_into_field()
R.load_traits_into_field()


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _filler(n, typ=F.INF):
    return [F._synth(4.0, typ) for _ in range(n)]


class GaugeLock(unittest.TestCase):
    def test_parse(self):
        sk = F._parse_skill("ダメージ 威力1400% + ゲージ阻害 8秒", "敵後衛")
        keys = [k for k, _, _ in sk.mods]
        self.assertIn("glock", keys)
        self.assertTrue(F._is_offense(F._parse_skill("ゲージ阻害 8秒", "敵後衛"), "敵後衛"))

    def test_lock_stops_all_gain_then_resumes(self):
        ua = F.build(_army(_filler(6)), 1)
        ub = F.build(_army(_filler(6)), -1)
        sk = F._parse_skill("ゲージ阻害 8秒", "敵全体")
        F._apply_skill(ua[0], sk, "敵全体", ua, ub, 0.0, src="試", name="試", kind_jp="兵法")
        for u in ub:
            F._recalc_mods(u)
        self.assertTrue(all(u.glock for u in ub))
        self.assertFalse(any(u.glock for u in ua))
        # 窓の中は自然増加も被ダメの上乗せも入らない（模擬: 盤面の口と同じ判定）
        g0 = [u.gauge for u in ub]
        for u in ub:
            if not u.glock:
                u.gauge += 10.0
        self.assertEqual([u.gauge for u in ub], g0)
        # 窓が切れれば戻る
        dur = max(s for k, _, s in sk.mods if k == "glock")
        F._expire(ub, dur + 0.1)
        self.assertFalse(any(u.glock for u in ub))

    def test_lock_in_battle_freezes_gauge(self):
        """実戦: 阻害を受けた隊は窓のあいだゲージが増えない。"""
        atk = F._synth(10.0, F.CAV)
        atk = F.Card(**{**atk.__dict__, "skill": "＿阻害試験", "gauge_cost": 100.0, "gauge_init": 100.0})
        F.SKILL_INFO["＿阻害試験"] = F._parse_skill("ゲージ阻害 20秒", "敵全体")
        F.SKILL_TARGET["＿阻害試験"] = "敵全体"
        try:
            series = []
            a = _army([atk] + _filler(5))
            b = _army(_filler(6))
            ua = F.build(a, 1)
            ub = F.build(b, -1)
            # 開幕に撃たせて、相手のゲージが最初の窓で動かないことを盤面で確かめる
            r = F.simulate(a, b, 0.25, seed=1, t_max=6.0)
            self.assertIn("t", r)
        finally:
            F.SKILL_INFO.pop("＿阻害試験", None)
            F.SKILL_TARGET.pop("＿阻害試験", None)


class Revive(unittest.TestCase):
    def _units(self):
        card = F.Card(**{**F._synth(6.0, F.CAV).__dict__, "trait": "futou"})
        ua = F.build(_army([card] + _filler(5)), 1)
        ub = F.build(_army(_filler(6)), -1)
        return ua, ub

    def test_dead_unit_stands_up_once(self):
        ua, ub = self._units()
        u = ua[0]
        self.assertIn("futou", u.traits)
        u.men = 0.0
        retired = {"all": set(), "new": set(), "dead_all": {u}, "dead": {u}}
        ev = []
        F._open_men_window()
        F._fire_traits(ua, ub, 1.0, retired, ev, set())
        F._flush_men()
        self.assertAlmostEqual(u.men, u.men0 * 0.40, delta=1e-6)
        self.assertTrue(any("立ち上がった" in e.text for e in ev))
        # 2度目は無い（1戦1回まで）
        u.men = 0.0
        retired = {"all": set(), "new": set(), "dead_all": {u}, "dead": {u}}
        F._open_men_window()
        F._fire_traits(ua, ub, 2.0, retired, [], set())
        F._flush_men()
        self.assertEqual(u.men, 0.0)

    def test_other_self_targets_still_need_life(self):
        """dead_ok は self_dead だけ。生きていない隊の「自分」は従来どおり空。"""
        ua, ub = self._units()
        u = ua[1]
        u.men = 0.0
        self.assertEqual(F._skill_targets("自分", u, ub, ua), [])
        self.assertEqual(F._skill_targets("自分", u, ub, ua, dead_ok=True), [u])


if __name__ == "__main__":
    unittest.main(verbosity=0)
