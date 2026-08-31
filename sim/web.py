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

from . import auth as A
from . import field as F
from . import ladder as L
from . import match as M
from . import play as PL
from . import players as P
from . import rosterdata as R
from . import senki as SK

PORT = int(os.environ.get("SANGOKU_PORT", "8035"))
# 既定は自分の機械の中だけ。スマホから触るときは SANGOKU_HOST=0.0.0.0。
HOST = os.environ.get("SANGOKU_HOST", "127.0.0.1")
# 公開モード（§7.118）。立てると (1) 名乗りログインを止めて OIDC だけにする
# (2) dev の口を環境変数に関係なく閉じる (3) クッキーへ Secure を付ける
# (4) /api/state から他人の pid を配らない。必須の環境変数は main() が検査。
PUBLIC = os.environ.get("SANGOKU_PUBLIC", "0") == "1"
# 試験用の口（/api/dev_*）は、外に出した瞬間に閉じる。
# 手元に閉じているときだけ既定で開き、SANGOKU_DEV で明示的に上書きできる。
# **公開モードでは環境変数に関係なく閉じる**（うっかりの余地を残さない）。
DEV_DOORS = (not PUBLIC) and os.environ.get(
    "SANGOKU_DEV", "1" if HOST in ("127.0.0.1", "localhost") else "0") == "1"
# 攻勢の表示（§7.56）が仮定する「標準的な鎧」= 3兵種の平均。
_DEF_MEAN = sum(F.DEF_BY_TYPE.values()) / len(F.DEF_BY_TYPE)
WEBUI = os.path.join(os.path.dirname(__file__), "webui")

VIEWS = {"/": "home", "/senki": "senki", "/deck": "deck",
         "/council": "council", "/replays": "replays",
         "/replay": "replay"}

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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>三国布陣</title>
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#14100c">
<link rel="apple-touch-icon" href="/icons/app-180.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="三国布陣">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
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


def _my_rating(cx, me):
    """帯ごとの「現在の武名」と戦績（§7.86・§7.89）。順位表は毎時の断面
    なので、**自分の値だけは即時**を出す。戦績は刻み（result 列）から数える
    ので、刻みの無い旧記録は入らない。"""
    rec = P.record_of(cx, me.id)
    out = {}
    for bn in L.BOARDS:
        b = PL.load_board(cx, bn)
        w, l, _d = rec.get(bn, (0, 0, 0))
        out[bn] = {"rating": round(b.get(me.id), 1),
                   "games": b.games.get(me.id, 0), "w": w, "l": l}
    return out


def _deck_records(cx, pid):
    """保存デッキの戦績（§7.120）。**中身一致で自動集計** — 対戦の記録
    （battles・§7.58）は編成の陣容と勝敗の刻みを持っているので、新しい表を
    足さずに (レギュレーション, 札の並び, 陣形) で突き合わせれば、過去の
    対戦ぶんまで遡って出る。編成を1枚でも変えると別デッキ扱いになる（戦績は
    その編成のものとして残る）。

    数えるのは**レート対象だけ**（ranked / tenka）。稽古（free / room）を
    混ぜると「試しに10連敗」が看板の数字を汚す。恩賞（trait）は無視して
    札の並びと陣形だけで一致を取る — 恩賞替えは同じデッキの調整と見なす。

    戻り: {(レギュ名, 札名タプル, 陣形名): {"n":出陣, "w":勝, "l":負}}
    """
    import json as _json
    regs = [n for n, _ in M.REGULATIONS]
    out = {}

    def bump(reg, snap, mark):
        try:
            names = tuple(c["n"] for c in snap["cards"])
            form = F.FORM_ALIAS.get(snap["form"], snap["form"])
        except (KeyError, TypeError):
            return
        cell = out.setdefault((reg, names, form), {"n": 0, "w": 0, "l": 0})
        cell["n"] += 1
        if mark == "○":
            cell["w"] += 1
        elif mark == "●":
            cell["l"] += 1

    for r in cx.execute(
            "SELECT board, pid_a, pid_b, snap_a, snap_b, result FROM battles"
            " WHERE (pid_a = ? OR pid_b = ?) AND result <> ''"
            " AND mode IN ('ranked', 'tenka')", (pid, pid)):
        mine = r["snap_a"] if r["pid_a"] == pid else r["snap_b"]
        if not mine:
            continue
        marks = r["result"]
        if r["pid_b"] == pid:
            marks = marks.translate(str.maketrans("○●", "●○"))
        try:
            d = _json.loads(mine)
        except ValueError:
            continue
        if "units" in d:                      # 天下（3レギュぶんの陣容）
            for i, snap in enumerate(d["units"]):
                if i < len(regs) and i < len(marks):
                    bump(regs[i], snap, marks[i])
        elif r["board"] in regs:              # BO1（そのレギュの陣容1つ）
            bump(r["board"], d, marks[0] if marks else "")
    return out


def _trait_names():
    """特性キー → 表示名。**traits.csv の 名前 列が一次**（旧実装はここに
    別の対応表を持っていて、同じ量の定義が2箇所になっていた）。"""
    return {t["キー"]: t["名前"] for t in R.traits()}


