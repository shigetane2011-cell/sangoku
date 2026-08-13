#!/usr/bin/env python3
"""部隊戦の戦闘エンジン（仕様書 v0.2.1 §5〜§8 の実装）。

設計上の制約:
- すべて整数演算。浮動小数点は使わない（§8.4）。
- 乱数はシードから決定論的に生成し、消費順序を §8.1 の処理順序に厳密に従わせる。
- 同じ (ルール版, 編成, 戦場条件, シード) なら必ず同じ結果になる。

内部スケール:
- 位置・射程・移動距離は 1単位 = 10 で保持する（0.1単位の精度）。
- 必殺技ゲージは 最大100 を 10000 で保持する。
- 各種係数は千分率または百分率の整数。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

TICK_MS = 100                 # 1ティック = 0.1秒
MAX_TICKS = 900               # 1部隊戦の上限 90秒（§8.2）
LANE_DEPTH = 100 * 10         # レーン奥行き 100単位（§5.2）
BACK_OFFSET = 15 * 10         # 後衛は自軍前衛の後方15単位
GAUGE_MAX = 10000             # 必殺技ゲージ最大 100
GAUGE_PER_SEC = 200           # 時間経過によるゲージ上昇 2.0/秒（§7.2・v0.3 実測で改訂）

DEFENSE_K = 100               # ダメージ軽減の定数 K（§6.2）
TROOP_ADVANTAGE = 115         # 兵種有利のダメージ補正 +15%（§5.3）
CRIT_MULT = 150               # クリティカル倍率 1.5倍（§6.4）
DAMAGE_VARIANCE = 5           # 通常ダメージ乱数 ±5%（§6.4）
DAMAGE_FLOOR_PCT = 10         # 最低保証ダメージ = 基本ダメージの10%（§6.2）
WEAR_FLOOR = 600              # 消耗係数の下限 0.6（§6.1・確定）
WEAR_RANGE = 400              # 消耗係数の可変幅 0.4

# 決着促進（v0.3 で追加を提案）。上限時間まで粘る展開が多すぎると、
# 部隊戦の1/3がタイマー決着になり実況の締まりが悪くなるため、
# 一定時刻から与ダメージを逓増させて上限前に決着させる。
SUDDEN_START = 500            # 50秒から発動
SUDDEN_MAX = 300              # 90秒時点で 3.0倍

ACC_MIN = 20                  # 状態効果適用後の最終命中率の下限（§6.5）
ACC_MAX = 100
MOD_CAP = 50                  # 1つの能力への補正合計は ±50% に丸める（§6.5）

COMMANDER_HP_BONUS = 110      # 総大将の最大兵力 +10%（§4.2）
COMMANDER_GAUGE_BONUS = 110   # 総大将の必殺技ゲージ上昇率 +10%（§4.2）

# 三すくみ（§5.3）。attacker が victim に対して有利なら True。
BEATS = {"cav": "arc", "arc": "inf", "inf": "cav"}

# 配置枠の固定順序（§8.1）。行動順の決定に使う。
SLOTS = [("front", 0), ("front", 1), ("front", 2),
         ("back", 0), ("back", 1), ("back", 2)]

BATTLEFIELDS = {
    "plain":  {"label": "平原", "cav_speed": 115},
    "narrow": {"label": "隘路", "no_lane_support": True, "front_taken": 90},
    "rain":   {"label": "雨天", "arc_acc": -15, "arc_range": 80},
    "fog":    {"label": "濃霧", "all_range": 70},
    "clear":  {"label": "平時"},
}


class Rng:
    """xorshift64*。シードから決定論的に整数列を生成する（§8.4）。"""

    __slots__ = ("s",)

    def __init__(self, seed: int):
        self.s = (seed & 0xFFFFFFFFFFFFFFFF) or 0x9E3779B97F4A7C15

    def next(self) -> int:
        x = self.s
        x ^= (x << 13) & 0xFFFFFFFFFFFFFFFF
        x ^= x >> 7
        x ^= (x << 17) & 0xFFFFFFFFFFFFFFFF
        self.s = x
        return (x * 0x2545F4914F6CDD1D) & 0xFFFFFFFFFFFFFFFF

    def below(self, n: int) -> int:
        return self.next() % n

    def pct(self) -> int:
        """0〜99 を返す。確率判定用。"""
        return self.next() % 100


@dataclass
class Effect:
    """状態効果（§6.5）。"""
    kind: str                 # "mod" | "stun" | "dot"
    stat: str = ""            # mod のとき: atk / dfn / acc / speed
    value: int = 0            # mod は%、dot は攻撃力に対する%
    remaining: int = 0        # 残りティック
    interval: int = 0         # dot の発生間隔（ティック）
    countdown: int = 0        # dot の次回発生までのティック
    source_atk: int = 0       # dot の威力計算に使う発生元の攻撃力
    name: str = ""


@dataclass
class Unit:
    card: dict
    side: int                 # 0 or 1
    lane: int                 # 0=レーン1 ... 2=レーン3
    row: str                  # "front" | "back"
    is_commander: bool
    max_hp: int
    hp: int
    pos: int                  # 共有座標。side0 は +方向、side1 は −方向へ進む
    gauge: int = 0
    next_attack: int = 0      # 次に攻撃できるティック
    effects: list = field(default_factory=list)
    retreated: bool = False
    transfer: int = 0         # 隣レーンへの支援移動の残り距離
    # 統計
    dealt: int = 0
    taken: int = 0
    hits: int = 0
    swings: int = 0
    crits: int = 0
    skills: int = 0
    stun_ticks: int = 0

    @property
    def troop(self) -> str:
        return self.card["troop"]

    def mod(self, stat: str) -> int:
        """状態効果による補正の合計（%）。異名は加算合成し ±50% で丸める（§6.5）。"""
        total = 0
        for e in self.effects:
            if e.kind == "mod" and e.stat == stat:
                total += e.value
        return max(-MOD_CAP, min(MOD_CAP, total))

    def stunned(self) -> bool:
        return any(e.kind == "stun" for e in self.effects)


class Battle:
    """1つの部隊戦。仕様書 §8.1 の12ステップをティックごとに処理する。"""

    def __init__(self, teams, seed: int, battlefield: str = "clear", log: bool = False):
        """teams: [team0, team1]。team は {"units": [...], "commander": index} 形式。"""
        self.rng = Rng(seed)
        self.field = BATTLEFIELDS[battlefield]
        self.field_key = battlefield
        self.tick = 0
        self.events: list = []
        self.log = log
        # 先攻側をシードから決定論的に決める（§8.1）
        self.first = self.rng.below(2)
        self.units: list[Unit] = []
        self.defaults = TROOP_TABLE
        for side, team in enumerate(teams):
            for idx, entry in enumerate(team["units"]):
                self.units.append(self._make_unit(entry, side, idx == team["commander"]))
        self.result = None

    # ---- 構築 -------------------------------------------------------------

    def _make_unit(self, entry, side, is_commander) -> Unit:
        card = entry["card"]
        hp = card["hp"]
        if is_commander:
            hp = hp * COMMANDER_HP_BONUS // 100
        lane, row = entry["lane"], entry["row"]
        if side == 0:
            pos = 0 if row == "front" else -BACK_OFFSET
        else:
            pos = LANE_DEPTH if row == "front" else LANE_DEPTH + BACK_OFFSET
        return Unit(card=card, side=side, lane=lane, row=row, is_commander=is_commander,
                    max_hp=hp, hp=hp, pos=pos)

    # ---- 参照値 -----------------------------------------------------------

    def base(self, u: Unit, key: str) -> int:
        return self.defaults[u.troop][key]

    def speed(self, u: Unit) -> int:
        v = u.card["speed"]
        if u.troop == "cav" and "cav_speed" in self.field:
            v = v * self.field["cav_speed"] // 100
        v = v * (100 + u.mod("speed")) // 100
        return max(1, v)

    def reach(self, u: Unit) -> int:
        r = self.base(u, "range")
        if u.troop == "arc" and "arc_range" in self.field:
            r = r * self.field["arc_range"] // 100
        if "all_range" in self.field:
            r = r * self.field["all_range"] // 100
        return r

    def interval(self, u: Unit) -> int:
        return self.base(u, "interval")

    def accuracy(self, u: Unit, target: Unit) -> int:
        acc = u.card["acc"]
        if u.troop == "arc" and "arc_acc" in self.field:
            acc += self.field["arc_acc"]
        acc += u.mod("acc")
        # 回避補正は兵種標準値と移動速度から算出する（§6.3）
        evade = self.base(target, "evade_base") + target.card["speed"] // 4
        return max(ACC_MIN, min(ACC_MAX, acc - evade))

    def gauge_rate(self, u: Unit) -> int:
        rate = u.card["gauge_rate"]
        if u.is_commander:
            rate = rate * COMMANDER_GAUGE_BONUS // 100
        return rate

    def alive(self, side: int, lane: int | None = None):
        return [u for u in self.units
                if u.side == side and not u.retreated and (lane is None or u.lane == lane)]

    # ---- ダメージ（§6.2） -------------------------------------------------

    def damage(self, attacker: Unit, target: Unit, power: int, *,
               is_skill: bool, roll_crit: bool = True) -> int:
        wear = WEAR_FLOOR + WEAR_RANGE * attacker.hp // attacker.max_hp
        atk = attacker.card["atk"] * (100 + attacker.mod("atk")) // 100
        base = atk * power // 100
        base = base * wear // 1000
        if BEATS.get(attacker.troop) == target.troop:
            base = base * TROOP_ADVANTAGE // 100
        dfn = target.card["dfn"] * (100 + target.mod("dfn")) // 100
        dfn = max(0, dfn)
        if "front_taken" in self.field and target.row == "front":
            base = base * self.field["front_taken"] // 100
        dmg = base * DEFENSE_K // (DEFENSE_K + dfn)
        crit = False
        if roll_crit and self.rng.pct() < attacker.card["crit"]:
            dmg = dmg * CRIT_MULT // 100
            crit = True
        if not is_skill:
            span = DAMAGE_VARIANCE * 2 + 1
            dmg = dmg * (100 - DAMAGE_VARIANCE + self.rng.below(span)) // 100
        dmg = max(dmg, base * DAMAGE_FLOOR_PCT // 100)
        return self.apply_damage(attacker, target, dmg, crit)

    def sudden(self) -> int:
        """決着促進の倍率（百分率）。SUDDEN_START 以降、上限時刻へ向けて逓増する。"""
        if self.tick <= SUDDEN_START:
            return 100
        span = max(1, MAX_TICKS - SUDDEN_START)
        return 100 + (SUDDEN_MAX - 100) * (self.tick - SUDDEN_START) // span

    def apply_damage(self, attacker: Unit | None, target: Unit, dmg: int, crit: bool) -> int:
        dmg = dmg * self.sudden() // 100
        dmg = min(dmg, target.hp)
        target.hp -= dmg
        target.taken += dmg
        if attacker is not None:
            attacker.dealt += dmg
            attacker.crits += 1 if crit else 0
            # ゲージ: 与ダメージ / 被ダメージ（§7.2）
            attacker.gauge += dmg * 2000 // max(1, target.max_hp)
        target.gauge += dmg * 1500 // max(1, target.max_hp)
        return dmg

    # ---- 標的選択（§5.2） ------------------------------------------------

    def pick_target(self, u: Unit):
        enemies = self.alive(1 - u.side, u.lane)
        if not enemies:
            return None
        reach = self.reach(u)
        in_range = [e for e in enemies if abs(e.pos - u.pos) <= reach]
        if not in_range:
            return None
        # 弓兵（前衛越し攻撃が可能な兵種）は狙撃を行い、残兵力率の最も低い敵を狙う。
        # 総大将を名指しで狙わせると「総大将を先に落とした側が勝つ」だけの競争になり、
        # 編成・配置の判断が結果に反映されなくなるため、優先対象にはしない。
        if u.troop == "arc":
            return min(in_range, key=lambda e: (e.hp * 1000 // e.max_hp,
                                                e.row != "front", e.card["id"]))
        # 歩兵・騎兵は前衛優先、同条件なら残兵力率の低い方
        return min(in_range, key=lambda e: (e.row != "front",
                                            e.hp * 1000 // e.max_hp, e.card["id"]))

    def approach_target(self, u: Unit):
        """移動先を決めるための標的（射程外でもよい）。"""
        enemies = self.alive(1 - u.side, u.lane)
        if not enemies:
            return None
        if u.troop == "arc":
            return min(enemies, key=lambda e: (e.hp * 1000 // e.max_hp,
                                               e.row != "front", e.card["id"]))
        return min(enemies, key=lambda e: (e.row != "front",
                                           e.hp * 1000 // e.max_hp, e.card["id"]))

    # ---- 必殺技（§7） ----------------------------------------------------

    def skill_targets(self, u: Unit, selector: str):
        foes = self.alive(1 - u.side)
        allies = self.alive(u.side)
        if selector == "front_enemy":
            t = self.pick_target(u) or (min(self.alive(1 - u.side, u.lane),
                                            key=lambda e: (e.row != "front", e.hp),
                                            default=None))
            return [t] if t else []
        if selector == "lane_enemies":
            return self.alive(1 - u.side, u.lane)
        if selector == "enemy_back_lane":
            back = [e for e in self.alive(1 - u.side, u.lane) if e.row == "back"]
            return back or self.alive(1 - u.side, u.lane)
        if selector == "all_enemies":
            return foes
        if selector == "enemy_lowest":
            return [min(foes, key=lambda e: (e.hp, e.card["id"]))] if foes else []
        if selector == "lane_allies":
            return self.alive(u.side, u.lane)
        if selector == "all_allies":
            return allies
        if selector == "lowest_ally":
            return [min(allies, key=lambda a: (a.hp, a.card["id"]))] if allies else []
        if selector == "self":
            return [u]
        raise ValueError(f"unknown selector: {selector}")

    def cast(self, u: Unit):
        skill = u.card["skill"]
        targets = self.skill_targets(u, skill["target"])
        u.skills += 1
        u.gauge = 0
        for eff in skill["effects"]:
            kind = eff["type"]
            for t in targets:
                if t.retreated:
                    continue
                if kind == "damage":
                    # 必殺技は原則必中（§6.4）。乱数はクリティカル判定のみ消費する。
                    self.damage(u, t, eff["power"], is_skill=True)
                elif kind == "mod":
                    self.add_effect(t, Effect(kind="mod", stat=eff["stat"], value=eff["value"],
                                              remaining=eff["duration"], name=skill["name"]))
                elif kind == "stun":
                    self.add_effect(t, Effect(kind="stun", remaining=eff["duration"],
                                              name=skill["name"]))
                elif kind == "dot":
                    self.add_effect(t, Effect(kind="dot", value=eff["power"],
                                              remaining=eff["duration"],
                                              interval=eff["interval"],
                                              countdown=eff["interval"],
                                              source_atk=u.card["atk"], name=skill["name"]))
                elif kind == "gauge":
                    t.gauge = min(GAUGE_MAX, t.gauge + eff["value"])
        self.emit(f"{u.card['name']}の必殺技「{skill['name']}」が発動")

    def add_effect(self, target: Unit, eff: Effect):
        """同名の効果は上書きせず、効果量が大きい方・持続の長い方を採る（§6.5）。"""
        for cur in target.effects:
            if cur.kind == eff.kind and cur.stat == eff.stat and cur.name == eff.name:
                if abs(eff.value) > abs(cur.value):
                    cur.value = eff.value
                cur.remaining = max(cur.remaining, eff.remaining)
                return
        target.effects.append(eff)

    # ---- ティック処理（§8.1） --------------------------------------------

    def order(self):
        """行動順: 配置枠の固定順序 → 先攻側 → 後攻側。"""
        out = []
        for row, lane in SLOTS:
            for side in (self.first, 1 - self.first):
                for u in self.units:
                    if u.side == side and u.row == row and u.lane == lane and not u.retreated:
                        out.append(u)
        return out

    def step(self):
        self.tick += 1
        actors = self.order()

        # 1. 状態効果の経過処理
        for u in actors:
            if u.retreated:
                continue
            for e in list(u.effects):
                if e.kind == "dot":
                    e.countdown -= 1
                    if e.countdown <= 0:
                        e.countdown = e.interval
                        dmg = e.source_atk * e.value // 100
                        dmg = dmg * DEFENSE_K // (DEFENSE_K + u.card["dfn"])
                        self.apply_damage(None, u, dmg, False)
                if e.kind == "stun":
                    u.stun_ticks += 1
                e.remaining -= 1
                if e.remaining <= 0:
                    u.effects.remove(e)

        # 2-3. 移動と接敵判定
        for u in actors:
            if u.retreated or u.stunned():
                continue
            if not self.alive(1 - u.side, u.lane):
                self.lane_support(u)
                continue
            target = self.approach_target(u)
            if target is None:
                continue
            gap = abs(target.pos - u.pos)
            if gap > self.reach(u):
                mv = min(self.speed(u), gap - self.reach(u))
                u.pos += mv if u.side == 0 else -mv

        # 4-8. 攻撃可否・標的選択・命中・クリティカル・ダメージ
        for u in actors:
            if u.retreated or u.stunned() or self.tick < u.next_attack:
                continue
            target = self.pick_target(u)
            if target is None:
                continue
            u.next_attack = self.tick + self.interval(u)
            u.swings += 1
            if self.rng.pct() < self.accuracy(u, target):
                u.hits += 1
                self.damage(u, target, 100, is_skill=False)

        # 9. ゲージ増加（時間経過分。与被ダメージ分は damage() 内で加算済み）
        for u in actors:
            if u.retreated:
                continue
            u.gauge += GAUGE_PER_SEC // 10 * self.gauge_rate(u) // 100

        # 10. 必殺技発動。ゲージ最大の武将を §7.3 の順で処理する。
        ready = [u for u in actors if not u.retreated and u.gauge >= GAUGE_MAX]
        ready.sort(key=lambda u: (-self.speed(u), SLOTS.index((u.row, u.lane)), u.card["id"]))
        for u in ready:
            if u.retreated:
                continue
            self.cast(u)

        # 11. 撤退判定
        for u in self.units:
            if not u.retreated and u.hp <= 0:
                u.retreated = True
                u.hp = 0
                self.emit(f"{u.card['name']}が撤退")
                # 敵の撤退でゲージ +10（§7.2）
                for k in self.alive(1 - u.side, u.lane):
                    k.gauge += 1000

        # 12. 勝敗判定
        return self.judge()

    def lane_support(self, u: Unit):
        """自レーンの敵が全滅した場合の隣レーン支援移動（§5.2）。"""
        if self.field.get("no_lane_support"):
            return
        if u.transfer > 0:
            u.transfer -= self.speed(u)
            if u.transfer <= 0:
                u.lane = u.transfer_to
                u.transfer = 0
            return
        candidates = []
        for lane in (u.lane - 1, u.lane + 1):
            if 0 <= lane <= 2:
                foes = self.alive(1 - u.side, lane)
                if foes:
                    # 敵が多いレーンを優先し、同数なら中央を優先する
                    candidates.append((-len(foes), abs(lane - 1), lane))
        if not candidates:
            return
        candidates.sort()
        u.transfer_to = candidates[0][2]
        u.transfer = 25 * 10

    def judge(self):
        """総大将撤退を最優先し、同一ティックでの同時成立は引き分け（§8.2・確定）。"""
        lost = []
        for side in (0, 1):
            cmdr = next(u for u in self.units if u.side == side and u.is_commander)
            wiped = not self.alive(side)
            if cmdr.retreated or wiped:
                lost.append(side)
        if len(lost) == 2:
            return self.finish(None, "同時成立")
        if lost:
            reason = "総大将撤退" if next(
                u for u in self.units if u.side == lost[0] and u.is_commander).retreated else "全滅"
            return self.finish(1 - lost[0], reason)
        return None

    def remaining_rate(self, side: int) -> int:
        """残存兵力率（千分率）。"""
        total = sum(u.max_hp for u in self.units if u.side == side)
        left = sum(u.hp for u in self.units if u.side == side)
        return left * 1000 // max(1, total)

    def finish(self, winner, reason):
        self.result = {
            "winner": winner, "reason": reason, "ticks": self.tick,
            "remaining": [self.remaining_rate(0), self.remaining_rate(1)],
            "battlefield": self.field_key,
        }
        return self.result

    def timeout(self):
        """時間切れ判定（§8.2）。"""
        r0, r1 = self.remaining_rate(0), self.remaining_rate(1)
        if abs(r0 - r1) >= 10:            # 差が1%以上
            return self.finish(0 if r0 > r1 else 1, "時間切れ・残存兵力")
        d0 = sum(u.dealt for u in self.units if u.side == 0)
        d1 = sum(u.dealt for u in self.units if u.side == 1)
        if d0 != d1:
            return self.finish(0 if d0 > d1 else 1, "時間切れ・与ダメージ")
        n0, n1 = len(self.alive(0)), len(self.alive(1))
        if n0 != n1:
            return self.finish(0 if n0 > n1 else 1, "時間切れ・残存部隊数")
        c0 = sum(u.card["cost"] for u in self.units if u.side == 0)
        c1 = sum(u.card["cost"] for u in self.units if u.side == 1)
        if c0 != c1:
            return self.finish(0 if c0 < c1 else 1, "時間切れ・低コスト")
        return self.finish(None, "引き分け")

    def emit(self, text: str):
        if self.log:
            self.events.append((self.tick, text))

    def run(self):
        while self.tick < MAX_TICKS:
            res = self.step()
            if res:
                return res
        return self.timeout()


def load_cards(path: str | None = None):
    path = path or os.path.join(os.path.dirname(__file__), "cards.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data, {c["id"]: c for c in data["cards"]}


DATA, CARDS = load_cards()
TROOP_TABLE = DATA["troop_defaults"]


def make_team(card_ids, placement=None, commander=0):
    """card_ids の順に 前衛左・中・右 → 後衛左・中・右 へ配置する。"""
    placement = placement or SLOTS
    units = []
    for cid, (row, lane) in zip(card_ids, placement):
        units.append({"card": CARDS[cid], "lane": lane, "row": row})
    return {"units": units, "commander": commander}


def simulate(team_a, team_b, seed: int, battlefield: str = "clear", log: bool = False):
    return Battle([team_a, team_b], seed, battlefield, log).run()


if __name__ == "__main__":
    a = make_team(["chohi_toyo", "kakoton_dokugan", "ryofu_hisho",
                   "kochu_teigun", "shokatsuryo_garyo", "chouun_chohan"], commander=4)
    b = make_team(["jokou_choku", "gakushin_sento", "kanu_kanju",
                   "shuyu_sekiheki", "rikuson_iryo", "choryo_shoyoshin"], commander=4)
    battle = Battle([a, b], seed=12345, log=True)
    res = battle.run()
    for tick, text in battle.events:
        print(f"【{tick // 600:02d}:{tick // 10 % 60:02d}】 {text}")
    print(res)
