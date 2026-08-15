#!/usr/bin/env python3
"""強さの物差し。**まず物差しがゼロを指すか確かめてから使う。**

usage:
  python3 sim/measure.py zero     物差しの零点と分解能を確かめる（他より先にこれ）
  python3 sim/measure.py card     カード1枚のずれ（鏡像から1枚だけ差し替える）

----------------------------------------------------------------------------
なぜ balance.py の差し替え勝率を捨てたか
----------------------------------------------------------------------------

この戦闘モデルは**小さな差がふくらんで決着する**（§4.6・案D で受け入れた性質）。
増幅を消すと兵種の三すくみも一緒に消えるため、これは直せない。

したがって「実カードの数編成を相手にした勝率」は**構造的に飽和する**。
0% か 100% しか出ない物差しで羽根の重さを量ることになり、
「少し強い」と「かなり強い」の区別がつかない。

さらに balance.cmd_swap は、固定した基準編成と比べる形でカードを測っていながら、
**その基準編成が50%なのかを一度も確かめていなかった**。実測すると
低コスト戦91% / 中コスト戦58% / 高コスト戦36%、6相手のうち4〜5マスが
0か100に張り付いていた。目盛りがずれた物差しで全部を測っていたことになる。
（§13 の事例10・16）

----------------------------------------------------------------------------
この物差しの作り
----------------------------------------------------------------------------

1. **零点を測定不要にしない。測る。** 同一編成どうしは左右対称なので勝率は
   50%のはずだが、「はずだ」で済ませたのが今回の失敗である。毎回いっしょに測り、
   ずれはそこから引く。装置が自分の零点を報告しないなら、その測定は読まない。

2. **零点の近くだけを使う。** 増幅する物差しは 50% のあたりでしか目盛りが効かない。
   0や100へ張り付いた測定は「差が大きい」ではなく「読めていない」と扱う。

3. **参照編成は合成カードで作る。** 実カードだと役割・兵種・必殺技が交絡し、
   何を測ったのか言えなくなる（§5.3 の測定方法の注意と同じ理由）。

4. **同じコストで比べる。** 参照側の枠を測る札と同コストで作り直し、残りを
   他の5枠へ配るので両軍の合計コストは必ず一致する。ずれは「このコストの札として
   平均より強いか」であって、コスト差ではない。

----------------------------------------------------------------------------
この物差しで**まだ測れないもの**
----------------------------------------------------------------------------

**支援型の札は過小に出るはずである。** 参照編成の味方は無個性な合成カードなので、
味方を強くする効果は「強くする相手が平凡」という条件で測られる。実編成では
支援先が強い札になるため、価値はこれより高い。§13 に既出の「支援系の必殺技と
陣頭は現在の測定方法では測れない」と同じ制約で、この物差しでも解けていない。

したがって支援型の大きな負のずれは、**そのまま弱さと読んではいけない**。
実測例: 樊建〔伝令〕（コスト1・支援）が −30〜−44pt。同じ札が旧・採用率では
低コスト戦で42%と最多採用だった。**どちらかが嘘をついている。** 旧側には
サンプラの偏り（§13 の事例3）があり、こちら側には上記の制約がある。
先に決着させること。
"""
import math
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import roster
from engine import CARDS, SLOTS, Battle, make_team

REGULATIONS = {"low": ("低コスト戦", 18), "mid": ("中コスト戦", 30),
               "high": ("高コスト戦", 40)}


def play(team_a, team_b, seeds):
    """左右を入れ替えながら seeds 回戦わせ、(A勝ち, B勝ち, 引き分け) を返す。

    先攻はシードから決まるので、入れ替えないと同一編成でも50%にならない。
    """
    a = b = d = 0
    for s in range(seeds):
        seed = s * 7919 + 13
        if s % 2 == 0:
            r = Battle([team_a, team_b], seed=seed).run()
            w = r["winner"]
        else:
            r = Battle([team_b, team_a], seed=seed).run()
            w = None if r["winner"] is None else 1 - r["winner"]
        if w == 0:
            a += 1
        elif w == 1:
            b += 1
        else:
            d += 1
    return a, b, d


def rate(team_a, team_b, seeds):
    """勝率を百分率で返す。引き分けは0.5勝として数える（§8.2）。"""
    a, b, d = play(team_a, team_b, seeds)
    return (a + d / 2) * 100 / max(1, a + b + d)


def noise_2sigma(seeds):
    """完全に均衡していても出るばらつき（2σ, pt）。"""
    return 2 * 100 / math.sqrt(seeds)


# --- 参照編成 -------------------------------------------------------------

def neutral_costs(cap, slot=None, slot_cost=None):
    """コスト上限ちょうどに配分した6枠のコストを返す。

    slot/slot_cost を指定すると、その枠を指定コストに固定し、残りを他の5枠へ配る。
    **これで参照編成と差し替え編成の合計コストが必ず一致する。**
    一致しないと「コストが高いから強い」を測ってしまい、札の良し悪しが見えない。
    """
    if slot is None:
        base, extra = divmod(cap, 6)
        return [base + (1 if i < extra else 0) for i in range(6)]
    rest = cap - slot_cost
    if rest < 5:
        return None                      # 他の5枠が最低コスト1を割る。測れない
    base, extra = divmod(rest, 5)
    others = [base + (1 if i < extra else 0) for i in range(5)]
    out = others[:slot] + [slot_cost] + others[slot:]
    return out


