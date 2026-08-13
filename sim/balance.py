#!/usr/bin/env python3
"""バランス検証ハーネス（仕様書 v0.2.1 §4.6 の指標を実測する）。

usage:
  python3 sim/balance.py check       健全性（決定論・戦闘時間・決着理由・必殺技回数）
  python3 sim/balance.py troops      兵種三すくみが機能しているか
  python3 sim/balance.py swap        差し替え勝率（目標 47-53%）
  python3 sim/balance.py meta        採用率（目標は基準値の0.3〜2.5倍）
  python3 sim/balance.py commander   総大将の前衛配置と後衛配置の勝率差
  python3 sim/balance.py cost        コストの加算性（1点の価値がどこでも同じか）
  python3 sim/balance.py all         すべて
"""

import sys
from collections import Counter

from engine import CARDS, SLOTS, Battle, Rng, make_team

REGULATIONS = {"low": ("低コスト戦", 18), "mid": ("中コスト戦", 30), "high": ("高コスト戦", 40)}
ALL_IDS = sorted(CARDS)
SEEDS = 400          # 1条件あたりの乱数シード数


def play(team_a, team_b, seeds=SEEDS, battlefield="clear"):
    """同一編成同士を seeds 回戦わせ、(A勝ち, B勝ち, 引き分け) を返す。

    先攻の有利/不利を打ち消すため、半数は左右を入れ替えて戦わせる。
    """
    a = b = d = 0
    for s in range(seeds):
        if s % 2 == 0:
            r = Battle([team_a, team_b], seed=s * 7919 + 13).run()
            w = r["winner"]
        else:
            r = Battle([team_b, team_a], seed=s * 7919 + 13).run()
            w = None if r["winner"] is None else 1 - r["winner"]
        if w == 0:
            a += 1
        elif w == 1:
            b += 1
        else:
            d += 1
    return a, b, d


def winrate(team_a, team_b, seeds=SEEDS):
    a, b, d = play(team_a, team_b, seeds)
    return (a * 100 + d * 50) // max(1, a + b + d)


def random_team(rng, cap, commander_slot=None):
    """コスト上限を満たす6人編成をランダムに作る。超過分は安い札へ置換して修復する。"""
    ids = []
    pool = list(ALL_IDS)
    while len(ids) < 6:
        pick = pool.pop(rng.below(len(pool)))
        ids.append(pick)
    ids.sort(key=lambda c: -CARDS[c]["cost"])
    remaining = [c for c in ALL_IDS if c not in ids]
    remaining.sort(key=lambda c: CARDS[c]["cost"])
    while sum(CARDS[c]["cost"] for c in ids) > cap and remaining:
        cheapest = remaining.pop(0)
        if CARDS[cheapest]["cost"] < CARDS[ids[0]]["cost"]:
            ids[0] = cheapest
            ids.sort(key=lambda c: -CARDS[c]["cost"])
        else:
            break
    if sum(CARDS[c]["cost"] for c in ids) > cap:
        return None
    order = list(ids)
    for i in range(len(order) - 1, 0, -1):
        j = rng.below(i + 1)
        order[i], order[j] = order[j], order[i]
    cmd = commander_slot if commander_slot is not None else rng.below(6)
    return make_team(order, commander=cmd), order, cmd


def sample_teams(cap, count, seed=1):
    rng = Rng(seed)
    out = []
    guard = 0
    while len(out) < count and guard < count * 40:
        guard += 1
        t = random_team(rng, cap)
        if t:
            out.append(t)
    return out


# --- check ---------------------------------------------------------------

def cmd_check():
    print("=== 健全性チェック ===")
    teams = sample_teams(40, 12, seed=99)

    same = True
    for i in range(20):
        a = Battle([teams[0][0], teams[1][0]], seed=1000 + i).run()
        b = Battle([teams[0][0], teams[1][0]], seed=1000 + i).run()
        if a != b:
            same = False
    print(f"決定論（同一シードで同一結果）: {'OK' if same else 'NG'}")

    ticks, reasons, skills, timeouts = [], Counter(), [], 0
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            for s in range(6):
                bt = Battle([teams[i][0], teams[j][0]], seed=s * 131 + i * 17 + j)
                r = bt.run()
                ticks.append(r["ticks"])
                reasons[r["reason"]] += 1
                if r["ticks"] >= 900:
                    timeouts += 1
                skills.extend(u.skills for u in bt.units)
    n = len(ticks)
    ticks.sort()
    print(f"戦闘数: {n}")
    print(f"戦闘時間: 中央値 {ticks[n // 2] / 10:.1f}秒 / 平均 {sum(ticks) / n / 10:.1f}秒 "
          f"/ 最短 {ticks[0] / 10:.1f}秒 / 時間切れ {timeouts * 100 // n}%")
    print(f"必殺技の平均発動回数: {sum(skills) / len(skills):.2f} 回/部隊（§7.1 目標 1.2回）")
    print("決着理由:")
    for k, v in reasons.most_common():
        print(f"  {k:<20} {v * 100 // n:>3}%")


