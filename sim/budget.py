#!/usr/bin/env python3
"""兵種ごとのステータス予算を検算する。

同じコストなら、兵種が違っても「実効耐久 × 火力」の総合値は揃っている必要がある。
揃っていないと、三すくみの補正や射程の差を調整しても兵種の優劣が動かない。

v0.3 の初期データでは総合値/コストが 歩兵556 / 騎兵701 / 弓兵304 と大きく割れており、
弓兵が歩兵・騎兵の両方に勝率0%という結果になっていた。カード追加のたびに本ツールで
確認する。

usage: python3 sim/budget.py
"""

import json
import os

TOLERANCE = 8      # 総合値/コストの許容ぶれ幅（%）


def load():
    path = os.path.join(os.path.dirname(__file__), "cards.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def effective_hp(card):
    """防御力によるダメージ軽減を織り込んだ実質的な耐久。"""
    return card["hp"] * (100 + card["dfn"]) // 100


def dps(card, interval):
    """1秒あたりの理論火力（×100）。"""
    return card["atk"] * 100 // interval


def main():
    data = load()
    intervals = {t: v["interval"] for t, v in data["troop_defaults"].items()}
    labels = {t: v["label"] for t, v in data["troop_defaults"].items()}

    print(f"{'兵種':>4} {'枚数':>4} {'間隔':>4} {'実効耐久/コスト':>14} "
          f"{'火力/コスト':>11} {'総合/コスト':>11}")
    totals = {}
    for troop in ("inf", "cav", "arc"):
        cards = [c for c in data["cards"] if c["troop"] == troop]
        cost = sum(c["cost"] for c in cards)
        eh = sum(effective_hp(c) for c in cards)
        dp = sum(dps(c, intervals[troop]) for c in cards)
        product = sum(effective_hp(c) * dps(c, intervals[troop]) // 1000 for c in cards)
        totals[troop] = product // cost
        print(f"{labels[troop]:>4} {len(cards):>4} {intervals[troop]:>4} "
              f"{eh // cost:>14} {dp // cost:>11} {product // cost:>11}")

    lo, hi = min(totals.values()), max(totals.values())
    spread = (hi - lo) * 100 // max(1, lo)
    print(f"\n総合値/コストのぶれ: {spread}%（許容 {TOLERANCE}%以内）"
          f" → {'OK' if spread <= TOLERANCE else 'NG: 兵種間の予算が揃っていない'}")

    print("\n個別カードの総合値/コスト（同コスト帯で大きく外れる札は要確認）")
    rows = []
    for c in data["cards"]:
        score = effective_hp(c) * dps(c, intervals[c["troop"]]) // 1000 // c["cost"]
        rows.append((c["tier"], -score, c["name"], c["cost"], labels[c["troop"]], score))
    order = {"high": 0, "mid": 1, "low": 2}
    rows.sort(key=lambda r: (order[r[0]], r[1]))
    tier = None
    for t, _, name, cost, troop, score in rows:
        if t != tier:
            tier = t
            print(f"  [{ {'high': '高', 'mid': '中', 'low': '低'}[t] }コスト帯]")
        print(f"    {score:>5}  {name}（コスト{cost} / {troop}）")


if __name__ == "__main__":
    main()
