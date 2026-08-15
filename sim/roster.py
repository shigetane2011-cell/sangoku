#!/usr/bin/env python3
"""武将ロスターから cards.json を生成する。

能力値を手で置くのをやめ、**コストから機械的に導出する**。理由は2つある。

1. 60人ぶんの能力値を手で置くと、兵種ごと・コスト帯ごとの予算がすぐ崩れる。
   v0.3 の24枚では総合値/コストが 歩兵556 / 騎兵701 / 弓兵304 まで割れており、
   弓兵は他の2兵種に勝率0%だった。
2. コストを通貨として扱いたい（アイテムにもコストを持たせたい）なら、
   コストと強さの関係が式で表現されていなければならない。

コストと強さの関係（§4.6）:

    強さ(コスト) = 枠の基礎価値 + コスト比例分

1部隊は6枠固定なので、枠の基礎価値は全編成に等しく乗る。したがって
**合計コストが同じ編成は、配分によらず総価値が等しくなる**。これがないと、
コスト2の武将は相手の火力に耐えられず貢献前に消えるため、極端な配分編成が
一方的に弱くなる（実測: 極端配分は均等配分に勝率0%）。

usage: python3 sim/roster.py [--write]
"""

import json
import math
import os
import sys

# 枠の基礎価値の割合。コスト5の武将の強さを1.0としたときの、
# コストを1点も払わずに得られる分。sim/balance.py cost で決めた実測値。
SLOT_VALUE = 0.55
NORM_COST = 5           # 正規化の基準コスト
TARGET_SCORE = 2550     # コスト5の武将の総合値（実効耐久 × 実効火力 / 1000）
CRIT_MULT = 1.5         # クリティカル倍率（engine.CRIT_MULT と揃える）
REF_ACC = 90            # 回避の価値を測る基準命中率

# 行動面の価値の係数。射程と移動速度は「戦闘中にどれだけ得をするか」が
# 相手の兵種や戦場条件で変わるため、数式で価格付けできない。兵種ごとの係数を
# 実測（sim/balance.py troops）で決め、能力値から差し引く。
#   1.00 より大きい = 行動面で得をしているので素の能力値を下げる
#   1.00 より小さい = 行動面の見返りが薄いので素の能力値を上げる
# 弓兵は射程45により接敵前に数発を無償で撃てる。騎兵は移動速度が速いが、
# 6対6の膠着戦では接敵と隣レーン支援の時間短縮にしかならず見返りが薄い。
# 突撃（engine.CHARGE_BONUS）で速さが火力へ変換されるようになったため再較正した。
# 兵種ごとの平均勝率（自分以外の2兵種に対して・600戦）が 歩兵53.5 / 騎兵52.0 /
# 弓兵44.5 と割れており、弓兵だけが2σ（5.8pt）を超えて低かった。
# 50%からのずれを 10pt/% で総合値へ換算して置き直す。
BEHAVIOR_PREMIUM = {"inf": 1.004, "cav": 0.982, "arc": 1.044}

# --- 必殺技と固有特性の価値をコストへ織り込む（§7.5 案A）-------------------
#
# 能力値はコスト式で釣り合っているが、その上に乗る必殺技と固有特性は無料だった。
# 実測では必殺技の強さに幅86pt、固有特性に幅31ptがあり、勝敗が「どの効果を引いたか」
# で決まっていた。効果の価値を総合値へ換算して差し引く。
#
# 値は「その効果は総合値の何%ぶんに相当するか」で持つ。**勝率では持たない。**
# 効果の価値は勝率でしか測れないが、予算は総合値で表されている。両者の関係は
# 非線形（+2%→72%、+5%→80%、+8%→98% と飽和する）ため、換算率で一発割りする
# 方式は使えない。代わりに**残差を反復で潰す**。測り直して 50% から外れたぶんを
# ADJUST_STEP で割って現在値へ足し込み、また測り直す。手順は sim/README.md にある。
#
#     新しい補正 = 現在の補正 + (実測勝率 − 50) / ADJUST_STEP
#
# SUPPORT_STEP は1枚混ぜ方式（balance.py support）用の刻み。1枚ぶんしか効かないので
# 勾配が小さく、消耗係数があった頃は総合値1%あたり 1.5〜1.9pt だった。
# **撤廃後は 5.0pt/% へ上がっており、2.5 のままだと2倍行き過ぎて振動する**
# （burn が 29% → 71% と往復した）。6.0 へ倒す。
#
# なお2回測れているなら反復より**補間のほうが速い**。(補正, 勝率) の2点を結んで
# 勝率50%になる点を読めばよい。反復は1点しか無いときの手段である。
SUPPORT_STEP = 6.0

# ADJUST_STEP は「総合値1%あたり勝率何pt動くか」の見込み。大きいほど慎重に寄る。
# balance.py sensitivity は 6.5pt/% を返すが、**この値を入れると振動する。**
# sensitivity は両軍の能力を一様に倍する測り方で、効果を能力へ交換する場面より
# 勾配が緩い。1種類の補正だけを振って測ると、釣り合いの近くでは 10〜30pt/% あった
# （legacy を 1.0%〜5.0% で振ると 78%→30%、局所では -33pt/% の区間もある）。
# 勾配を過小に見積もると行き過ぎて往復するだけなので、**大きめの20に倒す。**
# 収束は遅くなるが、遅いのは待てばよく、振動は待っても直らない。
ADJUST_STEP = 20

# 必殺技ひな型の補正（総合値の%）。正 = 強いので素の能力値を下げる。
# **balance.py support（1枚だけ混ぜる方式）の実測から反復で決める。**
# balance.py skills（全員同技の総当たり）では測れない。全員が同じ技を持つと
# 全体効果の補正が上限（±50%）で頭打ちになり、curse と rally が弱く見える。
# 総当たりで46%/53%だったものが、1枚混ぜでは98%/97%だった。
SKILL_ADJUST = {
    "roar": 5.92, "burn": 5.28, "strike": 0.86, "guard": 0.22, "curse": 0.01,
    "rally": -3.41, "sweep": -4.26, "raid": -5.13, "hold": -5.75,
    "snipe": -5.83, "snare": -6.65, "urge": -12.57,
}

# --- 必殺技の価格を式で出す（§7.5）---------------------------------------
#
# 80枚それぞれに固有の必殺技を作ると、反復での価格付けは回らない。1反復で80回の
# 測定になるためである。**機構ごとに換算係数を一度測り、あとは計算で出す。**
#
#     価格 = 発動回数 × Σ(効果量 × 実効重み × min(持続, 残り時間) × 実効対象数)
#     発動回数 = 戦闘時間 ÷ (消費ゲージ ÷ ゲージ獲得速度)
#
# 素の式（効果量 × 対象数）では実測との相関が r=0.26 しかなく、成立していなかった。
# 抜けていた換算係数は3つである。
#
# 1. **能力補正の「%」は効果の「%」ではない。** 防御は除算方式 K/(K+dfn) なので
#    +28% でも被ダメージは −7.9% にしかならない。速度は移動にしか効かないので
#    接敵後の与ダメージには無価値（突撃の分だけ少し戻る）。
# 2. **持続時間は戦闘の残り時間で頭打ちになる。** ゲージは50秒で満タン、戦闘は
#    62秒なので、1回目の発動時点で残り約12秒しかない。250ティックの効果は
#    半分以上が捨てられている。
# 3. **範囲は対象数に比例しない。** 総ダメージを揃えて測ると、1列は単体の
#    実効54%、全体は30%しかない。撃破は敵の出力を丸ごと消すので複利で効くが、
#    分散ダメージは出力を消さないためである。
#
# この3つを入れると相関は r=0.26 → 0.69 まで上がる。
#
# **ただし残差は最大 5.9%（総合値）残っており、単独で価格を決めるには粗い。**
# 反復の測定誤差が 2σ で 3.5pt 前後なので、まだ誤差の外である。用途は
# 「新しい技の初期値を計算で置き、あとは反復で詰める」ことと、80枚ぶんを
# 一括で見積もって外れ値を探すことである。反復の置き換えにはならない。
#
# 残差の大きい順に snare / urge / strike / guard で、いずれも穴の在り処を指す。
#   snare  速度デバフの価値がまだ過大。接敵後は移動しないので実質ゼロに近い
#   urge   ゲージ付与を「通常攻撃1発ぶん」と雑に置いている
#   strike / guard  単体・自レーンへの効果を過小評価している
STAT_WEIGHT = {
    "atk": 1.00,      # 毎発の威力にそのまま乗る
    "dfn": 0.33,      # 除算方式 K/(K+dfn) で減衰する（+28% → 被ダメージ −7.9%）
    "acc": 1.00,      # 命中率に比例して手数が減る
    "speed": 0.10,    # 接敵後は効かない。突撃（§5.3）のぶんだけ価値がある
}

