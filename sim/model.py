#!/usr/bin/env python3
"""最小モデル。**下から積み上げて、どのパラメータがコスト等価を壊すかを見る。**

usage:
  python3 sim/model.py ladder    素の状態から1つずつ機能を戻し、どれが壊すか
  python3 sim/model.py lanes     レーン数だけを振る
  python3 sim/model.py rules     ダメージの配り方 × レーン数 × 威力の総当たり
  python3 sim/model.py width     レーンを廃した1本の戦線。接敵幅を振る

----------------------------------------------------------------------------
結論（rules の実測。威力/コストを 0.3〜0.01 で振っても符号が変わらないもの）
----------------------------------------------------------------------------

**コストへの厳密比例は必要だが、足りない。ダメージの配り方が決める。**

| 配り方 | レーン | 2体×15 対 6体×5 | 30体×1 対 6体×5 |
| --- | --- | --- | --- |
| 集中砲火（余りは次の敵へ） | 1 | +15〜+33% | −19〜−28% |
| 集中砲火（余りは次の敵へ） | 3 | +17〜+33% | −25〜−42% |
| 集中砲火（余りは捨てる） | 1 | 符号が反転する | 符号が反転する |
| 集中砲火（余りは捨てる） | 3 | +7〜+33% | 符号が反転する |
| **均等割り** | **1** | **0.00%** | **0.00%** |
| 均等割り | 3 | +7〜+22% | 0.00% |

1. **集中砲火だと二乗則が効き、少数精鋭が勝つ。** 総コストが同じでも
   2体×15 が 6体×5 に3割勝ち越す。レーン数によらない。
2. **均等割り・1レーンだけが人数について厳密に等価**（どの威力でも 0.00%）。
   ただし枠ごとの配分（7/3, 9/1）は威力を下げると −11% まで漂う。
3. **レーンを3本にすると、均等割りでも人数の等価が壊れる**（2体×15 が +20%）。
4. **「余りは捨てる」（いまの engine）は威力で符号が反転する。** つまり
   1撃の大きさと兵力の比で balance の向きが変わる。コスト1の札とコスト10の札が
   別々の regime に住むことになり、**通分できない原因になっている可能性が高い。**

**位置取りに意味を持たせる仕組みは、どれもコスト等価を壊す。**
どこまで等価を捨てて位置の depth を買うか、という設計判断になる。

----------------------------------------------------------------------------
この測定で2回間違えた（両方とも「完全に均衡なら何が出るか」を怠った形）
----------------------------------------------------------------------------

1. **残存を満額で数えた。** 兵力 0.01 で生き残ったコスト10の札を「10残った」と
   数え、実体のない +33% が出た。兵力の割合で数えるべきだった。
2. **威力＝コストにして退化させた。** 攻撃力＝兵力＝コストにすると、1周期の
   総ダメージが相手の総兵力とちょうど等しくなり、**どの条件でも1周期で相打ち**に
   なる。全条件が 0.00% に並び、「レーンも人数も完全に等価」と読みかけた。
   攻撃周期のテストが 1.2倍でも 1.7倍でも 0.00% だったのが手掛かりになった。
   **全部ゼロが並んだら、まず計器を疑う。**

----------------------------------------------------------------------------
なぜ engine.py で測らないか
----------------------------------------------------------------------------

engine.py には20以上の仕組みが同時に入っていて、どれが効いているか分けられない。
**順番が逆だった。** 先に「コストが等価になる最小の形」を作り、そこへ1つずつ
戻して、どれが等価を壊すかを見る。シミュレータは導出を反証するために使う。

----------------------------------------------------------------------------
土台の主張
----------------------------------------------------------------------------

攻撃力 a = k·コスト、兵力 h = k·コスト、攻撃周期が同じなら、集中砲火の戦闘の
戦力は (Σa)(Σh) = k²(Σコスト)² になる。**総コストだけで決まり、
配分にも人数にも依存しない。**

いまの roster.py はこれをやっていない。予算が Σᵢ(hᵢ·aᵢ)（1枚ごとに掛けてから
足す）なのに対し、戦闘は (Σh)(Σa)（足してから掛ける）で決まる。この2つは
全員同じ能力値のときしか一致しない。実測では総コスト30で配分だけ変えると
予算は 1.25M で不動、戦闘側は 0.988 倍まで落ち、勝率は 50%→18% になった。

----------------------------------------------------------------------------
距離は相対で持つ
----------------------------------------------------------------------------

**絶対単位（LANE_DEPTH=1000, range=100, speed=8）では通分できない。**
すべて開戦時の彼我距離 D=1 に対する比で持つ。
  reach  射程 / D      1.0 なら開戦と同時に全員が射程内
  speed  1周期に詰める距離 / D
こうすると「射程が長い」は「何周期ぶん近づかずに済むか」に翻訳でき、
攻撃周期・移動速度・初期配置が同じ土俵に乗る。
"""
import sys

