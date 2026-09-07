# -*- coding: utf-8 -*-
"""矢数の数え方 "shots"（§7.186）の受け入れ試験。

**矢は各部隊が自分で持ち、通常射撃をした量だけ減る。** 既定は "attrition" のままなので、
まず「既定が動いていない」ことを見張る。次に "shots" の規則を1つずつ確かめる:
部隊ごとに持つ／撃ったときだけ減る／兵法は使わない／弓以外は関係ない。
"""
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from sim import rosterdata as R, field as F, match as M, dummies as D   # noqa: E402

R.load_skills_into_field(); R.load_traits_into_field()
F.SKILLS_ON = F.TRAITS_ON = True
ROSTER = M._roster_cards()
CARDS = {c.name: c for c in ROSTER}
FOE = D.make_entry(ROSTER, [p for p in D.PERSONAS if p.name == "強弓"][0], 3,
                   caps=(("赤壁", 40.0),)).units[0]
ARCHER = "黄忠〔定軍山〕"
INFANTRY = "張飛〔当陽橋〕"


def army():
    order = [CARDS[INFANTRY], CARDS["曹仁〔堅守〕"], CARDS["郝昭〔陳倉〕"],
             CARDS[ARCHER], CARDS["李典〔慎重〕"], CARDS["貂蝉〔傾国〕"]]
    return F.Army(tuple(order), F.FORM_STANDARD)


def run(**consts):
    """定数を差し替えて1局打ち、名前→(与損害, 兵法ぶんの損害) を返す。"""
    keep = {k: getattr(F, k) for k in consts}
    for k, v in consts.items():
        setattr(F, k, v)
    try:
        r = F.simulate(army(), FOE, dt=0.25, seed=22)
    finally:
        for k, v in keep.items():
            setattr(F, k, v)
    return {x[0]: (x[2], x[5]) for x in r["dealt_a"]}, r


class AmmoShotsTest(unittest.TestCase):
    def test_default_mode_unchanged(self):
        """既定は "attrition" のまま。ここが変わったら値札が丸ごと動く（§7.185）。"""
        self.assertEqual(F.AMMO_MODE, "attrition")
        self.assertAlmostEqual(F.AMMO_SPAN, 0.25)

    def test_parts_matches_plain(self):
        """parts=True の1つ目は parts なしと同じ。2つ目は接敵抑制だけ（矢切れを含まない）。"""
        a = army()
        u = F.Unit(0, CARDS[ARCHER], F.FORM_STANDARD, (0.0, 0.0), False)
        u.shot = 99.0                       # 矢は尽きている
        gaps = [10.0, 20.0, 30.0]
        for mode in ("attrition", "shots"):
            keep = F.AMMO_MODE
            F.AMMO_MODE = mode
            try:
                sup = F._suppress(u, gaps)
                sup2, fire = F._suppress(u, gaps, parts=True)
            finally:
                F.AMMO_MODE = keep
            self.assertAlmostEqual(sup, sup2, msg=mode)
            self.assertLessEqual(sup2, fire + 1e-12, msg=mode)
            self.assertAlmostEqual(fire, 1.0 - F.SUPPRESS_MAX * F.smooth_gate(
                min(gaps), 0.0, F.SUPPRESS_R), msg=mode)
        self.assertEqual(F._suppress(F.Unit(0, CARDS[INFANTRY], F.FORM_STANDARD, (0.0, 0.0), True), gaps, parts=True),
                         (1.0, 1.0))

    def test_arrows_run_out(self):
        """"shots" で持ち矢を絞ると、弓の**通常射撃**が落ちる（＝実際に数えている）。

        総量で見てはいけない: 矢が減ると戦闘が長引き、その間に兵法が増えるので
        総量は逆に増えうる（実測 16,948 → 16,830）。**通常射撃ぶんだけ**を見る。
        """
        many, _ = run(AMMO_MODE="shots", AMMO_SHOTS=999.0)
        few, _ = run(AMMO_MODE="shots", AMMO_SHOTS=2.0)
        shots = lambda d: d[ARCHER][0] - d[ARCHER][1]
        self.assertLess(shots(few), shots(many) * 0.9,
                        "矢を絞れば通常射撃は落ちる（{:,.0f} → {:,.0f}）".format(
                            shots(many), shots(few)))

    def test_only_archers_spend(self):
        """矢を数えるのは弓だけ（歩兵・騎兵の抑制は 1.0 のまま）。"""
        for name, front in ((INFANTRY, True), ("貂蝉〔傾国〕", False)):
            u = F.Unit(0, CARDS[name], F.FORM_STANDARD, (0.0, 0.0), front)
            u.shot = 999.0
            if u.typ != F.ARC:
                self.assertEqual(F._suppress(u, [0.0], parts=True), (1.0, 1.0), name)

    def test_skill_does_not_spend_arrows(self):
        """兵法は矢を使わない: 矢が尽きても兵法ぶんの損害は残る。"""
        few, _ = run(AMMO_MODE="shots", AMMO_SHOTS=0.5)
        self.assertGreater(few[ARCHER][1], 0.0, "矢が尽きても兵法は出る")

    def test_not_spent_out_of_range(self):
        """射程外・的なしでは減らない: 相手がいない側の弓は矢を1本も使わない。

        `_weights` が射程外で 0 を返し、射撃の場（tot<=0 で continue）へ来ないので、
        持ち矢を極小にしても**開幕からの数ティック**では出力が落ちない。
        ここでは規則そのものを見る: 消費は射撃の場だけに書かれている。
        """
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "sim", "field.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(src.count("u.shot += (u.men / u.men0) * fire"), 2,
                         "消費は左右の射撃の場の2か所だけ")
        self.assertIn('elif AMMO_MODE == "shots":\n                pass', src,
                      "従来の消費の場では素通し（二重計上しない）")

    def test_suppressed_archer_saves_arrows(self):
        """取り付かれて撃てない間は矢が減らない（消費に接敵抑制の残りを掛けている）。"""
        u = F.Unit(0, CARDS[ARCHER], F.FORM_STANDARD, (0.0, 0.0), False)
        near = F._suppress(u, [0.0], parts=True)[1]
        far = F._suppress(u, [9999.0], parts=True)[1]
        self.assertLess(near, far * 0.5, "密着では放てる割合が半分以下")
        self.assertAlmostEqual(far, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
