"""勝敗条件を差し替えて、コストが部隊単位で通分できるかを測る。

総大将撤退で決まる限り、コストは部隊単位で通分できない。総大将のいるレーンだけが
特別に重くなるためである（実測: 同じ移動でも総大将の位置で 0.3% / 45.2% / 93.8%）。

差し替える候補は、どれも**和で書けるもの**にする。和なら6枠の配分によらないので、
通分が成立する見込みがある。
  全滅のみ    : 相手を全員倒す。決まらなければ時間切れで残存兵力
  潰走 X%     : 残存兵力率が X% を割ったら負け（和で書ける・早く終わる）

usage: python3 sim/winrule_probe.py

実測（総コスト30固定・全員 歩兵均衡役・600戦・2σ=8.2pt）:

  勝敗条件        レーンをまたぐ 6/4・7/3・9/1   同じレーン内        決着
  総大将撤退      28.2 / 10.5 /  0.2%          47.2 / 50.3 / 50.5%  89.0秒
  全滅のみ        48.7 / 48.8 / 49.7%          43.8 / 46.7 / 41.5%  89.9秒（時間切れ91%）
  潰走 20%       50.6 / 44.9 / 45.2%          50.3 / 44.9 / 45.5%  77.9秒（潰走86%）
  潰走 35%       50.1 / 40.4 / 35.1%          56.6 / 57.1 / 63.7%  67.2秒
  潰走 50%       46.8 / 40.0 / 28.7%          48.7 / 41.5 / 33.6%  56.1秒

**潰走20%は、またごうが同じレーン内だろうが同じ値になる。** どこへコストを
移しても結果が変わらないことが、加法性が成立している署名である。

早く決着させるほど加法性は壊れる（潰走50%は56秒で終わるが 28.7% まで落ちる）。
攻防が均される前に決まるためで、決着の速さと通分は直接のトレードオフになる。

**この測定は必殺技も特性も無い一様なカードによる。** 実カードなら決着は
60秒台なので、閾値は再較正が要る。
"""
import sys
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import engine
import roster
from engine import CARDS, Battle
from measure import rate, noise_2sigma

N = 600
SLOTS = [("front", 0), ("front", 1), ("front", 2),
         ("back", 0), ("back", 1), ("back", 2)]
BASE_JUDGE = Battle.judge
ROUT = 0


def judge_no_commander(self):
    """総大将を見ない。全滅、または残存兵力率が ROUT を割ったら負け。"""
    lost = []
    for side in (0, 1):
        if not self.alive(side) or (ROUT and self.remaining_rate(side) < ROUT):
            lost.append(side)
    if len(lost) == 2:
        return self.finish(None, "同時成立")
    if lost:
        return self.finish(1 - lost[0], "全滅" if not self.alive(lost[0]) else "潰走")
    return None


def uniform(costs, tag, commander=4):
    cards = []
    for i, c in enumerate(costs):
        p = f"勝{tag}{i}c{c}"
        roster.ROMAJI[p] = f"wn{tag}{i}c{c}"
        card = roster.build_card((p, "勝", c, "inf", "bruiser", "strike", []))
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": commander}


def probe(label, a, b):
    ref = uniform([5] * 6, f"r{label[:4]}{a}{b}")
    z = rate(ref, ref, N)
    out = []
    for t in (1, 2, 4):
        costs = [5] * 6
        costs[a] += t
        costs[b] -= t
        trial = uniform(costs, f"{label[:4]}{a}{b}{t}")
        out.append(f"{costs[a]}/{costs[b]} {rate(trial, ref, N):5.1f}%")
    print(f"    {label:<26} 零点 {z:4.1f}%   " + "  ".join(out))


def health(label):
    """決着時間と決着理由。勝敗条件を変えたら必ず見る。"""
    ref = uniform([5] * 6, f"h{label[:4]}")
    ticks, reasons = 0, {}
    for s in range(200):
        r = Battle([ref, ref], s * 7919 + 13).run()
        ticks += r["ticks"]
        reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1
    top = sorted(reasons.items(), key=lambda x: -x[1])[:3]
    body = " / ".join(f"{k} {v * 100 // 200}%" for k, v in top)
    print(f"    {'決着':<26} {ticks / 200 / 10:.1f}秒   {body}")


print(f"総コスト30固定・全員 歩兵均衡役・{N}戦（2σ={noise_2sigma(N):.1f}pt）")
print("枠0（前衛L0）→ 枠1（前衛L1）＝総大将(枠4)のいるレーンの盾を薄くする\n")
print("  [いまのまま: 総大将撤退で決着]")
probe("レーンをまたぐ", 0, 1)
probe("同じレーン内", 0, 3)
health("いまのまま")
Battle.judge = judge_no_commander
for rout in (0, 200, 350, 500):
    name = "全滅のみ" if not rout else f"潰走 {rout // 10}%"
    ROUT = rout
    print(f"\n  [{name}]")
    probe("レーンをまたぐ", 0, 1)
    probe("同じレーン内", 0, 3)
    health(name)
Battle.judge = BASE_JUDGE
