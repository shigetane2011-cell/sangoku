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
from sim import web as W
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

# 旧クッキーの互換（過去のブラウザに残っている pid=...）
r, d = req("GET", "/api/state", cookie="pid=" + me["id"])
check("旧 pid クッキーも手元では通る", (json.loads(d).get("me") or {}).get("id") == me["id"])

r, d = req("POST", "/api/dev_heifu", body={}, )
check("dev の口は手元では開く（401=要ログインでなく404でないこと）",
      r.status != 404, f"got {r.status}")
r, d = req("POST", "/api/dev_heifu", cookie=sid, body={})
check("dev_heifu が sid で通る", r.status == 200, f"got {r.status}")
print()
if FAIL:
    print("失敗:", FAIL); sys.exit(1)
print("全部通った")
