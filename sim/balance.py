#!/usr/bin/env python3
"""バランス検証ハーネス（仕様書 v0.2.1 §4.6 の指標を実測する）。

usage:
  python3 sim/balance.py check       健全性（決定論・戦闘時間・決着理由・必殺技回数）
  python3 sim/balance.py troops      兵種三すくみが機能しているか
  python3 sim/balance.py swap        差し替え勝率（目標 47-53%）
  python3 sim/balance.py meta        採用率（目標は基準値の0.3〜2.5倍）
  python3 sim/balance.py commander   総大将の前衛配置と後衛配置の勝率差
  python3 sim/balance.py cost        コストの加算性（1点の価値がどこでも同じか）
  python3 sim/balance.py skills      必殺技のひな型ごとの強さ
  python3 sim/balance.py traits      誘発型の固有特性ごとの強さ
  python3 sim/balance.py formations  陣形×構成の総当たり
  python3 sim/balance.py support     1枚だけ混ぜたときの寄与（支援技の価格付け）
  python3 sim/balance.py exploit     攻略探索（最強編成を探し、収束するか循環するか）
  python3 sim/balance.py sensitivity 総合値の差と勝率の差の換算率
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


def print_next(name, values):
    """次の反復で roster.py へ貼る補正表を出力する（§7.5）。

    効果の価値は勝率でしか測れないが、予算は総合値で表されている。両者の関係は
    非線形なので一発で当てられない。測る → 貼る → 測る、を残差が消えるまで繰り返す。
    """
    body = ", ".join(f'"{k}": {values[k]:.2f}'
                     for k in sorted(values, key=lambda k: -values[k]))
    print(f"\n  次の反復で roster.py へ貼る値:\n    {name} = {{{body}}}")


def random_team(rng, cap, commander_slot=None):
    """コスト上限を満たす6人編成をランダムに作る。

    **超過分を最安札へ置換してはいけない。** 以前はそうしていたが、低コスト戦では
    ランダムな6枚の合計が32前後になり上限18をほぼ必ず超えるため、置換が毎回走って
    最安の札が機械的に詰め込まれていた。実測で樊建〔伝令〕が40編成中40に入っていた。
    採用率でコスト1の支援武将が87〜100%だったのは強さではなくこの偏りで、
    **指標が測っていたのはサンプラの癖だった**。

    代わりに1枚ずつ引く。残り枠を最安で埋めても上限に収まる札だけを候補にすれば、
    修復が要らず、特定の札が優先されることもない。
    """
    ids = []
    for _ in range(6):
        left = 6 - len(ids) - 1
        spent = sum(CARDS[c]["cost"] for c in ids)
        pool = sorted((c for c in ALL_IDS if c not in ids),
                      key=lambda c: (CARDS[c]["cost"], c))
        costs = [CARDS[c]["cost"] for c in pool]
        cands = [c for i, c in enumerate(pool)
                 if spent + costs[i] + sum((costs[:i] + costs[i + 1:])[:left]) <= cap]
        if not cands:
            return None
        ids.append(cands[rng.below(len(cands))])
    # 余ったコストを使い切る方向へ持ち上げる。1枚ずつ引くだけだと「残り枠を最安で
    # 埋めても収まる」制約が毎回かかるため中位の札に寄り、高コスト戦でも合計が
    # 上限40に対して31までしか伸びなかった。実プレイでは上限まで使うのが普通で、
    # そのままだと高コストの札の採用率を過小に見積もる。
    for _ in range(12):
        slot = rng.below(6)
        spent = sum(CARDS[c]["cost"] for c in ids) - CARDS[ids[slot]]["cost"]
        up = [c for c in ALL_IDS if c not in ids
              and CARDS[c]["cost"] > CARDS[ids[slot]]["cost"]
              and spent + CARDS[c]["cost"] <= cap]
        if up:
            ids[slot] = up[rng.below(len(up))]
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


def mixed_team(troop, roles=("tank", "tank", "bruiser", "bruiser", "dps", "dps")):
    """役割を混ぜた単一兵種の編成。**三すくみはこれで測る。**

    全員を同じ役割で揃えると、後衛も前衛と同じ硬さになり、騎兵が迂回して後衛を
    潰す価値が中立になってしまう。実際の編成は後衛に火力役（柔らかい札）が並ぶため、
    迂回はそこを直撃する。単一役割で測っていたときは孤立の罰 +30% で足りて
    見えたが、混成で測ると +30% でも +70% でも歩兵→騎兵は 0% のままだった。
    """
    import roster
    cards = []
    for i, role in enumerate(roles):
        person = f"混{troop}{role}{i}"
        roster.ROMAJI[person] = f"x{troop}{role}{i}"
        card = roster.build_card((person, "検証", 5, troop, role, "strike", "検証", []))
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_troops():
    print("=== 兵種三すくみ ===")
    label = {"inf": "歩兵", "cav": "騎兵", "arc": "弓兵"}
    print("  [役割を混ぜた編成 — こちらが本番の指標]")
    print("  耐久2・均衡2・火力2。実際の編成と同じく後衛に火力役が並ぶ。")
    mixed = {t: mixed_team(t) for t in ("inf", "cav", "arc")}
    for a, b in (("inf", "cav"), ("cav", "arc"), ("arc", "inf")):
        wr = winrate(mixed[a], mixed[b], seeds=200)
        mark = "OK" if 55 <= wr <= 80 else ("NG(弱すぎ)" if wr < 55 else "NG(強すぎ)")
        print(f"    {label[a]}→{label[b]}  {wr:>3}%  {mark}")

    print("\n  [役割を揃えた編成 — 参考。役割が相性へどれだけ効くかを見る]")
    print("  揃えると後衛も前衛と同じ硬さになり、迂回の価値が中立になるので")
    print("  本番の指標にはできない。帯外でも直ちに問題とは限らない。")
    for role in ("bruiser", "tank", "dps"):
        mono = {t: mono_team(t, role=role) for t in ("inf", "cav", "arc")}
        cells = []
        for a, b in (("inf", "cav"), ("cav", "arc"), ("arc", "inf")):
            cells.append(f"{label[a]}→{label[b]} {winrate(mono[a], mono[b], seeds=200):>3}%")
        print(f"    {ROLE_LABEL[role]:<4} " + "  ".join(cells))


ROLE_LABEL = {"tank": "耐久", "bruiser": "均衡", "dps": "火力"}


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
    # 母数を増やすことが要点。**上位8編成では下限割れが測れない。**
    # 8編成×6枠=48枚の抽選しかないので、完全に均衡していても 60×(1-6/60)^8 ≒ 26枚が
    # 1度も出ない。実測の下限割れ34枚のうち26枚はノイズだった。
    # 上位30編成なら 60×0.9^30 ≒ 2.5枚まで下がり、信号が読める。
    # 総当たりは編成数の二乗で効くので、固定の対戦相手への成績で順位付けする。
    for key, (label, cap) in REGULATIONS.items():
        teams = sample_teams(cap, 120, seed=2026)
        panel = [t[0] for t in sample_teams(cap, 12, seed=99991)]
        scores = []
        for t in teams:
            a = sum(play(t[0], o, seeds=8)[0] for o in panel)
            scores.append(a)
        ranked = sorted(range(len(teams)), key=lambda i: -scores[i])
        top = ranked[:30]
        freq = Counter()
        for i in top:
            freq.update(teams[i][1])
        chance = round(len(ALL_IDS) * (1 - 6 / len(ALL_IDS)) ** len(top))
        print(f"\n[{label}] {len(teams)}編成から上位{len(top)}編成の採用率"
              f"（均衡していても偶然 約{chance}枚は0%になる）")
        over = [c for c in ALL_IDS if freq[c] * 100 // len(top) > 30]
        under = [c for c in ALL_IDS if freq[c] * 100 // len(top) < 3]
        for cid, n in freq.most_common(5):
            print(f"    {n * 100 // len(top):>3}%  {CARDS[cid]['name']} (コスト{CARDS[cid]['cost']})")
        print(f"  上限超過(>30%) {len(over)}枚 / 下限割れ(<3%) {len(under)}枚")
        if under:
            print(f"    下限割れ: {', '.join(CARDS[c]['name'] for c in under[:8])}")
        # 下限割れが何に偏っているかを出す。名前を8枚並べても傾向は読めない。
        # 特定の兵種・役割に偏っていれば、それは個々の武将ではなく価格付けの問題。
        for key, label2 in (("troop", "兵種"), ("role", "役割")):
            pool = Counter(CARDS[c][key] for c in ALL_IDS)
            hit = Counter(CARDS[c][key] for c in under)
            cells = " ".join(f"{k}{hit[k]:>2}/{pool[k]:<2}({hit[k]*100//pool[k]:>3}%)"
                             for k in sorted(pool))
            print(f"    下限割れの{label2}別: {cells}")


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



# --- skills --------------------------------------------------------------

def skill_team(skill_key, cost=5, troop="inf", role="bruiser"):
    """全員が同じ必殺技を持つ検証用編成。必殺技以外の条件を完全に揃える。"""
    import roster
    cards = []
    for i in range(6):
        person = f"技{skill_key}{i}"
        roster.ROMAJI[person] = f"s{skill_key}{i}"
        card = roster.build_card((person, "検証", cost, troop, role, skill_key, skill_key, []))
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_skills():
    """必殺技のひな型ごとの強さを総当たりで測る。

    能力値はコスト式で揃うが、必殺技の効果量は手で置いている。ここが揃っていないと、
    編成の勝敗は必殺技の引きで決まってしまう。
    """
    import roster
    print("=== 必殺技のひな型ごとの強さ ===")
    print("  コスト5・歩兵・均衡役で揃え、必殺技だけを変えた編成同士を総当たりさせる。")
    skipped = sorted(roster.UNPRICED_SKILLS & set(roster.SKILLS))
    if skipped:
        print(f"  除外（このハーネスでは測れない）: {'、'.join(skipped)}")
    print()
    keys = [k for k in sorted(roster.SKILLS) if k not in roster.UNPRICED_SKILLS]
    teams = {k: skill_team(k) for k in keys}
    scores = {k: [] for k in keys}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            wr = winrate(teams[a], teams[b], seeds=80)
            scores[a].append(wr)
            scores[b].append(100 - wr)
    ranked = sorted(keys, key=lambda k: -sum(scores[k]) / len(scores[k]))
    print(f"  {'ひな型':<10} {'平均勝率':>8} {'現補正':>7} {'次補正':>7}  {'効果':<40}")
    nxt = {}
    for k in ranked:
        avg = sum(scores[k]) / len(scores[k])
        cur = roster.SKILL_ADJUST.get(k, 0.0)
        nxt[k] = roster.next_adjust(cur, avg)
        eff = roster.SKILLS[k]
        desc = " / ".join(f"{e['type']}" + (f"({e.get('power') or e.get('value') or e.get('duration')})")
                          for e in eff["effects"])
        print(f"  {k:<10} {avg:>7.0f}% {cur:>+7.2f} {nxt[k]:>+7.2f}  {eff['target']} → {desc}")
    lo, hi = min(sum(scores[k]) / len(scores[k]) for k in keys), max(sum(scores[k]) / len(scores[k]) for k in keys)
    print(f"\n  最弱 {lo:.0f}% 〜 最強 {hi:.0f}%（幅 {hi - lo:.0f}pt）"
          f" → {'OK' if hi - lo <= 30 else 'NG: 必殺技の強さが揃っていない'}")
    print_next("SKILL_ADJUST", nxt)



# --- traits --------------------------------------------------------------

def trait_team(trait_key=None, cost=5, troop="inf", role="bruiser", skill="strike",
               rot=0):
    """全員が同じ固有特性を持つ検証用編成。特性以外の条件を完全に揃える。

    勢力は魏・蜀・呉を2枚ずつ割り当てる。対抗能力（vs_wei など）は相手の勢力が
    一致したときだけ働くので、**相手に勢力が無いと測れない**。

    **勢力をレーンと揃えてはいけない。** `[i % 3]` で配ると lane0=魏・lane1=蜀・
    lane2=呉 となり、戦闘はレーン内で完結するため特効は1レーンでしか働かない。
    そのうえ総大将は slot 4（lane1）なので、蜀特効だけが総大将のレーンに当たり、
    機構的に同一なはずの3つが 97% / 35% / 24% に割れた。
    前後で勢力をずらして各レーンに2勢力を入れ、さらに rot で全体を回す。
    rot=0,1,2 の平均を採れば総大将の勢力による偏りも消える。
    """
    import roster
    cards = []
    order = ("wei", "shu", "go")
    for i in range(6):
        person = f"特{trait_key or 'none'}{i}r{rot}"
        roster.ROMAJI[person] = f"x{trait_key or 'none'}{i}r{rot}"
        # 前衛は 0,1,2 / 後衛は 1,2,0 の順に配り、レーンごとに2勢力を混ぜる
        roster.FACTION_OF[person] = order[(i % 3 + i // 3 + rot) % 3]
        traits = [trait_key] if trait_key else []
        card = roster.build_card((person, "検証", cost, troop, role, skill, "検証", traits))
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_traits():
    """誘発型の固有特性ごとの強さを、特性なしの編成と比べて測る。

    必殺技と同じく、特性の価値もコスト予算（§4.6）に含める必要がある。
    含めるにはまず「どれだけ強いか」を数値で知る必要がある。
    """
    import roster
    print("=== 誘発型の固有特性ごとの強さ ===")
    print("  コスト5・歩兵・均衡役・必殺技 strike で揃え、特性だけを変えて")
    print("  「特性なし」の編成と戦わせる。50%なら価値ゼロ。\n")
    keys = sorted(roster.TRIGGERS) + sorted(roster.COUNTERS)
    rows = []
    for k in keys:
        # 勢力の配り方を3通り回して平均する。総大将の勢力による偏りを消すため。
        wr = sum(winrate(trait_team(k, rot=r), trait_team(None, rot=r), seeds=120)
                 for r in range(3)) // 3
        rows.append((wr, k))
    rows.sort(reverse=True)
    print(f"  {'特性':<10} {'対 特性なし':>10} {'現補正':>7} {'次補正':>7}  {'条件':<16} {'上限':>4}")
    nxt = {}
    for wr, k in rows:
        cur = roster.TRAIT_ADJUST.get(k, 0.0)
        nxt[k] = roster.next_adjust(cur, wr)
        if k in roster.COUNTERS:
            name = roster.FACTION_LABEL[roster.COUNTERS[k]] + "特効"
            cond, lim = f"敵が{roster.FACTION_LABEL[roster.COUNTERS[k]]}", "常在"
        else:
            tr = roster.TRIGGERS[k]
            name, cond, lim = tr["name"], tr["trigger"], str(tr.get("limit", 1))
        print(f"  {name:<10} {wr:>9}% {cur:>+7.2f} {nxt[k]:>+7.2f}  {cond:<16} {lim:>4}")
    lo, hi = rows[-1][0], rows[0][0]
    print(f"\n  最弱 {lo}% 〜 最強 {hi}%（幅 {hi - lo}pt）"
          f" → {'OK' if hi - lo <= 20 else 'NG: 特性の価値が揃っていない'}")
    print_next("TRAIT_ADJUST", nxt)



# --- formations ----------------------------------------------------------

def formation_team(formation, mix, cost=5):
    """陣形と兵種構成を指定した検証用編成。

    mix は前衛から順に割り当てる (兵種, 役割) の並び。陣形の枠順は
    engine.FORMATIONS が持つ。総大将は最も安全な枠（最後尾）へ置く。
    """
    import roster
    from engine import FORMATIONS
    slots = FORMATIONS[formation]["slots"]
    cards = []
    for i, (troop, role) in enumerate(mix):
        person = f"陣{formation}{troop}{role}{i}"
        roster.ROMAJI[person] = f"f{formation}{troop}{role}{i}"
        c = roster.build_card((person, "検証", cost, troop, role, "strike", "検証", []))
        c["skill"] = {"name": "なし", "target": "self", "effects": []}
        CARDS[c["id"]] = c
        cards.append(c)
    units = [{"card": c, "lane": l, "row": r} for c, (r, l) in zip(cards, slots)]
    fcount = {L: sum(1 for u in units if u["lane"] == L and u["row"] == "front")
              for L in range(3)}
    cmd = min(range(len(units)),
              key=lambda i: (units[i]["row"] == "front", -fcount[units[i]["lane"]], i))
    return {"units": units, "commander": cmd}


def cmd_formations():
    """陣形×兵種構成の総当たり。狙いは「どれかが無敵にならない」こと。

    自由配置ではなく名前のついた陣形から選ばせるので、組み合わせが有限になり
    ここで釣り合わせられる（§4.1）。
    """
    from engine import FORMATIONS
    print("=== 陣形×構成の総当たり ===")
    print("  コスト5×6人・必殺技と固有特性なし。総大将は各型の最も安全な枠。")
    print("  **同じ6枚を陣形だけ変えて置く。** 陣形ごとに構成を変えると、"
          "陣形の差か構成の差か分からなくなる。\n")
    COMPOSITIONS = {
        "std": ("標準", [("inf", "tank")] * 2 + [("cav", "dps")] * 2
                + [("arc", "dps")] * 2),
        "cav": ("騎馬", [("cav", "dps")] * 6),
        "bow": ("盾弓", [("inf", "tank")] * 3 + [("arc", "dps")] * 3),
    }
    builds = {(f, c): formation_team(f, COMPOSITIONS[c][1])
              for f in FORMATIONS for c in COMPOSITIONS}
    keys = list(builds)
    grid, sc = {}, {k: [] for k in keys}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            wr = winrate(builds[a], builds[b], seeds=120)
            grid[(a, b)] = wr; grid[(b, a)] = 100 - wr
            sc[a].append(wr); sc[b].append(100 - wr)
    name = lambda k: f"{FORMATIONS[k[0]]['label']}・{COMPOSITIONS[k[1]][0]}"
    print(f"  {'型':<12}" + "".join(f"{name(k)[:6]:>8}" for k in keys) + f"{'平均':>7}")
    for a in keys:
        print(f"  {name(a):<12}" + "".join(f"{grid.get((a,b),'—'):>8}" for b in keys)
              + f"{sum(sc[a])//len(sc[a]):>6}%")
    avgs = {k: sum(sc[k]) / len(sc[k]) for k in keys}
    lo, hi = min(avgs.values()), max(avgs.values())
    unbeaten = [name(k) for k in keys if all(grid[(k, b)] >= 45 for b in keys if b != k)]
    print(f"\n  最弱 {lo:.0f}% 〜 最強 {hi:.0f}%（幅 {hi-lo:.0f}pt）"
          f" → {'OK' if hi - lo <= 30 else 'NG: 陣形の強さが揃っていない'}")
    print(f"  天敵のない型: {'、'.join(unbeaten) if unbeaten else 'なし（すべての型に天敵がある）'}"
          f" → {'NG' if unbeaten else 'OK'}")


# --- support -------------------------------------------------------------

def single_skill_team(skill_key, cost=5, troop="inf", role="bruiser"):
    """5枚を strike にし、後衛の1枚だけを skill_key にした編成。

    支援技（ゲージ付与・回復）は全員同技の総当たりでは測れない。攻撃手段が
    ゼロになり、実測5%が「弱い」ではなく「測れていない」を意味してしまう。
    1枚だけ混ぜれば、部隊としての攻撃力を保ったまま寄与を測れる。
    """
    import roster
    cards = []
    for i in range(6):
        key = skill_key if i == 4 else "strike"     # 4番=後衛中央に置く
        person = f"支{skill_key}{i}"
        roster.ROMAJI[person] = f"g{skill_key}{i}"
        card = roster.build_card((person, "検証", cost, troop, role, key, key, []))
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_support():
    """1枚だけ混ぜる方式で必殺技を測る。支援技の価格付けに使う（§7.5）。

    **カードは既に価格を払っているので、正しく値付けできている技は50%になる。**
    ずれるのは価格付けできていない技だけ、という形で出る。
    """
    import roster
    print("=== 1枚だけ混ぜたときの寄与 ===")
    print("  5枚を strike で揃え、後衛の1枚だけを変えて、全員 strike の編成と戦わせる。")
    print("  能力値は既に補正を払っているので、正しく値付けできていれば50%になる。\n")
    base = single_skill_team("strike")
    keys = sorted(roster.SKILLS)
    rows = sorted(((winrate(single_skill_team(k), base, seeds=300), k) for k in keys),
                  reverse=True)
    print(f"  {'ひな型':<10} {'寄与':>6} {'現補正':>7}")
    for wr, k in rows:
        mark = "  ← 価格付けなし" if k in roster.UNPRICED_SKILLS else ""
        print(f"  {k:<10} {wr:>5}% {roster.SKILL_ADJUST.get(k, 0.0):>+7.2f}{mark}")
    lo, hi = min(wr for wr, _ in rows), max(wr for wr, _ in rows)
    print(f"\n  最弱 {lo}% 〜 最強 {hi}%（幅 {hi - lo}pt）"
          f" → {'OK' if hi - lo <= 15 else 'NG: 実際の編成では釣り合っていない'}")
    nxt = {k: round(roster.SKILL_ADJUST.get(k, 0.0) + (wr - 50) / roster.SUPPORT_STEP, 2)
           for wr, k in rows}
    print_next("SKILL_ADJUST", nxt)


# --- exploit -------------------------------------------------------------
#
# ここまでの指標には構造的な穴がある。swap と meta は**ランダム編成の平均**を見て
# おり、troops と formations は**こちらが手で組んだ型**しか見ていない。
# 実際のプレイヤーがするのは「最強を探しに来る」ことで、それを再現していない。
#
# 探索で強い編成が見つかること自体は問題ではない。攻略は面白さの本体である。
# 問題は**攻略が1つに収束すること**。だからこの指標が見るのは強さの上限ではなく、
# 「見つかった最強に対して、また別の最強が見つかるか」である。
#   収束（同じ編成に戻る・世代を跨いで勝ち続ける） → 詰み
#   循環（前の世代の最強が次の世代に食われる）     → 健全

def team_of(ids, formation, cmd):
    return make_team(ids, commander=cmd, formation=formation)


def evaluate(team, opponents, seeds):
    """相手集団に対する平均勝率。"""
    return sum(winrate(team, o, seeds=seeds) for o in opponents) // len(opponents)


def neighbors(ids, formation, cmd, cap, rng, per_slot=4):
    """1手で到達できる編成を挙げる。1枚差し替え・陣形変更・総大将変更。"""
    from engine import FORMATIONS
    out = []
    pool = [c for c in ALL_IDS if c not in ids]
    for slot in range(6):
        rest = sum(CARDS[c]["cost"] for i, c in enumerate(ids) if i != slot)
        legal = [c for c in pool if rest + CARDS[c]["cost"] <= cap]
        for _ in range(min(per_slot, len(legal))):
            pick = legal[rng.below(len(legal))]
            trial = list(ids)
            trial[slot] = pick
            out.append((trial, formation, cmd))
    for f in FORMATIONS:
        if f != formation:
            out.append((list(ids), f, cmd))
    for c in range(6):
        if c != cmd:
            out.append((list(ids), formation, c))
    return out


def climb(opponents, cap, rng, start, steps=8, seeds=10):
    """山登りで opponents にもっとも強い編成を探す。"""
    ids, formation, cmd = start
    best = evaluate(team_of(ids, formation, cmd), opponents, seeds)
    for _ in range(steps):
        gain = None
        for trial in neighbors(ids, formation, cmd, cap, rng, per_slot=3):
            wr = evaluate(team_of(*trial), opponents, seeds)
            if wr > best:
                best, gain = wr, trial
        if gain is None:
            break
        ids, formation, cmd = gain
    return (ids, formation, cmd), best


def best_climb(opponents, cap, rng, starts, steps=8, seeds=10):
    """複数の初期値から登り、もっとも良かったものを採る。

    **初期値が1つだと探索の失敗を meta の性質と誤読する。** 固定の baseline から
    6手だけ登らせていたときは、ある世代で前世代に 0% の編成しか見つけられず、
    それを「循環している」と読みかけた。実際には対抗札（魏特効）が相手に刺さる
    はずの場面で、探索がそこへ辿り着けていなかっただけだった。
    判定基準が信用できないと、対抗能力が効いたかどうかも判断できない。
    """
    best, best_wr = None, -1
    for start in starts:
        found, wr = climb(opponents, cap, rng, start, steps, seeds)
        if wr > best_wr:
            best, best_wr = found, wr
    return best, best_wr


def cmd_exploit():
    """攻略探索。最強編成を探す行為を機械的に再現し、収束するか循環するかを見る。"""
    from engine import FORMATIONS
    cap = REGULATIONS["high"][1]
    rng = Rng(20260814)
    field = [t[0] for t in sample_teams(cap, 3, seed=777)]
    print("=== 攻略探索（高コスト戦・上限40）===")
    print("  山登りで最強編成を探し、それを次の世代の標的にする。")
    print("  **見つかることは問題ではない。1つに収束することが問題。**\n")

    # 初期値は baseline とランダム3種。**1つだけだと探索の失敗を meta の性質と
    # 誤読する。** 実際に前世代へ 0% の編成しか見つけられず「循環している」と
    # 読みかけたことがある。
    forms = list(FORMATIONS)
    starts = [(baseline(cap), "kakuyoku", 4)]
    for t in sample_teams(cap, 3, seed=31337):
        starts.append((t[1], forms[rng.below(len(forms))], t[2]))
    print(f"  初期値 {len(starts)}種（baseline + ランダム3）から8手ずつ登る\n")

    history = []
    opponents = field
    for gen in range(4):
        (ids, form, cmd), wr = best_climb(opponents, cap, rng, starts)
        team = team_of(ids, form, cmd)
        names = "/".join(CARDS[c]["name"].split("〔")[0] for c in ids)
        print(f"  第{gen + 1}世代  対 前世代 {wr}%  陣形 {FORMATIONS[form]['label']}"
              f"  総大将 {CARDS[ids[cmd]]['name'].split('〔')[0]}")
        print(f"    {names}  (合計{sum(CARDS[c]['cost'] for c in ids)}/{cap})")
        # 過去の世代すべてに対する勝率。収束していれば全部に勝ち続ける。
        if history:
            past = "  ".join(f"第{i+1}世代 {winrate(team, h, seeds=60)}%"
                             for i, (h, _) in enumerate(history))
            print(f"    過去世代との相性: {past}")
        history.append((team, ids))
        opponents = [team]
        print()

    print("  判定")
    last = history[-1][0]
    beats_all = [winrate(last, h, seeds=60) for h, _ in history[:-1]]
    if beats_all and min(beats_all) >= 55:
        print("    最終世代が過去のすべてに勝つ → NG: 攻略が収束している（詰み）")
    else:
        print("    過去世代に食われる関係がある → OK: 攻略が循環している")

    # 循環しているだけでは足りない。**毎世代どれだけ札が入れ替わるか**を見る。
    # 6枠のうち1〜2枠しか動かず残りが固定なら、実質は1つの最適解である。
    sets = [set(ids) for _, ids in history]
    core = set.intersection(*sets)
    turn = [len(sets[i] - sets[i - 1]) for i in range(1, len(sets))]
    print(f"    毎世代の入れ替わり: {turn} 枚 / 6枠")
    if core:
        print(f"    全世代に残った札 {len(core)}枚: "
              f"{'、'.join(CARDS[c]['name'].split('〔')[0] for c in sorted(core))}"
              f" → {'NG: 固定コアがある' if len(core) >= 2 else '許容範囲'}")
    else:
        print("    全世代に残った札: なし → OK: 固定コアがない")


# --- sensitivity ---------------------------------------------------------

def scaled_team(score_mult, cost=5, troop="inf", role="bruiser", skill="strike", tag=""):
    """総合値を score_mult 倍した検証用編成。

    総合値 = 実効耐久 × 実効火力 なので攻撃力を √score_mult 倍し、端数は
    roster.solve_hp で兵力へ吸収させる。**ここを50刻みで丸めてはいけない。**
    丸めていたときは +1% の要求が量子化で0%になり、換算率そのものを測り損ねていた。
    """
    import math
    import roster
    cards = []
    for i in range(6):
        person = f"感{tag}{i}"
        roster.ROMAJI[person] = f"z{tag}{i}"
        card = roster.build_card((person, "検証", cost, troop, role, skill, "検証", []))
        t = roster.TROOP[troop]
        base = roster.effective_score(card["hp"], card["atk"], card["dfn"],
                                      t["interval"], card["acc"], card["crit"],
                                      roster.evade_of(troop))
        card["atk"] = max(5, round(card["atk"] * math.sqrt(score_mult)))
        card["hp"] = roster.solve_hp(base * score_mult, card["atk"],
                                     card["dfn"], troop)
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_sensitivity():
    """総合値の差が勝率の差へどう換算されるかを測る。

    必殺技や固有特性の価値は勝率でしか測れないが、コスト予算（§4.6）は総合値で
    表されている。両者をつなぐ換算率がないと、能力の価値を予算へ入れられない。
    """
    print("=== 総合値の差と勝率の関係 ===")
    print("  総合値だけを変えた編成を、等倍の編成と戦わせる。\n")
    base = scaled_team(1.0, tag="base")
    print(f"  {'総合値':>8} {'勝率':>6} {'1%あたり':>9}")
    rows = []
    for pct in (1, 2, 3, 5, 8, 12):
        wr = winrate(scaled_team(1 + pct / 100, tag=f"p{pct}"), base, seeds=300)
        rows.append((pct, wr))
        print(f"  {pct:>+7}% {wr:>5}% {(wr - 50) / pct:>8.1f}pt")
    usable = [(p, w) for p, w in rows if w < 95]
    if usable:
        avg = sum((w - 50) / p for p, w in usable) / len(usable)
        print(f"\n  換算率: 総合値1%あたり 約{avg:.1f}pt（飽和していない範囲の平均）")
        print(f"  → 勝率で +{avg * 5:.0f}pt ぶんの能力は、総合値5%ぶんに相当する。")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    table = {"check": cmd_check, "troops": cmd_troops, "swap": cmd_swap,
             "meta": cmd_meta, "commander": cmd_commander, "cost": cmd_cost,
             "skills": cmd_skills, "traits": cmd_traits,
             "formations": cmd_formations, "exploit": cmd_exploit,
             "support": cmd_support,
             "sensitivity": cmd_sensitivity}
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

    # (2) 2人ぶんと1人ぶん。§4.6 のコスト式は「枠の基礎価値 + コスト比例分」であり、
    #     2人なら枠の基礎価値が2回計上される。したがって合計コストが同じでも
    #     2人が勝つのが正しい。ここは加算性の検証ではなく、その差の大きさを見る。
    #     アイテムにコストを持たせる場合、アイテムはコスト比例分だけを買うため、
    #     「武将1体ぶんのコストのアイテム」は「武将1体」より弱くなる。
    print("\n  [2] コスト1+2 の2人 対 コスト3 の1人（枠の基礎価値ぶん2人が勝つのが正しい）")
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
        print(f"    コスト{pair[0]}+{pair[1]} の2人 対 コスト{single} の1人: {wr:>3}%"
              f"  （枠1つぶんの価値の差）")


if __name__ == "__main__":
    main()
