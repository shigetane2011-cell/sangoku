# -*- coding: utf-8 -*-
"""引き延ばし（§7.259）の受け入れ試験。

テストプレイの案「今、混乱・延焼・足止めしてる敵兵の適用時間1.5倍、サイクル遅い」
から作った器。**新しくは掛けない** —— いま掛かっているものの寿命だけを伸ばす。
だから「味方が先に撒く編成でだけ働く」＝編成の軸になる。

見張るのは6つ。

1. 三つとも伸びる（足止め・混乱・延焼。入れ物がそれぞれ違う）
2. **元の長さ**に掛かる（残り時間ではない）。だから**撃つ時刻で値打ちが変わらない**
3. 何も掛かっていない相手には**何も起きない**（新しく掛ける器ではない）
4. 味方の強化・回復は伸ばさない（敵に掛かった弱体だけ）
5. 器の無い組み（味方対象・倍率が範囲外）は読み込みで断る
6. 値段は倍率−1 に比例し、王平 c5 の予算に収まる
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

EF = "敵前衛"


def _sides():
    a = F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)), F.FORM_STANDARD)
    b = F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)), F.FORM_STANDARD)
    return F.build(a, 1), F.build(b, -1)


def _cast(u, eff, tgt, ua, ub, t=0.0, name="＿探"):
    sk = F._parse_skill(eff, tgt)
    F._apply_skill(u, sk, tgt, ua, ub, t, src=name, name=name, kind_jp="兵法")
    return sk


def _front(ub):
    return [x for x in ub if x.is_front][0]


def _stun_end(f):
    return next((e[0] for e in f.effects if e[1] == "stun"), None)


def _dot_end(f):
    return next((e[0] for e in f.overtime if e[1] == "dot"), None)


class Parse(unittest.TestCase):
    def test_reads_the_percent(self):
        self.assertAlmostEqual(F._parse_skill("引き延ばし 150%", EF).stretch, 1.5)

    def test_refuses_ally_targets(self):
        for tgt in ("味方前衛", "味方全体", "自分"):
            with self.assertRaises(SystemExit):
                F._parse_skill("引き延ばし 150%", tgt)

    def test_refuses_out_of_range(self):
        for pct in ("100", "90", "250"):
            with self.assertRaises(SystemExit):
                F._parse_skill("引き延ばし {}%".format(pct), EF)

    def test_plain_skills_are_untouched(self):
        self.assertEqual(F._parse_skill("行動阻害 3秒", EF).stretch, 0.0)

    def test_a_lone_stretch_is_a_readable_effect(self):
        """`rosterdata` の「1つも読めていない」の見張りを通ること（§7.259）。

        引き延ばしは**単独で持つ**器なので、見張りの一覧に足し忘れると
        王平の行が「書式ちがい」で撥ねられる（実際に当たった）。
        """
        R.load_skills_into_field()      # 例外が出なければ通っている


class Board(unittest.TestCase):
    def test_all_three_stretch(self):
        ua, ub = _sides()
        _cast(ua[0], "行動阻害 3秒", EF, ua, ub)
        _cast(ua[0], "混乱 25%（14秒）", EF, ua, ub)
        _cast(ua[0], "継続ダメージ 威力25%（12秒）", EF, ua, ub)
        f = _front(ub)
        before = (_stun_end(f), f.chaos_until, _dot_end(f))
        _cast(ua[1], "引き延ばし 150%", EF, ua, ub)
        after = (_stun_end(f), f.chaos_until, _dot_end(f))
        for b, a, secs in zip(before, after,
                              (F.skill_dur(3.0), F.skill_dur(14.0), F.skill_dur(12.0))):
            self.assertAlmostEqual(a - b, secs * 0.5, places=6)

    def test_it_uses_the_original_length_not_the_remaining(self):
        """**撃つ時刻で値打ちが変わらない。** 残りに掛ける形だと「直後に撃つほど得」
        になって、値付けが立たなくなる（STRETCH_PRICE の注記）。"""
        grew = []
        for cast_at in (0.0, 1.0, 3.0):
            ua, ub = _sides()
            _cast(ua[0], "継続ダメージ 威力25%（12秒）", EF, ua, ub)
            f = _front(ub)
            before = _dot_end(f)
            _cast(ua[1], "引き延ばし 150%", EF, ua, ub, t=cast_at)
            grew.append(round(_dot_end(f) - before, 6))
        self.assertEqual(len(set(grew)), 1, "撃つ時刻で伸びる量が変わっている: {}".format(grew))
        self.assertAlmostEqual(grew[0], F.skill_dur(12.0) * 0.5, places=6)

    def test_nothing_happens_on_a_clean_enemy(self):
        """**新しくは掛けない。** 何も掛かっていなければ何も起きない。"""
        ua, ub = _sides()
        f = _front(ub)
        _cast(ua[0], "引き延ばし 150%", EF, ua, ub)
        self.assertEqual(f.effects, [])
        self.assertEqual(f.chaos_until, 0.0)
        self.assertEqual(f.overtime, [])

    def test_ally_buffs_and_heals_are_not_stretched(self):
        ua, ub = _sides()
        _cast(ua[0], "攻撃力 +20%（30秒）", "味方前衛", ua, ub)
        a = [x for x in ua if x.is_front][0]
        keep = [e[0] for e in a.effects]
        _cast(ua[1], "引き延ばし 150%", EF, ua, ub)
        self.assertEqual([e[0] for e in a.effects], keep)

    def test_an_expired_effect_is_not_revived(self):
        ua, ub = _sides()
        _cast(ua[0], "行動阻害 3秒", EF, ua, ub)
        f = _front(ub)
        end = _stun_end(f)
        _cast(ua[1], "引き延ばし 150%", EF, ua, ub, t=end + 1.0)
        self.assertAlmostEqual(_stun_end(f), end, places=6)


class Narration(unittest.TestCase):
    def test_the_line_says_it_does_not_apply_anything_new(self):
        ua, ub = _sides()
        ev = []
        _cast(ua[0], "行動阻害 3秒", EF, ua, ub)
        sk = F._parse_skill("引き延ばし 150%", EF)
        F._apply_skill(ua[1], sk, EF, ua, ub, 0.0, src="＿延", name="＿延",
                       kind_jp="兵法", ev=ev)
        text = "".join(e.text for e in ev)
        self.assertIn("いま掛かっている", text)
        self.assertIn("150%", text)


class Price(unittest.TestCase):
    def test_proportional_to_the_multiplier_minus_one(self):
        gc, gi = D.GAUGE_TIER["大技"]
        args = dict(gauge_cost=gc, gauge_init=gi, cost=5.0, typ=F.INF, tilt="中庸")
        v15 = D.effect_value(F._parse_skill("引き延ばし 150%", EF), EF, **args)
        v20 = D.effect_value(F._parse_skill("引き延ばし 200%", EF), EF, **args)
        self.assertAlmostEqual(v20 / v15, 2.0, places=3)

    def test_it_fits_wang_ping(self):
        gc, gi = D.GAUGE_TIER["大技"]
        # 【§7.264】段の重みを 0.479 → 0.350 へ下げ、決戦型の効果文をそのぶん
        # 太らせた（身体は不変）。王平の引き延ばしも 150% → 168% になっている。
        # **請求は前と同じ 0.49** —— 値打ちの置き方（弱体1本ぶん）は変えていない。
        v = D.effect_value(F._parse_skill("引き延ばし 168%", EF), EF,
                           gauge_cost=gc, gauge_init=gi, cost=5.0, typ=F.INF,
                           tilt="中庸")
        self.assertLess(v, D.EFFECT_CAP * 5.0)
        # 名簿の弱体25本の「0.5×元の長さ」ぶんの中央 0.496 の**1本ぶん**で置いた。
        # 2本ぶんで作って測ったら、組んだ編成でも −2.50% とただ弱くなった
        # （STRETCH_PRICE の表）。大技は1戦1回・遅いので、その時点で生きている
        # 弱体は平均1本、というのが実測の言っていること。
        self.assertAlmostEqual(v, 0.493, places=2)


class WangPing(unittest.TestCase):
    """王平〔無当〕が実際にその器で載っていること。"""

    def test_the_card_carries_it(self):
        sk = F.SKILL_INFO["無当飛軍"]
        self.assertAlmostEqual(sk.stretch, 1.68)   # §7.264 で 1.5 → 1.68
        self.assertEqual(F.SKILL_TARGET["無当飛軍"], EF)

    def test_the_stun_overlap_is_gone(self):
        """5コスト歩兵の行動阻害3枚重複のうち、王平が抜けたこと（§7.259）。"""
        g = [dict(r) for r in R.generals()]
        s = {r["兵法名"]: r for r in R.skills()}
        stun5 = [r["名前"] for r in g if r["コスト"] == "5" and r["兵種"] == "歩兵"
                 and "行動阻害" in s.get(r["兵法"], {}).get("効果", "")]
        self.assertNotIn("王平〔無当〕", stun5)
        self.assertLessEqual(len(stun5), 2, "5コスト歩兵の足止めがまだ3枚ある")

    def test_the_display_says_it_applies_nothing_new(self):
        from sim import web as W
        g = {x["名前"]: x for x in R.generals()}["王平〔無当〕"]
        row = {x["兵法名"]: x for x in R.skills()}["無当飛軍"]
        txt = W._skill_display(g, row)
        self.assertIn("引き延ばし", txt)
        self.assertIn("新しくは掛けない", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