TOTAL = 30
EVEN = [5, 5, 5, 5, 5, 5]


def resolve(costs_a, costs_b, *, lanes=1, reach=1.0, speed=1.0,
            cycle_a=1.0, cycle_b=1.0, focus=True, overkill="flow",
            lethality=0.1, max_cycles=10000):
    """1戦を決定論的に解き、残存コストの差（％）を返す。

    0 なら完全に等価。正なら A が勝ち越し。**乱数を使わないので誤差はない。**

    lethality は「1撃の威力 ÷ コスト」。**1.0 にしてはいけない。**
    攻撃力＝兵力＝コストにすると、各陣営の1周期あたり総ダメージが相手の総兵力と
    等しくなり、**どの条件でも1周期で相打ちになる**。それに気付かず
    「レーンも人数も 0.00% で完全に等価」と読みかけた。決着が一瞬で相殺して
    いただけである。0.1 なら10周期以上かかり、攻防が実際に展開する。
    """
    def make(costs, side):
        return [{"lane": i % lanes, "hp": float(c), "max_hp": float(c),
                 "atk": float(c) * lethality, "cost": float(c),
                 "pos": 0.0 if side == 0 else 1.0, "side": side}
                for i, c in enumerate(costs)]

    units = make(costs_a, 0) + make(costs_b, 1)
    acc = [0.0, 0.0]                       # 攻撃周期の端数を持ち越す
    for _ in range(max_cycles):
        alive = [u for u in units if u["hp"] > 0]
        if not any(u["side"] == 0 for u in alive) or \
           not any(u["side"] == 1 for u in alive):
            break
        # --- 移動: 同レーンの敵が射程に入るまで詰める
        for u in alive:
            foes = [e for e in alive if e["side"] != u["side"]
                    and e["lane"] == u["lane"]]
            if not foes:
                continue
            gap = min(abs(e["pos"] - u["pos"]) for e in foes)
            if gap > reach:
                step = min(speed, gap - reach)
                tgt = min(foes, key=lambda e: abs(e["pos"] - u["pos"]))
                u["pos"] += step if tgt["pos"] > u["pos"] else -step
        # --- 攻撃: 射程内の敵へ、同時に解決する
        dealt = {}
        for side, cyc in ((0, cycle_a), (1, cycle_b)):
            acc[side] += 1.0 / cyc
            shots = int(acc[side])
            acc[side] -= shots
            if not shots:
                continue
            for u in alive:
                if u["side"] != side:
                    continue
                foes = [e for e in alive if e["side"] != side
                        and e["lane"] == u["lane"]
                        and abs(e["pos"] - u["pos"]) <= reach]
                if not foes:
                    continue
                # 集中砲火は残り兵力の少ない敵から。過剰分は次の敵へ流す
                order = sorted(foes, key=lambda e: (e["hp"], id(e))) if focus \
                    else foes
                pool = u["atk"] * shots
                if not focus:
                    share = pool / len(order)
                    for e in order:
                        dealt[id(e)] = dealt.get(id(e), 0.0) + share
                    continue
                if overkill == "waste":
                    # 1体だけを殴り、余った分は捨てる（いまの engine と同じ）
                    e = order[0]
                    dealt[id(e)] = dealt.get(id(e), 0.0) + pool
                    continue
                for e in order:
                    if pool <= 0:
                        break
                    left = e["hp"] - dealt.get(id(e), 0.0)
                    if left <= 0:
                        continue
                    hit = min(pool, left)
                    dealt[id(e)] = dealt.get(id(e), 0.0) + hit
                    pool -= hit
        for u in alive:
            u["hp"] -= dealt.get(id(u), 0.0)
    # **残存は兵力の割合で数える。** 満額で数えると、兵力が 0.01 でも生きていれば
    # コスト10ぶん残ったことになり、実体のない差が出る（最初これで +33% が出た）。
    left = [sum(u["cost"] * u["hp"] / u["max_hp"]
                for u in units if u["side"] == s and u["hp"] > 0)
            for s in (0, 1)]
    return (left[0] - left[1]) * 100 / sum(costs_a)


