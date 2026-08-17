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


# ============================================================================
# 【不採用】素の順位制（挑戦ラダー）。**収束が遅すぎる**ので採用しない
# ============================================================================
#
# 一つ上と当たって入れ替える形。実装して測ったら、隣どうしの入れ替えは**カードの
# シャッフルと同じで混ざるのが遅い**（混合時間 ~N² 巡）と分かった。
#
#   16人 256巡（毎時1戦で11日） / 100人 1万巡（1.1年） / 1000人 100万巡（114年）
#
# 実測でも +1コストという明確に強い札が50巡で6ランクしか上がらない。人数が
# 増えると、強い新規が上位へ到達するのが事実上不可能になる。
#
# **狙い（次の対戦相手が事前に分かる）は組み方を決定的にすれば満たせる**ので、
# 内部は Elo で組んで、表示を順位にする（下の `Board`）。以下は比較用に残す。
#
# 一つ上の順位と当たり、勝てば入れ替わる。Elo とほぼ同じ序列に落ち着くが、
# **次の対戦相手が事前に分かる**点が決定的に違う。
#
# §3 の時間割は「毎時06分 次の対戦相手と戦場条件を告知」「06〜55分 相手の前回
# 編成を踏まえて編成を変更」と、**次の相手が分かっている前提**で書かれている。
# Elo でも組んだ後に告知はできるが、順位制なら構造的にそうなる。偵察（§3.2）が
# 「一般的な対策」ではなく「この相手への対策」になり、編成研究型の核が回る。
#
# ── 均衡なら何が出るか（測る前に計算しておく）────────────────
#
# 隣どうしで勝てば入れ替わるので、実力が等しければ順位は ±1 のランダムウォーク。
# N巡後の順位の標準偏差は **√N ランク**（ただし端で跳ね返るので、人数が少ないと
# それより小さく出る）。勝率 p の人は1巡あたり **(2p-1) ランク**上がる。
#
# ── 組み方 ──────────────────────────────────
#
# 偶奇を巡ごとに入れ替える。そうしないと (1,2)(3,4)... の組が固定され、
# **2位と3位が永久に当たらない**。
LADDER_STEP = 1          # 勝ったときに動く順位の数。1 で隣と入れ替え（不採用方式）


def ladder_pairs(order: Sequence[Entrant], rnd: int
                 ) -> List[Tuple[Entrant, Entrant]]:
    """順位表から今巡の組を作る。**上位が先、下位が挑戦側。**

    偶奇を巡ごとに入れ替えるので、あぶれるのは端の1人だけになる。
    """
    off = rnd % 2
    return [(order[i], order[i + 1]) for i in range(off, len(order) - 1, 2)]


def play_ladder_round(order: List[Entrant], rnd: int, dt: float = 0.5) -> None:
    """1巡ぶん。**下位が勝ったら入れ替える。** 順位表そのものを書き換える。"""
    reg = rnd % len(M.REGULATIONS)
    swaps = []
    for hi, lo in ladder_pairs(order, rnd):
        seed = hash((rnd, hi.pid, lo.pid)) & 0x7FFFFFFF
        r = M.play_one(lo.entry, hi.entry, reg, dt, seed=seed)
        won = r["winner"] == "A"          # A = 挑戦側（下位）
        for x, s, o in ((lo, 1.0 if won else 0.0, hi),
                        (hi, 0.0 if won else 1.0, lo)):
            x.games += 1
            x.wins += s
            x.recent.append(o.pid)
        if won:
            swaps.append((hi, lo))
    # **入れ替えは全部の対戦が終わってから。** 途中で順位表を触ると、同じ巡の
    # 後ろの組が動いたあとの順位で戦うことになる（同時解決の破れと同じ形）。
    idx = {e.pid: i for i, e in enumerate(order)}
    for hi, lo in swaps:
        i, j = idx[hi.pid], idx[lo.pid]
        order[i], order[j] = order[j], order[i]
        idx[hi.pid], idx[lo.pid] = j, i


# ============================================================================
# 採用方式: レートは内部、表示は順位、組み方は先に決めて告知する（§7.35）
# ============================================================================
#
# **レートは4本持つ。** BO1 の3レギュレーションと、BO3（毎時の自動参加）。
# 1本にまとめると全員が得意帯だけで挑み、残り2部隊が飾りになる。分ければ
# 「高コスト戦だけ極める」「低コスト戦を掘る」が別々の順位として成立する
# （旧 DCI が Standard と Limited で別レートだったのと同じ）。
#
# **組む → 告知 → 編成期間 → 戦う** の順を関数の形で分ける。§3 の時間割が
#
#   毎時06分 次の対戦相手を告知 → 06〜50分 編成期間 → 次の00分 対戦
#
# なので、`plan_round` で組んだ結果が編成期間中ずっと固定されていなければ
# 偵察が成立しない。**組んでから戦うまでの間に編成が変わる**のがこのゲームの
# 読み合いなので、2つを1つの関数に混ぜないこと。

BOARDS: Tuple[str, ...] = ("低コスト戦", "中コスト戦", "高コスト戦", "統一(BO3)")
BO3_BOARD = 3


