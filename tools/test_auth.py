# -*- coding: utf-8 -*-
"""公開モードの受け入れ試験。モックIdPで OIDC の往復を通しで確かめる。"""
import http.client, json, os, re, sqlite3, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SP = os.path.dirname(os.path.abspath(__file__))
import tempfile
DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-test-"), "players.db")
if os.path.exists(DB):
    os.remove(DB)

IDP_PORT, APP_PORT = 8981, 8982
os.environ.update({
    "SANGOKU_PUBLIC": "1",
    "SANGOKU_SECRET": "x" * 48,
    "SANGOKU_BASE_URL": f"http://127.0.0.1:{APP_PORT}",
    "SANGOKU_OIDC_CLIENT_ID": "test-client",
    "SANGOKU_OIDC_CLIENT_SECRET": "test-secret",
    "SANGOKU_OIDC_AUTH_URL": f"http://127.0.0.1:{IDP_PORT}/auth",
    "SANGOKU_OIDC_TOKEN_URL": f"http://127.0.0.1:{IDP_PORT}/token",
    "SANGOKU_OIDC_USERINFO_URL": f"http://127.0.0.1:{IDP_PORT}/userinfo",
    "SANGOKU_DB": DB,
    "SANGOKU_PORT": str(APP_PORT),
    "SANGOKU_DEV": "1",              # 公開モードが env より勝つことの検査に使う
    "no_proxy": "127.0.0.1,localhost", "NO_PROXY": "127.0.0.1,localhost",
})
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CODES = {}   # code -> (challenge)
class IdP(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        if u.path == "/auth":
            # 実IdPなら人がログインする画面。試験では即コードを発行して戻す。
            code = "code-123"
            CODES[code] = q.get("code_challenge", "")
            back = q["redirect_uri"] + "?" + urllib.parse.urlencode(
                {"code": code, "state": q.get("state", "")})
            self.send_response(302); self.send_header("Location", back); self.end_headers()
        elif u.path == "/userinfo":
            auth = self.headers.get("Authorization", "")
            body = (json.dumps({"sub": "google-sub-42", "email": "taiko@example.com",
                                "name": "測 太閤"}) if auth == "Bearer at-xyz"
                    else json.dumps({}))
            b = body.encode(); self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
        else:
            self.send_response(404); self.end_headers()
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = dict(urllib.parse.parse_qsl(self.rfile.read(n).decode()))
        ok = (form.get("code") in CODES and form.get("client_id") == "test-client"
              and form.get("client_secret") == "test-secret"
              and form.get("code_verifier"))
        # PKCE の検証（S256）
        if ok:
            import base64, hashlib
            ch = base64.urlsafe_b64encode(hashlib.sha256(
                form["code_verifier"].encode()).digest()).decode().rstrip("=")
            ok = ch == CODES[form["code"]]
        body = json.dumps({"access_token": "at-xyz"} if ok else {"error": "bad"})
        b = body.encode(); self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)

idp = ThreadingHTTPServer(("127.0.0.1", IDP_PORT), IdP)
threading.Thread(target=idp.serve_forever, daemon=True).start()

from sim import web as W
app = ThreadingHTTPServer(("127.0.0.1", APP_PORT), W.App)
threading.Thread(target=app.serve_forever, daemon=True).start()

FAIL = []
def check(name, cond, detail=""):
    print(("  OK  " if cond else "  NG  ") + name + (f"  {detail}" if detail and not cond else ""))
    if not cond: FAIL.append(name)

def req(method, path, cookie="", body=None, origin=None, timeout=120):
    c = http.client.HTTPConnection("127.0.0.1", APP_PORT, timeout=timeout)
    headers = {}
    if cookie: headers["Cookie"] = cookie
    if origin: headers["Origin"] = origin
    payload = json.dumps(body) if body is not None else None
    if payload: headers["Content-Type"] = "application/json"
    c.request(method, path, payload, headers)
    r = c.getresponse()
    data = r.read()
    return r, data