# 実効対象数。総ダメージを揃えて実測した「単体換算」から出す（balance.py support）。
#   単体 240%×1 = 単体換算240%（100%）  1列 120%×2 = 130%（54%）
#   全体  40%×6 = 単体換算 71%（30%）
# **2体目はほとんど価値がない。** 1列は単体の1.08倍にしかならない。レーンの
# 2体目は後衛で、前衛が生きているあいだ集中の対象にならないためである。
EFFECTIVE_TARGETS = {1: 1.00, 2: 1.08, 3: 1.35, 6: 1.79}

# バフ・デバフは対象数がそのまま効く。味方6人の攻撃力を上げれば6人ぶん働く。
# **ダメージだけが集中の影響を受ける**ので、係数を分ける。
BUFF_TARGETS = {1: 1.00, 2: 2.00, 3: 3.00, 6: 6.00}

# 回復1点はダメージ何点ぶんか。**回復は敵の出力を削らない**ので、分散ダメージが
# 弱いのと同じ理由で弱い。撃破は敵の火力を丸ごと消すが、回復は自軍の火力を
# 増やしはしない。実測では 回復150% が strike(190%) に18%、300%で32%、
# 500%で39%、800%で60%。釣り合うのは威力640%あたりで、190/640 ≒ 0.30。
HEAL_WEIGHT = 0.30


def effective_targets(n, is_damage):
    table = EFFECTIVE_TARGETS if is_damage else BUFF_TARGETS
    if n in table:
        return table[n]
    keys = sorted(table)
    lo = max(k for k in keys if k <= n) if any(k <= n for k in keys) else keys[0]
    hi = min(k for k in keys if k >= n) if any(k >= n for k in keys) else keys[-1]
    if lo == hi:
        return table[lo]
    f = (n - lo) / (hi - lo)
    return table[lo] + (table[hi] - table[lo]) * f


# 価格式が使う戦闘の実測値。engine を import せずに済むよう写しで持つ。
BATTLE_TICKS = 620          # 決着の中央値（balance.py check）
GAUGE_FILL_TICKS = 500      # ゲージ満タンまで = engine.GAUGE_MAX / GAUGE_PER_SEC
REF_INTERVAL = 12           # 換算の基準にする攻撃間隔（歩兵）

TARGET_COUNT = {
    "front_enemy": 1, "enemy_lowest": 1, "self": 1, "lowest_ally": 1,
    "lane_enemies": 2, "lane_allies": 2, "enemy_back_lane": 1,
    "all_enemies": 6, "all_allies": 6,
}
# 集中の影響を受けるのは**即時ダメージだけ**。継続ダメージ（dot）は前衛が落ちた
# 後も効き続けるので取りこぼしが少ない。dot も集中扱いにすると相関が
# 0.69 → 0.62 へ下がる。ただし12種での判別なので強い証拠ではない。
DAMAGE_KINDS = {"damage"}

# 「通常攻撃 何発ぶんか」を総合値の%へ直す係数。実測の SKILL_ADJUST へ
# 最小二乗で合わせて決める（balance.py skillprice が出す）。
SKILL_PRICE_SCALE = 0.637
SKILL_PRICE_BASE = -8.32


# 発動回数の実測表。**時間だけの計算では合わない。**
#
# 消費・ゲージ獲得速度・初期ゲージの3つは、どれも「1回撃つのに要るティック数」に
# しか効かない。したがって素の計算値（naive）1本へまとめられる。
#
#   naive = (戦闘時間 + 初期ゲージぶんの前倒し) ÷ 1回ぶんの所要ティック
#
#   消費   naive   実測    比
#    50%    2.48   3.45   1.39
#    75%    1.65   2.28   1.38
#   100%    1.24   1.63   1.31
#   125%    0.99   1.24   1.25
#   150%    0.83   0.93   1.12
#   200%    0.62   0.65   1.05
#   300%    0.41   0.50   1.22
#
# **計算より多く撃てる。** ゲージは時間経過だけでなく与被ダメージと撃破からも
# 入るためで、軽い技ほど上乗せが効く（1.39倍→1.05倍）。
#
# 表の値はロスター実測の 1.24回/戦（balance.py check）へ正規化してある。
#
# **注意: この表は一度作り直している。** 最初に測ったときは消費150%で0.50回・
# 200%で0.07回しか出ず、「使える消費は50〜125%しかない」と結論しかけた。
# 原因はゲージ付与の上限を満タン(100)で固定していたことで、消費100超の技だけが
# 撃破ゲージとゲージ引き継ぎを受け取れていなかった（engine.gauge_ceiling）。
# 上限を武将ごとの消費量に合わせたら 0.93回 / 0.65回 になった。
# **可変にしたパラメータの上限が固定のまま残っていないかを必ず見ること。**
NAIVE_TO_CASTS = {0.41: 0.38, 0.62: 0.49, 0.83: 0.71, 0.99: 0.94,
                  1.24: 1.24, 1.65: 1.73, 2.48: 2.62}

# 与被ダメージ・撃破から入るゲージのぶん、1回目の発動は名目より早い。
# **持続効果の「残り時間」もこれで計算する。** 名目のままだと、消費125%以上の
# 技は残り時間が0になり、持続効果の価値がまるごと消える（周瑜の火計が価値0に
# なっていた）。実測の比 1.31〜1.39 の下側を採る。
GAUGE_SOURCE_BOOST = 1.31
GAUGE_TICKS_PER_PCT = GAUGE_FILL_TICKS / 100      # ゲージ1%ぶんのティック数


def casts_for(gauge_cost=100, gauge_rate=100, gauge_start=0):
    """1戦あたりの必殺技の発動回数。消費・獲得速度・初期ゲージから出す。"""
    per_pct = GAUGE_TICKS_PER_PCT * 100 / max(1, gauge_rate)
    period = max(1e-9, gauge_cost * per_pct)      # 1回ぶんの所要ティック
    naive = (BATTLE_TICKS + gauge_start * per_pct) / period
    keys = sorted(NAIVE_TO_CASTS)
    if naive <= keys[0]:
        return NAIVE_TO_CASTS[keys[0]] * naive / keys[0]
    if naive >= keys[-1]:
        return NAIVE_TO_CASTS[keys[-1]] * naive / keys[-1]
    for a, b in zip(keys, keys[1:]):
        if a <= naive <= b:
            f = (naive - a) / (b - a)
            return NAIVE_TO_CASTS[a] + (NAIVE_TO_CASTS[b] - NAIVE_TO_CASTS[a]) * f
    return NAIVE_TO_CASTS[1.24]


