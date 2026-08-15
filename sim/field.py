# -*- coding: utf-8 -*-
"""
sim/field.py -- 位置ベースの最小モデル（レーン廃止版）

================================================================================
 このファイルの立ち位置
================================================================================
sim/engine.py はレーン（3本の平行通路）を前提とした旧実装で、置き換える対象。
本ファイルはレーンを廃し、**位置・移動・射程だけ**で戦闘を組む。迂回・孤立の罰・
斜射・前衛の盾・後衛の射程延長は特別規則として書かず、実座標と ZOC から出す。

寸法は「1枚 10,000人」という設定から導いている。

    1枚         正面 500m × 奥行 30m        （10,000人 / 15,000m² ≒ 1.5m²/人）
    前衛の中心  |y| = 30m                   （両軍の前衛間 60m）
    後衛の中心  |y| = 150m                  （前衛の 120m 後方）
    弓の射程    180m = 150 + 30             （後衛から敵前衛にちょうど届く）
    盤面        正面 1580m × 奥行 300m      （500×3 = 1500 に左右 40m の余白）
    前衛の定位置 x = -500 / 0 / +500

敵軍は自軍を **180度回転** させて置く（§5.1「自軍の左翼は敵軍の右翼と正対する」）。
反転（鏡映）ではない。零点測定はこの回転対称性の検算でもある。

================================================================================
 現状（このファイルで測れているもの）
================================================================================
- 零点（同じ編成どうし）= 0.00%。dt = 1.0 / 0.5 / 0.25 / 0.125 で一致。
  真の鏡像では残存兵力率が**ビット単位で一致**する（ra - rb = 0.0）。
- 零点には感度がある（`field.py selftest` の陽性対照で確認済み。下記）。
- 戦列は広がらない。開始 1000.0m → 終了 1000.0m。dt を 8倍振っても、
  時間を 8倍に延ばしても変化 0.0m。

================================================================================
 「戦列が横へ散る」問題と、その決着（本ファイルの主題）
================================================================================
■ 症状（引き継ぎ時）
   斥力（REPULSE）で押し合うため、開始 x=±500m が終了 x=±637m まで広がる。
   斥力を弱めると散らないが、時間刻みを変えると答えの符号が反転する
   （7/3 の配分が +1.3% ↔ -18.8%）。

■ 原因（実測。`field.py spread` で再現できる）
   **2つの症状は同じ1つの原因だった。斥力ではなく、それを陽的オイラーで
   積分していることが原因である。**

   旧実装を再現して、収束後（ダメージを切って t=720）の戦列幅を測った。

       斥力      dt=1.0    dt=0.5   dt=0.25  dt=0.125
       12        1066.7    1033.2    1016.6    1008.3
        4        1022.1    1011.1    1005.5    1002.8
        1        1005.5    1002.8    1001.4    1000.7

   広がりの量（1000 からの超過）は

       超過幅 ≒ 5.53 × 斥力 × dt          （上の12点すべてに合う）

   **斥力にも dt にも正比例する。** つまり広がりは平衡位置ではなく、1ステップ
   ぶんの押し出し（力 × dt）がそのまま残ったものである。dt → 0 で消える。
   これは物理ではなく積分誤差である。

   ここから両方の症状が出る。

   (a) 横へ散る … 力 × dt ぶんだけ毎回外へ出て、そこで釣り合ってしまう。
   (b) 符号が反転する … **斥力を半分にすることと dt を半分にすることが同じ操作**
       になっている（積 斥力×dt しか効かないため）。だから「斥力を弱めて散らなく
       する」と「dt を変える」を独立に扱えない。斥力を弱めると幾何のずれは
       小さくなるが消えはせず、測りたい差（配分による数%）と同じ大きさまで
       下がったところで、dt を変えるたびに符号が入れ替わる。

   **弱める方向に答えはなかった。** 2つのつまみは同じ1つのつまみだった。

■ 両立する形（採用した解）
   斥力という**力**を捨て、2つの構造で置き換えた。どちらも距離だけで決まり、
   分岐を含まず、陽的積分を使わない。

   1. 味方どうしの間隔は **定位置（station）** が持つ。
      隊列とはそもそも「各札の持ち場が決まっていること」であって、押し合った
      結果ではない。味方間に力を一切かけない。**力がなければ広がりようがない。**
      強さの調整ではなく、自由度そのものの削除である。

   2. 敵との接触は **不動点を持つ接近則** が持つ。
      速度を距離だけの連続関数にして、常微分方程式を**閉じた形で解く**。

          dd/dt = -v · (1 - exp(-d/L))                    … d は残り距離

          解: u = d/L に対し   u' = log1p( expm1(u) · exp(-v·dt/L) )

      - d が大きいところでは速度 v の等速（u' ≒ u - v·dt/L）
      - d → 0 では指数的に減速し、**d = 0 を超えない**
      - 厳密解なので **dt を変えても同じ点に着く**（積分誤差が存在しない）
      - `if 射程内なら止まる` のような分岐が要らない ⇒ dt 依存が入らない

■ 効かなかった案（測ったうえで捨てたもの。蒸し返さないため記録する）
   - 斥力の強さを下げる … 上の表のとおり dt を下げるのと同じ操作。分離できない
   - 盤面の縁で位置を切り詰める … 縁が分岐になり、触れた瞬間から dt 依存が出る
   - dt を小さくして誤魔化す … 広がりは減るが、斥力を弱めたのと区別がつかない

================================================================================
 計器の非対称・計器の罠（すべて実測で見つけたもの。再発防止のため残す）
================================================================================
 1. **中心間距離で測るとレーンが復活する。**
    1枚は 500m 幅の方陣であって点ではない。中心間で測ると横に並んだ札は 500m
    離れていることになり、真正面の敵としか戦えない。廃したはずのレーンが距離
    計算の中で生き返り、コストの加算性が **9/1 で -50%** まで壊れた。縁と縁の
    距離（`box_gap`）に変えると戦列が1本の線になり、同じ条件で **-6.67%** に
    収まった。斜射も特別規則なしで出るようになった。

 2. **中央の札（x=0）の迂回方向が両軍とも +x になっていた。**
    大域座標の符号で決めていたため、180度回転の対称性が中央の1枚だけで壊れて
    いた。零点が 騎兵×6 で 0.4420 / 0.4416 とずれて発覚。自軍フレーム
    （x × side）で符号を取ると回転で自動的に対称になる。

 3. **§8.2 の「差1%未満は引き分け」を測定に使うと計器が死ぬ。**
    近い編成どうしが全部引き分けになり、勝率が一律 50.00% に張り付く。零点も
    7/3 の配分差も 0.00 に見えるが、これは対称だからではなく band に飲まれて
    いるだけである。**陽性対照（A側だけ攻撃力 +0.001%）でも 0.00 のままになり、
    計器が死んでいることが分かった。** 引き分け帯は見せ方の規則であって測定の
    規則ではない。測定では 1e-9 まで下げる。真の鏡像は ra - rb がビット単位で
    0.0 なので、下げても零点は 0.00 のまま出る。

 4. **零点は不公平しか見つけられない。**
    敵軍を §5.1 に反して鏡映で配置しても零点は 0.00 のままである。鏡映でも
    「両軍が対等」であることは変わらないため。壊れるのは対面関係であって公平性
    ではない。零点で見えるもの／見えないものは `field.py selftest` に列挙した。

 5. **前衛か後衛かを現在の y で分類すると、広がりではなく分類の変化を測る。**
    前進した札が途中で別の分類へ移るため。開戦時の枠で固定する。

================================================================================
 残課題
================================================================================
 - 必殺技・固有特性・状態効果はまだ載っていない（最小モデルのため）。
 - 潰走閾値 20% は §8.2 の総大将即敗北を置き換えたもの。値そのものは暫定。
 - 陣形は (前衛枚数, 正面幅) の2値でしか表していない。鶴翼/魚鱗などの名前付き
   陣形へ写すのは次の段階。
"""

