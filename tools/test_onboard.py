# -*- coding: utf-8 -*-
"""初回の導入と最初の敗北の案内の受け入れ試験（§7.121）。"""
import http.client, json, os, sys, threading, time
from http.server import ThreadingHTTPServer

import tempfile
DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-test-"), "players.db")
PORT = 8985
os.environ.update({"SANGOKU_DB": DB, "SANGOKU_PORT": str(PORT),
                   "SANGOKU_HOST": "127.0.0.1",
                   "no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"})
os.environ.pop("SANGOKU_PUBLIC", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import web as W, players as P

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

req("GET", "/api/state")            # 温め
r, d = req("POST", "/api/login", body={"new": "導入検査"})
sid = (r.getheader("Set-Cookie") or "").split(";")[0]

print("[1] 初回の導入")
st = json.loads(req("GET", "/api/state", cookie=sid)[1])
check("新規プレイヤーに onboard が立つ", st.get("onboard") is True)
r, d = req("POST", "/api/seen", cookie=sid, body={"key": "onboard"})
check("seen が通る", json.loads(d).get("ok") is True)
st = json.loads(req("GET", "/api/state", cookie=sid)[1])
check("見たら二度と立たない", st.get("onboard") is False)
r, d = req("POST", "/api/seen", cookie=sid, body={"key": "first_defeat"})
check("許可の無い旗は書けない", r.status == 400)

print("[2] 最初の敗北の案内")
# わざと最弱の6枚で初戦へ。負けるまで挑む（種は時刻から出るので1秒あける）
D = json.loads(req("GET", "/api/deckdata", cookie=sid)[1])
roster = D["roster"]
# **負けが確実な最安デッキ**を組む。前衛は歩兵だけにする — 騎兵を1枚でも入れると
# 初陣（弓の多い張宝の隊）を 88〜98% で勝ってしまい（馬上回避 §7.144 の後は 98%）、
# 12戦で1度も負けず「最初の敗北」の案内が出ない。歩兵3＋弓3 の最安は勝率 2%。
melee = sorted([c for c in roster if c["typ"] == "歩兵"],
               key=lambda c: c["cost"])
rear = sorted([c for c in roster if c["typ"] == "弓兵"],
              key=lambda c: c["cost"])
deck = [c["name"] for c in melee[:3]] + [c["name"] for c in rear[:3]]
assert len(deck) == 6, deck
got_first = None
for t in range(12):
    r, d = req("POST", "/api/senki_fight", cookie=sid,
               body={"i": 0, "cards": deck, "form": "魚鱗"})
    j = json.loads(d)
    if "error" in j:
        print("   fight error:", j["error"]); break
    if j["win"] == "負け":
        got_first = j.get("first_defeat")
        break
    time.sleep(1.1)
check("負けた戦で first_defeat が立つ", got_first is True, str(got_first))
# もう一度負けても出ない
got_second = None
for t in range(12):
    time.sleep(1.1)
    r, d = req("POST", "/api/senki_fight", cookie=sid,
               body={"i": 0, "cards": deck, "form": "魚鱗"})
    j = json.loads(d)
    if j.get("win") == "負け":
        got_second = j.get("first_defeat")
        break
check("2度目の敗北では出ない", got_second is False, str(got_second))

print()
if FAIL:
    print("失敗:", FAIL); sys.exit(1)
print("全部通った")
