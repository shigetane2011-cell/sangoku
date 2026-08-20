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
# 戦記の敵は常にこのぶん重い（§7.62）。実測合わせ: 軍師の草案を無調整で
# ぶつけたときの平均勝率が +0点=61% / **+1点=51%** / +2点=40%。押すだけなら
# 五分、編成を直せば勝てる — が狙いなので +1。
SENKI_EDGE = 1.0

# 周回スケーリング（§7.60）: **2周に1点、家来を強い実カードへ差し替える。**
#   周回Nの敵 ＝ 章ボスのデッキの雑兵を（大将と登用対象は固定のまま）
#   同兵種・+1点の実在カードへ順に入れ替えたもの（合計 素+⌊N/2⌋点）。
# 差し替えなら**技ごと強くなる**。ここに至る棄却の履歴:
#   1) +N点を cost へ等分 → コスト曲線は兵力に平らで技が買えず、雑兵構成の
#      ボスは+30点でも脅威にならなかった（実測）。
#   2) 兵力×(1+1.2%N) の乗算 → 効くが、毎周同じ敵の数字が増えるだけで、
#      技の穴も残る。差し替え式は毎周顔ぶれが変わり、パズルが更新される。
# ペースは実測合わせ: 1点/周だと壁が周回3〜5に来て急すぎた。2周に1点で
# 最初の壁（黄巾）が周回8〜10・赤壁12〜14・五丈原26〜30（例デッキ据え置き）。
# デッキがフル強化に達したら以後は兵力+1.2%/周の乗算へ接続（LAP_RATE）。
# プール改訂時は senki の壁も測り直すこと。
LAP_STEP_EVERY = 2      # 何周ごとに家来が1点ぶん入れ替わるか
LAP_RATE = 0.012        # フル強化後の継続スケール（兵力/周）

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
    """挑戦ラダーの戦場解放（§7.60: 官渡=第4章到達・赤壁=第6章到達）。

    フリー対戦・ルーム・天下はこの門を通さない（天下は3デッキ登録が実質の
    門になっている）。試験用の全部持ち（dev_senki）は cleared を進めるので
    自然に全戦場が開く。
    """
    c = cleared(cx, player_id)
    return {"汜水関": True,
            "官渡": c >= _ch_first(4),
            "赤壁": c >= _ch_first(6)}


# ------------------------------------------------------- 持ち込み（§7.62）
def enemy_cost(cards, b: Dict) -> float:
    return enemy_army(cards, b).total_cost()


def player_cap(cards, b: Dict) -> float:
    """その戦へ持ち込める上限。**敵と同じ重さ**が原則（章ボスだけ敵が
    BOSS_EXTRA ぶん重い＝格上）。戦場の上限は超えない。

    こうしないと戦記は作業になる: 敵の手組みデッキは物語の顔ぶれ優先で
    上限を余らせており（実測で +1〜+23点。第7章は敵17点 対 自軍40点）、
    こちらは登録デッキのまま踏み潰すだけになっていた。上限を敵に合わせると
    毎戦が別の詰将棋になり、**準備が意味を持つ**（テストプレイの指摘）。
    """
    cap = (enemy_cost(cards, b) - SENKI_EDGE
           - (BOSS_EXTRA if b["boss"] else 0.0))
    board_cap = dict(M.REGULATIONS)[b["board"]]
    return max(float(M.UNIT_SIZE), min(cap, board_cap))


def suggest_deck(cards, unlocked, b: Dict, seed: int = 0):
    """軍師の草案。その戦の上限ちょうどで、**手持ちだけ**から組む。

    毎回ゼロから組ませると準備が苦行になるので、押せば出陣できる案を必ず
    用意する。強さは保証しない（それを直すのがプレイヤーの仕事）。
    """
    from . import play as PL
    # 敵に出ている人物は草案から外す（同じ顔が両軍に並ぶと締まらない。
    # 規則としては許すが、初期案としては選ばない）
    foes = {M.person_of(c) for c in enemy_army(cards, b).cards}
    pool = [c for c in cards if M.person_of(c) in unlocked]
    names, _note, form = PL.draft_deck(
        pool, b["board"], b["form"], "おまかせ", "おまかせ", "おまかせ",
        seed, foes, cap=player_cap(cards, b), ratio=1.0)
    return names, form