_MOD_JP = {"攻撃力": "攻撃力", "命中率": "攻撃力（命中）", "防御力": "防御力",
           "移動速度": "移動速度", "気勢": "気勢",
           "兵法防御": "兵法防御", "兵法反射": "兵法反射",
           "通常攻撃防御": "通常攻撃防御",
           # 畏怖は攻撃力マイナスの呼び名（§7.64）。札の文言は「畏怖」の
           # ままにし、括弧で機構を添える — 名前で雰囲気、括弧で読める量
           "畏怖": "畏怖（敵の攻撃力）"}


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
        elif sk.power_hi > sk.power:
            # 威力の幅（§7.67）。**幅を隠さない** — `sk.power` だけを見せると
            # 低いほうの数字が確定値のように読める。振れ幅そのものが札の性格
            # （張飛の一喝・王允の誅董の密詔）なので、両端を出す（§7.113）。
            parts.append("損害 約{:,.0f}〜{:,.0f}人（放つたびに振れる）".format(
                F.SKILL_SCALE * sk.power * coef,
                F.SKILL_SCALE * sk.power_hi * coef))
        else:
            # 「守りで目減り」の注記は毎行に付けず、凡例に1回書く（冗長の指摘）
            parts.append("損害 約{:,.0f}人".format(
                F.SKILL_SCALE * sk.power * coef))
    if getattr(sk, "heal_pct", 0.0) > 0.0:
        # 割合回復（§7.129）。**対象の最大兵力**に対する割合なので、撃ち手の
        # 能力では実数にできない（畏怖と同じで、語彙から漏らすと生の文が
        # そのまま出る）。何に対する割合かを言い切る。
        parts.append("立て直し 味方1隊の最大兵力の{:g}%".format(sk.heal_pct * 100.0))
    if sk.heal > 0.0:
        if coef is None:
            parts.append("回復（量は持ち主の武将しだい）")
        else:
            total = F.HEAL_SCALE * sk.heal * coef * (sk.dur if sk.dur > 0 else 1.0)
            parts.append("回復 約{:,.0f}人".format(total))
    raw = sk_row.get("効果", "")
    # 畏怖（§7.64）は攻撃力マイナスの呼び名で、符号を文に持たない書式
    # （「畏怖 -12%（30秒・知力比）」）。**語彙から漏らすと二重に壊れる** —
    # 単独持ちは raw フォールバックで生の「秒」が出て、ダメージ兵法に付いた
    # ものは行そのものが消える（実際に徐盛・闞沢と張飛ら4枚で踏んだ）。
    # 「・知力比」の接尾（§7.67）も両方の書式で受ける。
    for m in _re.finditer(r"(攻撃力|命中率|防御力|移動速度|気勢|兵法防御|兵法反射|通常攻撃防御|畏怖)"
                          r"\s*([+-]?\d+)%（(\d+)秒(・知力比)?）", raw):
        # 符号の無い書式は畏怖だけ（常に弱体）。他は CSV が必ず符号を持つ
        sign = (m.group(2) if m.group(2)[0] in "+-"
                else ("-" if m.group(1) == "畏怖" else "+") + m.group(2))
        parts.append("{} {}%（{:.0f}分間{}）".format(
            _MOD_JP[m.group(1)], sign, F.mins(float(m.group(3))),
            "・撃ち手の知略しだいで効き目が変わる" if m.group(4) else ""))
    m = _re.search(r"混乱\s*(\d+)%（(\d+)秒）", raw)
    if m:
        # 「20%」が**何の20%か**が読めない（テストプレイの指摘: 部隊の20%？
        # 成功確率？）。混乱は成功判定ではなく状態の**濃さ**で、結果は2つ —
        # 出力が落ちることと、与ダメージの一部が味方へ向くこと。**式から
        # 実数を出して括弧へ入れる**（§7.47: 読めない内部の数字を見せない）。
        c = float(m.group(1)) / 100.0
        parts.append("混乱 {}%（出力 -{:.0f}%・同士討ち {:.0f}%／{:.0f}分間）".format(
            m.group(1), 100.0 * (1.0 - 1.0 / (1.0 + c)),
            100.0 * F.CHAOS_FF * c / (1.0 + c), F.mins(float(m.group(2)))))
    m = _re.search(r"行動阻害\s*(\d+)秒", raw)
    if m:
        # 混乱と並ぶと「足止めにも%があるのか」と読まれる（テストプレイの
        # 指摘）。**止まるのは攻撃と移動の両方**だと言い切る。
        parts.append("足止め {:.0f}分間（攻撃も前進も止まる）".format(
            F.mins(float(m.group(1)))))
    m = _re.search(r"代償\s*兵力(\d+)%", raw)
    if m:
        parts.append("代償 放つたびに自隊の残り兵力の{}%を失う".format(m.group(1)))
    m = _re.search(r"兵法打消し（(\d+)秒）", raw)
    if m:
        parts.append("打消し 構えた隊を狙う敵の兵法を丸ごと無効化（{:.0f}分間）"
                     .format(F.mins(float(m.group(1)))))
    m = _re.search(r"ゲージ付与", raw)
    if m:
        parts.append("味方のゲージを進める")
    return " ＋ ".join(parts) if parts else raw


_TRAIT_CONDS = {"ally_retreat": "味方の隊が崩れた時",
                "enemy_retreat": "敵の隊が崩れた時",
                "self_low_hp": "自身の兵が減った時",
                "ally_skill": "味方が兵法を放った時",
                # 自身の**全滅**（§7.113）。「崩れた」（残存30%割れ）とは別で、
                # 兵が一人も残らなかった時。書き分けないと self_low_hp と
                # 同じ条件に読める。
                "self_dead": "自身の隊が全滅した時",
                # 敵の攻め兵法（§7.129）。強化・回復・構えには反応しない旨を
                # 短く言い切る — 「兵法を受けた時」だと構えにも見える。
                "foe_skill": "敵が攻め兵法を放った時"}


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
        elif "上限なし" in note:
            cond += "・回数の上限なし"
        # 効果は兵法と**同じ器で発動する**ので、表示も同じ換算を通す:
        # 回復は実数、時間は分、命中率は攻撃力（命中）。対象が自分以外なら
        # 明示する — 書かないと全部が自分バフに読める（テストプレイの指摘）。
        m = _re.search(r"対象 ([^/]+)", note)
        target = (m.group(1).strip() if m else "自分")
        desc = _skill_display(g, {"効果": desc, "対象": target})
        if target != "自分":
            cond = "・".join(x for x in ("対象 " + target, cond.lstrip("・")) if x)
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
    elif key == "restraint":
        desc = ("自身が初めて兵法を放った後、敵味方の各武将は"
                "初回発動後の自然ゲージ増加 -{:.0%}"
                "（一発目・初期・与ダメージ・被弾の獲得は不変）"
                .format(1.0 - F.RESTRAINT_NATURAL_MULT))
    elif key in F.FACTION_OF:
        desc = "{}の武将への与ダメージ +{:.0%}（群雄にも当たる）".format(
            F.FACTION_OF[key], F.VS_FACTION)
    return desc, cond


def _roster_json(only=None):
    """武将一覧（§7.47 の開示設計）。

    見せるのは**プレイヤーが支払う・選ぶ判断に使う量**だけ: 能力値・兵法の中身・
    特性の中身・ゲージ。内部帳簿（能力値コスト・効果予算・総合値・実力比・
    値段表）は出さない — 正解表になって編成の探索が死ぬため。

    only を渡すと**解放済みの人物だけ**返す（§7.60。未登用は姿も見せない —
    戦記で出会うのが初対面になる）。
    """
    if not F.SKILL_INFO:
        # 兵法の中身（発動型の自動判定に使う）が未読込なら積む（§7.127）
        R.load_skills_into_field()
    sk = {s["兵法名"]: s for s in R.skills()}
    tr = {t["キー"]: t for t in R.traits()}
    names_jp = _trait_names()
    out = []
    for g in R.generals():
        if only is not None and g["人物"] not in only:
            continue
        s = sk.get(g["兵法"], {})
        traits = []
        for k in R.traits_of(g):
            t = tr.get(k, {})
            desc, cond = _trait_brief(g, k, t)
            traits.append({"key": k, "name": names_jp.get(k, k),
                           "kind": t.get("型", ""), "cond": cond, "desc": desc})
        out.append({
            "name": g["名前"], "person": g["人物"], "cost": float(g["コスト"]),
            # 武将版（§7.135）。同一人物の2枚目以降にだけ意味がある番号
            # なので、UIは1のときはバッジを出さない。顔絵は名前優先で
            # 探す（無ければ人物名の絵、それも無ければ生成プレースホルダ）。
            "version": int((g.get("版") or "").strip() or 1),
            "portraitUrl": "/portrait/" + urllib.parse.quote(g["名前"]),
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
            "skill": g["兵法"], "skill_desc": _skill_display(g, s),
            "skill_target": s.get("対象", ""),
            "gauge_cost": g["消費ゲージ%"], "gauge_rate": g["ゲージ上昇率"],
            "gauge_init": g["初期ゲージ"],
            # 兵法の巡り（§7.127）: 発動型と自然蓄積の目安。秒はサーバで計算して
            # 渡す（GAUGE_PER_SEC をフロントへ重複記載しない）。
            "cadence": _cadence(g, s),
            "traits": traits, "trait": g["固有特性"],
            "quote": g.get("台詞", ""),
        })
    return out


