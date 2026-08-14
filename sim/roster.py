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
BEHAVIOR_PREMIUM = {"inf": 1.00, "cav": 0.98, "arc": 1.05}

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
# 勾配が小さく、実測で総合値1%あたり 1.5〜1.9pt。振動を避けて 2.5 に倒す。
SUPPORT_STEP = 2.5

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
    "burn": 10.48, "curse": 6.89, "roar": 5.49, "rally": 4.27, "guard": 2.22,
    "snipe": 1.30, "sweep": 0.65, "snare": -0.15, "raid": -0.36,
    "strike": -0.47, "urge": -4.40, "hold": -5.62,
}

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
    "laststand": 3.35, "legacy": 3.10, "pursuit": 2.85, "avenge": 2.05,
    "rearguard": 2.00, "chain": 0.50,
    # vanguard は総大将かつ前衛のときだけ働く条件付きで、他と同じ土俵で測れない。
    # §4.2 の測定（前衛配置で +16〜+48pt）の中間から暫定で 2.5% 相当とする。
    "vanguard": 2.50,
    # 対抗能力。+25%のダメージが敵のおよそ1/3に乗るので、火力+8%相当から始める。
    # 反復の刻み（ADJUST_STEP=20）は慎重に倒してあるため、0から反復すると
    # 何往復も要る。効果量から見積もれる場合は初期値を計算で置く。
    "vs_shu": 8.65, "vs_wei": 7.90, "vs_go": 7.75,
}


def ability_premium(skill_key, traits):
    """必殺技と固有特性の価値を、総合値の倍率へ換算する。

    1.00 より大きい = 効果が強いので素の能力値を下げる
    1.00 より小さい = 効果が弱いので素の能力値を上げる
    """
    pct = SKILL_ADJUST.get(skill_key, 0.0)
    for t in traits or ():
        pct += TRAIT_ADJUST.get(t, 0.0)
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

# 必殺技のひな型。役割と兵種から選ぶ。名前はカードごとに差し替える。
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
}