# --- troops --------------------------------------------------------------

def mono_team(troop, costs=(5, 5, 5, 5, 5, 5), role="bruiser"):
    """指定兵種だけの検証用編成を作る。

    実カードから選ぶと、コストを揃えても役割構成（耐久型か火力型か）が兵種ごとに
    偏り、三すくみではなく役割の差を測ってしまう。コスト・役割を固定した合成カードを
    使い、兵種以外の条件を完全に揃える。
    """
    import roster
    cards = []
    for i, c in enumerate(costs):
        entry = (f"検証{troop}{i}", "検証", c, troop, role, "strike", "検証", [])
        roster.ROMAJI[entry[0]] = f"t{troop}{i}"
        card = roster.build_card(entry)
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_troops():
    print("=== 兵種三すくみ ===")
    print("  コスト・役割を固定した合成カードで、兵種以外の条件を揃えて比較する。")
    label = {"inf": "歩兵", "cav": "騎兵", "arc": "弓兵"}
    for role in ("bruiser", "tank", "dps"):
        mono = {t: mono_team(t, role=role) for t in ("inf", "cav", "arc")}
        print(f"\n  [役割: {role}]")
        for a, b in (("inf", "cav"), ("cav", "arc"), ("arc", "inf")):
            wr = winrate(mono[a], mono[b], seeds=200)
            mark = "OK" if 55 <= wr <= 80 else ("NG(弱すぎ)" if wr < 55 else "NG(強すぎ)")
            print(f"    {label[a]}→{label[b]}  {wr:>3}%  {mark}")


# --- swap ----------------------------------------------------------------

def baseline(cap):
    """コスト上限に対して素直に組んだ基準編成を作る。"""
    picks = []
    total = 0
    for cid in sorted(ALL_IDS, key=lambda c: -CARDS[c]["cost"]):
        if len(picks) == 6:
            break
        left = 6 - len(picks) - 1
        cheapest = sorted(CARDS[c]["cost"] for c in ALL_IDS if c not in picks)[:left]
        if total + CARDS[cid]["cost"] + sum(cheapest) <= cap:
            picks.append(cid)
            total += CARDS[cid]["cost"]
    for cid in sorted(ALL_IDS, key=lambda c: CARDS[c]["cost"]):
        if len(picks) == 6:
            break
        if cid not in picks and total + CARDS[cid]["cost"] <= cap:
            picks.append(cid)
            total += CARDS[cid]["cost"]
    return picks


def cmd_swap():
    print("=== 差し替え勝率（§4.6 目標 47-53%）===")
    for key, (label, cap) in REGULATIONS.items():
        base = baseline(cap)
        opponents = [t[0] for t in sample_teams(cap, 6, seed=555)]
        print(f"\n[{label}] 基準編成: {'/'.join(CARDS[c]['name'] for c in base)} "
              f"(合計{sum(CARDS[c]['cost'] for c in base)}/{cap})")
        # 候補ごとに「コストが最も近い基準枠」と入れ替える。プレイヤーが実際に行う
        # 差し替えに近く、全枠×全候補の総当たりより結果が読みやすい。
        rows = []
        for cand in ALL_IDS:
            if cand in base:
                continue
            options = []
            for slot in range(6):
                trial = list(base)
                trial[slot] = cand
                if sum(CARDS[c]["cost"] for c in trial) <= cap:
                    options.append((abs(CARDS[base[slot]]["cost"] - CARDS[cand]["cost"]), slot))
            if not options:
                continue
            slot = min(options)[1]
            trial = list(base)
            trial[slot] = cand
            team = make_team(trial, commander=4)
            total = sum(winrate(team, o, seeds=60) for o in opponents) // len(opponents)
            rows.append((total, CARDS[base[slot]]["name"], CARDS[cand]["name"]))
        rows.sort()
        out_of_range = [r for r in rows if r[0] < 47 or r[0] > 53]
        print(f"  差し替え {len(rows)} 通り / 目標帯(47-53%)から外れ {len(out_of_range)} 件 "
              f"({len(out_of_range) * 100 // max(1, len(rows))}%)")
        for wr, o, i in rows[:3]:
            print(f"    最低 {wr:>3}%  {o} → {i}")
        for wr, o, i in rows[-3:]:
            print(f"    最高 {wr:>3}%  {o} → {i}")