def spreads(total=TOTAL):
    """総コストを固定したまま、配分だけを変えた編成を並べる。"""
    out = [("均等 5/5/5/5/5/5", EVEN)]
    for a, b in ((6, 4), (7, 3), (8, 2), (9, 1)):
        out.append((f"2枠だけ動かす {a}/{b}", [a, b, 5, 5, 5, 5]))
    out.append(("全体を偏らせる 10/9/8/1/1/1", [10, 9, 8, 1, 1, 1]))
    # レーン単位の偏り。3レーンなら枠 i はレーン i%3 に入るので、
    # 下は「レーン0へ寄せた」形になる（枠0と枠3がレーン0）。
    out.append(("1レーンへ寄せる 10/1/1/10/4/4", [10, 1, 1, 10, 4, 4]))
    return out


def run(title, **kw):
    print(f"  {title}")
    for label, costs in spreads():
        m = resolve(costs, EVEN, **kw)
        flag = "" if abs(m) < 0.5 else "  ← 壊れている"
        print(f"    {label:<20} 残存コスト差 {m:+7.2f}%{flag}")
    print()


def cmd_ladder():
    print("=== 積み上げ: どの機能がコスト等価を壊すか ===")
    print("  攻撃力・兵力はコストに厳密比例（切片ゼロ）。総コストは常に30で固定。")
    print("  **完全に通分できていれば、残存コスト差は全部 0.00% になる。**")
    print("  相手は常に均等割り 5/5/5/5/5/5。乱数なし・決定論。\n")
    run("[1] 素の形（1レーン・開戦と同時に全員が射程内・周期同じ）",
        lanes=1, reach=1.0, speed=1.0)
    run("[2] レーンを3本に分ける（他は素のまま）",
        lanes=3, reach=1.0, speed=1.0)
    run("[3] 射程を絞る（1レーン・射程 0.1D・速度 0.1D/周期）",
        lanes=1, reach=0.1, speed=0.1)
    run("[4] 3レーン ＋ 射程を絞る（いまの engine に近い）",
        lanes=3, reach=0.1, speed=0.1)
    run("[5] 素の形だが、集中砲火をやめて均等割りにする",
        lanes=1, reach=1.0, speed=1.0, focus=False)


def by_lane(totals):
    """レーンごとの合計コストを指定して6枠を作る（3レーン×2枠、枠 i はレーン i%3）。"""
    costs = [0.0] * 6
    for lane, t in enumerate(totals):
        costs[lane] = t / 2
        costs[lane + 3] = t / 2
    return costs


def cmd_lanes():
    print("=== レーンへの配分を振る（3レーン・素の形）===")
    print("  攻撃力・兵力はコストに比例。総コストは常に30で固定。")
    print("  相手は 10/10/10（均等）。**通分できていれば全部 0.00%。**")
    print()
    print("  注意: 最初に選んだ 20/5/5 はたまたま完全均衡点だった。")
    print("  A が sqrt(20²−10²)=17.32 残し、B が 2·sqrt(10²−5²)=17.32 残す。")
    print("  m=2y のときだけ一致する偶然で、**1点だけ見て「壊れない」と")
    print("  読んではいけない**例である。範囲を掃く。\n")
    for totals in ((10, 10, 10), (12, 9, 9), (14, 8, 8), (16, 7, 7),
                   (18, 6, 6), (20, 5, 5), (22, 4, 4), (24, 3, 3),
                   (15, 15, 0.01), (12, 12, 6)):
        m = resolve(by_lane(totals), by_lane((10, 10, 10)),
                    lanes=3, reach=1.0, speed=1.0)
        flag = "" if abs(m) < 0.5 else "  ← 壊れている"
        print(f"    レーン配分 {str(totals):<16} 残存コスト差 {m:+7.2f}%{flag}")
    print()
    print("  参考: 同じ配分を1レーン（レーン分割なし）で解くと——")
    for totals in ((18, 6, 6), (24, 3, 3), (15, 15, 0.01)):
        m = resolve(by_lane(totals), by_lane((10, 10, 10)),
                    lanes=1, reach=1.0, speed=1.0)
        print(f"    レーン配分 {str(totals):<16} 残存コスト差 {m:+7.2f}%")