_TIER_JP = {"手数": "連発型", "標準": "標準型", "大技": "決戦型"}


def _cadence(g, srow) -> dict:
    """兵法の巡り（§7.127）。発動型と、自然蓄積だけで見た初動・再発の目安。

    目安の式: 初動 (消費−初期)÷(自然増加×上昇率)、再発 消費÷(同)。上昇率は
    100表記を1.00に直す。実際は攻撃・被弾・気勢・恩賞で早まるので
    「自然蓄積の目安」（討ち取り給は未配線 — field.GAUGE_ON_ROUT の注記）。
    **表示は戦場の分**（§7.47 の規約: 時間は実況と同じ「分」で語る。盤面の
    秒を出すと兵法説明の「81分間」や実況の時刻と物差しが揃わない）。5分丸め。
    早い/普通/遅いの敷居は内部秒のまま — 表示単位と判定を絡ませない。"""
    gc = float(g["消費ゲージ%"])
    gi = float(g["初期ゲージ"])
    rate = max(float(g["ゲージ上昇率"]) / 100.0, 1e-6)
    per = F.GAUGE_PER_SEC * rate
    first = max(gc - gi, 0.0) / per
    rep = gc / per
    tier = R.tier_for(srow, F.SKILL_INFO.get(g["兵法"]))
    m5 = lambda x: int(round(F.mins(x) / 5.0) * 5)
    f_lbl = "早い" if first < 45.0 else ("普通" if first <= 65.0 else "遅い")
    r_lbl = "早い" if rep < 60.0 else ("遅い" if rep <= 120.0 else "かなり遅い")
    return {"tier": tier, "tier_jp": _TIER_JP.get(tier, "標準型"),
            "first_m": m5(first), "first_label": f_lbl,
            "repeat_m": m5(rep), "repeat_label": r_lbl}


_FB_FACTION = {"魏": "gi", "蜀": "shoku", "呉": "go", "群雄": "gunyu"}


