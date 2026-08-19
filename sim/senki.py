# -*- coding: utf-8 -*-
"""sim/senki.py — 戦記（討伐→登用キャンペーン・§7.60）

物語は書かない。1話 ＝ 敵の固定デッキ1つ＋前口上＋登用。プレイヤーは在野の
無名君主として演義の名場面を外側から渡り歩き、**倒した名将を登用して**自軍へ
加える。解放システム（unlocks・戦記①）の配達手段であり、実質のチュートリアル
であり、過疎期のソロコンテンツでもある。

データの正本は sim/data/senki.csv。**起動時に全戦を検証して、壊れていれば
大声で死ぬ**（コスト上限・配置規則・登用の重複と取りこぼし。§13 の流儀 —
静かに欠けたキャンペーンを配るほうが高くつく）。

設計の決めごと（docs/design/senki-campaign-draft.md）:
  - 帯の進行: 1〜3章＝汜水関18 → 4〜5章＝官渡30 → 6〜8章＝赤壁40。
    章がそのままレギュレーション名に重なる（第4章=官渡・第5章=赤壁）。
  - 章ボスだけ上限+2点の「格上」。PvE なので許される演出。
  - 敗北ペナルティなし・何度でも再挑戦。乱数は挑戦ごとに引き直す
    （シード固定は棄却 — 編成を写した瞬間に全員同点になり番付が退化する）。
  - 戦記戦はレートも兵符も動かさない。リプレイは残る（自分だけが観られる）。
"""

from __future__ import annotations

import json as _json
from typing import Dict, List, Optional

from . import field as F
from . import ladder as L
from . import match as M
from . import players as P
from . import rosterdata as R

BOSS_EXTRA = 2          # 章ボスの「格上」ぶん（コスト点）

# 章の看板（番号 → (章名, 舞台の説明)）。戦の中身は CSV が正本。
CHAPTERS: Dict[int, tuple] = {
    1: ("黄巾の乱", "義勇の旗揚げ。妖術の霧の中で初陣を飾る"),
    2: ("反董卓連合", "汜水関から虎牢関へ。天下無双との遭遇"),
    3: ("群雄割拠", "宛城・下邳・江東。乱世の猛者たちを訪ね歩く"),
    4: ("官渡", "河北の大軍と中原の智略。奸雄はここで覇を固めた"),
    5: ("三顧から赤壁へ", "伏龍と鳳雛、そして長江の火。天下三分の幕開け"),
    6: ("荊州争奪から漢中へ", "定軍山の攻防と樊城の水攻め。荊州が動く"),
    7: ("夷陵と南征", "火は南へ。書生の火計と南蛮王の心攻め"),
    8: ("北伐から五丈原へ", "丞相の遺志と冢虎の持久。長い戦いの果て"),
}

_CACHE: Optional[List[Dict]] = None


def battles() -> List[Dict]:
    """全戦のリスト（通し番号順）。初回に CSV から読み、検証してから返す。"""
    global _CACHE
    if _CACHE is None:
        rows = []
        for r in R._load("senki.csv"):
            if not r.get("戦名"):
                continue
            rows.append({
                "i": len(rows),
                "ch": int(r["章"]), "no": int(r["戦"]),
                "title": r["戦名"], "board": r["帯"],
                "boss": bool((r.get("ボス") or "").strip()),
                "form": r["陣形"],
                "deck": [x.strip() for x in r["敵デッキ"].split("、") if x.strip()],
                "recruits": [x.strip() for x in r["登用"].split("、") if x.strip()],
                "intro": r["前口上"],
            })
        errs = _validate(rows)
        if errs:
            raise ValueError("senki.csv が壊れている:\n  " + "\n  ".join(errs))
        _CACHE = rows
    return _CACHE


def _validate(rows: List[Dict]) -> List[str]:
    """データ検証。壊れたキャンペーンを静かに配らないための関門。"""
    from . import play as PL
    errs: List[str] = []
    cards = M._roster_cards()
    caps = dict(M.REGULATIONS)
    persons = {g["人物"] for g in R.generals()}
    start = set(R.senki_start())
    seen: Dict[str, str] = {}
    for b in rows:
        tag = "{}-{}「{}」".format(b["ch"], b["no"], b["title"])
        if b["board"] not in caps:
            errs.append(tag + ": 帯が変 " + b["board"])
            continue
        cap = caps[b["board"]] + (BOSS_EXTRA if b["boss"] else 0)
        army, perrs = PL.parse_deck(cards, F.TRAIT_SEP.join(b["deck"]), b["form"])
        if perrs or army is None:
            errs.append(tag + ": " + "／".join(perrs))
            continue
        errs += [tag + ": " + e for e in M.placement_errors(army)]
        if len(army.cards) != M.UNIT_SIZE:
            errs.append(tag + ": {}人必要".format(M.UNIT_SIZE))
        if army.total_cost() > cap + 1e-9:
            errs.append(tag + ": コスト{:g}が上限{:g}を超過".format(
                army.total_cost(), cap))
        for p in b["recruits"]:
            if p not in persons:
                errs.append(tag + ": 登用対象が居ない " + p)
            elif p in start:
                errs.append(tag + ": 登用対象が初期セットと重複 " + p)
            elif p in seen:
                errs.append(tag + ": 登用が {} と重複 ".format(seen[p]) + p)
            seen[p] = tag
    missing = persons - set(R.senki_start()) - set(seen)
    if missing:
        errs.append("誰の戦でも登用されない: " + "・".join(sorted(missing)))
    return errs


