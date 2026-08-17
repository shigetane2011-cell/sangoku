# -*- coding: utf-8 -*-
"""sim/web.py -- ローカルWeb版（ブラウザで遊ぶ・§7.42）

    python3 -m sim.web            # → http://localhost:8035

**手元専用。** 認証が無いので、このまま外へ公開しないこと。公開の段階では
外部認証（パスワードを自分で持たない）とマネージド DB へ移す（players.py 冒頭）。
依存は標準ライブラリだけ。画面は sim/webui/（app.css / app.js）。

盤面・マッチ・順位表・実況・検証は sim/ の実装をそのまま呼ぶ。**このファイルは
ルーティングと JSON への詰め替えだけ**を持つ（同じ量の定義を2箇所に持たない）。
"""

from __future__ import annotations

import html
import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import field as F
from . import ladder as L
from . import match as M
from . import play as PL
from . import players as P
from . import rosterdata as R

PORT = 8035
WEBUI = os.path.join(os.path.dirname(__file__), "webui")

VIEWS = {"/": "home", "/deck": "deck", "/replays": "replays", "/replay": "replay"}

SHELL = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>三国布陣</title>
<link rel="stylesheet" href="/static/app.css">
<body data-view="{view}">
<div class="wrap"><div id="app"><p class="muted">読み込み中……</p></div></div>
<script src="/static/app.js"></script>
"""


def _trait_names():
    return {t["キー"]: t["名前"] if t["名前"] != t["キー"] else
            {"vanguard": "陣頭", "vs_wei": "対魏", "vs_shu": "対蜀",
             "vs_go": "対呉"}.get(t["キー"], t["キー"])
            for t in R.traits()}


def _roster_json():
    """武将一覧（§7.47 の開示設計）。

    見せるのは**プレイヤーが支払う・選ぶ判断に使う量**だけ: 能力値・技の中身・
    特性の中身・ゲージ。内部帳簿（能力値コスト・効果予算・総合値・実力比・
    値段表）は出さない — 正解表になって編成の探索が死ぬため。
    """
    sk = {s["技名"]: s for s in R.skills()}
    tr = {t["キー"]: t for t in R.traits()}
    names_jp = _trait_names()
    out = []
    for g in R.generals():
        s = sk.get(g["必殺技"], {})
        traits = []
        conds = {"ally_retreat": "味方の隊が崩れた時",
                 "enemy_retreat": "敵の隊が崩れた時",
                 "self_low_hp": "自身の兵が減った時",
                 "ally_skill": "味方が必殺技を放った時"}
        import re as _re
        for k in R.traits_of(g):
            t = tr.get(k, {})
            note = t.get("備考") or ""
            kind = t.get("型", "")
            desc = t.get("効果", "")
            cond = ""
            if kind == "誘発":
                m = _re.search(r"(\w+) で発動", note)
                cond = conds.get(m.group(1) if m else "", "")
                m = _re.search(r"1戦(\d+)回", note)
                if m:
                    cond += "・1戦{}回まで".format(m.group(1))
            # 常在型の数字は field.py の定数から注入（定義を2箇所に持たない）
            if k == "vanguard":
                desc = "前衛に置くと兵力 +{:.1%}（後衛では働かない）".format(
                    F.VANGUARD_MEN)
            elif k in F.FACTION_OF:
                desc = "{}の武将への与ダメージ +{:.0%}（群雄にも当たる）".format(
                    F.FACTION_OF[k], F.VS_FACTION)
            traits.append({"key": k, "name": names_jp.get(k, k),
                           "kind": kind, "cond": cond, "desc": desc})
        out.append({
            "name": g["名前"], "person": g["人物"], "cost": float(g["コスト"]),
            "typ": g["兵種"], "faction": g["勢力"], "role": g["役割"],
            # 武勇・知略は**歴史イメージの演出値**（1〜100・盤面に不干渉）。
            # エンジン内部の武力・知力は帳簿なので出さない（§7.47）。
            "men": int(float(g["兵力"])), "might": int(g["武勇"]),
            "wits": int(g["知略"]), "atk": round(float(g["攻撃力"])),
            "dfn": round(float(g["防御力"])),
            "skill": g["必殺技"], "skill_desc": s.get("効果", ""),
            "gauge_cost": g["消費ゲージ%"], "gauge_rate": g["ゲージ上昇率"],
            "gauge_init": g["初期ゲージ"],
            "traits": traits, "trait": g["固有特性"],
            "quote": g.get("台詞", ""),
        })
    return out


class App(BaseHTTPRequestHandler):

    # ------------------------------------------------------------ 低レベル
    def _send(self, body: bytes, code: int = 200,
              ctype: str = "text/html; charset=utf-8", cookie: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200, cookie: str = "") -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode(), code,
                   "application/json; charset=utf-8", cookie)

    def _cx(self):
        return P.connect()

    def _me(self, cx):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "pid":
                return P.get(cx, v)
        return None

    def log_message(self, *a):
        pass

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            if url.path == "/favicon.ico":
                svg = ("<svg xmlns='http://www.w3.org/2000/svg' "
                       "viewBox='0 0 16 16'><text y='13' font-size='13'>"
                       "⚔️</text></svg>").encode()
                return self._send(svg, 200, "image/svg+xml")
            if url.path.startswith("/static/"):
                return self._static(url.path[len("/static/"):])
            if url.path in VIEWS:
                return self._send(SHELL.format(view=VIEWS[url.path]).encode())
            if url.path == "/api/state":
                return self._api_state()
            if url.path == "/api/deckdata":
                return self._api_deckdata()
            if url.path == "/api/replays":
                return self._api_replays()
            if url.path == "/api/replay":
                return self._api_replay(q)
            self._send(b"not found", 404, "text/plain")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == "/api/login":
                return self._api_login(body)
            if url.path == "/api/deck":
                return self._api_deck(body)
            if url.path == "/api/round":
                return self._api_round()
            if url.path == "/api/onsho":
                return self._api_onsho(body)
            if url.path == "/api/savedeck":
                return self._api_savedeck(body)
            if url.path == "/api/deldeck":
                return self._api_deldeck(body)
            self._send(b"not found", 404, "text/plain")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _static(self, name: str):
        path = os.path.join(WEBUI, os.path.basename(name))
        if not os.path.exists(path):
            return self._send(b"not found", 404, "text/plain")
        ctype = "text/css" if name.endswith(".css") else "text/javascript"
        with open(path, "rb") as f:
            self._send(f.read(), 200, ctype + "; charset=utf-8")

    # ---------------------------------------------------------------- API
    def _api_state(self):
        cx = self._cx()
        me = self._me(cx)
        players = P.all_players(cx)
        names = {p.id: p.display_name for p in players}
        kinds = {p.id: p.kind for p in players}
        boards = []
        for bn in L.BOARDS:
            r = P.board_ratings(cx, bn)
            b = PL.load_board(cx, bn)
            table = [{"rank": i + 1, "name": names.get(pid, "?"),
                      "kind": kinds.get(pid, "dummy"),
                      "rating": b.get(pid), "games": b.games.get(pid, 0),
                      "me": bool(me and pid == me.id)}
                     for i, pid in enumerate(b.order(list(r)))]
            boards.append({"name": bn, "round": P.board_round(cx, bn),
                           "table": table})
        entry_ok = False
        heifu = None
        onsho = None
        if me:
            cards = M._roster_cards()
            entry, errs = PL.entry_of(cx, cards, me.id, me.display_name)
            entry_ok = not errs
            n, wait = P.heifu(cx, me.id, int(time.time()))
            heifu = {"count": n, "cap": P.HEIFU_CAP, "next_in": wait}
            import datetime
            names_jp = _trait_names()
            key = P.daily_onsho(cx, me.id, list(names_jp),
                                datetime.date.today().isoformat())
            if key:
                onsho = {"key": key, "name": names_jp.get(key, key)}
            # 告知（次の対戦相手と、その陣形）。§3 の駆け引きの入口。
            entries = PL.ensure_dummies(cx, cards)
            if entry_ok:
                entries[me.id] = entry
            names2 = {p.id: p.display_name for p in players}
            for bd in boards:
                rnd, pairs = PL.announce(cx, entries, bd["name"])
                mine = next((pr for pr in pairs if me.id in pr), None)
                if mine is None:
                    continue
                foe = mine[1] if mine[0] == me.id else mine[0]
                fe = entries.get(foe)
                reg = (PL.REG_NAMES.index(bd["name"])
                       if bd["name"] in PL.REG_NAMES else None)
                if fe is None:
                    forms = "?"
                elif reg is not None:
                    forms = F.FORM_NAME.get(fe.unit(reg).form.n_front, "?")
                else:
                    forms = "・".join(F.FORM_NAME.get(u.form.n_front, "?")
                                      for u in fe.units)
                bd["next"] = {"foe": names2.get(foe, "?"), "forms": forms,
                              "round": rnd + 1}
        self._json({
            "me": {"id": me.id, "name": me.display_name} if me else None,
            "humans": [{"id": p.id, "name": p.display_name}
                       for p in players if p.kind == P.HUMAN],
            "boards": boards, "entry_ok": entry_ok, "heifu": heifu,
            "onsho": onsho,
        })

    def _api_login(self, body):
        cx = self._cx()
        pid = body.get("pid", "")
        new = (body.get("new") or "").strip()
        if new:
            pl = P.register(cx, new, kind=P.HUMAN,
                            email="local+{}@example.invalid".format(P.new_id()[:8]))
            pid = pl.id
        if P.get(cx, pid) is None:
            return self._json({"ok": False}, 400)
        self._json({"ok": True}, cookie="pid={}; Path=/; SameSite=Lax".format(pid))

    def _api_deckdata(self):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        decks = {}
        for reg, (raw, fm) in P.decks_of(cx, me.id).items():
            army, errs = PL.parse_deck(cards, raw, fm)
            decks[reg] = {"form": F.FORM_ALIAS.get(fm, fm),
                          "cards": [c.name for c in army.cards] if army else []}
        _, entry_errors = PL.entry_of(cx, cards, me.id, me.display_name)
        from . import design as D
        names_jp = _trait_names()
        onsho = [{"id": r["id"], "key": r["trait_key"],
                  "name": names_jp.get(r["trait_key"], r["trait_key"]),
                  "value": round(D.trait_value(r["trait_key"]), 2),
                  "general": r["general_name"]}
                 for r in P.owned_traits(cx, me.id)]
        saved = []
        for r in P.saved_decks(cx, me.id):
            army, _ = PL.parse_deck(cards, r["cards"], r["formation"])
            saved.append({
                "id": r["id"], "name": r["name"], "reg": r["regulation"],
                "form": F.FORM_ALIAS.get(r["formation"], r["formation"]),
                "cards": [c.name for c in army.cards] if army
                         else [x.strip() for x in r["cards"].split(F.TRAIT_SEP)
                               if x.strip()],
                "cost": army.total_cost() if army else None,
            })
        self._json({
            "regs": [{"name": n, "cap": c} for n, c in M.REGULATIONS],
            "roster": _roster_json(),
            "decks": decks,
            "entry_errors": entry_errors,
            "onsho": onsho,
            "saved": saved,
        })

    def _api_deck(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        reg = M.REG_ALIAS.get(body.get("reg", ""), body.get("reg", ""))
        fm = body.get("form", "魚鱗")
        names = [str(x) for x in body.get("cards", [])]
        raw = F.TRAIT_SEP.join(names)      # 「、」区切り＝CLI・DB と同じ表現
        army, errs = PL.parse_deck(cards, raw, fm)
        caps = dict(M.REGULATIONS)
        if reg not in caps:
            errs.append("そのレギュレーションは無い")
        if army is not None and not errs:
            errs += M.placement_errors(army)
            if army.total_cost() > caps[reg] + 1e-9:
                errs.append("合計コスト {:g} が上限 {:g} を超えている".format(
                    army.total_cost(), caps[reg]))
            if len(army.cards) != M.UNIT_SIZE:
                errs.append("{}人必要（いまは{}人）".format(
                    M.UNIT_SIZE, len(army.cards)))
        if errs:
            return self._json({"ok": False, "errors": errs})
        P.set_deck(cx, me.id, reg, raw, fm)
        _, entry_errors = PL.entry_of(cx, cards, me.id, me.display_name)
        self._json({"ok": True, "cost": army.total_cost(),
                    "entry_errors": entry_errors})

    def _api_round(self):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        entry, errs = PL.entry_of(cx, cards, me.id, me.display_name)
        if errs:
            return self._json({"error": "先に編成を直す: " + "／".join(errs)}, 400)
        if not P.spend_heifu(cx, me.id, 3, int(time.time())):
            return self._json({"error": "兵符が足りない（BO1の3戦で3枚要る。"
                               "30分に1枚回復する）"}, 402)
        entries = PL.ensure_dummies(cx, cards)
        entries[me.id] = entry
        results = []
        for bn in L.BOARDS:
            before = PL.load_board(cx, bn).get(me.id)
            b, rnd, pairs = PL.run_round(cx, cards, entries, bn)
            mine = next((pr for pr in pairs if me.id in pr), None)
            if mine is None:
                continue
            foe = mine[1] if mine[0] == me.id else mine[0]
            seed = L.battle_seed(bn, rnd, mine[0], mine[1])
            me_first = mine[0] == me.id
            score = ""
            if b.reg is None:
                r = M.play(entries[mine[0]], entries[mine[1]], 0.5, seed=seed)
                wa = r["wins_a"] if me_first else r["wins_b"]
                wb = r["wins_b"] if me_first else r["wins_a"]
                verdict = "勝ち" if wa > wb else ("負け" if wb > wa else "引き分け")
                score = "{:g}-{:g}".format(wa, wb)
            else:
                r = M.play_one(entries[mine[0]], entries[mine[1]], b.reg, 0.5,
                               seed=seed)
                w = r["winner"]
                verdict = ("勝ち" if (w == "A") == me_first else "負け") \
                    if w != "引き分け" else "引き分け"
            row = cx.execute(
                "SELECT id FROM matches WHERE board=? AND round=?"
                " AND (pid_a=? OR pid_b=?)", (bn, rnd, me.id, me.id)).fetchone()
            names = {p.id: p.display_name for p in P.all_players(cx)}
            results.append({
                "board": bn, "rnd": rnd, "foe": names.get(foe, "?"),
                "verdict": verdict, "score": score,
                "rating": b.get(me.id), "delta": b.get(me.id) - before,
                "rank": b.order(list(entries)).index(me.id) + 1,
                "match_id": row["id"] if row else None,
            })
        self._json({"results": results})

    def _api_onsho(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        oid = int(body.get("owned_id", 0))
        gen = (body.get("general") or "").strip()
        mine = {r["id"]: r for r in P.owned_traits(cx, me.id)}
        if oid not in mine:
            return self._json({"ok": False, "errors": ["その恩賞は持っていない"]})
        if not gen:
            P.set_trait(cx, me.id, oid, "", None)
            return self._json({"ok": True})
        if not any(g["名前"] == gen for g in R.generals()):
            return self._json({"ok": False, "errors": ["その武将はいない"]})
        slot = P.free_slot(cx, me.id, gen)
        if slot is None:
            return self._json({"ok": False,
                               "errors": ["{} の軍功枠は3つとも埋まっている".format(gen)]})
        P.set_trait(cx, me.id, oid, gen, slot)
        self._json({"ok": True})

    def _api_savedeck(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        name = (body.get("name") or "").strip()
        reg = M.REG_ALIAS.get(body.get("reg", ""), body.get("reg", ""))
        if not name:
            return self._json({"ok": False, "errors": ["名前を付ける"]})
        if len(name) > 24:
            return self._json({"ok": False, "errors": ["名前は24字まで"]})
        if reg not in dict(M.REGULATIONS):
            return self._json({"ok": False, "errors": ["そのレギュレーションは無い"]})
        cards = [str(x) for x in body.get("cards", [])]
        P.save_deck_as(cx, me.id, name, reg, F.TRAIT_SEP.join(cards),
                       body.get("form", "魚鱗"))
        self._json({"ok": True})

    def _api_deldeck(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        P.delete_saved_deck(cx, me.id, int(body.get("id", 0)))
        self._json({"ok": True})

    def _api_replays(self):
        cx = self._cx()
        names = {p.id: p.display_name for p in P.all_players(cx)}
        out = []
        for bn in L.BOARDS:
            ms = P.matches_of(cx, bn, limit=16)
            out.append({"name": bn, "matches": [
                {"id": m["id"], "round": m["round"],
                 "a": names.get(m["pid_a"], "?"), "b": names.get(m["pid_b"], "?")}
                for m in ms]})
        self._json({"boards": out})

    def _api_replay(self, q):
        cx = self._cx()
        me = self._me(cx)
        mid = int(q.get("id", 0))
        row = cx.execute("SELECT * FROM matches WHERE id = ?", (mid,)).fetchone()
        if row is None:
            return self._json({"error": "その記録は無い"}, 404)
        m = dict(row)
        cards = M._roster_cards()
        names = {p.id: p.display_name for p in P.all_players(cx)}
        entries = PL.ensure_dummies(cx, cards)
        for p in P.all_players(cx, kind=P.HUMAN):
            e, es = PL.entry_of(cx, cards, p.id, p.display_name)
            if not es:
                entries[p.id] = e
        a, b = entries.get(m["pid_a"]), entries.get(m["pid_b"])
        if a is None or b is None:
            return self._json({"error": "編成を再構成できない（登録が変わった）"}, 410)
        me_first = not (me and me.id == m["pid_b"])
        games = []
        if m["board"] in PL.REG_NAMES:
            reg = PL.REG_NAMES.index(m["board"])
            cap = M.REGULATIONS[reg][1]
            g = PL.replay_data(M.with_surplus(a.unit(reg), cap),
                               M.with_surplus(b.unit(reg), cap),
                               0.5, m["seed"], me_first)
            g["label"] = m["board"]
            games.append(g)
        else:
            for i, (label, cap) in enumerate(M.REGULATIONS):
                g = PL.replay_data(M.with_surplus(a.unit(i), cap),
                                   M.with_surplus(b.unit(i), cap),
                                   0.5, m["seed"] * 3 + i, me_first)
                g["label"] = label
                games.append(g)
        mine_id = m["pid_a"] if me_first else m["pid_b"]
        foe_id = m["pid_b"] if me_first else m["pid_a"]
        self._json({
            "title": "{} 対 {}".format(names.get(m["pid_a"], "?"),
                                       names.get(m["pid_b"], "?")),
            "board": m["board"], "round": m["round"],
            "mine_name": names.get(mine_id, "?"),
            "foe_name": names.get(foe_id, "?"),
            "me_first": me_first, "games": games,
        })


def main() -> None:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), App)
    print("http://localhost:{}  （Ctrl+C で終了）".format(PORT))
    srv.serve_forever()


if __name__ == "__main__":
    main()
