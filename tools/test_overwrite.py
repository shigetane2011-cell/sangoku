# -*- coding: utf-8 -*-
"""上書き（§7.263）の受け入れ試験。

テストプレイの提案「文聘〔江夏〕→ 相手バフ解除」への答え。**解除にはしなかった**
——「持っていなければゼロ・持っていれば大当たり」で分散が大きすぎ、§7.53 の帯の
真ん中に置けない（孫尚香の一撃で同じ所を踏んでいる・§7.168）。代わりに
**上乗せ**にした: 素の弱体は必ず入り、相手の攻めが上がっているぶんだけ深く沈む。

見張るのは6つ。

1. **素の節と組でしか書けない。** 単独の「上書き -10%」は読み込みで断る
   （下限がゼロの器＝値札が置けない器を作らないための門番）
2. 効くのは**攻撃倍率が 1.0 を超えている相手だけ**。上がっていない隊は素のまま
3. **誰が上げたかを問わない**（兵法でも固有特性でも宝物でも同じに沈む）。
   「剥がす」のではなく「深く沈める」なので、掛け手を覚えておく必要がない
4. **上書きだけが残ることはない** —— 素の節と同じ口・同じ秒数へ足し込む
5. 値段は**素の攻撃力弱体の単価 × 当たる割合**（`OVERWRITE_RATE` ＝ 実測の中央値
   1/3）。素の節と**同じ層**に置いてあるので段の重みが両方へ等しく掛かる
   （§7.259 で層を取り違えて請求が 1/5 になった所）
6. 文聘が実際に持ち、**持ち手は1枚だけ**（1枚だけが持つ器＝そのまま個性）
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import design as D            # noqa: E402
from sim import rosterdata as R        # noqa: E402

R.load_skills_into_field()
R.load_traits_into_field()

# 器そのものを試す合成の札（対象は前衛 —— 前だけ狙えることを見るのに都合が良い）
EFF = "攻撃力 -15%（40秒） + 上書き -10%"
TGT = "敵前衛"
# 実際の文聘。**足止めは捨てなかった**（§7.263 ③ —— 捨てると盤面で 5.4% 落ちた）
CARD_TGT = "敵後衛"
CARD_EFF = "行動阻害 2秒 + 攻撃力 -15%（40秒） + 上書き -10%"


def _sides():
    a = F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)), F.FORM_STANDARD)
    b = F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)), F.FORM_STANDARD)
    return F.build(a, 1), F.build(b, -1)


def _fire(caster, sk, own, foe, t=0.0):
    F._apply_skill(caster, sk, TGT, own, foe, t,
                   src="＿江夏", name="＿江夏", kind_jp="兵法")
    for u in own + foe:
        F._recalc_mods(u)


class ItCannotStandAlone(unittest.TestCase):
    """素の節が無い「上書き」は読み込みで死ぬ（§7.263 ①）。"""

    def test_without_a_plain_debuff_it_is_refused(self):
        with self.assertRaises(SystemExit):
            F._parse_skill("上書き -10%", TGT)

    def test_only_on_enemies(self):
        with self.assertRaises(SystemExit):
            F._parse_skill(EFF, "味方前衛")

    def test_the_pair_reads(self):
        sk = F._parse_skill(EFF, TGT)
        self.assertAlmostEqual(sk.overwrite, F.skill_mag(0.10), places=6)
        self.assertEqual(sk.mods, (("atk", -F.skill_mag(0.15), F.skill_dur(40.0)),))


class OnlyTheAlreadyBuffed(unittest.TestCase):
    def test_a_plain_enemy_takes_only_the_base(self):
        ua, ub = _sides()
        _fire(ua[0], F._parse_skill(EFF, TGT), ua, ub)
        for f in [u for u in ub if u.is_front]:
            self.assertAlmostEqual(f.atk_mult, 1.0 - F.skill_mag(0.15), places=6,
                                   msg="上がっていない相手にまで深く入っている")

    def test_a_buffed_enemy_sinks_deeper(self):
        ua, ub = _sides()
        buff = F._parse_skill("攻撃力 +20%（60秒）", "味方前衛")
        F._apply_skill(ub[0], buff, "味方前衛", ub, ua, 0.0,
                       src="＿鼓舞", name="＿鼓舞", kind_jp="兵法")
        for u in ub:
            F._recalc_mods(u)
        _fire(ua[0], F._parse_skill(EFF, TGT), ua, ub)
        want = 1.0 + F.skill_mag(0.20) - F.skill_mag(0.15) - F.skill_mag(0.10)
        for f in [u for u in ub if u.is_front]:
            self.assertAlmostEqual(f.atk_mult, want, places=6)

    def test_the_bar_is_the_multiplier_not_the_caster(self):
        """**誰が上げたかを問わない。** 倍率そのものを見る（§7.263 ③）。"""
        ua, ub = _sides()
        front = [u for u in ub if u.is_front]
        front[0].perm_atk = 0.20          # 宝物・常在の強化と同じ口
        for u in ub:
            F._recalc_mods(u)
        _fire(ua[0], F._parse_skill(EFF, TGT), ua, ub)
        self.assertAlmostEqual(
            front[0].atk_mult,
            1.0 + 0.20 - F.skill_mag(0.15) - F.skill_mag(0.10), places=6)
        self.assertAlmostEqual(front[1].atk_mult, 1.0 - F.skill_mag(0.15),
                               places=6, msg="隣まで深く入っている")

    def test_it_expires_with_the_base(self):
        """**上書きだけが残らない**（同じ口・同じ秒数・§7.263 ④）。"""
        ua, ub = _sides()
        buff = F._parse_skill("攻撃力 +20%（600秒）", "味方前衛")
        F._apply_skill(ub[0], buff, "味方前衛", ub, ua, 0.0,
                       src="＿鼓舞", name="＿鼓舞", kind_jp="兵法")
        for u in ub:
            F._recalc_mods(u)
        _fire(ua[0], F._parse_skill(EFF, TGT), ua, ub)
        late = F.skill_dur(40.0) + 1.0
        for u in ub:
            u.effects = [e for e in u.effects if e[0] > late]
            F._recalc_mods(u)
        for f in [u for u in ub if u.is_front]:
            self.assertAlmostEqual(f.atk_mult, 1.0 + F.skill_mag(0.20), places=6,
                                   msg="弱体が切れた後も沈んだまま")


class TheCommentarySaysIt(unittest.TestCase):
    """**盤面で効いているのに実況が黙る成分を作らない**（§7.98 の続き）。"""

    def test_the_extra_bite_is_narrated(self):
        ua, _ub = _sides()
        txt = F._skill_extra(("overwrite", -0.10, 60.0, 0.6, "atk", ua[:2]))
        self.assertIn("追い討ち", txt)
        self.assertNotIn("解除", txt)

    def test_it_is_not_silent(self):
        """器を足して実況の枝を忘れると、**盤面では効くのに何も言わない**
        成分ができる（落とし穴79 の仲間）。空文字を返していないことを見張る。"""
        ua, _ub = _sides()
        self.assertTrue(F._skill_extra(("overwrite", -0.10, 60.0, 0.6, "atk", ua[:2])))


class Price(unittest.TestCase):
    def test_it_is_the_measured_share_of_the_plain_debuff(self):
        """素の単価 × 当たる割合ちょうど（§7.263 ⑤）。"""
        plain = D.effect_value(F._parse_skill("攻撃力 -15%（40秒）", TGT),
                               TGT, 150.0, 0.0, 1.0, 5.0, F.INF, "中庸")
        pair = D.effect_value(F._parse_skill(EFF, TGT),
                              TGT, 150.0, 0.0, 1.0, 5.0, F.INF, "中庸")
        self.assertAlmostEqual((pair - plain) / plain,
                               (10.0 / 15.0) * D.OVERWRITE_RATE, places=6)

    def test_the_share_is_the_measured_median(self):
        self.assertAlmostEqual(D.OVERWRITE_RATE, 1.0 / 3.0, places=6)

    def test_the_tier_weight_hits_both_halves_alike(self):
        """段の層の取り違え（§7.259 で踏んだ）が起きていないこと。"""
        args = (TGT, 300.0, 0.0, 1.0, 5.0, F.INF, "中庸")   # 決戦型
        plain = D.effect_value(F._parse_skill("攻撃力 -15%（40秒）", TGT), *args)
        pair = D.effect_value(F._parse_skill(EFF, TGT), *args)
        self.assertAlmostEqual((pair - plain) / plain,
                               (10.0 / 15.0) * D.OVERWRITE_RATE, places=6)


class WenPing(unittest.TestCase):
    def test_the_card_carries_it(self):
        row = {r["兵法名"]: r for r in R.skills()}["江夏の水軍"]
        self.assertEqual(row["対象"], CARD_TGT)
        self.assertEqual(row["効果"], CARD_EFF)

    def test_it_is_the_only_holder(self):
        holders = [r["兵法名"] for r in R.skills() if "上書き" in (r["効果"] or "")]
        self.assertEqual(holders, ["江夏の水軍"])

    def test_the_screen_says_it_is_not_a_dispel(self):
        from sim import web as W
        row = {r["兵法名"]: r for r in R.skills()}["江夏の水軍"]
        g = {r["名前"]: r for r in R.generals()}["文聘〔江夏〕"]
        txt = W._skill_display(g, row)
        self.assertIn("上書き", txt)
        self.assertIn("合わせて -25%", txt)
        self.assertIn("足止め", txt)          # 足止めの節も画面から消えていない
        self.assertNotIn("秒", txt)
        self.assertFalse(re.search(r"解除|打ち消", txt),
                         "「解除」と読める語が混ざっている: " + txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
