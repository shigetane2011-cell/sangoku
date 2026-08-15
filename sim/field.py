#!/usr/bin/env python3
"""レーンを廃した2次元の最小モデル。**位置・移動・射程だけで組む。**

usage:
  python3 sim/field.py geometry   1枚10,000人から寸法を導く
  python3 sim/field.py additive   総コストを固定して配分を動かす（0.00%になるか）
  python3 sim/field.py troops     兵種の三すくみが出るか
  python3 sim/field.py forms      陣形（広く浅い／狭く深い）が極端でない差になるか

----------------------------------------------------------------------------
なぜ作り直すか
----------------------------------------------------------------------------

engine.py はレーンという離散の仕切りの上に20以上の特別規則を積んでいる。
今日の測定で、その多くが**何もしていない**か**幾何がすでに同じ仕事をしている**
ことが分かった。

  ・前衛の盾（reachable）を外しても数字が1つも動かない
    → 後衛は盾ではなく**距離**で守られていた
  ・レーン間の攻撃を開いても、支援移動を切っても、ほぼ動かない
  ・コストが部隊単位で通分できない原因は総大将（単一の生死点）だった

そして寸法が壊れていた。**弓の射程450が、接敵時の盤面の全奥行き400より長い。**
戦線が接した瞬間に全員が射程内に入るので、位置に意味が無くなっていた。

----------------------------------------------------------------------------
1枚10,000人から寸法を導く
----------------------------------------------------------------------------

正面1m/人、列間隔1.5m、20列とすると 1枚は **正面500m × 奥行30m**。
第二線を120m後方に置くと、接敵時の距離はこうなる。

  自軍第二線 → 敵前列    G + D      = 150m
  自軍第二線 → 敵第二線  2G + 2D    = 300m

弓の有効射程を180mにすると `150 ≤ 180 < 300`。
**弓は敵の前列を撃てるが、敵の奥までは届かない。** これが要件だった。

盤面は正面1500m（3枚横並び）× 奥行300m で、**横が5倍長い**。
レーンは横方向を3点に潰していたので、実座標にすれば要らなくなる。

----------------------------------------------------------------------------
消えた手置きの定数
----------------------------------------------------------------------------

  LANE_CAP / 陣形のレーン割り      → 実座標
  DETOUR_PER_BLOCKER / MAX_TICKS  → ZOC ＋ 翼を回る実移動（距離は正面幅から出る）
  FLANKED_TAKEN（孤立の罰）        → 裏へ出た位置そのものが罰になる
  reachable（前衛の盾）            → 距離
  CROSS_LANE_PENALTY（斜射）       → 射程が届くかどうか
  後衛の射程延長                   → 第二線は予備。前列が崩れたら前へ出る

残るのは「1枚10,000人」「正面1m/人」「列間隔1.5m」「弓の射程180m」
「兵種ごとの速度」だけである。

----------------------------------------------------------------------------
いまの状態: **まだ数字を読んではいけない**
----------------------------------------------------------------------------

零点（同じ編成どうし）は 0.00% で正しい。しかし**時間刻みを変えると答えが
変わる**ので、それ以外の数字は読めない。

  dt=2.0   9/1 -22.37%   dt=1.0  9/1 -22.53%
  dt=0.5   9/1  +6.53%   dt=0.25 9/1 +13.00%     ← 符号が反転する

原因はたぶん step_move の横ずれで、拘束圏に入るかどうかの二値判定を
一歩ごとに行うため、刻みを細かくすると別の経路を選ぶ。連続な形
（斥力場のようなもの）に直す必要がある。

**「第一線へ寄せる +36%」だけは dt によらず安定している**ので、
これは本物の効果の可能性が高い。他は保留。

----------------------------------------------------------------------------
この過程で計器の左右非対称を2つ潰した（どちらも零点で見つかった）
----------------------------------------------------------------------------

1. **移動を側ごとに順番へ処理していた。** 先に動く側は相手の古い位置を見て
   動き、後の側は新しい位置を見て動く。零点が +19.67% になっていた。
   位置を控えてから全員の一歩を決め、まとめて適用する形に直した。
2. **標的の同点処理に敵の絶対座標 e.y を使っていた。** 青は敵の第一線を、
   朱は敵の第二線を選ぶ。横に殴れるようにした途端、零点が +21.00% になった。
   自分から見た相対量（|e.y - u.y| など）で書くように直した。

**どちらも「同じ編成どうしは 0.00% のはず」を毎回測っていたから見つかった。**
新しいモデルを作ったら、まず零点を測ること。
"""
import math
import sys