from __future__ import annotations

import argparse
import itertools
import math
from dataclasses import dataclass, replace
from typing import Dict, List, Sequence, Tuple

# ============================================================================
# 寸法（すべて m 単位。10,000人/枚 から導出）
# ============================================================================

CARD_MEN = 10_000.0          # 1枚の兵力
CARD_W = 500.0               # 1枚の正面（基準）
CARD_D = 30.0                # 1枚の奥行（基準）
CARD_AREA = CARD_W * CARD_D  # 面積は陣形を変えても保存する

FRONT_Y = 30.0               # 前衛の中心 |y|
REAR_Y = 150.0               # 後衛の中心 |y|
BOARD_W = 1580.0
BOARD_D = 300.0

BOW_RANGE = REAR_Y + FRONT_Y  # 180m
BASE_FRONTAGE = 1500.0        # 500m × 3

# ============================================================================
# 兵種（§5.2 / §5.3 の兵種標準値。単位を m/s と m として読む）
# ============================================================================

INF, CAV, ARC = "inf", "cav", "arc"
TYPES = (INF, CAV, ARC)
TYPE_JP = {INF: "歩兵", CAV: "騎兵", ARC: "弓兵"}

SPEED = {INF: 8.0, CAV: 12.0, ARC: 7.0}      # §5.2 移動速度 8 / 12 / 7
INTERVAL = {INF: 1.2, CAV: 1.1, ARC: 1.3}    # §6.3 攻撃間隔
# 射程はすべて**縁から縁**で測る（box_gap）。弓の 150m は、標準の奥行 30m では
# 中心間 180m に等しい（後衛 |y|=150 から敵前衛 |y|=30 へちょうど届く）。
BOW_RANGE_EDGE = REAR_Y - FRONT_Y - CARD_D  # = 90 ... 中心間 180 に相当するのは下
BOW_RANGE_EDGE = (REAR_Y + FRONT_Y) - CARD_D  # = 150
RANGE = {INF: 0.0, CAV: 0.0, ARC: BOW_RANGE_EDGE}
ACT_COEF = {INF: 1.00, CAV: 0.90, ARC: 1.12}  # §6.1 行動面の係数

# 三すくみ（§5.3）。有利側にのみボーナス。
BEATS = {INF: CAV, CAV: ARC, ARC: INF}

# ============================================================================
# 自由なパラメータ（結論に使う前に必ず振って符号の安定を確かめること）
# ============================================================================

TYPE_BONUS = 0.04       # §5.3 有利補正（+4%）
LETHALITY = 0.024       # ダメージ係数
FOCUS = 0.0             # 射撃の集中度。§5.2「残兵力の少ない方を狙う」を連続化した
                        # もの。0 なら射程内へ均等配分（ランチェスター線形則）、
                        # 大きいほど弱った敵へ集まる（二乗則）。コストの加算性は
                        # この値で決まる（詳細はファイル冒頭）。
BASE_ATK = 100.0
BASE_DEF = 50.0
BASE_COST = 5.0
SPLIT_EXP = 1.0         # コストを兵力側へ割る指数（上の Unit.__init__ を参照）

APPROACH_L = 20.0       # 接近則の減速長さ L
RANGE_SOFT = 20.0       # 射程の縁をなまらせる幅（分岐を作らないため）
ZOC_R = 220.0           # ZOC の届く距離
ZOC_STR = 1.6           # ZOC の制動の強さ
FLANK_MARGIN = 30.0     # 迂回時に敵前衛の外縁からどれだけ外を回るか
LEGACY_REACH = 1.6      # 【旧実装の再現用】斥力の届く距離（札幅の倍数）

