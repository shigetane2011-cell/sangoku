# -*- coding: utf-8 -*-
"""§7.174 帳簿の精算の受け入れ試験。

**盤面の計算（勝敗・対象選択・反射・余勢）には触れず、帳簿だけを直した**ことを
確かめる。同時解決の単位（通常攻撃の1ティックの蓄積器・兵法の1窓）ごとに、対象が
実際に失った／得た兵力を各寄与（防御・軽減後の量）へ比例配分し、残りは超過として
別に持つ。

  1. 残兵100へ同威力2発 → 50ずつ（超過は各 実効−50）。討ち取りは両方に付く
  2. 部隊の処理順を入れ替えても各寄与の実損害は変わらない
  3. 寄与が 3:1 なら 75:25
  4. 損害と回復が同じ窓: 開始・終了の差ではなく、回復ぶんも損害の実量に数える
  5. 同じ窓の回復が満タンで余る: 余りは回復の側に付け、実回復は分け合う
  6. 通常攻撃・馬前・同士討ちが同じティックに重なる: 同じ精算で比例配分
  7. 1戦を通した恒等式: 実損失 = 被ダメ + 代償 + 本陣崩壊 − 受けた回復、
     与ダメ = 矛先の合計、被ダメの総和 = 与ダメ + 同士討ちの総和
  8. 記録の有無・帳簿の付け方で勝敗・兵力が変わらない
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

TSTR = "敵1体（残兵力が最少）"
HEAL1 = "味方1体（残兵力が最少）"


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _filler(n, typ=F.INF, cost=4.0):
    return [F._synth(cost, typ) for _ in range(n)]


class _Window(unittest.TestCase):
    def setUp(self):
        F._CASTS, F._CAST_BY_ID = [], {}
        F._JP["A"], F._JP["B"] = "曹", "孫"
        F._DUP_NAMES = set()
        self.big = F._parse_skill("ダメージ 威力3000%", TSTR)
        self.mid = F._parse_skill("ダメージ 威力1000%", TSTR)

    def tearDown(self):
        F._CASTS, F._CAST_BY_ID = None, {}
        if F._SKILL_DELTA is not None:
            F._flush_men()

    def _units(self, men=100.0):
        ua = F.build(_army(_filler(6)), 1)
        ub = F.build(_army(_filler(6)), -1)
        ub[0].men = men
        return ua, ub, ub[0]

    def _cast(self, u, sk, ua, ub, name="試", ev=None):
        F._apply_skill(u, sk, TSTR, ua, ub, 1.0, src=name, name=name,
                       kind_jp="兵法", ev=ev, seen=set())

    # 1. 残兵100へ同威力2発 → 50ずつ
    def test_two_equal_hits_split_evenly(self):
        ua, ub, tgt = self._units()
        ev = []
        F._open_men_window()
        self._cast(ua[0], self.big, ua, ub, ev=ev)
        self._cast(ua[1], self.big, ua, ub, ev=ev)
        F._flush_men()
        self.assertEqual(tgt.men, 0.0)
        c1, c2 = F._CASTS
        self.assertAlmostEqual(c1["damage"], 50.0, places=6)
        self.assertAlmostEqual(c2["damage"], 50.0, places=6)
        self.assertAlmostEqual(ua[0].dealt_skill, 50.0, places=6)
        self.assertAlmostEqual(ua[1].dealt_skill, 50.0, places=6)
        self.assertAlmostEqual(tgt.taken, 100.0, places=6)
        # 超過は「実効値 − 実損害」で、両者を足すと当てた量の合計 − 100
        self.assertGreater(c1["over"], 0.0)
        self.assertAlmostEqual(c1["over"] + c2["over"] + 100.0,
                               c1["damage"] + c1["over"] + c2["damage"] + c2["over"], places=6)
        self.assertAlmostEqual(tgt.over_taken, c1["over"] + c2["over"], places=6)
        self.assertAlmostEqual(ua[0].over_dealt, c1["over"], places=6)
        # 討ち取りは同じ窓で寄与した両方に付き、**共同撃破**の印が付く
        self.assertEqual(c1["kills"], [F._who(tgt)])
        self.assertEqual(c2["kills"], [F._who(tgt)])
        self.assertEqual(c1["shared_kills"], [F._who(tgt)])
        self.assertEqual(c2["shared_kills"], [F._who(tgt)])
        # 実況: 各発動の行は量を語るが「討ち取る」とは言わず（二重に名乗らない）、
        # 壊滅の行が「○○の【試】と△△の【試】で□□の隊が壊滅」とまとめる
        texts = [e.text for e in ev if e.kind == "兵法"]
        self.assertEqual(len(texts), 2)
        for tx in texts:
            self.assertIn("に 50 の損害", tx)
            self.assertNotIn("討ち取る", tx)
        gap = [[10.0] * len(ub) for _ in ua]
        F._log_tick(ev, set(), 1.0, ua, ub, gap)
        wipe = [e for e in ev if e.kind == "壊滅"]
        self.assertEqual(len(wipe), 1)
        self.assertIn("{}の【試】と{}の【試】で{}の隊が壊滅".format(
            F._who(ua[0]), F._who(ua[1]), F._who(tgt)), wipe[0].text)
        self.assertTrue(wipe[0].must)
        # 矛先も実損害（鍵は _pair_add と同じ: 名のある札は _who、合成は兵種名）
        key = F._who(tgt) if tgt.name else F.TYPE_JP[tgt.typ]
        self.assertAlmostEqual(ua[0].pair[key], 50.0, places=6)

    # 2. 処理順を入れ替えても貢献度が変わらない
    def test_order_independent(self):
        def run(order):
            ua, ub, tgt = self._units()
            F._CASTS, F._CAST_BY_ID = [], {}
            F._open_men_window()
            for i in order:
                self._cast(ua[i], self.big if i == 0 else self.mid, ua, ub, name="試%d" % i)
            F._flush_men()
            return {c["skill"]: (round(c["damage"], 9), round(c["over"], 9), tuple(c["kills"]))
                    for c in F._CASTS}, tgt.men
        a, ma = run((0, 1))
        b, mb = run((1, 0))
        self.assertEqual(a, b)
        self.assertEqual(ma, mb)

    # 3. 寄与 3:1 → 75:25
    def test_proportional_to_contribution(self):
        ua, ub, tgt = self._units()
        F._open_men_window()
        self._cast(ua[0], self.big, ua, ub, name="大")
        self._cast(ua[1], self.mid, ua, ub, name="中")
        F._flush_men()
        c_big, c_mid = F._CASTS
        eff_big = c_big["damage"] + c_big["over"]
        eff_mid = c_mid["damage"] + c_mid["over"]
        self.assertAlmostEqual(c_big["damage"], 100.0 * eff_big / (eff_big + eff_mid), places=6)
        self.assertAlmostEqual(c_mid["damage"], 100.0 * eff_mid / (eff_big + eff_mid), places=6)
        self.assertAlmostEqual(c_big["damage"] + c_mid["damage"], 100.0, places=6)

    # 4. 損害と回復が同じ窓（回復を先に撃って対象に載せてから2発）
    def test_damage_and_heal_in_one_window(self):
        ua, ub, tgt = self._units()
        heal = F._parse_skill("回復 最大兵力の1%", HEAL1)
        F._open_men_window()
        F._apply_skill(ub[1], heal, HEAL1, ub, ua, 1.0, src="癒", name="癒", kind_jp="兵法")
        self._cast(ua[0], self.big, ua, ub, name="甲")
        self._cast(ua[1], self.big, ua, ub, name="乙")
        F._flush_men()
        c_heal, c1, c2 = F._CASTS
        h = c_heal["heal"]
        self.assertGreater(h, 0.0)
        self.assertAlmostEqual(tgt.heal_taken, h, places=6)
        self.assertAlmostEqual(ub[1].healed, h, places=6)
        # 盤面: 2発とも開始時の残兵で頭打ち（100ずつ）なので 100+h−200
        self.assertAlmostEqual(tgt.men, max(0.0, 100.0 + h - 200.0), places=6)
        # 帳簿: 実損害は「開始・終了の差」ではなく、回復ぶんも含めて 200
        self.assertAlmostEqual(c1["damage"] + c2["damage"], 200.0, places=6)
        self.assertAlmostEqual(tgt.taken, 200.0, places=6)
        # 恒等式: 実損失 = 被ダメ − 受けた回復
        self.assertAlmostEqual(100.0 - tgt.men, tgt.taken - tgt.heal_taken, places=6)

    # 5. 同じ窓の回復2つが満タンで余る
    def test_overheal_shared(self):
        ua, ub, tgt = self._units(men=0.0)
        tgt.men = tgt.men0 - 40.0
        hb = F._parse_skill("回復 攻撃力の300%", HEAL1)
        F._open_men_window()
        F._apply_skill(ub[1], hb, HEAL1, ub, ua, 1.0, src="癒A", name="癒A", kind_jp="兵法")
        F._apply_skill(ub[2], hb, HEAL1, ub, ua, 1.0, src="癒B", name="癒B", kind_jp="兵法")
        F._flush_men()
        self.assertAlmostEqual(tgt.men, tgt.men0, places=6)
        a, b = F._CASTS
        self.assertAlmostEqual(a["heal"] + b["heal"], 40.0, places=6)
        self.assertAlmostEqual(a["heal"], 20.0, places=6)
        self.assertAlmostEqual(tgt.heal_taken, 40.0, places=6)
        self.assertAlmostEqual(ub[1].healed + ub[2].healed, 40.0, places=6)

    # 単独の寄与（窓なし）: 実損害は残兵で頭打ち、超過は別。討ち取りは単独なので
    # 発動の行が「討ち取る」と言い、壊滅の行は従来どおり
    def test_single_hit_without_window(self):
        ua, ub, tgt = self._units()
        ev = []
        self._cast(ua[0], self.big, ua, ub, ev=ev)
        c = F._CASTS[-1]
        self.assertAlmostEqual(c["damage"], 100.0, places=6)
        self.assertGreater(c["over"], 1000.0)
        self.assertAlmostEqual(ua[0].dealt_skill, 100.0, places=6)
        self.assertAlmostEqual(ua[0].over_dealt, c["over"], places=6)
        self.assertEqual(tgt.men, 0.0)
        self.assertEqual(c["kills"], [F._who(tgt)])
        self.assertEqual(c["shared_kills"], [])
        self.assertIn("討ち取る", ev[-1].text)
        gap = [[10.0] * len(ub) for _ in ua]
        F._log_tick(ev, set(), 1.0, ua, ub, gap)
        wipe = [e for e in ev if e.kind == "壊滅"]
        self.assertEqual(len(wipe), 1)
        self.assertIn("ついに壊滅", wipe[0].text)

    # 撃破に関与した回数（共同撃破を含む）がリプレイの行に載る
    def test_kills_involved_in_replay_rows(self):
        import sim.play as PL
        name = "＿共同試験"
        F.SKILL_INFO[name] = F._parse_skill("ダメージ 威力2000%", "敵全体")
        F.SKILL_TARGET[name] = "敵全体"
        try:
            atk = F.Card(**{**F._synth(10.0, F.CAV).__dict__, "skill": name, "gauge_cost": 60.0})
            a = _army([atk, atk] + _filler(4))
            b = _army(_filler(6))
            rep = PL.replay_data(a, b, 0.25, 3, True)
        finally:
            F.SKILL_INFO.pop(name, None)
            F.SKILL_TARGET.pop(name, None)
        for u in rep["mine"] + rep["foe"]:
            self.assertIn("kills_involved", u)
            self.assertIn("kills_shared", u)
            self.assertLessEqual(u["kills_shared"], u["kills_involved"])
        # 記録の側と一致: 各発動の kills（相手ごとに1回）を武将で束ねた数
        from collections import defaultdict
        by = defaultdict(set)
        for c in rep["casts"]:
            for tg in c["kills"]:
                by[(c["side"], c["who"])].add(tg)
        for u in rep["mine"] + rep["foe"]:
            n = sum(len(v) for (sd, who), v in by.items()
                    if sd == ("mine" if u in rep["mine"] else "foe") and who.startswith(u["card"]))
            self.assertEqual(u["kills_involved"], n)


class NormalPhase(unittest.TestCase):
    """6. 通常攻撃・馬前・同士討ちが同じティックに重なる。"""

    def test_settle_shares_actual_loss(self):
        F._JP["A"], F._JP["B"] = "曹", "孫"
        ua = F.build(_army(_filler(6)), 1)
        ub = F.build(_army(_filler(6)), -1)
        t = ub[0]
        t.men = 100.0
        acc = [0.0] * 6
        contrib = [("normal", ua[0], t, 300.0), ("normal", ua[1], t, 100.0),
                   ("cover", ua[2], t, 50.0), ("ff", ub[1], t, 50.0)]
        for _, _, _, a in contrib:
            acc[0] += a
        F._settle_normal(contrib, ub, acc)
        self.assertAlmostEqual(ua[0].dealt, 60.0, places=6)
        self.assertAlmostEqual(ua[1].dealt, 20.0, places=6)
        self.assertAlmostEqual(ua[2].dealt, 10.0, places=6)
        self.assertAlmostEqual(t.covered, 10.0, places=6)
        self.assertAlmostEqual(ub[1].ff_dealt, 10.0, places=6)
        self.assertAlmostEqual(t.ff_taken, 10.0, places=6)
        self.assertAlmostEqual(t.taken, 100.0, places=6)
        self.assertAlmostEqual(t.over_taken, 400.0, places=6)
        self.assertAlmostEqual(ua[0].over_dealt, 240.0, places=6)
        self.assertAlmostEqual(ub[1].ff_over, 40.0, places=6)
        self.assertEqual(contrib, [])
        # 蓄積器はそのまま（盤面の反映は呼び手の仕事）
        self.assertAlmostEqual(acc[0], 500.0, places=6)

    def test_friendly_fire_immediate_caps_by_remaining(self):
        ua = F.build(_army(_filler(6)), 1)
        for x in ua[1:]:
            x.men = 50.0
        acc = [0.0] * 6
        F._friendly_fire(ua[0], ua, acc, 5000.0)
        for x in ua[1:]:
            self.assertAlmostEqual(x.ff_taken, 50.0, places=6)
        self.assertAlmostEqual(ua[0].ff_dealt, 250.0, places=6)
        self.assertGreater(ua[0].ff_over, 0.0)
        self.assertAlmostEqual(sum(acc[1:]), ua[0].ff_dealt + ua[0].ff_over, places=6)


class WholeBattle(unittest.TestCase):
    """7〜8. 1戦を通した恒等式と、帳簿が勝敗に触れないこと。"""

    def _decks(self):
        import sim.match as M
        import sim.play as PL
        cards = M._roster_cards()
        a, ea = PL.parse_deck(cards, "潘璋〔急襲〕、趙雲〔長坂坡〕、周泰〔身代〕、曹操〔魏王〕、"
                              "祝融〔火神〕、田豊〔剛直〕", "鶴翼")
        b, eb = PL.parse_deck(cards, "夏侯淵〔神速〕、霍峻〔葭萌〕、陳琳〔檄文〕、楽綝〔揚州〕、"
                              "丁奉〔雪中〕、孫尚香〔弓腰姫〕", "雁行")
        c, ec = PL.parse_deck(cards, "荀彧〔王佐〕、賈詡〔毒士〕、韓当〔老弓〕、周泰〔身代〕、"
                              "華佗〔神医〕、田豊〔剛直〕", "魚鱗")
        self.assertFalse(ea or eb or ec, (ea, eb, ec))
        return a, b, c

    def _check_identities(self, r, casts):
        rows = r["dealt_a"] + r["dealt_b"]
        for row in rows:
            n, t, d, m, m0, sd, fa, ff, rf, cs, hl, al, tk = row[:13]
            od, ot, fo, sc, ht, cl, wl = row[-9:-2]
            # 実損失 = 被ダメ + 代償 + 本陣崩壊 + 壊滅解隊 − 受けた回復
            # （壊滅解隊は §7.239。ANNIHIL_UNIT を割った隊は戦場から降りるので、
            #   そのとき残っていた兵は**誰の戦果でもない**損失として別に持つ）
            self.assertAlmostEqual(m0 - m, tk + sc + cl + wl - ht,
                                   delta=1e-6 * max(m0, 1.0), msg=str(n))
            # 与ダメ = 矛先の合計
            self.assertAlmostEqual(d, sum(row[16].values()), delta=1e-6 * max(d, 1.0), msg=str(n))
            self.assertGreaterEqual(od, 0.0)
            self.assertGreaterEqual(ot, 0.0)
        # 被ダメの総和 = 与ダメ + 同士討ちの総和
        self.assertAlmostEqual(sum(x[12] for x in rows),
                               sum(x[2] for x in rows) + sum(x[7] for x in rows),
                               delta=1e-6 * max(sum(x[12] for x in rows), 1.0))
        # 発動記録の実損害の総和は兵法与ダメの総和を超えない（継続分は記録の外）
        self.assertLessEqual(sum(c["damage"] for c in casts),
                             sum(x[5] for x in rows) + 1e-6)

    def test_identities_hold_over_real_battles(self):
        a, b, c = self._decks()
        for x, y, seed in ((a, b, 4242), (b, c, 7), (c, a, 99), (a, c, 3)):
            for dt in (0.5, 0.25):
                casts = []
                r = F.simulate(x, y, dt, seed=seed, casts=casts)
                self._check_identities(r, casts)

    def test_bookkeeping_does_not_touch_outcome(self):
        a, b, c = self._decks()
        for x, y, seed in ((a, b, 4242), (b, c, 7)):
            plain = F.simulate(x, y, 0.5, seed=seed)
            casts, ev = [], []
            recorded = F.simulate(x, y, 0.5, seed=seed, casts=casts, events=ev)
            for key in ("score", "diff", "ra", "rb", "t", "reason"):
                self.assertEqual(plain[key], recorded[key])
            self.assertEqual([u[3] for u in plain["dealt_a"]], [u[3] for u in recorded["dealt_a"]])
            self.assertEqual([u[3] for u in plain["dealt_b"]], [u[3] for u in recorded["dealt_b"]])
            self.assertTrue(casts)


if __name__ == "__main__":
    unittest.main(verbosity=1)
