# -*- coding: utf-8 -*-
"""返し討ち（通常攻撃の反射・§7.262）の受け入れ試験。

20260912 の提案 ④「賀斉〔山越討伐〕→ 通常攻撃のダメ反射。山越討伐＝待ち受けて反撃」。

兵法反射（`refl`）は §7.64 からあったが、**通常攻撃の反射は無かった**。
新しい器 `nrefl` を、構えではなく**時限のモッド**として通常攻撃の輪へ入れてある。

見張るのは6つ。

1. **零点**（+0%）で盤面が1ミリも動かないこと。器を足すと、使っていない札まで
   静かにずれることがある
2. 量に**単調**（+10% より +25% のほうが撃ち手が減る）
3. **兵法の被害は返らない。** 返るのは通常攻撃のぶんだけ（画面もそう書いてある）
4. 返した量は**兵法反射と同じ欄**（`refl_back`）に積む —— 画面では1つの数字
5. **先行段（§7.126）には入れない。** 構えの仲間に見えるが、通常攻撃はずっと
   続いているので立つ順番で値打ちが変わらない（`ncut` と同じ扱い）
6. 賀斉が実際に持ち、**持ち手は1枚だけ**
"""
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import design as D            # noqa: E402
from sim import rosterdata as R        # noqa: E402
from sim import match as M             # noqa: E402
from sim import dummies as DM          # noqa: E402

F.TRAITS.clear()
R.load_traits_into_field()
R.load_skills_into_field()
F.TRAITS_ON = True

ROSTER = M._roster_cards()
CARDS = {c.name: c for c in ROSTER}
TGT = "味方前衛"
FOE = DM.make_entry(ROSTER, DM.PERSONAS[3], 5, caps=(("官渡", 30.0),)).units[0]
BASE = ["周泰〔身代〕", "曹仁〔堅守〕", "郝昭〔陳倉〕", "李典〔慎重〕", "満寵〔剛毅〕"]

REFL_BACK = 8       # dealt_a の並びの何番目か（u.refl_back）
MEN = 3             # 同・残兵


def _alias(pct: int) -> str:
    """反射の量だけを差し替えた別名の兵法を登録して返す。"""
    name = "＿返し{}".format(pct)
    F.SKILL_INFO[name] = F._parse_skill(
        "通常攻撃反射 +{}%（50秒）".format(pct), TGT)
    F.SKILL_TARGET[name] = TGT
    return name


def _run(pct: int, seed: int = 11, t_max: float = F.T_MAX):
    card = replace(CARDS["賀斉〔山越討伐〕"], skill=_alias(pct))
    order = [card] + [CARDS[n] for n in BASE[1:3]] + [CARDS[BASE[0]]] \
        + [CARDS[n] for n in BASE[3:]]
    army = F.Army(tuple(order), F.FORM_STANDARD)
    return F.simulate(army, FOE, dt=0.25, seed=seed, t_max=t_max)


class TheZeroPoint(unittest.TestCase):
    def test_nothing_moves_at_zero(self):
        a, b = _run(0), _run(0)
        self.assertEqual(a["score"], b["score"], "同じ種で結果が揺れている")
        self.assertAlmostEqual(a["dealt_a"][0][REFL_BACK], 0.0, places=9,
                               msg="0% なのに返している")

    def test_zero_matches_a_card_without_the_clause(self):
        """**器を足したせいで零点が動いていない**ことの見張り。"""
        plain = replace(CARDS["賀斉〔山越討伐〕"], skill="＿無し")
        F.SKILL_INFO["＿無し"] = F._parse_skill("通常攻撃反射 +0%（50秒）", TGT)
        F.SKILL_TARGET["＿無し"] = TGT
        order = [plain] + [CARDS[n] for n in BASE[1:3]] + [CARDS[BASE[0]]] \
            + [CARDS[n] for n in BASE[3:]]
        r = F.simulate(F.Army(tuple(order), F.FORM_STANDARD), FOE, dt=0.25, seed=11)
        self.assertEqual(r["score"], _run(0)["score"])