# (人物, 字号, コスト, 兵種, 役割, 必殺技ひな型, 必殺技名, 特性)
ROSTER = [
    # --- コスト10 ---
    ("呂布", "飛将", 10, "cav", "burst", "strike", "無双乱舞", ["vanguard"]),
    ("諸葛亮", "臥龍", 10, "arc", "support", "curse", "東南の風", []),
    # --- コスト9 ---
    ("関羽", "漢寿亭侯", 9, "cav", "bruiser", "sweep", "青龍偃月", []),
    ("曹操", "魏王", 9, "cav", "support", "rally", "唯才是挙", ["vanguard"]),
    ("周瑜", "赤壁", 9, "arc", "dps", "burn", "火計", []),
    ("趙雲", "長坂坡", 9, "cav", "dps", "raid", "単騎突入", []),
    # --- コスト8 ---
    ("張飛", "当陽橋", 8, "inf", "tank", "roar", "一喝", ["vanguard"]),
    ("司馬懿", "冢虎", 8, "arc", "support", "snare", "堅忍", ["vs_shu"]),
    ("黄忠", "定軍山", 8, "arc", "burst", "snipe", "百歩穿楊", []),
    ("孫策", "小覇王", 8, "cav", "dps", "strike", "江東の疾風", ["vanguard"]),
    ("陸遜", "夷陵", 8, "arc", "support", "burn", "連環の計", ["vs_shu"]),
    ("馬超", "錦馬超", 8, "cav", "dps", "raid", "西涼の驍将", ["pursuit"]),
    # --- コスト7 ---
    ("夏侯惇", "独眼", 7, "inf", "tank", "guard", "抜矢啖睛", ["vanguard"]),
    ("太史慈", "神射", 7, "arc", "dps", "snipe", "神射", ["laststand"]),
    ("甘寧", "錦帆賊", 7, "cav", "burst", "raid", "百騎劫営", ["vanguard", "vs_wei"]),
    ("張遼", "逍遥津", 7, "cav", "bruiser", "strike", "突撃", ["laststand", "vs_go"]),
    ("龐統", "鳳雛", 7, "arc", "support", "urge", "鳳雛の献策", []),
    ("徐晃", "長駆", 7, "inf", "bruiser", "strike", "長駆直入", []),
    # --- コスト6 ---
    ("魏延", "子午", 6, "cav", "dps", "snipe", "子午の奇襲", ["pursuit"]),
    ("姜維", "幼麟", 6, "cav", "bruiser", "sweep", "九伐中原", ["legacy", "vs_wei"]),
    ("張郃", "巧変", 6, "inf", "bruiser", "strike", "巧変", []),
    ("呂蒙", "白衣", 6, "arc", "dps", "raid", "白衣渡江", []),
    ("郭嘉", "鬼才", 6, "arc", "support", "curse", "十勝十敗", ["chain"]),
    ("于禁", "毅重", 6, "inf", "tank", "guard", "毅重", []),
    ("荀彧", "王佐", 6, "arc", "support", "rally", "王佐の才", ["chain"]),
    # --- コスト5 ---
    ("楽進", "先登", 5, "inf", "bruiser", "hold", "先登", []),
    ("李典", "慎重", 5, "arc", "dps", "sweep", "斉射", []),
    ("凌統", "断金", 5, "inf", "dps", "strike", "断金の交", ["avenge"]),
    ("程普", "老練", 5, "inf", "tank", "guard", "老練", []),
    ("黄蓋", "苦肉", 5, "inf", "tank", "roar", "苦肉の計", ["vanguard"]),
    ("賈詡", "毒士", 5, "arc", "support", "curse", "離間の計", []),
    ("法正", "翼侯", 5, "arc", "support", "urge", "献策", ["chain"]),
    ("夏侯淵", "神速", 5, "cav", "dps", "raid", "神速", []),
    # --- コスト4 ---
    ("曹仁", "堅守", 4, "inf", "tank", "guard", "鉄壁", []),
    ("韓当", "老弓", 4, "arc", "dps", "sweep", "連射", []),
    ("朱然", "江陵", 4, "inf", "tank", "hold", "江陵の守", ["rearguard"]),
    ("王平", "無当", 4, "inf", "bruiser", "hold", "無当飛軍", ["legacy", "vs_wei"]),
    ("郭淮", "雍涼", 4, "cav", "bruiser", "strike", "雍涼の備", []),
    ("荀攸", "謀主", 4, "arc", "support", "snare", "謀主", []),
    ("陳宮", "公台", 4, "arc", "support", "curse", "公台の策", []),
    ("高順", "陥陣", 4, "inf", "dps", "strike", "陥陣営", ["vanguard"]),
    # --- コスト3 ---
    ("陳到", "白毦", 3, "inf", "tank", "guard", "白毦兵", ["vanguard"]),
    ("傅僉", "守将", 3, "inf", "tank", "guard", "堅守", []),
    ("廖化", "老将", 3, "inf", "bruiser", "hold", "殿軍", ["legacy"]),
    ("馬岱", "追撃", 3, "cav", "dps", "snipe", "追撃", []),
    ("周泰", "身代", 3, "inf", "tank", "guard", "身代わり", ["avenge"]),
    ("徐盛", "疑城", 3, "arc", "bruiser", "sweep", "疑城の計", []),
    ("曹洪", "救主", 3, "cav", "bruiser", "strike", "救主", []),
    ("満寵", "剛毅", 3, "arc", "dps", "snipe", "剛毅", ["vs_go"]),
    # --- コスト2 ---
    ("潘璋", "急襲", 2, "cav", "dps", "raid", "急襲", ["pursuit"]),
    ("丁奉", "雪中", 2, "arc", "burst", "snipe", "雪中奮短兵", []),
    ("呉懿", "外戚", 2, "inf", "bruiser", "hold", "堅陣", []),
    ("張嶷", "越巂", 2, "inf", "tank", "guard", "越巂の鎮", ["rearguard"]),
    ("李厳", "正方", 2, "arc", "support", "urge", "督運", []),
    ("楽綝", "揚州", 2, "cav", "dps", "strike", "揚州の驍", []),
    ("董襲", "断纜", 2, "inf", "dps", "strike", "断纜", ["laststand"]),
    # --- コスト1 ---
    ("樊建", "伝令", 1, "arc", "support", "urge", "伝令", ["chain"]),
    ("宗預", "使者", 1, "arc", "support", "rally", "結盟", []),
    ("全琮", "護軍", 1, "cav", "bruiser", "strike", "護軍", []),
    ("孫乾", "従事", 1, "inf", "support", "guard", "従事", []),
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
    "wei": ["曹操", "司馬懿", "夏侯惇", "張遼", "徐晃", "張郃", "郭嘉", "于禁", "荀彧",
            "楽進", "李典", "賈詡", "夏侯淵", "曹仁", "郭淮", "荀攸", "曹洪", "満寵", "楽綝"],
    "shu": ["諸葛亮", "関羽", "趙雲", "張飛", "黄忠", "馬超", "龐統", "魏延", "姜維",
            "法正", "王平", "陳到", "傅僉", "廖化", "馬岱", "呉懿", "張嶷", "李厳",
            "樊建", "宗預", "孫乾"],
    "go":  ["周瑜", "孫策", "陸遜", "太史慈", "甘寧", "呂蒙", "凌統", "程普", "黄蓋",
            "韓当", "朱然", "周泰", "徐盛", "潘璋", "丁奉", "董襲", "全琮"],
    "gun": ["呂布", "陳宮", "高順"],
}
FACTION_LABEL = {"wei": "魏", "shu": "蜀", "go": "呉", "gun": "群"}
FACTION_OF = {p: f for f, ps in FACTION.items() for p in ps}

# 対抗能力。指定勢力への与ダメージが増える常在型（§6.6）。
# 効果量は engine.COUNTER_BONUS。標的は魏・蜀・呉のみ。
COUNTERS = {"vs_wei": "wei", "vs_shu": "shu", "vs_go": "go"}

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
    person, epithet, cost, troop, role, skill_key, skill_name, traits = entry
    t, r = TROOP[troop], ROLE[role]
    dfn = max(10, round(t["dfn"] * r["dfn"]))
    # 総合値が value(cost) になるよう倍率を解く
    hp1, atk1 = 1000 * r["hp"], 20 * r["atk"]
    score1 = effective_score(hp1, atk1, dfn, t["interval"], t["acc"], t["crit"],
                             evade_of(troop))
    target = (value(cost) / BEHAVIOR_PREMIUM[troop]
              / ability_premium(skill_key, traits))
    f = math.sqrt(target / score1)
    atk = max(5, round(atk1 * f))          # 端数は兵力側で吸収する（solve_hp）
    hp = solve_hp(target, atk, dfn, troop)
    card = {
        "id": f"{ROMAJI[person]}_{cost}",
        "person": person,
        "faction": FACTION_OF.get(person, "gun"),
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
    if traits:
        # 文字列は常在型の組み込み特性、TRIGGERS のキーは誘発型に展開する（§6.6）
        card["traits"] = [dict(TRIGGERS[t]) if t in TRIGGERS else t for t in traits]
    card["skill"] = {"name": skill_name, **SKILLS[skill_key]}
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