def skill_value(skill, gauge_cost=None, gauge_rate=100, gauge_start=0):
    """必殺技1つの価値を「通常攻撃 何発ぶん（1戦あたり）」で返す。

    **消費・獲得速度・初期ゲージは個別に価格を付けない。** 3つとも発動回数に
    しか効かず、その価値は必殺技の中身と掛け算になるためである。加算的な予算
    （ability_premium）に別項目として足すと、強い技を持つ札ほど過小評価になる。
    まとめて発動回数へ畳んでから、技の価格として1つ計上する。
    """
    if gauge_cost is None:
        gauge_cost = skill.get("gauge", 100)
    fill = (GAUGE_FILL_TICKS * gauge_cost / 100 * 100 / max(1, gauge_rate)
            / GAUGE_SOURCE_BOOST)
    fill = max(0.0, fill - gauge_start * GAUGE_TICKS_PER_PCT * 100 / max(1, gauge_rate))
    casts = casts_for(gauge_cost, gauge_rate, gauge_start)
    remain = max(0.0, BATTLE_TICKS - fill)     # 1回目の発動後に残る時間
    n = TARGET_COUNT[skill["target"]]
    per_cast = 0.0
    for e in skill["effects"]:
        kind = e["type"]
        eff_n = effective_targets(n, kind in DAMAGE_KINDS)
        if kind == "damage":
            per_cast += e["power"] / 100 * eff_n
        elif kind == "dot":
            ticks = min(e["duration"], remain) / e["interval"]
            per_cast += e["power"] / 100 * ticks * eff_n
        elif kind == "mod":
            swings = min(e["duration"], remain) / REF_INTERVAL
            per_cast += (abs(e["value"]) / 100 * STAT_WEIGHT.get(e["stat"], 1.0)
                         * swings * eff_n)
        elif kind == "stun":
            per_cast += min(e["duration"], remain) / REF_INTERVAL * eff_n
        elif kind == "gauge":
            per_cast += e["seconds"] * 10 / fill * eff_n
        elif kind == "heal":
            per_cast += e["power"] / 100 * HEAL_WEIGHT * eff_n
    return per_cast * casts


def skill_price(skill, gauge_cost=None, gauge_rate=100, gauge_start=0):
    """必殺技の価格（総合値の%）。正 = 強いので素の能力値を下げる。"""
    attacks = BATTLE_TICKS / REF_INTERVAL
    v = skill_value(skill, gauge_cost, gauge_rate, gauge_start)
    return SKILL_PRICE_SCALE * v / attacks * 100 + SKILL_PRICE_BASE


# 全員同技の総当たり（balance.py skills）では測れない必殺技。そちらからは除外する。
# 価格そのものは balance.py support（1枚混ぜ）で測れるので SKILL_ADJUST に値を持つ。
# urge はゲージ付与だけで攻撃手段を持たないため、全員が urge の編成は敵を倒せない。
# 実測5%は「弱い」ではなく「測れていない」を意味する。総当たりの平均は50%に固定
# されるので、5%の1枚が混ざると残り11種の基準点が54%へ持ち上がり、
# **正しく価格付けされた必殺技まで「まだ強い」と誤判定される**。
# 「1枚だけ通常編成へ混ぜたときの寄与」で測り直すまで、補正は0のまま据え置く（§7.5）。
UNPRICED_SKILLS = {"urge"}

# 固有特性の補正（総合値の%）。「特性なし」との対戦で50%を超えたぶんが価値。
TRAIT_ADJUST = {
    "laststand": 4.20, "legacy": 2.05, "pursuit": 1.70, "avenge": 1.35,
    "rearguard": 1.55, "chain": -0.05,
    # 陣頭。条件を「前衛に置く」へ緩めたことで初めて実測できるようになった。
    # 暫定の2.50%は大幅に安すぎた（1枚だけ前衛に置いて対戦すると94%）。
    "vanguard": 9.20,
    # 対抗能力。効果量を +25% から +10% へ下げたので、価格も 25分の10 へ置き直して
    # から反復した（engine.COUNTER_BONUS）。反復の刻み（ADJUST_STEP=20）は慎重に
    # 倒してあるため、0から反復すると何往復も要る。効果量から見積もれる場合は
    # 初期値を計算で置く。
    "vs_shu": 2.54, "vs_wei": 2.31, "vs_go": 2.23,
    "diehard": 1.85,
    "relief": 1.25,
    "double": 1.10,
    "cheer": 0.75,
    "bloodpath": 0.70,
    "sustain": 0.60,
    "order": 0.55,
    "disrupt": 0.10,
    "banner": 0.00,
    "banner": 0.00,
    "banner": 0.00,
}


# 勢力そのものの有利不利（§6.6）。対抗能力の的になりやすさは能力ではなく
# 所属で決まるので、必殺技や特性と同じように予算へ織り込む。
#
# 群雄は無所属で、**どの勢力の対抗能力の的にもなる**（engine.UNALIGNED）。
# 魏蜀呉が4枚ずつの特効に狙われるのに対し、群は12枚すべてに狙われる。
# その一方的な不利を負の補正＝値引きとして返し、素の能力値を上げる。
# 無補正での不利は実測 -6.7pt、-1.35 で -0.7pt まで詰まる（balance.py factions）。
#
# **勾配1歩の反復で決めてはいけない。範囲を掃く。** 攻撃力は整数なので、
# 値引きを増やすと hp と atk の配分が入れ替わる。総合値は上がっているのに、
# 被ダメージ増加のかかる相手には耐久のほうが効くため成績が落ちる。実測は
# のこぎり波で、-1.35 の 51.7% に対し -1.70 は 45.7% へ落ちる（atk 56→57）。
# -1.35 は谷から離れた平坦部にある。
FACTION_ADJUST = {"gun": -1.35}


def ability_premium(skill, traits, faction=None, gauge_rate=100, gauge_start=0):
    """必殺技・固有特性・勢力の価値を、総合値の倍率へ換算する。

    1.00 より大きい = 効果が強いので素の能力値を下げる
    1.00 より小さい = 効果が弱いので素の能力値を上げる

    `skill` は技の定義そのもの（辞書）を受け取り、価格は式で出す。
    **80枚それぞれに固有の技を持たせるため、キーの表引きはやめた。**
    ひな型ごとの反復では1反復で80回の測定になり、回らない。
    """
    if isinstance(skill, dict):
        pct = skill_price(skill, gauge_rate=gauge_rate, gauge_start=gauge_start)
    else:
        pct = SKILL_ADJUST.get(skill, 0.0)   # ひな型キー（検証ハーネス用）
    for t in traits or ():
        pct += TRAIT_ADJUST.get(t, 0.0)
    pct += FACTION_ADJUST.get(faction, 0.0)
    return 1 + pct / 100


def next_adjust(current, measured):
    """実測勝率から次の反復の補正量を出す。balance.py skills / traits が使う。"""
    return round(current + (measured - 50) / ADJUST_STEP, 2)

