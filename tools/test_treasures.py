# -*- coding: utf-8 -*-
"""宝物（§7.138）の受け入れ試験。

前半（unittest）はエンジン: 恒久項が _recalc_mods を生き延びるか・勢力の宝の
条件・打消し貫通・札モッド・誘発4種・杜康の台詞・陣容の往復。
後半（HTTP）は日替わり3択・1人1個・装備制限・1武将1個・予算超過。
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-treasure-"), "players.db")
PORT = 8989
os.environ.update({"SANGOKU_DB": DB, "SANGOKU_PORT": str(PORT),
                   "SANGOKU_HOST": "127.0.0.1",
                   "no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})
os.environ.pop("SANGOKU_PUBLIC", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dataclasses                     # noqa: E402
from sim import field as F             # noqa: E402
from sim import match as M             # noqa: E402
from sim import play as PL             # noqa: E402
from sim import rosterdata as R        # noqa: E402

if not F.TRAITS:
    R.load_traits_into_field()
if not F.SKILL_INFO:
    R.load_skills_into_field()


def _synth_with(trait="", **kw):
    c = F._synth(4.0, F.INF)
    if trait or kw:
        c = dataclasses.replace(c, trait=trait, **kw)
    return c


def _army(cards):
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _filler(n):
    return [F._synth(4.0, F.INF) for _ in range(n)]


class TreasureEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._traits_on = F.TRAITS_ON
        F.TRAITS_ON = True

    @classmethod
    def tearDownClass(cls):
        F.TRAITS_ON = cls._traits_on

    # 1〜2. 恒久項が _recalc_mods の代入上書きを生き延びる
    def test_perm_scut_survives_recalc(self):
        u = F.build(_army([_synth_with("t_motoku")] + _filler(5)), 1)[0]
        self.assertAlmostEqual(u.scut_mult, 1.0 - F.TREASURE_MOTOKU_SCUT)
        F._fx_add(u, (10.0, "scut", 0.2, "試験"))
        F._expire([u], 0.0)     # 時限と恒久が同居
        self.assertAlmostEqual(u.scut_mult, 1.0 - 0.2 - F.TREASURE_MOTOKU_SCUT)
        F._expire([u], 20.0)    # 時限が切れても恒久は残る
        self.assertAlmostEqual(u.scut_mult, 1.0 - F.TREASURE_MOTOKU_SCUT)

    def test_perm_atk_survives_recalc(self):
        u = F.build(_army([_synth_with("t_rendo")] + _filler(5)), 1)[0]
        self.assertAlmostEqual(u.atk_mult, 1.0 + F.TREASURE_RENDO_ATK)
        F._fx_add(u, (10.0, "atk", 0.1, "試験"))
        F._expire([u], 0.0)
        self.assertAlmostEqual(u.atk_mult, 1.0 + 0.1 + F.TREASURE_RENDO_ATK)
        F._expire([u], 20.0)
        self.assertAlmostEqual(u.atk_mult, 1.0 + F.TREASURE_RENDO_ATK)

    # 3. 木牛流馬は後衛だけ
    def test_mokgyu_rear_only(self):
        card = _synth_with("t_mokgyu")
        rear = F.build(_army(_filler(3) + [card] + _filler(2)), 1)[3]
        front = F.build(_army([card] + _filler(5)), 1)[0]
        self.assertAlmostEqual(rear.def_mult, 1.0 + F.TREASURE_MOKGYU_DEF)
        self.assertAlmostEqual(front.def_mult, 1.0)

    # 4. 赤兎馬: 兵力+2%（盤面）＋速度寄せ+0.3（札モッド）
    def test_sekitoba_men_and_speed(self):
        base = F.build(_army([_synth_with()] + _filler(5)), 1)[0]
        got = F.build(_army([_synth_with("t_sekitoba")] + _filler(5)), 1)[0]
        self.assertAlmostEqual(got.men0, base.men0 * (1.0 + F.TREASURE_SEKITOBA_MEN))
        c = PL.apply_treasure_card_mods(_synth_with("t_sekitoba"))
        self.assertAlmostEqual(c.spd_lean, 0.3)

    # 5〜6. 勢力の宝: 3人で立ち、2人では立たない
    def _shu3(self, holder_key=""):
        shu = [c for c in M._roster_cards() if c.faction == "蜀"][:3]
        if holder_key:
            shu[0] = dataclasses.replace(shu[0], trait=F.TRAIT_SEP.join(
                list(F.trait_keys(shu[0].trait)) + [holder_key]))
        return F.build(_army(shu + _filler(3)), 1)

    def test_faction_treasure_needs_three(self):
        us = self._shu3("t_shokkin")
        base = self._shu3("")
        F._apply_faction_treasures(us)
        F._apply_faction_treasures(base)
        # 持ち主が居れば全軍の兵力が伸びる（埋め草の合成カードも含めて）
        self.assertAlmostEqual(us[5].men0, base[5].men0 * 1.02)
        # 2人しか居なければ立たない
        shu2 = [c for c in M._roster_cards() if c.faction == "蜀"][:2]
        shu2[0] = dataclasses.replace(shu2[0], trait=F.TRAIT_SEP.join(
            list(F.trait_keys(shu2[0].trait)) + ["t_shokkin"]))
        us2 = F.build(_army(shu2 + _filler(4)), 1)
        m0 = us2[5].men0
        F._apply_faction_treasures(us2)
        self.assertAlmostEqual(us2[5].men0, m0)

    def test_gyokuji_rate_for_gunyu(self):
        gy = [c for c in M._roster_cards() if c.faction == "群雄"][:3]
        gy[0] = dataclasses.replace(gy[0], trait=F.TRAIT_SEP.join(
            list(F.trait_keys(gy[0].trait)) + ["t_gyokuji"]))
        us = F.build(_army(gy + _filler(3)), 1)
        F._apply_faction_treasures(us)
        for u in us:
            self.assertAlmostEqual(u.rate_mult, 1.08)

    # 7. 七星宝刀: 構えを素通しする（帳簿にも実況にも残さない）
    def test_shichisei_pierces_nullify(self):
        sk = F.Skill(power=3.0)
        for pierce in (False, True):
            atk = _synth_with("t_shichisei" if pierce else "")
            ua = F.build(_army([atk] + _filler(5)), 1)
            ub = F.build(_army(_filler(6)), -1)
            for f in ub:            # 誰が的に選ばれても構えている状態にする
                f.nullify = True
            tgt = F._skill_targets("敵1体（正面）", ua[0], ub, ua)[0]
            before = tgt.men
            F._apply_skill(ua[0], sk, "敵1体（正面）", ua, ub, 0.0,
                           src="試験砲", name="試験砲", kind_jp="兵法")
            blocked = sum(x.null_blocked for x in ub)
            if pierce:
                self.assertLess(tgt.men, before)            # 通った
                self.assertEqual(blocked, 0)                # 帳簿も無言
            else:
                self.assertAlmostEqual(tgt.men, before)     # 霧散した
                self.assertEqual(blocked, 1)

    # 8. 青龍偃月刀・白羽扇: 実カードの武力/知力が上がり、狙い撃ちの的は動かない
    def test_seiryu_hakuusen_stats(self):
        roster = {c.name: c for c in M._roster_cards()}
        base_c = roster["貂蝉〔傾国〕"]
        for key, field_name in (("t_seiryu", "might"), ("t_hakuusen", "wits")):
            c = dataclasses.replace(base_c, trait=key)
            c = PL.apply_treasure_card_mods(c)
            self.assertAlmostEqual(getattr(c, field_name),
                                   getattr(base_c, field_name) + 15.0)
            self.assertAlmostEqual(c.fame_wits, base_c.fame_wits)  # 的は不変
        # 合成カード（武力0の指定なし運用）には足さない
        s = PL.apply_treasure_card_mods(_synth_with("t_seiryu"))
        self.assertEqual(s.might, 0.0)

    # 9. 誘発4種が TRAITS に載り、宝物由来なら「秘策」で語られる
    def test_triggered_treasures_loaded_and_redacted(self):
        for k in ("t_teki", "t_kinno", "t_gyokutai", "t_seino"):
            self.assertIn(k, F.TRAITS)
        cond, target, cap, sk, jp = F.TRAITS["t_teki"]
        for hidden in (False, True):
            card = _synth_with("t_teki",
                               hidden_trait="t_teki" if hidden else "")
            ua = F.build(_army([card] + _filler(5)), 1)
            ub = F.build(_army(_filler(6)), -1)
            events = []
            F._apply_skill(ua[0], sk, target, ua, ub, 0.0, src="t_teki",
                           ev=events, seen=set(), name=jp, kind_jp="誘発")
            text = events[0].text
            if hidden:
                self.assertNotIn("的盧", text)
                self.assertIn("秘策", text)
            else:
                self.assertIn("的盧", text)

    # 10. 玉帯詔: 気勢(rate)の時限モッドの初消費者 — 効いて、切れる
    def test_gyokutai_rate_mod_first_consumer(self):
        cond, target, cap, sk, jp = F.TRAITS["t_gyokutai"]
        ua = F.build(_army([_synth_with("t_gyokutai")] + _filler(5)), 1)
        ub = F.build(_army(_filler(6)), -1)
        F._apply_skill(ua[0], sk, target, ua, ub, 0.0, src="t_gyokutai",
                       kind_jp="誘発")
        for u in ua:
            self.assertAlmostEqual(u.rate_mult, 1.15)
        F._expire(ua, 60.0)     # 窓（30秒）が閉じたら戻る
        for u in ua:
            self.assertAlmostEqual(u.rate_mult, 1.0)

    # 11. 青嚢書: 最大兵力の6%を一度だけ回復
    #     **CSV の数字がそのまま盤面に出る**（§7.152 の裁定）。固有特性・宝物は
    #     兵法と同じ器を通るが、**予算の縮尺（SKILL_DUR/BURST_SCALE）は掛けない** —
    #     縮尺の理屈は「ゲージの供給が増えたぶんの補償」で、誘発型はゲージで
    #     撃たないから当てはまらない。ここは縮尺が漏れていないことの見張りでもある
    #     （§7.151 では漏れていて 9% になっていた）。
    def test_seino_heal(self):
        cond, target, cap, sk, jp = F.TRAITS["t_seino"]
        self.assertFalse(sk.scaled, "誘発型に予算の縮尺は掛からない")
        ua = F.build(_army([_synth_with("t_seino")] + _filler(5)), 1)
        ub = F.build(_army(_filler(6)), -1)
        u = ua[0]
        u.men = u.men0 * 0.3    # 自分が最少になるよう削っておく
        F._apply_skill(u, sk, target, ua, ub, 0.0, src="t_seino",
                       kind_jp="誘発")
        self.assertAlmostEqual(u.men, u.men0 * 0.36, delta=u.men0 * 1e-6)

    # 12. 杜康の酒: 決めゼリフが開幕から必ず・一度だけ出る
    def test_toko_quote_guaranteed(self):
        card = dataclasses.replace(
            F._synth(4.0, F.INF), name="杜康持ち", trait="t_toko",
            quote="何を以て憂いを解かん")
        a = _army([card] + _filler(5))
        lines = F.narrate(a, F.flat_army(cost=4.0, typ=F.INF), dt=0.25, seed=1)
        text = "\n".join(lines)
        self.assertEqual(text.count("何を以て憂いを解かん"), 1)
        # 布陣の直後（開幕）に出ている
        i_open = next(i for i, l in enumerate(lines) if "の陣を布く" in l)
        i_quote = next(i for i, l in enumerate(lines) if "何を以て憂いを解かん" in l)
        self.assertEqual(i_quote, i_open + 1)

    # 13. 陣容の往復: 札モッド（武力・寄せ）がリプレイでも再現される
    def test_snapshot_round_trip_reproduces_card_mods(self):
        roster = {c.name: c for c in M._roster_cards()}
        base = roster["貂蝉〔傾国〕"]
        c = dataclasses.replace(base, trait="t_seiryu、t_gentetsu",
                                hidden_trait="t_seiryu、t_gentetsu")
        c = PL.apply_treasure_card_mods(c)
        army = _army([c] + [roster["呂布〔飛将〕"]] + list(_filler(0)))
        snap = PL.snap_army(army)
        rebuilt = PL.army_from_snap(list(roster.values()), snap)
        rc = rebuilt.cards[0]
        self.assertAlmostEqual(rc.might, c.might)
        self.assertAlmostEqual(rc.wits, c.wits)
        self.assertAlmostEqual(rc.def_lean, c.def_lean)
        self.assertEqual(rc.trait, c.trait)
        self.assertEqual(rc.hidden_trait, c.hidden_trait)

    # 14. 対称性: 同じ宝物入りの同編成どうしは引き分け（零点）
    def test_symmetric_with_treasures(self):
        shu = [c for c in M._roster_cards() if c.faction == "蜀"][:3]
        shu[0] = dataclasses.replace(shu[0], trait=F.TRAIT_SEP.join(
            list(F.trait_keys(shu[0].trait)) + ["t_shokkin"]))
        a = _army(shu + [_synth_with("t_motoku"), _synth_with("t_rendo"),
                         _synth_with()])
        r = F.simulate(a, a, dt=0.5, seed=None)
        self.assertAlmostEqual(r["diff"], 0.0, places=12)


# ── HTTP（test_character_versions.py と同じハーネス）──────────

FAIL = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  NG  ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        FAIL.append(name)


def req(method, path, cookie="", body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=180)
    h = {"Cookie": cookie} if cookie else {}
    payload = json.dumps(body) if body is not None else None
    if payload:
        h["Content-Type"] = "application/json"
    c.request(method, path, payload, h)
    r = c.getresponse()
    return r, r.read()


def http_checks() -> bool:
    from http.server import ThreadingHTTPServer
    from sim import web as W
    app = ThreadingHTTPServer(("127.0.0.1", PORT), W.App)
    threading.Thread(target=app.serve_forever, daemon=True).start()

    r, d = req("POST", "/api/login", body={"new": "宝物検査"})
    sid = (r.getheader("Set-Cookie") or "").split(";")[0]
    req("POST", "/api/dev_senki", cookie=sid, body={})   # 全登用（試験用）

    print("[15] 日替わり3択")
    st = json.loads(req("GET", "/api/state", cookie=sid)[1])
    ch = (st.get("treasure") or {}).get("choices") or []
    check("choices が3帯そろって出る", len(ch) == 3
          and {c["tier"] for c in ch} == {"小", "中", "大"}, str(ch))
    st2 = json.loads(req("GET", "/api/state", cookie=sid)[1])
    ch2 = (st2.get("treasure") or {}).get("choices") or []
    check("読み直しても同じ候補（決定的）",
          [c["key"] for c in ch] == [c["key"] for c in ch2])
    check("候補はすべて t_ キー", all(c["key"].startswith("t_") for c in ch))

    print("[16] 選んで授かる → 同日2回目は拒否")
    pick = ch[0]["key"]
    r, d = req("POST", "/api/treasure_pick", cookie=sid, body={"key": pick})
    check("1回目が通る", json.loads(d).get("ok"), d)
    r, d = req("POST", "/api/treasure_pick", cookie=sid, body={"key": ch[1]["key"]})
    check("同日2回目は拒否", not json.loads(d).get("ok"), d)
    D = json.loads(req("GET", "/api/deckdata", cookie=sid)[1])
    check("deckdata の treasures に載る",
          any(t["key"] == pick for t in D["treasures"]), str(D["treasures"]))
    check("treasure_budgets が3戦場ぶん出る",
          set(D["treasure_budgets"]) == {"汜水関", "官渡", "赤壁"})

    print("[17] dev door 全獲得 → 日替わりは店じまい")
    req("POST", "/api/dev_treasure", cookie=sid, body={})
    D = json.loads(req("GET", "/api/deckdata", cookie=sid)[1])
    check("18種すべて所持", len(D["treasures"]) == 18, len(D["treasures"]))
    st3 = json.loads(req("GET", "/api/state", cookie=sid)[1])
    check("choices が消える（全所持）", not st3.get("treasure"))

    print("[18] 装備の検証")
    # 歩兵（曹仁〔堅守〕）へ赤兎馬（騎兵のみ）→ 拒否
    r, d = req("POST", "/api/treasure", cookie=sid,
               body={"key": "t_sekitoba", "general": "曹仁〔堅守〕"})
    j = json.loads(d)
    check("騎兵のみの宝物は歩兵に拒否", not j.get("ok") and
          "騎兵" in (j.get("errors") or [""])[0], d)
    r, d = req("POST", "/api/treasure", cookie=sid,
               body={"key": "t_seiryu", "general": "曹仁〔堅守〕"})
    check("通常の装備が通る", json.loads(d).get("ok"), d)
    r, d = req("POST", "/api/treasure", cookie=sid,
               body={"key": "t_hakuusen", "general": "曹仁〔堅守〕"})
    j = json.loads(d)
    check("同じ武将へ2個目は拒否", not j.get("ok") and
          "1武将に宝物は1つ" in (j.get("errors") or [""])[0], d)
    r, d = req("POST", "/api/treasure", cookie=sid,
               body={"key": "t_nonexistent", "general": "曹仁〔堅守〕"})
    check("未所持キーは拒否", not json.loads(d).get("ok"))
    r, d = req("POST", "/api/treasure", cookie=sid,
               body={"key": "t_seiryu", "general": ""})
    check("外すのが通る", json.loads(d).get("ok"), d)
    D = json.loads(req("GET", "/api/deckdata", cookie=sid)[1])
    row = next(t for t in D["treasures"] if t["key"] == "t_seiryu")
    check("外れて未装備に戻る", row["general"] == "")

    print("[19] 軍功予算の超過が entry_errors に出る")
    # 官渡へデッキを組み、CSV の高額順（装備制限なしの宝物だけ）に
    # 予算を超えるまで積む。値段は実測で変わるので決め打ちにしない —
    # 6個積んでも超えられない値付けになったら、ここで声を出して落ちる
    # （そのときはこの試験の前提ごと考え直すこと）。
    used = set()
    front, rear = [], []
    for c in sorted((x for x in D["roster"] if x["typ"] != "弓兵"),
                    key=lambda c: c["cost"]):
        if c["person"] not in used and len(front) < 3:
            front.append(c["name"]); used.add(c["person"])
    for c in sorted((x for x in D["roster"] if x["typ"] == "弓兵"),
                    key=lambda c: c["cost"]):
        if c["person"] not in used and len(rear) < 3:
            rear.append(c["name"]); used.add(c["person"])
    deck = front + rear
    r, d = req("POST", "/api/deck", cookie=sid,
               body={"reg": "官渡", "form": "魚鱗", "cards": deck})
    check("（準備）官渡へデッキ登録", json.loads(d).get("ok"), d)
    budget = PL.treasure_budget_kou(30.0)          # 官渡
    rich = sorted(((PL.treasure_kou(k), k)
                   for k, row in PL.treasure_rows().items()
                   if not row["装備制限"]), reverse=True)
    picked, total = [], 0
    for kou, k in rich:
        if total > budget or len(picked) >= len(deck):
            break
        picked.append(k); total += kou
    check("（前提）積める宝物で予算を超えられる", total > budget,
          "上位{}個でも {}功 ≦ 予算{}功".format(len(picked), total, budget))
    for key, gen in zip(picked, deck):
        r, d = req("POST", "/api/treasure", cookie=sid,
                   body={"key": key, "general": gen})
        check("（準備）{} を装備".format(key), json.loads(d).get("ok"), d)
    D = json.loads(req("GET", "/api/deckdata", cookie=sid)[1])
    check("予算超過が entry_errors に出る",
          any("軍功" in e and "超えている" in e for e in D["entry_errors"]),
          str(D["entry_errors"]))

    app.shutdown()
    return not FAIL


if __name__ == "__main__":
    res = unittest.main(module=sys.modules[__name__], argv=[sys.argv[0]],
                        exit=False, verbosity=1).result
    ok_http = http_checks()
    print()
    if res.wasSuccessful() and ok_http:
        print("全部通った")
        sys.exit(0)
    bad = []
    if not res.wasSuccessful():
        bad.append("エンジン {}件".format(len(res.failures) + len(res.errors)))
    if FAIL:
        bad.append("HTTP " + str(FAIL))
    print("失敗あり: " + "・".join(bad))
    sys.exit(1)
