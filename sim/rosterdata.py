# -*- coding: utf-8 -*-
"""sim/data/*.csv（武将80枚・必殺技80種・固有特性19種）の読み込みと検算。

**使う前に必ず `python3 sim/rosterdata.py` を通すこと。** データが狂っていると、
その上で測る勝率も値付けも全部嘘になる（§13 の「測定が嘘をついた」事例と同じ形）。

CSV は UTF-8 BOM 付きなので `utf-8-sig` で開く。BOM を無視すると先頭列の
キーが '\\ufeff名前' になり、**1行も読めないのに例外も出ない**（実測で踏んだ）。
"""

from __future__ import annotations

import csv
import math
import os
import statistics as st
from collections import Counter
from typing import Dict, List

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 兵種標準の攻撃間隔（§6.3）。CSV に列が無いのでここで補う。
INTERVAL = {"歩兵": 1.2, "騎兵": 1.1, "弓兵": 1.3}


def _load(name: str) -> List[Dict[str, str]]:
    with open(os.path.join(DATA, name), encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if any(v.strip() for v in r.values())]


def generals() -> List[Dict[str, str]]:
    return [r for r in _load("generals.csv") if r.get("名前")]


def skills() -> List[Dict[str, str]]:
    return [r for r in _load("skills.csv") if r.get("技名")]


def traits() -> List[Dict[str, str]]:
    return [r for r in _load("traits.csv") if r.get("キー")]


def power(g: Dict[str, str]) -> float:
    """総合値 = 実効耐久 × 実効火力（§4.6）。

    実効耐久 = 兵力 × (100 + 防御力) / 100
    実効火力 = 攻撃力 × 命中率 × クリティカル期待値 / 攻撃間隔

    §6.1「強さに効く数値をひとつでも式から落とすと、その値が高い兵種が予算外の
    優位を持つ」。命中・クリ・攻撃間隔を必ず入れる。
    """
    men = float(g["兵力"])
    atk = float(g["攻撃力"])
    dfn = float(g["防御力"])
    hit = float(g["命中率"]) / 100.0
    crit = float(g["クリ率"]) / 100.0
    dur = men * (100.0 + dfn) / 100.0
    fire = atk * hit * (1.0 + 0.5 * crit) / INTERVAL[g["兵種"]]
    return dur * fire / 1e5


def affine_fit(rows) -> tuple:
    """総合値 ≒ A + B×コスト を最小二乗で当てる。

    §4.6 / sim/roster.py の設計は「枠の基礎価値 A ＋ コスト比例分 B×c」。
    1部隊は6枠固定なので、この形なら **合計コストが同じ編成は配分によらず
    総価値が等しくなる**（6A + B×合計コスト）。A が大きいほど安い札が得に見えるが、
    枠数が固定なので編成の総価値には効かない、というのが狙いである。
    """
    xs = [float(r["_cost"]) for r in rows]
    ys = [r["_power"] for r in rows]
    mx, my = st.mean(xs), st.mean(ys)
    b = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
         / sum((x - mx) ** 2 for x in xs))
    return my - b * mx, b