TROOP = {
    "inf": {"interval": 12, "range": 100, "evade_base": 0, "label": "歩兵",
            "speed": 8, "dfn": 52, "acc": 88, "crit": 9},
    "cav": {"interval": 11, "range": 100, "evade_base": 5, "label": "騎兵",
            "speed": 12, "dfn": 44, "acc": 90, "crit": 14},
    "arc": {"interval": 13, "range": 450, "evade_base": 0, "label": "弓兵",
            "speed": 7, "dfn": 36, "acc": 91, "crit": 12},
}

# 役割ごとの内訳。耐久と火力の積が1になるようにして、総合値を変えずに個性だけ変える。
ROLE = {
    "tank":    {"hp": 1.35, "atk": 1 / 1.35, "dfn": 1.15, "gauge": 95,  "label": "耐久"},
    "bruiser": {"hp": 1.15, "atk": 1 / 1.15, "dfn": 1.05, "gauge": 100, "label": "均衡"},
    "dps":     {"hp": 1 / 1.25, "atk": 1.25, "dfn": 0.92, "gauge": 100, "label": "火力"},
    "burst":   {"hp": 1 / 1.4, "atk": 1.4, "dfn": 0.85, "gauge": 105, "label": "瞬発"},
    "support": {"hp": 1.1, "atk": 1 / 1.1, "dfn": 0.95, "gauge": 130, "label": "支援"},
}

# --- 必殺技を1枚ずつ書くための補助 -----------------------------------------
#
# 80枚それぞれに固有の技を持たせる。価格は skill_price が式で出すので、
# ここでは**人物像に合う機構と数値**だけを決めればよい。
#
#   S(名前, 対象, [効果...], gauge=消費%)
#   D(威力)          即時ダメージ（通常攻撃の%）
#   DOT(威力, 持続, 間隔)  継続ダメージ
#   HEAL(威力)       回復（発動者の攻撃力の%）
#   MOD(能力, 増減%, 持続)  能力補正
#   STUN(持続)       行動阻害
#   GAUGE(秒数)      ゲージ付与（自然増加の何秒ぶん）


def S(name, target, effects, gauge=100):
    return {"name": name, "target": target, "effects": effects, "gauge": gauge}


def D(power):
    return {"type": "damage", "power": power}


def DOT(power, duration=120, interval=20):
    return {"type": "dot", "power": power, "duration": duration, "interval": interval}


def HEAL(power):
    return {"type": "heal", "power": power}


def MOD(stat, value, duration=120):
    return {"type": "mod", "stat": stat, "value": value, "duration": duration}


def STUN(duration):
    return {"type": "stun", "duration": duration}


def GAUGE(seconds):
    return {"type": "gauge", "seconds": seconds}


# 検証ハーネス用の必殺技ひな型（balance.py skills / support / skillprice が使う）。
# **ロスターの武将はこれを使わない。** 各自が固有の技を持つ。
SKILLS = {
    "strike":  {"target": "front_enemy", "effects": [{"type": "damage", "power": 190}]},
    "sweep":   {"target": "lane_enemies", "effects": [{"type": "damage", "power": 120}]},
    "snipe":   {"target": "enemy_lowest", "effects": [{"type": "damage", "power": 215}]},
    "raid":    {"target": "enemy_back_lane", "effects": [{"type": "damage", "power": 175}]},
    "hold":    {"target": "front_enemy", "effects": [{"type": "stun", "duration": 30}]},
    "roar":    {"target": "lane_enemies",
                "effects": [{"type": "stun", "duration": 20},
                            {"type": "mod", "stat": "atk", "value": -18, "duration": 100}]},
    "guard":   {"target": "lane_allies",
                "effects": [{"type": "mod", "stat": "dfn", "value": 28, "duration": 250}]},
    # 全体効果の量は対象1人あたりで考える。6人へ届くので単体技の1/3が上限（§7.5）。
    "rally":   {"target": "all_allies",
                "effects": [{"type": "mod", "stat": "atk", "value": 6, "duration": 220}]},
    "urge":    {"target": "all_allies", "effects": [{"type": "gauge", "seconds": 8}]},
    "curse":   {"target": "all_enemies",
                "effects": [{"type": "mod", "stat": "acc", "value": -5, "duration": 200}]},
    "burn":    {"target": "lane_enemies",
                "effects": [{"type": "dot", "power": 38, "duration": 200, "interval": 20}]},
    "snare":   {"target": "lane_enemies",
                "effects": [{"type": "mod", "stat": "speed", "value": -35, "duration": 200},
                            {"type": "dot", "power": 24, "duration": 150, "interval": 20}]},
}

# 誘発型の固有特性（§6.6）。戦闘中の出来事で発火し、ゲージは消費しない。
# 「単に強くする」ではなく「別の組み立てを開く」方向で設計する。
# limit は1戦闘あたりの発動回数の上限。
TRIGGERS = {
    # 味方が落ちるほど強くなる。損害を織り込んだ編成を成立させる。
    "legacy":   {"name": "遺志", "trigger": "ally_retreat", "target": "self", "limit": 3,
                 "effects": [{"type": "mod", "stat": "atk", "value": 15, "duration": 200}]},
    # 味方の撤退をゲージへ変える。ゲージ引き継ぎ（§7.4）と重ねて使う。
    "avenge":   {"name": "弔い合戦", "trigger": "ally_retreat", "target": "all_allies", "limit": 2,
                 "effects": [{"type": "gauge", "seconds": 4}]},
    # 味方が落ちた瞬間に自レーンを固める。前衛を薄くする編成を支える。
    "rearguard": {"name": "殿", "trigger": "ally_retreat", "target": "lane_allies", "limit": 2,
                  "effects": [{"type": "mod", "stat": "dfn", "value": 20, "duration": 200}]},
    # 削られてから本領を出す。消耗係数（§6.1）の減衰を打ち消す方向に働く。
    "laststand": {"name": "背水", "trigger": "self_low_hp", "threshold": 40,
                  "target": "self", "limit": 1,
                  "effects": [{"type": "mod", "stat": "atk", "value": 25, "duration": 900}]},
    # 味方の必殺技に合わせて自分のゲージが進む。必殺技の連鎖を組める。
    "chain":    {"name": "呼応", "trigger": "ally_skill", "target": "self", "limit": 5,
                 "effects": [{"type": "gauge", "seconds": 2}]},
    # 倒すほど加速する。突破役の押し込みを伸ばす。
    "pursuit":  {"name": "執念", "trigger": "enemy_retreat", "target": "self", "limit": 3,
                 "effects": [{"type": "mod", "stat": "atk", "value": 12, "duration": 200}]},
    # --- 80枚へ1つずつ配るために追加（v0.5 末）。誘発条件は既存の4種を使い回す。
    # 新しい誘発条件を足すとエンジン側の実装と決定論の検証が増えるため、
    # **条件は増やさず、効果と対象で個性を作る。**
    # 味方の死を部隊全体の士気へ変える。撤退が「損」だけで終わらないようにする。
    "banner":   {"name": "弔旗", "trigger": "ally_retreat", "target": "all_allies", "limit": 2,
                 "effects": [{"type": "mod", "stat": "atk", "value": 6, "duration": 200}]},
    # 削られてから硬くなる。前衛を長持ちさせる方向の背水。
    "diehard":  {"name": "死守", "trigger": "self_low_hp", "threshold": 40,
                 "target": "self", "limit": 1,
                 "effects": [{"type": "mod", "stat": "dfn", "value": 40, "duration": 900}]},
    # 追い詰められて活路を開く。速さで抜ける型を支える。
    "bloodpath": {"name": "血路", "trigger": "self_low_hp", "threshold": 35,
                  "target": "self", "limit": 1,
                  "effects": [{"type": "mod", "stat": "speed", "value": 40, "duration": 900},
                              {"type": "mod", "stat": "acc", "value": 10, "duration": 900}]},
    # 倒すたびに立て直す。長く戦い続ける将の表現。回復（heal）を使う。
    "sustain":  {"name": "継戦", "trigger": "enemy_retreat", "target": "self", "limit": 3,
                 "effects": [{"type": "heal", "power": 90}]},
    # 味方の必殺技に合わせて自レーンが押す。前線の連携。
    "cheer":    {"name": "鼓舞", "trigger": "ally_skill", "target": "lane_allies", "limit": 4,
                 "effects": [{"type": "mod", "stat": "atk", "value": 8, "duration": 120}]},
    # 撃破を部隊全体のゲージへ変える。攻めが次の攻めを呼ぶ。
    "order":    {"name": "号令", "trigger": "enemy_retreat", "target": "all_allies", "limit": 2,
                 "effects": [{"type": "gauge", "seconds": 3}]},
    # 瀕死から身を守る。落ちにくさで貢献する将。
    "double":   {"name": "影武者", "trigger": "self_low_hp", "threshold": 30,
                 "target": "self", "limit": 1,
                 "effects": [{"type": "heal", "power": 220}]},
    # 味方の必殺技に合わせて敵の手元を狂わせる。妨害役の連携。
    "disrupt":  {"name": "連環", "trigger": "ally_skill", "target": "all_enemies", "limit": 3,
                 "effects": [{"type": "mod", "stat": "acc", "value": -4, "duration": 120}]},
    # 味方が落ちた場に踏みとどまり、その場で回復する。後衛を支える。
    "relief":   {"name": "救護", "trigger": "ally_retreat", "target": "lowest_ally", "limit": 2,
                 "effects": [{"type": "heal", "power": 130}]},
}

