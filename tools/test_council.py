# -*- coding: utf-8 -*-
"""軍議演習の受け入れ試験: 演習令・陣容再戦・戦績/相手への非混入。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import field as F
from sim import match as M
from sim import play as PL
from sim import players as P


FAIL = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  NG  ") + name
          + ("" if cond else "  " + str(detail)))
    if not cond:
        FAIL.append(name)


tmp = tempfile.mkdtemp(prefix="sangoku-council-")
cx = P.connect(os.path.join(tmp, "players.db"))
cards = M._roster_cards()
dummies = PL.ensure_dummies(cx, cards)
me = P.register(cx, "軍議検査")
foe_pid = next(iter(dummies))
foe = P.get(cx, foe_pid)
reg = PL.REG_NAMES[0]
reg_i = 0

# 在野の有効な部隊を自軍登録にも借り、戦闘機構そのものではなく軍議の配管を検査。
mine_army = dummies[foe_pid].unit(reg_i)
P.set_deck(cx, me.id, reg, "、".join(c.name for c in mine_army.cards),
           F.FORM_NAME[mine_army.form.n_front])
other_pid = next(pid for pid in dummies if pid != foe_pid)
foe_army = dummies[other_pid].unit(reg_i)
now = 1_800_000_000
source = P.record_battle(
    cx, "ranked", reg, me.id, other_pid, 123,
    json.dumps(PL.snap_army(mine_army), ensure_ascii=False),
    json.dumps(PL.snap_army(foe_army), ensure_ascii=False),
    "2027-01", now - 60, result="●")

print("[1] 演習令")
check("初期値は10", P.enshu(cx, me.id, now) == (10, 0),
      P.enshu(cx, me.id, now))
check("1枚消費できる", P.spend_enshu(cx, me.id, 1, now))
check("消費後は9・次回復600秒", P.enshu(cx, me.id, now) == (9, 600),
      P.enshu(cx, me.id, now))
check("10分後に1枚回復", P.enshu(cx, me.id, now + 600) == (10, 0),
      P.enshu(cx, me.id, now + 600))
P.refill_enshu(cx, me.id, now)

print("[2] 過去陣容への仮想対戦")
before_record = P.record_of(cx, me.id)
r = PL.council_battle(cx, cards, me, source, now + 1)
check("演習が成立", "battle_id" in r, r)
run = P.council_run(cx, r.get("battle_id", 0))
check("元記録との対応を保存", run and run["source_battle_id"] == source, run)
battle = cx.execute("SELECT * FROM battles WHERE id=?",
                    (r.get("battle_id", 0),)).fetchone()
check("独立したcouncilモード", battle and battle["mode"] == "council",
      dict(battle) if battle else None)
check("仮想敵pidで相手本人へ混ぜない",
      battle and battle["pid_b"] == "council:{}".format(source),
      battle["pid_b"] if battle else None)
check("敵側は元対戦の陣容を固定",
      battle and battle["snap_b"] == json.dumps(
          PL.snap_army(foe_army), ensure_ascii=False))
check("演習令だけ1枚減る", P.enshu(cx, me.id, now + 1)[0] == 9,
      P.enshu(cx, me.id, now + 1))
check("通常戦績へ軍議結果を加えない", P.record_of(cx, me.id) == before_record,
      (before_record, P.record_of(cx, me.id)))
check("元の相手の戦歴へ軍議を加えない",
      all(x["id"] != r.get("battle_id")
          for x in P.battles_of(cx, pid=other_pid)))

print("[3] 不正な入口")
stranger = P.register(cx, "無関係者")
bad = PL.council_battle(cx, cards, stranger, source, now + 2)
check("参加していない陣容は使えない", "error" in bad, bad)
again = PL.council_battle(cx, cards, me, r.get("battle_id", 0), now + 3)
check("演習記録を孫コピーできない", "error" in again, again)

print("[4] 赤チームの候補（§7.148・探索器の出力）")
reds = PL.red_team_entries(cards)
check("候補が読める（docs/balance/bo3-goodstuff.json）", len(reds) >= 1, len(reds))
check("メタ解析の均衡 support も 100番台で載る", any(r >= 101 for r, _n, _e in reds), [r for r, _n, _e in reds])
if reds:
    rank, rname, rentry = reds[0]
    check("3部隊とも合法", not M.validate(rentry), M.validate(rentry))
    P.refill_enshu(cx, me.id, now + 10)
    before_record = P.record_of(cx, me.id)
    rr = PL.council_battle_red(cx, cards, me, rank, reg, now + 10)
    check("汜水関で演習が成立", "battle_id" in rr, rr)
    run = P.council_run(cx, rr.get("battle_id", 0))
    check("記録の source は −順位・名前は赤チーム", run and run["source_battle_id"] == -rank
          and run["foe_name"] == rname, run)
    battle = cx.execute("SELECT * FROM battles WHERE id=?", (rr.get("battle_id", 0),)).fetchone()
    check("仮想敵pidは council:red:順位", battle and battle["pid_b"] == "council:red:{}".format(rank),
          battle["pid_b"] if battle else None)
    check("敵側の陣容は候補の6枚", battle and json.loads(battle["snap_b"])["cards"][0]["n"] == rentry.unit(reg_i).cards[0].name)
    check("演習令だけ1枚減る", P.enshu(cx, me.id, now + 10)[0] == 9, P.enshu(cx, me.id, now + 10))
    check("通常戦績は不変", P.record_of(cx, me.id) == before_record)
    bad = PL.council_battle_red(cx, cards, me, 999, reg, now + 11)
    check("無い順位は弾く", "error" in bad, bad)
    bad2 = PL.council_battle_red(cx, cards, me, rank, "天下", now + 12)
    check("他の戦場が未登録なら天下は弾く", "error" in bad2, bad2)

print()
if FAIL:
    print("失敗:", FAIL)
    sys.exit(1)
print("全部通った")