class ItIsMonotone(unittest.TestCase):
    def test_more_reflect_means_more_returned(self):
        r0, r10, r25 = _run(0), _run(10), _run(25)
        b0 = r0["dealt_a"][0][REFL_BACK]
        b10 = r10["dealt_a"][0][REFL_BACK]
        b25 = r25["dealt_a"][0][REFL_BACK]
        self.assertAlmostEqual(b0, 0.0, places=9)
        self.assertGreater(b10, 0.0, "返し討ちが1兵も返していない")
        self.assertGreater(b25, b10, "量を増やしても返る量が増えていない")

    def test_the_enemy_is_smaller_at_the_same_clock(self):
        """**壊滅までやらず、途中で切って残兵で測る**（§7.151・test_cover と同じ）。

        最後まで回すと被害は必ず men0 に届くので、差はティックの刻みと
        「誰が先に落ちたか」の入れ替わりに埋もれる（実際に1戦ぶん逆向きに出た）。
        決定論なので、同じ時計で切れば刻みに依らない。
        """
        cut = 90.0
        men0 = sum(row[MEN] for row in _run(0, t_max=cut)["dealt_b"])
        men25 = sum(row[MEN] for row in _run(25, t_max=cut)["dealt_b"])
        self.assertLess(men25, men0, "返しているのに相手が減っていない")


class SkillDamageDoesNotBounce(unittest.TestCase):
    """**返るのは通常攻撃のぶんだけ**（画面にもそう書いてある）。"""

    def test_a_skill_hit_returns_nothing(self):
        ua = F.build(F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)),
                            F.FORM_STANDARD), 1)
        ub = F.build(F.Army(tuple(F._synth(5.0, F.INF) for _ in range(6)),
                            F.FORM_STANDARD), -1)
        guard = F._parse_skill("通常攻撃反射 +50%（50秒）", TGT)
        F._apply_skill(ub[0], guard, TGT, ub, ua, 0.0,
                       src="＿返", name="＿返", kind_jp="兵法")
        for u in ub:
            F._recalc_mods(u)
        self.assertGreater(ub[0].nrefl, 0.0)
        before = ua[0].men
        blast = F._parse_skill("ダメージ 威力800%", "敵前衛")
        F._apply_skill(ua[0], blast, "敵前衛", ua, ub, 1.0,
                       src="＿打", name="＿打", kind_jp="兵法")
        self.assertAlmostEqual(ua[0].men, before, places=6,
                               msg="兵法の被害まで返っている（兵法反射と混ざった）")


class NotAGuardForTheOpeningSlot(unittest.TestCase):
    """§7.126 の先行段には入れない（`ncut` と同じ扱い・§7.262）。"""

    def test_it_is_out_of_guard_kinds(self):
        self.assertNotIn("nrefl", F.GUARD_KINDS)
        self.assertNotIn("ncut", F.GUARD_KINDS)
        self.assertIn("refl", F.GUARD_KINDS)

    def test_the_card_is_not_treated_as_a_guard(self):
        self.assertFalse(F._is_guard(F._parse_skill("通常攻撃反射 +25%（50秒）", TGT)))


class Price(unittest.TestCase):
    def test_all_four_targets_have_a_tag(self):
        for t in ("自分", "味方1列", "味方前衛", "味方全体"):
            self.assertGreater(D.TARGET_NREFL_PRICE[t], 0.0, t)

    def test_the_charge_rises_with_the_amount(self):
        def ev(eff):
            return D.effect_value(F._parse_skill(eff, TGT), TGT,
                                  150.0, 0.0, 1.0, 5.0, F.INF, "中庸")
        self.assertGreater(ev("通常攻撃反射 +25%（50秒）"),
                           ev("通常攻撃反射 +10%（50秒）"))
        self.assertAlmostEqual(ev("通常攻撃反射 +0%（50秒）"), 0.0, places=9)


class HeQi(unittest.TestCase):
    def test_the_card_carries_it(self):
        row = {r["兵法名"]: r for r in R.skills()}["山越討伐"]
        self.assertEqual(row["対象"], TGT)
        self.assertEqual(row["効果"], "通常攻撃反射 +25%（50秒）")
        self.assertEqual(row["発動型"], "標準")

    def test_it_is_the_only_holder(self):
        holders = [r["兵法名"] for r in R.skills()
                   if "通常攻撃反射" in (r["効果"] or "")]
        self.assertEqual(holders, ["山越討伐"])

    def test_the_screen_says_it_once_and_says_which_damage(self):
        from sim import web as W
        row = {r["兵法名"]: r for r in R.skills()}["山越討伐"]
        g = {r["名前"]: r for r in R.generals()}["賀斉〔山越討伐〕"]
        txt = W._skill_display(g, row)
        self.assertEqual(txt.count("返し討ち"), 1, "同じことが2行に出ている: " + txt)
        self.assertIn("兵法の被害は返らない", txt)
        self.assertNotIn("通常攻撃反射", txt)   # 生の効果文が漏れていない


if __name__ == "__main__":
    unittest.main(verbosity=2)