# (人物, 字号, コスト, 兵種, 役割, 必殺技ひな型, 必殺技名, 特性)
ROSTER = [
    # 人物 / 字号 / コスト / 兵種 / 役割 / 必殺技 / 固有特性(1つ) / ゲージ上書き
    #
    # **必殺技は全員が固有。** 価格は skill_price が式で出すので、ここでは
    # 人物像に合う機構と数値だけを決める（§7.5）。消費ゲージ・獲得速度・
    # 初期ゲージは発動回数を通じて価格へ入る。
    #
    # **固有特性は1人1つ。対抗能力（勢力特効）もこの枠に数える**（§6.6）。
    # 持続は概ね100〜150ティックに収める。1回目の発動が50秒付近で戦闘が62秒
    # なので、それより長くしても捨てるだけである。

    # --- コスト10 ---------------------------------------------------------
    ("曹操", "魏王", 10, "cav", "support",
     S("唯才是挙", "all_allies", [MOD("atk", 9, 150)], gauge=125), ["order"]),
    ("司馬懿", "冢虎", 10, "arc", "support",
     S("堅忍", "all_enemies", [MOD("acc", -7, 150), MOD("speed", -25, 150)]), ["vs_shu"]),
    ("関羽", "漢寿亭侯", 10, "cav", "bruiser",
     S("青龍偃月", "front_enemy", [D(330)], gauge=150), ["vs_go"]),
    ("諸葛亮", "臥龍", 10, "arc", "support",
     S("東南の風", "lane_enemies", [DOT(46, 140), MOD("acc", -6, 140)], gauge=125),
     ["disrupt"]),
    ("張飛", "当陽橋", 10, "inf", "tank",
     S("一喝", "lane_enemies", [STUN(26), MOD("atk", -14, 140)]), ["vanguard"]),
    ("呂布", "飛将", 10, "cav", "burst",
     S("無双乱舞", "lane_enemies", [D(300)], gauge=175), ["laststand"],
     {"gauge_start": 25}),
    ("孫策", "小覇王", 10, "cav", "dps",
     S("江東の疾風", "front_enemy", [D(150), MOD("atk", 10, 120)], gauge=75),
     ["pursuit"], {"gauge_start": 20}),
    ("周瑜", "赤壁", 10, "arc", "dps",
     S("火計", "lane_enemies", [DOT(54, 140)], gauge=125), ["vs_wei"]),

    # --- コスト9 ----------------------------------------------------------
    ("張遼", "逍遥津", 9, "cav", "bruiser",
     S("突撃", "front_enemy", [D(160), MOD("speed", 30, 120)], gauge=75), ["vs_go"],
     {"gauge_start": 20}),
    ("夏侯惇", "独眼", 9, "inf", "tank",
     S("抜矢啖睛", "self", [HEAL(260), MOD("atk", 20, 140)]), ["diehard"]),
    ("馬超", "錦馬超", 9, "cav", "dps",
     S("西涼の驍将", "enemy_back_lane", [D(250)]), ["bloodpath"]),
    ("趙雲", "長坂坡", 9, "cav", "dps",
     S("単騎突入", "enemy_back_lane", [D(215), MOD("dfn", 25, 120)]), ["sustain"]),
    ("劉備", "徳将", 9, "inf", "tank",
     S("仁徳", "all_allies", [HEAL(105)], gauge=125), ["vanguard"]),
    ("董卓", "暴虐", 9, "inf", "tank",
     S("暴威", "lane_enemies", [STUN(22), MOD("dfn", -18, 140)]), ["banner"]),
    ("陸遜", "夷陵", 9, "arc", "support",
     S("連環の計", "lane_enemies", [DOT(34, 140), MOD("speed", -30, 140)]), ["vs_shu"]),
    ("太史慈", "神射", 9, "arc", "dps",
     S("神射", "enemy_lowest", [D(300)], gauge=125), ["laststand"]),

    # --- コスト8 ----------------------------------------------------------
    ("許褚", "虎痴", 8, "inf", "tank",
     S("虎痴", "lane_allies", [MOD("dfn", 34, 150)]), ["vanguard"]),
    ("徐晃", "長駆", 8, "inf", "bruiser",
     S("長駆直入", "front_enemy", [D(200), MOD("speed", 25, 120)]), ["cheer"]),
    ("張郃", "巧変", 8, "inf", "bruiser",
     S("巧変", "front_enemy", [D(150), MOD("dfn", 20, 120)], gauge=75), ["vs_shu"]),
    ("黄忠", "定軍山", 8, "arc", "burst",
     S("百歩穿楊", "enemy_lowest", [D(320)], gauge=150), ["pursuit"]),
    ("姜維", "幼麟", 8, "cav", "bruiser",
     S("九伐中原", "lane_enemies", [D(150)]), ["vs_wei"]),
    ("甘寧", "錦帆賊", 8, "cav", "burst",
     S("百騎劫営", "enemy_back_lane", [D(175)], gauge=75), ["vs_wei"],
     {"gauge_start": 20}),
    ("孫権", "碧眼", 8, "arc", "support",
     S("江東の主", "all_allies", [MOD("atk", 7, 150)], gauge=100), ["order"]),
    ("呂蒙", "白衣", 8, "arc", "support",
     S("白衣渡江", "enemy_back_lane", [D(180), MOD("acc", -10, 130)]), ["disrupt"]),

    # --- コスト7 ----------------------------------------------------------
    ("郭嘉", "鬼才", 7, "arc", "support",
     S("十勝十敗", "all_enemies", [MOD("acc", -6, 140)], gauge=75), ["chain"]),
    ("典韋", "古之悪来", 7, "inf", "tank",
     S("悪来", "lane_enemies", [STUN(24)]), ["vanguard"]),
    ("于禁", "毅重", 7, "inf", "tank",
     S("毅重", "lane_allies", [MOD("dfn", 30, 150)]), ["rearguard"]),
    ("龐統", "鳳雛", 7, "arc", "support",
     S("鳳雛の献策", "all_allies", [GAUGE(6)], gauge=50), ["chain"]),
    ("魏延", "子午", 7, "cav", "dps",
     S("子午の奇襲", "enemy_back_lane", [D(230)], gauge=125), ["bloodpath"]),
    # 消費150%の大技を初期ゲージでも速度でも補っていない唯一の札で、採用率が
    # 3レギュレーションとも下限割れだった。初期ゲージを与えて0.76回→1.03回にする。
    ("顔良", "河北の驍", 7, "cav", "burst",
     S("河北の驍将", "front_enemy", [D(290)], gauge=150), ["laststand"],
     {"gauge_start": 20}),
    ("魯粛", "塌上策", 7, "arc", "support",
     S("塌上の策", "all_allies", [MOD("atk", 5, 140)], gauge=75), ["chain"]),
    ("凌統", "断金", 7, "inf", "dps",
     S("断金の交", "front_enemy", [D(170)], gauge=75), ["avenge"]),

    # --- コスト6 ----------------------------------------------------------
    ("鄧艾", "陰平", 6, "inf", "bruiser",
     S("陰平越え", "enemy_back_lane", [D(200)]), ["vs_shu"]),
    ("荀彧", "王佐", 6, "arc", "support",
     S("王佐の才", "all_allies", [GAUGE(5)], gauge=50), ["chain"]),
    ("夏侯淵", "神速", 6, "cav", "dps",
     S("神速", "front_enemy", [D(115)], gauge=50), ["pursuit"], {"gauge_start": 30}),
    ("関平", "麒麟児", 6, "cav", "dps",
     S("麒麟児", "front_enemy", [D(190)]), ["cheer"]),
    ("法正", "翼侯", 6, "arc", "support",
     S("献策", "lane_allies", [MOD("atk", 16, 140)]), ["disrupt"]),
    ("文醜", "河北の勇", 6, "cav", "bruiser",
     S("河北の勇", "lane_enemies", [D(140)]), ["legacy"]),
    ("黄蓋", "苦肉", 6, "inf", "tank",
     S("苦肉の計", "lane_enemies", [DOT(40, 130)]), ["vanguard"]),
    ("程普", "老練", 6, "inf", "tank",
     S("老練", "lane_allies", [MOD("dfn", 28, 150)]), ["rearguard"]),

    # --- コスト5 ----------------------------------------------------------
    ("鍾会", "士季", 5, "cav", "bruiser",
     S("士季の計", "lane_enemies", [D(130)]), ["disrupt"]),
    ("賈詡", "毒士", 5, "arc", "support",
     S("離間の計", "all_enemies", [MOD("atk", -6, 140)]), ["chain"]),
    ("楽進", "先登", 5, "inf", "bruiser",
     S("先登", "front_enemy", [STUN(30)]), ["vanguard"]),
    ("李典", "慎重", 5, "arc", "dps",
     S("斉射", "lane_enemies", [D(125)]), ["rearguard"]),
    ("馬謖", "幼常", 5, "arc", "bruiser",
     S("街亭", "lane_enemies", [DOT(26, 130), MOD("speed", -25, 130)]), ["legacy"]),
    ("王平", "無当", 5, "inf", "bruiser",
     S("無当飛軍", "front_enemy", [STUN(26)]), ["vs_wei"]),
    ("韓当", "老弓", 5, "arc", "dps",
     S("連射", "lane_enemies", [D(95)], gauge=75), ["sustain"]),
    ("朱然", "江陵", 5, "inf", "tank",
     S("江陵の守", "lane_allies", [MOD("dfn", 26, 150)]), ["rearguard"]),

    # --- コスト4 ----------------------------------------------------------
    ("郭淮", "雍涼", 4, "cav", "bruiser",
     S("雍涼の備", "front_enemy", [D(175)]), ["diehard"]),
    ("荀攸", "謀主", 4, "arc", "dps",
     S("謀主", "lane_enemies", [DOT(24, 130)]), ["chain"]),
    ("曹仁", "堅守", 4, "inf", "tank",
     S("鉄壁", "lane_allies", [MOD("dfn", 24, 150)]), ["vs_go"]),
    ("高順", "陥陣", 4, "inf", "dps",
     S("陥陣営", "front_enemy", [D(185)]), ["vanguard"]),
    ("陳宮", "公台", 4, "arc", "support",
     S("公台の策", "all_enemies", [MOD("acc", -5, 140)]), ["legacy"]),
    ("張任", "落鳳", 4, "cav", "dps",
     S("落鳳坡", "enemy_lowest", [D(215)]), ["pursuit"]),
    ("徐盛", "疑城", 4, "arc", "bruiser",
     S("疑城の計", "all_enemies", [MOD("atk", -5, 140)]), ["disrupt"]),
    ("周泰", "身代", 4, "inf", "tank",
     S("身代わり", "lowest_ally", [HEAL(230)]), ["double"]),

    # --- コスト3 ----------------------------------------------------------
    ("満寵", "剛毅", 3, "arc", "dps",
     S("剛毅", "enemy_lowest", [D(200)]), ["vs_go"]),
    ("曹洪", "救主", 3, "cav", "bruiser",
     S("救主", "lowest_ally", [HEAL(200)]), ["relief"]),
    ("馬岱", "追撃", 3, "cav", "dps",
     S("追撃", "enemy_lowest", [D(190)]), ["pursuit"]),
    ("陳到", "白毦", 3, "inf", "tank",
     S("白毦兵", "lane_allies", [MOD("dfn", 22, 150)]), ["vanguard"]),
    ("廖化", "老将", 3, "cav", "dps",
     S("殿軍", "front_enemy", [STUN(24)]), ["legacy"]),
    ("厳顔", "老当", 3, "inf", "bruiser",
     S("老当益壮", "lane_enemies", [D(115)]), ["laststand"]),
    ("傅僉", "守将", 3, "inf", "tank",
     S("堅守", "lane_allies", [MOD("dfn", 20, 150)]), ["diehard"]),
    ("諸葛瑾", "子瑜", 3, "arc", "support",
     S("子瑜の弁", "all_allies", [MOD("atk", 4, 140)], gauge=75), ["banner"]),

    # --- コスト2 ----------------------------------------------------------
    ("楽綝", "揚州", 2, "cav", "dps",
     S("揚州の驍", "front_enemy", [D(170)]), ["cheer"]),
    ("李厳", "正方", 2, "cav", "bruiser",
     S("督運", "all_allies", [GAUGE(4)], gauge=50), ["chain"]),
    ("張嶷", "越巂", 2, "inf", "tank",
     S("越巂の鎮", "lane_allies", [MOD("dfn", 18, 150)]), ["rearguard"]),
    ("呉懿", "外戚", 2, "cav", "bruiser",
     S("堅陣", "front_enemy", [STUN(22)]), ["banner"]),
    ("董襲", "断纜", 2, "inf", "dps",
     S("断纜", "front_enemy", [D(160)]), ["laststand"]),
    ("潘璋", "急襲", 2, "cav", "dps",
     S("急襲", "enemy_back_lane", [D(150)]), ["pursuit"]),
    ("張昭", "文淵", 2, "arc", "support",
     S("江東の柱石", "all_allies", [MOD("atk", 4, 140)]), ["relief"]),
    ("丁奉", "雪中", 2, "arc", "burst",
     S("雪中奮短兵", "front_enemy", [D(130)], gauge=75), ["bloodpath"]),

    # --- コスト1 ----------------------------------------------------------
    ("田豫", "国譲", 1, "cav", "bruiser",
     S("国譲の守", "front_enemy", [STUN(20)]), ["diehard"]),
    ("糜竺", "子仲", 1, "inf", "bruiser",
     S("糧道", "all_allies", [GAUGE(3)], gauge=50), ["relief"]),
    ("簡雍", "憲和", 1, "inf", "support",
     S("弁舌", "all_enemies", [MOD("acc", -4, 140)]), ["chain"]),
    ("樊建", "伝令", 1, "arc", "support",
     S("伝令", "all_allies", [GAUGE(3)], gauge=50), ["chain"]),
    ("宗預", "使者", 1, "arc", "support",
     S("結盟", "all_allies", [MOD("atk", 3, 140)]), ["banner"]),
    ("孫乾", "従事", 1, "inf", "support",
     S("従事", "lane_allies", [MOD("dfn", 16, 150)]), ["relief"]),
    ("呂範", "子衡", 1, "arc", "bruiser",
     S("子衡の備", "front_enemy", [STUN(18)]), ["rearguard"]),
    ("全琮", "護軍", 1, "cav", "bruiser",
     S("護軍", "front_enemy", [D(150)]), ["cheer"]),
]

