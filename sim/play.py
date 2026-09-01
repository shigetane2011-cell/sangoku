# -*- coding: utf-8 -*-
"""sim/play.py -- 実プレイヤーの導線（まず1人で回す・§7.40）

登録 → デッキを組む → 検証 → ランク戦の巡へ参加 → リプレイと順位を見る、を
CLI で通す。盤面・マッチ・順位表の実装は field/match/ladder をそのまま使い、
ここは**配線だけ**を持つ（同じ量の定義を2箇所に持たない）。

  python3 -m sim.play register --name 自分 --email you@example.com
  python3 -m sim.play roster --reg 汜水関
  python3 -m sim.play deck --player <id> --reg 汜水関 \\
      --cards "張飛、許褚、趙雲、黄忠、夏侯淵" --form 狭く深い
  python3 -m sim.play status --player <id>
  python3 -m sim.play round --player <id> [--board 汜水関] [--replay]
  python3 -m sim.play standings [--board 汜水関]

設計メモ:
- **デッキの並び順がそのまま配置**（前衛から）。陣形が前衛の枚数を決めるので、
  「雁行」なら先頭2枚が前衛（近接）、残り4枚が後衛（弓）になる。
- round は本来「組む→告知→編成期間→戦う」（§3）だが、CLI では告知と戦いを
  続けて行う。組み方は plan_round のまま決定的なので、サーバー化しても同じ組になる。
- ダミーの編成は (性格, 通し番号) から決定的に再構成できるので DB には持たない。
  レートと巡カウンタだけを持つ（players.ratings / boards）。
- 対戦の乱数種は ladder.battle_seed（CRC32）。**プロセスが変わっても同じ巡は
  同じ戦いになる**（§8.4。Python の hash() は塩が変わるので使えない）。
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Optional, Tuple

from . import field as F
from . import ladder as L
from . import match as M
from . import players as P
from . import dummies as D
from . import rosterdata as R

FORM_BY_NAME = D.FORM_BY_NAME
REG_NAMES = tuple(label for label, _ in M.REGULATIONS)
MIN_DUMMIES = 36    # 在野の数（§7.83 で 15→24、§7.133 で 24→36。12性格×3）


# ----------------------------------------------------------------------------
# カードとデッキ
# ----------------------------------------------------------------------------

def _card_index(cards) -> Dict[str, F.Card]:
    """武将名（字号つき）と人物名の両方から引ける索引。人物名は一意のときだけ。"""
    idx: Dict[str, F.Card] = {}
    by_person: Dict[str, List[F.Card]] = {}
    for c in cards:
        idx[c.name] = c
        by_person.setdefault(M.person_of(c), []).append(c)
    for person, cs in by_person.items():
        if len(cs) == 1 and person not in idx:
            idx[person] = cs[0]
    return idx


def parse_deck(cards, names_raw: str, form_name: str
               ) -> Tuple[Optional[F.Army], List[str]]:
    """「、」区切りの武将名と陣形名から Army を作る。errs が空なら成功。

    陣形の旧名（広く浅い/標準/狭く深い）は読み替えて受ける（DB に残っていても
    壊れないように）。"""
    errs: List[str] = []
    form = FORM_BY_NAME.get(F.FORM_ALIAS.get(form_name, form_name))
    if form is None:
        return None, ["陣形は {} のどれか".format("・".join(FORM_BY_NAME))]
    idx = _card_index(cards)
    picked: List[F.Card] = []
    for raw in re.split(r"[、,]", names_raw):
        n = raw.strip()
        if not n:
            continue
        c = idx.get(n)
        if c is None:
            errs.append("{} というカードが見つからない（同名複数なら字号まで書く）"
                        .format(n))
            continue
        picked.append(c)
    if errs:
        return None, errs
    return F.Army(tuple(picked), form), errs


# 軍功予算（§7.61）。恩賞のセットはデッキ本体の点でなく**専用の別予算**を
# 消費する。単位は「功」＝0.01コスト点の整数（表示に小数点を出さない）。
# 全員一律なので、古参と新規の差は強さでなく品揃え（選択肢の数）だけになる
# （§3.1: 在席時間を強さにしない）。値付けは従来の実測表のまま。
# 予算は**戦場比例**: コスト上限×5功（汜水関90・官渡150・赤壁200）。


def onsho_budget_kou(cap: float) -> int:
    return int(round(cap * 5))


# ── 恩賞の品揃え（§7.70）────────────────────────
#
# **一様抽選をやめた**（20種一様だと55%の日が10功以下の小物＝ハズレ日）。
# 日替わりで 小・中・大 から1つずつの3候補を出し、1つ選ぶ。候補は
# (プレイヤーID, 日付, 段) から決定的 — 読み直しで引き直せないのは従来通り。
#
# 2〜4功の攻バフ小物5種（呼応・号令・弔い合戦・遺志・弔旗）は**恩賞の
# プールから外した**（生まれつきの特性としては健在）。効果が薄く重複していて、
# 引いた日の喜びが無い。
#
# 寄せの書（§7.66 の防御寄せ・速度寄せを動かす書物）は**恩賞にしか無い**
# 品。交換レートは実測で釣り合い済みなので功は0 — 強さでなく形を変える
# サイドグレード。速度は値段0・±0.3制限（§7.66）の範囲でだけ動かす。
ONSHO_BOOKS = {
    "book_kenjin": ("堅陣の書", "防御寄せ +0.3 — 鎧が2割方厚くなり、そのぶん兵が薄くなる（釣り合いは§7.66の実測）", 0.3, 0.0),
    "book_keisou": ("軽装の書", "防御寄せ -0.3 — 鎧を軽くして兵を厚くする", -0.3, 0.0),
    "book_shikku": ("疾駆の書", "速度寄せ +0.3 — 足が1割方速くなる（強さは動かない・演出の書）", 0.0, 0.3),
}
ONSHO_TIERS = {
    "大": ("command", "vanguard", "vs_wei", "vs_shu", "vs_go"),
    "中": ("disrupt", "laststand", "cheer", "diehard"),
    "小": ("relief", "bloodpath", "sustain", "double", "rearguard", "pursuit",
           "book_kenjin", "book_keisou", "book_shikku"),
}


def onsho_candidates(player_id: str, today: str):
    """本日の3候補（小・中・大から1つずつ）。決定的で引き直せない。"""
    import zlib
    out = []
    for tier in ("小", "中", "大"):
        pool = ONSHO_TIERS[tier]
        k = pool[zlib.crc32((player_id + today + tier).encode()) % len(pool)]
        out.append((tier, k))
    return out


def kou_of(key: str) -> int:
    """特性1つの値段（功・整数）。実測値段 × 100 の丸め。書は0（釣り合い済み）。"""
    if key in ONSHO_BOOKS:
        return 0
    from . import design as D
    return int(round(D.trait_value(key) * 100))


def _apply_onsho(cx, player_id: str, army: F.Army) -> Tuple[F.Army, int]:
    """セット済みの恩賞（軍功枠・§7.43）を札へ合流し、功の合計を返す。

    盤面は card.trait の「、」区切りを複数特性として読む（§7.37）。値段は
    実測表 `design.trait_value` を功に丸めたもの — **タダ盛りにはしない**が、
    デッキ本体の上限でなく軍功予算（ONSHO_BUDGET_KOU）から払う（§7.61。
    「ご褒美を付けたら編成が崩れる」体験を避ける）。生まれつきの特性は
    効果予算で支払い済みなので数えない。"""
    import dataclasses
    extra = 0
    out = []
    for c in army.cards:
        keys = [k for k in P.traits_on(cx, player_id, c.name)
                if k not in F.trait_keys(c.trait)]
        books = [k for k in keys if k in ONSHO_BOOKS]
        keys = [k for k in keys if k not in ONSHO_BOOKS]
        if keys:
            extra += sum(kou_of(k) for k in keys)
            c = dataclasses.replace(
                c, trait=F.TRAIT_SEP.join(list(F.trait_keys(c.trait)) + keys),
                hidden_trait=F.TRAIT_SEP.join(keys))
        if books:
            # 寄せの書（§7.70）: 特性でなくカードの枠を動かす。対価は盤面が
            # lean_men_comp で自動で払う（功0）。積み過ぎは寄せの上限で頭打ち。
            dd = sum(ONSHO_BOOKS[k][2] for k in books)
            ds = sum(ONSHO_BOOKS[k][3] for k in books)
            c = dataclasses.replace(
                c, def_lean=max(-1.0, min(1.0, c.def_lean + dd)),
                spd_lean=max(-1.0, min(1.0, c.spd_lean + ds)))
        out.append(c)
    return dataclasses.replace(army, cards=tuple(out)), extra


# ── 宝物（§7.138・恩賞の後継）────────────────────────
#
# 品揃えは sim/data/treasures.csv（18種・帯=大7/中6/小5・**数値は全部仮**）。
# 同じ宝物は1人1個・1武将に1個。獲得は§7.70の日替わり3択の器のまま、
# 候補を**未所持からだけ**引く。値段は CSV の功列（手書きの仮値 —
# design.trait_value は新キーの実測値を持たないので通さない。実測は別タスク）。

_TREASURES: "Dict[str, Dict[str, str]] | None" = None


def treasure_rows() -> Dict[str, Dict[str, str]]:
    """宝物の台帳（キー→行）。挿入順=CSV順を保つ — 抽選の決定性の土台。"""
    global _TREASURES
    if _TREASURES is None:
        from . import rosterdata as R
        _TREASURES = {t["キー"]: t for t in R.treasures()}
    return _TREASURES


def treasure_tiers() -> Dict[str, List[str]]:
    tiers: Dict[str, List[str]] = {"小": [], "中": [], "大": []}
    for k, t in treasure_rows().items():
        tiers[t["帯"]].append(k)
    return tiers


def treasure_candidates(player_id: str, today: str, owned) -> List[Tuple[str, str]]:
    """本日の3候補（小・中・大から1つずつ・**未所持からだけ**）。決定的で
    引き直せない（§7.70 と同じ crc32）。帯を集めきったらその帯は候補なし —
    全部集めたら日替わりは店じまい。所持集合は日次ガードで日内不変なので、
    読み直しでもプールが揺れず、同じ候補が出続ける。"""
    import zlib
    out = []
    for tier in ("小", "中", "大"):
        pool = [k for k in treasure_tiers()[tier] if k not in owned]
        if not pool:
            continue
        k = pool[zlib.crc32((player_id + today + tier).encode()) % len(pool)]
        out.append((tier, k))
    return out


def treasure_kou(key: str) -> int:
    """宝物1つの値段（功・整数）。CSV の手書き仮値。実測は別タスク（§7.138）。"""
    return int(treasure_rows()[key]["功"])


def treasure_budget_kou(cap: float) -> int:
    """軍功予算（§7.61 のまま・戦場比例 cap×5功）。宝物もここから払う。"""
    return int(round(cap * 5))


# 札そのものを動かす宝物（§7.138・数値は全部仮）。効果の実体が Card の
# フィールドにあるもの — 武具の武力・書の知力・鎧/鞍の寄せ・馬の速度寄せ。
TREASURE_CARD_MODS: Dict[str, Dict[str, float]] = {
    "t_seiryu": {"might": 15.0},        # 青龍偃月刀: 武力+15
    "t_hakuusen": {"wits": 15.0},       # 白羽扇: 知力+15
    "t_gentetsu": {"def_lean": 0.3},    # 玄鉄の鎧（堅陣の書の後継）
    "t_keiki": {"def_lean": -0.3},      # 軽騎の鞍（軽装の書の後継）
    "t_sekitoba": {"spd_lean": 0.3},    # 赤兎馬の足（兵力+2%は盤面の定数側）
}


def apply_treasure_card_mods(card: F.Card) -> F.Card:
    """宝物の札モッドを写す（§7.138・純関数）。

    **装備経路（_apply_treasures）と陣容の復元経路（army_from_snap）の両方が
    「素の札へキーを合流した直後に1回」呼ぶ。** 陣容は名前と特性キーしか
    持たないので、ここを復元側でも通さないと、リプレイが宝物抜きの素の
    強さで再生されてしまう（旧・寄せの書は陣容にキーが残らず、実際この穴が
    あった — 宝物では全キーを trait/hidden_trait に運んで塞ぐ）。"""
    import dataclasses
    d_might = d_wits = dd = ds = 0.0
    for k in F.trait_keys(card.trait):
        m = TREASURE_CARD_MODS.get(k)
        if not m:
            continue
        d_might += m.get("might", 0.0)
        d_wits += m.get("wits", 0.0)
        dd += m.get("def_lean", 0.0)
        ds += m.get("spd_lean", 0.0)
    kw = {}
    if (d_might or d_wits) and card.might > 0.0:
        # 実カードだけ（合成カードは might=0 の指定なし運用・§6.3）。
        # wits==0 は might へ落ちる仕様（Unit.__init__）なので先に展開して足す。
        kw["might"] = card.might + d_might
        kw["wits"] = (card.wits or card.might) + d_wits
    if dd:
        kw["def_lean"] = max(-1.0, min(1.0, card.def_lean + dd))
    if ds:
        kw["spd_lean"] = max(-1.0, min(1.0, card.spd_lean + ds))
    return dataclasses.replace(card, **kw) if kw else card


def _apply_treasures(cx, player_id: str, army: F.Army) -> Tuple[F.Army, int]:
    """持たせた宝物（§7.138）を札へ合流し、功の合計を返す。

    キーは trait と hidden_trait の**両方**へ乗せる（§7.136 の秘匿と、
    陣容→リプレイ再構成の運搬役）。効果の実体が盤面の定数側にある宝物も、
    札モッド側にある宝物も、演出だけの宝物も、**記録には全キーが要る**。"""
    import dataclasses
    extra = 0
    out = []
    for c in army.cards:
        keys = [k for k in P.treasures_on(cx, player_id, c.name)
                if k not in F.trait_keys(c.trait)]
        if keys:
            extra += sum(treasure_kou(k) for k in keys)
            c = dataclasses.replace(
                c, trait=F.TRAIT_SEP.join(list(F.trait_keys(c.trait)) + keys),
                hidden_trait=F.TRAIT_SEP.join(
                    list(F.trait_keys(c.hidden_trait)) + keys))
            c = apply_treasure_card_mods(c)
        out.append(c)
    return dataclasses.replace(army, cards=tuple(out)), extra


class BoardEntry:
    """レギュレーションごとの部隊の入れ物（§7.48）。

    M.Entry は3部隊固定（BO3 の登録の器）だが、**1デッキでも BO1 には
    出られる**ようにする。unit(i) だけ互換にしてあり、盤面の解決・リプレイは
    どちらの器でも動く。天下(BO3) は3つ揃った時だけ。
    """

    def __init__(self, units: Dict[int, F.Army], name: str = ""):
        self.units_map = units
        self.name = name

    def unit(self, i: int) -> F.Army:
        return self.units_map[i]

    @property
    def units(self):
        return tuple(self.units_map[i] for i in range(len(M.REGULATIONS)))


def entry_of(cx, cards, player_id: str, name: str
             ) -> Tuple[BoardEntry, Dict[str, bool], List[str]]:
    """登録から (部隊の入れ物, 盤面ごとの可否, 不備一覧) を作る。

    デッキごとに独立して検証し、valid なデッキの盤面だけ出られる。
    **同一人物は登録デッキ全体で1枚**（§4.1）— BO1 どうしでも共有しない。
    関羽をどの帯で使うかという配分が編成の骨になっているため。
    """
    decks = P.decks_of(cx, player_id)
    units: Dict[int, F.Army] = {}
    reg_errs: Dict[int, List[str]] = {}
    errs: List[str] = []
    for i, (reg, cap) in enumerate(M.REGULATIONS):
        if reg not in decks:
            continue
        raw, form_name = decks[reg]
        army, es = parse_deck(cards, raw, form_name)
        if army is not None and not es:
            army, extra = _apply_treasures(cx, player_id, army)
            if len(army.cards) != M.UNIT_SIZE:
                es.append("{}人必要（いまは{}人）".format(
                    M.UNIT_SIZE, len(army.cards)))
            base = sum(c.cost for c in army.cards)
            if base > cap + 1e-9:
                es.append("合計コスト {:g} が上限 {:g} を超えている".format(base, cap))
            if extra > treasure_budget_kou(cap):
                es.append("軍功 {}功 が予算 {}功 を超えている"
                          "（宝物を外すか安い物へ）".format(
                              extra, treasure_budget_kou(cap)))
            es += M.placement_errors(army)
            # 本陣（§7.52）はデッキに1人まで。宝物は command を配らないので
            # 実質は生まれつきの数だが、防衛的に合流後へ残す（§7.138）。
            honjin = [c for c in army.cards
                      if "command" in F.trait_keys(c.trait)]
            if len(honjin) > 1:
                es.append("本陣は1部隊に1人まで（いまは {}）"
                          .format("、".join(c.name for c in honjin)))
            # 本陣は弓兵（＝後衛）専用。前衛の本陣は実測でほぼ必ず討たれる
            # （§7.52・戦死74〜89%）ので registration で弾く。生まれつきの
            # 3人（袁紹・費禕・劉表）は全員弓兵。
            jp_typ = {F.INF: "歩兵", F.CAV: "騎兵", F.ARC: "弓兵"}
            for c in honjin:
                if c.typ != F.ARC:
                    es.append("本陣は弓兵にだけ授けられる（{} は{}）"
                              .format(c.name, jp_typ.get(c.typ, c.typ)))
        if es:
            reg_errs[i] = es
            errs += ["{}: {}".format(reg, e) for e in es]
        elif army is not None:
            units[i] = army
    # 同一人物の重複は、関わる盤面をどちらも塞ぐ（登録レベルの規則）。
    # **面の境界で除外しない** — 同じ面の中の重複（CLI・直接書き込みなど
    # /api/deck の検証を経ない経路）も見逃さない（§7.135）。
    seen: Dict[str, Tuple[int, str]] = {}
    dup_regs: set = set()
    for i, army in units.items():
        for c in army.cards:
            p = M.person_of(c)
            if p in seen:
                errs.append("{}: {} は {} の {} と同一人物（別バージョンも不可）"
                            .format(M.REGULATIONS[i][0], c.name,
                                    M.REGULATIONS[seen[p][0]][0], seen[p][1]))
                dup_regs |= {i, seen[p][0]}
            else:
                seen[p] = (i, c.name)
    ok = {}
    for i, (reg, _) in enumerate(M.REGULATIONS):
        ok[reg] = i in units and i not in dup_regs
    ok["天下"] = all(ok[r] for r, _ in M.REGULATIONS)
    playable = {i: a for i, a in units.items() if i not in dup_regs}
    return BoardEntry(playable, name=name), ok, errs


# ----------------------------------------------------------------------------
# ダミーの再構成と順位表
# ----------------------------------------------------------------------------

def dummy_entries(cx, cards) -> Dict[str, M.Entry]:
    """登録済みダミーの編成を (性格, 通し番号) から決定的に再構成する。

    名前の読み方は `dummies.parse_name` ただ一つ（§7.102）。ここに自前の
    正規表現を置くと、増員側と食い違ったときに気づけない。
    """
    out: Dict[str, M.Entry] = {}
    for pl in P.all_players(cx, kind=P.DUMMY):
        got = D.parse_name(pl.display_name)
        if not got:
            continue
        k, num = got
        out[pl.id] = D.make_entry(cards, D.PERSONAS[k], D.deck_seed(k, num))
    return out


def load_board(cx, name: str) -> L.Board:
    b = L.Board(name, REG_NAMES.index(name) if name in REG_NAMES else None)
    for pid, (r, g) in P.board_ratings(cx, name).items():
        b.rating[pid] = r
        b.games[pid] = g
    return b


def save_board(cx, b: L.Board) -> None:
    P.save_board_ratings(cx, b.name, {
        pid: (b.rating[pid], b.games.get(pid, 0)) for pid in b.rating})


def ensure_dummies(cx, cards) -> Dict[str, M.Entry]:
    """足りなければ在野を足す。**頭数ではなく「埋まっている枠」で数える。**

    旧実装（§7.102）が作った同名の在野が残っている DB では、頭数だけ
    MIN_DUMMIES に届いてしまい、**欠けている性格が永久に埋まらない**。
    枠（性格, 通し番号）の異なり数で見れば、同名がいくつ居ても正しく足りる。
    既に居る同名はそのまま残す（武名を持っているので消さない）。
    """
    ents = dummy_entries(cx, cards)
    have = sum(len(v) for v in D.existing_slots(cx).values())
    if have < MIN_DUMMIES:
        # **名前と番号は seed_ladder が数えて決める**（§7.102）。ここから
        # 通し番号を渡すと、性格を足したときに割り当てがずれて重複が出る。
        D.seed_ladder(cx, cards, MIN_DUMMIES - have)
        ents = dummy_entries(cx, cards)
    return ents


# ----------------------------------------------------------------------------
# コマンド
# ----------------------------------------------------------------------------

def cmd_register(args) -> None:
    cx = P.connect(args.db)
    pl = P.register(cx, args.name, kind=P.HUMAN, email=args.email)
    print("登録した: {}  id={}".format(pl.display_name, pl.id))
    print("次: python3 -m sim.play roster でカードを見て、deck でデッキを組む")


def _canon_reg(name):
    return M.REG_ALIAS.get(name, name) if name else name


def cmd_roster(args) -> None:
    gs = R.generals()
    args.reg = _canon_reg(args.reg)
    if args.reg:
        cap = dict(zip(REG_NAMES, (c for _, c in M.REGULATIONS)))[args.reg]
        print("{}（上限 {:g}点・6枚・弓は後衛だけ・同一人物は3部隊で1枚）"
              .format(args.reg, cap))
    for g in sorted(gs, key=lambda g: (-float(g["コスト"]), g["名前"])):
        print("  {:>2}点 {:<3} {:<14} 兵法:{} 特性:{}".format(
            g["コスト"], g["兵種"], g["名前"], g["兵法"], g["固有特性"] or "-"))


def cmd_deck(args) -> None:
    cx = P.connect(args.db)
    args.reg = _canon_reg(args.reg)
    cards = M._roster_cards()
    army, errs = parse_deck(cards, args.cards, args.form)
    if not errs:
        cap = dict(zip(REG_NAMES, (c for _, c in M.REGULATIONS)))[args.reg]
        errs += ["{}".format(e) for e in M.placement_errors(army)]
        cost = army.total_cost()
        if cost > cap + 1e-9:
            errs.append("合計コスト {:g} が上限 {:g} を超えている".format(cost, cap))
        if len(army.cards) != M.UNIT_SIZE:
            errs.append("{}人必要（いまは{}人）".format(M.UNIT_SIZE, len(army.cards)))
    if errs:
        print("登録できない:")
        for e in errs:
            print("  - " + e)
        sys.exit(1)
    P.set_deck(cx, args.player, args.reg, args.cards, args.form)
    left = cap - cost
    print("{} に登録した（合計 {:g}点 / 上限 {:g}点・余り {:g}点は初期ゲージ"
          "{:.0%}に変わる）".format(args.reg, cost, cap,
                                    left, M.surplus_ratio(army, cap)))


def cmd_status(args) -> None:
    cx = P.connect(args.db)
    cards = M._roster_cards()
    pl = P.get(cx, args.player)
    if pl is None:
        print("その id の登録者が居ない"); sys.exit(1)
    print("{}  ({})".format(pl.display_name, pl.id))
    decks = P.decks_of(cx, pl.id)
    for reg in REG_NAMES:
        if reg in decks:
            raw, fm = decks[reg]
            army, es = parse_deck(cards, raw, fm)
            cost = army.total_cost() if army else 0.0
            print("  {:<6} [{}] {:g}点  {}".format(reg, fm, cost, raw))
        else:
            print("  {:<6} 未登録".format(reg))
    entry, ok, errs = entry_of(cx, cards, pl.id, pl.display_name)
    for e in errs:
        print("  - " + e)
    print("出られる順位表: " + ("・".join(b for b in L.BOARDS if ok.get(b)) or "なし"))
    for name in L.BOARDS:
        r = P.board_ratings(cx, name)
        if pl.id in r:
            b = load_board(cx, name)
            pids = list(r)
            rank = b.order(pids).index(pl.id) + 1
            print("  {:<10} 武名{:.0f}  {}位/{}人  {}戦".format(
                name, r[pl.id][0], rank, len(pids), r[pl.id][1]))


def all_human_entries(cx, cards) -> Dict[str, Tuple["BoardEntry", Dict[str, bool]]]:
    """人間全員の (部隊, 盤面可否)。告知は**資格のある全員**で組む（§7.48）。

    その巡の参加者だけで次巡を告知すると、一度あぶれた人間が告知に入れず
    **恒久的にはぶられ続ける**（実測）。"""
    out = {}
    for p in P.all_players(cx, kind=P.HUMAN):
        e, ok, _ = entry_of(cx, cards, p.id, p.display_name)
        if any(ok.values()):
            out[p.id] = (e, ok)
    return out


def full_board_entries(cx, dummies, humans, board_name: str):
    """その盤面の完全な参加者集合（在野 + 資格のある人間全員・偶数化つき）。"""
    ent = dict(dummies)
    for pid, (e, ok) in humans.items():
        if ok.get(board_name):
            ent[pid] = e
    if len(ent) % 2 == 1:
        b = load_board(cx, board_name)
        hum = set(humans)
        low = min((p for p in ent if p not in hum), key=lambda p: b.get(p),
                  default=None)
        if low is not None:
            del ent[low]
    return ent


def board_entries(cx, dummies, entry, ok, board_name: str, me_id: str):
    """その盤面の参加者を組む。**奇数なら最下位の在野を今巡休みにする**（§7.48）。

    奇数のまま組むと毎巡1人あぶれ、人間があぶれ得る（実測: 「出陣したのに
    自分だけ居ない」）。休むのは在野から選ぶ — 足場は人間のためにある。
    """
    ent = dict(dummies)
    if ok.get(board_name):
        ent[me_id] = entry
    if len(ent) % 2 == 1:
        b = load_board(cx, board_name)
        low = min((p for p in ent if p != me_id), key=lambda p: b.get(p),
                  default=None)
        if low is not None:
            del ent[low]
    return ent


def announce(cx, entries: Dict[str, M.Entry], board_name: str
             ) -> Tuple[int, List[Tuple[str, str]]]:
    """次の巡の組を告知する（§3: 組む→告知→編成期間→戦う）。

    既に告知済みならそれを返す。**告知後のデッキ変更は自由**（それがこの
    2段階の目的。相手の陣形を見て自陣を差し替える駆け引き）。組は変わらない。
    """
    rnd = P.board_round(cx, board_name)
    pairs = P.load_pairs(cx, board_name, rnd)
    if not pairs:
        b = load_board(cx, board_name)
        pairs = L.plan_round(b, list(entries), rnd)
        P.save_pairs(cx, board_name, rnd, pairs)
    return rnd, pairs


def run_round(cx, cards, entries: Dict[str, M.Entry], board_name: str,
              dt: float = 0.5) -> Tuple[L.Board, int, List[Tuple[str, str]]]:
    """1つの順位表で1巡回し、全対戦を記録する（CLI と Web の共通部）。

    告知済みの組があれば**その組で**戦う（組み直さない）。終わったらすぐ
    次の巡を告知するので、戦い終えた瞬間から次の相手が見える。
    """
    b = load_board(cx, board_name)
    rnd, pairs = announce(cx, entries, board_name)
    pairs = [(x, y) for x, y in pairs if x in entries and y in entries]
    L.resolve_round(b, pairs, entries, rnd, dt=dt)
    save_board(cx, b)
    P.bump_board_round(cx, board_name)
    P.clear_pairs(cx, board_name, rnd)
    for x, y in pairs:
        P.record_match(cx, board_name, rnd, x, y,
                       L.battle_seed(board_name, rnd, x, y))
    announce(cx, entries, board_name)      # 次の巡をすぐ告知
    return b, rnd, pairs


# ----------------------------------------------------------------------------
# ⑨ 出陣（§7.58）: 挑戦ラダー・天下の定刻開催・フリー対戦・月次シーズン
# ----------------------------------------------------------------------------
#
# 設計（テストプレイと合意した形）:
#   BO1  = 非同期の挑戦ラダー。兵符1枚でいつでも出陣、相手は同レート帯から
#          システムが選ぶ（対戦カードは事前に見えない）。受ける側は登録デッキが
#          常に防衛に立ち、拒否できない。同じ相手には1時間に1回まで。
#   天下 = 毎時00分の自動開催。3デッキ揃っていれば自動参加・兵符不要。
#          休戦令を1日8枚（8時間）使い、生活時間は組合せ対象から外れる。
#          対戦相手は事前に見せず、開催時点の登録デッキで組んで即時解決する。
#   フリー = 在野といつでも／ルーム番号でプレイヤー同士。レート不変動・兵符不要。
#   シーズン = 月次。月末の順位表と全デッキの陣容を控えてからレートを完全リセット
#          （可変Kが月初を数戦で収束させるので、ソフトリセットは要らない）。
#
# **定刻処理はすべて遅延評価**（tick）。cron が要らず、手元でもクラウドでも
# 同じ動きになる — 兵符の回復と同じ考え方。

import json as _json

TENKA_HOURS = tuple(range(24))  # 天下は毎時00分（サーバーの地方時）
TRUCE_LOCK_SEC = 2 * 3600      # 休戦令は開催2時間前に締切
TRUCE_DAYS_SHOWN = 7           # 画面から日別変更できる範囲


def snap_army(army: F.Army, mult: float = 1.0) -> dict:
    """デッキの陣容。名前と（宝物込みの）特性・陣形だけ持てば再構成できる。

    mult は戦記番付の周回スケーリング（§7.60: 敵全体の兵力×mult）。
    陣容へ記録しないとリプレイが素の強さで再生されてしまう。h は宝物で
    加わったキー（§7.136・§7.138）。実況の種明かし防止と札モッドの復元
    （army_from_snap の apply_treasure_card_mods）は詳報の再生時に
    組み立て直すので、これも一緒に記録しておかないとリプレイでだけ
    正体がバレる／素の強さに戻ってしまう。
    """
    def _card(c: F.Card) -> dict:
        r = {"n": c.name, "t": c.trait}
        if c.hidden_trait:
            r["h"] = c.hidden_trait
        return r
    d = {"form": F.FORM_NAME[army.form.n_front],
         "cards": [_card(c) for c in army.cards]}
    if mult != 1.0:
        d["mult"] = mult
    return d


def army_boost(army: F.Army, mult: float) -> F.Army:
    """軍全体の兵力を mult 倍する（§7.60 周回。F.Card.boost）。ダメージは
    men×atk 比例なので耐久と火力が同率で上がる。

    当初の「+N点を cost/stat_cost へ等分」は棄却した — コスト曲線は兵力に
    対して極端に平ら（値段の大半は兵法が占める）な上、兵法は足せないので、
    雑兵構成のボスでは+30点でも脅威にならなかった（実測）。乗算なら
    どの構成にも同じ率で効き、飽和しない。"""
    import dataclasses
    cards = tuple(dataclasses.replace(c, boost=c.boost * mult)
                  for c in army.cards)
    return dataclasses.replace(army, cards=cards)


def army_plus(army: F.Army, plus: float) -> F.Army:
    """旧・周回上乗せ（+N点を cost/stat_cost へ等分）。**新規には使わない** —
    切り替え前に記録した陣容（"plus" 入り）の再生専用に残してある。"""
    import dataclasses
    per = plus / max(1, len(army.cards))
    cards = tuple(dataclasses.replace(
        c, cost=c.cost + per,
        stat_cost=(c.stat_cost or c.cost) + per) for c in army.cards)
    return dataclasses.replace(army, cards=cards)


def army_from_snap(cards, snap: dict) -> F.Army:
    import dataclasses
    from . import rosterdata as R
    idx = {c.name: c for c in cards}
    picked = []
    for it in snap["cards"]:
        c = idx.get(it["n"])
        if c is None:
            raise KeyError(it["n"])
        c = dataclasses.replace(
            c, trait=it.get("t", c.trait),
            hidden_trait=it.get("h", c.hidden_trait))
        # 宝物の札モッド（§7.138）を復元する。陣容はキーしか運ばないので、
        # ここを通さないとリプレイが宝物抜きの素の強さで再生されてしまう。
        picked.append(apply_treasure_card_mods(c))
    army = F.Army(tuple(picked), FORM_BY_NAME[F.FORM_ALIAS.get(
        snap["form"], snap["form"])])
    if snap.get("plus"):        # 旧形式（切り替え前の陣容）
        army = army_plus(army, float(snap["plus"]))
    if snap.get("mult"):
        army = army_boost(army, float(snap["mult"]))
    return army


def snap_entry(entry) -> str:
    return _json.dumps({"units": [snap_army(entry.unit(i))
                                  for i in range(len(M.REGULATIONS))]},
                       ensure_ascii=False)


def entry_from_snap(cards, js: str):
    d = _json.loads(js)
    if "units" in d:
        return M.Entry(tuple(army_from_snap(cards, s) for s in d["units"]))
    return BoardEntry({i: army_from_snap(cards, d)
                       for i in range(len(M.REGULATIONS))})


def result_mark(score: float) -> str:
    """勝敗の刻み1文字（§7.81）。record_battle の result に積む。"""
    return "○" if score > 0.5 else ("●" if score < 0.5 else "△")


def season_key(now: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(now).strftime("%Y-%m")


def hour_key(now: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H")


def tenka_events(t0: int, t1: int):
    """(t0, t1] にある天下の開催時刻を (通し番号, unix秒) で返す。"""
    import datetime
    out = []
    day = datetime.datetime.fromtimestamp(t0).replace(
        hour=0, minute=0, second=0, microsecond=0)
    while day.timestamp() <= t1:
        for h in TENKA_HOURS:
            t = int(day.replace(hour=h).timestamp())
            if t0 < t <= t1:
                out.append((int(day.strftime("%Y%m%d")) * 100 + h, t))
        day += datetime.timedelta(days=1)
    return out


def _local_day(day: str):
    """YYYY-MM-DDをサーバー地方時の0時へ。存在しない日付もここで弾く。"""
    import datetime
    try:
        d = datetime.datetime.strptime(day, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError("日付が正しくない")
    if d.strftime("%Y-%m-%d") != day:
        raise ValueError("日付が正しくない")
    return d


def _event_time(day: str, hour: int) -> int:
    return int(_local_day(day).replace(hour=int(hour)).timestamp())


def truce_is_active(cx, player_id: str, at: int) -> bool:
    """指定開催で休戦中か。開催時刻の地方日付・時を正本にする。"""
    import datetime
    d = datetime.datetime.fromtimestamp(at)
    mask, _source = P.truce_day(cx, player_id, d.strftime("%Y-%m-%d"))
    return bool(mask & (1 << d.hour))


def truce_hour_states(day: str, now: int) -> dict:
    """画面用の締切状態。判断はブラウザの時計でなくサーバー側に置く。"""
    now = int(now)
    cutoff = now + TRUCE_LOCK_SEC
    past = [h for h in range(24) if _event_time(day, h) <= now]
    deadline = [h for h in range(24)
                if now < _event_time(day, h) <= cutoff]
    return {"past": past, "deadline": deadline,
            "locked": past + deadline}


def truce_locked_hours(day: str, now: int):
    """もう変更できない時刻。過去＋開催2時間前に入った枠を含む。"""
    return truce_hour_states(day, now)["locked"]


def truce_schedules(cx, player_id: str, now: int) -> dict:
    """画面用の通常設定と今日から7日分。規則の判断はサーバー側に残す。"""
    import datetime
    today = datetime.datetime.fromtimestamp(now).date()
    days = []
    for n in range(TRUCE_DAYS_SHOWN):
        day = (today + datetime.timedelta(days=n)).isoformat()
        mask, source = P.truce_day(cx, player_id, day)
        states = truce_hour_states(day, now)
        days.append({"day": day, "hours": P.truce_hours(mask),
                     "source": source,
                     "past": states["past"],
                     "deadline": states["deadline"],
                     "locked": states["locked"]})
    return {"name": "休戦令", "count": P.TRUCE_HOURS,
            "lock_sec": TRUCE_LOCK_SEC,
            "default_hours": P.truce_hours(P.truce_default(cx, player_id)),
            "days": days}


def set_truce_default(cx, player_id: str, hours, now: int) -> dict:
    """通常設定を更新する。

    今日まで遡って設定が変わると、既に出た試合を「休戦したこと」にできてしまう。
    そこで締切済みの開催を含む日だけ旧設定を日別陣容として残し、新しい通常設定は
    その次の完全に未締切の日から効かせる。
    """
    import datetime
    new_mask = P.truce_mask(hours)
    old_mask = P.truce_default(cx, player_id)
    if new_mask == old_mask:
        return truce_schedules(cx, player_id, now)
    today = datetime.datetime.fromtimestamp(now).date()
    cutoff = datetime.datetime.fromtimestamp(now + TRUCE_LOCK_SEC).date()
    d = today
    with cx:
        while d <= cutoff:
            day = d.isoformat()
            explicit = cx.execute(
                "SELECT 1 FROM truce_days WHERE player_id=? AND day=?",
                (player_id, day)).fetchone()
            if explicit is None:
                # 更新前の有効設定を丸ごと残す。これで同日の未来枠だけ新設定に
                # なって8枚を超減する、という境界の分かりにくさも避けられる。
                cx.execute(
                    "INSERT INTO truce_days (player_id,day,mask,updated_at)"
                    " VALUES (?,?,?,?)", (player_id, day, old_mask, int(now)))
            d += datetime.timedelta(days=1)
        cx.execute(
            "INSERT INTO truce_defaults (player_id,mask,updated_at) VALUES (?,?,?)"
            " ON CONFLICT(player_id) DO UPDATE SET"
            " mask=excluded.mask,updated_at=excluded.updated_at",
            (player_id, new_mask, int(now)))
    return truce_schedules(cx, player_id, now)


def set_truce_day(cx, player_id: str, day: str, hours, now: int,
                  reset: bool = False) -> dict:
    """日別設定。過去と締切2時間以内のbitは一つも変えさせない。"""
    import datetime
    target = _local_day(day).date()
    today = datetime.datetime.fromtimestamp(now).date()
    if target < today or target >= today + datetime.timedelta(
            days=TRUCE_DAYS_SHOWN):
        raise ValueError("日別の休戦令は今日から7日分だけ変更できる")
    old_mask, _source = P.truce_day(cx, player_id, day)
    new_mask = P.truce_default(cx, player_id) if reset else P.truce_mask(hours)
    changed = old_mask ^ new_mask
    locked = truce_locked_hours(day, now)
    if any(changed & (1 << h) for h in locked):
        raise ValueError("開催2時間前を過ぎた休戦令は変更できない")
    if reset:
        P.delete_truce_day(cx, player_id, day)
    else:
        P.save_truce_day(cx, player_id, day, new_mask, now)
    return truce_schedules(cx, player_id, now)


def _season_archive(cx, season: str) -> None:
    """シーズンの陣容（§7.58）。順位表と全デッキを焼いてから消す。"""
    names = {p.id: p.display_name for p in P.all_players(cx)}
    kinds = {p.id: p.kind for p in P.all_players(cx)}
    for bn in L.BOARDS:
        b = load_board(cx, bn)
        rows = [{"pid": pid, "name": names.get(pid, "?"),
                 "kind": kinds.get(pid, "?"),
                 "rating": round(b.get(pid), 1), "games": b.games.get(pid, 0)}
                for pid in b.order(list(b.rating))]
        with cx:
            cx.execute("INSERT OR REPLACE INTO archives (season, board, data)"
                       " VALUES (?, ?, ?)",
                       (season, bn, _json.dumps(rows, ensure_ascii=False)))
    decks = {}
    for p in P.all_players(cx, kind=P.HUMAN):
        decks[p.display_name] = {reg: {"cards": raw, "form": form}
                                 for reg, (raw, form) in
                                 P.decks_of(cx, p.id).items()}
    with cx:
        cx.execute("INSERT OR REPLACE INTO archives (season, board, data)"
                   " VALUES (?, ?, ?)",
                   (season, "デッキ", _json.dumps(decks, ensure_ascii=False)))


def _tenka_participants(cx, cards, at: int = None):
    """天下に出られる全員（休戦していない3デッキ所持者 + 在野）。

    在野は休戦令を持たない。人間が全員休戦なら開催自体を省略するので、在野だけが
    夜通しレートを動かすことはない。
    """
    dummies = ensure_dummies(cx, cards)
    ents = dict(dummies)
    for p in P.all_players(cx, kind=P.HUMAN):
        if at is not None and truce_is_active(cx, p.id, at):
            continue
        e, ok, _ = entry_of(cx, cards, p.id, p.display_name)
        if ok.get("天下"):
            ents[p.id] = e
    return ents


def _tenka_resolve(cx, cards, serial: int, now: int) -> int:
    """天下1開催ぶんを解決する。組合せは締切後、開催時に初めて作る。"""
    # ThreadingHTTPServer では同じ00分に複数の state 要求が来る。開催番号を
    # PRIMARY KEY で先取りし、二重対戦・二重レートをDB制約で止める。
    import time as _time
    wall = int(_time.time())
    with cx:
        claim = cx.execute(
            "INSERT OR IGNORE INTO tenka_runs"
            " (serial,scheduled_at,state,started_at) VALUES (?,?,?,?)",
            (int(serial), int(now), "running", wall))
    if claim.rowcount == 0:
        return 0
    try:
        ents = _tenka_participants(cx, cards, now)
        human_ids = {p.id for p in P.all_players(cx, kind=P.HUMAN)}
        if not any(pid in human_ids for pid in ents):
            P.clear_pairs(cx, "天下", serial)
            with cx:
                cx.execute(
                    "UPDATE tenka_runs SET state='done',finished_at=?,fought=0"
                    " WHERE serial=?", (int(_time.time()), int(serial)))
            return 0
        b = load_board(cx, "天下")
        # 人間が奇数でも人間をあぶれさせない。在野を1人だけ休ませて偶数にする。
        # 不戦勝にはせず、全員が実際のBO3を戦う約束を守る。
        if len(ents) % 2:
            dummy_ids = [pid for pid in ents if pid not in human_ids]
            if dummy_ids:
                rested_dummy = min(dummy_ids, key=lambda pid: (b.get(pid), pid))
                del ents[rested_dummy]
        pairs = L.plan_round(b, list(ents), serial)
    except Exception as e:
        with cx:
            cx.execute("UPDATE tenka_runs SET state='failed',finished_at=?,"
                       " error=? WHERE serial=?",
                       (int(_time.time()), str(e)[:500], int(serial)))
        raise
    fought = 0
    try:
        for a, y in pairs:
            if a not in ents or y not in ents:
                continue
            seed = L.battle_seed("天下", serial, a, y)
            r = M.play(ents[a], ents[y], 0.5, seed=seed)
            sa = 1.0 if r["wins_a"] > r["wins_b"] else (
                0.0 if r["wins_b"] > r["wins_a"] else 0.5)
            ea = L.expected(b.get(a), b.get(y))
            ka = L.k_of(b.games.get(a, 0)); kb = L.k_of(b.games.get(y, 0))
            b.rating[a] = b.get(a) + ka * (sa - ea)
            b.rating[y] = b.get(y) - kb * (sa - ea)
            b.games[a] = b.games.get(a, 0) + 1
            b.games[y] = b.games.get(y, 0) + 1
            P.record_battle(cx, "tenka", "天下", a, y, seed,
                            snap_entry(ents[a]), snap_entry(ents[y]),
                            season_key(now), now,
                            result="".join(result_mark(g["結果"]["score"])
                                           for g in r["games"]),
                            rule_version=F.BATTLE_RULE_VERSION)
            fought += 1
        save_board(cx, b)
        P.clear_pairs(cx, "天下", serial)
        with cx:
            cx.execute("UPDATE tenka_runs SET state='done',finished_at=?,fought=?"
                       " WHERE serial=?",
                       (int(_time.time()), fought, int(serial)))
        return fought
    except Exception as e:
        # 自動再試行はしない。何戦まで書けたか不明な状態で重ねる方が危険。
        with cx:
            cx.execute("UPDATE tenka_runs SET state='failed',finished_at=?,"
                       " fought=?,error=? WHERE serial=?",
                       (int(_time.time()), fought, str(e)[:500], int(serial)))
        raise


def tick(cx, cards, now: int) -> None:
    """定刻処理の遅延評価。**どの入口からでも最初に呼ぶ。**

    やること: (1) 月が変わっていたら陣容→レートリセット (2) 期限の来た天下を
    開催（複数たまっていれば順に） (3) 順位表の毎時断面を更新。全部が冪等で、
    呼び忘れた時間は次の呼び出しがまとめて片付ける。
    """
    # (0) 旧 matches からの引っ越し（1回だけ）。陣容なし＝当時の登録から再構成
    if not P.ledger_get(cx, "migrated_battles"):
        for m in cx.execute("SELECT * FROM matches ORDER BY id"):
            # rule_version は渡さない（既定 ''）。旧 matches は当時どのルール
            # で戦ったか記録が無いので、いま分かるふりをしない（§7.135）。
            P.record_battle(cx, "tenka" if m["board"] == "天下" else "ranked",
                            m["board"], m["pid_a"], m["pid_b"], m["seed"],
                            "", "", season_key(now), now)
        P.ledger_set(cx, "migrated_battles", "1")
    # (0b) 解放の種まき（1回だけ・§7.60）。この時点で居る人間は**全解放で救済**
    #      — 一度全部使えた物を後から取り上げると必ず揉める。以後の新規は
    #      初期セット（ensure_unlocks / ログイン時）。
    if not P.ledger_get(cx, "unlocks_seeded"):
        persons = sorted({M.person_of(c) for c in cards})
        for pl in P.all_players(cx, P.HUMAN):
            P.unlock(cx, pl.id, persons, "migration")
        P.ledger_set(cx, "unlocks_seeded", "1")
    # (0c) 素の初期状態への揃え直し（1回だけ・公開前の手元専用 2026-08-20）。
    #      テストプレイの指示: 「初期状態は初期武将＋恩賞未取得。UXを新規と
    #      揃える」— 全人間を初期セット40人・恩賞ゼロ・戦記未進行へ戻す。
    #      全部持ちの状態は試験用ボタン（dev_senki / dev_onsho）で1押しで
    #      再現できるので救済は不要になった。**公開後はこの手の一斉リセットを
    #      してはならない**（(0b) の注のとおり揉める。手元だから許される）。
    if not P.ledger_get(cx, "fresh_start_v2"):
        from . import rosterdata as R
        start = R.senki_start()
        with cx:
            for pl in P.all_players(cx, P.HUMAN):
                cx.execute("DELETE FROM unlocks WHERE player_id = ?", (pl.id,))
                cx.execute("DELETE FROM owned_traits WHERE player_id = ?",
                           (pl.id,))
                cx.execute("DELETE FROM senki WHERE player_id = ?", (pl.id,))
                cx.execute("DELETE FROM senki_laps WHERE player_id = ?",
                           (pl.id,))
        for pl in P.all_players(cx, P.HUMAN):
            P.unlock(cx, pl.id, start, "start")
        P.ledger_set(cx, "fresh_start_v2", "1")
    # (1) シーズン
    cur = season_key(now)
    stored = P.ledger_get(cx, "season")
    if not stored:
        P.ledger_set(cx, "season", cur)
    elif stored != cur:
        _season_archive(cx, stored)
        P.reset_ratings(cx)
        P.ledger_set(cx, "season", cur)
    # (2) 天下の解決。1日2回版から切り替えた最初の1回は、現在時までを済扱いに
    #     する。さもないと導入直後のアクセスで過去24時間ぶんを一気に開催する。
    if not P.ledger_get(cx, "tenka_hourly_v1"):
        import datetime
        d = datetime.datetime.fromtimestamp(now).replace(
            minute=0, second=0, microsecond=0)
        serial = int(d.strftime("%Y%m%d")) * 100 + d.hour
        old_done = int(P.ledger_get(cx, "tenka_done", "0"))
        P.ledger_set(cx, "tenka_done", str(max(old_done, serial)))
        P.ledger_set(cx, "tenka_anchor", str(now))
        with cx:
            cx.execute("DELETE FROM pairings WHERE board='天下'")
        P.ledger_set(cx, "tenka_hourly_v1", "1")
    done = int(P.ledger_get(cx, "tenka_done", "0"))
    t0 = int(P.ledger_get(cx, "tenka_anchor", "0")) or (now - 24 * 3600)
    for serial, t in tenka_events(t0, now):
        if serial > done:
            _tenka_resolve(cx, cards, serial, t)
            P.ledger_set(cx, "tenka_done", str(serial))
    P.ledger_set(cx, "tenka_anchor", str(now))
    # (3) 毎時断面
    hk = hour_key(now)
    for bn in L.BOARDS:
        r = cx.execute("SELECT hour_key, data FROM standings_cache"
                       " WHERE board = ?", (bn,)).fetchone()
        empty = r is not None and r["data"] == "[]" and P.board_ratings(cx, bn)
        if r is None or r["hour_key"] != hk or empty:
            b = load_board(cx, bn)
            rows = [{"pid": pid, "rating": round(b.get(pid), 1),
                     "games": b.games.get(pid, 0)}
                    for pid in b.order(list(b.rating))]
            with cx:
                cx.execute("INSERT OR REPLACE INTO standings_cache"
                           " (board, hour_key, data) VALUES (?, ?, ?)",
                           (bn, hk, _json.dumps(rows)))


def ensure_unlocks(cx, player_id: str) -> set:
    """解放済みの人物集合（§7.60）。1行も無ければ初期セットを配ってから返す
    — 登録の入口をどこに増やしても取りこぼさない安全網。既存プレイヤーは
    tick() の種まきが先に全解放しているので、ここへは落ちてこない。"""
    unl = P.unlocked(cx, player_id)
    if not unl:
        from . import rosterdata as R
        P.unlock(cx, player_id, R.senki_start(), "start")
        unl = P.unlocked(cx, player_id)
    return unl


def next_tenka(now: int):
    """次の天下（通し番号, unix秒）。表示用。"""
    return tenka_events(now, now + 24 * 3600)[0]


def cached_standings(cx, board: str):
    r = cx.execute("SELECT data FROM standings_cache WHERE board = ?",
                   (board,)).fetchone()
    return _json.loads(r["data"]) if r else []


def _rate_single(cx, board_name: str, a: str, y: str, sa: float) -> tuple:
    """1試合ぶんのレート更新。戻りは (aの旧, aの新)。"""
    b = load_board(cx, board_name)
    ea = L.expected(b.get(a), b.get(y))
    ka = L.k_of(b.games.get(a, 0)); kb = L.k_of(b.games.get(y, 0))
    old = b.get(a)
    b.rating[a] = old + ka * (sa - ea)
    b.rating[y] = b.get(y) - kb * (sa - ea)
    b.games[a] = b.games.get(a, 0) + 1
    b.games[y] = b.games.get(y, 0) + 1
    save_board(cx, b)
    return old, b.rating[a]


def attack(cx, cards, me, reg_name: str, now: int) -> dict:
    """BO1の出陣（§7.58）。相手は同レート帯からシステムが選ぶ。

    返り値: {"error": …} か {"battle_id", "foe", "win", "rating_old/new"}。
    """
    entry, ok, errs = entry_of(cx, cards, me.id, me.display_name)
    if not ok.get(reg_name):
        return {"error": "{} のデッキが出せる状態にない: {}".format(
            reg_name, "／".join(errs) or "未登録")}
    from . import senki as SK
    if not SK.board_gate(cx, me.id).get(reg_name, True):
        need = "第四章（官渡）" if reg_name == "官渡" else "第六章"
        return {"error": "{} の戦場は戦記を{}まで進めると挑める".format(
            reg_name, need)}
    reg_i = REG_NAMES.index(reg_name)
    dummies = ensure_dummies(cx, cards)
    cand = dict(dummies)
    for pid, (e, ok2) in all_human_entries(cx, cards).items():
        if ok2.get(reg_name):
            cand[pid] = e
    cand = {pid: e for pid, e in cand.items() if pid != me.id}
    if not cand:
        return {"error": "相手が居ない"}
    b = load_board(cx, reg_name)
    mine_r = b.get(me.id)
    order = sorted(cand, key=lambda p: (abs(b.get(p) - mine_r), p))
    pool = [p for p in order[:5]
            if not P.fought_recently(cx, reg_name, me.id, p, now)]
    if not pool:        # 近い5人と全員戦ったばかりなら帯を広げる
        pool = [p for p in order
                if not P.fought_recently(cx, reg_name, me.id, p, now)][:5]
    if not pool:
        return {"error": "近い相手とは全員戦ったばかり（同じ相手には1時間に1回まで）"}
    if not P.spend_heifu(cx, me.id, 1, now):
        return {"error": "兵符が足りない（10分に1枚回復する）"}
    import random as _random
    foe = _random.choice(pool)
    seed = L.battle_seed(reg_name, now, me.id, foe)
    ua, ub = entry.unit(reg_i), cand[foe].unit(reg_i)
    r = M.play_one(BoardEntry({reg_i: ua}), BoardEntry({reg_i: ub}),
                   reg_i, 0.5, seed=seed)
    sa = 1.0 if r["winner"] == "A" else (0.0 if r["winner"] == "B" else 0.5)
    old, new = _rate_single(cx, reg_name, me.id, foe, sa)
    bid = P.record_battle(
        cx, "ranked", reg_name, me.id, foe, seed,
        _json.dumps(snap_army(ua), ensure_ascii=False),
        _json.dumps(snap_army(ub), ensure_ascii=False),
        season_key(now), now, result=result_mark(sa),
        rule_version=F.BATTLE_RULE_VERSION)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    return {"battle_id": bid, "foe": names.get(foe, "?"),
            "win": ("勝ち" if sa > 0.5 else ("負け" if sa < 0.5 else "引き分け")),
            "rating_old": round(old, 1), "rating_new": round(new, 1)}


def council_battle(cx, cards, me, source_battle_id: int, now: int) -> dict:
    """軍議演習。過去に戦った敵の陣容へ、現在登録中の自軍を当てる。

    レート・通常戦績・報酬は動かさず、演習令を1枚だけ消費する。相手本人の
    pid は記録に使わないため、相手側の戦歴にも演習は現れない。
    """
    src = cx.execute("SELECT * FROM battles WHERE id = ?",
                     (source_battle_id,)).fetchone()
    if src is None or me.id not in (src["pid_a"], src["pid_b"]):
        return {"error": "その対戦陣容は使えない"}
    if src["mode"] in ("senki", "council"):
        return {"error": "その記録は軍議演習の相手にできない"}
    if not src["snap_a"] or not src["snap_b"]:
        return {"error": "古い記録のため敵布陣の陣容が残っていない"}
    board = src["board"]
    if board not in REG_NAMES and board != "天下":
        return {"error": "その戦場は軍議演習に対応していない"}
    if P.enshu(cx, me.id, now)[0] < 1:
        return {"error": "演習令が足りない（10分に1枚回復する）"}

    mine_entry, ok, errs = entry_of(cx, cards, me.id, me.display_name)
    if not ok.get(board):
        return {"error": "{} の登録デッキが出せる状態にない: {}".format(
            board, "／".join(errs) or "未登録")}

    me_was_a = src["pid_a"] == me.id
    foe_pid = src["pid_b"] if me_was_a else src["pid_a"]
    foe_snap = src["snap_b"] if me_was_a else src["snap_a"]
    names = {p.id: p.display_name for p in P.all_players(cx)}
    foe_name = names.get(foe_pid, "名もなき軍")

    # 消費前に陣容を再構成し、壊れた記録で演習令だけ失わないようにする。
    try:
        foe_entry = entry_from_snap(cards, foe_snap)
        if board == "天下":
            result = M.play(mine_entry, foe_entry, 0.5,
                            seed=L.battle_seed("council", source_battle_id,
                                               now, me.id))
            sa = (1.0 if result["wins_a"] > result["wins_b"] else
                  (0.0 if result["wins_b"] > result["wins_a"] else 0.5))
            marks = "".join(result_mark(g["結果"]["score"])
                            for g in result["games"])
            mine_snap = snap_entry(mine_entry)
        else:
            reg_i = REG_NAMES.index(board)
            ua, ub = mine_entry.unit(reg_i), foe_entry.unit(reg_i)
            seed = L.battle_seed("council", source_battle_id, now, me.id)
            result = M.play_one(BoardEntry({reg_i: ua}),
                                BoardEntry({reg_i: ub}), reg_i, 0.5,
                                seed=seed)
            sa = (1.0 if result["winner"] == "A" else
                  (0.0 if result["winner"] == "B" else 0.5))
            marks = result_mark(sa)
            mine_snap = _json.dumps(snap_army(ua), ensure_ascii=False)
    except (KeyError, ValueError, TypeError):
        return {"error": "敵布陣の陣容を再構成できない"}

    if not P.spend_enshu(cx, me.id, 1, now):
        return {"error": "演習令が足りない（10分に1枚回復する）"}
    seed = L.battle_seed("council", source_battle_id, now, me.id)
    bid = P.record_battle(
        cx, "council", board, me.id, "council:{}".format(source_battle_id),
        seed, mine_snap, foe_snap, season_key(now), now, result=marks,
        rule_version=F.BATTLE_RULE_VERSION)
    with cx:
        cx.execute(
            "INSERT INTO council_runs"
            " (battle_id, source_battle_id, player_id, foe_name)"
            " VALUES (?, ?, ?, ?)",
            (bid, source_battle_id, me.id, foe_name))
    n, wait = P.enshu(cx, me.id, now)
    return {"battle_id": bid, "source_id": source_battle_id,
            "foe": foe_name,
            "win": ("勝ち" if sa > 0.5 else
                    ("負け" if sa < 0.5 else "引き分け")),
            "enshu": {"count": n, "cap": P.ENSHU_CAP,
                      "next_in": wait, "regen": P.ENSHU_REGEN_SEC}}


def free_battle(cx, cards, me, reg_name: str, foe_pid: str, now: int) -> dict:
    """フリー対戦（在野戦・§7.58）。レートも兵符も動かない。"""
    entry, ok, errs = entry_of(cx, cards, me.id, me.display_name)
    if not ok.get(reg_name):
        return {"error": "{} のデッキが出せる状態にない".format(reg_name)}
    dummies = ensure_dummies(cx, cards)
    if foe_pid not in dummies:
        return {"error": "その在野は居ない"}
    reg_i = REG_NAMES.index(reg_name)
    seed = L.battle_seed("free", now, me.id, foe_pid)
    ua, ub = entry.unit(reg_i), dummies[foe_pid].unit(reg_i)
    r = M.play_one(BoardEntry({reg_i: ua}), BoardEntry({reg_i: ub}),
                   reg_i, 0.5, seed=seed)
    sa = 1.0 if r["winner"] == "A" else (0.0 if r["winner"] == "B" else 0.5)
    bid = P.record_battle(
        cx, "free", reg_name, me.id, foe_pid, seed,
        _json.dumps(snap_army(ua), ensure_ascii=False),
        _json.dumps(snap_army(ub), ensure_ascii=False),
        season_key(now), now, result=result_mark(sa),
        rule_version=F.BATTLE_RULE_VERSION)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    return {"battle_id": bid, "foe": names.get(foe_pid, "?"),
            "win": ("勝ち" if sa > 0.5 else ("負け" if sa < 0.5 else "引き分け"))}


def room_join(cx, cards, me, code: str, now: int) -> dict:
    """ルーム対戦（§7.58）。番号を入れた瞬間に解決。レート不変動。"""
    room = P.room_get(cx, code)
    if room is None:
        return {"error": "そのルーム番号は無い"}
    if room["battle_id"]:
        return {"error": "そのルームは対戦済み"}
    if room["creator"] == me.id:
        return {"error": "自分のルームには入れない"}
    reg_name = room["regulation"]
    reg_i = REG_NAMES.index(reg_name)
    entry, ok, errs = entry_of(cx, cards, me.id, me.display_name)
    if not ok.get(reg_name):
        return {"error": "{} のデッキが出せる状態にない".format(reg_name)}
    ua = army_from_snap(cards, _json.loads(room["snap"]))   # 発行側の陣容
    ub = entry.unit(reg_i)
    seed = L.battle_seed("room", code, now)
    r = M.play_one(BoardEntry({reg_i: ua}), BoardEntry({reg_i: ub}),
                   reg_i, 0.5, seed=seed)
    sb = 1.0 if r["winner"] == "B" else (0.0 if r["winner"] == "A" else 0.5)
    bid = P.record_battle(
        cx, "room", reg_name, room["creator"], me.id, seed,
        room["snap"], _json.dumps(snap_army(ub), ensure_ascii=False),
        season_key(now), now, result=result_mark(1.0 - sb),
        rule_version=F.BATTLE_RULE_VERSION)
    P.room_close(cx, code, bid)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    return {"battle_id": bid, "foe": names.get(room["creator"], "?"),
            "win": ("勝ち" if sb > 0.5 else ("負け" if sb < 0.5 else "引き分け"))}


def cmd_round(args) -> None:
    print("この操作は⑨（§7.58）で廃止した。出陣は Web の各盤面の「出陣」"
          "（挑戦ラダー）、天下は定刻開催（試験は /api/dev_tenka）を使う。")
    sys.exit(1)
    cx = P.connect(args.db)
    cards = M._roster_cards()
    pl = P.get(cx, args.player)
    if pl is None:
        print("その id の登録者が居ない"); sys.exit(1)
    entry, ok, errs = entry_of(cx, cards, pl.id, pl.display_name)
    for e in errs:
        print("  - " + e)
    if not any(ok.values()):
        print("出られる順位表が無い（先にデッキを登録する）")
        sys.exit(1)
    dummies = ensure_dummies(cx, cards)
    humans_e = all_human_entries(cx, cards)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    boards = [_canon_reg(args.board)] if args.board else list(L.BOARDS)
    for name in boards:
        entries = full_board_entries(cx, dummies, humans_e, name)
        if not ok.get(name) and pl.id in entries:
            entries = {k: v for k, v in entries.items() if k != pl.id}
        b, rnd, pairs = run_round(cx, cards, entries, name, dt=args.dt)
        pids = list(entries)
        mine = next((pr for pr in pairs if pl.id in pr), None)
        if mine is None:
            print("{:<10} 第{}巡: 相手なし（人数が奇数）".format(name, rnd + 1))
            continue
        foe = mine[1] if mine[0] == pl.id else mine[0]
        # 同じ種で読み直す（resolve と同一の戦い）
        seed = L.battle_seed(b.name, rnd, mine[0], mine[1])
        me_first = mine[0] == pl.id
        if b.reg is None:
            r = M.play(entries[mine[0]], entries[mine[1]], args.dt, seed=seed)
            sa = r["winner"]
            won = (sa == "A") == me_first if sa != "引き分け" else None
            score = "{:g}-{:g}".format(r["wins_a"] if me_first else r["wins_b"],
                                       r["wins_b"] if me_first else r["wins_a"])
        else:
            r = M.play_one(entries[mine[0]], entries[mine[1]], b.reg,
                           args.dt, seed=seed)
            won = (r["winner"] == "A") == me_first if r["winner"] != "引き分け" else None
            score = ""
        verdict = "勝ち" if won else ("負け" if won is not None else "引き分け")
        rank = b.order(pids).index(pl.id) + 1
        print("{:<10} 第{}巡: 対 {:<8} {} {}  → 武名{:.0f} {}位/{}人".format(
            name, rnd + 1, names.get(foe, foe), verdict, score,
            b.get(pl.id), rank, len(pids)))
        if args.replay and b.reg is not None:
            cap = M.REGULATIONS[b.reg][1]
            ua = M.with_surplus(entries[mine[0]].unit(b.reg), cap)
            ub = M.with_surplus(entries[mine[1]].unit(b.reg), cap)
            replay_one(ua, ub, args.dt, seed, me_first)
        elif args.replay:
            # BO3: 3戦を順に再生する。**種は play() と同じ導出**（seed*3+i・§8.3）。
            # ずらし方を変えると「リプレイが実際に起きた戦いと別物」になる。
            wa = wb = 0.0
            for i, (label, cap) in enumerate(M.REGULATIONS):
                ua = M.with_surplus(entries[mine[0]].unit(i), cap)
                ub = M.with_surplus(entries[mine[1]].unit(i), cap)
                g = F.simulate(ua, ub, args.dt, seed=seed * 3 + i)
                sc = g["score"] if me_first else 1.0 - g["score"]
                wa += sc
                wb += 1.0 - sc
                v = "勝ち" if sc > 0.5 else ("負け" if sc < 0.5 else "引き分け")
                print("    ▼ 第{}戦（{}） {}　累計 {:g}-{:g}".format(
                    i + 1, label, v, wa, wb))
                replay_one(ua, ub, args.dt, seed * 3 + i, me_first)


def eval_chart(series, me_first: bool, width: int = 60) -> List[str]:
    """形勢グラフ（将棋AIの評価値グラフの型・§9.4）。

    縦軸は残存率の差（自軍 − 敵軍）。上にいれば自軍優勢。横軸は戦場の時刻。
    """
    if not series:
        return []
    step = max(1, len(series) // width)
    pts = series[::step]
    diffs = [(ra - rb) if me_first else (rb - ra) for _, ra, rb in pts]
    bands = (0.25, 0.15, 0.05, -0.05, -0.15, -0.25)   # 行の下限（上から）
    def row_of(d):
        for i, lo in enumerate(bands):
            if d >= lo:
                return i
        return len(bands)
    # 全セルを全角で組む（半角を混ぜると列がずれる）
    rows = [["　"] * len(pts) for _ in range(len(bands) + 1)]
    for x in range(len(pts)):
        rows[3][x] = "・"                 # ±0 の点線
    for x, d in enumerate(diffs):
        rows[row_of(d)][x] = "●"
    labels = ("+25│", "   │", "   │", " ±0│", "   │", "   │", "-25│")
    out = ["── 形勢（上=自軍優勢・縦軸は残存率差%） ──"]
    for lab, cells in zip(labels, rows):
        out.append(lab + "".join(cells))
    # 横軸: 2時間ごとの目盛り（全角数字で列を揃える）
    zen = str.maketrans("0123456789", "０１２３４５６７８９")
    axis = ["　"] * len(pts)
    marks = []
    for x, (t, _, _) in enumerate(pts):
        h = int((F.BATTLE_START_H * 60 + F.mins(t)) // 60)
        if not marks or h >= marks[-1] + 2:
            marks.append(h)
            lab = "{}時".format(h).translate(zen)
            for j, ch in enumerate(lab):
                if x + j < len(axis):
                    axis[x + j] = ch
    out.append("   └" + "".join(axis))
    return out


# 陣形 → 前衛の数。**正本は field.FORM_NAME**（ここに数を書き写さない）。
_FRONT_OF = {name: n for n, name in F.FORM_NAME.items()}


def _main_slots(form_name: str, typ: str) -> int:
    """その陣形で、主役の兵種が実際に入れる枠の数。

    後衛は弓（と槍持ち）だけなので、近接の主役は前衛の数で頭打ちになり、
    弓の主役は後衛の数で決まる。陣形を名指しされたときに「何人ぶんしか
    無い」と言うために使う。
    """
    front = _FRONT_OF.get(form_name, 3)
    return (M.UNIT_SIZE - front) if typ == "弓兵" else front


def _form_floor(pool, form_name: str) -> float:
    """その陣形を**手持ちで組んだときの最安**。上限に収まるかを見るため。

    後衛の枠は弓か槍持ちでしか埋まらないので、陣形によっては手持ちの
    値段の下限そのものが上限を超えることがある（序盤の雁行など）。
    """
    front = _FRONT_OF.get(form_name, 3)
    rear_n = M.UNIT_SIZE - front
    rear = sorted(c.cost for c in pool
                  if c.typ == F.ARC or getattr(c, "spear", False))
    near = sorted(c.cost for c in pool if c.typ != F.ARC)
    if len(rear) < rear_n or len(near) < front:
        return float("inf")
    return sum(rear[:rear_n]) + sum(near[:front])


def draft_deck(cards, reg_name: str, form_name: str, style: str, typ: str,
               faction: str, seed: int, exclude_persons=(),
               cap: Optional[float] = None, ratio: float = 0.9,
               pin_form: bool = False) -> Tuple[List[str], str, str]:
    """アンケートの回答から**たたき台**のデッキを組む（§7.54）。

    ダミーの編成器（dummies.make_entry）をそのまま使う — 回答を性格
    （役割・兵種の重み）へ写すだけで、規則（前衛は近接・後衛は弓・上限・
    同一人物）を破らない編成が出る。同じ量の定義を2箇所に持たない。

    **主役の兵種を指定されたら本気で寄せる**（テストプレイの指摘: 2.2倍の
    重みでは主役率24〜42%で、弓が多数派になることさえあった）。重みを
    12倍/0.15倍にし、**陣形も主役が並ぶ形へ軍師が選び直す** — 後衛は弓の
    定石で組むので、近接主役は前衛の多い鶴翼、弓主役は後衛の多い雁行で
    ないと物理的に枠が無い。戻り値は (名前, ひとこと, 使う陣形)。

    **わざと少し弱く作る**: 上限の9割で組む。最強の答えを渡すと編成の探索が
    死ぬ（§7.47 の開示設計と同じ理由）。余った1割が「入れ替えて仕上げる」
    余白になる。seed を変えると引き直せる。
    """
    from . import dummies as DM
    role_w = {
        "力押し":  {F.TANK: 1.6, F.BAL: 1.6, F.DPS: 2.0, F.BURST: 0.5, F.SUP: 0.6},
        "兵法":  {F.TANK: 0.6, F.BAL: 0.8, F.DPS: 1.3, F.BURST: 2.4, F.SUP: 1.4},
        "守り":    {F.TANK: 2.6, F.BAL: 1.0, F.DPS: 0.5, F.BURST: 0.4, F.SUP: 1.8},
    }.get(style, {F.TANK: 1.0, F.BAL: 1.2, F.DPS: 1.0, F.BURST: 0.8, F.SUP: 0.8})
    note_head = ""
    form_name = F.FORM_ALIAS.get(form_name, form_name)
    if typ in F.TYPE_JP.values():
        typ_w = {t: (12.0 if F.TYPE_JP[t] == typ else 0.15)
                 for t in (F.INF, F.CAV, F.ARC)}
        best = "雁行" if typ == "弓兵" else "鶴翼"
        if form_name != best:
            if pin_form:
                # 陣形を名指しされたら動かさない。ただし**黙って裏切らない** —
                # 後衛は弓の定石で組むので、近接主役に後衛の多い陣形を指すと
                # 主役の枠が物理的に足りない。そのことを先に言う。
                note_head = ("陣形は{}のまま組んだ（指定）。{}の枠は{}人ぶんしか"
                             "無いので、主役を通したいなら{}に替える。"
                             ).format(form_name, typ,
                                      _main_slots(form_name, typ), best)
            else:
                form_name = best
                note_head = "主役の{}が並ぶよう陣形は{}にした。".format(typ, best)
    else:
        typ_w = {t: 1.0 for t in (F.INF, F.CAV, F.ARC)}
    greed = {"力押し": 0.6, "守り": 0.3}.get(style, 0.5)
    p = DM.Persona("たたき台", typ_w, role_w, form_name, greed)
    reg_i = next(i for i, (n, _) in enumerate(M.REGULATIONS) if n == reg_name)
    # cap を渡すとその戦だけの上限で組む（戦記の草案・§7.62）。既定は
    # レギュレーション上限の ratio 倍（＝わざと少し弱い たたき台）。
    limit = float(cap) if cap else M.REGULATIONS[reg_i][1] * ratio
    caps = tuple((n, round(limit if n == reg_name else c * ratio))
                 for n, c in M.REGULATIONS)
    note = ("その戦の上限ちょうどで組んだ草案。入れ替えて仕上げよう" if cap
            else "上限の9割で組んだたたき台。入れ替えと段位上げで仕上げよう")

    def build(pool):
        try:
            e = DM.make_entry(pool, p, seed, caps=caps)
        except ValueError:
            # 候補が尽きて編成が立たない（勢力しばり等で池が痩せた）
            return None
        u = e.unit(reg_i)
        ok = (len(u.cards) == M.UNIT_SIZE and u.total_cost() <= limit + 1e-9)
        return [c.name for c in u.cards] if ok else None

    note = note_head + note
    pool = [c for c in cards if M.person_of(c) not in set(exclude_persons)]
    if faction in ("魏", "蜀", "呉", "群雄"):
        names = build([c for c in pool if c.faction == faction])
        if names is not None:
            return names, note, form_name
        note = "その勢力だけでは埋まらず、他勢力も混ぜた。" + note
    names = build(pool)
    if names is None:
        # 組めなかった理由を取り違えない。**その陣形の最安が上限を超えて
        # いる**のが原因なら、人物の重なりの話をしても直せない。
        floor = _form_floor(pool, form_name)
        if floor > limit + 1e-9:
            return ([], "{}は後衛{}人ぶんが要る。手持ちの一番安い組み合わせでも"
                        "{:.0f}点で、上限{:.0f}点に収まらない".format(
                            form_name, M.UNIT_SIZE - _FRONT_OF.get(form_name, 3),
                            floor, limit), form_name)
        return ([], "たたき台を組めなかった（他のデッキと人物が重なりすぎている）",
                form_name)
    return names, note, form_name


def battle_notes(ua, ub, r, series, me_first: bool) -> List[str]:
    """軍師の見立て（§9.5）。**記録からの読み取り専用**で、勝敗にも測定にも
    触れない（§9.3 の原則。実況・戦況図と同じ側）。

    出すのは最大3行: 山場（形勢が入れ替わった・差がいちばん開いた時刻と、
    そのとき崩れた隊）、それに勝因・敗因を高々2つ。原因は盤面が実際に持つ
    量からだけ引く — 本陣の陥落（cmd_fell）、兵種相性（type_edge・コスト点）、
    陣形の三すくみ（§7.39 実測の向き）、与ダメの内訳（通常/必殺）、早い崩れ。
    推測で語らない: どのしきい値も「言い切れる大きさ」にだけ反応させる。
    """
    mine_a, foe_a = (ua, ub) if me_first else (ub, ua)
    mine_r, foe_r = ((r["dealt_a"], r["dealt_b"]) if me_first
                     else (r["dealt_b"], r["dealt_a"]))
    sc = r["score"] if me_first else 1.0 - r["score"]
    won, lost = sc > 0.5, sc < 0.5
    gap = [(t, (ra - rb) if me_first else (rb - ra)) for t, ra, rb in series]
    notes: List[str] = []

    def who(row):
        return M.person_of(F.Card(0, row[1], name=row[0])) or F.TYPE_JP[row[1]]

    # ── 山場 ──────────────────────────────────────────────
    # 最後に符号が変わった時刻（逆転）。序盤のゆらぎは見ない（振れ幅で足切り）。
    if gap and (won or lost):
        sign = 1.0 if won else -1.0
        flip = None
        swing = max(abs(g) for _, g in gap)
        for (t0, g0), (t1, g1) in zip(gap, gap[1:]):
            if g0 * sign < 0.0 <= g1 * sign and abs(g0) > 0.02 and swing > 0.05:
                flip = t1
        # 差がいちばん動いた15秒（きっかけの時刻）。逆転が無ければこちら。
        peak, peak_t = 0.0, None
        j = 0
        for i, (t0, g0) in enumerate(gap):
            while j < len(gap) - 1 and gap[j][0] - t0 < 15.0:
                j += 1
            d = (gap[j][1] - g0) * sign
            if d > peak:
                peak, peak_t = d, (t0 + gap[j][0]) / 2.0
        t_key = flip if flip is not None else (peak_t if peak > 0.04 else None)
        if t_key is not None:
            # そのとき崩れた隊があれば名指しする（±12秒）。**向きに合う側を
            # 優先** — 勝ちへ振れた山場で自軍の崩れを名指しすると、崩れが
            # 勝因に読めてしまう。
            rows_jp = ((foe_r, "敵の") if won else (mine_r, ""),)
            near = [(jp, row) for rows, jp in rows_jp for row in rows
                    if row[6] is not None and abs(row[6] - t_key) < 12.0]
            hint = ""
            if near:
                side_jp, row = min(near, key=lambda x: abs(x[1][6] - t_key))
                hint = "。{}{}の隊が崩れたあたりである".format(side_jp, who(row))
            notes.append("山場は【{}】{}{}".format(
                F.clock(t_key), "、ここで形勢が入れ替わった" if flip is not None
                else "ごろ、ここでいちばん差が開いた", hint))

    # ── 勝因・敗因（高々2つ） ────────────────────────────────
    causes: List[str] = []
    mine_fell, foe_fell = ((r["cmd_fell"][0], r["cmd_fell"][1]) if me_first
                           else (r["cmd_fell"][1], r["cmd_fell"][0]))
    if mine_fell and lost:
        causes.append("敗因は本陣の陥落。これがすべてで、崩れるまでの優劣は関係が無い")
    elif foe_fell and won:
        causes.append("勝因は敵本陣の討ち取り。総崩れを誘って決めた")
    if len(causes) < 2 and (won or lost):
        te = F.type_edge(mine_a, foe_a)   # コスト点。正なら自軍が有利
        if abs(te) >= 0.7 and (te > 0) == won:
            causes.append("兵種の噛み合わせが{}効いた（コスト{:.1f}点ぶんの{}）".format(
                "こちらに" if won else "敵に", abs(te), "得" if won else "不利"))
    if len(causes) < 2 and (won or lost):
        beats = {3: 4, 4: 2, 2: 3}    # 魚鱗>鶴翼>雁行>魚鱗（§7.39 実測の向き）
        mf, ff = mine_a.form.n_front, foe_a.form.n_front
        if mf != ff:
            if beats.get(ff) == mf and lost:
                causes.append("陣形は敵の{}がこちらの{}に強い並びだった（三すくみ）".format(
                    F.FORM_NAME[ff], F.FORM_NAME[mf]))
            elif beats.get(mf) == ff and won:
                causes.append("陣形は{}が{}を食う並びを取れていた（三すくみ）".format(
                    F.FORM_NAME[mf], F.FORM_NAME[ff]))
    if len(causes) < 2 and (won or lost):
        m_n = sum(x[2] - x[5] for x in mine_r); f_n = sum(x[2] - x[5] for x in foe_r)
        m_s = sum(x[5] for x in mine_r); f_s = sum(x[5] for x in foe_r)
        dn, ds = (f_n - m_n) if lost else (m_n - f_n), (f_s - m_s) if lost else (m_s - f_s)
        base_n, base_s = max(m_n, f_n, 1.0), max(m_s, f_s, 1.0)
        if dn / base_n > 0.15 and dn >= ds:
            causes.append("通常攻撃の打ち合いで{}（{:.1f}万 対 {:.1f}万）".format(
                "押し負けた" if lost else "押し切った",
                m_n / 1e4, f_n / 1e4))
        elif ds / base_s > 0.15:
            causes.append("兵法の応酬で{}（兵法の与ダメ {:.1f}万 対 {:.1f}万）".format(
                "撃ち負けた" if lost else "撃ち勝った", m_s / 1e4, f_s / 1e4))
    if len(causes) < 2 and lost and series:
        early = [row for row in mine_r
                 if row[6] is not None and row[6] < series[-1][0] * 0.45]
        if early:
            row = min(early, key=lambda x: x[6])
            causes.append("{}が【{}】と早くに崩れ、戦列が痩せたのも痛い".format(
                who(row), F.clock(row[6])))
    if not causes and r["reason"] == "time" and gap and abs(gap[-1][1]) < 0.05:
        causes.append("時間いっぱいの判定までもつれた際どい勝負で、明確な敗着は無い"
                      if lost else "時間いっぱいの判定までもつれた際どい勝負だった")
    notes += causes[:2]
    if not notes:
        notes.append("痛み分け。決め手を欠いたまま終わった" if sc == 0.5
                     else "終始おおきな山場のないまま決した")
    return notes


def replay_data(ua, ub, dt: float, seed: int, me_first: bool) -> dict:
    """1戦ぶんのリプレイを構造化して返す（Web用・§7.42）。

    実況行・形勢の時系列・戦果表・勝敗。CLI の replay_one と同じ素材から作る
    （同じ量の定義を2箇所に持たない: 行は narrate、数字は simulate の診断出力）。
    """
    line_sides = []
    lines = F.narrate(ua, ub, dt, seed=seed, sides=line_sides)
    series = []
    r = F.simulate(ua, ub, dt, seed=seed, series=series)
    step = max(1, len(series) // 240)
    mine, foe = (r["dealt_a"], r["dealt_b"]) if me_first else (r["dealt_b"], r["dealt_a"])
    def rows(xs):
        # §7.88 の「見えにくい効き」＋ §7.94 の合戦詳録。列は末尾に足す
        out = []
        for (n, t, d, m, m0, sd, _fa, ff, rf, cs, hl, al,
             tk, fi, st, sp, pair, det, nb, nn, ss, gw, ft, sv, wp) in xs:
            person = M.person_of(F.Card(0, t, name=n)) or F.TYPE_JP[t]
            out.append({
                "name": person,
                # 武将版（§7.135）。合戦詳録・戦果表は人物名だけ出すが、対戦
                # 当時どの版を使ったかはここで追える（名前=n は不変のキー）。
                "person": person, "version": R.version_of(n) if n else 1,
                "typ": F.TYPE_JP[t], "dealt": round(d), "men": round(m),
                "men0": round(m0), "skill_dealt": round(sd),
                # 真の壊滅（ANNIHIL_UNIT）。苦戦（ROUT_UNIT=15%）はペナルティの
                # ない演出用の一方向ウォッチマークで攻略的な意味を持たないため、
                # 詳報からは外した（テストプレイの指摘・§7.49後記3）
                "wiped": F.clock(wp) if wp is not None else None,
                "ff": round(ff), "refl": round(rf), "cut": round(cs),
                "heal": round(hl), "lost": round(al),
                # 合戦詳録（§7.94）
                "taken": round(tk), "fires": fi,
                # 阻害は戦場の分で（実況の時刻と同じ物差し・§9.1）
                "stun": round(F.mins(st)), "sup": round(sp),
                # 矛先: 与えた量の大きい順。細かい流れ弾は割合で切る
                "targets": [[k, round(v)] for k, v in
                            sorted(pair.items(), key=lambda kv: -kv[1])
                            if v >= 0.02 * max(d, 1.0)],
                "detour": round(det) if det is not None else None,
                # 構えの帳簿（§7.126）: 打消し回数と兵法名・兵法防御の軽減
                # （cut は通常攻撃防御と混みなので兵法ぶんを別に）・張り/空振り
                "null_blocked": nb, "null_names": list(nn),
                "scut_saved": round(ss),
                "guard_casts": gw[0], "guard_idle": gw[1],
                # 発動実績（§7.127）: 初回と各発動の時刻（戦場の時計表示）
                "fire_times": [F.clock(x) for x in ft],
                # 余勢の帳簿（§7.76 後記）: 超過損害・余勢で通った損害・回数
                "spill_over": round(sv[0]), "spill_dealt": round(sv[1]),
                "spill_n": sv[2],
            })
        return out
    sc = r["score"] if me_first else 1.0 - r["score"]
    # 行ごとの主体を**自軍/敵軍に翻訳して**渡す（§7.92）。画面が文章から
    # 名前を拾って当てる必要をなくす — 同じ武将が両軍にいると必ず外す。
    mine_key = "A" if me_first else "B"
    return {"lines": lines,
            "line_sides": ["mine" if x == mine_key else ("foe" if x else "")
                           for x in line_sides],
            "notes": battle_notes(ua, ub, r, series, me_first),
            "mine_names": [u.name for u in (ua if me_first else ub).cards if u.name],
            "foe_names": [u.name for u in (ub if me_first else ua).cards if u.name],
            "series": [[round(F.mins(t), 1),
                        round((ra - rb) if me_first else (rb - ra), 4)]
                       for t, ra, rb in series[::step]],
            "mine": rows(mine), "foe": rows(foe),
            "verdict": "勝ち" if sc > 0.5 else ("負け" if sc < 0.5 else "引き分け")}


def print_report(ua, ub, dt: float, seed: int, me_first: bool) -> None:
    """戦果表と形勢グラフ。実況は流れを語り、こちらは数字で振り返る。

    攻略の糸口はここにある: 与ダメが小さい札は「働く前に落ちた」か「射程・対面が
    悪い」、残兵が多いのに負けたなら「前衛だけ削られて後衛が余った」。
    """
    series = []
    r = F.simulate(ua, ub, dt, seed=seed, series=series)
    for line in eval_chart(series, me_first):
        print("    " + line)
    mine, foe = (r["dealt_a"], r["dealt_b"]) if me_first else (r["dealt_b"], r["dealt_a"])
    print("    ── 軍功帳（与ダメ=千人・残兵%） ──")
    for tag, rows in (("自軍", mine), ("敵軍", foe)):
        cells = []
        for (name, typ, dealt, men, men0, sd, _fa,
             ff, rf, cs, hl, al, *_detail, wp) in rows:
            pct = 100.0 * men / men0 if men0 > 0 else 0.0
            state = "壊滅" if pct <= 0.5 else "{:.0f}%".format(pct)
            # 真の壊滅（0.5%割れ）の時刻だけ出す。苦戦（15%割れ）はペナルティの
            # ない演出用の一方向ウォッチマークで攻略的な意味を持たないため、
            # 詳報からは外した（テストプレイの指摘・§7.49後記3）。実況の
            # 「苦戦」行・早い崩れの見立てには引き続き使う
            when = "・{}壊".format(F.clock(wp)) if wp is not None else ""
            # 見えにくい効き（§7.88）は**出た時だけ**添える
            extra = ""
            if len(_detail) >= 10 and _detail[6] > 0:   # 打消し（§7.126）
                extra += " 消{}回".format(_detail[6])
            if cs >= 300.0:
                extra += " 軽{:.1f}".format(cs / 1000.0)
            if rf >= 300.0:
                extra += " 反{:.1f}".format(rf / 1000.0)
            if ff >= 300.0:
                extra += " 同士{:.1f}".format(ff / 1000.0)
            if hl >= 300.0:
                extra += " 癒{:.1f}".format(hl / 1000.0)
            if al >= 300.0:
                extra += " 封{:.1f}".format(al / 1000.0)
            cells.append("{}({}) 与{:.1f}(兵法{:.1f}) 残{}{}{}".format(
                M.person_of(F.Card(0, typ, name=name)) or F.TYPE_JP[typ],
                F.TYPE_JP[typ][0], dealt / 1000.0, sd / 1000.0, state, when,
                extra))
        print("    {}: {}".format(tag, " / ".join(cells[:3])))
        if len(cells) > 3:
            print("          {}".format(" / ".join(cells[3:])))
    print("    ── 軍師の見立て ──")
    for n in battle_notes(ua, ub, r, series, me_first):
        print("    ・" + n)


def replay_one(ua, ub, dt: float, seed: int, me_first: bool) -> None:
    """1戦ぶんのリプレイ一式（実況・形勢グラフ・戦果表）。"""
    for line in F.narrate(ua, ub, dt, seed=seed):
        print("    " + line)
    print_report(ua, ub, dt, seed, me_first)


def cmd_next(args) -> None:
    """告知された次の対戦相手（と相手の陣形）を見る。"""
    cx = P.connect(args.db)
    cards = M._roster_cards()
    pl = P.get(cx, args.player)
    if pl is None:
        print("その id の登録者が居ない"); sys.exit(1)
    entry, ok, errs = entry_of(cx, cards, pl.id, pl.display_name)
    dummies = ensure_dummies(cx, cards)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    for bn in L.BOARDS:
        entries = dict(dummies)
        if ok.get(bn):
            entries[pl.id] = entry
        rnd, pairs = announce(cx, entries, bn)
        mine = next((pr for pr in pairs if pl.id in pr), None)
        if mine is None:
            print("{:<6} 第{}巡: （組に入っていない）".format(bn, rnd + 1))
            continue
        foe = mine[1] if mine[0] == pl.id else mine[0]
        fe = entries.get(foe)
        reg = REG_NAMES.index(bn) if bn in REG_NAMES else None
        if fe is None:
            forms = "?"
        elif reg is not None:
            forms = F.FORM_NAME.get(fe.unit(reg).form.n_front, "?")
        else:
            forms = "・".join(F.FORM_NAME.get(u.form.n_front, "?")
                              for u in fe.units)
        print("{:<6} 第{}巡: 対 {}（陣: {}）".format(
            bn, rnd + 1, names.get(foe, foe), forms))


def cmd_standings(args) -> None:
    cx = P.connect(args.db)
    names = {p.id: p.display_name for p in P.all_players(cx)}
    kinds = {p.id: p.kind for p in P.all_players(cx)}
    for name in ([_canon_reg(args.board)] if args.board else list(L.BOARDS)):
        r = P.board_ratings(cx, name)
        if not r:
            print("{:<10} まだ誰も戦っていない".format(name))
            continue
        b = load_board(cx, name)
        pids = b.order(list(r))
        print("{}（{}人・{}巡）".format(name, len(pids), P.board_round(cx, name)))
        for i, pid in enumerate(pids[:args.limit], 1):
            tag = "" if kinds.get(pid) == P.HUMAN else "（在野）"
            print("  {:>2}位 {:<10}{} 武名{:.0f} {}戦".format(
                i, names.get(pid, pid), tag, b.get(pid), b.games.get(pid, 0)))


def main() -> None:
    p = argparse.ArgumentParser(description="実プレイヤーの導線")
    p.add_argument("--db", default=P.DB_PATH)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("register"); s.add_argument("--name", required=True)
    s.add_argument("--email", required=True); s.set_defaults(fn=cmd_register)
    s = sub.add_parser("roster"); s.add_argument("--reg", choices=REG_NAMES + tuple(M.REG_ALIAS))
    s.set_defaults(fn=cmd_roster)
    s = sub.add_parser("deck"); s.add_argument("--player", required=True)
    s.add_argument("--reg", choices=REG_NAMES + tuple(M.REG_ALIAS), required=True)
    s.add_argument("--cards", required=True)
    s.add_argument("--form", choices=tuple(FORM_BY_NAME), required=True)
    s.set_defaults(fn=cmd_deck)
    s = sub.add_parser("status"); s.add_argument("--player", required=True)
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("round"); s.add_argument("--player", required=True)
    s.add_argument("--board", choices=L.BOARDS + tuple(M.REG_ALIAS))
    s.add_argument("--replay", action="store_true")
    s.add_argument("--dt", type=float, default=0.5)
    s.set_defaults(fn=cmd_round)
    s = sub.add_parser("next"); s.add_argument("--player", required=True)
    s.set_defaults(fn=cmd_next)
    s = sub.add_parser("standings"); s.add_argument("--board", choices=L.BOARDS + tuple(M.REG_ALIAS))
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_standings)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
