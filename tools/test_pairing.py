# -*- coding: utf-8 -*-
"""天下の組み合わせ — 再戦回避が実際に働くかの試験（§7.240）。戦闘は回さない。

`ladder.plan_round` は `Board.recent`（直近に当たった相手）を見て
REMATCH_GAP 人を避ける。ところが **ratings 表はレートと対局数しか持たない**
ので、読み込んだ順位表の recent は空のままだった。毎時の天下では在野の
対局数が多くて K が小さく、レートがほとんど動かない —— 組み方は決定的なので、
首位の人は同じ2位と延々と当たり続けた（テストプレイの報告「天下同じ相手と
あたりまくる」・実測で24開催の相手が2人）。

ここは「対戦記録から recent を組み直す口」と「それを渡せば相手が散る」を突く。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SANGOKU_DB",
                      os.path.join(tempfile.mkdtemp(prefix="sangoku-pair-"),
                                   "players.db"))

from sim import ladder as L        # noqa: E402
from sim import players as P       # noqa: E402


def _cx():
    db = os.path.join(tempfile.mkdtemp(prefix="sangoku-pair-"), "players.db")
    return P.connect(db)


def _board(n, human_rating=1700.0, dummy_games=300):
    """遊び込んだ順位表。**在野は対局数が多く K が小さい＝レートが動かない。**"""
    b = L.Board("天下", None)
    for i in range(n):
        pid = "d{:02d}".format(i)
        b.rating[pid] = 1500.0 - i * 12.0
        b.games[pid] = dummy_games
    b.rating["me"] = human_rating
    b.games["me"] = 0
    return b


class RecentFromBattles(unittest.TestCase):
    def test_reads_both_sides_newest_last(self):
        cx = _cx()
        for a, y in (("me", "d00"), ("me", "d01"), ("d02", "me")):
            P.record_battle(cx, "tenka", "天下", a, y, 1, "", "", "2026-09", 0)
        got = P.recent_opponents(cx, "天下", 3)
        self.assertEqual(got["me"], ["d00", "d01", "d02"])    # 古い順
        self.assertEqual(got["d02"], ["me"])                  # 相手側も入る

    def test_keeps_only_the_last_per(self):
        cx = _cx()
        for i in range(6):
            P.record_battle(cx, "tenka", "天下", "me", "d{:02d}".format(i),
                            1, "", "", "2026-09", 0)
        self.assertEqual(P.recent_opponents(cx, "天下", 2)["me"], ["d04", "d05"])

    def test_ignores_council_and_other_boards(self):
        cx = _cx()
        P.record_battle(cx, "tenka", "天下", "me", "d00", 1, "", "", "2026-09", 0)
        P.record_battle(cx, "council", "天下", "me", "council:1", 1, "", "", "2026-09", 0)
        P.record_battle(cx, "free", "天下", "me", "d09", 1, "", "", "2026-09", 0)
        P.record_battle(cx, "ranked", "官渡", "me", "d08", 1, "", "", "2026-09", 0)
        self.assertEqual(P.recent_opponents(cx, "天下", 3)["me"], ["d00"])


class PlanRound(unittest.TestCase):
    def test_frozen_ladder_repeats_without_recent(self):
        """recent が空だと、動かない順位表では毎回同じ組になる（不具合の再現）。"""
        b = _board(36)
        first = dict(L.plan_round(b, list(b.rating), 1))
        again = dict(L.plan_round(b, list(b.rating), 2))
        self.assertEqual(first, again)

    def test_recent_forces_a_different_foe(self):
        """直近の相手を渡すと、同じ順位表でも別の相手になる。"""
        b = _board(36)
        foe = dict(L.plan_round(b, list(b.rating), 1))["me"]
        b.recent = {"me": [foe], foe: ["me"]}
        self.assertNotEqual(dict(L.plan_round(b, list(b.rating), 2))["me"], foe)

    def test_rotation_covers_gap_plus_one(self):
        """窓を通して回すと、少なくとも REMATCH_GAP+1 人と当たる。"""
        b = _board(36)
        recent = {}
        seen = []
        for r in range(L.REMATCH_GAP + 1):
            b.recent = {k: list(v) for k, v in recent.items()}
            foe = dict(L.plan_round(b, list(b.rating), r))["me"]
            seen.append(foe)
            recent.setdefault("me", []).append(foe)
            recent.setdefault(foe, []).append("me")
        self.assertEqual(len(set(seen)), L.REMATCH_GAP + 1, seen)

    def test_falls_back_when_everyone_is_recent(self):
        """全員が直近なら回避を諦める（組めないまま落ちない）。"""
        b = _board(3)
        others = [p for p in b.rating if p != "me"]
        b.recent = {"me": others * 2}
        pairs = dict(L.plan_round(b, list(b.rating), 1))
        self.assertIn("me", pairs)


class LiveWiring(unittest.TestCase):
    """**天下の本番経路が recent を入れているか。** ここが試験の要 —
    `plan_round` 側がいくら正しくても、組を作る直前に渡し忘れたら直っていない。
    組み方だけを見たいので、`plan_round` を差し替えて戦闘は1本も回さない。
    """

    def test_tenka_resolve_fills_recent(self):
        import datetime
        from sim import match as M, play as PL, dummies as D, field as F

        db = os.path.join(tempfile.mkdtemp(prefix="sangoku-live-"), "players.db")
        os.environ["SANGOKU_DB"] = db
        cx = P.connect(db)
        me = P.register(cx, "たね")
        cards = M._roster_cards()
        forms = {F.FORM_WIDE: "鶴翼", F.FORM_STANDARD: "魚鱗", F.FORM_DEEP: "雁行"}
        for i, (reg, _cap) in enumerate(M.REGULATIONS):
            army = D.make_entry(cards, D.PERSONAS[0], D.deck_seed(0, 1)).units[i]
            P.set_deck(cx, me.id, reg, "、".join(c.name for c in army.cards),
                       forms[army.form])
        P.record_battle(cx, "tenka", "天下", me.id, "somebody",
                        1, "", "", "2026-09", 0)
        seen = {}
        orig = PL.L.plan_round

        def spy(board, pids, rnd):
            seen["recent"] = dict(board.recent)
            return []                      # 組まない＝戦闘を回さない

        PL.L.plan_round = spy
        try:
            at = int(datetime.datetime(2026, 9, 12, 9, 0).timestamp())
            PL._tenka_resolve(cx, cards, 1, at)
        finally:
            PL.L.plan_round = orig
        self.assertIn("recent", seen, "plan_round が呼ばれていない")
        self.assertEqual(seen["recent"].get(me.id), ["somebody"],
                         "組を作る直前に直近の相手を渡していない")


if __name__ == "__main__":
    unittest.main(verbosity=2)