# 勢力（§4.4）。対抗能力の軸に使う。
#
# **自己強化ではなく対抗能力を軸にする。** 自己強化（AとBを組むと両方強い）は
# 正のフィードバックで、使うほど正しさが強化されるため編成が固定化する。
# 対抗能力（魏に強い）は負のフィードバックで、**自分の成功が自分の価値を下げる**。
# 魏特効が流行れば魏が減り、魏が減れば魏特効が腐り、また魏が戻る。原理的に収束しない。
#
# 群雄は3人しかいないので対抗の標的にはしない。魏19・蜀21・呉17 の3勢力を軸とする。
FACTION = {
    "wei": ["曹操", "司馬懿", "夏侯惇", "張遼", "徐晃", "張郃", "許褚", "郭嘉", "于禁", "典韋", "荀彧", "夏侯淵", "鄧艾", "楽進",
            "李典", "賈詡", "鍾会", "曹仁", "郭淮", "荀攸", "曹洪", "満寵", "楽綝", "田豫"],
    "shu": ["諸葛亮", "関羽", "張飛", "趙雲", "馬超", "劉備", "黄忠", "姜維", "龐統", "魏延", "法正", "関平", "王平", "馬謖",
            "陳到", "傅僉", "廖化", "馬岱", "厳顔", "呉懿", "張嶷", "李厳", "樊建", "宗預", "孫乾", "簡雍", "糜竺"],
    "go": ["周瑜", "孫策", "陸遜", "太史慈", "甘寧", "呂蒙", "孫権", "凌統", "魯粛", "程普", "黄蓋", "韓当", "朱然", "周泰",
            "徐盛", "諸葛瑾", "潘璋", "丁奉", "董襲", "張昭", "全琮", "呂範"],
    "gun": ["呂布", "董卓", "顔良", "文醜", "陳宮", "高順", "張任"],
}
FACTION_LABEL = {"wei": "魏", "shu": "蜀", "go": "呉", "gun": "群"}
FACTION_OF = {p: f for f, ps in FACTION.items() for p in ps}