def check() -> int:
    G, S, T = generals(), skills(), traits()
    for g in G:
        g["_cost"] = int(g["コスト"])
        g["_power"] = power(g)
    bad = 0
    print(f"武将 {len(G)}枚 / 必殺技 {len(S)}種 / 固有特性 {len(T)}種")

    # --- 参照の整合 -------------------------------------------------------
    sk = {s["技名"]: s for s in S}
    tk = {t["キー"]: t for t in T}
    miss_s = [g["名前"] for g in G if g["必殺技"] not in sk]
    miss_t = sorted({g["固有特性"] for g in G if g["固有特性"] not in tk})
    unused = [s["技名"] for s in S if s["技名"] not in {g["必殺技"] for g in G}]
    cost_ng = [(g["名前"], g["コスト"], sk[g["必殺技"]]["コスト"])
               for g in G if g["必殺技"] in sk
               and g["コスト"] != sk[g["必殺技"]]["コスト"]]
    cnt = Counter(g["固有特性"] for g in G)
    cnt_ng = [(k, tk[k]["採用枚数"], cnt.get(k, 0)) for k in tk
              if int(tk[k]["採用枚数"]) != cnt.get(k, 0)]
    for label, v in (("必殺技が未定義", miss_s), ("固有特性が未定義", miss_t),
                     ("使われていない必殺技", unused),
                     ("必殺技とコストが不一致", cost_ng),
                     ("固有特性の採用枚数が不一致", cnt_ng)):
        print(f"  {label}: {v if v else 'なし'}")
        bad += len(v)

    # --- 実力比% が能力値から再現できるか ---------------------------------
    k = st.mean(float(g["実力比%"]) / g["_power"] for g in G)
    err = [abs(float(g["実力比%"]) - k * g["_power"]) / float(g["実力比%"]) * 100
           for g in G]
    print(f"  実力比% ≒ 総合値 × {k:.2f}  （最大誤差 {max(err):.2f}% / "
          f"平均 {st.mean(err):.2f}%）")
    if max(err) > 10.0:
        print("    ★ 実力比% が能力値から再現できない。列か式のどちらかが誤り")
        bad += 1

    # --- §4.6 の ±8% -----------------------------------------------------
    print("  コスト帯ごとの ばらつき（§4.6 は ±8%以内）")
    for c in sorted({g["_cost"] for g in G}):
        v = [g["_power"] / c for g in G if g["_cost"] == c]
        m = st.mean(v)
        dev = max(abs(x - m) / m * 100 for x in v)
        mark = "  ★超過" if dev > 8.0 else ""
        print(f"    コスト{c:<3} {len(v)}枚  ±{dev:5.1f}%{mark}")
        if dev > 8.0:
            bad += 1

    # --- 配分によらず総価値が等しいか（設計の眼目） ------------------------
    A, B = affine_fit(G)
    print(f"  総合値 ≒ {A:.3f} + {B:.4f} × コスト  "
          f"（枠の基礎価値 + コスト比例分）")
    by = {}
    for c in sorted({g["_cost"] for g in G}):
        by[c] = st.mean(g["_power"] for g in G if g["_cost"] == c)
    plans = [("均等 5×6", [5] * 6), ("やや偏り 6/6/6/4/4/4", [6, 6, 6, 4, 4, 4]),
             ("偏り 7/7/4/4/4/4", [7, 7, 4, 4, 4, 4]),
             ("極端 10/10/4/2/2/2", [10, 10, 4, 2, 2, 2]),
             ("極端 10/9/8/1/1/1", [10, 9, 8, 1, 1, 1])]
    print("  6枠・合計コスト30 での総価値（配分によらず等しいのが設計の狙い）")
    vals = []
    for name, cs in plans:
        v = sum(by[c] for c in cs)
        vals.append(v)
        print(f"    {name:<22} {v:6.2f}")
    spread = (max(vals) - min(vals)) / st.mean(vals) * 100
    print(f"    → 幅 {spread:.1f}%（0% が理想。均等が最良で、偏るほど下がる）")

    print("\n" + ("検算 NG が {} 件".format(bad) if bad else "検算 OK"))
    return bad


if __name__ == "__main__":
    raise SystemExit(1 if check() else 0)


# ============================================================================
# field.py への橋渡し
# ============================================================================
#
# **実カードはバランス測定に使わない。** 実編成をサンプルした勝率パネルは飽和して
# 測れない（§13）。統制した合成カードで測る。ここで作るのは実況・リプレイ用である。

ROLE_MAP = {"耐久": "tank", "均衡": "bal", "火力": "dps",
            "瞬発": "dps", "支援": "bal"}

# 知力の暫定導出。**CSV に知力の列が無いので、役割から仮に置いている。**
# 本来はカードごとに手で置くべき値であり、これは足場にすぎない。
# 支援型（軍師）を高く、火力・瞬発型（猛将）を低くする。
WITS_BY_ROLE = {"支援": 1.45, "均衡": 1.05, "耐久": 0.85, "火力": 0.70, "瞬発": 0.65}
TYPE_MAP = {"歩兵": "inf", "騎兵": "cav", "弓兵": "arc"}


def to_cards(names=None):
    """武将名のリストから field.Card を作る。名前を省くと全80枚を返す。"""
    from . import field as F  # 遅延 import（rosterdata 単体でも検算できるように）
    idx = {g["名前"]: g for g in generals()}
    picks = [idx[n] for n in names] if names else list(idx.values())
    return [F.Card(cost=float(g["コスト"]), typ=TYPE_MAP[g["兵種"]],
                   role=ROLE_MAP[g["役割"]], name=g["名前"],
                   trait=g["固有特性"], faction=g["勢力"],
                   might=float(g["攻撃力"]),
                   wits=float(g["攻撃力"]) * WITS_BY_ROLE[g["役割"]],
                   skill=g["必殺技"], gauge_cost=float(g["消費ゲージ%"]),
                   gauge_rate=float(g["ゲージ上昇率"]) / 100.0,
                   gauge_init=float(g["初期ゲージ"])) for g in picks]


def load_skills_into_field() -> int:
    """必殺技の威力・種別・対象を field.py へ読み込む。"""
    from . import field as F
    n = 0
    for sk in skills():
        p = F._skill_power(sk["効果"])
        k = F._skill_kind(sk["効果"], sk["対象"])
        F.SKILL_INFO[sk["技名"]] = (p, k)
        F.SKILL_TARGET[sk["技名"]] = sk["対象"]
        n += 1
    return n


def by_cost(cost: int):
    return [g["名前"] for g in generals() if int(g["コスト"]) == cost]
