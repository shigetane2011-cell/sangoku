# -*- coding: utf-8 -*-
"""sim/ladder.py -- ランク戦（レートとマッチング）

§10 の未決だった方式を Elo で埋める。**BO1 がレートを動かし、BO3（毎時の自動
参加）は別のランキング**（§7.29）。

================================================================================
 均衡していたら何が出るか（測る前に計算しておく）
================================================================================
盤面には乱数がある（σ=0.15）ので、**同じ編成どうしでもレートは散らばる**。
零点は 0 ではない。Elo の更新は勝敗ごとに ±K·(1-期待値) なので、実力が完全に
等しい集団のレートはランダムウォークになり、N戦後の標準偏差は

    sd ≈ K · √(N · p(1-p)) = K · √N / 2        （p = 0.5）

K=24・1人50戦なら sd ≈ 85 点。**この値と比べて初めて「散らばりすぎ」が言える。**
0 と比べると、正しく動いていても壊れて見える。

陽性対照も同じく数値で予測できる。勝率 p の相手に対する Elo の落ち着き先は

    Δレート = -400 · log10(1/p - 1)

コスト +1点 は勝率 73%（§7.27 の曲線）なので **Δ ≈ +170点**。ここへ寄れば、
ラダーは強さを拾えている。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field as dfield
from typing import Dict, List, Optional, Sequence, Tuple

from . import field as F
from . import match as M

K_FACTOR = 24.0          # 1戦あたりの最大変動
RATING_START = 1500.0
BAND = 200.0             # マッチングの帯（この幅の中から相手を選ぶ）
REMATCH_GAP = 3          # 直近この回数は同じ相手と当てない


def expected(ra: float, rb: float) -> float:
    """A から見た期待勝率（Elo）。"""
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def rating_for(p: float) -> float:
    """勝率 p が落ち着くレート差。**陽性対照の予測に使う。**"""
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return -400.0 * math.log10(1.0 / p - 1.0)


def null_spread(k: float, games: int) -> float:
    """実力が完全に等しいときのレートの標準偏差（ランダムウォーク）。"""
    return k * math.sqrt(games) / 2.0


@dataclass
class Entrant:
    """ラダー上の1人。編成と、レートだけを持つ。"""
    pid: str
    name: str
    entry: M.Entry
    rating: float = RATING_START
    games: int = 0
    wins: float = 0.0
    recent: List[str] = dfield(default_factory=list)


def pair_up(field_: Sequence[Entrant], rng: random.Random
            ) -> List[Tuple[Entrant, Entrant]]:
    """同レート帯で組む。**直近の相手は避ける。**

    レート順に並べて隣どうしから取る。帯（BAND）に収まらない相手しか居なければ、
    待たせずに範囲を広げる（§10「待機時間に応じて範囲を拡張」）。
    """
    pool = sorted(field_, key=lambda e: (-e.rating, e.pid))
    used: set = set()
    out = []
    for a in pool:
        if a.pid in used:
            continue
        best = None
        for b in pool:
            if b.pid in used or b.pid == a.pid:
                continue
            if b.pid in a.recent[-REMATCH_GAP:]:
                continue
            d = abs(a.rating - b.rating)
            if best is None or d < best[0]:
                best = (d, b)
        if best is None:            # 直近を避けきれない場合は避けずに組む
            for b in pool:
                if b.pid not in used and b.pid != a.pid:
                    best = (abs(a.rating - b.rating), b)
                    break
        if best is None:
            continue
        b = best[1]
        used.add(a.pid); used.add(b.pid)
        out.append((a, b))
    return out


def play_round(field_: Sequence[Entrant], rnd: int, dt: float = 0.5,
               rng: Optional[random.Random] = None) -> None:
    """1巡ぶん。**レギュレーションは挑戦ごとに割り当てる**（§7.32 の未決）。

    3部隊すべてを使わせるため、巡ごとに帯を変える。1本のレートで得意帯だけを
    選ばせると、残り2部隊が飾りになる。
    """
    rng = rng or random.Random(rnd)
    reg = rnd % len(M.REGULATIONS)
    for a, b in pair_up(field_, rng):
        seed = hash((rnd, a.pid, b.pid)) & 0x7FFFFFFF
        r = M.play_one(a.entry, b.entry, reg, dt, seed=seed)
        sa = 1.0 if r["winner"] == "A" else (0.0 if r["winner"] == "B" else 0.5)
        ea = expected(a.rating, b.rating)
        a.rating += K_FACTOR * (sa - ea)
        b.rating -= K_FACTOR * (sa - ea)
        for x, s, o in ((a, sa, b), (b, 1.0 - sa, a)):
            x.games += 1
            x.wins += s
            x.recent.append(o.pid)


def standings(field_: Sequence[Entrant]) -> List[Entrant]:
    """順位表。**現在レートで並べる**（最高到達では並べない。§7.29）。"""
    return sorted(field_, key=lambda e: (-e.rating, e.pid))