# 検証用の合成カードに付く勢力。どの対抗能力の的にもならず、予算の補正も受けない。
#
# **既定を "gun" にしてはいけない。** balance.py の合成カードは人物名が
# FACTION_OF に無いので既定値がそのまま入る。既定が群だと、群に付けた
# 値引き（FACTION_ADJUST）が検証用カードにも乗る。しかも攻撃力は整数丸めで
# 動かず値引きが全額 hp へ回るため、耐久寄りの相性だけが持ち上がる。
# 実際、三すくみの 歩兵→騎兵 が 67% → 74% へ動いた。
NEUTRAL_FACTION = "none"

# 逆に、実在の武将が勢力表から漏れて中立になると、その札だけ対抗能力が
# 効かなくなる。静かに起きるので明示的に落とす。
_missing = [e[0] for e in ROSTER if e[0] not in FACTION_OF]
if _missing:
    raise ValueError(f"勢力が未設定の武将: {_missing}")

# 対抗能力。指定勢力への与ダメージが増える常在型（§6.6）。
# 効果量は engine.COUNTER_BONUS。標的は魏・蜀・呉のみ。
COUNTERS = {"vs_wei": "wei", "vs_shu": "shu", "vs_go": "go"}

# 常在型のうち、エンジン側に処理を持つ組み込み特性（§6.6）。
# **陣頭は「前衛に置いた」だけで働く。総大将であることは要求しない。**
# 総大将かつ前衛を条件にしていたときは発動しなかった。総大将は落ちたら即敗北
# なので最も安全な枠に置くのが最適で、攻略探索の4世代とも総大将はコスト1の
# 糜竺だった。陣頭を持つ11人は2.5%ぶんの能力値を払って何も得ていなかった。
PASSIVES = {"vanguard": "陣頭"}

ROMAJI = {
    "呂布": "ryofu", "諸葛亮": "shokatsuryo", "関羽": "kanu", "曹操": "sosou",
    "周瑜": "shuyu", "趙雲": "chouun", "張飛": "chohi", "司馬懿": "shibai",
    "黄忠": "kochu", "孫策": "sonsaku", "陸遜": "rikuson", "馬超": "bachou",
    "夏侯惇": "kakoton", "太史慈": "taishiji", "甘寧": "kannei", "張遼": "choryo",
    "龐統": "hoto", "徐晃": "jokou", "魏延": "gien", "姜維": "kyoi",
    "張郃": "chokaku", "呂蒙": "ryomo", "郭嘉": "kakuka", "于禁": "ukin",
    "荀彧": "juniku", "楽進": "gakushin", "李典": "riten", "凌統": "ryoto",
    "程普": "teiho2", "黄蓋": "kogai", "賈詡": "kaku", "法正": "hosei",
    "夏侯淵": "kakoen", "曹仁": "sojin", "韓当": "kanto", "朱然": "shuzen",
    "王平": "ohei", "郭淮": "kakuwai", "荀攸": "junyu", "陳宮": "chinkyu",
    "高順": "kojun", "陳到": "chinto", "傅僉": "fusen", "廖化": "ryoka",
    "馬岱": "batai", "周泰": "shutai", "徐盛": "josei", "曹洪": "sokou",
    "満寵": "manchou", "潘璋": "hansho", "丁奉": "teiho", "呉懿": "goi",
    "張嶷": "chogyoku", "李厳": "rigen", "楽綝": "gakushin2", "董襲": "toshu",
    "樊建": "hanken", "宗預": "soyo", "全琮": "zensou", "孫乾": "sonken",
    "劉備": "ryubi",
    "董卓": "toutaku",
    "許褚": "kyocho",
    "孫権": "sonken2",
    "典韋": "tenni",
    "魯粛": "roshuku",
    "顔良": "ganryo",
    "鄧艾": "tougai",
    "関平": "kanpei",
    "文醜": "bunshu",
    "馬謖": "bashoku",
    "鍾会": "shoukai",
    "張任": "chojin",
    "諸葛瑾": "shokatsukin",
    "厳顔": "gengan",
    "張昭": "chosho",
    "簡雍": "kanyo",
    "糜竺": "bijiku",
    "呂範": "ryohan",
    "田豫": "denyo",
}


