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
  python3 sim/balance.py bo3         BO3のマッチ勝率（1戦の勝率が3戦でどう化けるか）
  python3 sim/balance.py factions    勢力の規模と対抗能力の実効価値
  python3 sim/balance.py amplify     増幅の強さ（総合値の差が勝率へどれだけ拡大されるか）
  python3 sim/balance.py skillprice  必殺技の価格式が実測と合っているか
  python3 sim/balance.py sensitivity 総合値の差と勝率の差の換算率
  python3 sim/balance.py all         すべて
"""

import math
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
        entry = (f"検証{troop}{i}", "検証", c, troop, role, "strike", [])
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
        card = roster.build_card((person, "検証", 5, troop, role, "strike", []))
        CARDS[card["id"]] = card
        cards.append(card)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_troops():
    print("=== 兵種三すくみ ===")
    label = {"inf": "歩兵", "cav": "騎兵", "arc": "弓兵"}
    lo, hi = (round(per_battle_for(t) * 100) for t in TARGET_MATCH)
    print(f"  目標帯 1戦 {lo}-{hi}%（マッチ {TARGET_MATCH[0]*100:.0f}-"
          f"{TARGET_MATCH[1]*100:.0f}% からの逆算・§5.3）")
    print("  **帯はマッチ単位で決めて1戦へ逆算する。** 三すくみの優位は3部隊とも")
    print("  同じ向きに効きうるので、1戦で許した幅がマッチで最大まで増幅される"
          f"（{hi}%→{TARGET_MATCH[1]*100:.0f}%）。")
    print(f"  下端 {lo}% は 200戦の2σ={2*100/math.sqrt(200):.0f}pt に埋もれるので、"
          "守れるのは上端だけである。\n")
    print("  [役割を混ぜた編成 — こちらが本番の指標]")
    print("  耐久2・均衡2・火力2。実際の編成と同じく後衛に火力役が並ぶ。")
    mixed = {t: mixed_team(t) for t in ("inf", "cav", "arc")}
    for a, b in (("inf", "cav"), ("cav", "arc"), ("arc", "inf")):
        wr = winrate(mixed[a], mixed[b], seeds=200)
        mark = "OK" if lo <= wr <= hi else ("NG(弱すぎ)" if wr < lo else "NG(強すぎ)")
        m3 = match_rate((wr / 100,) * 3) * 100
        print(f"    {label[a]}→{label[b]}  {wr:>3}%  (マッチ {m3:>3.0f}%)  {mark}")

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
    # 上位N編成は**カード枚数から決める**。固定にすると枚数を増やしたとき
    # 偶然0%になる札が増え、下限割れが読めなくなる。60枚なら28、80枚なら42。
    n = len(ALL_IDS)
    top_n = max(20, round(math.log(3 / n) / math.log(1 - 6 / n)))
    for key, (label, cap) in REGULATIONS.items():
        teams = sample_teams(cap, top_n * 4, seed=2026)
        panel = [t[0] for t in sample_teams(cap, 12, seed=99991)]
        scores = []
        for t in teams:
            a = sum(play(t[0], o, seeds=8)[0] for o in panel)
            scores.append(a)
        ranked = sorted(range(len(teams)), key=lambda i: -scores[i])
        top = ranked[:top_n]
        freq = Counter()
        for i in top:
            freq.update(teams[i][1])
        # **0回の確率ではなく、閾値以下の確率で数える。** 下限割れは「3%未満」なので
        # 上位42編成なら1回だけ出た札も含まれる。0回の確率だけで見積もると
        # 期待値を3枚と誤り、実際の13枚と比べて10枚ぶんの信号を捏造してしまう。
        thr = (3 * len(top) - 1) // 100
        q = 6 / len(ALL_IDS)
        chance = round(len(ALL_IDS) * sum(
            math.comb(len(top), k) * q ** k * (1 - q) ** (len(top) - k)
            for k in range(thr + 1)))
        # そのレギュレーションで実際に使える札の数。上限18の枠にコスト10は
        # 入れられるが実用にならないので、使えない札が下限割れになるのは正常。
        usable = sum(1 for c in ALL_IDS if CARDS[c]["cost"] * 6 <= cap * 2)
        print(f"\n[{label}] {len(teams)}編成から上位{len(top)}編成の採用率")
        print(f"  偶然でも約{chance}枚は下限割れになる / "
              f"この上限で実用になる札は約{usable}枚（残り{len(ALL_IDS)-usable}枚は"
              f"高すぎて使えないので下限割れが正常）")
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
        card = roster.build_card((person, "検証", cost, troop, role, skill_key, []))
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
        card = roster.build_card((person, "検証", cost, troop, role, skill, traits))
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
    keys = sorted(roster.TRIGGERS) + sorted(roster.COUNTERS) + sorted(roster.PASSIVES)
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
        if k in roster.PASSIVES:
            name, cond, lim = roster.PASSIVES[k], "前衛に置く", "常在"
        elif k in roster.COUNTERS:
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


# --- factions ------------------------------------------------------------

def faction_team(factions, counters=None, cost=5, tag=""):
    """勢力を指定した検証用編成。

    `counters` は factions と同じ長さで、各枠が持つ対抗能力（不要な枠は None）。
    **1枚だけ持たせたいときに全員へ配らないこと。** 実戦の特効の密度は
    12枚/80枚 = 6枠に0.9枚なので、全員に配ると6倍の濃さで測ることになる。
    最初にそれをやって「1枚の特効で勝率0%」という数字を出した。

    **勢力は必ず明示する。** 合成カードの既定は roster.NEUTRAL_FACTION で、
    どの対抗能力の的にもならない。明示しないと「相手が全員中立」の条件で
    測ることになり、特効の価値は必ず0と出る。
    """
    import roster
    counters = counters if counters is not None else [None] * len(factions)
    cards = []
    for i, (f, ct) in enumerate(zip(factions, counters)):
        person = f"勢{tag}{i}"
        roster.ROMAJI[person] = f"y{tag}{i}"
        roster.FACTION_OF[person] = f
        c = roster.build_card((person, "検証", cost, "inf", "bruiser", "strike",
                               [ct] if ct else []))
        c["skill"] = {"name": "なし", "target": "self", "effects": []}
        CARDS[c["id"]] = c
        cards.append(c)
    return {"units": [{"card": c, "lane": l, "row": r}
                      for c, (r, l) in zip(cards, SLOTS)], "commander": 4}


def cmd_factions():
    """勢力の規模と、対抗能力の実効価値を測る（§6.6）。

    対抗能力の価値は「相手が対象である確率 × 効果量」なので、**勢力の規模が
    そのまま札の強さになる**。規模が違えば同じ +25% でも価値が違う。
    群雄は的にされないため、この軸から外れている。
    """
    import roster
    print("=== 勢力と対抗能力 ===")
    print("  完全に均衡していれば、どの勢力の編成も勝率50%、どの対抗能力も")
    print("  同じ実効価値になる。まず規模を数える。\n")

    share = Counter(c["faction"] for c in CARDS.values())
    total = sum(share.values())
    print(f"  {'勢力':<6}{'枚数':>5}{'割合':>7}{'6枠の期待':>10}{'1枚でも当たる':>13}"
          f"{'低コスト戦':>11}")
    for f in ("wei", "shu", "go", "gun"):
        s = share[f] / total
        costs = sorted(c["cost"] for c in CARDS.values() if c["faction"] == f)
        cheapest6 = sum(costs[:6]) if len(costs) >= 6 else None
        low = "不可" if cheapest6 is None or cheapest6 > REGULATIONS["low"][1] \
            else f"{cheapest6}"
        print(f"  {roster.FACTION_LABEL[f]:<6}{share[f]:>5}{s*100:>6.0f}%"
              f"{s*6:>9.2f}人{(1-(1-s)**6)*100:>11.0f}%{low:>12}")
    print("  「1枚でも当たる」= 敵6枚のうち少なくとも1枚がその勢力である確率。")
    print("  「低コスト戦」= その勢力だけで6枚組んだときの最小コスト（上限18）。")

    print("\n  対抗能力を持つ札の数: "
          + " / ".join(f"{roster.FACTION_LABEL[v]}特効 "
                       f"{sum(1 for c in CARDS.values() if k in c.get('traits', []))}枚"
                       for k, v in roster.COUNTERS.items()))

    print("\n  対抗能力の価値を、敵の勢力構成を振って測る（コスト5・歩兵・6枚）")
    print("  **全員が蜀特効を持つ編成**が、敵6枚のうち蜀がk枚のときにどれだけ勝つか。")
    print("  **特効なしの同じ編成を並べる。** 特効の価格ぶんだけ能力値が下がって")
    print("  いるので、当たらなければ50%を割る。そこが価格が正しいかの目安になる。\n")
    all_shu = ["vs_shu"] * 6
    print(f"  {'敵の蜀':>6}{'蜀特効あり':>11}{'特効なし(対照)':>16}")
    for k in (0, 1, 2, 3, 4, 6):
        enemy = faction_team(["shu"] * k + ["go"] * (6 - k), tag=f"e{k}")
        with_c = winrate(faction_team(["go"] * 6, all_shu, tag="c"), enemy, seeds=200)
        without = winrate(faction_team(["go"] * 6, tag="n"), enemy, seeds=200)
        print(f"  {k:>5}枚{with_c:>10}%{without:>15}%")
    gun_enemy = faction_team(["gun"] * 6, tag="eg")
    gun_c = winrate(faction_team(["go"] * 6, all_shu, tag="c2"), gun_enemy, seeds=200)
    gun_n = winrate(faction_team(["go"] * 6, tag="n2"), gun_enemy, seeds=200)
    print(f"  {'群6枚':>6}{gun_c:>10}%{gun_n:>15}%   ← 無所属なのでどの特効も刺さる")

    exp_shu = share["shu"] / total * 6
    print(f"\n  実戦での的中枚数の期待値は 蜀 {exp_shu:.2f}枚（上の表の1〜2枚の行）。")
    print("  対抗能力はそこで釣り合うように価格付けしてある（roster.TRAIT_ADJUST）。")
    print("  **ただし価値の傾きが急すぎる。** 0枚で1%・2枚で63%・3枚で95%なので、")
    print("  対抗能力は「戦略」ではなく「当たれば勝ち・外れれば負け」の賭けになっている。")
    gun_share = share["gun"] / total
    print(f"\n  **群雄は {share['gun']}枚・{gun_share*100:.0f}% しかない。**")
    print(f"  仮に群特効を作っても、敵6枚に群が入る確率は "
          f"{(1-(1-gun_share)**6)*100:.0f}% で、期待{gun_share*6:.2f}枚。")
    print(f"  他の勢力の {exp_shu/(gun_share*6):.1f}分の1 しか当たらない。"
          "負のフィードバック（§6.6）は\n  規模に比例して効くので、この規模では自己調整が働かない。")
    print("  → 専用の群特効は作らず、**群を「どの特効の的にもなる無所属」として"
          "軸へ載せる**。")

    print("\n  無所属の不利がどれだけかを測り、FACTION_ADJUST を決める。")
    print("  敵6枚のうち1枚が特効を持つ統制パネルを使い、特効の種類を3通り回す。")
    print("  群はどれにも刺さり、魏蜀呉は1/3だけ刺さる。")
    print("  **実ロスターからサンプルした敵で測ってはいけない。** 強さが揃って")
    print("  いないので10組中8組が0%か100%へ張り付き、数%の能力差が見えない。\n")
    # 実戦の特効の密度の確認。誘発型の特性は dict なので文字列だけを見る。
    sampled = [t for t, _ids, _cmd in sample_teams(REGULATIONS["mid"][1], 10, seed=7)]
    ncount = Counter(sum(1 for u in e["units"]
                         for t in u["card"].get("traits", [])
                         if isinstance(t, str) and t in roster.COUNTERS)
                     for e in sampled)
    print("  参考: 実ロスターの敵10編成が持つ特効の枚数 "
          + " / ".join(f"{k}枚 {v}編成" for k, v in sorted(ncount.items())))

    panels = {f: faction_team(["go"] * 6, [f"vs_{f}"] + [None] * 5, tag=f"p{f}")
              for f in ("wei", "shu", "go")}
    scores = {}
    for f in ("wei", "shu", "go", "gun"):
        team = faction_team([f] * 6, tag=f"t{f}")
        vs = {g: winrate(team, p, seeds=200) for g, p in panels.items()}
        scores[f] = sum(vs.values()) / len(vs)
        print(f"    {roster.FACTION_LABEL[f]}6枚 → "
              + " / ".join(f"{roster.FACTION_LABEL[g]}特効に {v}%" for g, v in vs.items())
              + f"   平均 {scores[f]:.1f}%")
    ref = sum(scores[f] for f in ("wei", "shu", "go")) / 3
    diff = scores["gun"] - ref
    cur = roster.FACTION_ADJUST.get("gun", 0.0)
    noise = 2 * 100 / math.sqrt(200 * 3)
    print(f"\n  魏蜀呉の平均 {ref:.1f}% に対し 群 {scores['gun']:.1f}%（差 {diff:+.1f}pt）")
    print(f"  現補正 {cur:+.2f}  2σ={noise:.1f}pt  "
          f"{'OK: 釣り合っている' if abs(diff) <= noise else '要調整'}")
    print("\n  **勾配1歩の反復では決めないこと。範囲を掃く。** 攻撃力が整数なので、")
    print("  値引きを増やすと hp と atk の配分が入れ替わる。総合値が上がっていても")
    print("  被ダメージ増加のかかる相手には耐久のほうが効くため、勝率はのこぎり波に")
    print("  なる。実測 -1.05→50.7% / -1.35→51.7% / -1.70→45.7%（atk 56→57）。")
    print("  現在値は谷から離れた平坦部にある。")


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
        c = roster.build_card((person, "検証", cost, troop, role, "strike", []))
        c["skill"] = {"name": "なし", "target": "self", "effects": []}
        CARDS[c["id"]] = c
        cards.append(c)
    units = [{"card": c, "lane": l, "row": r} for c, (r, l) in zip(cards, slots)]
    fcount = {L: sum(1 for u in units if u["lane"] == L and u["row"] == "front")
              for L in range(3)}
    cmd = min(range(len(units)),
              key=lambda i: (units[i]["row"] == "front", -fcount[units[i]["lane"]], i))
    # **formation を返し忘れると陣形の固有効果が一切かからない。**
    # engine.Battle は teams[i]["formation"] を見て効果表を引くので、
    # ここが無いと枠の配置だけを測ることになる。実際その状態で
    # 「効果量を差し替えても総当たりの数字が動かない」と読んでいた。
    return {"units": units, "commander": cmd, "formation": formation}


COMPOSITIONS = {
    "std": ("標準", [("inf", "tank")] * 2 + [("cav", "dps")] * 2
            + [("arc", "dps")] * 2),
    "cav": ("騎馬", [("cav", "dps")] * 6),
    "bow": ("盾弓", [("inf", "tank")] * 3 + [("arc", "dps")] * 3),
}


def cmd_formations():
    """陣形×兵種構成の総当たり。狙いは「どれかが無敵にならない」こと。

    自由配置ではなく名前のついた陣形から選ばせるので、組み合わせが有限になり
    ここで釣り合わせられる（§4.1）。

    **陣形の margin と構成の margin を分けて数える。** 12型を一緒くたに並べると
    0%/100% のセルが陣形のせいか構成のせいかが分からない。分けずに測っていた
    あいだ、陣形の効果をいくら動かしても数字が動かず、原因を取り違えていた。
    """
    from engine import FORMATIONS, LANE_CAP
    fkeys, ckeys = list(FORMATIONS), list(COMPOSITIONS)
    n = 120
    print("=== 陣形×構成の総当たり ===")
    print("  コスト5×6人・必殺技と固有特性なし。総大将は各型の最も安全な枠。")
    print("  **同じ6枚を陣形だけ変えて置く。** 陣形ごとに構成を変えると、"
          "陣形の差か構成の差か分からなくなる。")
    print(f"  完全に均衡していれば全セル50%・幅0pt。{n}戦の2σ = "
          f"{2*100/math.sqrt(n):.0f}pt なので、幅が"
          f"{2*100/math.sqrt(n):.0f}pt 以下なら均衡と区別できない。\n")
    print(f"  レーンあたりの人数は全陣形で {LANE_CAP} に揃えてある: "
          + " / ".join(
              f"{FORMATIONS[f]['label']}"
              f"{[sum(1 for r, l in FORMATIONS[f]['slots'] if l == L) for L in range(3)]}"
              f"前{[sum(1 for r, l in FORMATIONS[f]['slots'] if l == L and r == 'front') for L in range(3)]}"
              for f in fkeys))
    print()

    builds = {(f, c): formation_team(f, COMPOSITIONS[c][1])
              for f in fkeys for c in ckeys}
    keys = list(builds)
    grid, sc = {}, {k: [] for k in keys}
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            wr = winrate(builds[a], builds[b], seeds=n)
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
          f" → {'OK' if hi - lo <= 30 else 'NG: 型の強さが揃っていない'}")
    print(f"  天敵のない型: {'、'.join(unbeaten) if unbeaten else 'なし（すべての型に天敵がある）'}"
          f" → {'NG' if unbeaten else 'OK'}")

    # --- 内訳。陣形のせいか構成のせいかを分ける ---
    def summarize(pairs, title):
        vals = [(a, b, grid[(a, b)]) for a, b in pairs]
        nums = [v for _, _, v in vals]
        ext = [(a, b, v) for a, b, v in vals if v <= 5 or v >= 95]
        print(f"\n  [{title}] {len(nums)}組  幅 {max(nums)-min(nums)}pt"
              f"（{min(nums)}%〜{max(nums)}%）  極端(≦5/≧95) {len(ext)}/{len(nums)}")
        for a, b, v in ext:
            print(f"      {name(a)} → {name(b)} {v}%")
        return max(nums) - min(nums)

    fpairs = [((f1, c), (f2, c)) for c in ckeys
              for i, f1 in enumerate(fkeys) for f2 in fkeys[i + 1:]]
    cpairs = [((f, c1), (f, c2)) for f in fkeys
              for i, c1 in enumerate(ckeys) for c2 in ckeys[i + 1:]]
    fw = summarize(fpairs, "陣形の margin（構成を固定して陣形だけ変える）")
    cw = summarize(cpairs, "構成の margin（陣形を固定して構成だけ変える）")
    print(f"\n  → 陣形 {fw}pt / 構成 {cw}pt。"
          f"{'構成' if cw >= fw else '陣形'}のほうが大きい。")

    print("\n  構成ごとに、どの陣形が最善か（同じ構成の相手だけと戦った平均）")
    for c in ckeys:
        row = {f: sum(grid[((f, c), (g, c))] for g in fkeys if g != f) / (len(fkeys) - 1)
               for f in fkeys}
        best = max(row, key=row.get)
        print(f"    {COMPOSITIONS[c][0]}: "
              + " / ".join(f"{FORMATIONS[f]['label']}{row[f]:.0f}%" for f in fkeys)
              + f"  幅 {max(row.values())-min(row.values()):.0f}pt"
              f"  最善={FORMATIONS[best]['label']}")
    print("  **構成ごとに最善の陣形が違えば、陣形の選択は本物である。**"
          "同じ陣形が常に最善なら\n  選択は形だけになる。")


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
        card = roster.build_card((person, "検証", cost, troop, role, key, []))
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


# --- bo3 -----------------------------------------------------------------

# 帯ごとのシードずらし。**hash() を使ってはいけない。** Python の文字列ハッシュは
# プロセスごとに変わるため、bo3 の測定値が実行のたびに動いていた（§8.4 違反）。
BO3_SALT = {"low": 11, "mid": 37, "high": 71}


def bo3_match(a, b, seeds=60):
    """3レギュレーションを戦い、2勝した側を勝ちとする。a から見たマッチ勝率。

    引き分けはどちらの勝ちにも数えない。**2勝に届く側がなければマッチも引き分け**
    （§8.2 で確定）とし、50% として集計する（play と同じ扱い）。
    **引き分けを一律に負けと数えてはいけない。** 時間切れは最大16%出るので、
    A 側だけが systematically 損をする。

    play と同様に半数は左右を入れ替える。先攻の有利/不利を打ち消すため。
    """
    wins = ties = 0
    for i in range(seeds):
        won = lost = 0
        for key in REGULATIONS:
            seed = i * 7919 + 13 + BO3_SALT[key]
            if i % 2 == 0:
                w = Battle([a[key], b[key]], seed=seed).run()["winner"]
            else:
                r = Battle([b[key], a[key]], seed=seed).run()["winner"]
                w = None if r is None else 1 - r
            won += 1 if w == 0 else 0
            lost += 1 if w == 1 else 0
        if won >= 2:
            wins += 1
        elif lost < 2:
            ties += 1
    return (wins * 100 + ties * 50) // seeds


def match_rate(ps):
    """1戦あたりの勝率 (p1,p2,p3) から、2勝で決着するマッチの勝率を出す。"""
    p1, p2, p3 = ps
    return p1 * p2 + p1 * p3 + p2 * p3 - 2 * p1 * p2 * p3


def per_battle_for(target):
    """マッチ勝率の目標から1戦あたりの勝率を逆算する（優位が3戦とも効く場合）。"""
    lo, hi = 0.5, 1.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if match_rate((mid, mid, mid)) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def bo3_pool(rng, weights=(1, 1, 1)):
    """人物が重ならない3部隊を組む（§4.3）。weights で帯ごとの力の入れ方を変える。

    weights が (2,2,1) なら低コスト戦を捨てる配分になる。**BO3 は2勝すればよいので
    1帯を捨てる戦略が成立しうる**が、各レギュレーションのコスト上限は独立なので
    帯をまたいで予算は移せない。移せるのは「良い札をどの帯へ回すか」だけである。
    """
    used, teams = set(), {}
    order = sorted(REGULATIONS, key=lambda k: -weights[list(REGULATIONS).index(k)])
    for key in order:
        cap = REGULATIONS[key][1]
        ids = []
        for _ in range(6):
            left = 6 - len(ids) - 1
            spent = sum(CARDS[c]["cost"] for c in ids)
            pool = sorted((c for c in ALL_IDS
                           if c not in ids and CARDS[c]["person"] not in used),
                          key=lambda c: (CARDS[c]["cost"], c))
            costs = [CARDS[c]["cost"] for c in pool]
            cands = [c for i, c in enumerate(pool)
                     if spent + costs[i] + sum((costs[:i] + costs[i + 1:])[:left]) <= cap]
            if not cands:
                return None
            ids.append(cands[rng.below(len(cands))])
        for _ in range(12):
            slot = rng.below(6)
            spent = sum(CARDS[c]["cost"] for c in ids) - CARDS[ids[slot]]["cost"]
            up = [c for c in ALL_IDS if c not in ids
                  and CARDS[c]["person"] not in used
                  and CARDS[c]["cost"] > CARDS[ids[slot]]["cost"]
                  and spent + CARDS[c]["cost"] <= cap]
            if up:
                ids[slot] = up[rng.below(len(up))]
        used.update(CARDS[c]["person"] for c in ids)
        teams[key] = make_team(ids, commander=rng.below(6), formation="kakuyoku")
    return teams


TARGET_MATCH = (0.55, 0.80)   # 三すくみの有利側が取ってよいマッチ勝率（§5.3）


def cmd_bo3():
    """1戦あたりの勝率が、3戦のマッチ勝率へどう化けるかを見る。

    **目標帯はマッチ単位で決め、1戦へ逆算する。** 逆にしてはいけない。
    ここまでの指標はすべて1戦あたりで測っており、1戦で許した幅がマッチで
    どこまで膨らむかを見ていなかった。
    """
    print("=== BO3（3レギュレーション・2勝で決着）===")
    lo_p, hi_p = (per_battle_for(t) for t in TARGET_MATCH)

    print("  完全に均衡していれば（3戦とも50%）マッチも50%。まずそこを確認する。")
    print(f"    3戦とも50% → マッチ {match_rate((.5, .5, .5)) * 100:.0f}%\n")

    print("  **BO3 は一律の増幅器ではない。優位が何戦に効くかで向きが変わる。**")
    print(f"  {'1戦':>6}{'1戦だけ':>10}{'2戦':>8}{'3戦とも':>9}   {'向き'}")
    for p in (55, 60, 65, 70, 80, 90):
        q = p / 100
        m1 = match_rate((q, .5, .5)) * 100
        m2 = match_rate((q, q, .5)) * 100
        m3 = match_rate((q, q, q)) * 100
        print(f"  {p:>5}% {m1:>8.0f}% {m2:>7.0f}% {m3:>8.0f}%   "
              f"{'1戦だけなら減る／3戦なら増える'}")
    print("\n  1戦だけに効く優位は**薄まる**（0.5p+0.25）。2戦に効くとちょうど"
          "\n  そのまま（p）。3戦とも効いて初めて増幅する（3p²−2p³）。")
    print("  → BO3 が報いるのは「刺さる1枚」ではなく「3帯すべてで通る読み」である。")

    print(f"\n  目標帯をマッチ単位で {TARGET_MATCH[0]*100:.0f}-{TARGET_MATCH[1]*100:.0f}% "
          f"と置くと、1戦あたりでは {lo_p*100:.0f}-{hi_p*100:.0f}% になる。")
    print(f"    現行の1戦の帯 55-80% → マッチ {match_rate((.55,)*3)*100:.0f}-"
          f"{match_rate((.8,)*3)*100:.0f}%（上端が緩すぎる）")
    print(f"    改めた1戦の帯 {lo_p*100:.0f}-{hi_p*100:.0f}% → マッチ "
          f"{TARGET_MATCH[0]*100:.0f}-{TARGET_MATCH[1]*100:.0f}%")
    n_need = math.ceil((100 / (lo_p * 100 - 50)) ** 2)
    print(f"  ただし下端 {lo_p*100:.0f}% は 50% と紙一重で、"
          f"区別するには1条件あたり {n_need} 戦が要る（2σ）。")
    print(f"  現在の SEEDS={SEEDS} では 2σ={100/math.sqrt(SEEDS):.1f}pt なので、"
          "下端は**測定で守れない**。\n  守れるのは上端だけである。")

    print("\n  換算式を実測で検算する（総合値を振った合成カードで既知の勝率を作る）")
    even = {k: scaled_team(1.0, tag="b3e") for k in REGULATIONS}
    for mult, edges in ((1.03, 3), (1.03, 1), (1.06, 3)):
        strong = scaled_team(mult, tag=f"b3s{int(mult*100)}")
        p = winrate(strong, scaled_team(1.0, tag="b3e2"), seeds=300) / 100
        keys = list(REGULATIONS)[:edges]
        team = {k: (strong if k in keys else scaled_team(1.0, tag=f"b3n{k}"))
                for k in REGULATIONS}
        got = bo3_match(team, even, seeds=120)
        want = match_rate(tuple(p if k in keys else .5 for k in REGULATIONS)) * 100
        ok = "OK" if abs(got - want) <= 12 else "NG: 式が実態と合っていない"
        print(f"    総合値+{(mult-1)*100:.0f}% を{edges}戦に効かせる: 1戦{p*100:.0f}% "
              f"→ マッチ実測{got}% / 式{want:.0f}%  {ok}")

    print("\n  3戦が同じ勝率とは限らない。帯ごとに強さが違う場合:")
    for label, ps in (("均等 60/60/60", (60, 60, 60)),
                      ("やや偏り 70/60/50", (70, 60, 50)),
                      ("2帯集中 85/85/20", (85, 85, 20)),
                      ("1帯を捨てる 90/90/10", (90, 90, 10))):
        m = match_rate(tuple(x / 100 for x in ps)) * 100
        print(f"    {label:<20} → マッチ {m:.0f}%")
    print("  → 2勝でよいので**1帯を捨てる配分が有利になりうる**。ただし各帯の")
    print("     コスト上限は独立なので、帯をまたいで予算は移せない。移せるのは")
    print("     良い札をどの帯へ回すかだけ。実測で確かめる。\n")

    rng = Rng(4649)
    panel = [t for t in (bo3_pool(rng) for _ in range(4)) if t]
    print(f"  人物が重ならない3部隊を組み、対戦相手{len(panel)}組と戦わせる")
    for label, w in (("均等", (1, 1, 1)), ("中高に寄せる", (1, 2, 2)),
                     ("高へ寄せる", (1, 1, 3))):
        t = bo3_pool(Rng(sum(w) * 977 + 31), w)
        if not t:
            continue
        per = {k: sum(winrate(t[k], o[k], seeds=40) for o in panel) // len(panel)
               for k in REGULATIONS}
        m = sum(bo3_match(t, o, seeds=40) for o in panel) // len(panel)
        print(f"    {label:<12} 1戦 低{per['low']:>3}% 中{per['mid']:>3}% "
              f"高{per['high']:>3}%  →  マッチ {m:>3}%")


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
        card = roster.build_card((person, "検証", cost, troop, role, skill, []))
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


def cmd_skillprice():
    """必殺技の価格式が実測と合っているかを見る（§7.5）。

    80枚それぞれに固有の必殺技を作ると、反復での価格付けは回らない。
    **機構ごとに換算係数を一度測り、あとは計算で出す。** その式が実測を
    どこまで説明できるかがここで分かる。合わない技があれば、そこが穴である。
    """
    import roster
    print("=== 必殺技の価格式 ===")
    print("  価格 = 発動回数 × Σ(効果量 × 実効重み × min(持続, 残り時間) × 実効対象数)")
    print(f"  戦闘{roster.BATTLE_TICKS/10:.0f}秒 / ゲージ満タン"
          f"{roster.GAUGE_FILL_TICKS/10:.0f}秒 → 発動"
          f"{roster.BATTLE_TICKS/roster.GAUGE_FILL_TICKS:.2f}回")
    print(f"  1回目の発動時点で残り{(roster.BATTLE_TICKS-roster.GAUGE_FILL_TICKS)/10:.0f}秒"
          "しかない。持続効果はここで頭打ちになる。\n")

    keys = [k for k in roster.SKILLS]
    raw = {k: roster.skill_value(roster.SKILLS[k]) for k in keys}
    attacks = roster.BATTLE_TICKS / roster.REF_INTERVAL
    xs = [raw[k] / attacks * 100 for k in keys]
    ys = [roster.SKILL_ADJUST.get(k, 0.0) for k in keys]
    # 実測へ最小二乗で合わせる（傾きと切片）
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    slope = sxy / sxx if sxx else 0.0
    base = my - slope * mx
    syy = sum((b - my) ** 2 for b in ys)
    r = sxy / (sxx * syy) ** 0.5 if sxx and syy else 0.0

    print(f"  {'技':<8}{'式(生)':>9}{'式(較正後)':>12}{'実測':>9}{'残差':>8}")
    rows = sorted(keys, key=lambda k: -(slope * (raw[k] / attacks * 100) + base))
    for k in rows:
        fitted = slope * (raw[k] / attacks * 100) + base
        m = roster.SKILL_ADJUST.get(k, 0.0)
        print(f"  {k:<8}{raw[k]/attacks*100:>8.1f}%{fitted:>11.2f}{m:>+9.2f}"
              f"{fitted - m:>+8.2f}")
    print(f"\n  相関 r = {r:.2f}   傾き {slope:.3f} / 切片 {base:+.2f}")
    print(f"  roster.py へ貼る値: SKILL_PRICE_SCALE = {slope:.3f}"
          f" / SKILL_PRICE_BASE = {base:.2f}")
    worst = max(keys, key=lambda k: abs(slope * (raw[k] / attacks * 100) + base
                                        - roster.SKILL_ADJUST.get(k, 0.0)))
    print(f"  最大の残差: {worst}")

    print("\n  発動回数を決める3つのパラメータ（strike を例に）")
    print("  **個別に価格を付けない。** どれも発動回数にしか効かず、その価値は")
    print("  技の中身と掛け算になるので、発動回数へ畳んでから技の価格に入れる。\n")
    print(f"  {'条件':<22}{'時間だけの計算':>15}{'実測に合わせた式':>18}{'価格':>9}")
    cases = [("消費50%", dict(gauge_cost=50)),
             ("消費75%", dict(gauge_cost=75)),
             ("消費100%（標準）", dict()),
             ("消費150%", dict(gauge_cost=150)),
             ("消費200%", dict(gauge_cost=200)),
             ("獲得速度130%", dict(gauge_rate=130)),
             ("獲得速度70%", dict(gauge_rate=70)),
             ("初期ゲージ50%", dict(gauge_start=50)),
             ("消費150%+速度150%", dict(gauge_cost=150, gauge_rate=150))]
    for label, kw in cases:
        cost = kw.get("gauge_cost", 100)
        rate = kw.get("gauge_rate", 100)
        naive = ((roster.BATTLE_TICKS + kw.get("gauge_start", 0)
                  * roster.GAUGE_TICKS_PER_PCT * 100 / rate)
                 / (cost * roster.GAUGE_TICKS_PER_PCT * 100 / rate))
        real = roster.casts_for(cost, rate, kw.get("gauge_start", 0))
        v = roster.skill_value(roster.SKILLS["strike"], cost, rate,
                               kw.get("gauge_start", 0))
        print(f"  {label:<22}{naive:>13.2f}回{real:>16.2f}回"
              f"{slope*v/attacks*100+base:>8.2f}")
    print("\n  **計算より多く撃てる。** ゲージは時間経過だけでなく与被ダメージと")
    print("  撃破からも入るためで、軽い技ほど上乗せが効く（1.39倍→1.05倍）。")
    print("  重い技も成立する。消費150%は速度150%か初期ゲージ50%と組み合わせれば")
    print("  標準と同じ1.2回前後まで戻せる。")


def cmd_amplify():
    """増幅の強さを測る（§4.6）。総合値の差が勝率へどれだけ拡大されるか。

    **基準を2つ先に置く。** 勝率の立ち上がりの急さが問題であって、強い側が
    勝つこと自体は正しい。基準がないと「急すぎる」を判定できない。

      1. 増幅なしの下限: 強さがそのまま勝率になるモデル s/(s+1)。+3% で 50.7%。
      2. 設計側の上限: 中コスト戦の上限30なので 1コスト = 総合値の約3.3%。
         三すくみの読み勝ちですら1戦71%まで（§5.3）なので、1コスト差はそれ以下。
    """
    import roster
    print("=== 増幅の測定 ===")
    print("  総合値だけを変えた編成を等倍の編成と戦わせ、勝率の立ち上がりを見る。")
    print("  増幅なし（s/(s+1)）なら +10% でも 52.4% にしかならない。")
    print("  実際の対戦ゲームはそれより急でよい。問題は急すぎることである。\n")

    def score_of(team):
        c = team["units"][0]["card"]
        t = roster.TROOP["inf"]
        return roster.effective_score(c["hp"], c["atk"], c["dfn"], t["interval"],
                                      c["acc"], c["crit"], roster.evade_of("inf"))

    base = scaled_team(1.0, tag="ampbase")
    base_score = score_of(base)
    rows = []
    print(f"  {'要求':>5}{'実現':>7}{'勝率':>7}{'増幅なし':>9}")
    for pct in (2, 4, 5, 6, 7, 8, 10, 12, 15):
        team = scaled_team(1 + pct / 100, tag=f"amp{pct}")
        real = (score_of(team) / base_score - 1) * 100
        wr = winrate(team, base, seeds=300)
        rows.append((real, wr))
        bt = (1 + pct / 100) / (2 + pct / 100) * 100
        print(f"  {pct:>+4}%{real:>+6.1f}%{wr:>6}%{bt:>8.1f}%")

    def interp(target, xs):
        prev = (0.0, 50.0)
        for real, wr in rows:
            if (wr >= target) if xs else (real >= target):
                key = wr if xs else real
                span = key - (prev[1] if xs else prev[0])
                f = (target - (prev[1] if xs else prev[0])) / max(1e-9, span)
                return (prev[0] + (real - prev[0]) * f if xs
                        else prev[1] + (wr - prev[1]) * f)
            prev = (real, wr)
        return None

    width = interp(75, True)
    print(f"\n  決定幅（勝率75%に必要な総合値差）: "
          f"{f'{width:.1f}%' if width else '15%超'}  ← 大きいほど許容誤差が広い")
    print(f"  1コスト相当（総合値3.3%）の勝率: {interp(3.3, False):.0f}%")

    print("\n  実際のコストでも測る（無個性カード6枚・合計コストだけを変える）")
    for a, b in ((30, 29), (30, 27), (30, 24)):
        def flat(total, tag):
            cards = [vanilla(c, "inf", cid=f"{tag}{i}")
                     for i, c in enumerate([5, 5, 5, 5, 5, total - 25])]
            for c in cards:
                CARDS[c["id"]] = c
            return make_team([c["id"] for c in cards], commander=4)
        print(f"    合計{a} 対 合計{b}（{a-b}コスト差）: "
              f"{winrate(flat(a, f'ca{a}'), flat(b, f'cb{b}'), seeds=200):>3}%")

    print("\n  **緩和策は v0.5 末に案A〜Fを実測し、案D（現状維持）を採った。**")
    print("  増幅を緩める案はどれも三すくみを同じ向きに壊す（歩兵→騎兵が反転）。")
    print("  三すくみの +4% が 67% になるのも60秒の複利によるもので、")
    print("  **増幅と三すくみは同じ源から出ている**。詳細は仕様書 §4.6。")


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
             "support": cmd_support, "bo3": cmd_bo3, "factions": cmd_factions,
             "amplify": cmd_amplify, "skillprice": cmd_skillprice,
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
    card = roster.build_card((person, "検証", cost, troop, "bruiser", "strike", []))
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