# --- meta ----------------------------------------------------------------

def cmd_meta():
    print("=== 採用率（§4.6 目標 基準値の0.3〜2.5倍）===")
    for key, (label, cap) in REGULATIONS.items():
        teams = sample_teams(cap, 32, seed=2026)
        scores = [0] * len(teams)
        for i in range(len(teams)):
            for j in range(i + 1, len(teams)):
                a, b, d = play(teams[i][0], teams[j][0], seeds=8)
                scores[i] += a * 2 + d
                scores[j] += b * 2 + d
        ranked = sorted(range(len(teams)), key=lambda i: -scores[i])
        top = ranked[:len(teams) // 4]
        freq = Counter()
        for i in top:
            freq.update(teams[i][1])
        print(f"\n[{label}] 上位{len(top)}編成の採用率")
        over = [c for c in ALL_IDS if freq[c] * 100 // len(top) > 30]
        under = [c for c in ALL_IDS if freq[c] * 100 // len(top) < 3]
        for cid, n in freq.most_common(5):
            print(f"    {n * 100 // len(top):>3}%  {CARDS[cid]['name']} (コスト{CARDS[cid]['cost']})")
        print(f"  上限超過(>30%) {len(over)}枚 / 下限割れ(<3%) {len(under)}枚")
        if under:
            print(f"    下限割れ: {', '.join(CARDS[c]['name'] for c in under[:8])}")


# --- commander -----------------------------------------------------------

def has_vanguard(cid):
    return "vanguard" in CARDS[cid].get("traits", [])


def cmd_commander():
    print("=== 総大将の配置 ===")
    print("  同一編成で総大将だけを前衛・中/後衛・中に変えて勝率を比較する。")
    print("  後衛有利は仕様として受け入れる。固有特性「陣頭」を持つ武将だけ、")
    print("  前衛配置が後衛配置を上回ることを確認する（§4.2）。")
    for key, (label, cap) in REGULATIONS.items():
        base = baseline(cap)
        opponents = [t[0] for t in sample_teams(cap, 6, seed=777)]
        print(f"\n  [{label}]")
        # 総大将にする札を、陣頭を持つもの/持たないものからそれぞれ選ぶ
        picks = []
        plain = next((i for i, c in enumerate(base) if not has_vanguard(c)), None)
        van = next((i for i, c in enumerate(base) if has_vanguard(c)), None)
        if plain is not None:
            picks.append(("陣頭なし", plain))
        if van is not None:
            picks.append(("陣頭あり", van))
        else:
            print("    ※ 基準編成に陣頭持ちがいないため、1枠を差し替えて測定する")
            cand = next((c for c in ALL_IDS if has_vanguard(c) and c not in base
                         and sum(CARDS[x]["cost"] for x in base) - CARDS[base[0]]["cost"]
                         + CARDS[c]["cost"] <= cap), None)
            if cand:
                base = [cand] + base[1:]
                picks.append(("陣頭あり", 0))
        for tag, slot in picks:
            order = list(base)
            order[0], order[slot] = order[slot], order[0]   # 対象を先頭へ
            front = make_team(order, commander=0)           # 前衛・左
            back_order = list(order)
            back_order[0], back_order[3] = back_order[3], back_order[0]
            back = make_team(back_order, commander=3)       # 後衛・左
            wf = sum(winrate(front, o, seeds=60) for o in opponents) // len(opponents)
            wb = sum(winrate(back, o, seeds=60) for o in opponents) // len(opponents)
            gap = wf - wb
            name = CARDS[order[0]]["name"]
            verdict = "前衛が有利" if gap > 3 else ("後衛が有利" if gap < -3 else "拮抗")
            print(f"    {tag}（{name}）: 前衛 {wf}% / 後衛 {wb}%  差 {gap:+d}pt → {verdict}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    table = {"check": cmd_check, "troops": cmd_troops, "swap": cmd_swap,
             "meta": cmd_meta, "commander": cmd_commander, "cost": cmd_cost}
    if cmd == "all":
        for fn in (cmd_check, cmd_troops, cmd_commander, cmd_swap, cmd_meta):
            fn()
            print()
    elif cmd in table:
        table[cmd]()
    else:
        sys.exit(__doc__)


# --- cost ----------------------------------------------------------------

def vanilla(cost, troop="inf", cid=None):
    """コストだけから能力値を決めた検証用の無個性カード。

    実カードは兵種・必殺技・特性・役割が混ざるため、コストと強さの関係だけを
    見たいときはこれを使う。能力値は roster.py と同じコスト式から導出する。
    """
    import roster
    person = cid or f"検証{troop}{cost}"
    roster.ROMAJI[person] = (cid or f"v{troop}{cost}")
    card = roster.build_card((person, "検証", cost, troop, "bruiser", "strike", "検証", []))
    card["skill"] = {"name": "なし", "target": "self", "effects": []}
    return card


def cmd_cost():
    print("=== コストの加算性 ===")
    print("  コスト1点の価値が、どこに配分しても同じかを見る。")
    print("  無個性カード（兵種・必殺技・特性なし）を使い、コスト以外の差を排除する。\n")

    # (1) 1枠内の線形性: コスト a+b の1枚 と、コスト a の1枚＋コスト b ぶんの
    #     強化（アイテム相当）が釣り合うか。ここでは 6枠すべてで比較する。
    print("  [1] 同じ合計コストを6枠へ均等に配る場合と、偏らせる場合")
    CAP = 30
    dists = {
        "均等 5-5-5-5-5-5": [5, 5, 5, 5, 5, 5],
        "やや偏り 8-6-5-4-4-3": [8, 6, 5, 4, 4, 3],
        "強い偏り 10-8-4-3-3-2": [10, 8, 4, 3, 3, 2],
        "極端 10-10-4-2-2-2": [10, 10, 4, 2, 2, 2],
    }
    teams = {}
    for label, costs in dists.items():
        assert sum(costs) == CAP, (label, sum(costs))
        cards = [vanilla(c, "inf", cid=f"{label}-{i}") for i, c in enumerate(costs)]
        for c in cards:
            CARDS[c["id"]] = c
        teams[label] = {"units": [{"card": c, "lane": l, "row": r}
                                  for c, (r, l) in zip(cards, SLOTS)], "commander": 3}
    labels = list(teams)
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            wr = winrate(teams[a], teams[b], seeds=200)
            mark = "OK" if 45 <= wr <= 55 else "NG"
            print(f"    {a:<20} 対 {b:<20} {wr:>3}%  {mark}")

    # (2) 2人ぶんと1人ぶん: 6人固定では起きないが、アイテムにコストを持たせると
    #     「1枠にコスト n ぶん」の比較になるため、人数を変えて素の加算性を測る。
    print("\n  [2] コスト1+2 の2人 対 コスト3 の1人（人数を変えた参考測定）")
    for pair, single in [((1, 2), 3), ((2, 3), 5), ((3, 4), 7)]:
        many = [vanilla(pair[0], "inf", cid=f"m{pair[0]}a"), vanilla(pair[1], "inf", cid=f"m{pair[1]}b")]
        one = [vanilla(single, "inf", cid=f"s{single}")]
        for c in many + one:
            CARDS[c["id"]] = c
        # 2人は同じレーンの前衛・後衛へ置く。別レーンに置くと相手のいないレーンで
        # 支援移動の時間を空費し、コストではなく配置の差を測ってしまう。
        ta = {"units": [{"card": many[0], "lane": 0, "row": "front"},
                        {"card": many[1], "lane": 0, "row": "back"}], "commander": 0}
        tb = {"units": [{"card": one[0], "lane": 0, "row": "front"}], "commander": 0}
        wr = winrate(ta, tb, seeds=200)
        mark = "OK" if 45 <= wr <= 55 else "NG"
        print(f"    コスト{pair[0]}+{pair[1]} の2人 対 コスト{single} の1人: {wr:>3}%  {mark}")


if __name__ == "__main__":
    main()