def enemy_army(cards, b: Dict) -> F.Army:
    from . import play as PL
    army, _ = PL.parse_deck(cards, F.TRAIT_SEP.join(b["deck"]), b["form"])
    return army


# ---------------------------------------------------------------- 進行
def cleared(cx, player_id: str) -> int:
    r = cx.execute("SELECT cleared FROM senki WHERE player_id = ?",
                   (player_id,)).fetchone()
    return r["cleared"] if r else 0


def set_cleared(cx, player_id: str, n: int) -> None:
    with cx:
        cx.execute(
            "INSERT INTO senki (player_id, cleared) VALUES (?, ?)"
            " ON CONFLICT(player_id) DO UPDATE SET cleared = excluded.cleared,"
            " updated_at = datetime('now')", (player_id, n))


_CH_FIRST: Dict[int, int] = {}


def _ch_first(ch: int) -> int:
    """その章の最初の戦の通し番号。"""
    if not _CH_FIRST:
        for b in battles():
            _CH_FIRST.setdefault(b["ch"], b["i"])
    return _CH_FIRST.get(ch, 10 ** 9)


def board_gate(cx, player_id: str) -> Dict[str, bool]:
    """挑戦ラダーの帯解放（§7.60: 官渡=第4章到達・赤壁=第6章到達）。

    **移行組（全解放で救済された既存プレイヤー）は全帯そのまま** — 遊べていた
    盤面を後から閉じない。フリー対戦・ルーム・天下はこの門を通さない
    （天下は3デッキ登録が実質の門になっている）。
    """
    row = cx.execute(
        "SELECT 1 FROM unlocks WHERE player_id = ? AND source = 'migration'"
        " LIMIT 1", (player_id,)).fetchone()
    if row is not None:
        return {"汜水関": True, "官渡": True, "赤壁": True}
    c = cleared(cx, player_id)
    return {"汜水関": True,
            "官渡": c >= _ch_first(4),
            "赤壁": c >= _ch_first(6)}


# ---------------------------------------------------------------- 戦闘
def fight(cx, cards, me, idx: int, now: int) -> Dict:
    """戦記の1戦。勝てば（未クリアの戦なら）進行が進み、登用が起きる。

    レートも兵符も動かない。乱数は挑戦ごとに引き直す（now を種に混ぜる）。
    クリア済みの戦は何度でも再戦できる（報酬は無し・稽古扱い）。
    """
    from . import play as PL
    bs = battles()
    prog = cleared(cx, me.id)
    if not 0 <= idx < len(bs):
        return {"error": "その戦は無い"}
    if idx > prog:
        return {"error": "先の戦にはまだ進めない（順に進む）"}
    b = bs[idx]
    entry, ok, errs = PL.entry_of(cx, cards, me.id, me.display_name)
    if not ok.get(b["board"]):
        return {"error": "{} のデッキが出せる状態にない（編成で登録してから挑もう）"
                .format(b["board"])}
    reg_i = PL.REG_NAMES.index(b["board"])
    foe = enemy_army(cards, b)
    seed = L.battle_seed("senki", b["i"], me.id, now)
    ua = entry.unit(reg_i)
    r = M.play_one(PL.BoardEntry({reg_i: ua}), PL.BoardEntry({reg_i: foe}),
                   reg_i, 0.5, seed=seed)
    won = r["winner"] == "A"
    bid = P.record_battle(
        cx, "senki", b["board"], me.id, "senki:{}".format(b["i"]), seed,
        _json.dumps(PL.snap_army(ua), ensure_ascii=False),
        _json.dumps(PL.snap_army(foe), ensure_ascii=False),
        PL.season_key(now), now)
    recruits: List[Dict] = []
    if won and idx == prog:
        set_cleared(cx, me.id, prog + 1)
        got = P.unlock(cx, me.id, b["recruits"],
                       "senki:{}-{}".format(b["ch"], b["no"]))
        if got:
            by_person = {g["人物"]: g for g in R.generals()}
            for p in b["recruits"]:
                g = by_person[p]
                recruits.append({"person": p, "name": g["名前"],
                                 "cost": float(g["コスト"]), "typ": g["兵種"],
                                 "quote": g.get("台詞", "")})
    return {"battle_id": bid, "title": b["title"],
            "win": "勝ち" if won else ("負け" if r["winner"] == "B" else "引き分け"),
            "recruits": recruits,
            "cleared": cleared(cx, me.id), "total": len(bs)}


def title_of(pid: str) -> str:
    """battles 表の pid（"senki:12"）を戦名へ。リプレイ一覧の表示用。"""
    try:
        b = battles()[int(pid.split(":", 1)[1])]
        return "{}（戦記）".format(b["title"])
    except (ValueError, IndexError):
        return "戦記"
