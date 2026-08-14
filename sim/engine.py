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
KILL_GAUGE_SECONDS = 3        # 敵撤退時のゲージ加算。固定値ではなく「自然増加の何秒ぶん」
SURPLUS_GAUGE_PER_COST = 2    # 余剰コスト1につき初期ゲージ +2%（§4.5）
SURPLUS_GAUGE_CAP = 10        # 余剰コストによる初期ゲージの上限 +10%

DEFENSE_K = 100               # ダメージ軽減の定数 K（§6.2）
TROOP_ADVANTAGE = 104         # 兵種有利のダメージ補正 +4%（§5.3・v0.4 で改訂）
# 部隊戦は60秒以上続くため、毎回の攻撃に乗る補正は複利で効く。+15% では
# コスト揃えの単一兵種編成で有利側が100%勝利していた。実測は
# +3%→47/100/64、+5%→72/100/76、+10%→95/100/95（歩→騎/騎→弓/弓→歩）。

# 歩兵が弓兵から受けるダメージの軽減（%）。
# 弓兵→歩兵の優位は、射程による接敵前攻撃だけでなく「弓兵は残兵力率が最も低い敵を
# 狙えるが、歩兵は前衛しか殴れない」という標的選択の差からも来ている。この非対称は
# 兵種ごとの一律係数（roster.BEHAVIOR_PREMIUM）では吸収できないため、
# 相性そのものに対策を置く。盾を並べて矢を受け止める表現でもある（§5.3）。
INF_RANGED_GUARD = 2

CRIT_MULT = 150               # クリティカル倍率 1.5倍（§6.4）
DAMAGE_VARIANCE = 5           # 通常ダメージ乱数 ±5%（§6.4）
DAMAGE_FLOOR_PCT = 10         # 最低保証ダメージ = 基本ダメージの10%（§6.2）
WEAR_FLOOR = 500              # 消耗係数の下限 0.5（§6.1・v0.5 で改訂）
WEAR_RANGE = 1000 - WEAR_FLOOR

# 撤退した味方の必殺技ゲージを、生存している味方へ引き継ぐ割合（%）。
# 必殺技ゲージを個人の持ち物ではなく「部隊全体の資産」として扱う。
# 部隊が1体減ると必殺技と固有特性がまるごと失われるため、低コスト武将を多く並べた
# 編成が構造的に不利になる。引き継ぎはその罰を緩める。
# 同時に「ゲージ上昇率の高い安い武将を先に落とさせ、強い必殺技を持つ武将へ回す」
# という組み立てを可能にする。
GAUGE_INHERIT_PCT = 100

# 決着促進（v0.3 で追加を提案）。上限時間まで粘る展開が多すぎると、
# 部隊戦の1/3がタイマー決着になり実況の締まりが悪くなるため、
# 一定時刻から与ダメージを逓増させて上限前に決着させる。
SUDDEN_START = 350            # 35秒から発動
SUDDEN_MAX = 500              # 90秒時点で 5.0倍
# v0.5 で 50秒開始・3.0倍から強めた。迂回と後退射撃を入れて交戦が始まるのが遅くなり、
# 時間切れ決着が 12% から 48% へ増えたため。強めても 37% までしか戻らない。
# 主因は弓兵の後退射撃で、決着促進では対症療法にしかならない（§13）。

ACC_MIN = 20                  # 状態効果適用後の最終命中率の下限（§6.5）
ACC_MAX = 100
MOD_CAP = 50                  # 1つの能力への補正合計は ±50% に丸める（§6.5）

COMMANDER_HP_BONUS = 110      # 総大将の最大兵力 +10%（§4.2）
COMMANDER_GAUGE_BONUS = 110   # 総大将の必殺技ゲージ上昇率 +10%（§4.2）

# 固有特性「陣頭」（§4.2）。総大将として前衛に配置したときだけ働く。
# 総大将は後衛に置くのが基本的に有利であり、それ自体は自然な判断として受け入れる。
# 前衛配置を全体調整で成立させるのではなく、この特性を持つ一部の武将にだけ
# 「前に出て戦う」選択肢を与える。
VANGUARD_HP_BONUS = 125       # 自身の最大兵力 +25%
VANGUARD_ALLY_ATK = 8         # 味方全体の攻撃力 +8%

