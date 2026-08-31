# -*- coding: utf-8 -*-
"""武将バージョン管理の受け入れ試験（§7.135）: 版一覧・重複禁止・顔絵の
版差し替え・デッキ復元・試合記録・旧データ互換。"""
import http.client, json, os, sys, threading, time
import urllib.parse
from http.server import ThreadingHTTPServer

import tempfile
DB = os.path.join(tempfile.mkdtemp(prefix="sangoku-test-"), "players.db")
PORT = 8988
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

req("GET", "/api/state")
r, d = req("POST", "/api/login", body={"new": "武将版検査"})
sid = (r.getheader("Set-Cookie") or "").split(";")[0]
req("POST", "/api/dev_senki", cookie=sid, body={})   # 全登用（試験用）
r, d = req("GET", "/api/deckdata", cookie=sid)
D = json.loads(d)
regs = [x["name"] for x in D["regs"]]
me_id = json.loads(req("GET", "/api/state", cookie=sid)[1])["me"]["id"]

# 面を組み立てる小道具。同じ人物を二度使わないよう used で追う。
used = set()
def build(front_n, rear_n, exclude=()):
    f, b = [], []
    for c in sorted((x for x in D["roster"] if x["typ"] != "弓兵"),
                     key=lambda c: c["cost"]):
        if c["person"] in used or c["name"] in exclude or len(f) >= front_n:
            continue
        f.append(c); used.add(c["person"])
    for c in sorted((x for x in D["roster"] if x["typ"] == "弓兵"),
                     key=lambda c: c["cost"]):
        if c["person"] in used or c["name"] in exclude or len(b) >= rear_n:
            continue
        b.append(c); used.add(c["person"])
    return [c["name"] for c in f + b]

print("[A] 既存データのみでの通常動作（回帰）")
r, d = req("POST", "/api/deck", cookie=sid,
           body={"reg": regs[0], "form": "魚鱗", "cards": build(3, 3)})
check("版を持たない武将だけの通常保存が通る", json.loads(d).get("ok"), d)

print("[B] 呂布v1/v2の両方がrosterに出る")
lb = [c for c in D["roster"] if c["person"] == "呂布"]
check("呂布が2枚rosterに出る", len(lb) == 2, str([c["name"] for c in lb]))
lb_v1 = next((c for c in lb if c["version"] == 1), None)
lb_v2 = next((c for c in lb if c["version"] == 2), None)
check("版番号が1と2で出る", bool(lb_v1) and bool(lb_v2),
      str([(c["name"], c.get("version")) for c in lb]))
check("既存120人はversionが出ても1", all(
    c["version"] == 1 for c in D["roster"] if c["person"] != "呂布"))

print("[C] 同一人物の別バージョンは同時使用不可")
# 魚鱗は3前衛/3後衛（F.FORM_NAME[3] == "魚鱗"）。呂布(騎兵)が前衛を1枠
# 使うので、残りは前衛2・後衛3で埋める。
fill5 = build(2, 3, exclude=(lb_v1["name"], lb_v2["name"]))
r, d = req("POST", "/api/deck", cookie=sid,
           body={"reg": regs[1], "form": "魚鱗",
                 "cards": [lb_v1["name"], lb_v2["name"]] + fill5})
j = json.loads(d)
check("同じ面に呂布v1+v2は保存できない（面内）", not j.get("ok"), d)
check("エラー文に同一人物の説明が出る",
      any("同一人物" in e for e in j.get("errors", [])), d)

r, d = req("POST", "/api/deck", cookie=sid,
           body={"reg": regs[1], "form": "魚鱗", "cards": [lb_v1["name"]] + fill5})
check("（準備）呂布v1を面2へ登録できる", json.loads(d).get("ok"), d)
fill5b = build(2, 3, exclude=(lb_v1["name"], lb_v2["name"]))
r, d = req("POST", "/api/deck", cookie=sid,
           body={"reg": regs[2], "form": "魚鱗", "cards": [lb_v2["name"]] + fill5b})
j = json.loads(d)
check("面2に呂布v1がいると面3へ呂布v2を追加できない（面またぎ・逆方向）",
      not j.get("ok") and any("同一人物" in e for e in j.get("errors", [])), d)

boards = [{"reg": regs[0], "form": "魚鱗", "cards": build(3, 3)},
          {"reg": regs[1], "form": "魚鱗",
           "cards": [lb_v1["name"], lb_v2["name"]] + fill5},
          {"reg": regs[2], "form": "魚鱗", "cards": build(3, 3)}]