def resolve_line(costs_a, costs_b, *, width=2, lethality=0.1, focus=True,
                 overkill="flow", max_cycles=10000):
    """レーンを廃した1本の戦線。**同時に接敵できるのは各軍 width 人まで。**

    レーンという離散の仕切りを持たない。並び順が縦深で、先頭から width 人が
    戦線に立ち、倒れると次が繰り上がる。三国志の会戦の形であり、同時に
    「集中を制限する」ことでもあるので、コストの加法性がどう動くかを見る。

    width=6 は全員が同時に殴り合う＝二乗則がそのまま効く形。
    width=1 は一騎討ちの列＝完全な線形則。そのあいだを掃く。
    """
    def make(costs):
        return [{"hp": float(c), "max_hp": float(c), "atk": float(c) * lethality,
                 "cost": float(c)} for c in costs]

    sides = [make(costs_a), make(costs_b)]
    for _ in range(max_cycles):
        live = [[u for u in s if u["hp"] > 0] for s in sides]
        if not live[0] or not live[1]:
            break
        front = [ls[:width] for ls in live]      # 先頭から width 人が戦線に立つ
        dealt = {}
        for side in (0, 1):
            foes = front[1 - side]
            if not foes:
                continue
            order = sorted(foes, key=lambda e: (e["hp"], id(e))) if focus else foes
            for u in front[side]:
                pool = u["atk"]
                if not focus:
                    for e in order:
                        dealt[id(e)] = dealt.get(id(e), 0.0) + pool / len(order)
                    continue
                if overkill == "waste":
                    dealt[id(order[0])] = dealt.get(id(order[0]), 0.0) + pool
                    continue
                for e in order:
                    if pool <= 0:
                        break
                    left = e["hp"] - dealt.get(id(e), 0.0)
                    if left <= 0:
                        continue
                    hit = min(pool, left)
                    dealt[id(e)] = dealt.get(id(e), 0.0) + hit
                    pool -= hit
        for s in sides:
            for u in s:
                u["hp"] -= dealt.get(id(u), 0.0)
    left = [sum(u["cost"] * u["hp"] / u["max_hp"] for u in s if u["hp"] > 0)
            for s in sides]
    return (left[0] - left[1]) * 100 / sum(costs_a)


def cmd_width():
    """接敵幅を振って、コストの加法性がどこで戻るかを見る。

    **完全に通分できていれば全部 0.00%。** 総コストは常に30で固定。
    レーンを廃したので、比べるのは「並び順のどこへコストを寄せるか」になる。
    """
    cases = (("先頭2枠 7/3", [7, 3, 5, 5, 5, 5]),
             ("先頭2枠 9/1", [9, 1, 5, 5, 5, 5]),
             ("先頭に寄せる 9/9/9/1/1/1", [9, 9, 9, 1, 1, 1]),
             ("後方に寄せる 1/1/1/9/9/9", [1, 1, 1, 9, 9, 9]),
             ("2体×15", [15, 15]), ("30体×1", [1] * 30))
    print("=== 接敵幅（レーンなし・1本の戦線）===")
    print("  攻撃力・兵力はコストに厳密比例。総コストは常に30。相手は 6体×5。")
    print("  **通分できていれば全部 0.00%。** 威力/コスト=0.01（実ゲームの動作点）\n")
    for width in (1, 2, 3, 4, 6):
        out = [f"{n} {resolve_line(c, EVEN, width=width, lethality=0.01):+7.2f}%"
               for n, c in cases]
        print(f"  接敵幅 {width}")
        print("    " + "  ".join(out[:3]))
        print("    " + "  ".join(out[3:]))
    print()
    print("  参考: 幅6は全員が同時に殴り合う＝いまのレーン内と同じ二乗則の形。")
    print("  幅1は一騎討ちの列＝線形則。")


def cmd_rules():
    """ダメージの配り方 × レーン数 × 威力。**符号が安定しているものだけ読む。**"""
    cases = (("7/3", [7, 3, 5, 5, 5, 5]), ("9/1", [9, 1, 5, 5, 5, 5]),
             ("2体×15", [15, 15]), ("30体×1", [1] * 30))
    rules = (("集中・余りを流す・1レーン", dict(lanes=1)),
             ("集中・余りを流す・3レーン", dict(lanes=3)),
             ("集中・余りを捨てる・1レーン", dict(lanes=1, overkill="waste")),
             ("集中・余りを捨てる・3レーン", dict(lanes=3, overkill="waste")),
             ("均等割り・1レーン", dict(lanes=1, focus=False)),
             ("均等割り・3レーン", dict(lanes=3, focus=False)))
    print("=== ダメージの配り方 × レーン数 ===")
    print("  攻撃力・兵力はコストに厳密比例。総コストは常に30。相手は 6体×5。")
    print("  **通分できていれば全部 0.00%。**")
    print("  威力/コストを振っても符号が変わらないものだけを結論に使う。\n")
    for label, kw in rules:
        print(f"  {label}")
        for leth in (0.3, 0.1, 0.03, 0.01):
            out = [f"{n} {resolve(c, EVEN, lethality=leth, **kw):+7.2f}%"
                   for n, c in cases]
            print(f"    威力{leth:<6} " + " ".join(out))
        print()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ladder"
    table = {"ladder": cmd_ladder, "lanes": cmd_lanes, "rules": cmd_rules,
             "width": cmd_width}
    if cmd not in table:
        sys.exit(__doc__)
    table[cmd]()