def value(cost):
    """コストから総合値を出す。枠の基礎価値 + コスト比例分。"""
    ratio = SLOT_VALUE + (1 - SLOT_VALUE) * cost / NORM_COST
    return TARGET_SCORE * ratio


def effective_score(hp, atk, dfn, interval, acc, crit, evade=0):
    """総合値 = 実効耐久 × 実効火力。

        実効耐久 = 兵力 × (100 + 防御力) / 100 × 回避による被弾減
        実効火力 = 攻撃力 × 命中率 × クリティカル期待値 / 攻撃間隔

    **強さに効く数値をひとつでも落とすと、その値が高い兵種が予算外の優位を持つ。**
    実際に2回とも歩兵→騎兵の勝率が1桁になった。

    - 命中率・クリティカル率を落としていたとき: 騎兵（命中90・クリ14）と
      歩兵（命中88・クリ9）で実効火力に約8%の差。
    - 回避を落としていたとき: 騎兵は回避8、歩兵は回避2で、被弾率に約7%の差。

    回避は兵種標準値と移動速度から算出する（§6.3）。engine.accuracy() と同じ式。
    """
    effective_hp = hp * (100 + dfn) / 100 * REF_ACC / max(1, REF_ACC - evade)
    dps = atk * (acc / 100) * (1 + crit / 100 * (CRIT_MULT - 1)) * 100 / interval
    return effective_hp * dps / 1000


def evade_of(troop):
    """回避補正。engine.accuracy() の evade と同じ計算。"""
    t = TROOP[troop]
    return t["evade_base"] + t["speed"] // 4


def tier_of(cost):
    return "low" if cost <= 3 else ("mid" if cost <= 6 else "high")


def solve_hp(target, atk, dfn, troop):
    """総合値が target になる兵力を解く。

    攻撃力は整数で持つため、1点の刻みが総合値の約1.8%（コスト5で atk 56）にあたる。
    兵力まで50刻みで丸めると刻みが約3%になり、**それより細かい価格付けが
    表現できなくなる**。実際、必殺技の補正 +0.18〜+2.52% の7種が同一の
    hp 3700 / atk 56 に潰れ、価格付けがまるごと効いていなかった。
    総合値は兵力について線形なので、丸めた攻撃力に対して兵力を解けば刻みは
    0.3% まで細かくなる。役割の内訳のずれは1%未満に収まる。
    """
    t = TROOP[troop]
    unit = effective_score(1, atk, dfn, t["interval"], t["acc"], t["crit"],
                           evade_of(troop))
    return max(200, round(target / unit / 10) * 10)


def build_card(entry):
    # 9番目は任意の上書き辞書（gauge / gauge_rate / gauge_start など）。
    # 武将ごとの個性をゲージの側で表すために使う。**能力値は上書きさせない。**
    person, epithet, cost, troop, role, skill, traits = entry[:7]
    over = entry[7] if len(entry) > 7 else {}
    rate = over.get("gauge_rate", ROLE[role]["gauge"])
    start = over.get("gauge_start", 0)
    t, r = TROOP[troop], ROLE[role]
    faction = FACTION_OF.get(person, NEUTRAL_FACTION)
    dfn = max(10, round(t["dfn"] * r["dfn"]))
    # 総合値が value(cost) になるよう倍率を解く
    hp1, atk1 = 1000 * r["hp"], 20 * r["atk"]
    score1 = effective_score(hp1, atk1, dfn, t["interval"], t["acc"], t["crit"],
                             evade_of(troop))
    target = (value(cost) / BEHAVIOR_PREMIUM[troop]
              / ability_premium(skill, traits, faction, rate, start))
    f = math.sqrt(target / score1)
    atk = max(5, round(atk1 * f))          # 端数は兵力側で吸収する（solve_hp）
    hp = solve_hp(target, atk, dfn, troop)
    card = {
        "id": f"{ROMAJI[person]}_{cost}",
        "person": person,
        "faction": faction,
        "name": f"{person}〔{epithet}〕",
        "tier": tier_of(cost),
        "cost": cost,
        "troop": troop,
        "role": role,
        "hp": hp,
        "atk": atk,
        "dfn": dfn,
        "speed": t["speed"],
        "gauge_rate": r["gauge"],
        "acc": t["acc"],
        "crit": t["crit"],
    }
    # 効果量を連動させるための実力比（コスト5を100とする百分率・§7.5）。
    # 能力補正の%・行動阻害のティック数・ゲージ付与の量にこれを掛ける。
    # **コストではなく実際の総合値を使う。** 価格付けで能力値を削られたカードは
    # ここも下がるので、値上げがそのまま効果量へ跳ね返り、価格付けが機能する。
    achieved = effective_score(hp, atk, dfn, t["interval"], t["acc"], t["crit"],
                               evade_of(troop))
    card["power"] = max(20, round(achieved / TARGET_SCORE * 100))
    # ゲージ系の上書き。**能力値ではなく発動回数を動かす**ので、価格は
    # 必殺技の側へ織り込む（skill_price が発動回数として受け取る）。
    for key in ("gauge_rate", "gauge_start"):
        if key in over:
            card[key] = over[key]
    if traits:
        # 文字列は常在型の組み込み特性、TRIGGERS のキーは誘発型に展開する（§6.6）
        card["traits"] = [dict(TRIGGERS[t]) if t in TRIGGERS else t for t in traits]
    card["skill"] = dict(skill) if isinstance(skill, dict) else {
        "name": skill, **SKILLS[skill]}
    return card


def generate():
    cards = [build_card(e) for e in ROSTER]
    ids = [c["id"] for c in cards]
    assert len(set(ids)) == len(ids), "武将カードIDが重複している"
    persons = [c["person"] for c in cards]
    assert len(set(persons)) == len(persons), "人物IDが重複している"
    return {
        "data_version": "v0.5-roster-60",
        "note": ("sim/roster.py が生成する。手で編集しないこと。"
                 "能力値はコストから機械的に導出している（§4.6）。"),
        "troop_defaults": {k: {kk: vv for kk, vv in v.items()
                               if kk in ("interval", "range", "evade_base", "label")}
                           for k, v in TROOP.items()},
        "cards": cards,
    }


def report(data):
    intervals = {t: v["interval"] for t, v in data["troop_defaults"].items()}
    print(f"武将 {len(data['cards'])}人")
    from collections import Counter
    print("  コスト分布:", dict(sorted(Counter(c["cost"] for c in data["cards"]).items())))
    print("  兵種分布:", dict(Counter(c["troop"] for c in data["cards"])))
    print("  役割分布:", dict(Counter(c["role"] for c in data["cards"])))
    print("  特性持ち:", sum(1 for c in data["cards"] if c.get("traits")))
    print("\n  コスト別の総合値/コスト（枠の基礎価値があるため低コストほど高くなるのが正しい）")
    by_cost = {}
    for c in data["cards"]:
        s = round(effective_score(c["hp"], c["atk"], c["dfn"],
                                  intervals[c["troop"]], c["acc"], c["crit"],
                                  evade_of(c["troop"])))
        by_cost.setdefault(c["cost"], []).append(s)
    for cost in sorted(by_cost):
        v = by_cost[cost]
        print(f"    コスト{cost:>2}: 総合値 {sum(v)//len(v):>5} "
              f"(幅 {min(v)}-{max(v)}) / コスト比 {sum(v)//len(v)//cost:>4}")


if __name__ == "__main__":
    data = generate()
    report(data)
    if "--write" in sys.argv:
        path = os.path.join(os.path.dirname(__file__), "cards.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\nwrote {path}")