# --- 1枚10,000人から導く寸法（m） -----------------------------------------
MEN = 10000
FRONT_PER_MAN = 1.0
RANK_SPACING = 1.5
RANKS = 20
UNIT_W = MEN / RANKS * FRONT_PER_MAN      # 正面 500m
UNIT_D = RANKS * RANK_SPACING             # 奥行 30m
LINE_GAP = 120.0                          # 第一線と第二線の間隔
SIDE_GAP = 40.0                           # 横に並べるときの隙間
START_GAP = 600.0                         # 開戦時の両軍前列の距離

MELEE = 10.0                              # 近接の届く距離（接触）
BOW = 180.0                               # 弓の有効射程
SPEED = {"inf": 1.0, "cav": 2.5, "arc": 0.8}      # m/秒
INTERVAL = {"inf": 1.2, "cav": 1.1, "arc": 1.3}   # 攻撃間隔（秒）
RANGE = {"inf": MELEE, "cav": MELEE, "arc": BOW}
TROOP_ADV = {"inf": "cav", "cav": "arc", "arc": "inf"}
ADV_BONUS = 1.03

ROUT = 0.20                               # 残存兵力率がこれを割ったら潰走
MAX_SEC = 1800


class Unit:
    __slots__ = ("x", "y", "hp", "max_hp", "atk", "cost", "troop", "side",
                 "idx", "target", "next_at", "locked")

    def __init__(self, x, y, cost, troop, side, idx):
        self.x, self.y = x, y
        self.hp = self.max_hp = 1000.0 * cost
        self.atk = 10.0 * cost
        self.cost, self.troop, self.side = cost, troop, side
        self.idx = idx            # 側の中での通し番号。同点処理を左右対称にする
        self.target = None
        self.next_at = 0.0
        self.locked = False


def gap(a, b):
    """2つの方陣の縁と縁の距離（m）。**点ではなく面として扱う。**

    10,000人の方陣は正面500m・奥行30m あるので、点で扱うと横位置の意味が消える。
    """
    dx = abs(a.x - b.x) - UNIT_W
    dy = abs(a.y - b.y) - UNIT_D
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


def reach(u):
    return RANGE[u.troop]


def deploy(costs, troops, side, front=3, width_scale=1.0):
    """6枚を2線に置く。front は第一線の枚数。width_scale は陣形の横の広がり。

    横位置は中央そろえ。width_scale を上げると横に広がり（鶴翼）、
    下げると密集する（魚鱗）。**陣形はレーンの割り振りではなく実座標になる。**
    """
    units = []
    lines = [costs[:front], costs[front:]]
    tls = [troops[:front], troops[front:]]
    n = 0
    for li, (row, trow) in enumerate(zip(lines, tls)):
        if not row:
            continue
        pitch = (UNIT_W + SIDE_GAP) * width_scale
        span = pitch * (len(row) - 1)
        y = (0.0 if side == 0 else START_GAP) + (
            -li * LINE_GAP if side == 0 else li * LINE_GAP)
        for i, (c, t) in enumerate(zip(row, trow)):
            units.append(Unit(-span / 2 + pitch * i, y, c, t, side, n))
            n += 1
    return units


def pick(u, foes):
    """**最も近い敵を狙い、倒すまで変えない。**

    射程内で最も柔らかい敵を選ぶ（全知の集中砲火）ようにすると、二乗則が
    最大に効いて位置の意味が薄れる。今日の最小モデルで、集中砲火にすると
    少数精鋭が3割勝ち越し、均等割りにすると人数が完全に等価になった。
    **配り方が二乗則を作っている。** 幾何に決めさせるほうが筋が通る。
    """
    if u.target is not None and u.target.hp > 0:
        return u.target
    live = [e for e in foes if e.hp > 0]
    if not live:
        return None
    # **同点の処理は自分から見た相対量で書く。** 敵の絶対座標 e.y で並べると、
    # 青は敵の第一線を、朱は敵の第二線を選ぶことになり左右対称でなくなる。
    # 横に殴れるようにした途端、同じ編成どうしの零点が +21% になった。
    u.target = min(live, key=lambda e: (round(gap(u, e), 6),
                                        round(abs(e.y - u.y), 6),
                                        round(abs(e.x - u.x), 6), e.idx))
    return u.target