def check_deck(cx, cards, me, b: Dict, names, form):
    """持ち込むデッキの検証。戻りは (Army or None, 不備一覧)。

    戦記は PvE なので**他のデッキとの人物かぶりは見ない**（登録デッキの
    配分に縛られず、手持ちを自由に試せる場にする）。見るのは登用済みか・
    6枚か・配置規則・本陣の規則・持ち込み上限・軍功予算。
    """
    from . import play as PL
    army, errs = PL.parse_deck(cards, F.TRAIT_SEP.join(names), form)
    if errs or army is None:
        return None, errs
    unl = PL.ensure_unlocks(cx, me.id)
    locked = sorted({M.person_of(c) for c in army.cards} - unl)
    if locked:
        errs.append("まだ登用していない: " + "・".join(locked))
    if len(army.cards) != M.UNIT_SIZE:
        errs.append("{}人必要（いまは{}人）".format(M.UNIT_SIZE, len(army.cards)))
    persons = [M.person_of(c) for c in army.cards]
    dup = sorted({p for p in persons if persons.count(p) > 1})
    if dup:
        errs.append("同じ人物は1枚まで: " + "・".join(dup))
    cap = player_cap(cards, b)
    if army.total_cost() > cap + 1e-9:
        errs.append("合計 {:g}点 が持ち込み上限 {:g}点 を超えている".format(
            army.total_cost(), cap))
    errs += M.placement_errors(army)
    army, kou = PL._apply_onsho(cx, me.id, army)
    if kou > PL.onsho_budget_kou(cap):
        errs.append("軍功 {}功 が予算 {}功 を超えている".format(
            kou, PL.onsho_budget_kou(cap)))
    honjin = [c for c in army.cards if "command" in F.trait_keys(c.trait)]
    if len(honjin) > 1:
        errs.append("本陣は1部隊に1人まで")
    for c in honjin:
        if c.typ != F.ARC:
            errs.append("本陣は弓兵にだけ授けられる（{}）".format(c.name))
    return (None, errs) if errs else (army, [])


def save_last_deck(cx, player_id: str, i: int, names, form: str) -> None:
    """その戦へ持ち込んだ編成を覚える（§7.62）。負けても消えない。"""
    with cx:
        cx.execute(
            "INSERT INTO senki_decks (player_id, battle_i, cards, formation)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(player_id, battle_i) DO UPDATE SET"
            " cards = excluded.cards, formation = excluded.formation,"
            " updated_at = datetime('now')",
            (player_id, i, F.TRAIT_SEP.join(names), form))


def last_deck(cx, player_id: str, i: int):
    r = cx.execute("SELECT cards, formation FROM senki_decks"
                   " WHERE player_id = ? AND battle_i = ?",
                   (player_id, i)).fetchone()
    if r is None:
        return None
    return {"cards": [x.strip() for x in r["cards"].split(F.TRAIT_SEP)
                      if x.strip()],
            "form": r["formation"]}


