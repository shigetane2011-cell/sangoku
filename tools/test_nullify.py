# -*- coding: utf-8 -*-
"""兵法打消しの「回数（＋窓）」の受け入れ試験（§7.152）。

窓の秒数だけだった構えに「その一度で N発まで」を足した。回数は**消費で減る**
ので、毎ティック効果の山から組み直す _recalc_mods を生き延びなければならない
（宝物の恒久項と同じ落とし穴）。残りは対象の隊で**分け合う**（味方前衛に
張れば前衛あわせて N発）。ここはその境目だけを突く。
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import rosterdata as R        # noqa: E402

if not F.SKILL_INFO:
    R.load_skills_into_field()

STANCE = "＿試験の構え"
SHOT = "＿試験砲"


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _filler(n, typ=F.INF):
    return [F._synth(4.0, typ) for _ in range(n)]


def _stance(effect, t=0.0, target="自分"):
    """効果文どおりの構えを張って (味方, 敵, 兵法, 撃たれる隊)。

    構えは実際の発動の口（_apply_skill）を通す — 残りの入れ物はそこで作られる
    ので、手で effects へ積むと「分け合う」ところが試験から抜ける。
    """
    ua = F.build(_army(_filler(6)), 1)
    ub = F.build(_army(_filler(6)), -1)
    tgt = F._skill_targets("敵1体（正面）", ub[0], ua, ub)[0]
    sk = F._parse_skill(effect, target)
    caster = ua[0] if target == "自分" else tgt
    F._apply_skill(caster if target != "自分" else tgt, sk, target, ua, ub, t,
                   src=STANCE, name=STANCE, kind_jp="兵法")
    for u in ua:
        F._recalc_mods(u)
    return ua, ub, sk, tgt


def _shoot(ua, ub, tgt, t=0.0, ev=None):
    """敵の隊 ub[0] が構えた隊へ兵法を撃つ。撃ち込めたら True。"""
    before = tgt.men
    F._apply_skill(ub[0], F.Skill(power=3.0), "敵1体（正面）", ub, ua, t,
                   src=SHOT, name=SHOT, kind_jp="兵法", ev=ev)
    return tgt.men < before - 1e-9


class Grammar(unittest.TestCase):
    def test_old_form_is_unlimited(self):
        self.assertEqual(F._skill_mods("兵法打消し（60秒）"),
                         (("null", math.inf, F.skill_dur(60.0)),))

    def test_count_form(self):
        for text in ("兵法打消し 2発（60秒）", "兵法打消し2発（60秒）"):
            self.assertEqual(F._skill_mods(text),
                             (("null", 2.0, F.skill_dur(60.0)),))


class Charges(unittest.TestCase):
    def test_two_shots_then_through(self):
        ua, ub, _, tgt = _stance("兵法打消し 2発（60秒）")
        self.assertTrue(tgt.nullify)
        self.assertFalse(_shoot(ua, ub, tgt))      # 1発目 霧散
        self.assertFalse(_shoot(ua, ub, tgt))      # 2発目 霧散
        self.assertTrue(_shoot(ua, ub, tgt))       # 3発目は通る
        self.assertEqual(tgt.null_blocked, 2)
        self.assertFalse(tgt.nullify)

    def test_recalc_does_not_restore(self):
        """毎ティックの組み直しで残り回数が戻らない（恒久項と同じ落とし穴）。"""
        ua, ub, _, tgt = _stance("兵法打消し 2発（60秒）")
        self.assertFalse(_shoot(ua, ub, tgt))
        F._recalc_mods(tgt)
        self.assertAlmostEqual(tgt.null_pool[0], 1.0)
        self.assertFalse(_shoot(ua, ub, tgt))
        F._recalc_mods(tgt)
        self.assertTrue(_shoot(ua, ub, tgt))

    def test_unlimited_blocks_many(self):
        ua, ub, _, tgt = _stance("兵法打消し（60秒）")
        for _ in range(5):
            self.assertFalse(_shoot(ua, ub, tgt))
        self.assertEqual(tgt.null_blocked, 5)

    def test_recast_refreshes(self):
        """構えを張り直したら残り回数も戻る（入れ物を作り直すので自然にそうなる）。"""
        ua, ub, sk, tgt = _stance("兵法打消し 2発（60秒）")
        self.assertFalse(_shoot(ua, ub, tgt))
        self.assertFalse(_shoot(ua, ub, tgt))
        self.assertFalse(tgt.nullify)
        F._apply_skill(tgt, sk, "自分", ua, ub, 10.0, src=STANCE, name=STANCE,
                       kind_jp="兵法")
        F._recalc_mods(tgt)
        self.assertTrue(tgt.nullify)
        self.assertFalse(_shoot(ua, ub, tgt, 10.0))

    def test_window_end_clears(self):
        ua, ub, _, tgt = _stance("兵法打消し 2発（30秒）")
        self.assertFalse(_shoot(ua, ub, tgt))
        F._expire(ua + ub, F.skill_dur(30.0) + 1.0)
        self.assertFalse(tgt.nullify)
        self.assertAlmostEqual(tgt.null_cap, 0.0)
        self.assertIsNone(tgt.null_pool)
        self.assertTrue(_shoot(ua, ub, tgt))

    def test_pool_is_shared_across_targets(self):
        """味方前衛に張った 2発 は「前衛あわせて2発」（隊ごとに2発ではない）。"""
        ua, ub, _, _ = _stance("兵法打消し 2発（60秒）", target="味方前衛")
        front = [u for u in ua if u.is_front]
        self.assertGreater(len(front), 1)
        self.assertTrue(all(u.nullify for u in front))
        self.assertTrue(all(u.null_pool is front[0].null_pool for u in front))

        def volley():
            F._apply_skill(ub[0], F.Skill(power=3.0), "敵前衛", ub, ua, 0.0,
                           src=SHOT, name=SHOT, kind_jp="兵法")
            return sum(u.men for u in front)

        before = sum(u.men for u in front)
        self.assertAlmostEqual(volley(), before)      # 1発目 霧散
        self.assertAlmostEqual(volley(), before)      # 2発目 霧散
        self.assertAlmostEqual(front[0].null_pool[0], 0.0)
        self.assertLess(volley(), before)             # 3発目は前衛ごと通る
        self.assertEqual(sum(u.null_blocked for u in front), 2)


class Words(unittest.TestCase):
    def test_pool_survives_phase_recalc(self):
        """兵法のフェーズ（蓄積器）を通しても入れ物が残り、回数が効く（§7.165）。
        以前は蓄積器の中で _recalc_mods が走ると入れ物が None に戻り、実戦では
        「何発でも」になっていた（単体テストは蓄積器を通らないので見えなかった）。"""
        ua = F.build(_army(_filler(6)), 1)
        ub = F.build(_army(_filler(6)), -1)
        tgt = F._skill_targets("敵1体（正面）", ub[0], ua, ub)[0]
        sk = F._parse_skill("兵法打消し 1発（60秒）", "自分")
        F._open_men_window()
        F._apply_skill(tgt, sk, "自分", ua, ub, 0.0, src=STANCE, name=STANCE, kind_jp="兵法")
        F._recalc_mods(tgt)          # フェーズ中の組み直し（ここで入れ物が消えていた）
        F._flush_men()
        F._recalc_mods(tgt)
        self.assertIsNotNone(tgt.null_pool)
        self.assertAlmostEqual(tgt.null_pool[0], 1.0)
        self.assertFalse(_shoot(ua, ub, tgt))      # 1発目 霧散
        self.assertTrue(_shoot(ua, ub, tgt))       # 2発目は通る（回数が効いている）
        self.assertEqual(tgt.null_blocked, 1)
        self.assertFalse(tgt.nullify)

    def test_line_says_count(self):
        ua = F.build(_army(_filler(6)), 1)
        def line(amount):
            return F._skill_line(ua[0], "＿試験", "味方前衛", ua[:2], "buff",
                                 amount, 90.0, stat="null")
        self.assertIn("あわせて2発まで", line(2.0))
        self.assertNotIn("発まで", line(math.inf))

    def test_blocked_line_counts_down(self):
        ua, ub, _, tgt = _stance("兵法打消し 2発（60秒）")
        ev = []
        for _ in range(2):
            _shoot(ua, ub, tgt, ev=ev)
        self.assertIn("あと1発", ev[0].text)
        self.assertIn("構えは尽きた", ev[1].text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
