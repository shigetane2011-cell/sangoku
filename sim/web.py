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
import re
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import field as F
from . import ladder as L
from . import match as M
from . import play as PL
from . import players as P
from . import rosterdata as R
from . import senki as SK

PORT = int(os.environ.get("SANGOKU_PORT", "8035"))
# 攻勢の表示（§7.56）が仮定する「標準的な鎧」= 3兵種の平均。
_DEF_MEAN = sum(F.DEF_BY_TYPE.values()) / len(F.DEF_BY_TYPE)
WEBUI = os.path.join(os.path.dirname(__file__), "webui")

VIEWS = {"/": "home", "/senki": "senki", "/deck": "deck",
         "/replays": "replays", "/replay": "replay"}

# 起動時刻。これより新しい .py がディスクにあれば「サーバが古い」— ファイルを
# 差し替えたのに再起動していない状態。**新旧混在は静かに壊れる**（版ずれの
# JSがAPIの形の違いで例外を出し、画面の枠が巻き添えで消える事故が実際に
# 起きた）ので、画面のバナーで再起動を促す。
_BOOT_TIME = time.time()


def _server_stale() -> bool:
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        return any(os.path.getmtime(f) > _BOOT_TIME
                   for f in glob.glob(os.path.join(here, "*.py")))
    except OSError:
        return False