def neutral_cards(costs, tag=""):
    """指定コストの、無個性な6枚を作る。

    必殺技も固有特性も持たせない。**測りたいもの以外を盤上に置かない。**
    役割は前衛に耐久・中列に均衡・後衛に火力を置く実戦の形へ合わせる
    （全員同じ役割にすると迂回の価値が中立化する。§5.3 の測定方法の注意）。
    """
    roles = ("tank", "tank", "bruiser", "bruiser", "dps", "dps")
    troops = ("inf", "inf", "cav", "cav", "arc", "arc")
    cards = []
    for i, (role, troop, cost) in enumerate(zip(roles, troops, costs)):
        person = f"参照{tag}{i}c{cost}"
        roster.ROMAJI[person] = f"ref{tag}{i}c{cost}"
        card = roster.build_card((person, "参照", cost, troop, role, "strike", []))
        CARDS[card["id"]] = card
        cards.append(card)
    return cards


def team_of(cards, commander=4):
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)],
            "commander": commander}


# --- 零点 -----------------------------------------------------------------

def zero_point(cap, seeds):
    """参照編成どうしを戦わせ、物差しの零点を返す。

    **50%だと仮定しない。** 仮定して測ったのが balance.cmd_swap の失敗である。
    """
    cards = neutral_cards(neutral_costs(cap))
    return rate(team_of(cards), team_of(cards), seeds)


def cmd_zero():
    print("=== 物差しの零点 ===")
    print("  同一編成どうしなので、左右対称なら 50.0% になるはず。")
    print("  **「はずだ」で済ませずに毎回測る。** これを確かめずに使ったのが")
    print("  差し替え勝率の失敗だった（§13 の事例16）。\n")
    for _key, (label, cap) in REGULATIONS.items():
        print(f"  [{label}] コスト上限 {cap}")
        for seeds in (200, 600, 1200):
            z = zero_point(cap, seeds)
            n = noise_2sigma(seeds)
            ok = "OK" if abs(z - 50) <= n else "**ずれている**"
            print(f"    {seeds:>5}戦  零点 {z:5.1f}%  "
                  f"（偶然のばらつき 2σ={n:4.1f}pt）  {ok}")
        print()


# --- カード1枚のずれ -------------------------------------------------------

def card_deviation(card_id, cap, seeds, slot=None):
    """参照編成の1枠を card_id へ差し替え、零点からのずれ(pt)を返す。

    **同じコストの無個性な札と入れ替える。** 参照編成側の同じ枠を
    card_id と同コストで作り直し、残りを他の5枠へ配るので、両軍の合計コストは
    必ず一致する。したがってこのずれは「このコストの札として、平均より強いか」を
    表す。コスト差ではない。

    戻り値は (ずれ, 零点, 差し替え後の勝率, 差し替えた枠)。
    **零点も同じ実行の中で測って返す。** 別の日に測った零点は使わない。
    """
    card = CARDS[card_id]
    if card["cost"] > cap - 5:
        return None                     # 他の5枠が成立しない
    if slot is None:
        # 兵種の合う枠を優先し、なければ中央寄りの枠を使う
        troops = ("inf", "inf", "cav", "cav", "arc", "arc")
        ok = [i for i in range(6) if troops[i] == card["troop"]] or list(range(6))
        slot = ok[len(ok) // 2]
    costs = neutral_costs(cap, slot, card["cost"])
    if costs is None:
        return None
    ref = neutral_cards(costs)
    trial = list(ref)
    trial[slot] = card
    z = rate(team_of(ref), team_of(ref), seeds)
    w = rate(team_of(trial), team_of(ref), seeds)
    return w - z, z, w, slot


def cmd_card():
    ids = sys.argv[2:] or ["kanu_10"]
    seeds = 400
    print("=== カード1枚のずれ ===")
    print(f"  参照編成の1枠を差し替え、零点からのずれを見る（{seeds}戦・"
          f"2σ={noise_2sigma(seeds):.1f}pt）")
    print("  **50%から遠い測定は「差が大きい」ではなく「読めていない」。**\n")
    for _key, (label, cap) in REGULATIONS.items():
        print(f"  [{label}]")
        for cid in ids:
            if cid not in CARDS:
                print(f"    {cid} は存在しない")
                continue
            out = card_deviation(cid, cap, seeds)
            if out is None:
                print(f"    {CARDS[cid]['name']}: コスト超過で入らない")
                continue
            dev, z, w, slot = out
            mark = "" if 5 <= w <= 95 else "  ← 飽和。読めていない"
            print(f"    {CARDS[cid]['name']:<20} 零点 {z:5.1f}% → {w:5.1f}% "
                  f"（ずれ {dev:+5.1f}pt・{slot}枠）{mark}")
        print()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "zero"
    table = {"zero": cmd_zero, "card": cmd_card}
    if cmd not in table:
        sys.exit(__doc__)
    table[cmd]()
