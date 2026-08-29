# -*- coding: utf-8 -*-
"""手元モードの互換試験。名乗りログイン・旧pidクッキー・devの口が生きること。"""
import http.client, json, os, sys, threading
from http.server import ThreadingHTTPServer

SP = os.path.dirname(os.path.abspath(__file__))
import tempfile
DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-test-"), "players.db")
if os.path.exists(DB):
    os.remove(DB)
PORT = 8983
os.environ.update({"SANGOKU_DB": DB, "SANGOKU_PORT": str(PORT),
                   "SANGOKU_HOST": "127.0.0.1",
                   "no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})
os.environ.pop("SANGOKU_PUBLIC", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import web as W, players as P, play as PL, match as M, field as F
app = ThreadingHTTPServer(("127.0.0.1", PORT), W.App)
threading.Thread(target=app.serve_forever, daemon=True).start()

FAIL = []
def check(name, cond, detail=""):
    print(("  OK  " if cond else "  NG  ") + name + ("" if cond else f"  {detail}"))
    if not cond: FAIL.append(name)

def req(method, path, cookie="", body=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=120)
    h = {"Cookie": cookie} if cookie else {}
    payload = json.dumps(body) if body is not None else None
    if payload: h["Content-Type"] = "application/json"
    c.request(method, path, payload, h)
    r = c.getresponse(); return r, r.read()

req("GET", "/api/state")   # 温め（在野の初期化）
r, d = req("GET", "/api/state")
st = json.loads(d)
check("auth.mode が local", st.get("auth", {}).get("mode") == "local")

r, d = req("POST", "/api/login", body={"new": "検証太郎"})
check("名乗りログインが通る", r.status == 200 and json.loads(d).get("ok"))
sid = (r.getheader("Set-Cookie") or "").split(";")[0]
check("署名つき sid が出る", sid.startswith("sid=") and "." in sid)
r, d = req("GET", "/api/state", cookie=sid)
me = json.loads(d).get("me")
check("sid で me が立つ", bool(me) and me["name"] == "検証太郎")
state = json.loads(d)
truce = state.get("tenka", {}).get("truce", {})
check("休戦令の初期設定8枚がstateへ出る",
      truce.get("default_hours") == list(range(8))
      and len(truce.get("days", [])) == 7, truce)

# 2日後なら締切境界に掛からず、どの時刻に走らせても日別変更できる。
future = truce.get("days", [{}, {}, {}])[2].get("day")
r, d = req("POST", "/api/truce", cookie=sid,
           body={"action": "day", "day": future,
                 "hours": list(range(8, 16))})
changed = json.loads(d)
check("Webから日別の休戦令を変更できる",
      r.status == 200 and changed.get("ok"), changed)
r, d = req("POST", "/api/truce", cookie=sid,
           body={"action": "day", "day": future, "hours": [1, 2]})
check("Webも8枚でない休戦令を拒否", r.status == 400, d)

# 旧クッキーの互換（過去のブラウザに残っている pid=...）
r, d = req("GET", "/api/state", cookie="pid=" + me["id"])
check("旧 pid クッキーも手元では通る", (json.loads(d).get("me") or {}).get("id") == me["id"])

r, d = req("POST", "/api/dev_heifu", body={}, )
check("dev の口は手元では開く（401=要ログインでなく404でないこと）",
      r.status != 404, f"got {r.status}")
r, d = req("POST", "/api/dev_heifu", cookie=sid, body={})
check("dev_heifu が sid で通る", r.status == 200, f"got {r.status}")
r, d = req("POST", "/api/dev_enshu", cookie=sid, body={})
check("dev_enshu（無料MAX）が sid で通る", r.status == 200, f"got {r.status}")

# 軍議演習の Web 配管（対象一覧→実行→リプレイ→戦歴動線）。
cx = P.connect(DB)
cards = M._roster_cards()
dummies = PL.ensure_dummies(cx, cards)
pids = list(dummies)
army_a = dummies[pids[0]].unit(0)
army_b = dummies[pids[1]].unit(0)
reg = PL.REG_NAMES[0]
P.set_deck(cx, me["id"], reg, "、".join(c.name for c in army_a.cards),
           F.FORM_NAME[army_a.form.n_front])
source = P.record_battle(
    cx, "ranked", reg, me["id"], pids[1], 777,
    json.dumps(PL.snap_army(army_a), ensure_ascii=False),
    json.dumps(PL.snap_army(army_b), ensure_ascii=False),
    PL.season_key(1800000000), 1800000000, "●")
r, d = req("GET", "/api/council", cookie=sid)
council = json.loads(d)
check("軍議APIに演習令10と対戦魚拓が出る",
      r.status == 200 and council.get("ticket", {}).get("count") == 10
      and any(x["id"] == source for x in council.get("targets", [])), council)
r, d = req("POST", "/api/council_fight", cookie=sid,
           body={"source_id": source})
fought = json.loads(d)
check("Webから軍議演習を実行できる",
      r.status == 200 and fought.get("battle_id"), fought)
if fought.get("battle_id"):
    r, d = req("GET", "/api/replay?id=" + str(fought["battle_id"]), cookie=sid)
    replay = json.loads(d)
    check("軍議結果をリプレイできる",
          r.status == 200 and replay.get("mode") == "council"
          and replay.get("foe_name") == P.get(cx, pids[1]).display_name, replay)
    r, d = req("GET", "/api/replays", cookie=sid)
    hist = json.loads(d).get("battles", [])
    srcrow = next((x for x in hist if x["id"] == source), {})
    check("戦歴の元対戦に演習動線が出る", srcrow.get("can_council"), srcrow)
print()
if FAIL:
    print("失敗:", FAIL); sys.exit(1)
print("全部通った")