ROUT_RATIO = 0.20       # 残存兵力率がこれを割ったら潰走（総大将の置き換え）
# 【重要・計器】§8.2 の「残存兵力率の差が1%未満なら引き分け」は**見せ方の規則**で
# あって、測定に使ってはいけない。1% を測定へ持ち込むと、近い編成どうしが全部
# 引き分けになり、勝率が一律 50.00% に張り付く。実際これで
#   ・零点が 0.00 に見える（対称だからではなく、band に飲まれているから）
#   ・7/3 の配分差が 0.00 に見える
# の2つが同時に起きた。陽性対照（A側だけ攻撃力 +0.1%）でも 0.00 のままになり、
# 計器が死んでいることが分かった。真の鏡像は ra-rb が**ビット単位で 0.0** なので、
# band を 0 近くまで下げても零点は 0.00 のまま出る。
DRAW_BAND = 1e-9        # 測定用。完全同値のときだけ引き分け
T_MAX = 90.0            # §8.2 上限90秒


# ============================================================================
# 幾何のヘルパ
# ============================================================================

def smooth_gate(d: float, r: float, soft: float) -> float:
    """距離 d が r 以内なら 1、r+soft で 0 になる C1 連続な窓。

    分岐（if d < r）を使うとその境界で dt 依存が生じる。両端で微分が 0 になる
    smoothstep を使い、**距離だけで決まる連続な形**にしている。
    """
    if d <= r:
        return 1.0
    t = (d - r) / soft
    if t >= 1.0:
        return 0.0
    return 1.0 - t * t * (3.0 - 2.0 * t)


def box_gap(a: "Unit", b: "Unit") -> float:
    """2つの方陣の**縁と縁**の距離。中心間距離ではない。

    1枚は 500m 幅の方陣であって点ではない。中心間で測ると、横に並んだ札は
    500m 離れていることになり、隣の敵と接触できない。すると各札は真正面の
    敵としか戦わなくなり、**廃したはずのレーンが距離計算の中で復活する**
    （実測でコストの加算性が +46.67% まで壊れた）。縁で測ると戦列が1本の線に
    なり、斜射（隣の敵への攻撃）が特別規則なしで出る。
    """
    dx = abs(a.x - b.x) - (a.width + b.width) / 2.0
    dy = abs(a.y - b.y) - (a.depth + b.depth) / 2.0
    dx = max(dx, 0.0)
    dy = max(dy, 0.0)
    return math.hypot(dx, dy)


def approach_exact(d: float, v: float, dt: float, L: float = APPROACH_L) -> float:
    """dd/dt = -v(1 - exp(-d/L)) の厳密解。残り距離 d を dt だけ進めた値を返す。

    これが「時間刻みを変えても答えが変わらない」ことの根拠。数値積分ではなく
    解析解なので、dt = 1.0 でも dt = 0.125 でも同じ点に着く。d < 0 にならない。
    """
    if d <= 0.0 or v <= 0.0 or dt <= 0.0:
        return max(d, 0.0)
    u = d / L
    kdt = v * dt / L
    if u > 100.0:
        # expm1 の桁溢れ回避。この領域では等速（誤差 e^-100 以下）。
        return max(L * (u - kdt), 0.0)
    return L * math.log1p(math.expm1(u) * math.exp(-kdt))


# ============================================================================
# 編成の記述
# ============================================================================

# 役割は総合値を変えず**内訳だけ**を変える（§4.6）。耐久寄りは兵力へ、火力寄りは
# 攻撃力へ寄せる。§5.3 の「三すくみは役割を混ぜた編成で測る」を満たすために要る。
TANK, BAL, DPS = "tank", "bal", "dps"
ROLE_JP = {TANK: "耐久", BAL: "均衡", DPS: "火力"}
ROLE_MEN = {TANK: 1.4, BAL: 1.0, DPS: 1.0 / 1.4}
MIXED_ROLES = (TANK, TANK, BAL, BAL, DPS, DPS)


@dataclass(frozen=True)
class Card:
    cost: float
    typ: str
    role: str = BAL

    def label(self) -> str:
        return f"{TYPE_JP[self.typ]}{ROLE_JP[self.role]}{self.cost:g}"


@dataclass(frozen=True)
class Formation:
    """陣形は (前衛の枚数, 正面幅) だけで表す。

    面積 500×30 を保存するので、正面を広げれば奥行が薄くなる。
    「広く浅い」「狭く深い」がこの2値から自然に出る。
    """
    n_front: int
    frontage: float = BASE_FRONTAGE

    def card_width(self) -> float:
        return self.frontage / max(self.n_front, 1)

    def card_depth(self) -> float:
        return CARD_AREA / self.card_width()


FORM_STANDARD = Formation(n_front=3, frontage=BASE_FRONTAGE)
FORM_WIDE = Formation(n_front=4, frontage=BASE_FRONTAGE)      # 広く浅い
FORM_DEEP = Formation(n_front=2, frontage=BASE_FRONTAGE * 0.7)  # 狭く深い


@dataclass(frozen=True)
class Army:
    cards: Tuple[Card, ...]
    form: Formation = FORM_STANDARD

    def total_cost(self) -> float:
        return sum(c.cost for c in self.cards)


# ============================================================================
# 戦闘中の実体
# ============================================================================