# 迂回（§5.2）。**騎兵は前衛を回り込んで後衛を狙う。回り込むあいだは攻撃できない。**
#
# 回り込む距離は敵前衛1人につき DETOUR_PER_BLOCKER。移動速度で割った時間だけ
# 攻撃が止まるので、前衛が厚いほど高くつき、足が速いほど安く済む。
# 兵種の役割はこれで分かれる。
#   歩兵 = 前衛を殴って盾を割る    騎兵 = 迂回して後衛を潰す    弓兵 = 射程で後衛を撃つ
#
# **時間で表すことが要点である。** 先に「突破できる／できない」の二値で作って失敗した。
# 通れるかどうかを人数比で決めると、抑えの値が5か6かだけで無敵の型が
# 騎兵突撃（91%）と盾＋弓（99%）のあいだで入れ替わり、**中間が存在しなかった**。
# さらにその前に「前衛1人につき1人ずつ抜ける」方式も試して失敗している。
# 2人で攻めるとき1人が前衛・1人が後衛へ分かれ、**攻め手だけが火力を分散する**。
# 守り手は集中し続けるので殴り合いで一方的に負け、騎兵→弓兵が 89% から 5% へ落ちた。
# 迂回を時間にすると、レーンの騎兵はまとめて回り込むので分散せず、
# かつ前衛の人数に比例して連続的に高くつく。二値にも分散にもならない。
DETOUR_PER_BLOCKER = 40 * 10

# 迂回した騎兵は敵中に孤立し、被ダメージが増える。
# これがないと迂回はあらゆる相手に得なので、**騎兵が万能になる**。
# 実測では歩兵→騎兵が 55% から 0% へ落ちた。歩兵の後衛は前衛と同じ硬さなので、
# 本来なら回り込む価値は薄いはずだが、罰がないため回り込み得になっていた。
# 罰を置くと「潰す価値のある後衛（弓兵）がいるときだけ回り込む」が正しくなる。
FLANKED_TAKEN = 130           # 迂回後の騎兵が受けるダメージ +30%

# 接敵抑制（§5.3）。近接兵に MELEE_GRIP まで寄られた弓兵は威力が落ちる。
#
# **これが射程に価値を与える本体である。** 弓兵が接敵しても素の威力のまま戦えると、
# 寄り切る側に何の見返りもない。実測では、盾と突破だけを入れても騎兵→弓兵は
# 3〜30% までしか戻らなかった。後衛へ抜ける権利を与えても、弓兵の射程内を
# より深く歩かされるだけで損をしていた。
# 接敵に罰があって初めて「速く寄る」ことが騎兵の役割になり、
# 「盾で寄らせない」ことが歩兵の役割になる。
MELEE_GRIP = 12 * 10
ARC_SUPPRESS = 8              # 接敵中の弓兵の与ダメージ −8%
# 8% でも効きは大きい。25% にすると弓兵→歩兵が 70% から 0% へ落ちる。
# §4.6 の増幅（総合値1%あたり勝率6.5pt）がそのまま乗るため、
# 見た目の小ささに対して勝率の動きが桁違いになる。

# 後退射撃（§5.3）。近接に KITE_TRIGGER まで寄られた弓兵は、下がりながら撃つ。
# 後退速度は移動速度の KITE_SPEED_PCT%。開始位置より後ろへは下がれない。
# これがないと射程の価値は「最初の数発」で尽き、寄り切られた時点で消える。
# 後衛に置いた弓兵ほど下がる余地が大きいため、配置の判断にも意味が出る。
KITE_TRIGGER = 20 * 10
KITE_SPEED_PCT = 50

# 三すくみ（§5.3）。attacker が victim に対して有利なら True。
BEATS = {"cav": "arc", "arc": "inf", "inf": "cav"}

# 配置枠の固定順序（§8.1）。行動順の決定に使う。
SLOTS = [("front", 0), ("front", 1), ("front", 2),
         ("back", 0), ("back", 1), ("back", 2)]

# 戦場条件（§5.4）。迂回のしやすさが地形の主要な効き目になる。
# 開けた地では騎兵が回り込みやすく、狭い地ではそもそも回り込めない。
BATTLEFIELDS = {
    "plain":  {"label": "平原", "cav_speed": 115, "detour": 85},
    "narrow": {"label": "隘路", "no_lane_support": True, "front_taken": 90,
               "no_detour": True},
    "rain":   {"label": "雨天", "arc_acc": -15, "arc_range": 80, "detour": 115},
    "fog":    {"label": "濃霧", "all_range": 70},
    "clear":  {"label": "平時"},
}