SHELL = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>三国布陣</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Yuji+Syuku&family=Shippori+Mincho+B1:wght@600;800&family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap">
<link rel="stylesheet" href="/static/app.css">
<body data-view="{view}">
<div class="wrap"><div id="app"><p class="muted">読み込み中……</p></div></div>
<script src="/static/app.js"></script>
"""
# フォントは Google Fonts（すべて OFL）: 題字・判＝Yuji Syuku（筆文字）、
# 見出し・武将名＝Shippori Mincho B1（太骨格の明朝）、本文＝Zen Kaku Gothic
# New。**オフラインでは読み込みに失敗してよい** — CSS のフォールバック
# （ヒラギノ明朝/游明朝/システムゴシック）へ自然に落ちる。


def _trait_names():
    """特性キー → 表示名。**traits.csv の 名前 列が一次**（旧実装はここに
    別の対応表を持っていて、同じ量の定義が2箇所になっていた）。"""
    return {t["キー"]: t["名前"] for t in R.traits()}


_MOD_JP = {"攻撃力": "攻撃力", "命中率": "攻撃力（命中）", "防御力": "防御力",
           "移動速度": "移動速度", "気勢": "気勢",
           "必殺技防御": "必殺技防御", "必殺技反射": "必殺技反射",
           "通常攻撃防御": "通常攻撃防御"}


def _skill_display(g, sk_row) -> str:
    """効果文を**プレイヤーの読める量**へ焼き直す（§7.47）。

    「威力976%」は内部の武力・知力（隠した帳簿）に掛かる係数なので、そのまま
    見せても読めない。本物の式（SKILL_SCALE × 威力 × 武知の混合）でその武将の
    実数へ換算し、時間は実況と同じ「分」（§7.36 の表示比率）で語る。
    """
    import re as _re
    sk = F._parse_skill(sk_row.get("効果", ""), sk_row.get("対象", ""))
    v = F.SKILL_WITS.get(sk.kind, 0.0)
    # g が無い（恩賞パネルなど、まだ持ち主が決まっていない）ときは実数に
    # できないので、量がセット先しだいであることを言う。
    coef = (float(g["武力"]) * (1.0 - v) + float(g["知力"]) * v) if g else None
    parts = []
    if sk.power > 0.0:
        if coef is None:
            parts.append("損害（量は持ち主の武将しだい）")
        elif sk.dur > 0.0:
            parts.append("延焼 毎分約{:,.0f}人（{:.0f}分間）".format(
                F.per_min(F.SKILL_SCALE * sk.power * coef), F.mins(sk.dur)))
        else:
            # 「守りで目減り」の注記は毎行に付けず、凡例に1回書く（冗長の指摘）
            parts.append("損害 約{:,.0f}人".format(
                F.SKILL_SCALE * sk.power * coef))
    if sk.heal > 0.0:
        if coef is None:
            parts.append("回復（量は持ち主の武将しだい）")
        else:
            total = F.HEAL_SCALE * sk.heal * coef * (sk.dur if sk.dur > 0 else 1.0)
            parts.append("回復 約{:,.0f}人".format(total))
    raw = sk_row.get("効果", "")
    for m in _re.finditer(r"(攻撃力|命中率|防御力|移動速度|気勢|必殺技防御|必殺技反射|通常攻撃防御)"
                          r"\s*([+-]\d+)%（(\d+)秒）", raw):
        parts.append("{} {}%（{:.0f}分間）".format(
            _MOD_JP[m.group(1)], m.group(2), F.mins(float(m.group(3)))))
    m = _re.search(r"混乱\s*(\d+)%（(\d+)秒）", raw)
    if m:
        parts.append("混乱 {}%（{:.0f}分間）".format(m.group(1), F.mins(float(m.group(2)))))
    m = _re.search(r"行動阻害\s*(\d+)秒", raw)
    if m:
        parts.append("足止め {:.0f}分間".format(F.mins(float(m.group(1)))))
    m = _re.search(r"代償\s*兵力(\d+)%", raw)
    if m:
        parts.append("代償 放つたびに自隊の残り兵力の{}%を失う".format(m.group(1)))
    m = _re.search(r"必殺技打消し（(\d+)秒）", raw)
    if m:
        parts.append("打消し 構えた隊を狙う敵の必殺技を丸ごと無効化（{:.0f}分間）"
                     .format(F.mins(float(m.group(1)))))
    m = _re.search(r"ゲージ付与", raw)
    if m:
        parts.append("味方のゲージを進める")
    return " ＋ ".join(parts) if parts else raw


_TRAIT_CONDS = {"ally_retreat": "味方の隊が崩れた時",
                "enemy_retreat": "敵の隊が崩れた時",
                "self_low_hp": "自身の兵が減った時",
                "ally_skill": "味方が必殺技を放った時"}


def _trait_brief(g, key, t):
    """特性1つの表示（説明文, 条件）。武将一覧と軍功枠の**両方がこれを使う**
    （同じ特性が場所で違う説明にならないように）。g=None は持ち主未定。"""
    import re as _re
    note = t.get("備考") or ""
    kind = t.get("型", "")
    desc = t.get("効果", "")
    cond = ""
    if kind == "誘発":
        m = _re.search(r"(\w+) で発動", note)
        cond = _TRAIT_CONDS.get(m.group(1) if m else "", "")
        m = _re.search(r"1戦(\d+)回", note)
        if m:
            cond += "・1戦{}回まで".format(m.group(1))
        # 効果は必殺技と**同じ器で発動する**ので、表示も同じ換算を通す:
        # 回復は実数、時間は分、命中率は攻撃力（命中）。対象が自分以外なら
        # 明示する — 書かないと全部が自分バフに読める（テストプレイの指摘）。
        m = _re.search(r"対象 ([^/]+)", note)
        target = (m.group(1).strip() if m else "自分")
        desc = _skill_display(g, {"効果": desc, "対象": target})
        if target != "自分":
            cond = "対象 {}・{}".format(target, cond)
    # 常在型の数字は field.py の定数から注入（定義を2箇所に持たない）
    if key == "vanguard":
        # 「兵力+4.5%」だけだと本陣（全軍+3%）と並んだとき誰の兵力か
        # 曖昧に読める（テストプレイの指摘）。自分の隊、と言い切る。
        desc = "前衛に置くと自分の隊の兵力 +{:.1%}（後衛では働かない）".format(
            F.VANGUARD_MEN)
    elif key == "command":
        desc = ("全軍の兵力 +{:.0%}。ただしこの隊の残存が{:.0%}を"
                "割ると全軍が総崩れ（弓兵専用・デッキに1人まで）"
                ).format(F.COMMAND_MEN, F.COMMAND_ROUT)
    elif key in F.FACTION_OF:
        desc = "{}の武将への与ダメージ +{:.0%}（群雄にも当たる）".format(
            F.FACTION_OF[key], F.VS_FACTION)
    return desc, cond


def _roster_json(only=None):
    """武将一覧（§7.47 の開示設計）。

    見せるのは**プレイヤーが支払う・選ぶ判断に使う量**だけ: 能力値・技の中身・
    特性の中身・ゲージ。内部帳簿（能力値コスト・効果予算・総合値・実力比・
    値段表）は出さない — 正解表になって編成の探索が死ぬため。

    only を渡すと**解放済みの人物だけ**返す（§7.60。未登用は姿も見せない —
    戦記で出会うのが初対面になる）。
    """
    sk = {s["技名"]: s for s in R.skills()}
    tr = {t["キー"]: t for t in R.traits()}
    names_jp = _trait_names()
    out = []
    for g in R.generals():
        if only is not None and g["人物"] not in only:
            continue
        s = sk.get(g["必殺技"], {})
        traits = []
        for k in R.traits_of(g):
            t = tr.get(k, {})
            desc, cond = _trait_brief(g, k, t)
            traits.append({"key": k, "name": names_jp.get(k, k),
                           "kind": t.get("型", ""), "cond": cond, "desc": desc})
        out.append({
            "name": g["名前"], "person": g["人物"], "cost": float(g["コスト"]),
            "typ": g["兵種"], "faction": g["勢力"], "role": g["役割"],
            # 武勇・知略は**歴史イメージの演出値**（1〜100・盤面に不干渉）。
            # エンジン内部の武力・知力は帳簿なので出さない（§7.47）。
            "men": int(float(g["兵力"])), "might": int(g["武勇"]),
            "wits": int(g["知略"]),
            # 攻勢・守勢（§7.56）: 生の攻撃力・防御力は役割5種・兵種3種の
            # 固定値で差が見えない（テストプレイの指摘）。**盤面の実式**で
            # 実数へ焼き直す — 攻勢は毎分の削り（標準的な鎧の相手・開幕
            # 基準）、守勢は鎧込みで受けきれる実効兵力。嘘の飾り値は
            # 作らない（§7.47。戦闘ログと桁が合う量だけを見せる）。
            "atk_pm": round(F.per_min(
                float(g["兵力"]) * F.LETHALITY
                * float(g["攻撃力"]) / F.BASE_ATK
                / R.INTERVAL[g["兵種"]]
                * 100.0 / (100.0 + _DEF_MEAN))),
            "eff_men": round(float(g["兵力"])
                             * (100.0 + float(g["防御力"])) / 100.0),
            "atk": round(float(g["攻撃力"])),
            "dfn": round(float(g["防御力"])),
            "spear": bool((g.get("槍") or "").strip()),
            "skill": g["必殺技"], "skill_desc": _skill_display(g, s),
            "skill_target": s.get("対象", ""),
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
            if url.path.startswith("/icons/"):
                return self._icon(url.path[len("/icons/"):])
            if url.path.startswith("/portrait/"):
                return self._portrait(
                    urllib.parse.unquote(url.path[len("/portrait/"):]))
            if url.path in VIEWS:
                return self._send(SHELL.format(view=VIEWS[url.path]).encode())
            if url.path == "/api/state":
                return self._api_state()
            if url.path == "/api/deckdata":
                return self._api_deckdata()
            if url.path == "/api/senki":
                return self._api_senki()
            if url.path == "/api/senki_prep":
                return self._api_senki_prep(q)
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
            if url.path == "/api/attack":
                return self._api_attack(body)
            if url.path == "/api/senki_fight":
                return self._api_senki_fight(body)
            if url.path == "/api/senki_lap":
                return self._api_senki_lap(body)
            if url.path == "/api/dev_senki":
                # 手元の試験用: 戦記を全クリア扱いにして全員登用。公開版では消す。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                SK.set_cleared(cx, me.id, len(SK.battles()))
                P.unlock(cx, me.id, [g["人物"] for g in R.generals()], "dev")
                return self._json({"ok": True})
            if url.path == "/api/dev_onsho":
                # 手元の試験用: 全種の恩賞を1つずつ獲得（未所持ぶんだけ）。
                # 公開版では消す（dev_heifu / dev_tenka / dev_senki と同じ口）。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                have = {r["trait_key"] for r in P.owned_traits(cx, me.id)}
                for key in _trait_names():
                    if key not in have:
                        P.grant_trait(cx, me.id, key)
                return self._json({"ok": True})
            if url.path == "/api/free":
                return self._api_free(body)
            if url.path == "/api/room":
                return self._api_room(body)
            if url.path == "/api/onsho":
                return self._api_onsho(body)
            if url.path == "/api/savedeck":
                return self._api_savedeck(body)
            if url.path == "/api/deldeck":
                return self._api_deldeck(body)
            if url.path == "/api/draft":
                return self._api_draft(body)
            if url.path == "/api/dev_heifu":
                # 手元の試験用: 兵符を満タンへ。公開版ではこの口ごと消す。
                me = self._me(self._cx())
                if me is None:
                    return self._json({"error": "login"}, 401)
                P.refill_heifu(self._cx(), me.id, int(time.time()))
                return self._json({"ok": True})
            if url.path == "/api/dev_tenka":
                # 手元の試験用: 次の天下を今すぐ開催する。公開版では消す。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                cards = M._roster_cards()
                now = int(time.time())
                serial, _t = PL.next_tenka(now)
                n = PL._tenka_resolve(cx, cards, serial, now)
                P.ledger_set(cx, "tenka_done", str(serial))
                return self._json({"ok": True, "fought": n})
            self._send(b"not found", 404, "text/plain")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    # 兵種印・コスト印（§7.59）。顔絵と同じく差し替え式で、
    # sim/webui/icons/ に置いた PNG をそのまま出す。無ければ404で、
    # 画面は枠だけになる（規則の表示は文字が正なので読めなくはならない）。
    _ICON_DIR = os.path.join(WEBUI, "icons")

    def _icon(self, name: str):
        name = os.path.basename(urllib.parse.unquote(name))
        if not re.fullmatch(r"[a-z0-9_-]+\.(png|webp|svg)", name):
            return self._send(b"not found", 404, "text/plain")
        path = os.path.join(self._ICON_DIR, name)
        if not os.path.exists(path):
            return self._send(b"not found", 404, "text/plain")
        ctype = {"png": "image/png", "webp": "image/webp",
                 "svg": "image/svg+xml"}[name.rsplit(".", 1)[1]]
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(body)

    # 顔絵（§7.59）。**差し替え式**: sim/webui/portraits/ に「人物名.png」
    # （jpg/webp/svgも可）を置けばそれを出す。無ければ勢力色＋姓の一字の
    # 生成SVG（明らかにダミーと分かる置き絵）を返す。素材の出所と権利は
    # 差し替える人が確かめる — こちらからフリー素材を焼き込むことはしない。
    _PORTRAIT_DIR = os.path.join(WEBUI, "portraits")
    _FACTION_HEX = {"魏": ("#2a3d5e", "#46689c"), "蜀": ("#28492f", "#47825a"),
                    "呉": ("#5e2727", "#a04343"), "群雄": ("#4d4122", "#8a7640")}

    def _portrait(self, person: str):
        person = os.path.basename(person).split(".")[0]
        for ext in ("png", "jpg", "jpeg", "webp", "svg"):
            path = os.path.join(self._PORTRAIT_DIR, person + "." + ext)
            if os.path.exists(path):
                ctype = {"svg": "image/svg+xml", "png": "image/png",
                         "webp": "image/webp"}.get(ext, "image/jpeg")
                with open(path, "rb") as f:
                    return self._send(f.read(), 200, ctype)
        g = next((x for x in R.generals() if x["人物"] == person), None)
        fac = (g or {}).get("勢力", "群雄")
        typ = (g or {}).get("兵種", "")[:1]
        c1, c2 = self._FACTION_HEX.get(fac, self._FACTION_HEX["群雄"])
        kanji = person[:1] or "将"
        import zlib
        tilt = (zlib.crc32(person.encode()) % 13) - 6   # 人ごとに少し違う表情
        svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 240 320'>
<defs><linearGradient id='g' x1='0' y1='0' x2='0' y2='1'>
<stop offset='0' stop-color='{c2}'/><stop offset='1' stop-color='{c1}'/>
</linearGradient><radialGradient id='v' cx='.5' cy='.38' r='.9'>
<stop offset='.45' stop-color='#00000000'/><stop offset='1' stop-color='#00000066'/>
</radialGradient></defs>
<rect width='240' height='320' fill='url(#g)'/>
<circle cx='120' cy='128' r='86' fill='none' stroke='#f0e6d055' stroke-width='2'/>
<circle cx='120' cy='128' r='78' fill='none' stroke='#f0e6d022' stroke-width='1'/>
<text x='120' y='168' text-anchor='middle' font-size='118'
 font-family='Hiragino Mincho ProN,Yu Mincho,serif' fill='#f0e6d0'
 fill-opacity='.88' transform='rotate({tilt} 120 128)'>{kanji}</text>
<text x='214' y='42' text-anchor='middle' font-size='26'
 font-family='Hiragino Mincho ProN,serif' fill='#f0e6d0' fill-opacity='.5'>{typ}</text>
<rect width='240' height='320' fill='url(#v)'/>
<rect x='4' y='4' width='232' height='312' fill='none'
 stroke='#00000055' stroke-width='8'/>
</svg>""".format(c1=c1, c2=c2, kanji=kanji, typ=typ, tilt=tilt)
        self.send_response(200)
        body = svg.encode()
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, name: str):
        path = os.path.join(WEBUI, os.path.basename(name))
        if not os.path.exists(path):
            return self._send(b"not found", 404, "text/plain")
        ctype = "text/css" if name.endswith(".css") else "text/javascript"
        with open(path, "rb") as f:
            body = f.read()
        # 画面の見た目を直したのに古いままに見える、という事故を断つ。
        # app.css / app.js は毎回取りに来させる（手元専用なので費用は無い）。
        self.send_response(200)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------------- API
    def _api_state(self):
        cx = self._cx()
        me = self._me(cx)
        cards = M._roster_cards()
        now = int(time.time())
        PL.tick(cx, cards, now)     # 定刻処理の遅延評価（§7.58）
        players = P.all_players(cx)
        names = {p.id: p.display_name for p in players}
        kinds = {p.id: p.kind for p in players}
        boards = []
        for bn in L.BOARDS:
            # 順位表は毎時の断面（表示だけ。レートの適用はマッチ即時）
            rows = PL.cached_standings(cx, bn)
            table = [{"rank": i + 1, "name": names.get(r["pid"], "?"),
                      "kind": kinds.get(r["pid"], "dummy"),
                      "rating": r["rating"], "games": r["games"],
                      "me": bool(me and r["pid"] == me.id)}
                     for i, r in enumerate(rows)]
            boards.append({"name": bn, "table": table})
        # 天下（1日2回の定刻開催）
        serial, at = PL.next_tenka(now)
        tenka = {"at": at, "in_sec": at - now, "auto": False, "foe": None}
        entry_ok = False
        boards_ok = {}
        heifu = None
        onsho = None
        senki_info = None
        if me:
            entry, boards_ok, errs = PL.entry_of(cx, cards, me.id,
                                                 me.display_name)
            # 帯の解放（§7.60: 官渡=第4章・赤壁=第6章。移行組は素通し）
            gate = SK.board_gate(cx, me.id)
            for bn, g in gate.items():
                if not g:
                    boards_ok[bn] = False
            prog = SK.cleared(cx, me.id)
            bs = SK.battles()
            senki_info = {"cleared": prog, "total": len(bs), "gate": gate,
                          "next": (bs[prog]["title"] if prog < len(bs)
                                   else None)}
            entry_ok = any(boards_ok.values())
            n, wait = P.heifu(cx, me.id, now)
            heifu = {"count": n, "cap": P.HEIFU_CAP, "next_in": wait,
                     "regen": P.HEIFU_REGEN_SEC}
            import datetime
            names_jp = _trait_names()
            key = P.daily_onsho(cx, me.id, list(names_jp),
                                datetime.date.today().isoformat())
            if key:
                onsho = {"key": key, "name": names_jp.get(key, key)}
            tenka["auto"] = bool(boards_ok.get("天下"))
            # 発表済み（開催1時間前〜）なら相手と陣形を見せる — 天下だけに
            # 残した偵察→編成調整の窓（§7.58）
            pairs = P.load_pairs(cx, "天下", serial)
            mine = next((pr for pr in pairs if me.id in pr), None)
            if mine is not None:
                foe = mine[1] if mine[0] == me.id else mine[0]
                fe = PL._tenka_participants(cx, cards).get(foe)
                tenka["foe"] = names.get(foe, "?")
                tenka["forms"] = ("・".join(
                    F.FORM_NAME.get(fe.unit(i).form.n_front, "?")
                    for i in range(3)) if fe is not None else "?")
                last = P.battles_of(cx, pid=foe, limit=1)
                tenka["battle_id"] = last[0]["id"] if last else None
        self._json({
            "stale_server": _server_stale(),
            "me": {"id": me.id, "name": me.display_name} if me else None,
            "humans": [{"id": p.id, "name": p.display_name}
                       for p in players if p.kind == P.HUMAN],
            "dummies": [{"id": p.id, "name": p.display_name}
                        for p in players if p.kind == P.DUMMY],
            "season": P.ledger_get(cx, "season"),
            "boards": boards, "entry_ok": entry_ok, "boards_ok": boards_ok,
            "heifu": heifu, "onsho": onsho, "tenka": tenka,
            "senki": senki_info,
            "banzuke": [{"name": names.get(r["player_id"], "?"),
                         "me": bool(me and r["player_id"] == me.id),
                         "lap": r["lap"], "zanhei": r["zanhei"]}
                        for r in SK.banzuke(cx, 10)],
        })

    def _api_login(self, body):
        cx = self._cx()
        pid = body.get("pid", "")
        new = (body.get("new") or "").strip()
        if new:
            pl = P.register(cx, new, kind=P.HUMAN,
                            email="local+{}@example.invalid".format(P.new_id()[:8]))
            pid = pl.id
            # 新規は初期セットから（§7.60）。既存の救済は PL.tick が済ませている
            P.unlock(cx, pid, R.senki_start(), "start")
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
        _, boards_ok, entry_errors = PL.entry_of(cx, cards, me.id,
                                                 me.display_name)
        from . import design as D
        names_jp = _trait_names()
        trs = {t["キー"]: t for t in R.traits()}
        # 恩賞は**種類ごとにまとめる**（毎日1つ授かるので同種が溜まる。
        # 1行ずつ並べると同じ名前がずらり — テストプレイの指摘）
        groups: dict = {}
        for r in P.owned_traits(cx, me.id):
            key = r["trait_key"]
            g2 = groups.get(key)
            if g2 is None:
                desc, cond = _trait_brief(None, key, trs.get(key, {}))
                g2 = groups[key] = {
                    "key": key, "name": names_jp.get(key, key),
                    "kou": PL.kou_of(key),
                    "desc": desc + ("（{}）".format(cond) if cond else ""),
                    "total": 0, "sets": [], "unset": []}
            g2["total"] += 1
            if r["general_name"]:
                g2["sets"].append({"id": r["id"], "general": r["general_name"]})
            else:
                g2["unset"].append(r["id"])
        onsho = sorted(groups.values(), key=lambda g2: -g2["kou"])
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
        unl = PL.ensure_unlocks(cx, me.id)
        self._json({
            "regs": [{"name": n, "cap": c} for n, c in M.REGULATIONS],
            "roster": _roster_json(only=unl),
            "pool": {"unlocked": sum(1 for g in R.generals()
                                     if g["人物"] in unl),
                     "total": len(R.generals())},
            "decks": decks,
            "entry_errors": entry_errors,
            "boards_ok": boards_ok,
            "onsho": onsho,
            "onsho_budgets": {n: PL.onsho_budget_kou(c)
                              for n, c in M.REGULATIONS},
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
            # 未登用の武将は登録できない（§7.60。検証は登録の瞬間だけ —
            # 保存庫の下書きは自由のまま）
            unl = PL.ensure_unlocks(cx, me.id)
            locked = sorted({M.person_of(c) for c in army.cards} - unl)
            if locked:
                errs.append("まだ登用していない: " + "・".join(locked))
        if errs:
            return self._json({"ok": False, "errors": errs})
        P.set_deck(cx, me.id, reg, raw, fm)
        _, boards_ok, entry_errors = PL.entry_of(cx, cards, me.id,
                                                 me.display_name)
        self._json({"ok": True, "cost": army.total_cost(),
                    "entry_errors": entry_errors, "boards_ok": boards_ok})

    def _api_attack(self, body):
        """BO1の出陣（§7.58）。兵符1枚・相手は同レート帯からシステムが選ぶ。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        now = int(time.time())
        PL.tick(cx, cards, now)
        reg = M.REG_ALIAS.get(body.get("reg", ""), body.get("reg", ""))
        if reg not in PL.REG_NAMES:
            return self._json({"error": "その順位表には出陣できない"}, 400)
        r = PL.attack(cx, cards, me, reg, now)
        self._json(r, 200 if "error" not in r else 400)

    def _api_free(self, body):
        """フリー対戦（在野戦）。レートも兵符も動かない。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        now = int(time.time())
        reg = M.REG_ALIAS.get(body.get("reg", ""), body.get("reg", ""))
        if reg not in PL.REG_NAMES:
            return self._json({"error": "レギュレーションが変"}, 400)
        r = PL.free_battle(cx, cards, me, reg, str(body.get("foe", "")), now)
        self._json(r, 200 if "error" not in r else 400)

    def _api_room(self, body):
        """ルーム対戦。create → 番号発行 / join → 即解決。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        now = int(time.time())
        act = body.get("action", "")
        if act == "create":
            reg = M.REG_ALIAS.get(body.get("reg", ""), body.get("reg", ""))
            if reg not in PL.REG_NAMES:
                return self._json({"error": "レギュレーションが変"}, 400)
            entry, ok, errs = PL.entry_of(cx, cards, me.id, me.display_name)
            if not ok.get(reg):
                return self._json({"error": "{} のデッキが出せる状態にない"
                                   .format(reg)}, 400)
            import json as _j
            snap = _j.dumps(PL.snap_army(entry.unit(PL.REG_NAMES.index(reg))),
                            ensure_ascii=False)
            code = P.room_create(cx, me.id, reg, snap, now)
            return self._json({"code": code, "reg": reg})
        if act == "join":
            r = PL.room_join(cx, cards, me, str(body.get("code", "")).strip(),
                             now)
            return self._json(r, 200 if "error" not in r else 400)
        self._json({"error": "action は create か join"}, 400)

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
        gr = next((g for g in R.generals() if g["名前"] == gen), None)
        if gr is None:
            return self._json({"ok": False, "errors": ["その武将はいない"]})
        if gr["人物"] not in PL.ensure_unlocks(cx, me.id):
            return self._json({"ok": False,
                               "errors": ["まだ登用していない武将には付けられない"]})
        slot = P.free_slot(cx, me.id, gen)
        if slot is None:
            return self._json({"ok": False,
                               "errors": ["{} の軍功枠は3つとも埋まっている".format(gen)]})
        P.set_trait(cx, me.id, oid, gen, slot)
        self._json({"ok": True})

    def _api_draft(self, body):
        """アンケート → たたき台デッキ（§7.54）。登録はしない — 編成欄へ
        流し込むだけで、仕上げと登録はプレイヤーの手に残す。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        reg = M.REG_ALIAS.get(body.get("reg", ""), body.get("reg", ""))
        if reg not in dict(M.REGULATIONS):
            return self._json({"ok": False, "errors": ["そのレギュレーションは無い"]})
        form = F.FORM_ALIAS.get(body.get("form", ""), body.get("form", "魚鱗"))
        # 軍師も未登用は知らない（§7.60）— たたき台は手持ちだけで組む
        unl = PL.ensure_unlocks(cx, me.id)
        cards = [c for c in M._roster_cards() if M.person_of(c) in unl]
        # 他のデッキで使っている人物は避ける（登録検証で両方塞がるため）
        by_name = {c.name: c for c in cards}
        exclude = set()
        for r2, (raw, _f) in P.decks_of(cx, me.id).items():
            if r2 == reg:
                continue
            for n in F.trait_keys(raw):
                if n in by_name:
                    exclude.add(M.person_of(by_name[n]))
        names, note, used_form = PL.draft_deck(
            cards, reg, form, str(body.get("style", "")),
            str(body.get("typ", "")), str(body.get("faction", "")),
            int(body.get("nonce", 0)), exclude)
        self._json({"ok": bool(names), "cards": names, "note": note,
                    "form": used_form})

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

    def _api_senki(self):
        """戦記の進行と戦の一覧（§7.60）。

        見せ方: クリア済み＝戦果と登用を全部、次の戦＝前口上と敵将と登用予定、
        その先＝章と戦名だけ（前口上も敵将も伏せる — 進む楽しみを残す）。
        """
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        prog = SK.cleared(cx, me.id)
        by_person = {g["人物"]: g for g in R.generals()}
        chapters = []
        for b in SK.battles():
            if not chapters or chapters[-1]["ch"] != b["ch"]:
                name, note = SK.CHAPTERS.get(b["ch"], ("", ""))
                chapters.append({"ch": b["ch"], "name": name, "note": note,
                                 "board": b["board"], "battles": []})
            state = ("cleared" if b["i"] < prog
                     else ("next" if b["i"] == prog else "locked"))
            row = {"i": b["i"], "no": b["no"], "title": b["title"],
                   "board": b["board"], "boss": b["boss"], "state": state}
            if state != "locked":
                row["intro"] = b["intro"]
                # 敵将＝その戦の主役（登用筆頭）。デッキ先頭は並び順の都合で
                # 端役のことがある（雁行だと大将が後衛に居る）
                row["foe"] = (by_person[b["recruits"][0]]["名前"]
                              if b["recruits"] else b["deck"][0])
                row["recruits"] = [
                    {"person": p,
                     "name": by_person[p]["名前"],
                     "cost": float(by_person[p]["コスト"])}
                    for p in b["recruits"]]
            chapters[-1]["battles"].append(row)
        gate = SK.board_gate(cx, me.id)
        _, boards_ok, _ = PL.entry_of(cx, M._roster_cards(), me.id,
                                      me.display_name)
        lap = None
        if prog >= len(SK.battles()):
            st = SK.lap_state(cx, me.id)
            lap = dict(st)
            _, plus_pts, mult = SK.lap_enemy(
                M._roster_cards(), SK.boss_battles()[st["stage"]], st["lap"])
            lap["plus_pts"] = plus_pts
            lap["mult_pct"] = round((mult - 1.0) * 100, 1)
            lap["step_every"] = SK.LAP_STEP_EVERY
            lap["bosses"] = [
                {"title": b["title"], "board": b["board"],
                 "beaten": k < st["stage"]}
                for k, b in enumerate(SK.boss_battles())]
            best = cx.execute(
                "SELECT lap, zanhei FROM senki_records WHERE player_id = ?"
                " ORDER BY lap DESC LIMIT 1", (me.id,)).fetchone()
            lap["best"] = dict(best) if best else None
        names = {p.id: p.display_name for p in P.all_players(cx)}
        banzuke = [{"name": names.get(r["player_id"], "?"),
                    "me": r["player_id"] == me.id,
                    "lap": r["lap"], "zanhei": r["zanhei"],
                    "version": r["version"], "at": r["done_at"][:10]}
                   for r in SK.banzuke(cx)]
        self._json({"cleared": prog, "total": len(SK.battles()),
                    "chapters": chapters, "gate": gate,
                    "boards_ok": boards_ok, "lap": lap, "banzuke": banzuke})

    def _api_senki_prep(self, q):
        """戦前の間（§7.62）: 敵の顔ぶれ・前口上・持ち込み上限・草案を返す。

        **戦う前に相手が全部見える**のが要点。見えたうえで上限ちょうどの
        編成を組むのが戦記の遊びで、見えない相手に登録デッキをぶつけるのは
        作業だった（テストプレイの指摘）。
        """
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        bs = SK.battles()
        i = int(q.get("i", -1))
        if not 0 <= i < len(bs):
            return self._json({"error": "その戦は無い"}, 404)
        prog = SK.cleared(cx, me.id)
        if i > prog:
            return self._json({"error": "先の戦にはまだ進めない"}, 400)
        b = bs[i]
        cards = M._roster_cards()
        unl = PL.ensure_unlocks(cx, me.id)
        cap = SK.player_cap(cards, b)
        brief = {c["name"]: c for c in _roster_json()}
        foe = SK.enemy_army(cards, b)
        by_person = {g["人物"]: g for g in R.generals()}
        lead = by_person.get(b["recruits"][0]) if b["recruits"] else None
        names, form = SK.suggest_deck(cards, unl, b, int(time.time()))
        saved = []
        for r in P.saved_decks(cx, me.id):
            if r["regulation"] != b["board"]:
                continue
            army, es = PL.parse_deck(cards, r["cards"], r["formation"])
            if army is None or es:
                continue
            saved.append({"id": r["id"], "name": r["name"],
                          "form": F.FORM_ALIAS.get(r["formation"],
                                                   r["formation"]),
                          "cards": [c.name for c in army.cards],
                          "cost": army.total_cost()})
        reg = None
        raw = P.decks_of(cx, me.id).get(b["board"])
        if raw:
            army, es = PL.parse_deck(cards, raw[0], raw[1])
            if army is not None and not es:
                reg = {"form": F.FORM_ALIAS.get(raw[1], raw[1]),
                       "cards": [c.name for c in army.cards],
                       "cost": army.total_cost()}
        self._json({
            "i": b["i"], "ch": b["ch"], "no": b["no"], "title": b["title"],
            "chapter": SK.CHAPTERS.get(b["ch"], ("", ""))[0],
            "board": b["board"], "boss": b["boss"], "intro": b["intro"],
            "cap": cap, "cleared": b["i"] < prog,
            "enemy": {
                "form": F.FORM_NAME[foe.form.n_front],
                "cost": foe.total_cost(),
                "front": foe.form.n_front,
                "cards": [dict(brief[c.name],
                               rear=(k >= foe.form.n_front))
                          for k, c in enumerate(foe.cards)],
                "taunt": (lead or {}).get("台詞", ""),
                "lead": (lead or {}).get("名前", ""),
            },
            "recruits": [{"person": p, "name": by_person[p]["名前"],
                          "cost": float(by_person[p]["コスト"]),
                          "typ": by_person[p]["兵種"]}
                         for p in b["recruits"]],
            "suggest": {"cards": names, "form": form},
            "last": SK.last_deck(cx, me.id, b["i"]),
            "saved": saved, "registered": reg,
        })

    def _api_senki_fight(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        now = int(time.time())
        PL.tick(cx, cards, now)
        deck = None
        if body.get("cards"):
            deck = {"cards": [str(x) for x in body["cards"]],
                    "form": str(body.get("form", ""))}
        r = SK.fight(cx, cards, me, int(body.get("i", -1)), now, deck=deck)
        self._json(r, 200 if "error" not in r else 400)

    def _api_senki_lap(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        now = int(time.time())
        PL.tick(cx, cards, now)
        r = SK.lap_fight(cx, cards, me, now)
        self._json(r, 200 if "error" not in r else 400)

    def _api_replays(self):
        cx = self._cx()
        me = self._me(cx)
        cards = M._roster_cards()
        PL.tick(cx, cards, int(time.time()))
        names = {p.id: p.display_name for p in P.all_players(cx)}
        import datetime
        mode_jp = {"ranked": "挑戦", "tenka": "天下", "free": "フリー",
                   "room": "ルーム", "senki": "戦記"}
        rows = []
        for m in P.battles_of(cx, limit=60):
            # 戦記は自分の記録にだけ出す（他家の一覧には載せない・§7.60）
            if m["mode"] == "senki" and not (me and m["pid_a"] == me.id):
                continue
            role = ""
            if me and m["mode"] == "ranked":
                role = ("挑" if m["pid_a"] == me.id
                        else ("防" if m["pid_b"] == me.id else ""))
            rows.append({
                "id": m["id"], "board": m["board"],
                "mode": mode_jp.get(m["mode"], m["mode"]), "role": role,
                "a": names.get(m["pid_a"], "?"),
                "b": (SK.title_of(m["pid_b"]) if m["mode"] == "senki"
                      else names.get(m["pid_b"], "?")),
                "at": datetime.datetime.fromtimestamp(
                    m["played_at"]).strftime("%m/%d %H:%M"),
                "mine": bool(me and me.id in (m["pid_a"], m["pid_b"]))})
        self._json({"battles": rows})

    def _api_replay(self, q):
        cx = self._cx()
        me = self._me(cx)
        mid = int(q.get("id", 0))
        row = cx.execute("SELECT * FROM battles WHERE id = ?", (mid,)).fetchone()
        if row is None:
            return self._json({"error": "その記録は無い"}, 404)
        m = dict(row)
        cards = M._roster_cards()
        names = {p.id: p.display_name for p in P.all_players(cx)}
        if m["mode"] == "senki":
            # 戦記のリプレイは本人だけ（§7.60。番付のデッキ非公開と同じ筋）
            if not (me and me.id == m["pid_a"]):
                return self._json({"error": "その記録は無い"}, 404)
            names[m["pid_b"]] = SK.title_of(m["pid_b"])

        def sides():
            if m["snap_a"] and m["snap_b"]:
                # 魚拓から再構成（§7.58）。後からデッキを変えても不変。
                return (PL.entry_from_snap(cards, m["snap_a"]),
                        PL.entry_from_snap(cards, m["snap_b"]))
            # 旧記録（魚拓なし）。当時の登録デッキから再構成するので、
            # 登録が変わっていれば再生できない。
            entries = PL.ensure_dummies(cx, cards)
            for p in P.all_players(cx, kind=P.HUMAN):
                e, ok2, _ = PL.entry_of(cx, cards, p.id, p.display_name)
                if any(ok2.values()):
                    entries[p.id] = e
            return entries.get(m["pid_a"]), entries.get(m["pid_b"])

        try:
            a, b = sides()
        except KeyError:
            a = b = None
        if a is None or b is None:
            return self._json({"error": "編成を再構成できない（登録が変わった）"}, 410)
        me_first = not (me and me.id == m["pid_b"])
        games = []
        try:
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
        except KeyError:
            return self._json({"error": "編成を再構成できない"
                               "（その戦場のデッキが外された）"}, 410)
        mine_id = m["pid_a"] if me_first else m["pid_b"]
        foe_id = m["pid_b"] if me_first else m["pid_a"]
        import datetime
        self._json({
            "title": "{} 対 {}".format(names.get(m["pid_a"], "?"),
                                       names.get(m["pid_b"], "?")),
            "board": m["board"], "mode": m["mode"],
            "when": datetime.datetime.fromtimestamp(
                m["played_at"]).strftime("%m/%d %H:%M"),
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