def step_move(u, foes, dt):
    """標的へ寄る。**敵の拘束圏（ZOC）へは踏み込まない。**

    拘束圏に入ると動けなくなるので、標的以外の敵の圏内を通る一歩は選ばない。
    避けるときは横へずれる。**これだけで翼を回る動きが出る。** 迂回という
    規則も、その距離の定数も要らない。回り込みの長さは敵の正面幅から出る。
    """
    tgt = pick(u, foes)
    if tgt is None:
        return
    d = gap(u, tgt)
    if d <= reach(u):
        return
    sp = SPEED[u.troop] * dt
    dx, dy = tgt.x - u.x, tgt.y - u.y
    n = math.hypot(dx, dy) or 1.0
    nx, ny = u.x + dx / n * sp, u.y + dy / n * sp
    probe = Unit(nx, ny, u.cost, u.troop, u.side, u.idx)
    blocker = [e for e in foes
               if e.hp > 0 and e is not tgt and gap(probe, e) <= MELEE]
    if blocker:
        # 拘束圏を避けて横へ回る。近い妨害から遠ざかる向きへ
        b = min(blocker, key=lambda e: gap(u, e))
        side = 1.0 if u.x >= b.x else -1.0
        u.x += side * sp
        return
    u.x, u.y = nx, ny


def rate_of(side_units):
    """残存兵力率。**負の兵力を混ぜないこと。**

    倒れた札の hp は負まで行くので、そのまま足すと残存率が実際より低く出る。
    しかも過剰ダメージの量は側で違うため、**同じ編成どうしでも 50% にならない**。
    """
    left = sum(max(u.hp, 0.0) for u in side_units)
    return left / max(1e-9, sum(u.max_hp for u in side_units))


def resolve(a_units, b_units, dt=1.0):
    """1戦を決定論的に解く。戻り値は (A残存率, B残存率, 決着秒, 理由)。

    **移動は同時に解く。** 側ごとに順番へ処理すると、先に動いた側は相手の
    古い位置を見て動き、後の側は新しい位置を見て動くことになる。これだけで
    同じ編成どうしの零点が +19.67% になっていた（0.00% でなければならない）。
    位置を控えてから全員の一歩を決め、まとめて適用する。
    """
    units = a_units + b_units
    sides = (a_units, b_units)
    t = 0.0
    while t < MAX_SEC:
        t += dt
        live = [u for u in units if u.hp > 0]
        if rate_of(a_units) < ROUT or rate_of(b_units) < ROUT:
            break
        snap = {id(u): (u.x, u.y) for u in live}
        for u in live:
            u.locked = any(e.hp > 0 and gap(u, e) <= MELEE
                           for e in sides[1 - u.side])
        moves = {}
        for u in live:
            if u.locked:
                continue
            step_move(u, sides[1 - u.side], dt)
            moves[id(u)] = (u.x, u.y)
            u.x, u.y = snap[id(u)]          # 控えた位置へ戻し、あとでまとめて適用
        for u in live:
            if id(u) in moves:
                u.x, u.y = moves[id(u)]
        dealt = {}
        for u in live:
            if t < u.next_at:
                continue
            tgt = pick(u, sides[1 - u.side])
            if tgt is None or gap(u, tgt) > reach(u):
                continue
            u.next_at = t + INTERVAL[u.troop]
            dmg = u.atk
            if TROOP_ADV[u.troop] == tgt.troop:
                dmg *= ADV_BONUS
            dealt[id(tgt)] = dealt.get(id(tgt), 0.0) + dmg
        for u in live:
            u.hp -= dealt.get(id(u), 0.0)
    rates = [rate_of(a_units), rate_of(b_units)]
    reason = "潰走" if min(rates) < ROUT else "時間切れ"
    return rates[0], rates[1], t, reason


def margin(costs_a, troops_a, costs_b, troops_b, **kw):
    """残存兵力率の差（％）。**0 なら完全に等価。**"""
    a = deploy(costs_a, troops_a, 0, **kw)
    b = deploy(costs_b, troops_b, 1, **kw)
    ra, rb, _t, _r = resolve(a, b)
    return (ra - rb) * 100


# --- 各コマンド -----------------------------------------------------------

EVEN = [5] * 6
INF6 = ["inf"] * 6