class Unit:
    __slots__ = (
        "side", "typ", "cost", "men", "men0", "atk", "dfn", "interval",
        "speed", "rng", "width", "depth", "x", "y", "path", "seg_len",
        "total_len", "progress", "is_front", "x0",
    )

    def __init__(self, side: int, card: Card, form: Formation,
                 station: Tuple[float, float], is_front: bool):
        self.side = side
        self.is_front = is_front
        self.typ = card.typ
        self.cost = card.cost

        # 総合値 ∝ コスト。実効耐久・実効火力へ均等に割り、行動面の係数で割り戻す。
        # （§6.1「強さに効く数値をひとつでも式から落とすと予算外の優位が出る」）
        s = (card.cost / BASE_COST) / ACT_COEF[card.typ]
        s = max(s, 1e-9)
        # 総合値 = 実効耐久 × 実効火力 ∝ コスト（§4.6）を保ったまま、コストを
        # 兵力側と攻撃側へどう割るかを SPLIT_EXP で選ぶ。
        #   SPLIT_EXP = 0.5 … 兵力 ∝ √c、攻撃 ∝ √c   （軍全体の兵力が Σ√c で劣加法）
        #   SPLIT_EXP = 1.0 … 兵力 ∝ c、  攻撃 ∝ 一定 （軍全体の兵力も火力も Σc で加法）
        # どちらでも 1枚の総合値は c に比例するが、**軍としての合計**が加法になるのは
        # 1.0 のときだけ。コストの加算性はここで決まる。
        rm = ROLE_MEN[card.role]
        self.men0 = CARD_MEN * (s ** SPLIT_EXP) * rm
        self.men = self.men0
        self.atk = BASE_ATK * (s ** (1.0 - SPLIT_EXP)) / rm
        self.dfn = BASE_DEF
        self.interval = INTERVAL[card.typ]
        self.speed = SPEED[card.typ]
        self.rng = RANGE[card.typ]
        self.width = form.card_width()
        self.depth = form.card_depth()

        self.x, self.y = station
        self.x0 = station[0]
        self.path: List[Tuple[float, float]] = [station]
        self.seg_len: List[float] = []
        self.total_len = 0.0
        self.progress = 0.0

    # -- 経路 -------------------------------------------------------------
    def set_path(self, pts: Sequence[Tuple[float, float]]) -> None:
        """開戦時に一度だけ決める。戦闘中は変えない。

        毎ティックで行き先を選び直すと、その選択が dt ごとに入れ替わって
        時間刻み依存になる。運動計画は開戦時に固定する。
        """
        self.path = [(self.x, self.y)] + list(pts)
        self.seg_len = []
        for a, b in zip(self.path, self.path[1:]):
            self.seg_len.append(math.hypot(b[0] - a[0], b[1] - a[1]))
        self.total_len = sum(self.seg_len)
        self.progress = 0.0

    def pos_at(self, s: float) -> Tuple[float, float]:
        s = min(max(s, 0.0), self.total_len)
        for (a, b), ln in zip(zip(self.path, self.path[1:]), self.seg_len):
            if ln <= 1e-12:
                continue
            if s <= ln:
                f = s / ln
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            s -= ln
        return self.path[-1]

    def ratio(self) -> float:
        return self.men / self.men0 if self.men0 > 0 else 0.0

    def effective_hp(self) -> float:
        return self.men * (100.0 + self.dfn) / 100.0


# ============================================================================
# 配置
# ============================================================================

def _stations(form: Formation, n_cards: int, side: int
              ) -> List[Tuple[float, float]]:
    """side = +1 を基準に置き、side = -1 は 180度回転（x,y ともに符号反転）。"""
    n_front = min(form.n_front, n_cards)
    n_rear = n_cards - n_front
    out: List[Tuple[float, float]] = []

    def spread(n: int, span: float) -> List[float]:
        if n <= 0:
            return []
        if n == 1:
            return [0.0]
        step = span / n
        return [-span / 2.0 + step * (i + 0.5) for i in range(n)]

    for x in spread(n_front, form.frontage):
        out.append((x, -FRONT_Y * side))
    for x in spread(n_rear, form.frontage * 0.7):
        out.append((x, -REAR_Y * side))

    if side < 0:
        out = [(-x, -y) for (x, y) in out]
    return out


def _softness(units: Sequence[Unit]) -> float:
    if not units:
        return 0.0
    return 1.0 / max(sum(u.effective_hp() for u in units) / len(units), 1e-9)


def _plan_paths(mine: List[Unit], foe: List[Unit], form: Formation,
                foe_form: Formation) -> None:
    """開戦時に各札の経路を決める。

    - 弓兵      : 動かない（初期配置で敵前衛に届いている）
    - 歩兵      : 正面の敵前衛へ接触距離まで前進
    - 騎兵      : 敵前衛の硬さと敵後衛の柔らかさの比 w で、正面突撃と迂回を**連続に**
                  内挿する。w=0 なら真っ直ぐ、w=1 なら外縁を回って後衛へ。
                  「割に合うときだけ回り込む」を if で書くと崖ができるので、
                  比で連続に混ぜる（§13 の「崖の上でないか確かめる」への対応）。
    """
    foe_front = [u for u in foe if u.is_front]
    foe_rear = [u for u in foe if not u.is_front]
    if not foe_front:
        foe_front = foe
    if not foe_rear:
        foe_rear = foe_front

    s_front = _softness(foe_front)
    s_rear = _softness(foe_rear)
    w = s_rear / (s_front + s_rear) if (s_front + s_rear) > 0 else 0.5
    # w は 0.5 を中心に動く。0.5 を「差がない」に写して 0..1 へ伸ばす。
    w = min(max((w - 0.5) * 2.0, 0.0), 1.0)

    foe_edge = foe_form.frontage / 2.0 + FLANK_MARGIN

    for u in mine:
        if u.typ == ARC:
            u.set_path([(u.x, u.y)])
            continue

        # 正面の敵前衛（x が最も近いもの）
        tgt = min(foe_front, key=lambda f: abs(f.x - u.x))
        contact = (u.depth + tgt.depth) / 2.0  # 縁が触れる中心間距離
        dy = tgt.y - u.y
        head_on = (tgt.x, tgt.y - math.copysign(contact, dy))

        if u.typ == INF or w <= 0.0:
            u.set_path([head_on])
            continue

        # 騎兵の迂回。自軍から見た自分の x の符号側の外縁を回る。
        #
        # 【計器の非対称・実測で発見】ここを大域座標の符号
        #   sgn = 1.0 if u.x >= 0.0 else -1.0
        # で書くと、x=0 の中央の札が**両軍とも +x 側**へ回り、180度回転の対称性が
        # 壊れる。零点が 騎兵×6 で 0.4420/0.4416 とずれて見つかった。自軍の向き
        # (side) を掛けて自軍フレームで符号を取ると、回転で自動的に対称になる。
        own_x = u.x * u.side
        sgn = (1.0 if own_x >= 0.0 else -1.0) * u.side
        rear = min(foe_rear, key=lambda f: abs(f.x - u.x))
        r_contact = (u.depth + rear.depth) / 2.0
        deep = (rear.x, rear.y + math.copysign(r_contact, dy))

        way_x = u.x + (sgn * foe_edge - u.x) * w
        way_y = u.y + (0.0 - u.y) * w
        aim = (head_on[0] + (deep[0] - head_on[0]) * w,
               head_on[1] + (deep[1] - head_on[1]) * w)
        u.set_path([(way_x, way_y), aim])


