# -*- coding: utf-8 -*-
"""デッキ保存庫の受け入れ試験（§7.120）: 枠数の上限・上書き・戦績の中身一致。"""
import http.client, json, os, sys, threading, time
from http.server import ThreadingHTTPServer

SP = os.path.dirname(os.path.abspath(__file__))
import tempfile
DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-test-"), "players.db")
PORT = 8984
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
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=180)
    h = {"Cookie": cookie} if cookie else {}
    payload = json.dumps(body) if body is not None else None
    if payload: h["Content-Type"] = "application/json"
    c.request(method, path, payload, h)
    r = c.getresponse(); return r, r.read()

req("GET", "/api/state")            # 温め（在野の初期化）
r, d = req("POST", "/api/login", body={"new": "保存庫検査"})
sid = (r.getheader("Set-Cookie") or "").split(";")[0]
r, d = req("GET", "/api/deckdata", cookie=sid)
D = json.loads(d)
check("deck_slots が返る", D.get("deck_slots") == 10, str(D.get("deck_slots")))

roster = [c["name"] for c in D["roster"]]
reg = D["regs"][0]["name"]

print("[1] 枠数の上限")
ok_all = True
for i in range(10):
    r, d = req("POST", "/api/savedeck", cookie=sid,
               body={"name": f"型{i}", "reg": reg, "form": "魚鱗",
                     "cards": roster[i:i + 6]})
    ok_all = ok_all and json.loads(d).get("ok")
check("10枠まで保存できる", ok_all)
r, d = req("POST", "/api/savedeck", cookie=sid,
           body={"name": "型10", "reg": reg, "form": "魚鱗", "cards": roster[:6]})
j = json.loads(d)
check("11枠目は断られる", not j.get("ok") and "枠まで" in "".join(j.get("errors", [])))
r, d = req("POST", "/api/savedeck", cookie=sid,
           body={"name": "型3", "reg": reg, "form": "鶴翼", "cards": roster[2:8]})
check("同じ名前への上書きは満杯でも通る", json.loads(d).get("ok"))

print("[2] 戦績の中身一致")
me_id = json.loads(req("GET", "/api/state", cookie=sid)[1])["me"]["id"]
cards = M._roster_cards()
deck_names = json.loads(req("GET", "/api/deckdata", cookie=sid)[1])
mine = [s for s in deck_names["saved"] if s["name"] == "型0"][0]
army, errs = PL.parse_deck(cards, "、".join(mine["cards"]), mine["form"])
assert army and not errs, errs
snap = json.dumps(PL.snap_army(army), ensure_ascii=False)
cx = P.connect(DB)
now = int(time.time())
P.record_battle(cx, "ranked", reg, me_id, "dummy-x", 1, snap, "", "2026-08", now, "○")
P.record_battle(cx, "ranked", reg, me_id, "dummy-x", 2, snap, "", "2026-08", now, "●")
P.record_battle(cx, "ranked", reg, "dummy-x", me_id, 3, "", snap, "2026-08", now, "●")  # 相手側→自分の勝ち
P.record_battle(cx, "free", reg, me_id, "dummy-x", 4, snap, "", "2026-08", now, "○")    # 稽古は数えない
r, d = req("GET", "/api/deckdata", cookie=sid)
rec = [s for s in json.loads(d)["saved"] if s["name"] == "型0"][0].get("rec")
check("戦績が付く（出陣3）", rec and rec["n"] == 3, str(rec))
check("勝敗が合う（2勝1敗・相手側の記録も反転して数える）",
      rec and rec["w"] == 2 and rec["l"] == 1, str(rec))
rec2 = [s for s in json.loads(d)["saved"] if s["name"] == "型1"][0].get("rec")
check("別の編成には付かない", rec2 is None, str(rec2))

print()
if FAIL:
    print("失敗:", FAIL); sys.exit(1)
print("全部通った")
