# -*- coding: utf-8 -*-
"""三面一括操作の受け入れ試験（§7.128）: 一斉登録・原子性・入れ替え・一斉リセット。"""
import http.client, json, os, sys, threading
from http.server import ThreadingHTTPServer

import tempfile
DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-test-"), "players.db")
PORT = 8987
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
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=180)
    h = {"Cookie": cookie} if cookie else {}
    payload = json.dumps(body) if body is not None else None
    if payload: h["Content-Type"] = "application/json"
    c.request(method, path, payload, h)
    r = c.getresponse(); return r, r.read()

req("GET", "/api/state")
r, d = req("POST", "/api/login", body={"new": "三面検査"})
sid = (r.getheader("Set-Cookie") or "").split(";")[0]
r, d = req("POST", "/api/dev_senki", cookie=sid, body={})   # 全登用（試験用）
r, d = req("GET", "/api/deckdata", cookie=sid)
D = json.loads(d)
regs = [x["name"] for x in D["regs"]]
caps = {x["name"]: x["cap"] for x in D["regs"]}

# 面ごとに人物の重ならない安い正規デッキを組む（魚鱗: 前3=弓以外・後3=弓か槍）
melee = sorted([c for c in D["roster"] if c["typ"] != "弓兵"], key=lambda c: c["cost"])
arc = sorted([c for c in D["roster"] if c["typ"] == "弓兵"], key=lambda c: c["cost"])
used = set()
def build(cap):
    f, b = [], []
    for c in melee:
        if c["person"] in used or len(f) >= 3: continue
        f.append(c); used.add(c["person"])
    for c in arc:
        if c["person"] in used or len(b) >= 3: continue
        b.append(c); used.add(c["person"])
    deck = f[:3] + b[:3]
    assert sum(c["cost"] for c in deck) <= cap, "試験デッキが上限超え"
    return [c["name"] for c in deck]
B = {reg: build(caps[reg]) for reg in regs}

print("[1] 一斉登録")
r, d = req("POST", "/api/deck_all", cookie=sid,
           body={"boards": [{"reg": reg, "form": "魚鱗", "cards": B[reg]}
                            for reg in regs]})
j = json.loads(d)
check("3面まとめて登録できる", j.get("ok") is True, str(j)[:200])
check("3面とも出陣可", all(j.get("boards_ok", {}).get(reg) for reg in regs),
      str(j.get("boards_ok")))

print("[2] 原子性（1面の不備で全体を書かない）")
bad = dict(B)
r, d = req("POST", "/api/deck_all", cookie=sid,
           body={"boards": [
               {"reg": regs[0], "form": "魚鱗", "cards": B[regs[0]][:5]},  # 5人
               {"reg": regs[1], "form": "魚鱗", "cards": B[regs[1]]},
               {"reg": regs[2], "form": "魚鱗", "cards": B[regs[2]]}]})
j = json.loads(d)
check("不備セットは断られる", j.get("ok") is False and regs[0] in j.get("errors", {}))
r, d = req("GET", "/api/deckdata", cookie=sid)
D2 = json.loads(d)
check("前の登録が残っている", D2["decks"].get(regs[0], {}).get("cards") == B[regs[0]])

print("[3] 面間の同一人物はセット内で弾く")
r, d = req("POST", "/api/deck_all", cookie=sid,
           body={"boards": [
               {"reg": regs[0], "form": "魚鱗", "cards": B[regs[0]]},
               {"reg": regs[1], "form": "魚鱗",
                "cards": B[regs[0]][:1] + B[regs[1]][1:]}]})   # 先頭を共有
j = json.loads(d)
check("同一人物入りセットは断られる", j.get("ok") is False, str(j)[:200])

print("[4] 入れ替え（従来は順番のパズルだった操作が1回で通る）")
S1 = B[regs[0]][:1] + B[regs[1]][1:]     # 面1の先頭 ↔ 面2の先頭 を交換
S2 = B[regs[1]][:1] + B[regs[0]][1:]
r, d = req("POST", "/api/deck_all", cookie=sid,
           body={"boards": [
               {"reg": regs[0], "form": "魚鱗", "cards": S2},
               {"reg": regs[1], "form": "魚鱗", "cards": S1},
               {"reg": regs[2], "form": "魚鱗", "cards": B[regs[2]]}]})
j = json.loads(d)
check("先頭どうしの交換が1回の呼び出しで通る", j.get("ok") is True, str(j)[:200])
check("交換後も3面とも出陣可", all(j.get("boards_ok", {}).get(reg) for reg in regs),
      str(j.get("boards_ok")))

print("[5] 一斉リセット")
r, d = req("POST", "/api/savedeck", cookie=sid,
           body={"name": "残る保存", "reg": regs[0], "form": "魚鱗",
                 "cards": B[regs[0]]})
check("（準備）保存庫に1枠", json.loads(d).get("ok"))
r, d = req("POST", "/api/deck_reset", cookie=sid, body={})
j = json.loads(d)
check("リセットが通る", j.get("ok") is True)
check("3面とも出陣不可へ", not any(j.get("boards_ok", {}).get(reg) for reg in regs))
r, d = req("GET", "/api/deckdata", cookie=sid)
D3 = json.loads(d)
check("登録デッキは空", not D3["decks"])
check("保存庫は残る", any(s["name"] == "残る保存" for s in D3.get("saved", [])))

print("[6] 認証")
r, d = req("POST", "/api/deck_all", body={"boards": []})
check("未ログインは401", r.status == 401)
r, d = req("POST", "/api/deck_reset", body={})
check("リセットも401", r.status == 401)

print()
print("NG {} 件".format(len(FAIL)) if FAIL else "全部 OK")
sys.exit(1 if FAIL else 0)
