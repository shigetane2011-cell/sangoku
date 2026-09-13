# -*- coding: utf-8 -*-
"""§7.175 BO3 計器の共通部（sim/bo3meter.py・`production_v1`）の受け入れ試験。

  1. 計器の各戦の結果・シリーズの勝者が、同条件で直接呼んだ M.play() と一致する
  2. 同じシードなら再実行の結果が一致する
  3. 左右を反転しても候補側の勝敗として正しく集計される（相手側から測ると勝敗が入れ替わる）
  4. 引き分けを敗北へ混ぜない（勝率＝勝÷全、得点率は別名）
  5. 平均残存率差が改善しても BO3 勝率が下がる場合を、そのまま報告できる
  6. 同じ軍を繰り返し測っても余剰コストの補正が累積しない
  7. 本番の検証を通らない登録は測らない
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import bo3meter as B          # noqa: E402
from sim import match as M             # noqa: E402
from tools import balance_common as C  # noqa: E402

_DATA = C.load_fixtures()
_CARDS = C.roster()
_IDX = C.card_index(_CARDS)


def _set(key):
    return C.named_set(_DATA, key, _IDX)


class MatchesProduction(unittest.TestCase):
    """1. 計器 ＝ 本番の M.play()。"""

    def test_series_equals_direct_play(self):
        cn, cand = _set("chappy")
        fn, foe = _set("counter")
        for seed in (0, 7):
            # A: 候補が先手
            s = B.play_series(cand, foe, seed, "A", dt=0.5)
            r = M.play(cand, foe, dt=0.5, seed=seed)
            self.assertEqual(s["outcome"], {"A": "W", "B": "L"}.get(r["winner"], "D"))
            self.assertAlmostEqual(s["match_diff"], r["diff"], places=12)
            for g, gr in zip(s["games"], r["games"]):
                self.assertEqual(g["regulation"], gr["規定"])
                self.assertEqual(g["outcome"], B.game_outcome(gr["結果"]["score"]))
                self.assertAlmostEqual(g["diff"], gr["結果"]["diff"], places=12)
                self.assertAlmostEqual(g["remain_diff"], gr["結果"]["ra"] - gr["結果"]["rb"], places=12)
            # B: 相手が先手（本番は M.play(相手, 候補) — 候補の視点へ戻す）
            s = B.play_series(cand, foe, seed, "B", dt=0.5)
            r = M.play(foe, cand, dt=0.5, seed=seed)
            self.assertEqual(s["outcome"], {"B": "W", "A": "L"}.get(r["winner"], "D"))
            self.assertAlmostEqual(s["match_diff"], -r["diff"], places=12)
            for g, gr in zip(s["games"], r["games"]):
                self.assertEqual(g["outcome"], B.game_outcome(1.0 - gr["結果"]["score"]))
                self.assertAlmostEqual(g["diff"], -gr["結果"]["diff"], places=12)
                self.assertAlmostEqual(g["remain_diff"], gr["結果"]["rb"] - gr["結果"]["ra"], places=12)

    def test_measure_uses_every_seed_and_side_once(self):
        cn, cand = _set("chappy")
        fn, foe = _set("counter")
        rep = B.measure(cand, [(fn, foe)], seeds=[1, 2, 3], jobs_n=1, name=cn)
        self.assertEqual(rep["bo3"]["series"], 6)
        self.assertEqual({(r["seed"], r["side"]) for r in rep["records"]},
                         {(s, sd) for s in (1, 2, 3) for sd in ("A", "B")})
        self.assertEqual(rep["conditions"]["bo3_protocol"], "production_v1")
        self.assertEqual(rep["conditions"]["treasures"], "none")
        for r in rep["records"]:
            direct = B.play_series(cand, foe, r["seed"], r["side"])
            self.assertEqual(r["outcome"], direct["outcome"])
            self.assertEqual(r["games"], [g["outcome"] for g in direct["games"]])


class Deterministic(unittest.TestCase):
    """2. 同じシードなら同じ結果。"""

    def test_rerun_identical(self):
        cn, cand = _set("chappy")
        fn, foe = _set("counter")
        a = B.measure(cand, [(fn, foe)], seeds=[4, 5], jobs_n=1)
        b = B.measure(cand, [(fn, foe)], seeds=[4, 5], jobs_n=2)     # 並列でも同じ
        self.assertEqual(a["records"], b["records"])
        self.assertEqual(a["bo3"], b["bo3"])
        self.assertEqual(a["by_regulation"], b["by_regulation"])


class SideFlip(unittest.TestCase):
    """3. 左右反転後も候補側の勝敗として正しい。"""

    def test_measuring_from_the_other_side_swaps_w_and_l(self):
        cn, cand = _set("chappy")
        fn, foe = _set("counter")
        seeds = [0, 1, 2]
        mine = B.measure(cand, [(fn, foe)], seeds=seeds, jobs_n=1)
        theirs = B.measure(foe, [(cn, cand)], seeds=seeds, jobs_n=1)
        self.assertEqual(mine["bo3"]["wins"], theirs["bo3"]["losses"])
        self.assertEqual(mine["bo3"]["losses"], theirs["bo3"]["wins"])
        self.assertEqual(mine["bo3"]["draws"], theirs["bo3"]["draws"])
        self.assertAlmostEqual(mine["bo3"]["mean_match_diff"], -theirs["bo3"]["mean_match_diff"], places=6)
        # 候補 対 相手（種S・候補先手）は、相手 対 候補（種S・相手後手＝候補先手）と同じ盤
        m = {(r["seed"], r["side"]): r for r in mine["records"]}
        t = {(r["seed"], r["side"]): r for r in theirs["records"]}
        swap = {"W": "L", "L": "W", "D": "D"}
        for seed in seeds:
            self.assertEqual(m[(seed, "A")]["outcome"], swap[t[(seed, "B")]["outcome"]])
            self.assertEqual(m[(seed, "B")]["outcome"], swap[t[(seed, "A")]["outcome"]])
            self.assertEqual([swap[x] for x in t[(seed, "B")]["games"]], m[(seed, "A")]["games"])


class DrawsAreNotLosses(unittest.TestCase):
    """4. 引き分けは別に数え、勝率に混ぜない。"""

    def test_draw_counted_separately(self):
        cn, cand = _set("chappy")
        fn, foe = _set("counter")
        real = M.play

        def fake(a, b, dt=0.5, seed=None):
            r = real(a, b, dt=dt, seed=seed)
            r = copy.deepcopy(r)
            r["winner"] = "引き分け"
            r["wins_a"] = r["wins_b"] = 1.5
            for g in r["games"]:
                g["結果"]["score"] = 0.5
            return r
        M.play = fake
        try:
            rep = B.measure(cand, [(fn, foe)], seeds=[0, 1], jobs_n=1)
        finally:
            M.play = real
        b = rep["bo3"]
        self.assertEqual((b["wins"], b["losses"], b["draws"]), (0, 0, 4))
        self.assertEqual(b["win_rate"], 0.0)
        self.assertEqual(b["point_rate"], 0.5)
        for r in rep["by_regulation"]:
            self.assertEqual((r["wins"], r["losses"], r["draws"]), (0, 0, 4))
            self.assertEqual(r["win_rate"], 0.0)


class CompareReportsHonestly(unittest.TestCase):
    """5. 残存率差が改善しても勝率が下がる場合をそのまま示す。前後の条件が違えば拒む。"""

    def _fake(self, outcomes, remain, cond_over=None):
        recs = []
        for i, (o, gs) in enumerate(outcomes):
            recs.append({"opponent": "X", "seed": i // 2, "side": "AB"[i % 2], "outcome": o,
                         "match_diff": remain * 3, "games": list(gs), "remain_diffs": [remain] * 3})
        rep = {"candidate": "c", "spec": {"armies": []},
               "opponents": [{"name": "X", "spec": {"armies": ["same"]}}],
               "conditions": {"bo3_protocol": B.PROTOCOL, "dt": 0.5, "seeds": [0, 1], "sides": ["A", "B"],
                              "treasures": "none", "commit": "x"},
               "records": recs}
        if cond_over:
            rep["conditions"].update(cond_over)
        full = [{"opponent": r["opponent"], "seed": r["seed"], "side": r["side"], "outcome": r["outcome"],
                 "match_diff": r["match_diff"],
                 "games": [{"outcome": g, "diff": remain, "remain_diff": remain} for g in r["games"]]}
                for r in recs]
        rep.update(B.aggregate(full))
        return rep

    def test_disagreement_is_reported(self):
        before = self._fake([("W", "WWL"), ("W", "WLW"), ("L", "LLW"), ("W", "WWL")], remain=0.01)
        after = self._fake([("L", "LWL"), ("W", "WLW"), ("L", "LLW"), ("W", "WWL")], remain=0.05)
        cmp = B.compare(before, after)
        self.assertEqual(cmp["bo3"]["win_rate_delta_points"], -25.0)
        self.assertGreater(cmp["by_regulation"][0]["mean_remain_diff_delta"], 0.0)
        self.assertTrue(any("残存率差は改善" in n for n in cmp["notes"]))
        self.assertEqual(cmp["series_flips"]["W->L"], 1)
        self.assertEqual(cmp["series_flips"]["L->W"], 0)
        self.assertEqual(cmp["series_flips"]["same"], 3)

    def test_refuses_different_conditions(self):
        before = self._fake([("W", "WWL")] * 4, remain=0.0)
        after = self._fake([("W", "WWL")] * 4, remain=0.0, cond_over={"dt": 0.25})
        with self.assertRaises(ValueError):
            B.compare(before, after)


class NoSurplusAccumulation(unittest.TestCase):
    """6. 同じ軍を繰り返し測っても余剰コストの補正が累積しない。"""

    def test_gauge_init_untouched_and_results_stable(self):
        import dataclasses
        cn, base = _set("chappy")
        fn, foe = _set("counter")
        # 余剰コストのある登録を作る（1枚のコストを1点下げる＝余り1点→初期ゲージ +1%）
        u0 = base.units[0]
        cheap = dataclasses.replace(u0.cards[0], cost=u0.cards[0].cost - 1.0)
        cand = M.Entry((dataclasses.replace(u0, cards=(cheap,) + tuple(u0.cards[1:])),) + tuple(base.units[1:]),
                       name="surplus")
        self.assertEqual(M.validate(cand), [])
        self.assertTrue(any(M.surplus_ratio(a, cap) > 0.0
                            for a, (_l, cap) in zip(cand.units, M.REGULATIONS)),
                        "余剰コストのある登録で試す")
        before = [[c.gauge_init for c in a.cards] for a in cand.units]
        runs = [B.measure(cand, [(fn, foe)], seeds=[3], jobs_n=1)["records"] for _ in range(3)]
        after = [[c.gauge_init for c in a.cards] for a in cand.units]
        self.assertEqual(before, after)
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])


class Legality(unittest.TestCase):
    """7. 本番の検証を通らない登録は測らない。"""

    def test_duplicate_person_is_refused(self):
        cn, cand = _set("chappy")
        fn, foe = _set("counter")
        u0 = cand.units[0]
        dup = M.Entry((u0, u0, cand.units[2]), name="dup")       # 同じ隊を2戦場に＝人物重複
        self.assertTrue(M.validate(dup))
        with self.assertRaises(ValueError):
            B.measure(dup, [(fn, foe)], seeds=[0], jobs_n=1)


if __name__ == "__main__":
    unittest.main(verbosity=1)
