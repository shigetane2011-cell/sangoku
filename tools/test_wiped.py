# -*- coding: utf-8 -*-
"""壊滅した隊は戦場から降りる（§7.239）の受け入れ試験。

テストプレイの報告（戦闘ログ）:

    09:02 自軍 霍峻〔葭萌〕の隊、ついに壊滅（9,450人を失う）。
    09:21 敵軍 黄月英〔木牛流馬〕の【元戎連弩】！ だが霍峻〔葭萌〕が
          田豊〔剛直〕の【剛直の諫言】で兵法全体を打ち消した。

19分前に壊滅を告げた隊が敵の兵法を打ち消していた。原因は閾値の二重帳簿で、
実況は残存 ANNIHIL_UNIT(0.5%) 割れを「壊滅」と告げるのに、盤面は `men > 0`
なら生きている隊として扱っていた。**打消しの構えは量を持たない入り切り**
なので、残り30人の隊が兵法1発を丸ごと消せる（実測で打消しの 8/10 が
壊滅告知済みの隊だった）。

不変条件をひとつに絞ったのが直し方である —— **壊滅を告げた隊は兵力 0**。
`wiped_at` が付いた隊の兵力は必ず 0 なので、既にある `men > 0` の門番が
そのまま全部効く。ここはその不変条件と、抜けていた「敵全体」を突く。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import rosterdata as R        # noqa: E402

if not F.SKILL_INFO:
    R.load_skills_into_field()


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _sides(n=6):
    ua = F.build(_army([F._synth(4.0, F.INF) for _ in range(n)]), 1)
    ub = F.build(_army([F._synth(4.0, F.INF) for _ in range(n)]), -1)
    return ua, ub


def _lopsided():
    """必ず何隊か壊滅する噛み合わせ（1点の歩兵 × 10点の騎兵）。"""
    return (_army([F._synth(1.0, F.INF) for _ in range(6)]),
            _army([F._synth(10.0, F.CAV) for _ in range(6)]))


class EnemyAll(unittest.TestCase):
    """「敵全体」だけ生存で絞っていなかった（「味方全体」は最初から絞っている）。"""

    def test_enemy_all_skips_the_fallen(self):
        ua, ub = _sides()
        ua[0].men = 0.0
        ua[1].men = 0.0
        tg = F._skill_targets("敵全体", ub[0], ua, ub)
        self.assertEqual(len(tg), 4)
        self.assertNotIn(ua[0], tg)
        self.assertNotIn(ua[1], tg)

    def test_enemy_all_damage_is_not_diluted(self):
        """頭数で割るので、死体が混じると生きている敵のぶんが薄まっていた。"""
        ua, ub = _sides()
        sk = F.Skill(power=3.0)
        F._apply_skill(ub[0], sk, "敵全体", ub, ua, 0.0,
                       src="＿砲", name="＿砲", kind_jp="兵法")
        full = ua[1].men0 - ua[1].men
        ua2, ub2 = _sides()
        ua2[0].men = 0.0
        ua2[2].men = 0.0
        F._apply_skill(ub2[0], sk, "敵全体", ub2, ua2, 0.0,
                       src="＿砲", name="＿砲", kind_jp="兵法")
        half = ua2[1].men0 - ua2[1].men
        # 6枚中2枚が倒れていれば、残り4枚は 6/4 倍ぶん深く入る
        self.assertAlmostEqual(half / full, 6.0 / 4.0, places=3)

    def test_all_fallen_yields_no_target(self):
        ua, ub = _sides()
        for u in ua:
            u.men = 0.0
        self.assertEqual(F._skill_targets("敵全体", ub[0], ua, ub), [])


class Nullify(unittest.TestCase):
    def test_fallen_unit_cannot_nullify(self):
        """倒れた隊の構えでは敵の兵法は消えない（報告そのもの）。"""
        ua, ub = _sides()
        F._apply_skill(ua[0], F._parse_skill("兵法打消し 2発（60秒）", "味方全体"),
                       "味方全体", ua, ub, 0.0,
                       src="＿構え", name="＿構え", kind_jp="兵法")
        for u in ua:
            F._recalc_mods(u)
        self.assertTrue(ua[0].nullify)
        # 構えを張った隊だけを倒し、残りの構えは落とす
        ua[0].men = 0.0
        for u in ua[1:]:
            u.effects = []
            u.null_pool = None
            F._recalc_mods(u)
        before = [u.men for u in ua]
        F._apply_skill(ub[0], F.Skill(power=3.0), "敵全体", ub, ua, 1.0,
                       src="＿砲", name="＿砲", kind_jp="兵法")
        after = [u.men for u in ua]
        self.assertTrue(any(a < b - 1e-9 for a, b in zip(after[1:], before[1:])),
                        "倒れた隊の構えで兵法が消えている")

    def test_standing_unit_still_nullifies(self):
        """立っている隊の構えは今までどおり効く（消したのは壊滅した隊だけ）。"""
        ua, ub = _sides()
        F._apply_skill(ua[0], F._parse_skill("兵法打消し 2発（60秒）", "味方全体"),
                       "味方全体", ua, ub, 0.0,
                       src="＿構え", name="＿構え", kind_jp="兵法")
        for u in ua:
            F._recalc_mods(u)
        before = [u.men for u in ua]
        F._apply_skill(ub[0], F.Skill(power=3.0), "敵全体", ub, ua, 1.0,
                       src="＿砲", name="＿砲", kind_jp="兵法")
        self.assertEqual(before, [u.men for u in ua])


class Retire(unittest.TestCase):
    """実戦を通した不変条件。**壊滅を告げた隊（wiped_at 付き）は兵力 0**。"""

    def test_simulate_zeroes_the_wiped(self):
        ua, ub = _lopsided()
        ev = []
        F.simulate(ua, ub, 0.5, events=ev)
        self.assertTrue([e for e in ev if e.kind == "壊滅"],
                        "壊滅の行が出ない噛み合わせでは試験にならない")

    def test_the_wiped_never_take_part_in_skills(self):
        """兵法の対象に壊滅済みの隊が現れないことを、実戦の中で見張る。"""
        seen = {"wiped": 0, "calls": 0}
        bad = []
        orig = F._skill_targets

        def watch(target, u, foe, own, dead_ok=False):
            tg = orig(target, u, foe, own, dead_ok)
            seen["calls"] += 1
            for x in tg:
                if x.wiped_at is not None:
                    seen["wiped"] += 1
                    if x.men > 0.0:
                        bad.append((target, F._who(x), x.men))
            return tg

        F._skill_targets = watch
        try:
            for s in range(6):
                ua, ub = _lopsided()
                ev = []
                F.simulate(ua, ub, 0.5, seed=100 + s, events=ev)
        finally:
            F._skill_targets = orig
        self.assertTrue(seen["calls"] > 0, "兵法が一度も撃たれていない")
        self.assertEqual(bad, [], "壊滅した隊が兵法に加わっている")

    def test_wiped_at_implies_zero_men(self):
        """不変条件そのもの。戦い終わりに `wiped_at` 付きの隊は全部 0 人。

        dealt_a/b の行は「末尾は covered・その前が wiped_at」で固定
        （simulate の注記）。3 は残兵。
        """
        found = 0
        for s in range(6):
            ua, ub = _lopsided()
            r = F.simulate(ua, ub, 0.5, seed=200 + s)
            for row in r["dealt_a"] + r["dealt_b"]:
                if row[-2] is not None:
                    found += 1
                    self.assertEqual(row[3], 0.0, row[0])
        self.assertTrue(found, "壊滅した隊が1つも出ていない")


if __name__ == "__main__":
    unittest.main(verbosity=2)
