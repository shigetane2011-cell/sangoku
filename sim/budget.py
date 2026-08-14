#!/usr/bin/env python3
"""カードの能力値がコスト式どおりかを検算する（§4.6）。

コストと強さの関係は

    強さ(コスト) = 枠の基礎価値 + コスト比例分

である。枠の基礎価値があるため、**総合値/コストは低コストほど高くなるのが正しい**。
したがって「総合値/コストが全カードで一定か」を見てはいけない。見るのは
**各カードが自分のコストに対応する目標値からどれだけ外れているか**である。

v0.3 では総合値/コストを一定に揃えようとしていたが、これは枠の価値を勘定に
入れていなかったための誤りで、v0.5 で撤回した。

usage: python3 sim/budget.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roster import (BEHAVIOR_PREMIUM, ROSTER, TROOP, ability_premium,  # noqa: E402
                    effective_score, evade_of, value)

TOLERANCE = 8      # 目標値からのずれの許容幅（%）

# 人物名 → (必殺技ひな型, 特性キー)。cards.json には展開後の形しか残らないため、
# 検算には ROSTER 側の定義が要る。
ABILITY = {e[0]: (e[5], e[7]) for e in ROSTER}


def load():
    path = os.path.join(os.path.dirname(__file__), "cards.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def score(card, interval):
    """総合値 = 実効耐久 × 実効火力（命中率・クリティカル率込み）。"""
    return effective_score(card["hp"], card["atk"], card["dfn"],
                           interval, card["acc"], card["crit"],
                           evade_of(card["troop"]))


def main():
    data = load()
    intervals = {t: v["interval"] for t, v in data["troop_defaults"].items()}
    labels = {t: v["label"] for t, v in data["troop_defaults"].items()}

    rows = []
    for c in data["cards"]:
        s = score(c, intervals[c["troop"]])
        skill_key, traits = ABILITY[c["person"]]
        target = (value(c["cost"]) / BEHAVIOR_PREMIUM[c["troop"]]
                  / ability_premium(skill_key, traits))
        rows.append((c, s, round((s / target - 1) * 100)))

    worst = max(rows, key=lambda r: abs(r[2]))
    print(f"カード {len(rows)}枚 / 目標値からのずれ 最大 {abs(worst[2])}%"
          f"（{worst[0]['name']}）  → {'OK' if abs(worst[2]) <= TOLERANCE else 'NG'}")

    print(f"\n{'兵種':>4} {'枚数':>4} {'間隔':>4} {'平均ずれ':>8} {'最大ずれ':>8}")
    for troop in ("inf", "cav", "arc"):
        rs = [r for r in rows if r[0]["troop"] == troop]
        if not rs:
            continue
        avg = sum(r[2] for r in rs) // len(rs)
        mx = max(abs(r[2]) for r in rs)
        print(f"{labels[troop]:>4} {len(rs):>4} {intervals[troop]:>4} {avg:>7}% {mx:>7}%")

    print(f"\n{'コスト':>6} {'枚数':>4} {'目標値':>7} {'実測平均':>8} {'総合値/コスト':>12}")
    for cost in sorted({c["cost"] for c in data["cards"]}):
        rs = [r for r in rows if r[0]["cost"] == cost]
        avg = sum(r[1] for r in rs) // len(rs)
        print(f"{cost:>6} {len(rs):>4} {round(value(cost)):>7} {avg:>8} {avg // cost:>12}")

    off = [r for r in rows if abs(r[2]) > TOLERANCE]
    if off:
        print(f"\n許容外のカード {len(off)}枚")
        for c, s, d in sorted(off, key=lambda r: -abs(r[2]))[:10]:
            print(f"    {d:>+4}%  {c['name']}（コスト{c['cost']} / {labels[c['troop']]}）")
    else:
        print("\n全カードが許容内。")


if __name__ == "__main__":
    main()