def cmd_geometry():
    print("=== 1枚10,000人から導く寸法 ===")
    print(f"  正面{FRONT_PER_MAN}m/人・列間隔{RANK_SPACING}m・{RANKS}列")
    print(f"    1枚      正面 {UNIT_W:.0f}m × 奥行 {UNIT_D:.0f}m")
    print(f"    第二線   {LINE_GAP:.0f}m 後方")
    print(f"    軍の正面 {(UNIT_W + SIDE_GAP) * 2 + UNIT_W:.0f}m（3枚横並び）")
    print()
    print("  接敵時の距離")
    print(f"    自軍第二線 → 敵前列    {LINE_GAP + UNIT_D:.0f}m")
    print(f"    自軍第二線 → 敵第二線  {2 * (LINE_GAP + UNIT_D):.0f}m")
    print(f"    弓の射程              {BOW:.0f}m")
    ok = LINE_GAP + UNIT_D <= BOW < 2 * (LINE_GAP + UNIT_D)
    print(f"    → 敵の前列には届くが奥までは届かない: {'成立' if ok else '**破れる**'}")
    print()
    print(f"  盤面は 正面{(UNIT_W + SIDE_GAP) * 2 + UNIT_W:.0f}m × "
          f"奥行{2 * (LINE_GAP + UNIT_D):.0f}m で、"
          f"横が {((UNIT_W + SIDE_GAP) * 2 + UNIT_W) / (2 * (LINE_GAP + UNIT_D)):.1f}倍 長い")
    print("  レーンは横方向を3点に潰していた。実座標にすれば要らない。")


def cmd_additive():
    print("=== コストの加法性（総コスト30固定・配分だけ動かす）===")
    print("  攻撃力・兵力はコストに厳密比例。相手は常に 5/5/5/5/5/5。")
    print("  **通分できていれば全部 0.00%。** 乱数なし・決定論。\n")
    cases = (("均等 5/5/5/5/5/5", EVEN),
             ("第一線の2枚 7/3", [7, 3, 5, 5, 5, 5]),
             ("第一線の2枚 9/1", [9, 1, 5, 5, 5, 5]),
             ("第一線と第二線 9/1", [9, 5, 5, 1, 5, 5]),
             ("第一線へ寄せる 9/9/9/1/1/1", [9, 9, 9, 1, 1, 1]),
             ("第二線へ寄せる 1/1/1/9/9/9", [1, 1, 1, 9, 9, 9]),
             ("端へ寄せる 10/5/1/10/5/1", [10, 5, 1, 10, 5, 1]))
    for label, costs in cases:
        m = margin(costs, INF6, EVEN, INF6)
        flag = "" if abs(m) < 2.0 else "  ← 壊れている"
        print(f"  {label:<26} 残存兵力差 {m:+7.2f}%{flag}")


def cmd_troops():
    print("=== 兵種の三すくみ ===")
    print("  特別規則なし。速い＝翼を取れる、射程長い＝先に削れる、だけで出るか。")
    print("  兵種有利のダメージ補正は +3%（engine と同じ）。\n")
    label = {"inf": "歩兵", "cav": "騎兵", "arc": "弓兵"}
    for a, b in (("inf", "cav"), ("cav", "arc"), ("arc", "inf")):
        m = margin(EVEN, [a] * 6, EVEN, [b] * 6)
        print(f"  {label[a]}→{label[b]}  残存兵力差 {m:+7.2f}%")
    print()
    print("  混成（第一線に近接・第二線に弓）どうし")
    mix = ["inf", "cav", "inf", "arc", "arc", "arc"]
    print(f"    同型どうし  残存兵力差 {margin(EVEN, mix, EVEN, mix):+7.2f}%")


def cmd_forms():
    print("=== 陣形（実座標での広がり）===")
    print("  横の広がりと第一線の枚数を変える。**極端な相性が出ないかを見る。**\n")
    forms = {"鶴翼（広く浅い）": dict(front=4, width_scale=1.4),
             "標準": dict(front=3, width_scale=1.0),
             "魚鱗（狭く深い）": dict(front=2, width_scale=0.6),
             "方円（密集）": dict(front=3, width_scale=0.5)}
    keys = list(forms)
    print("       " + "".join(f"{k[:6]:>12}" for k in keys))
    for ka in keys:
        row = []
        for kb in keys:
            a = deploy(EVEN, INF6, 0, **forms[ka])
            b = deploy(EVEN, INF6, 1, **forms[kb])
            ra, rb, _t, _r = resolve(a, b)
            row.append(f"{(ra - rb) * 100:+11.1f}%")
        print(f"  {ka[:5]:<6}" + "".join(row))
    print()
    print("  対角は同型どうしなので 0.0% になるのが正しい（計器の確認）。")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "geometry"
    table = {"geometry": cmd_geometry, "additive": cmd_additive,
             "troops": cmd_troops, "forms": cmd_forms}
    if cmd not in table:
        sys.exit(__doc__)
    table[cmd]()
