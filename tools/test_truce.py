# -*- coding: utf-8 -*-
"""休戦令（毎時天下）の小さな契約試験。戦闘シミュレーションは回さない。"""
import datetime
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-truce-"), "players.db")
os.environ["SANGOKU_DB"] = DB

from sim import field as F, match as M, play as PL, players as P  # noqa: E402

FAIL = []


def check(name, cond, detail=""):
    print(("  OK  " if cond else "  NG  ") + name +
          ("" if cond else "  " + str(detail)))
    if not cond:
        FAIL.append(name)


cx = P.connect(DB)
me = P.register(cx, "休戦試験")

# 地方時を正本にするコードなので、固定日時も timestamp() で同じ地方時へ変換する。
now_dt = datetime.datetime(2026, 8, 29, 12, 30)
now = int(now_dt.timestamp())
today = now_dt.date().isoformat()
tomorrow = (now_dt.date() + datetime.timedelta(days=1)).isoformat()

check("初期設定は0〜7時の8枚",
      P.truce_hours(P.truce_default(cx, me.id)) == list(range(8)))
check("0時は休戦、8時は参戦",
      PL.truce_is_active(cx, me.id, PL._event_time(today, 0)) and
      not PL.truce_is_active(cx, me.id, PL._event_time(today, 8)))

try:
    P.truce_mask([0, 1, 2])
    short_rejected = False
except ValueError:
    short_rejected = True
check("8枚未満を拒否", short_rejected)

new_default = list(range(8, 16))
PL.set_truce_default(cx, me.id, new_default, now)
today_mask, today_source = P.truce_day(cx, me.id, today)
tomorrow_mask, tomorrow_source = P.truce_day(cx, me.id, tomorrow)
check("通常設定変更でも今日の締切済み設定は魚拓で不変",
      P.truce_hours(today_mask) == list(range(8)) and today_source == "day")
check("新しい通常設定は翌日から反映",
      P.truce_hours(tomorrow_mask) == new_default and
      tomorrow_source == "default")

custom = list(range(10, 18))
PL.set_truce_day(cx, me.id, tomorrow, custom, now)
mask, source = P.truce_day(cx, me.id, tomorrow)
check("日別変更が通常設定より優先",
      P.truce_hours(mask) == custom and source == "day")
PL.set_truce_day(cx, me.id, tomorrow, [], now, reset=True)
mask, source = P.truce_day(cx, me.id, tomorrow)
check("日別変更を通常設定へ戻せる",
      P.truce_hours(mask) == new_default and source == "default")

# 今日の過去枠を後付け変更する操作は、8枚を守っていても拒否する。
try:
    PL.set_truce_day(cx, me.id, today, list(range(1, 9)), now)
    locked_rejected = False
except ValueError:
    locked_rejected = True
check("過去・開催2時間前の変更を拒否", locked_rejected)

start = int(datetime.datetime(2026, 8, 30, 0, 0).timestamp()) - 1
end = int(datetime.datetime(2026, 8, 30, 23, 0).timestamp())
events = PL.tenka_events(start, end)
check("天下は1日24回（毎時00分）", len(events) == 24,
      [datetime.datetime.fromtimestamp(t).hour for _, t in events])
check("開催通し番号は日付×100+時", events[0][0] == 2026083000 and
      events[-1][0] == 2026083023)

schedule = PL.truce_schedules(cx, me.id, now)
check("画面契約は休戦令8枚・7日分",
      schedule["name"] == "休戦令" and schedule["count"] == 8 and
      len(schedule["days"]) == 7)
check("締切表示に14時を含み15時を含まない",
      14 in schedule["days"][0]["locked"] and
      15 not in schedule["days"][0]["locked"])

# 実在野を使い、休戦者の除外・奇数時の在野調整・二重開催防止まで1開催だけ通す。
cards = M._roster_cards()
dummies = PL.ensure_dummies(cx, cards)
soldier = P.register(cx, "天下試験")
sample = next(iter(dummies.values()))
for i, reg in enumerate(PL.REG_NAMES):
    army = sample.unit(i)
    P.set_deck(cx, soldier.id, reg, "、".join(c.name for c in army.cards),
               F.FORM_NAME[army.form.n_front])
rest_at = PL._event_time(tomorrow, 0)
active_at = PL._event_time(tomorrow, 8)
check("休戦開催では人間を組合せ対象から外す",
      soldier.id not in PL._tenka_participants(cx, cards, rest_at))
check("休戦外かつ3デッキ登録なら組合せ対象",
      soldier.id in PL._tenka_participants(cx, cards, active_at))
rest_serial = int(tomorrow.replace("-", "")) * 100
active_serial = rest_serial + 8
before = cx.execute("SELECT COUNT(*) n FROM battles").fetchone()["n"]
check("全人間が休戦なら在野だけの天下を開催しない",
      PL._tenka_resolve(cx, cards, rest_serial, rest_at) == 0)
fought = PL._tenka_resolve(cx, cards, active_serial, active_at)
after = cx.execute("SELECT COUNT(*) n FROM battles").fetchone()["n"]
mine = cx.execute(
    "SELECT COUNT(*) n FROM battles WHERE mode='tenka'"
    " AND (pid_a=? OR pid_b=?)", (soldier.id, soldier.id)).fetchone()["n"]
check("奇数人数でも人間は1回のBO3を戦う",
      fought == 12 and after - before == 12 and mine == 1,
      {"fought": fought, "written": after - before, "mine": mine})
check("同じ開催番号の再要求は二重開催しない",
      PL._tenka_resolve(cx, cards, active_serial, active_at) == 0 and
      cx.execute("SELECT COUNT(*) n FROM battles").fetchone()["n"] == after)
run = cx.execute("SELECT state,fought FROM tenka_runs WHERE serial=?",
                 (active_serial,)).fetchone()
check("開催印が完了戦数を持つ", run["state"] == "done" and run["fought"] == 12)

# 旧1日2回版からの切替日に過去24時間をまとめて走らせない。
DB2 = os.path.join(tempfile.mkdtemp(prefix="sangoku-truce-migrate-"), "players.db")
cx2 = P.connect(DB2)
P.register(cx2, "移行試験")
PL.tick(cx2, cards, now)
current_serial = int(now_dt.strftime("%Y%m%d")) * 100 + now_dt.hour
check("毎時版への初回移行は現在時までを済扱い",
      int(P.ledger_get(cx2, "tenka_done")) == current_serial and
      cx2.execute("SELECT COUNT(*) n FROM battles").fetchone()["n"] == 0)
next_tick = int(now_dt.replace(hour=13, minute=1).timestamp())
PL.tick(cx2, cards, next_tick)
check("次の00分から通常の毎時処理へ入る",
      int(P.ledger_get(cx2, "tenka_done")) == current_serial + 1 and
      cx2.execute("SELECT state FROM tenka_runs WHERE serial=?",
                  (current_serial + 1,)).fetchone()["state"] == "done")

print()
if FAIL:
    print("失敗:", FAIL)
    sys.exit(1)
print("全部通った")