@dataclass
class Board:
    """1つの順位表。レートは内部に持ち、外へは順位で見せる。"""
    name: str
    reg: Optional[int]                    # BO1 のレギュレーション。None なら BO3
    rating: Dict[str, float] = dfield(default_factory=dict)
    games: Dict[str, int] = dfield(default_factory=dict)
    wins: Dict[str, float] = dfield(default_factory=dict)
    recent: Dict[str, List[str]] = dfield(default_factory=dict)

    def get(self, pid: str) -> float:
        return self.rating.setdefault(pid, RATING_START)

    def order(self, pids: Sequence[str]) -> List[str]:
        """順位。**現在レート順**（最高到達では並べない。§7.29）。"""
        return sorted(pids, key=lambda p: (-self.get(p), p))


def plan_round(board: Board, pids: Sequence[str], rnd: int
               ) -> List[Tuple[str, str]]:
    """次の対戦の組を決める。**戦う前に呼び、そのまま告知する。**

    同レート帯の隣どうしで組み、直近の相手は避ける。決定的なので、告知した組は
    編成期間中に変わらない。
    """
    order = board.order(pids)
    used: set = set()
    out: List[Tuple[str, str]] = []
    for a in order:
        if a in used:
            continue
        cand = [b for b in order
                if b not in used and b != a
                and b not in board.recent.get(a, [])[-REMATCH_GAP:]]
        if not cand:
            cand = [b for b in order if b not in used and b != a]
        if not cand:
            continue
        b = min(cand, key=lambda x: abs(board.get(a) - board.get(x)))
        used.add(a); used.add(b)
        out.append((a, b))
    return out


def resolve_round(board: Board, pairs: Sequence[Tuple[str, str]],
                  entries: Dict[str, M.Entry], rnd: int,
                  dt: float = 0.5) -> None:
    """告知済みの組を戦わせてレートを更新する。**組み直さない。**"""
    for a, b in pairs:
        seed = hash((board.name, rnd, a, b)) & 0x7FFFFFFF
        if board.reg is None:
            r = M.play(entries[a], entries[b], dt, seed=seed)
            sa = 1.0 if r["wins_a"] > r["wins_b"] else (
                0.0 if r["wins_b"] > r["wins_a"] else 0.5)
        else:
            r = M.play_one(entries[a], entries[b], board.reg, dt, seed=seed)
            sa = 1.0 if r["winner"] == "A" else (
                0.0 if r["winner"] == "B" else 0.5)
        ea = expected(board.get(a), board.get(b))
        board.rating[a] = board.get(a) + K_FACTOR * (sa - ea)
        board.rating[b] = board.get(b) - K_FACTOR * (sa - ea)
        for x, sc, o in ((a, sa, b), (b, 1.0 - sa, a)):
            board.games[x] = board.games.get(x, 0) + 1
            board.wins[x] = board.wins.get(x, 0.0) + sc
            board.recent.setdefault(x, []).append(o)


def make_boards() -> List[Board]:
    """4本の順位表を作る（BO1 の3レギュ + BO3）。"""
    return [Board(BOARDS[i], i if i < BO3_BOARD else None)
            for i in range(len(BOARDS))]


# ============================================================================
# ダミーの引き上げ（足場を抜く・§7.36）
# ============================================================================
#
# ダミーは**足場であって常設ではない**。人が増えたら抜く。抜き方を決めておかないと、
# 「いつの間にか人間がボット相手の成績で順位を得ている」状態が固定される。
#
# ── 抜くときに壊れうるもの ──────────────────────────
#
# **順位表に穴が空く。** 抜いたダミーの下に居た人が繰り上がるが、**それは戦って
# 得た順位ではない**。一気に抜くと、順位が実力と無関係にずれる。
#
# **相手が居なくなる。** 人が少ない時間帯にダミーを抜きすぎると、組めない人が出る。
# §10 の「過疎時間のAI・ゴースト対戦の可否」がここに当たる。
#
# ── 決めた段取り ────────────────────────────────
#
#  1. **下から抜く。** 上位のダミーは「人間が越えるべき壁」として残す価値があるが、
#     下位のダミーは人間の踏み台にしかならない。下位から抜けば繰り上がりも小さい。
#  2. **1巡に1体まで。** まとめて抜くと順位が跳ねる。
#  3. **人間の数が足りている表だけ抜く。** 組める最小人数（MIN_FIELD）を割る表では
#     抜かない。過疎の帯にはダミーを残す。
#  4. **抜いたダミーのレートは捨てる。** 残った人のレートは触らない。ゼロサムの
#     Elo なので、抜けた相手ぶんの点はそのまま残った人の中に居る。
MIN_FIELD = 8            # この人数を割る表からはダミーを抜かない


def retire_plan(board: Board, humans: Sequence[str], dummies: Sequence[str],
                target_ratio: float = 0.5) -> List[str]:
    """この巡に抜くダミーを返す（0体か1体）。

    `target_ratio` はダミーが占めてよい割合。人間が増えるほど自然に減る。
    **一気に抜かない**ので、返すのは最大1体。
    """
    total = len(humans) + len(dummies)
    if total - 1 < MIN_FIELD:
        return []
    if not dummies:
        return []
    if len(dummies) <= target_ratio * total:
        return []
    # 下位から抜く（順位表の末尾に近いダミー）
    order = board.order(list(humans) + list(dummies))
    for pid in reversed(order):
        if pid in dummies:
            return [pid]
    return []