def build(army: Army, side: int) -> List[Unit]:
    st = _stations(army.form, len(army.cards), side)
    n_front = min(army.form.n_front, len(army.cards))
    return [Unit(side, c, army.form, pos, i < n_front)
            for i, (c, pos) in enumerate(zip(army.cards, st))]


# ============================================================================
# 1戦の実行
# ============================================================================

def _weights(u: Unit, foes: List[Unit], gaps: List[float]) -> List[float]:
    """射撃の重み。射程は縁からの距離で測る。"""
    out = []
    for f, d in zip(foes, gaps):
        rat = f.ratio()
        # ratio を掛けて全滅した札を撃ち続けないようにし、exp(FOCUS(1-ratio)) で
        # 弱った札へ寄せる。どちらも ratio の連続関数で、分岐を含まない。
        out.append(smooth_gate(d, u.rng, RANGE_SOFT) * rat
                   * math.exp(FOCUS * (1.0 - rat)))
    return out


def simulate(a: Army, b: Army, dt: float = 0.25, t_max: float = T_MAX,
             repulse: float = 0.0, damage: bool = True) -> Dict:
    ua = build(a, +1)
    ub = build(b, -1)
    _plan_paths(ua, ub, a.form, b.form)
    _plan_paths(ub, ua, b.form, a.form)

    start_width = _front_width(ua)
    t = 0.0
    steps = int(round(t_max / dt))
    reason = "time"
    na, nb = len(ua), len(ub)
    men0a = sum(u.men0 for u in ua)
    men0b = sum(u.men0 for u in ub)

    for _ in range(steps):
        # --- 距離は1回だけ作り、移動の制動と射撃の両方で使う ----------------
        gap = [[box_gap(x, y) for y in ub] for x in ua]

        # --- 移動（両軍を同時に評価してから同時に反映する） -----------------
        newpos = []
        for i, u in enumerate(ua):
            newpos.append((u, _step_progress(u, ub, gap[i], dt)))
        for j, u in enumerate(ub):
            newpos.append((u, _step_progress(u, ua, [gap[i][j] for i in range(na)], dt)))
        for u, s in newpos:
            u.progress = s
            u.x, u.y = u.pos_at(s)

        if repulse > 0.0:
            _legacy_repulse(ua + ub, dt, repulse)

        # --- 射撃（同時解決。片側を先に処理すると左右非対称になる） ---------
        if damage:
            da = [0.0] * na
            db = [0.0] * nb
            for i, u in enumerate(ua):
                ws = _weights(u, ub, gap[i])
                tot = sum(ws)
                if tot <= 1e-12:
                    continue
                gate = max(ws)
                base = (u.men * LETHALITY * (u.atk / BASE_ATK) / u.interval
                        * gate / tot * dt)
                for j, (f, w) in enumerate(zip(ub, ws)):
                    if w <= 0.0:
                        continue
                    bonus = 1.0 + TYPE_BONUS if BEATS[u.typ] == f.typ else 1.0
                    db[j] += base * w * bonus * (100.0 / (100.0 + f.dfn))
            for j, u in enumerate(ub):
                col = [gap[i][j] for i in range(na)]
                ws = _weights(u, ua, col)
                tot = sum(ws)
                if tot <= 1e-12:
                    continue
                gate = max(ws)
                base = (u.men * LETHALITY * (u.atk / BASE_ATK) / u.interval
                        * gate / tot * dt)
                for i, (f, w) in enumerate(zip(ua, ws)):
                    if w <= 0.0:
                        continue
                    bonus = 1.0 + TYPE_BONUS if BEATS[u.typ] == f.typ else 1.0
                    da[i] += base * w * bonus * (100.0 / (100.0 + f.dfn))
            for u, d in zip(ua, da):
                u.men = max(u.men - d, 0.0)
            for u, d in zip(ub, db):
                u.men = max(u.men - d, 0.0)

        t += dt
        ra = sum(u.men for u in ua) / men0a
        rb = sum(u.men for u in ub) / men0b
        if ra < ROUT_RATIO or rb < ROUT_RATIO:
            reason = "rout"
            break

    ra = sum(u.men for u in ua) / men0a
    rb = sum(u.men for u in ub) / men0b
    score = 0.5 if abs(ra - rb) < DRAW_BAND else (1.0 if ra > rb else 0.0)
    return {"score": score, "ra": ra, "rb": rb, "t": t, "reason": reason,
            "width_start": start_width, "width_end": _front_width(ua)}


def _step_progress(u: Unit, foes: List[Unit], gaps: List[float],
                   dt: float) -> float:
    if u.total_len <= 1e-9:
        return 0.0
    z = 0.0
    for f, d in zip(foes, gaps):
        z += ZOC_STR * f.ratio() * smooth_gate(d, 0.0, ZOC_R)
    v = u.speed * math.exp(-z)
    remain = u.total_len - u.progress
    return u.total_len - approach_exact(remain, v, dt)


def _front_width(units: Sequence[Unit]) -> float:
    """前衛の左右端の中心間距離。戦列がどれだけ広がったかを測る。

    前衛か後衛かは**開戦時の枠**で決める。現在の y で分類すると、前進した札が
    途中で別の分類へ移り、広がりではなく分類の変化を測ってしまう（計器の罠）。
    迂回する騎兵は横へ大きく出るのが仕事なので、この指標からは外す。
    """
    fr = [u for u in units if u.is_front and u.typ != CAV]
    if len(fr) < 2:
        fr = [u for u in units if u.is_front]
    if len(fr) < 2:
        fr = list(units)
    xs = [u.x for u in fr]
    return max(xs) - min(xs)