def _formation_board_json(army, brief=None):
    """FormationBoard の読み取り専用データ契約へ Army を詰め替える。

    武将名を unitId とし、盤面順はエンジンと同じ
    「前衛左→右、後衛左→右」。勢力には現行データの魏も含める。

    brief（名前→札）は呼び手が使い回せる。天下のリプレイは3戦×2軍で
    6回ここを通るので、毎回 _roster_json() を組み直さない。
    """
    if brief is None:
        brief = {c["name"]: c for c in _roster_json()}
    faction = _FB_FACTION
    formation = {4: "kakuyoku", 3: "gyorin", 2: "gankou"}.get(
        army.form.n_front, "gyorin")
    slots = [c.name or None for c in army.cards]
    slots = (slots + [None] * 6)[:6]
    units = {}
    for c in army.cards:
        row = brief.get(c.name)
        if not row:
            continue
        units[c.name] = {
            "name": row["name"],
            # 版専用の絵があれば名前（版込み）で、無ければ人物名の絵へ
            # フォールバック（§7.135）。_portrait 側の解決順と対にする。
            "portraitUrl": row.get("portraitUrl")
                or "/portrait/" + urllib.parse.quote(row["person"]),
            "troopType": row["typ"],
            # 槍は後衛の可否を決める属性なので盤面まで運ぶ（§7.91）
            "spear": bool(row.get("spear")),
            "role": row["role"],
            "cost": row["cost"],
            "faction": faction.get(row["faction"], "gunyu"),
        }
    return {"formation": formation, "slots": slots, "units": units}


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
        cookie = self.headers.get("Cookie", "")
        pid = A.session_pid(cookie)          # 署名つき sid（§7.118）
        if pid:
            return P.get(cx, pid)
        if not PUBLIC:
            # 手元の互換: 旧実装のベタ pid クッキー。公開モードでは受けない —
            # /api/state が全員の pid を配っていた時代のクッキーは資格にならない。
            for part in cookie.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "pid":
                    return P.get(cx, v)
        return None

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else "?"

    def log_message(self, *a):
        pass

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        try:
            if url.path.startswith("/auth/"):
                return self._auth(url.path, q)
            if url.path == "/favicon.ico":
                svg = ("<svg xmlns='http://www.w3.org/2000/svg' "
                       "viewBox='0 0 16 16'><text y='13' font-size='13'>"
                       "⚔️</text></svg>").encode()
                return self._send(svg, 200, "image/svg+xml")
            if url.path.startswith("/static/"):
                return self._static(url.path[len("/static/"):])
            if url.path == "/manifest.webmanifest":
                # スマホの「ホーム画面に追加」で、額縁の無い一枚画面として開く
                man = json.dumps({
                    "name": "三国布陣", "short_name": "布陣",
                    "start_url": "/deck", "scope": "/",
                    "display": "standalone", "orientation": "portrait",
                    "background_color": "#14100c", "theme_color": "#14100c",
                    "icons": [{"src": "/icons/app-192.png", "sizes": "192x192",
                               "type": "image/png"},
                              {"src": "/icons/app-512.png", "sizes": "512x512",
                               "type": "image/png"}],
                }, ensure_ascii=False)
                return self._send(man.encode(), 200,
                                  "application/manifest+json; charset=utf-8")
            if url.path.startswith("/icons/"):
                return self._icon(url.path[len("/icons/"):])
            if url.path.startswith("/art/"):
                return self._art(url.path[len("/art/"):])
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
            if url.path == "/api/council":
                return self._api_council()
            if url.path == "/api/replays":
                return self._api_replays()
            if url.path == "/api/replay":
                return self._api_replay(q)
            self._send(b"not found", 404, "text/plain")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def do_POST(self):
        # CSRF の腰壁: Origin が付いていて自分のホストと違えば断る。
        # SameSite=Lax のクッキーと合わせた二重化（§7.118）。
        origin = self.headers.get("Origin", "")
        if origin:
            host = self.headers.get("Host", "")
            if urllib.parse.urlparse(origin).netloc not in ("", host):
                return self._json({"error": "origin"}, 403)
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
            if url.path == "/api/deck_all":
                return self._api_deck_all(body)
            if url.path == "/api/deck_reset":
                return self._api_deck_reset(body)
            if url.path == "/api/attack":
                return self._api_attack(body)
            if url.path == "/api/truce":
                return self._api_truce(body)
            if url.path == "/api/senki_fight":
                return self._api_senki_fight(body)
            if url.path == "/api/senki_lap":
                return self._api_senki_lap(body)
            if url.path == "/api/council_fight":
                return self._api_council_fight(body)
            if url.path == "/api/dev_senki":
                if not DEV_DOORS:
                    return self._send(b"not found", 404, "text/plain")
                # 手元の試験用: 戦記を全クリア扱いにして全員登用。公開版では消す。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                SK.set_cleared(cx, me.id, len(SK.battles()))
                P.unlock(cx, me.id, [g["人物"] for g in R.generals()], "dev")
                return self._json({"ok": True})
            if url.path == "/api/dev_onsho":
                if not DEV_DOORS:
                    return self._send(b"not found", 404, "text/plain")
                # 手元の試験用: 全種の恩賞を1つずつ獲得（未所持ぶんだけ）。
                # 公開版では消す（dev_heifu / dev_tenka / dev_senki と同じ口）。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                have = {r["trait_key"] for r in P.owned_traits(cx, me.id)}
                for key in list(_trait_names()) + list(PL.ONSHO_BOOKS):
                    if key not in have:
                        P.grant_trait(cx, me.id, key)
                return self._json({"ok": True})
            if url.path == "/api/free":
                return self._api_free(body)
            if url.path == "/api/room":
                return self._api_room(body)
            if url.path == "/api/onsho":
                return self._api_onsho(body)
            if url.path == "/api/onsho_pick":
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                import datetime
                today = datetime.date.today().isoformat()
                ok = P.pick_onsho(cx, me.id, str(body.get("key", "")),
                                  PL.onsho_candidates(me.id, today), today)
                return self._json({"ok": ok} if ok else
                                  {"ok": False,
                                   "errors": ["本日の恩賞はもう受け取っている"]})
            if url.path == "/api/seen":
                # 1回きりの案内を「見た」印。鍵は許可制 — 任意の旗を書かせない
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                if body.get("key") not in ("onboard",):
                    return self._json({"ok": False}, 400)
                P.flag_set(cx, me.id, body["key"])
                return self._json({"ok": True})
            if url.path == "/api/savedeck":
                return self._api_savedeck(body)
            if url.path == "/api/deldeck":
                return self._api_deldeck(body)
            if url.path == "/api/draft":
                return self._api_draft(body)
            if url.path == "/api/dev_heifu":
                if not DEV_DOORS:
                    return self._send(b"not found", 404, "text/plain")
                # 手元の試験用: 兵符を満タンへ。公開版ではこの口ごと消す。
                me = self._me(self._cx())
                if me is None:
                    return self._json({"error": "login"}, 401)
                P.refill_heifu(self._cx(), me.id, int(time.time()))
                return self._json({"ok": True})
            if url.path == "/api/dev_enshu":
                if not DEV_DOORS:
                    return self._send(b"not found", 404, "text/plain")
                # 手元の試験用: 演習令を無料でMAXへ。公開版ではこの口ごと消す。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                P.refill_enshu(cx, me.id, int(time.time()))
                return self._json({"ok": True})
            if url.path == "/api/dev_reset_record":
                if not DEV_DOORS:
                    return self._send(b"not found", 404, "text/plain")
                # 手元の試験用（§7.90・**今だけ**）: 自分の戦績を白紙に戻す。
                # レート・戦数・対戦記録を消す。デッキ・登用・恩賞は残す。
                # **公開版では消すこと**（他人の記録に触れる口を残さない）。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                with cx:
                    cx.execute("DELETE FROM ratings WHERE player_id = ?",
                               (me.id,))
                    cx.execute("DELETE FROM battles"
                               " WHERE pid_a = ? OR pid_b = ?", (me.id, me.id))
                    cx.execute("DELETE FROM standings_cache")
                return self._json({"ok": True})
            if url.path == "/api/dev_tenka":
                if not DEV_DOORS:
                    return self._send(b"not found", 404, "text/plain")
                # 手元の試験用: 次の天下を今すぐ開催する。公開版では消す。
                cx = self._cx()
                me = self._me(cx)
                if me is None:
                    return self._json({"error": "login"}, 401)
                cards = M._roster_cards()
                now = int(time.time())
                # **まだ開催していない回**を選ぶ。next_tenka は「次の予定」を
                # 返すだけなので、押すたび同じ回を解決して二重記録になり
                # （同じ種＝同じ結果の行が並ぶ）、レートも二重に動いていた
                # （§7.82）。済んだ回を飛ばして先の回へ進む。
                done = int(P.ledger_get(cx, "tenka_done", "0"))
                serial = None
                for sr, _t in PL.tenka_events(now, now + 30 * 24 * 3600):
                    if sr > done:
                        serial = sr
                        break
                if serial is None:
                    return self._json({"error": "開催できる回が無い"}, 400)
                n = PL._tenka_resolve(cx, cards, serial, now)
                P.ledger_set(cx, "tenka_done", str(serial))
                return self._json({"ok": True, "fought": n})
            self._send(b"not found", 404, "text/plain")
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _send_file(self, path: str, ctype: str, max_age: int = 86400) -> bool:
        """絵を返す。**変わっていなければ本体を送らない**（304）。

        顔絵120枚で約16MB あり、既定の no-store のままだと画面を開くたびに
        全部を引き直す。スマホの回線ではこれが一番効く。差し替え式なので
        中身の版は「更新時刻＋大きさ」を印にして見分ける。
        """
        try:
            st = os.stat(path)
        except OSError:
            return False
        tag = '"%x-%x"' % (int(st.st_mtime), st.st_size)
        if self.headers.get("If-None-Match") == tag:
            self.send_response(304)
            self.send_header("ETag", tag)
            self.send_header("Cache-Control", "max-age=%d" % max_age)
            self.end_headers()
            return True
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", tag)
        self.send_header("Cache-Control", "max-age=%d" % max_age)
        self.end_headers()
        self.wfile.write(body)
        return True

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
        self._send_file(path, ctype)

    # 題字絵（キービジュアル）。icons と同じ差し替え式で、
    # sim/webui/art/ に置いたものをそのまま出す。無ければ404 — 画面側は
    # 文字の題字へ落ちるので、絵が無くても読めなくならない。
    _ART_DIR = os.path.join(WEBUI, "art")

    def _art(self, name: str):
        name = os.path.basename(urllib.parse.unquote(name))
        if not re.fullmatch(r"[a-z0-9_-]+\.(png|webp|jpg|svg)", name):
            return self._send(b"not found", 404, "text/plain")
        path = os.path.join(self._ART_DIR, name)
        if not os.path.exists(path):
            return self._send(b"not found", 404, "text/plain")
        ctype = {"png": "image/png", "webp": "image/webp",
                 "jpg": "image/jpeg", "svg": "image/svg+xml"}[
                     name.rsplit(".", 1)[1]]
        self._send_file(path, ctype)

    # 顔絵（§7.59・§7.135）。**差し替え式**: sim/webui/portraits/ に
    # 「人物名.png」（jpg/webp/svgも可）を置けばそれを出す。無ければ勢力色＋
    # 姓の一字の生成SVG（明らかにダミーと分かる置き絵）を返す。素材の出所と
    # 権利は差し替える人が確かめる — こちらからフリー素材を焼き込むことはしない。
    #
    # 武将版（§7.135）: 呼び出し側は「名前」（例「呂布〔虓虎〕」）を渡す。
    # 版専用の絵を先に探し、無ければ人物名の絵（既存120枚はここで見つかる・
    # 後方互換）、それも無ければ生成プレースホルダの順で解決する。
    _PORTRAIT_DIR = os.path.join(WEBUI, "portraits")
    _FACTION_HEX = {"魏": ("#2a3d5e", "#46689c"), "蜀": ("#28492f", "#47825a"),
                    "呉": ("#5e2727", "#a04343"), "群雄": ("#4d4122", "#8a7640")}

    def _portrait(self, key: str):
        key = os.path.basename(key).split(".")[0]
        person = key.split("〔")[0] if "〔" in key else key
        for candidate in (key, person) if key != person else (key,):
            for ext in ("png", "jpg", "jpeg", "webp", "svg"):
                path = os.path.join(self._PORTRAIT_DIR, candidate + "." + ext)
                if os.path.exists(path):
                    ctype = {"svg": "image/svg+xml", "png": "image/png",
                             "webp": "image/webp"}.get(ext, "image/jpeg")
                    if self._send_file(path, ctype):
                        return
        g = next((x for x in R.generals() if x["名前"] == key), None) \
            or next((x for x in R.generals() if x["人物"] == person), None)
        fac = (g or {}).get("勢力", "群雄")
        typ = (g or {}).get("兵種", "")[:1]
        c1, c2 = self._FACTION_HEX.get(fac, self._FACTION_HEX["群雄"])
        kanji = person[:1] or "将"
        import zlib
        # tilt は key（版込み）から取る — 版専用の絵が無くても、版が違えば
        # 表情の傾きだけは自動的に変わる（§7.135。プレースホルダでも
        # 「別バージョンで見た目が変わる」を満たす最小限の仕掛け）。
        tilt = (zlib.crc32(key.encode()) % 13) - 6
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
            # _send が no-store を付ける。画面の札は直したら即入れ替わってほしい。
            self._send(f.read(), 200, ctype + "; charset=utf-8")

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
        # 天下（毎時00分の定刻開催・休戦令8枚/日）
        _serial, at = PL.next_tenka(now)
        tenka = {"at": at, "in_sec": at - now, "auto": False,
                 "eligible": False, "resting": False}
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
            today = datetime.date.today().isoformat()
            # 授与済みの判定は**地元の日付**で（§7.84。UTC のまま比べると
            # 日本の 0〜9時に帯が消えず、何度でも選べてしまっていた）
            if not cx.execute(
                    "SELECT 1 FROM owned_traits WHERE player_id=?"
                    " AND date(gained_at,'localtime')=?",
                    (me.id, today)).fetchone():
                cands = PL.onsho_candidates(me.id, today)
                trs = {t["キー"]: t for t in R.traits()}
                rows2 = []
                for tier, k in cands:
                    if k in PL.ONSHO_BOOKS:
                        nm, desc = PL.ONSHO_BOOKS[k][0], PL.ONSHO_BOOKS[k][1]
                    else:
                        nm = names_jp.get(k, k)
                        d2, cond = _trait_brief(None, k, trs.get(k, {}))
                        desc = d2 + ("（{}）".format(cond) if cond else "")
                    rows2.append({"key": k, "tier": tier, "name": nm,
                                  "kou": PL.kou_of(k), "desc": desc})
                onsho = {"choices": rows2}
            tenka["eligible"] = bool(boards_ok.get("天下"))
            tenka["resting"] = PL.truce_is_active(cx, me.id, at)
            tenka["auto"] = tenka["eligible"] and not tenka["resting"]
            tenka["truce"] = PL.truce_schedules(cx, me.id, now)
            # 次に実際に参加する開催。8時間連続休戦でも一目で復帰時刻が分かる。
            tenka["next_active_at"] = None
            if tenka["eligible"]:
                for _sr, t2 in PL.tenka_events(now, now + 2 * 24 * 3600):
                    if not PL.truce_is_active(cx, me.id, t2):
                        tenka["next_active_at"] = t2
                        break
            # 一時間ごとの16件を通知で流さず、ホームでは本日分を一行に畳む。
            import datetime
            d0 = datetime.datetime.fromtimestamp(now).replace(
                hour=0, minute=0, second=0, microsecond=0)
            report = {"n": 0, "w": 0, "l": 0, "d": 0}
            for row in cx.execute(
                    "SELECT pid_a,pid_b,result FROM battles"
                    " WHERE mode='tenka' AND played_at>=? AND played_at<?"
                    " AND (pid_a=? OR pid_b=?)",
                    (int(d0.timestamp()),
                     int((d0 + datetime.timedelta(days=1)).timestamp()),
                     me.id, me.id)):
                marks = row["result"] or ""
                if row["pid_b"] == me.id:
                    marks = marks.translate(str.maketrans("○●", "●○"))
                w, lose = marks.count("○"), marks.count("●")
                report["n"] += 1
                report["w" if w > lose else ("l" if lose > w else "d")] += 1
            tenka["report"] = report
        self._json({
            "stale_server": _server_stale(),
            "auth": {"mode": "oidc" if PUBLIC else "local"},
            # 初回の案内（§7.121）。人間・戦記が手つかず・まだ出していない、の
            # 3条件が揃ったときだけ。進行が動けば旗が無くても二度と出ない。
            "onboard": bool(me and me.kind == P.HUMAN
                            and SK.cleared(cx, me.id) == 0
                            and not P.flag_has(cx, me.id, "onboard")),
            "me": {"id": me.id, "name": me.display_name} if me else None,
            # 公開モードでは他人の pid を配らない（§7.118）。名乗りログインの
            # 選択肢リストは手元専用の道具である。
            "humans": [] if PUBLIC else [
                {"id": p.id, "name": p.display_name}
                for p in players if p.kind == P.HUMAN],
            "dummies": [{"id": p.id, "name": p.display_name}
                        for p in players if p.kind == P.DUMMY],
            "season": P.ledger_get(cx, "season"),
            "boards": boards, "entry_ok": entry_ok, "boards_ok": boards_ok,
            # 現在の武名（§7.86）。順位表は毎時の断面なので、**自分の値だけは
            # 即時**を出す（対戦直後に動いたことが画面で分からなかった）。
            "my_rating": (_my_rating(cx, me) if me else None),
            "heifu": heifu, "onsho": onsho, "tenka": tenka,
            "senki": senki_info,
            "banzuke": [{"name": names.get(r["player_id"], "?"),
                         "me": bool(me and r["player_id"] == me.id),
                         "lap": r["lap"], "zanhei": r["zanhei"]}
                        for r in SK.banzuke(cx, 10)],
        })

    _AUTH_RATE = A.RateLimit(20, 60)     # 認証の口: IPごと 20回/分

    def _auth(self, path, q):
        """OIDC のログイン往復（§7.118）。/auth/login → IdP → /auth/callback。"""
        if not self._AUTH_RATE.allow(self._client_ip()):
            return self._send(b"too many requests", 429, "text/plain")
        if path == "/auth/logout":
            self.send_response(303)
            self.send_header("Set-Cookie", A.clear_cookie(secure=PUBLIC))
            self.send_header("Location", "/")
            self.end_headers()
            return
        if not A.configured():
            return self._send("外部ログインは未設定（SANGOKU_OIDC_* を見よ）"
                              .encode(), 503, "text/plain; charset=utf-8")
        if path == "/auth/login":
            to, cookie = A.begin_login()
            self.send_response(303)
            self.send_header("Set-Cookie", cookie)
            self.send_header("Location", to)
            self.end_headers()
            return
        if path == "/auth/callback":
            try:
                who = A.finish_login(q, self.headers.get("Cookie", ""))
            except (ValueError, OSError) as e:
                return self._send("ログインに失敗した: {}".format(e).encode(),
                                  400, "text/plain; charset=utf-8")
            cx = self._cx()
            me = P.find_by_identity(cx, A.PROVIDER, who["sub"])
            if me is None:
                # 初回。表示名は IdP の名前 → メールの手前、の順で借りる。
                # 24文字で切る（順位表の列が壊れない長さ）。
                name = (who["name"] or who["email"].partition("@")[0]
                        or "主公")[:24]
                me = P.register(cx, name, kind=P.HUMAN, email=who["email"],
                                provider=A.PROVIDER, subject=who["sub"])
                P.unlock(cx, me.id, R.senki_start(), "start")
            self.send_response(303)
            self.send_header("Set-Cookie",
                             A.session_cookie(me.id, secure=PUBLIC))
            self.send_header("Location", "/")
            self.end_headers()
            return
        return self._send(b"not found", 404, "text/plain")

    def _api_login(self, body):
        if PUBLIC:
            # 公開では名乗るだけのログインを受けない（§7.118）。pid は公開情報
            # ではないが、推測や漏れの一撃でなりすませる口を外へ出さない。
            return self._json({"error": "oidc", "login": "/auth/login"}, 403)
        if not self._AUTH_RATE.allow(self._client_ip()):
            return self._json({"error": "rate"}, 429)
        cx = self._cx()
        pid = body.get("pid", "")
        new = (body.get("new") or "").strip()[:24]
        if new:
            pl = P.register(cx, new, kind=P.HUMAN,
                            email="local+{}@example.invalid".format(P.new_id()[:8]))
            pid = pl.id
            # 新規は初期セットから（§7.60）。既存の救済は PL.tick が済ませている
            P.unlock(cx, pid, R.senki_start(), "start")
        if P.get(cx, pid) is None:
            return self._json({"ok": False}, 400)
        # 手元でも署名つき sid を配る（経路を1本にして公開と同じ道を通す）
        self._json({"ok": True}, cookie=A.session_cookie(pid, secure=False))

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
                          "cards": [c.name for c in army.cards] if army else [],
                          "cost": army.total_cost() if army else None}
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
                if key in PL.ONSHO_BOOKS:
                    nm, desc, cond = (PL.ONSHO_BOOKS[key][0],
                                      PL.ONSHO_BOOKS[key][1], "")
                else:
                    nm = names_jp.get(key, key)
                    desc, cond = _trait_brief(None, key, trs.get(key, {}))
                g2 = groups[key] = {
                    "key": key, "name": nm,
                    "kou": PL.kou_of(key),
                    "desc": desc + ("（{}）".format(cond) if cond else ""),
                    "total": 0, "sets": [], "unset": []}
            g2["total"] += 1
            if r["general_name"]:
                g2["sets"].append({"id": r["id"], "general": r["general_name"]})
            else:
                g2["unset"].append(r["id"])
        onsho = sorted(groups.values(), key=lambda g2: -g2["kou"])
        recs = _deck_records(cx, me.id)
        saved = []
        for r in P.saved_decks(cx, me.id):
            army, _ = PL.parse_deck(cards, r["cards"], r["formation"])
            names = ([c.name for c in army.cards] if army
                     else [x.strip() for x in r["cards"].split(F.TRAIT_SEP)
                           if x.strip()])
            form = F.FORM_ALIAS.get(r["formation"], r["formation"])
            saved.append({
                "id": r["id"], "name": r["name"], "reg": r["regulation"],
                "form": form,
                "cards": names,
                "cost": army.total_cost() if army else None,
                "rec": recs.get((r["regulation"], tuple(names), form)),
            })
        unl = PL.ensure_unlocks(cx, me.id)
        self._json({
            "regs": [{"name": n, "cap": c} for n, c in M.REGULATIONS],
            "deck_slots": P.entitlement(cx, me.id).get("deck_slots", 10),
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

    def _board_check(self, cx, me, cards, reg, fm, names, check_other_boards=True):
        """登録1面ぶんの検証（/api/deck と /api/deck_all の共通部）。

        (army, raw, errs) を返す。検証規則はここ1箇所だけに持つ。

        check_other_boards は単面保存（/api/deck）だけ True にする。
        /api/deck_all は3面を丸ごと差し替える途中でここを呼ぶため、DBの
        旧内容と突き合わせると「入れ替え」を誤って重複扱いしてしまう
        （§7.135で発見・修正）。全面まとめての重複判定は呼び出し側が
        新しい内容どうしで別途行う。"""
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
            # 同一人物は面の中でも、他の面（DBに保存済み）とまたいでも1枚
            # まで（§4.1・§7.135）。単面保存の /api/deck はここを通さないと
            # クライアントの無効化表示だけが頼りになり、直叩きで抜けられる。
            others = []
            if check_other_boards:
                for r, (c_raw, f_raw) in P.decks_of(cx, me.id).items():
                    if r == reg:
                        continue
                    a, _ = PL.parse_deck(cards, c_raw, f_raw)
                    if a is not None:
                        others.append((r, a))
            errs += [msg for _label, msg in
                     M.duplicate_person_errors([(reg, army)] + others)]
        return army, raw, errs

    def _api_deck(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        reg = M.REG_ALIAS.get(body.get("reg", ""), body.get("reg", ""))
        fm = body.get("form", "魚鱗")
        names = [str(x) for x in body.get("cards", [])]
        army, raw, errs = self._board_check(cx, me, cards, reg, fm, names)
        if errs:
            return self._json({"ok": False, "errors": errs})
        P.set_deck(cx, me.id, reg, raw, fm)
        _, boards_ok, entry_errors = PL.entry_of(cx, cards, me.id,
                                                 me.display_name)
        self._json({"ok": True, "cost": army.total_cost(),
                    "entry_errors": entry_errors, "boards_ok": boards_ok})

    def _api_deck_all(self, body):
        """全部セットの一斉登録（§7.128）。渡された組が**新しい全登録**になる。

        面ごとの検証に加えて**面間の同一人物**もここで弾く（従来は登録後に
        entry_of が両面を塞ぐだけだった）。どれか1面でも不備なら**何も
        書かない** — 半端な置き換えで前の登録を失わせない。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        boards = body.get("boards", [])
        if not isinstance(boards, list) or not boards:
            return self._json({"ok": False,
                               "errors": {"全体": ["boards が空"]}})
        errs_by = {}
        new: dict = {}
        armies: dict = {}
        for b in boards:
            reg = M.REG_ALIAS.get(str(b.get("reg", "")), str(b.get("reg", "")))
            fm = b.get("form", "魚鱗")
            names = [str(x) for x in b.get("cards", [])]
            if reg in new:
                errs_by.setdefault(reg, []).append("同じ戦場が2回ある")
                continue
            army, raw, errs = self._board_check(cx, me, cards, reg, fm, names,
                                                check_other_boards=False)
            if errs:
                errs_by[reg] = errs
            else:
                new[reg] = (raw, fm)
                armies[reg] = army
        # 面間・面内の同一人物（登録レベルの規則・§4.1）。セット内で先に弾く。
        # duplicate_person_errors は面の境界で条件を分けないので、同じ面に
        # 同一人物が2回（別バージョン含む）入っていても見逃さない（§7.135）。
        for reg, msg in M.duplicate_person_errors(list(armies.items())):
            errs_by.setdefault(reg, []).append(msg)
        if errs_by:
            return self._json({"ok": False, "errors": errs_by})
        P.replace_decks(cx, me.id, new)
        _, boards_ok, entry_errors = PL.entry_of(cx, cards, me.id,
                                                 me.display_name)
        self._json({"ok": True,
                    "costs": {r: a.total_cost() for r, a in armies.items()},
                    "entry_errors": entry_errors, "boards_ok": boards_ok})

    def _api_deck_reset(self, body):
        """登録デッキの一斉リセット（§7.128）。保存庫と登用・恩賞は残す。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        P.clear_decks(cx, me.id)
        cards = M._roster_cards()
        _, boards_ok, entry_errors = PL.entry_of(cx, cards, me.id,
                                                 me.display_name)
        self._json({"ok": True, "entry_errors": entry_errors,
                    "boards_ok": boards_ok})

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

    def _api_truce(self, body):
        """天下の休戦令。通常設定か、今日から7日分の日別設定を更新する。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        now = int(time.time())
        # 遅延評価の未開催分を**旧設定のまま先に解決**する。設定を先に変えると、
        # サーバ停止中の過去開催へ新しい通常設定が遡ってしまう。
        PL.tick(cx, M._roster_cards(), now)
        action = str(body.get("action", ""))
        try:
            if action == "default":
                data = PL.set_truce_default(
                    cx, me.id, body.get("hours", []), now)
                return self._json({"ok": True, "truce": data,
                                   "message": "通常の休戦令を改めた"})
            if action in ("day", "reset_day"):
                data = PL.set_truce_day(
                    cx, me.id, str(body.get("day", "")),
                    body.get("hours", []), now,
                    reset=(action == "reset_day"))
                return self._json({
                    "ok": True, "truce": data,
                    "message": ("日別設定を通常へ戻した"
                                if action == "reset_day" else
                                "この日の休戦令を改めた")})
            return self._json({"error": "休戦令の操作が正しくない"}, 400)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)

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
        # 陣形を名指しされたら軍師に動かさせない（"おまかせ" なら従来どおり）
        pin_form = bool(body.get("pin_form"))
        # 軍師も未登用は知らない（§7.60）— たたき台は手持ちだけで組む
        unl = PL.ensure_unlocks(cx, me.id)
        all_cards = M._roster_cards()
        cards = [c for c in all_cards if M.person_of(c) in unl]
        # 戦記の戦を指されたら、その戦の上限ちょうどで組む（§7.62）。
        # 戦記は PvE なので、他のデッキとの人物の取り合いは見ない。
        senki = body.get("senki")
        cap = None
        exclude = set()
        if senki is not None:
            bs = SK.battles()
            i = int(senki)
            if not 0 <= i < len(bs):
                return self._json({"ok": False, "errors": ["その戦は無い"]})
            b = bs[i]
            if i > SK.cleared(cx, me.id):
                return self._json({"ok": False, "errors": ["先の戦にはまだ進めない"]})
            reg = b["board"]
            cap = SK.player_cap(all_cards, b)
            # 敵に出ている顔は草案から外す（規則では許すが、初期案にはしない）
            exclude = {M.person_of(c)
                       for c in SK.enemy_army(all_cards, b).cards}
        else:
            # 他のデッキで使っている人物は避ける（登録検証で両方塞がるため）
            by_name = {c.name: c for c in cards}
            for r2, (raw, _f) in P.decks_of(cx, me.id).items():
                if r2 == reg:
                    continue
                for n in F.trait_keys(raw):
                    if n in by_name:
                        exclude.add(M.person_of(by_name[n]))
        names, note, used_form = PL.draft_deck(
            cards, reg, form, str(body.get("style", "")),
            str(body.get("typ", "")), str(body.get("faction", "")),
            int(body.get("nonce", 0)), exclude,
            cap=cap, ratio=1.0 if cap else 0.9, pin_form=pin_form)
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
        # 保存庫の枠数はプランの権利（§7.120）。**同じ名前への上書きは枠を
        # 消費しない** — 「満杯だから調整もできない」を作らないため。
        cap = P.entitlement(cx, me.id).get("deck_slots", 10)
        if (not P.saved_deck_exists(cx, me.id, reg, name)
                and P.saved_deck_count(cx, me.id, reg) >= cap):
            return self._json({"ok": False, "errors": [
                "この戦場の保存庫は{}枠まで。どれかを消すか、同じ名前で上書きする"
                .format(cap)]})
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
            "hint": SK.battle_hint(cards, unl, b),
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

    def _api_council(self):
        """軍議演習の札数と、使える過去対戦の敵陣容。"""
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        now = int(time.time())
        PL.tick(cx, cards, now)
        _entry, boards_ok, _errs = PL.entry_of(cx, cards, me.id,
                                               me.display_name)
        names = {p.id: p.display_name for p in P.all_players(cx)}
        mode_jp = {"ranked": "挑戦", "tenka": "天下", "free": "在野",
                   "room": "ルーム"}
        import datetime
        targets = []
        for m in P.battles_of(cx, pid=me.id, limit=60):
            if m["mode"] in ("senki", "council"):
                continue
            if not m["snap_a"] or not m["snap_b"]:
                continue
            if m["board"] not in PL.REG_NAMES and m["board"] != "天下":
                continue
            me_a = m["pid_a"] == me.id
            foe_pid = m["pid_b"] if me_a else m["pid_a"]
            marks = m.get("result", "")
            if marks and not me_a:
                marks = marks.translate(str.maketrans("○●", "●○"))
            targets.append({
                "id": m["id"], "board": m["board"],
                "mode": mode_jp.get(m["mode"], m["mode"]),
                "foe": names.get(foe_pid, "名もなき軍"),
                "marks": marks,
                "at": datetime.datetime.fromtimestamp(
                    m["played_at"]).strftime("%m/%d %H:%M"),
                "ready": bool(boards_ok.get(m["board"])),
            })
            if len(targets) >= 30:
                break
        n, wait = P.enshu(cx, me.id, now)
        self._json({
            "ticket": {"name": "演習令", "count": n, "cap": P.ENSHU_CAP,
                       "next_in": wait, "regen": P.ENSHU_REGEN_SEC},
            "targets": targets,
        })

    def _api_council_fight(self, body):
        cx = self._cx()
        me = self._me(cx)
        if me is None:
            return self._json({"error": "login"}, 401)
        cards = M._roster_cards()
        now = int(time.time())
        PL.tick(cx, cards, now)
        try:
            source_id = int(body.get("source_id", 0))
        except (TypeError, ValueError):
            source_id = 0
        r = PL.council_battle(cx, cards, me, source_id, now)
        self._json(r, 200 if "error" not in r else 400)

    def _api_replays(self):
        cx = self._cx()
        me = self._me(cx)
        cards = M._roster_cards()
        PL.tick(cx, cards, int(time.time()))
        names = {p.id: p.display_name for p in P.all_players(cx)}
        import datetime
        mode_jp = {"ranked": "挑戦", "tenka": "天下", "free": "フリー",
                   "room": "ルーム", "senki": "戦記", "council": "軍議"}
        # **自分の記録は別に引く**（§7.82）。全体の直近60件から絞ると、
        # 天下1回で8件ほど書かれるダミー同士の対戦に自分の戦歴が押し出されて
        # 消えていた（「見えていない」の正体）。
        seen_ids = set()
        pool = []
        if me:
            # 天下は最大16件/日。40件では約2.5日で挑戦・軍議が押し出される。
            # 無制限にはせず、天下7日分＋ほかの遊びが残る200件を上限にする。
            pool += P.battles_of(cx, pid=me.id, limit=200)
        pool += P.battles_of(cx, limit=60)
        rows = []
        for m in pool:
            if m["id"] in seen_ids:
                continue
            seen_ids.add(m["id"])
            # 戦記は自分の記録にだけ出す（他家の一覧には載せない・§7.60）
            if m["mode"] == "senki" and not (me and m["pid_a"] == me.id):
                continue
            role = ""
            if me and m["mode"] == "ranked":
                role = ("挑" if m["pid_a"] == me.id
                        else ("防" if m["pid_b"] == me.id else ""))
            # 勝敗の刻み（§7.81）。自分が B 側なら裏返して「自分から見た」
            # 刻みにする。旧記録（result=''）は空のまま＝一覧では「—」。
            marks = m["result"] if "result" in m.keys() else ""
            if marks and me and m["pid_b"] == me.id:
                marks = marks.translate(str.maketrans("○●", "●○"))
            council = P.council_run(cx, m["id"]) if m["mode"] == "council" else None
            rows.append({
                "id": m["id"], "board": m["board"], "marks": marks,
                "mode": mode_jp.get(m["mode"], m["mode"]),
                "mode_key": m["mode"], "role": role,
                "a": names.get(m["pid_a"], "?"),
                "b": ((council or {}).get("foe_name", "?")
                      if m["mode"] == "council" else
                      (SK.title_of(m["pid_b"]) if m["mode"] == "senki"
                       else names.get(m["pid_b"], "?"))),
                "at": datetime.datetime.fromtimestamp(
                    m["played_at"]).strftime("%m/%d %H:%M"),
                "day": datetime.datetime.fromtimestamp(
                    m["played_at"]).strftime("%Y-%m-%d"),
                "mine": bool(me and me.id in (m["pid_a"], m["pid_b"])),
                "can_council": bool(
                    me and me.id in (m["pid_a"], m["pid_b"])
                    and m["mode"] not in ("senki", "council")
                    and m["snap_a"] and m["snap_b"]),
            })
        rows.sort(key=lambda r: -r["id"])        # 2つの束を混ぜたので並べ直す
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
        elif m["mode"] == "council":
            # 仮想敵の陣容は作成者だけが見られる。元の相手本人へは記録を出さない。
            run = P.council_run(cx, mid)
            if not (me and run and run["player_id"] == me.id):
                return self._json({"error": "その記録は無い"}, 404)
            names[m["pid_b"]] = run["foe_name"]

        def sides():
            if m["snap_a"] and m["snap_b"]:
                # 陣容から再構成（§7.58）。後からデッキを変えても不変。
                return (PL.entry_from_snap(cards, m["snap_a"]),
                        PL.entry_from_snap(cards, m["snap_b"]))
            # 旧記録（陣容なし）。当時の登録デッキから再構成するので、
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
        fb_brief = {c["name"]: c for c in _roster_json()}

        def add_boards(game, army_a, army_b):
            mine_army, foe_army = ((army_a, army_b) if me_first
                                    else (army_b, army_a))
            game["mine_board"] = _formation_board_json(mine_army, fb_brief)
            game["foe_board"] = _formation_board_json(foe_army, fb_brief)
            return game
        try:
            if m["board"] in PL.REG_NAMES:
                reg = PL.REG_NAMES.index(m["board"])
                cap = M.REGULATIONS[reg][1]
                army_a, army_b = a.unit(reg), b.unit(reg)
                g = PL.replay_data(M.with_surplus(army_a, cap),
                                   M.with_surplus(army_b, cap),
                                   0.5, m["seed"], me_first)
                add_boards(g, army_a, army_b)
                g["label"] = m["board"]
                games.append(g)
            else:
                for i, (label, cap) in enumerate(M.REGULATIONS):
                    army_a, army_b = a.unit(i), b.unit(i)
                    g = PL.replay_data(M.with_surplus(army_a, cap),
                                       M.with_surplus(army_b, cap),
                                       0.5, m["seed"] * 3 + i, me_first)
                    add_boards(g, army_a, army_b)
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
            "battle_id": mid,
            # 対戦時点の戦闘ルール版（§7.135）。旧記録（空文字列）は "1.0" に
            # 補完する — 現行ルールをそのまま初版としているので、値として嘘にならない。
            "rule_version": m["rule_version"] or "1.0",
            "can_council": bool(
                me and me.id in (m["pid_a"], m["pid_b"])
                and m["mode"] not in ("senki", "council")
                and m["snap_a"] and m["snap_b"]),
        })


def _lan_address() -> str:
    """同じ網の中から見えるこの機械の住所（分からなければ空）。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))      # 送らない。経路を選ばせるだけ
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def main() -> None:
    if PUBLIC:
        # **公開モードは設定が欠けたまま起動しない。** 途中まで動いて認証だけ
        # 無い、が一番危ない形なので、欠けを列挙して落ちる。
        missing = [k for k, v in (
            ("SANGOKU_SECRET", os.environ.get("SANGOKU_SECRET", "")),
            ("SANGOKU_BASE_URL", A.BASE_URL),
            ("SANGOKU_OIDC_CLIENT_ID", A.CLIENT_ID),
            ("SANGOKU_OIDC_CLIENT_SECRET", A.CLIENT_SECRET)) if not v]
        if missing:
            raise SystemExit("公開モード（SANGOKU_PUBLIC=1）に必要な環境変数が"
                             "無い: " + ", ".join(missing)
                             + "\n手順は docs/deploy.md を見よ。")
        if len(os.environ.get("SANGOKU_SECRET", "")) < 32:
            raise SystemExit("SANGOKU_SECRET が短すぎる（32文字以上の乱文字列に"
                             "すること。例: python3 -c \"import secrets;"
                             " print(secrets.token_urlsafe(48))\"）")
    srv = ThreadingHTTPServer((HOST, PORT), App)
    print("http://localhost:{}  （Ctrl+C で終了）".format(PORT))
    if PUBLIC:
        print("公開モード: 名乗りログイン停止・OIDCのみ・試験用の口は封鎖。")
        print("外向きURL: {}".format(A.BASE_URL))
    elif HOST not in ("127.0.0.1", "localhost"):
        ip = _lan_address()
        if ip:
            print("同じ Wi-Fi のスマホから: http://{}:{}".format(ip, PORT))
        print("※ この口には鍵が無い。名乗るだけで誰にでもなれるので、")
        print("　 同じ網に居る人しか触れない状態を保つこと。")
    if not DEV_DOORS:
        print("試験用の口（/api/dev_*）は閉じている。")
    srv.serve_forever()


if __name__ == "__main__":
    main()