r, d = req("POST", "/api/deck_all", cookie=sid, body={"boards": boards})
j = json.loads(d)
check("一斉登録も面内の呂布v1+v2を拒否する", not j.get("ok"), d)

print("[D] 顔絵がバージョンで変わる")
r1, d1 = req("GET", "/portrait/" + urllib.parse.quote(lb_v1["name"]))
r2, d2 = req("GET", "/portrait/" + urllib.parse.quote(lb_v2["name"]))
check("両方とも200で返る", r1.status == 200 and r2.status == 200,
      f"{r1.status} / {r2.status}")
check("roster側のportraitUrlも版で違う", lb_v1["portraitUrl"] != lb_v2["portraitUrl"],
      str((lb_v1["portraitUrl"], lb_v2["portraitUrl"])))
# 呂布には既に人物名の実絵（呂布.png）があるので、版専用ファイルを置かない
# 限りv1/v2は同じ絵にフォールバックするのが正しい（後方互換）。
check("専用絵が無い間はv1/v2とも同じ絵（人物名の絵へフォールバック）",
      d1 == d2, "専用絵が無いのに違う内容だった")
PORTRAITS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "sim", "webui", "portraits")
tmp_portrait = os.path.join(PORTRAITS_DIR, lb_v2["name"] + ".svg")
try:
    with open(tmp_portrait, "w", encoding="utf-8") as f:
        f.write("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    r3, d3 = req("GET", "/portrait/" + urllib.parse.quote(lb_v2["name"]))
    check("版専用ファイルを置くとv2だけそちらが優先される",
          r3.status == 200 and d3 != d1, f"{r3.status}")
    r4, d4 = req("GET", "/portrait/" + urllib.parse.quote(lb_v1["name"]))
    check("v1は引き続き人物名の絵のまま（v2専用ファイルに引っ張られない）",
          d4 == d1, "v1の絵が変わってしまった")
finally:
    if os.path.exists(tmp_portrait):
        os.remove(tmp_portrait)

print("[E] デッキ保存→再読込でバージョンが保持される")
r, d = req("GET", "/api/deckdata", cookie=sid)
D2 = json.loads(d)
saved_cards = D2["decks"].get(regs[1], {}).get("cards", [])
check("面2に呂布v1が残っている（保存の復元）", lb_v1["name"] in saved_cards, str(saved_cards))
check("面2に呂布v2は入っていない", lb_v2["name"] not in saved_cards, str(saved_cards))

print("[F] 試合記録にperson/version/rule_versionが乗る")
cards = M._roster_cards()
lb_v2_card = next(c for c in cards if c.name == lb_v2["name"])
side_fill = [c for c in cards if c.name != lb_v1["name"] and c.name != lb_v2["name"]
             and c.typ != "arc"][:2]
side_arc = [c for c in cards if c.typ == "arc"][:3]
army = F.Army((lb_v2_card,) + tuple(side_fill) + tuple(side_arc), F.FORM_STANDARD)
cx = P.connect(DB)
now = int(time.time())
bid = P.record_battle(
    cx, "free", regs[0], me_id, "dummy-x", 99,
    json.dumps(PL.snap_army(army), ensure_ascii=False),
    json.dumps(PL.snap_army(army), ensure_ascii=False),
    "2026-08", now, "○", rule_version=F.BATTLE_RULE_VERSION)
r, d = req("GET", f"/api/replay?id={bid}", cookie=sid)
rep = json.loads(d)
check("リプレイにrule_versionが乗る", rep.get("rule_version") == F.BATTLE_RULE_VERSION,
      str(rep.get("rule_version")))
mine_rows = (rep.get("games") or [{}])[0].get("mine", [])
lb_row = next((row for row in mine_rows if row.get("person") == "呂布"), None)
check("リプレイの武将行にperson/versionが乗る",
      lb_row is not None and lb_row.get("version") == 2, str(lb_row))

print("[G] 旧データ（rule_version無し）でも壊れない")
old_snap = json.dumps(PL.snap_army(army), ensure_ascii=False)
old_bid = P.record_battle(cx, "free", regs[0], me_id, "dummy-y", 100,
                          old_snap, old_snap, "2026-08", now, "○")  # rule_version省略＝旧記録
r, d = req("GET", f"/api/replay?id={old_bid}", cookie=sid)
old_rep = json.loads(d)
check("rule_version無しの旧記録は\"1.0\"に補完される",
      old_rep.get("rule_version") == "1.0", str(old_rep.get("rule_version")))
check("旧記録のリプレイ自体はエラーにならない", "error" not in old_rep, str(old_rep))

print()
if FAIL:
    print("失敗:", FAIL); sys.exit(1)
print("全部通った")