def _legacy_repulse(units: List[Unit], dt: float, strength: float) -> None:
    """【旧実装の再現】対斥力を陽的オイラーで積分する。

    直すためではなく、症状を測って再現できるようにするために残している。
    """
    for a in units:
        fx = 0.0
        for b in units:
            if a is b:
                continue
            dx = a.x - b.x
            dy = a.y - b.y
            d2 = dx * dx + dy * dy
            if d2 < 1e-9:
                continue
            d = math.sqrt(d2)
            reach = (a.width + b.width) / 2.0 * LEGACY_REACH
            if d < reach:
                fx += strength * (reach - d) / reach * (dx / d)
        a.x += fx * dt


# ============================================================================
# 合成カード（統制された編成。実カードのサンプルは使わない）
# ============================================================================

def flat_army(cost: float = BASE_COST, typ: str = INF, n: int = 6,
              form: Formation = FORM_STANDARD, roles=None) -> Army:
    roles = roles or [BAL] * n
    return Army(tuple(Card(cost, typ, r) for r in roles[:n]), form)


def type_army(typ: str, form: Formation = FORM_STANDARD) -> Army:
    """三すくみ用。役割を混ぜ、合計コストを 30 に揃えた単一兵種の編成（§5.3）。"""
    return Army(tuple(Card(BASE_COST, typ, r) for r in MIXED_ROLES), form)


def split_army(delta: float, i: int, j: int, typ: str = INF,
               form: Formation = FORM_STANDARD) -> Army:
    """総コスト 30 を保ったまま、枠 i から枠 j へ delta を移す。"""
    costs = [BASE_COST] * 6
    costs[i] += delta
    costs[j] -= delta
    return Army(tuple(Card(c, typ) for c in costs), form)


def mixed_army(typs: Sequence[str], cost: float = BASE_COST,
               form: Formation = FORM_STANDARD) -> Army:
    return Army(tuple(Card(cost, t) for t in typs), form)


def mixed_role_army(typs: Sequence[str], form: Formation = FORM_STANDARD) -> Army:
    return Army(tuple(Card(BASE_COST, t, r)
                      for t, r in zip(typs, MIXED_ROLES)), form)


# ============================================================================
# パネル（勝率の測り方）
# ============================================================================

def panel(a: Army, b: Army, dt: float = 0.25, rot: int = 6) -> float:
    """a を b にぶつけた勝率(%)。

    決定論なので1試合では 0/100 に飽和する。**枠の並びを回して**多数の局面を作り、
    さらに左右を入れ替えて両側から測る。左右入れ替えは仮定ではなく検算であり、
    零点はこれとは別に入れ替えなしでも測る（`zero`）。
    分解能は 100/(rot*rot*2) %。
    """
    tot = 0.0
    n = 0
    for r in range(rot):
        ar = Army(a.cards[r:] + a.cards[:r], a.form)
        for s in range(rot):
            br = Army(b.cards[s:] + b.cards[:s], b.form)
            tot += simulate(ar, br, dt=dt)["score"]
            tot += 1.0 - simulate(br, ar, dt=dt)["score"]
            n += 2
    return 100.0 * tot / n


def cost_panel(delta: float, dt: float = 0.25) -> float:
    """総コスト30を保ったまま枠 i→j へ delta を移した編成の、均等配分に対する優位。

    6枠の順序対 30 通り × 左右 2 = 60戦。分解能 1.67%。
    """
    base = flat_army()
    tot = 0.0
    n = 0
    for i in range(6):
        for j in range(6):
            if i == j:
                continue
            v = split_army(delta, i, j)
            tot += simulate(v, base, dt=dt)["score"]
            tot += 1.0 - simulate(base, v, dt=dt)["score"]
            n += 2
    return 100.0 * tot / n - 50.0


# ============================================================================
# 測定コマンド
# ============================================================================

DTS = (1.0, 0.5, 0.25, 0.125)
ZERO_CASES = (
    ("歩兵×6 標準", lambda: type_army(INF)),
    ("騎兵×6 標準", lambda: type_army(CAV)),
    ("弓兵×6 標準", lambda: type_army(ARC)),
    ("混成 標準", lambda: mixed_role_army([INF, INF, CAV, ARC, ARC, CAV])),
    ("混成 広く浅い", lambda: mixed_role_army([INF, INF, CAV, ARC, ARC, CAV],
                                              FORM_WIDE)),
    ("混成 狭く深い", lambda: mixed_role_army([INF, INF, CAV, ARC, ARC, CAV],
                                              FORM_DEEP)),
)


def cmd_zero(args) -> None:
    print("零点（同じ編成どうし。左右の入れ替えをせずに測る）")
    print("  0.00 でなければ左右非対称のバグ。50%だと仮定しない。")
    print()
    print("  {:<16}".format("編成") + "".join(f"dt={d:<8}" for d in DTS))
    for name, mk in ZERO_CASES:
        row = f"  {name:<16}"
        for dt in DTS:
            army = mk()
            row += "{:+8.2f}    ".format(100.0 * simulate(army, army, dt=dt)["score"] - 50.0)
        print(row)