# 初回の /api/state は在野の初期化で時間がかかる。先に温めておく。
req("GET", "/api/state")
print("[1] 公開モードの封鎖")
r, d = req("GET", "/api/state")
st = json.loads(d)
check("auth.mode が oidc", st.get("auth", {}).get("mode") == "oidc")
check("humans が空（pid を配らない）", st.get("humans") == [])
r, d = req("POST", "/api/login", body={"new": "侵入者"})
check("名乗りログインが 403", r.status == 403, f"got {r.status}")
r, d = req("POST", "/api/dev_heifu", body={})
check("dev の口が SANGOKU_DEV=1 でも 404", r.status == 404, f"got {r.status}")
r, d = req("POST", "/api/deck", body={}, origin="http://evil.example")
check("他所の Origin の POST が 403", r.status == 403, f"got {r.status}")

print("[2] OIDC の往復")
r, d = req("GET", "/auth/login")
check("IdP へ 303", r.status == 303)
loc = r.getheader("Location") or ""
oidc_cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
check("state と PKCE が付く", "state=" in loc and "code_challenge=" in loc)
# IdP を踏む（302 で /auth/callback へ戻される）
u = urllib.parse.urlparse(loc)
c = http.client.HTTPConnection("127.0.0.1", IDP_PORT, timeout=10)
c.request("GET", u.path + "?" + u.query); r2 = c.getresponse(); r2.read()
back = urllib.parse.urlparse(r2.getheader("Location"))
r, d = req("GET", back.path + "?" + back.query, cookie=oidc_cookie)
check("コールバックが 303 で通る", r.status == 303, d.decode()[:100])
sid = (r.getheader("Set-Cookie") or "").split(";")[0]
check("sid クッキーが出る", sid.startswith("sid=") and len(sid) > 20)

r, d = req("GET", "/api/state", cookie=sid)
me = json.loads(d).get("me")
check("sid で me が立つ", bool(me), d.decode()[:80])
check("表示名が IdP の名前", me and me.get("name") == "測 太閤")

print("[3] なりすましと再ログイン")
tam = sid[:-4] + "AAAA"
r, d = req("GET", "/api/state", cookie=tam)
check("署名を壊した sid は無効", json.loads(d).get("me") is None)
r, d = req("GET", "/api/state", cookie="pid={}".format(me["id"]))
check("公開モードは旧 pid クッキーを受けない", json.loads(d).get("me") is None)
# 同じ sub で2度目 → 同じプレイヤー
r, d = req("GET", "/auth/login")
loc = r.getheader("Location"); oc = (r.getheader("Set-Cookie") or "").split(";")[0]
u = urllib.parse.urlparse(loc)
c = http.client.HTTPConnection("127.0.0.1", IDP_PORT, timeout=10)
c.request("GET", u.path + "?" + u.query); r2 = c.getresponse(); r2.read()
back = urllib.parse.urlparse(r2.getheader("Location"))
r, d = req("GET", back.path + "?" + back.query, cookie=oc)
sid2 = (r.getheader("Set-Cookie") or "").split(";")[0]
r, d = req("GET", "/api/state", cookie=sid2)
me2 = json.loads(d).get("me")
check("同じ sub は同じプレイヤー", me2 and me2["id"] == me["id"])
cx = sqlite3.connect(DB)
n = cx.execute("SELECT COUNT(*) FROM players WHERE kind='human'").fetchone()[0]
check("human は1人だけ（重複登録なし）", n == 1, f"n={n}")
row = cx.execute("SELECT provider, subject, email FROM identities"
                 " WHERE provider='google'").fetchone()
check("identities に (google, sub) が入る",
      row == ("google", "google-sub-42", "taiko@example.com"), str(row))

print("[4] state 改竄と横取り")
r, d = req("GET", "/auth/callback?code=code-123&state=WRONG", cookie=oc)
check("state 不一致は 400", r.status == 400)
r, d = req("GET", "/auth/callback?code=code-123&state=x")
check("往復クッキー無しは 400", r.status == 400)

print()
if FAIL:
    print("失敗:", FAIL); sys.exit(1)
print("全部通った")
