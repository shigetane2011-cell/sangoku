# -*- coding: utf-8 -*-
"""効果文の伸び縮み（`rosterdata._scale_effect`）の受け入れ試験（§7.264）。

段を動かすとき（`retier`）と、決戦型を太らせるとき（`tools/retier_big.py`）に
通る唯一の口。**ここが間違うと名簿139枚が静かにずれる。**

見張るのは4つ。

1. **自分が受ける不利（反動）は伸びない。** 下の正規表現は「反動 攻撃力
   -20%（60秒）」の後半に当たるので、素通しすると**不利だけが長くなる**
   （＝札が弱くなる）。実際に踏んだ
2. **代償（兵力N%）も伸びない**（どの口にも当たらない、が意図どおりか）
3. **威力幅は両端そろって伸びる**（片側だけだと値付けの中央値がずれる）
4. 引き延ばしは**1倍を超えたぶん**だけ伸びる（150% を丸ごと m 倍しない）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import rosterdata as R        # noqa: E402

M = 1.4


class SelfPenaltiesDoNotGrow(unittest.TestCase):
    def test_recoil_is_left_alone(self):
        out = R._scale_effect("ダメージ 威力2300% + 反動 攻撃力 -20%（60秒）", M)
        self.assertIn("反動 攻撃力 -20%（60秒）", out)
        self.assertIn("威力3220%", out)

    def test_recoil_alone_is_left_alone(self):
        self.assertEqual(R._scale_effect("反動 攻撃力 -25%（90秒）", M),
                         "反動 攻撃力 -25%（90秒）")

    def test_cost_is_left_alone(self):
        out = R._scale_effect("ダメージ 威力3200% + 代償 兵力15%", M)
        self.assertIn("代償 兵力15%", out)

    def test_a_real_debuff_still_grows(self):
        """**相手に掛ける**弱体は伸びる（伸ばすのは秒数・§6.5 の同名規則）。"""
        out = R._scale_effect("敵前衛 攻撃力 -15%（40秒）", M)
        self.assertIn("攻撃力 -15%（56秒）", out)


class TheBandGrowsAtBothEnds(unittest.TestCase):
    def test_both_ends(self):
        out = R._scale_effect("ダメージ 威力267〜890%", M)
        self.assertEqual(out, "ダメージ 威力374〜1246%")

    def test_the_ratio_is_kept(self):
        out = R._scale_effect("ダメージ 威力1400〜2600%", M)
        lo, hi = (float(x) for x in
                  out.replace("ダメージ 威力", "").rstrip("%").split("〜"))
        self.assertAlmostEqual(hi / lo, 2600.0 / 1400.0, places=2)


class StretchGrowsOnlyTheExcess(unittest.TestCase):
    def test_only_the_part_above_one(self):
        # 150% は「元の 0.5 ぶん足す」。1.4倍なら 0.7 ぶん＝170%
        self.assertEqual(R._scale_effect("引き延ばし 150%", M), "引き延ばし 170%")

    def test_it_stops_at_the_cap(self):
        self.assertEqual(R._scale_effect("引き延ばし 180%", 5.0),
                         "引き延ばし {:.0f}%".format(F.STRETCH_CAP * 100.0))

    def test_mimic_scales_by_seconds(self):
        self.assertEqual(R._scale_effect("写し取り 決戦型（35秒）", M),
                         "写し取り 決戦型（49秒）")


class ItIsReversible(unittest.TestCase):
    def test_the_roster_still_reads(self):
        """名簿の決戦型59枚を伸ばして、**全部読めること**（読めない文を作らない）。"""
        n = 0
        for r in R.skills():
            sk = F.SKILL_INFO.get(r["兵法名"])
            if sk is None or R.tier_for(r, sk) != "大技":
                continue
            F._parse_skill(R._scale_effect(r["効果"], M), r["対象"])
            n += 1
        self.assertGreater(n, 40, "決戦型が急に減っている")


if __name__ == "__main__":
    R.load_skills_into_field()
    unittest.main(verbosity=2)