def cmd_selftest(args) -> None:
    """零点が 0.00 なのは「対称だから」であって、計器に感度がある証拠ではない。

    **全部ゼロが並んだら、まず計器を疑う。** ここでは既知の非対称をわざと入れ、
    零点が反応するもの／しないものを分けて確かめる。何が見えないかを知らずに
    「零点 0.00 だから健全」と言うのが、これまで測定が嘘をついた形である。
    """
    army = mixed_role_army([INF, INF, CAV, ARC, ARC, CAV])

    def zero() -> float:
        return 100.0 * simulate(army, army)["score"] - 50.0

    print("計器の陽性対照（わざと壊して、零点が反応するかを見る）")
    print()
    print("  {:<38}{:>8}  {}".format("注入した非対称", "零点", "期待"))
    print("  {:<38}{:>8.2f}  {}".format("なし（対称）", zero(), "0 であるべき"))

    # (1) 片側だけ能力を上げる … 検出できなければならない
    orig = Unit.__init__
    for pct in (0.1, 0.001):
        def patched(u, side, card, form, station, is_front, _p=pct):
            orig(u, side, card, form, station, is_front)
            if side > 0:
                u.atk *= (1.0 + _p / 100.0)
        Unit.__init__ = patched
        try:
            v = zero()
        finally:
            Unit.__init__ = orig
        print("  {:<38}{:>8.2f}  {}".format(
            f"A側だけ攻撃力 +{pct}%", v, "0 でないべき"))

    # (2) ダメージを逐次解決する … 検出できなければならない
    print("  {:<38}{:>8.2f}  {}".format(
        "ダメージをA→Bの順に逐次解決", _zero_sequential(army), "0 でないべき"))

    # (3) 敵軍を鏡映で配置する … **零点では見えない**
    orig_st = globals()["_stations"]

    def mirrored(form, n_cards, side):
        out = orig_st(form, n_cards, +1)
        return [(x, -y) for (x, y) in out] if side < 0 else out
    globals()["_stations"] = mirrored
    try:
        v = zero()
    finally:
        globals()["_stations"] = orig_st
    print("  {:<38}{:>8.2f}  {}".format("敵軍を鏡映で配置（§5.1 違反）", v,
                                        "0 のまま＝零点では見えない"))
    print()
    print("  鏡映配置が零点に出ないのは、鏡映でも「両軍が対等」だからである。")
    print("  壊れるのは対面関係（自軍左翼が敵のどちらと当たるか）であって公平性では")
    print("  ない。**零点は不公平しか見つけられない。** 対面関係は別途、配置を")
    print("  非対称にした編成で確かめる必要がある。")


def _zero_sequential(army: Army) -> float:
    """ダメージをA→Bの順に適用したときの零点（片側が先に減る＝不公平）。"""
    ua = build(army, +1)
    ub = build(army, -1)
    _plan_paths(ua, ub, army.form, army.form)
    _plan_paths(ub, ua, army.form, army.form)
    dt = 0.25
    for _ in range(int(T_MAX / dt)):
        gap = [[box_gap(x, y) for y in ub] for x in ua]
        for side_a, side_b, g in ((ua, ub, gap),
                                  (ub, ua, [list(c) for c in zip(*gap)])):
            for i, u in enumerate(side_a):
                ws = _weights(u, side_b, g[i])
                tot = sum(ws)
                if tot <= 1e-12:
                    continue
                base = (u.men * LETHALITY * (u.atk / BASE_ATK) / u.interval
                        * max(ws) / tot * dt)
                for f, w in zip(side_b, ws):
                    if w <= 0.0:
                        continue
                    bonus = 1.0 + TYPE_BONUS if BEATS[u.typ] == f.typ else 1.0
                    f.men = max(f.men - base * w * bonus
                                * (100.0 / (100.0 + f.dfn)), 0.0)
    ra = sum(u.men for u in ua) / sum(u.men0 for u in ua)
    rb = sum(u.men for u in ub) / sum(u.men0 for u in ub)
    return 100.0 * (0.5 if abs(ra - rb) < DRAW_BAND else
                    (1.0 if ra > rb else 0.0)) - 50.0


def cmd_spread(args) -> None:
    print("戦列の広がり（前衛の左右端の中心間距離 m。開始 1000.0 = x ±500）")
    print("  歩兵×6 で測る。混成だと迂回する騎兵が横へ出るのと区別できない。")
    print()
    army = flat_army(typ=INF)
    print("  {:<24}{:>9}{:>9}{:>9}{:>9}".format(
        "条件", "dt=1.0", "dt=0.5", "dt=0.25", "dt=0.125"))
    for label, rep in (("採用形（斥力なし）", 0.0), ("旧: 斥力 12", 12.0),
                       ("旧: 斥力 4（弱め）", 4.0), ("旧: 斥力 1（さらに弱め）", 1.0)):
        row = f"  {label:<24}"
        for dt in DTS:
            row += "{:>9.1f}".format(
                simulate(army, army, dt=dt, repulse=rep)["width_end"])
        print(row)

    print()
    print("平衡点の検算（ダメージを切って幾何だけを長時間まわす）")
    print("  止まるなら平衡点がある。伸び続けるなら弱めただけで直っていない。")
    print("  {:<24}{:>9}{:>9}{:>9}{:>9}".format(
        "条件", "t=90", "t=180", "t=360", "t=720"))
    for label, rep in (("採用形（斥力なし）", 0.0), ("旧: 斥力 4", 4.0),
                       ("旧: 斥力 1", 1.0)):
        row = f"  {label:<24}"
        for tm in (90.0, 180.0, 360.0, 720.0):
            row += "{:>9.1f}".format(simulate(army, army, dt=0.25, t_max=tm,
                                              repulse=rep, damage=False)["width_end"])
        print(row)


def margin(a: Army, b: Army, dt: float = 0.5) -> float:
    """a と b の残存兵力率の差。勝敗ではなく**差そのもの**を返す。"""
    r = simulate(a, b, dt=dt)
    return r["ra"] - r["rb"]


def cost_yardstick(dt: float = 0.5) -> float:
    """「総コストを 1 増やすと残存兵力率の差がいくつ動くか」を測る物差し。

    加算性を勝率で測ると、差が数値の埃ほどしかなくても勝敗は 0/100 に振れる。
    実際に測ると、9/1 まで偏らせたときの差は 4e-3 しかないのに、勝率は ±16.7〜50%
    と出る。**勝率は埃の符号を 増幅 しているだけで、強さを測っていない。**
    そこで、配分による差を「総コスト何点ぶんか」に換算して報告する。
    """
    base = flat_army()
    pts = []
    for extra in (0.3, 0.6, 1.2, 3.0, 6.0):
        v = flat_army(cost=BASE_COST + extra / 6.0)
        pts.append((extra, margin(v, base, dt)))
    # 原点を通る直線で当てる（差 = slope × 追加コスト）
    num = sum(x * y for x, y in pts)
    den = sum(x * x for x, _ in pts)
    return num / den