# 陣形（§4.1）。6枠へ自由に配置させるのではなく、名前のついた型から選ばせる。
# プレイヤーの操作は「選ぶだけ」で済み、組み合わせが有限なので釣り合わせられる。
# 自由配置だと数百通りになり、balance.py で測りきれない。
# 前衛の人数が迂回の値段を決めるので、そこが型ごとの個性になる。
FORMATIONS = {
    "kakuyoku": {"label": "鶴翼", "note": "前3後3。横に広く、どのレーンにも盾がある",
                 "slots": [("front", 0), ("front", 1), ("front", 2),
                           ("back", 0), ("back", 1), ("back", 2)]},
    "gyorin":   {"label": "魚鱗", "note": "前4後2。中央を厚くして押し込む",
                 "slots": [("front", 1), ("front", 1), ("front", 0), ("front", 2),
                           ("back", 1), ("back", 1)]},
    "hoen":     {"label": "方円", "note": "前5後1。守りを固め、中央に1人だけ隠す",
                 "slots": [("front", 0), ("front", 0), ("front", 1),
                           ("front", 2), ("front", 2), ("back", 1)]},
    "gankou":   {"label": "雁行", "note": "前2後4。盾を削って射手を並べる",
                 "slots": [("front", 0), ("front", 2),
                           ("back", 0), ("back", 1), ("back", 1), ("back", 2)]},
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
    home: int = 0             # 開始位置。弓兵が後退射撃で下がれる限界（§5.3）
    gauge: int = 0
    next_attack: int = 0      # 次に攻撃できるティック
    effects: list = field(default_factory=list)
    retreated: bool = False
    transfer: int = 0         # 隣レーンへの支援移動の残り距離
    detour: int = 0           # 迂回の残り距離（§5.2）
    flanking: bool = False    # このティックは迂回中。攻撃できない
    flanked: bool = False     # 回り込み済み。後衛を狙える
    # 統計
    dealt: int = 0
    taken: int = 0
    hits: int = 0
    swings: int = 0
    crits: int = 0
    skills: int = 0
    stun_ticks: int = 0
    triggers: int = 0                       # 誘発型の固有特性が発動した回数
    fired: dict = field(default_factory=dict)   # 特性名 → 発動回数

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
        self.firing = False           # 誘発の連鎖を防ぐためのフラグ
        for side, team in enumerate(teams):
            for idx, entry in enumerate(team["units"]):
                self.units.append(self._make_unit(entry, side, idx == team["commander"]))
        self.trigger_events = {t["trigger"] for u in self.units
                               for t in self.triggers_of(u.card)}
        self.apply_vanguard()
        self.apply_surplus(teams)
        self.result = None

    def apply_surplus(self, teams):
        """余剰コストを初期必殺技ゲージへ変換する（§4.5）。

        6人固定かつコスト上限制のため、コストを余らせても得がないと
        「上限ぴったりに使い切る」以外の選択肢が消える。率で与えるため、
        ゲージ上昇率の高低にかかわらず同じ割合だけ前倒しになる。
        """
        for side, team in enumerate(teams):
            cap = team.get("cap")
            if cap is None:
                continue
            spent = sum(e["card"]["cost"] for e in team["units"])
            surplus = max(0, cap - spent)
            pct = min(SURPLUS_GAUGE_CAP, surplus * SURPLUS_GAUGE_PER_COST)
            if not pct:
                continue
            for u in self.units:
                if u.side == side:
                    u.gauge += GAUGE_MAX * pct // 100

    def apply_vanguard(self):
        """「陣頭」を持つ総大将が前衛にいる側へ、開戦時に味方全体の攻撃力補正を与える。"""
        for side in (0, 1):
            cmdr = next(u for u in self.units if u.side == side and u.is_commander)
            if cmdr.row != "front" or not self.has_trait(cmdr.card, "vanguard"):
                continue
            for u in self.units:
                if u.side == side:
                    u.effects.append(Effect(kind="mod", stat="atk", value=VANGUARD_ALLY_ATK,
                                            remaining=MAX_TICKS + 1, name="陣頭"))

    # ---- 構築 -------------------------------------------------------------

    def has_trait(self, card, trait) -> bool:
        return trait in card.get("traits", [])

    def _make_unit(self, entry, side, is_commander) -> Unit:
        card = entry["card"]
        hp = card["hp"]
        lane, row = entry["lane"], entry["row"]
        if is_commander:
            hp = hp * COMMANDER_HP_BONUS // 100
            if row == "front" and self.has_trait(card, "vanguard"):
                hp = hp * VANGUARD_HP_BONUS // 100
        if side == 0:
            pos = 0 if row == "front" else -BACK_OFFSET
        else:
            pos = LANE_DEPTH if row == "front" else LANE_DEPTH + BACK_OFFSET
        return Unit(card=card, side=side, lane=lane, row=row, is_commander=is_commander,
                    max_hp=hp, hp=hp, pos=pos, home=pos)

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

    def exposed(self, u: Unit) -> bool:
        """迂回した騎兵が敵中に孤立しているか（§5.2）。

        **敵前衛が残っているあいだだけ**。盾を割ってしまえば背後も何もないので、
        罰を残し続けるのは筋が通らないし、騎兵が回り込む価値も無くなる。
        """
        return u.flanked and any(e.row == "front"
                                 for e in self.alive(1 - u.side, u.lane))

    def suppressed(self, u: Unit) -> bool:
        """弓兵が近接兵に組みつかれているか（§5.3）。組みつかれると威力が落ちる。"""
        if u.troop != "arc":
            return False
        return any(abs(e.pos - u.pos) <= MELEE_GRIP
                   for e in self.alive(1 - u.side, u.lane) if e.troop != "arc")

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

    def gauge_seconds(self, u: Unit, seconds: int) -> int:
        """「自然増加の n 秒ぶん」をゲージ量へ換算する。

        固定値でゲージを与えると、ゲージ上昇率の低い武将ほど相対的に得をする。
        秒数で定義すれば、その武将自身の上昇率に比例するため歪みが出ない。
        """
        return GAUGE_PER_SEC * self.gauge_rate(u) // 100 * seconds

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
        if self.suppressed(attacker):
            base = base * (100 - ARC_SUPPRESS) // 100
        if self.exposed(target):
            base = base * FLANKED_TAKEN // 100
        dfn = target.card["dfn"] * (100 + target.mod("dfn")) // 100
        dfn = max(0, dfn)
        if target.troop == "inf" and attacker.troop == "arc":
            base = base * (100 - INF_RANGED_GUARD) // 100
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

    # ---- 突破（§5.2） ----------------------------------------------------

    def flank_step(self, u: Unit, enemies: list) -> bool:
        """騎兵の迂回を1ティック進める（§5.2）。迂回中なら True。

        敵前衛と接触した時点から回り込みを始める。**回り込みが終わるまで攻撃できず、
        その失われた攻撃時間が迂回の値段になる。** 距離は敵前衛の人数に比例し、
        自分の移動速度で割った時間がかかるので、前衛が厚いほど高く、足が速いほど安い。
        """
        if u.troop != "cav" or u.flanked or self.field.get("no_detour"):
            return False
        front = [e for e in enemies if e.row == "front"]
        if not front or not any(e.row == "back" for e in enemies):
            return False          # 盾がない、または後衛がいないなら回り込む必要がない
        if min(abs(e.pos - u.pos) for e in front) > self.reach(u):
            return False          # まだ接触していない。通常どおり前進する
        if not u.detour:
            cost = len(front) * DETOUR_PER_BLOCKER
            u.detour = max(1, cost * self.field.get("detour", 100) // 100)
        u.detour -= self.speed(u)
        if u.detour <= 0:
            u.detour = 0
            u.flanked = True
            self.emit(f"{u.card['name']}が前衛を迂回した")
            return False
        return True

    # ---- 標的選択（§5.2） ------------------------------------------------

    def reachable(self, u: Unit, enemies: list) -> list:
        """u が狙える敵を返す。**前衛は後衛の盾になる。**

        近接兵は、そのレーンに敵前衛が生きているあいだ後衛へ届かない。回り込みを
        終えた騎兵だけが後衛を狙える。弓兵は射程が届くので制限を受けない。

        この「盾」がないと迂回は成立しない。盾のない状態で「後衛を狙う権利」だけを
        与えたところ、騎兵は弓兵の射程内をより深くまで歩かされるだけになり、
        勝率が 89% から 5% へ落ちた。迂回は盾を前提にして初めて利点になる。
        """
        if u.troop == "arc":
            return enemies
        front = [e for e in enemies if e.row == "front"]
        if not front:
            return enemies
        if u.flanked:
            return [e for e in enemies if e.row == "back"] or enemies
        return front

    def pick_target(self, u: Unit):
        enemies = self.reachable(u, self.alive(1 - u.side, u.lane))
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
        # 歩兵・騎兵は狙える範囲のうち残兵力率の低い敵を狙う。
        # 前衛/後衛の制限は reachable() が済ませている（§5.2）。
        return min(in_range, key=lambda e: (e.hp * 1000 // e.max_hp, e.card["id"]))

    def approach_target(self, u: Unit):
        """移動先を決めるための標的（射程外でもよい）。"""
        enemies = self.reachable(u, self.alive(1 - u.side, u.lane))
        if not enemies:
            return None
        if u.troop == "arc":
            return min(enemies, key=lambda e: (e.hp * 1000 // e.max_hp,
                                               e.row != "front", e.card["id"]))
        return min(enemies, key=lambda e: (e.hp * 1000 // e.max_hp, e.card["id"]))

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

    def apply_effects(self, source: Unit, effects, targets, label: str):
        """効果の並びを対象へ適用する。必殺技と誘発型の固有特性で共通に使う。"""
        for eff in effects:
            kind = eff["type"]
            for t in targets:
                if t.retreated:
                    continue
                if kind == "damage":
                    # 必殺技は原則必中（§6.4）。乱数はクリティカル判定のみ消費する。
                    self.damage(source, t, eff["power"], is_skill=True)
                elif kind == "mod":
                    self.add_effect(t, Effect(kind="mod", stat=eff["stat"], value=eff["value"],
                                              remaining=eff["duration"], name=label))
                elif kind == "stun":
                    self.add_effect(t, Effect(kind="stun", remaining=eff["duration"],
                                              name=label))
                elif kind == "dot":
                    self.add_effect(t, Effect(kind="dot", value=eff["power"],
                                              remaining=eff["duration"],
                                              interval=eff["interval"],
                                              countdown=eff["interval"],
                                              source_atk=source.card["atk"], name=label))
                elif kind == "gauge":
                    # seconds 指定を基本とする。value は旧形式の固定値。
                    gain = (self.gauge_seconds(t, eff["seconds"]) if "seconds" in eff
                            else eff.get("value", 0))
                    t.gauge = min(GAUGE_MAX, t.gauge + gain)

    def cast(self, u: Unit):
        skill = u.card["skill"]
        targets = self.skill_targets(u, skill["target"])
        u.skills += 1
        u.gauge = 0
        self.apply_effects(u, skill["effects"], targets, skill["name"])
        self.emit(f"{u.card['name']}の必殺技「{skill['name']}」が発動")
        # 味方の必殺技発動を誘発条件とする固有特性（§6.6）
        self.fire("ally_skill", u.side, source=u)

    # ---- 誘発型の固有特性（§6.6） ----------------------------------------

    def triggers_of(self, card):
        """カードの固有特性のうち、誘発型（dict 形式）だけを返す。

        文字列は常在型の組み込み特性（陣頭など）で、ここでは扱わない。
        """
        return [t for t in card.get("traits", []) if isinstance(t, dict)]

    def fire(self, event: str, side: int, source: Unit | None = None,
             subject: Unit | None = None):
        """誘発条件 event を満たした側の固有特性を発動させる。

        - 対象は side 側の生存武将。処理順は配置枠の固定順序（§8.1）で一意にする。
        - 各特性には発動回数の上限がある（既定1回）。上限まで達したら以後発動しない。
        - **誘発の連鎖は許さない。** 誘発型の効果が別の誘発型を呼ぶと、順序と回数が
          編成によって変わり、§8.4 の再現性と実況の可読性が壊れる。
        """
        if self.firing or event not in self.trigger_events:
            return
        self.firing = True
        try:
            for u in self.order():
                if u.side != side or u.retreated:
                    continue
                for tr in self.triggers_of(u.card):
                    if tr["trigger"] != event:
                        continue
                    if u is subject and not tr.get("include_self", True):
                        continue
                    name = tr["name"]
                    if u.fired.get(name, 0) >= tr.get("limit", 1):
                        continue
                    if event == "self_low_hp":
                        if u.hp * 100 > u.max_hp * tr.get("threshold", 50):
                            continue
                    targets = self.skill_targets(u, tr["target"])
                    if not targets:
                        continue
                    u.fired[name] = u.fired.get(name, 0) + 1
                    u.triggers += 1
                    self.apply_effects(u, tr["effects"], targets, name)
                    self.emit(f"{u.card['name']}の固有特性「{name}」が発動")
        finally:
            self.firing = False

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
            u.flanking = False
            if not self.alive(1 - u.side, u.lane):
                self.lane_support(u)
                continue
            # 騎兵の迂回。回り込んでいるあいだは前進も攻撃もしない（§5.2）
            if self.flank_step(u, self.alive(1 - u.side, u.lane)):
                u.flanking = True
                continue
            # 弓兵は近接に寄られたら下がりながら撃つ（§5.3）。開始位置が下限なので、
            # 後衛に置いた弓兵ほど下がる余地が大きい。移動速度は歩兵8・騎兵12に対し
            # 弓兵7で、後退はその半分。振り切ることはできず、時間を買うだけ。
            if u.troop == "arc":
                threat = min((abs(e.pos - u.pos)
                              for e in self.alive(1 - u.side, u.lane)
                              if e.troop != "arc"), default=None)
                if threat is not None and threat < KITE_TRIGGER:
                    step = max(1, self.speed(u) * KITE_SPEED_PCT // 100)
                    u.pos = (max(u.home, u.pos - step) if u.side == 0
                             else min(u.home, u.pos + step))
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
            if u.retreated or u.stunned() or u.flanking or self.tick < u.next_attack:
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

        # 兵力低下を誘発条件とする固有特性（§6.6）。撤退判定の前に見る。
        for side in (self.first, 1 - self.first):
            self.fire("self_low_hp", side)

        # 11. 撤退判定
        for u in self.units:
            if not u.retreated and u.hp <= 0:
                u.retreated = True
                u.hp = 0
                self.emit(f"{u.card['name']}が撤退")
                # 敵の撤退によるゲージ加算（§7.2）。固定値ではなく秒数換算。
                for k in self.alive(1 - u.side, u.lane):
                    k.gauge += self.gauge_seconds(k, KILL_GAUGE_SECONDS)
                # 撤退した味方のゲージを生存味方へ引き継ぐ（部隊全体資産）。
                if GAUGE_INHERIT_PCT and u.gauge > 0:
                    allies = self.alive(u.side)
                    if allies:
                        share = u.gauge * GAUGE_INHERIT_PCT // 100 // len(allies)
                        for a in allies:
                            a.gauge = min(GAUGE_MAX, a.gauge + share)
                    u.gauge = 0
                # 撤退を誘発条件とする固有特性（§6.6）
                self.fire("ally_retreat", u.side, source=u, subject=u)
                self.fire("enemy_retreat", 1 - u.side, source=u)

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


def make_team(card_ids, placement=None, commander=0, formation=None):
    """card_ids を配置する。formation を指定すると §4.1 の陣形の枠順に従う。"""
    if formation is not None:
        placement = FORMATIONS[formation]["slots"]
    placement = placement or SLOTS
    units = []
    for cid, (row, lane) in zip(card_ids, placement):
        units.append({"card": CARDS[cid], "lane": lane, "row": row})
    return {"units": units, "commander": commander}


def simulate(team_a, team_b, seed: int, battlefield: str = "clear", log: bool = False):
    return Battle([team_a, team_b], seed, battlefield, log).run()


if __name__ == "__main__":
    # 動作確認用に、コスト上限30の編成を2つ組んで1戦を実況付きで再生する。
    def pick(costs, exclude=()):
        used = set(exclude)
        out = []
        for c in costs:
            cid = next(k for k in sorted(CARDS)
                       if CARDS[k]["cost"] == c and CARDS[k]["person"] not in used)
            out.append(cid)
            used.add(CARDS[cid]["person"])
        return out

    left = pick([8, 6, 5, 5, 3, 3])
    right = pick([7, 6, 6, 5, 3, 3], exclude={CARDS[c]["person"] for c in left})
    battle = Battle([make_team(left, commander=4), make_team(right, commander=4)],
                    seed=12345, log=True)
    res = battle.run()
    for tick, text in battle.events:
        print(f"【{tick // 600:02d}:{tick // 10 % 60:02d}】 {text}")
    print(res)