# ---------------------------------------------------------------- 戦闘
def fight(cx, cards, me, idx: int, now: int, deck=None) -> Dict:
    """戦記の1戦。勝てば（未クリアの戦なら）進行が進み、登用が起きる。

    deck は {"cards": [名前...], "form": 陣形}。戦前の間（§7.62）で選んだ
    その戦だけの編成で、登録デッキとは無関係（レートも兵符も動かない）。
    省略時は軍師の草案で出る。乱数は挑戦ごとに引き直す。
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
    if deck and deck.get("cards"):
        names, form = list(deck["cards"]), deck.get("form") or b["form"]
    else:
        names, form = suggest_deck(cards, PL.ensure_unlocks(cx, me.id), b, now)
        if not names:
            return {"error": "手持ちでこの戦の編成が組めない"}
    ua, errs = check_deck(cx, cards, me, b, names, form)
    if errs:
        return {"error": "／".join(errs)}
    save_last_deck(cx, me.id, b["i"], names, form)
    reg_i = PL.REG_NAMES.index(b["board"])
    foe = enemy_army(cards, b)
    seed = L.battle_seed("senki", b["i"], me.id, now)
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


# ---------------------------------------------------------------- 周回（戦記番付）
def pool_version() -> str:
    """カードプールの版。記録に添える（後の改訂で条件が変わるため、番付は
    「当時の記録」として読む — 陸上の世界記録と同じ扱い）。"""
    import os
    import zlib
    p = os.path.join(R.DATA, "generals.csv")
    with open(p, "rb") as f:
        return format(zlib.crc32(f.read()) & 0xFFFFFFFF, "08x")


def boss_battles() -> List[Dict]:
    return [b for b in battles() if b["boss"]]


def lap_state(cx, player_id: str) -> Dict:
    r = cx.execute("SELECT lap, stage, zanhei FROM senki_laps"
                   " WHERE player_id = ?", (player_id,)).fetchone()
    if r is None:
        return {"lap": 1, "stage": 0, "zanhei": 0}
    return {"lap": r["lap"], "stage": r["stage"], "zanhei": r["zanhei"]}


def _lap_save(cx, player_id: str, lap: int, stage: int, zanhei: int) -> None:
    with cx:
        cx.execute(
            "INSERT INTO senki_laps (player_id, lap, stage, zanhei)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(player_id) DO UPDATE SET lap = excluded.lap,"
            " stage = excluded.stage, zanhei = excluded.zanhei,"
            " updated_at = datetime('now')",
            (player_id, lap, stage, zanhei))


def banzuke(cx, limit: int = 20) -> List[Dict]:
    """戦記番付。各人の最高記録（＝最深の完走周回）を (周回, 残兵) の辞書順で。"""
    rows = cx.execute(
        "SELECT r.player_id, r.lap, r.zanhei, r.version, r.done_at"
        " FROM senki_records r"
        " JOIN (SELECT player_id, MAX(lap) AS ml FROM senki_records"
        "       GROUP BY player_id) t"
        "   ON t.player_id = r.player_id AND t.ml = r.lap"
        " ORDER BY r.lap DESC, r.zanhei DESC, r.done_at LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def _upgraded(cards, b: Dict, steps: int):
    """章ボスのデッキへ差し替えを steps 点ぶん適用した軍と、実際に足せた
    点数を返す（§7.60 周回）。

    規則（決定論 — 全プレイヤーに同一の敵が出る）:
      - 大将と登用対象（recruits の人物）は固定。ボスの顔は変えない。
      - それ以外の**最安**の家来から、同兵種・+1点（無ければ+2、+3…）の
        実在カードへ差し替える。後衛の枠は弓か槍持ちだけ（配置規則を保つ）。
      - 同点の候補は 武勇+知略が高い順 → 名前順（プール改訂で変わり得るが、
        記録には版が付くので「当時の記録」として読める）。
    """
    import dataclasses
    base = enemy_army(cards, b)
    fixed = set(b["recruits"])
    deck = list(base.cards)
    n_front = base.form.n_front
    total = base.total_cost()
    target = total + steps
    used = 0
    guard = 0
    while total < target and guard < 200:
        guard += 1
        persons = {M.person_of(c) for c in deck}
        order = sorted((i for i, c in enumerate(deck)
                        if M.person_of(c) not in fixed),
                       key=lambda i: (deck[i].cost, deck[i].name))
        done = False
        for inc in (1, 2, 3):
            if total + inc > target + 1e-9:
                break
            for i in order:
                c = deck[i]
                rear = i >= n_front
                cand = [x for x in cards
                        if abs(x.cost - (c.cost + inc)) < 1e-9
                        and x.typ == c.typ
                        and (not rear or x.typ == F.ARC or x.spear)
                        and M.person_of(x) not in persons]
                if cand:
                    cand.sort(key=lambda x: (-(x.might + x.wits), x.name))
                    deck[i] = cand[0]
                    total += inc
                    used += inc
                    done = True
                    break
            if done:
                break
        if not done:
            break                    # フル強化（もう上がない）
    return dataclasses.replace(base, cards=tuple(deck)), used


def lap_enemy(cards, b: Dict, lap: int):
    """周回 lap の敵。戻りは (army, 実際に足した点数, 乗算)。

    2周に1点、家来を実カードへ差し替える。デッキがフル強化に達したら、
    以後の周は兵力×(1 + LAP_RATE×経過周) の乗算で継続（打ち止めを作らない）。
    """
    from . import play as PL
    steps = lap // LAP_STEP_EVERY
    army, used = _upgraded(cards, b, steps)
    mult = 1.0
    if used < steps:
        mult = 1.0 + LAP_RATE * (lap - used * LAP_STEP_EVERY)
        army = PL.army_boost(army, mult)
    return army, used, mult


def lap_fight(cx, cards, me, now: int) -> Dict:
    """戦記番付の1戦（全クリア後の章ボス8連戦・§7.60）。

    周回Nの敵＝lap_enemy（家来の差し替え＝技ごと強くなる）。勝てば残兵
    （自軍の残り兵の合計）がその周の点に積まれ、8人抜きで記録に登録して
    次の周へ。負けは何も減らない（乱数式・再挑戦自由。スコアを伸ばすには
    編成か試行を重ねる）。
    """
    from . import play as PL
    if cleared(cx, me.id) < len(battles()):
        return {"error": "戦記番付は全戦クリア後に開く"}
    st = lap_state(cx, me.id)
    bs = boss_battles()
    b = bs[st["stage"]]
    entry, ok, errs = PL.entry_of(cx, cards, me.id, me.display_name)
    if not ok.get(b["board"]):
        return {"error": "{} のデッキが出せる状態にない".format(b["board"])}
    reg_i = PL.REG_NAMES.index(b["board"])
    foe, plus_pts, mult = lap_enemy(cards, b, st["lap"])
    seed = L.battle_seed("senki-lap", st["lap"], b["i"], me.id, now)
    ua = entry.unit(reg_i)
    r = M.play_one(PL.BoardEntry({reg_i: ua}), PL.BoardEntry({reg_i: foe}),
                   reg_i, 0.5, seed=seed)
    won = r["winner"] == "A"
    bid = P.record_battle(
        cx, "senki", b["board"], me.id, "senki:{}".format(b["i"]), seed,
        _json.dumps(PL.snap_army(ua), ensure_ascii=False),
        _json.dumps(PL.snap_army(foe, mult=mult), ensure_ascii=False),
        PL.season_key(now), now)
    out = {"battle_id": bid, "title": "{}（周回{}）".format(b["title"], st["lap"]),
           "win": "勝ち" if won else ("負け" if r["winner"] == "B" else "引き分け"),
           "lap": st["lap"], "stage": st["stage"], "zanhei": st["zanhei"],
           "plus_pts": plus_pts,
           "mult_pct": round((mult - 1.0) * 100, 1)}
    if won:
        gained = round(sum(x[3] for x in r["結果"]["dealt_a"]))
        stage, zanhei = st["stage"] + 1, st["zanhei"] + gained
        out["gained"] = gained
        if stage >= len(bs):
            with cx:
                cx.execute(
                    "INSERT OR REPLACE INTO senki_records"
                    " (player_id, lap, zanhei, version) VALUES (?, ?, ?, ?)",
                    (me.id, st["lap"], zanhei, pool_version()))
            _lap_save(cx, me.id, st["lap"] + 1, 0, 0)
            out["lap_done"] = {"lap": st["lap"], "zanhei": zanhei}
        else:
            _lap_save(cx, me.id, st["lap"], stage, zanhei)
            out["stage"] = stage
            out["zanhei"] = zanhei
    return out


def title_of(pid: str) -> str:
    """battles 表の pid（"senki:12"）を戦名へ。リプレイ一覧の表示用。"""
    try:
        b = battles()[int(pid.split(":", 1)[1])]
        return "{}（戦記）".format(b["title"])
    except (ValueError, IndexError):
        return "戦記"
