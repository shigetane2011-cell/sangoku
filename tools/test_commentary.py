# -*- coding: utf-8 -*-
"""§7.173 実況・合戦詳録の作り替えの受け入れ試験。

レビューが「取りこぼしやすい」と挙げた6つの場面を狙って確かめる:
  1. 2回目以降の兵法で決着する（決め手が実況から消えない）
  2. 全体兵法を、対象の1隊が持つ構えで打ち消す（範囲と残りが分かる）
  3. 両軍の兵法と壊滅が同時刻に起こる（発動 → 壊滅 → 決着 の順・1出来事1行）
  4. 同士討ちが300人未満（少量でも記録に残り、加害側・被害側が分かる）
  5. 継続回復の途中で戦闘が終了する（予定と実量を分ける）
  6. 両軍に同名武将・同名兵法がある（軍名で区別・行ごとに主体）
あわせて文言（兵法への備え・混乱した・弱体の言い分け・複合兵法と代償）を見る。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import rosterdata as R        # noqa: E402
from sim import play as PL             # noqa: E402

if not F.SKILL_INFO:
    R.load_skills_into_field()
R.load_traits_into_field()


def _army(cards, form=F.FORM_STANDARD):
    return F.Army(tuple(cards), form)


def _filler(n, typ=F.INF, cost=4.0):
    return [F._synth(cost, typ) for _ in range(n)]


def _card(base, **kw):
    return F.Card(**{**base.__dict__, **kw})


def _register(name, effect, target):
    F.SKILL_INFO[name] = F._parse_skill(effect, target)
    F.SKILL_TARGET[name] = target


def _unregister(name):
    F.SKILL_INFO.pop(name, None)
    F.SKILL_TARGET.pop(name, None)


class _Recording(unittest.TestCase):
    """発動の記録を直に開いて、盤面の器（_apply_skill）を単体で叩く土台。"""

    def setUp(self):
        F._CASTS, F._CAST_BY_ID = [], {}
        F._JP["A"], F._JP["B"] = "曹", "孫"
        F._DUP_NAMES = set()

    def tearDown(self):
        F._CASTS, F._CAST_BY_ID = None, {}

    def _units(self):
        ua = F.build(_army(_filler(6)), 1)
        ub = F.build(_army(_filler(6)), -1)
        return ua, ub


class Scene2_WholeArmySkillNullified(_Recording):
    """全体兵法を、対象の1隊が持つ構えで打ち消す。"""

    def test_scope_and_remaining_are_told(self):
        ua, ub = self._units()
        guard = F._parse_skill("兵法打消し 2発（30秒）", "自分")
        F._apply_skill(ub[0], guard, "自分", ub, ua, 0.0, src="構え試験",
                       name="構え試験", kind_jp="兵法")
        F._recalc_mods(ub[0])
        self.assertTrue(ub[0].nullify)
        atk = F._parse_skill("ダメージ 威力300%", "敵全体")
        ev = []
        F._apply_skill(ua[0], atk, "敵全体", ua, ub, 1.0, src="斉射試験",
                       name="斉射試験", kind_jp="兵法", ev=ev, seen=set())
        rec = F._CASTS[-1]
        self.assertTrue(rec["nullified"])
        self.assertEqual(rec["blocker"], F._who(ub[0]))
        # 構えを与えた武将と兵法、残り回数が記録に残る
        self.assertEqual(rec["stance_by"], F._who(ub[0]))
        self.assertEqual(rec["stance_skill"], "構え試験")
        self.assertEqual(rec["left"], 1)
        self.assertEqual(rec["intended"], [F._who(u) for u in ub])
        self.assertEqual(rec["hit"], [])
        # **その1発全体**が霧散する — 構えを持たない5隊にも損害は無い
        self.assertTrue(all(u.men == u.men0 for u in ub))
        text = ev[-1].text
        self.assertIn("兵法全体を打ち消した", text)
        self.assertIn("対象の6隊への効果は発生しなかった", text)
        self.assertIn("構え試験", text)
        self.assertIn("残り: 1発", text)
        self.assertTrue(ev[-1].must, "打ち消された発動は枠に関係なく残す")
        # 2発目で尽き、3発目は通る
        F._apply_skill(ua[1], atk, "敵全体", ua, ub, 2.0, src="斉射試験",
                       name="斉射試験", kind_jp="兵法", ev=ev, seen=set())
        self.assertIn("尽きた", ev[-1].text)
        self.assertEqual(F._CASTS[-1]["left"], 0)
        F._apply_skill(ua[2], atk, "敵全体", ua, ub, 3.0, src="斉射試験",
                       name="斉射試験", kind_jp="兵法", ev=ev, seen=set())
        self.assertFalse(F._CASTS[-1]["nullified"])
        self.assertGreater(F._CASTS[-1]["damage"], 0.0)
        self.assertTrue(any(u.men < u.men0 for u in ub))


class Scene5_HealOverTimeInterrupted(_Recording):
    """継続回復の途中で戦闘が終わる: 実況は予定を語り、実量は記録に残る。"""

    def test_plan_and_actual_are_separate(self):
        ua, ub = self._units()
        for u in ua:
            u.men = u.men0 * 0.5
        sk = F._parse_skill("継続回復 攻撃力の300%（20秒）", "味方全体")
        self.assertGreater(sk.dur, 0.0)
        ev = []
        F._apply_skill(ua[0], sk, "味方全体", ua, ub, 0.0, src="癒試験",
                       name="癒試験", kind_jp="兵法", ev=ev, seen=set())
        rec = F._CASTS[-1]
        self.assertIsNotNone(rec["hot"])
        planned = rec["hot"]["planned"]
        self.assertGreater(planned, 0.0)
        self.assertEqual(rec["hot_actual"], 0.0)
        text = ev[-1].text
        self.assertIn("継続回復", text)
        self.assertNotIn("戦列に復帰", text, "予定総量を復帰した兵として語らない")
        # 窓（20秒）の 1/4 だけ進めて戦闘が終わったとする
        t = 0.0
        for _ in range(20):
            F._overtime(ua, t, 0.25)
            t += 0.25
        self.assertGreater(rec["hot_actual"], 0.0)
        self.assertLess(rec["hot_actual"], planned * 0.5)


class Scene4_FriendlyFire(_Recording):
    """同士討ち: 少量でも記録し、加害側・被害側が分かる。実況は一定量から。"""

    def test_small_amount_recorded_with_victims(self):
        ua, ub = self._units()
        acc = [0.0] * len(ua)
        F._friendly_fire(ua[0], ua, acc, 100.0)
        u = ua[0]
        self.assertGreater(u.ff_dealt, 0.0)
        self.assertLess(u.ff_dealt, F.FF_SHOW)
        self.assertAlmostEqual(sum(u.ff_pair.values()), u.ff_dealt, places=6)
        self.assertTrue(all(x.ff_taken > 0.0 for x in ua[1:]))
        self.assertEqual(ua[0].ff_taken, 0.0)
        # 少量では実況の行にならない
        ev, seen = [], set()
        gap = [[10.0] * len(ub) for _ in ua]
        F._log_tick(ev, seen, 10.0, ua, ub, gap)
        self.assertFalse(any(e.kind == "同士討ち" for e in ev))
        # 一定量を超えたら、加害の隊といちばん受けた味方を名指しして1行
        F._friendly_fire(ua[0], ua, acc, 20000.0)
        F._log_tick(ev, seen, 12.0, ua, ub, gap)
        ff = [e for e in ev if e.kind == "同士討ち"]
        self.assertEqual(len(ff), 1)
        victim, amt = max(u.ff_pair.items(), key=lambda kv: kv[1])
        self.assertIn(F._who(u), ff[0].text)
        self.assertIn(victim, ff[0].text)
        self.assertIn("同士討ち", ff[0].text)
        self.assertEqual(ff[0].side, "A")
        # 同じ隊の行は1戦に1本
        F._log_tick(ev, seen, 14.0, ua, ub, gap)
        self.assertEqual(sum(1 for e in ev if e.kind == "同士討ち"), 1)

    def test_detail_rows_carry_friendly_fire_breakdown(self):
        _register("＿混乱試験", "混乱 60%（40秒）", "敵全体")
        try:
            c = _card(F._synth(8.0, F.INF), skill="＿混乱試験", gauge_cost=60.0)
            a = _army([c] + _filler(5))
            b = _army(_filler(6))
            rep = PL.replay_data(a, b, 0.25, 5, True)
        finally:
            _unregister("＿混乱試験")
        rows = rep["mine"] + rep["foe"]
        for u in rows:
            self.assertIn("ff_pair", u)
            self.assertIn("ff_taken", u)
            self.assertIn("pos", u)
            self.assertIn("card", u)
        hurt = [u for u in rep["foe"] if u["ff"] > 0]
        self.assertTrue(hurt, "混乱させた側の同士討ちが記録される")
        for u in hurt:
            self.assertTrue(u["ff_pair"], "誰に何人かの内訳が付く")
            self.assertAlmostEqual(sum(v for _, v in u["ff_pair"]), u["ff"],
                                   delta=len(u["ff_pair"]) + 1)
        self.assertGreater(sum(u["ff_taken"] for u in rep["foe"]), 0)


class Scene3_SameSecondOrdering(unittest.TestCase):
    """両軍の兵法と壊滅が同時刻: 発動 → 壊滅 → 決着 の順で、1出来事1行。"""

    def test_cast_then_wipe_then_end_each_on_own_line(self):
        F._JP["A"], F._JP["B"] = "曹", "孫"
        t = 50.0
        opening = F.Event(-1.0, "布陣", F.LINE_PRIO["布陣"], "両軍、布陣。")
        e_end = F.Event(t, "決着", 1, "孫軍、残存30%を割って総崩れ。曹軍、勝ち鬨を上げる。")
        e_wipe = F.Event(t, "壊滅", F.LINE_PRIO["壊滅"], "孫X の隊、ついに壊滅。", side="B", must=True)
        e_b = F.Event(t, "兵法", F.LINE_PRIO["兵法"], "孫Y の【乙】が炸裂！！", mag=100.0, side="B", cast=2)
        e_a = F.Event(t, "兵法", F.LINE_PRIO["兵法"], "曹Z の【甲】が炸裂！！　孫X を討ち取る！",
                      mag=500.0, side="A", cast=1, must=True)
        quote = F.Event(t, "台詞", F.LINE_PRIO["台詞"], "曹Z「行くぞ」", mag=500.0,
                        ref=id(e_a), side="A")
        # 盤面が積む順とは違う順（決着が先）に並んでいても
        ev = [opening, e_end, e_wipe, e_b, e_a, quote]
        casts = [{"id": 1, "nth": 1}, {"id": 2, "nth": 1}]
        lines, sides = F._arrange(ev, casts, 1.0)
        self.assertEqual(len(lines), len(sides))

        def idx(s):
            return next(i for i, l in enumerate(lines) if s in l)

        self.assertLess(idx("【甲】"), idx("ついに壊滅"))
        self.assertLess(idx("【乙】"), idx("ついに壊滅"))
        self.assertLess(idx("ついに壊滅"), idx("総崩れ"))
        # 台詞は発動の直後（決着の後へ回らない）
        self.assertEqual(idx("行くぞ"), idx("【甲】") + 1)
        # 同じ秒でも1出来事1行で、各行に時刻が付く
        body = [l for l in lines if not l.startswith("━━") and "【布陣】" not in l
                and "「" not in l]
        self.assertEqual(len(body), 4)
        self.assertTrue(all("【{}】".format(F.clock(t)) in l for l in body))
        # 主体は行ごと
        self.assertEqual(sides[idx("【甲】")], "A")
        self.assertEqual(sides[idx("【乙】")], "B")
        self.assertEqual(sides[idx("ついに壊滅")], "B")
        self.assertEqual(sides[idx("行くぞ")], "A")

    def test_first_casts_before_repeats_and_must_survives_caps(self):
        F._JP["A"], F._JP["B"] = "曹", "孫"
        opening = F.Event(-1.0, "布陣", F.LINE_PRIO["布陣"], "両軍、布陣。")
        ev = [opening]
        casts = []
        # 同じ兵法を6回。大きさは後ほど大きいが、初回が先に選ばれる
        for i in range(6):
            casts.append({"id": i + 1, "nth": i + 1})
            ev.append(F.Event(10.0 + i, "兵法", F.LINE_PRIO["兵法"],
                              "曹Z の【甲】{}回目".format(i + 1), mag=100.0 * (i + 1),
                              side="A", cast=i + 1))
        # 別の武将の初回（小さい）と、6回目の打消し（must）
        casts.append({"id": 7, "nth": 1})
        ev.append(F.Event(20.0, "兵法", F.LINE_PRIO["兵法"], "孫Y の【乙】1回目", mag=1.0,
                          side="B", cast=7))
        ev[-2].must = True          # 甲の6回目（打ち消された・決め手）
        lines, _ = F._arrange(ev, casts, 1.0)
        text = "\n".join(lines)
        self.assertIn("【甲】1回目", text)
        self.assertIn("【乙】1回目", text)
        self.assertIn("【甲】6回目", text)
        self.assertNotIn("【甲】5回目", text)     # 枠（兵法3本）を超えた再発動は落ちる
        self.assertNotIn("【甲】2回目", text)

    def test_opening_cast_survives_bigger_later_casts(self):
        """**戦いで最初の兵法は必ず出す**（§7.241）。

        枠の中の取捨は「初回優先 → 大きい順」なので、開幕の1発は小さいから
        落ちていた（序盤は兵が減っていないぶん量が出ない）。実測で 178戦の
        35% で1発目が実況から消えていた（テストプレイの報告）。
        """
        F._JP["A"], F._JP["B"] = "曹", "孫"
        ev = [F.Event(-1.0, "布陣", F.LINE_PRIO["布陣"], "両軍、布陣。")]
        casts = [{"id": 1, "nth": 1}]
        ev.append(F.Event(5.0, "兵法", F.LINE_PRIO["兵法"], "曹Z の【開幕】",
                          mag=1.0, side="A", cast=1))
        # あとから撃たれた大技を、枠（兵法3本）ぶんより多く並べる
        for i in range(5):
            casts.append({"id": 10 + i, "nth": 1})
            ev.append(F.Event(50.0 + i, "兵法", F.LINE_PRIO["兵法"],
                              "孫Y の【大技{}】".format(i), mag=9000.0 + i,
                              side="B", cast=10 + i))
        lines, _ = F._arrange(ev, casts, 1.0)
        self.assertIn("【開幕】", "\n".join(lines))


class Scene1_LaterCastDecides(unittest.TestCase):
    """2回目以降の兵法で決着する: 決め手が実況から消えず、決着の前に出る。"""

    def test_decisive_repeat_cast_is_kept_before_the_end(self):
        name = "＿決め手試験"
        _register(name, "ダメージ 威力2000%", "敵全体")
        found = False
        try:
            atk = _card(F._synth(10.0, F.CAV), skill=name, gauge_cost=60.0, gauge_init=0.0)
            a = _army([atk] + _filler(5))
            b = _army(_filler(6))
            for seed in range(1, 7):
                d = F.narrate_full(a, b, 0.25, seed=seed)
                r = d["result"]
                if r["reason"] != "rout":
                    continue
                dec = [c for c in d["casts"] if c["decisive"]]
                if not dec:
                    continue
                # 決め手の発動は総崩れのティックのもの
                self.assertTrue(all(abs(c["t"] - r["t"]) < 1e-6 for c in dec))
                lines = d["lines"]
                i_end = next(i for i, l in enumerate(lines) if "総崩れ" in l)
                for c in dec:
                    hit = [i for i, l in enumerate(lines)
                           if "【{}】".format(c["skill"]) in l
                           and "【{}】".format(F.clock(c["t"])) in l]
                    self.assertTrue(hit, "決め手の発動が実況に無い: {}".format(c))
                    self.assertTrue(all(i < i_end for i in hit), "決め手が決着の後に出ている")
                    if c["nth"] >= 2:
                        found = True
                # 記録は全発動、実況は要約
                self.assertGreater(len(d["casts"]), sum(1 for l in lines if "【" + name + "】" in l))
        finally:
            _unregister(name)
        self.assertTrue(found, "2回目以降の発動が決め手になる戦いが1つも作れなかった（場面の再現に失敗）")


class Scene6_SameNameBothSides(unittest.TestCase):
    """両軍に同名武将・同名兵法: 軍名で区別し、行ごとに主体を持つ。"""

    def test_names_carry_army_and_every_cast_line_has_a_side(self):
        name = "＿同名兵法"
        _register(name, "ダメージ 威力300%", "敵前衛")
        try:
            twin = _card(F._synth(6.0, F.INF), name="同名太郎", skill=name, gauge_cost=60.0)
            a = _army([twin] + _filler(5))
            b = _army([twin] + _filler(5))
            d = F.narrate_full(a, b, 0.25, seed=2)
            casts = [c for c in d["casts"] if c["skill"] == name]
            self.assertTrue(any(c["side"] == "A" for c in casts))
            self.assertTrue(any(c["side"] == "B" for c in casts), "後手の同名兵法も記録される")
            for c in casts:
                self.assertTrue(c["who"].startswith("同名太郎（"), c["who"])
                self.assertTrue(c["who"].endswith("軍）"), c["who"])
            for ln, sd in zip(d["lines"], d["sides"]):
                if "【" + name + "】" in ln:
                    self.assertIn(sd, ("A", "B"))
                    self.assertIn("同名太郎（", ln)
            rep = PL.replay_data(a, b, 0.25, 2, True)
            self.assertTrue(all(u["pos"] for u in rep["mine"] + rep["foe"]))
            self.assertEqual(rep["end_reason"], d["result"]["reason"])
            self.assertEqual(rep["end_clock"], F.clock(d["result"]["t"]))
            self.assertEqual(len(rep["casts"]), len(d["casts"]))
            self.assertTrue(all(c["side"] in ("mine", "foe") for c in rep["casts"]))
        finally:
            _unregister(name)


class WipedUnitsStaySilent(unittest.TestCase):
    """壊滅した隊は、その後の出来事（迂回の到達・矢継ぎの乱れ）を語らない。"""

    def _bettor(self):
        F._JP["A"], F._JP["B"] = "曹", "孫"
        F._DUP_NAMES = set()
        ua = F.build(_army(_filler(6, typ=F.CAV)), 1)
        ub = F.build(_army(_filler(6)), -1)
        u = ua[0]
        u.total_len = max(u.total_len, 100.0)
        return ua, ub, u

    def test_wiped_bettor_never_arrives_and_wipe_line_closes_the_bet(self):
        ua, ub, u = self._bettor()
        seen = {("賭", id(u))}
        ev = []
        gap = [[10.0] * len(ub) for _ in ua]
        # 回り込む途中（道のりの 60%）で壊滅
        u.progress = 0.6 * u.total_len
        u.men = 0.0
        F._log_tick(ev, seen, 20.0, ua, ub, gap)
        wipe = [e for e in ev if e.kind == "壊滅" and F._who(u) in e.text]
        self.assertEqual(len(wipe), 1)
        self.assertIn("敵陣の背後へ回り込む途中（道のりの60%）で", wipe[0].text)
        self.assertTrue(wipe[0].must)
        self.assertIn(("着", id(u)), seen)
        # その後に経路を進み切っても「背後へ現れる」とは言わない
        u.progress = u.total_len
        F._log_tick(ev, seen, 40.0, ua, ub, gap)
        self.assertFalse(any("背後へ現れる" in e.text for e in ev))
        # 日没の締めでも「回り込めぬまま日が暮れる」を重ねない
        F._log_close(ev, seen, 400.0, "time", ua, ub, 0.5, 0.4)
        self.assertFalse(any("回り込めぬまま" in e.text and F._who(u) in e.text for e in ev))

    def test_living_bettor_still_arrives(self):
        ua, ub, u = self._bettor()
        seen = {("賭", id(u))}
        ev = []
        gap = [[10.0] * len(ub) for _ in ua]
        u.progress = u.total_len
        F._log_tick(ev, seen, 40.0, ua, ub, gap)
        self.assertTrue(any("背後へ現れる" in e.text and F._who(u) in e.text for e in ev))

    def test_wiped_archer_is_not_suppressed(self):
        F._JP["A"], F._JP["B"] = "曹", "孫"
        ua = F.build(_army(_filler(6, typ=F.ARC)), 1)
        ub = F.build(_army(_filler(6)), -1)
        for x in ua:
            x.men = 0.0
        ev, seen = [], set()
        gap = [[0.1] * len(ub) for _ in ua]     # 間近に迫られている
        F._log_tick(ev, seen, 20.0, ua, ub, gap)
        self.assertFalse(any(e.kind == "抑制" for e in ev))


class Wording(_Recording):
    """文言: 兵法への備え・混乱した・弱体の言い分け・複合兵法と代償・反動。"""

    def _line(self, effect, target, who=0):
        ua, ub = self._units()
        sk = F._parse_skill(effect, target)
        ev = []
        F._apply_skill(ua[who], sk, target, ua, ub, 0.0, src="試", name="試",
                       kind_jp="兵法", ev=ev, seen=set())
        self.assertTrue(ev)
        return ev[-1].text

    def test_skill_guard_is_not_called_keiryaku(self):
        text = self._line("兵法防御 +30%（20秒）", "味方前衛")
        self.assertIn("兵法への備え", text)
        self.assertNotIn("計略", text)

    def test_chaos_is_told_as_confusion_not_as_friendly_fire(self):
        text = self._line("混乱 25%（9秒）", "敵前衛")
        self.assertIn("混乱した", text)
        self.assertNotIn("同士討ち", text)

    def test_debuffs_name_the_stat(self):
        self.assertIn("足が鈍る", self._line("移動速度 -30%（10秒）", "敵前衛"))
        self.assertIn("守りが乱れる", self._line("防御力 -20%（10秒）", "敵前衛"))
        self.assertIn("刃が鈍る", self._line("攻撃力 -20%（10秒）", "敵前衛"))

    def test_compound_skill_tells_side_effects_and_cost(self):
        # 威力は「語る下限」（NARRATE_FLOOR＝対象の兵力の2%）を超える大きさに
        text = self._line("ダメージ 威力3000% + 防御力 -20%（15秒） + 代償 兵力10%", "敵前衛")
        self.assertIn("損害", text)
        self.assertIn("あわせて", text)
        self.assertIn("守りが乱れる", text)
        self.assertIn("代償として自隊の兵", text)
        rec = F._CASTS[-1]
        self.assertGreater(rec["sac"], 0.0)
        self.assertTrue(any(m[0] == "防御力" for m in rec["mods"]))

    def test_recoil_is_told(self):
        effect = "ダメージ 威力3000% + 反動 攻撃力 -20%（15秒）"
        self.assertTrue(F._parse_skill(effect, "敵前衛").self_mods)
        text = self._line(effect, "敵前衛")
        self.assertIn("反動で自身の刃が鈍る", text)
        self.assertTrue(F._CASTS[-1]["recoil"])

    def test_narration_targets_come_from_actual_recipients(self):
        # 割合回復は「解決後に最も減っている味方」を選び直す。文はその相手で書く
        ua, ub = self._units()
        ua[3].men = ua[3].men0 * 0.2
        sk = F._parse_skill("回復 最大兵力の10%", "味方1体（残兵力が最少）")
        ev = []
        F._apply_skill(ua[0], sk, "味方1体（残兵力が最少）", ua, ub, 0.0, src="試",
                       name="試", kind_jp="兵法", ev=ev, seen=set())
        self.assertIn(F._who(ua[3]), ev[-1].text)
        self.assertEqual(F._CASTS[-1]["hit"], [F._who(ua[3])])


if __name__ == "__main__":
    unittest.main(verbosity=1)