def cmd_cost(args) -> None:
    print("コストの加算性（総コスト30を固定し、配分だけ動かす）")
    print()
    print("【1】まず物差しを作る: 総コストを増やすと残存兵力率の差はどう動くか")
    print("  {:<14}{:>12}{:>16}".format("追加コスト", "差", "差/コスト"))
    base = flat_army()
    for extra in (0.3, 0.6, 1.2, 3.0, 6.0):
        m = margin(flat_army(cost=BASE_COST + extra / 6.0), base, args.dt)
        print("  +{:<13.1f}{:>12.2e}{:>16.4f}".format(extra, m, m / extra))
    slope = cost_yardstick(args.dt)
    print(f"  → 総コスト1点 = 差 {slope:.4f}")
    print()

    print("【2】配分を偏らせたときの差を、コスト何点ぶんかへ換算する")
    print("  完全に加法的なら 0.00 点。30点中の何%かで見る。")
    print("  {:<8}{:>12}{:>14}{:>12}{:>12}".format(
        "配分", "差(中央値)", "コスト換算", "30点中", "勝率(参考)"))
    for d, name in ((1.0, "6/4"), (2.0, "7/3"), (3.0, "8/2"), (4.0, "9/1")):
        ms = []
        for i in range(6):
            for j in range(6):
                if i != j:
                    ms.append(abs(margin(split_army(d, i, j), base, args.dt)))
        ms.sort()
        med = ms[len(ms) // 2]
        eq = med / slope
        print("  {:<8}{:>12.2e}{:>14.3f}{:>11.2f}%{:>12.1f}".format(
            name, med, eq, 100.0 * eq / 30.0, cost_panel(d, args.dt) + 50.0))
    print()
    print("  勝率の列は飽和して見えるが、実体は上の「30点中」の列である。")
    print("  9/1 まで偏らせても総コスト 0.3点未満ぶんしか動いていない。")
    print()

    print("【3】時間刻みを振って符号が変わらないか（結論に使える条件）")
    print("  {:<8}".format("配分") + "".join(f"dt={d:<10}" for d in DTS))
    for d, name in ((2.0, "7/3"), (4.0, "9/1")):
        row = f"  {name:<8}"
        for dt in DTS:
            sl = cost_yardstick(dt)
            ms = [margin(split_army(d, i, j), base, dt)
                  for i in range(6) for j in range(6) if i != j]
            ms.sort()
            row += "{:>+9.4f}   ".format(ms[len(ms) // 2] / sl)
        print(row)
    print("  （コスト点ぶんの中央値。符号と大きさが dt で変わらないことを見る）")


def cmd_triangle(args) -> None:
    global TYPE_BONUS
    keep = TYPE_BONUS
    print("兵種の三すくみ（役割を混ぜた統制編成。両側とも合計コスト30）")
    print("  完全に均衡していれば 50.0。目標帯 55〜80%。")
    print(f"  {args.rot * args.rot * 2} 戦/点、分解能 "
          f"{100.0 / (args.rot * args.rot * 2):.2f}%、dt={args.dt}")
    print()
    print("  {:<10}{:>12}{:>12}{:>12}{:>10}".format(
        "有利補正", "歩兵→騎兵", "騎兵→弓兵", "弓兵→歩兵", "同兵種"))
    for bo in args.bonus:
        TYPE_BONUS = bo
        row = f"  {bo:<10.3f}"
        for at, dt_ in ((INF, CAV), (CAV, ARC), (ARC, INF)):
            row += "{:>12.1f}".format(
                panel(type_army(at), type_army(dt_), args.dt, args.rot))
        row += "{:>10.1f}".format(
            panel(type_army(INF), type_army(INF), args.dt, args.rot))
        print(row)
    TYPE_BONUS = keep


def cmd_formation(args) -> None:
    print("陣形（面積 500×30 を保存して、正面と奥行を交換する）")
    print("  同じ6枚で陣形だけ変える。0/100 に貼り付いていないかを見る。")
    print(f"  {args.rot * args.rot * 2} 戦/セル、dt={args.dt}")
    print()
    typs = [INF, INF, CAV, ARC, ARC, CAV]
    forms = [("標準(前衛3)", FORM_STANDARD), ("広く浅い(前衛4)", FORM_WIDE),
             ("狭く深い(前衛2)", FORM_DEEP)]
    print("  {:<18}".format("攻＼守") + "".join(f"{n:<18}" for n, _ in forms))
    for na, fa in forms:
        row = f"  {na:<18}"
        for nb, fb in forms:
            row += "{:<18.1f}".format(
                panel(mixed_role_army(typs, fa), mixed_role_army(typs, fb),
                      args.dt, args.rot))
        print(row)


def cmd_calib(args) -> None:
    print("計器の素性（決着理由と戦闘時間）")
    for name, mk in ZERO_CASES[:4]:
        for dt in (1.0, 0.25):
            r = simulate(mk(), mk(), dt=dt)
            print(f"  {name:<14} dt={dt:<6} t={r['t']:6.2f}s "
                  f"理由={r['reason']:<5} 残存 {r['ra']:.4f}/{r['rb']:.4f}")


def main() -> None:
    p = argparse.ArgumentParser(description="位置ベース最小モデルの測定")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("zero", cmd_zero), ("selftest", cmd_selftest),
                     ("spread", cmd_spread), ("calib", cmd_calib)):
        sub.add_parser(name).set_defaults(func=fn)
    s = sub.add_parser("cost")
    s.add_argument("--dt", type=float, default=0.5)
    s.set_defaults(func=cmd_cost)
    s = sub.add_parser("triangle")
    s.add_argument("--dt", type=float, default=0.5)
    s.add_argument("--rot", type=int, default=6)
    s.add_argument("--bonus", type=float, nargs="*",
                   default=[0.0, 0.02, 0.04, 0.06, 0.10])
    s.set_defaults(func=cmd_triangle)
    s = sub.add_parser("formation")
    s.add_argument("--dt", type=float, default=0.5)
    s.add_argument("--rot", type=int, default=6)
    s.set_defaults(func=cmd_formation)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
