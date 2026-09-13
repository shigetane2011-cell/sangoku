# -*- coding: utf-8 -*-
"""tools/damage_curve.py -- 正面狙いの値打ちが一撃の大きさでどう伸びるか（§7.214）

    python3 tools/damage_curve.py                 # 既定9枚 × 生の一撃5段
    python3 tools/damage_curve.py --seeds 2       # 種を減らして速く

================================================================================
 何を決めるための計器か
================================================================================
打撃の値段は `damage_price(威力) = K × 威力^0.9512` という**単一の冪**である。
§7.213 で「大きい一撃では値打ちの伸びが寝るのではないか」という疑いが出たが、
あれは身体2枚の比で見たもので、**錨（正面）自身の伸びは測っていなかった**。

ここで比べるのは2つだけ（テストプレイの指示）:

    (a) いまの**単一の冪**             値打ち = a × 一撃^e
    (b) **大きい一撃で伸びが緩くなる**曲線  値打ち = a × 一撃^e ÷ (1 + 一撃/H)

**測った範囲で誤差が小さい、簡単なほうを採る。** (b) が (a) を意味のある差で
上回らなければ (a) のままにする。

================================================================================
 測り方
================================================================================
- **身体と発動周期を固定して、一撃だけを振る。** 対象は全部 `敵1体（正面）`。
  身体は 6〜9 枚（低・中・高コスト、武力寄り・知力寄り）を**大技段に揃える**
  （消費300・初期140。段が違うと発動回数が変わり、伸びの比較に混ざる）。
- 一撃の梯子は**盤面で実際に使われている範囲**に置く。打ち切りダメージ64枚の
  生の一撃（威力 × 兵法係数）は 37〜4,889・中央880 なので、200〜4,500 を採る。
  **10,000 のような盤面に無い大きさで曲線を決めない。**
- 身体ごとに `威力% = 目標の一撃 × 100 ÷ 兵法係数` を解いて、**別々の身体が
  同じ生の一撃で並ぶ**ようにする。これで「生の一撃だけで説明できるか」も見える
  （身体ごとに線が分かれるなら、生の一撃だけでは足りない）。
- 誤差は**土台と相手をかたまりごと**選び直して取る（§7.213 の訂正）。
"""

from __future__ import annotations

import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PANEL_BASES", "12")

from multiprocessing import Pool      # noqa: E402
from dataclasses import replace       # noqa: E402

from sim import field as F            # noqa: E402
from sim import rosterdata as R       # noqa: E402
import tools.skill_panel as SP        # noqa: E402
import tools.target_price as TP       # noqa: E402

TARGET = "敵1体（正面）"
# 生の一撃の梯子。盤面の実分布（37〜4,889・中央880・95%点3,696）の中に置く。
HITS = (200.0, 500.0, 1200.0, 2500.0, 4500.0)
# 身体9枚。**全部 大技段（消費300・初期140）**。コスト帯と知力の傾きを散らす。
BODIES = ("董襲〔断纜〕", "丁奉〔雪中〕", "劉曄〔智嚢〕", "高覧〔四庭柱〕",
          "王双〔大刀〕", "荀攸〔謀主〕", "関平〔麒麟児〕", "孫尚香〔弓腰姫〕",
          "黄忠〔定軍山〕")

_S = {}


def skill_coef(name):
    """その札の兵法係数＝武力(1−v) + 知力·v（v は兵法種で決まる）。"""
    g = [x for x in R.generals() if x["名前"] == name][0]
    s = [r for r in R.skills() if r["武将"] == name][0]
    v = F.SKILL_WITS[F.SKILL_INFO[s["兵法名"]].kind]
    return float(g["武力"]) * (1.0 - v) + float(g["知力"]) * v


def _init(seeds):
    SP._init(12, seeds)
    _S.update(bases=SP._S["bases"], opps=SP._S["opps"], cards=SP._S["cards"], seeds=seeds)


def _one(job):
    name, power = job          # power は 威力%（None なら兵法なし）
    c = _S["cards"][name]
    row = [r for r in R.skills() if r["武将"] == name][0]
    sk = row["兵法名"]
    with F.unscaled():         # 毎回 名簿へ戻してから差し替える（工程の使い回し）
        F.SKILL_INFO[sk] = F._parse_skill(row["効果"], row["対象"])
    F.SKILL_TARGET[sk] = row["対象"]
    if power is None:
        c = replace(c, skill="")
    else:
        with F.unscaled():
            # **威力は整数で書く。小数は黙って消える**（落とし穴44・§7.189）。
            # 一度これで全段が同じ値になり、伸びがゼロに見えた。
            F.SKILL_INFO[sk] = F._parse_skill("ダメージ 威力{:d}%".format(int(power)), TARGET)
        F.SKILL_TARGET[sk] = TARGET
    d = []
    for base in _S["bases"]:
        a = SP._swap_into(base, c)
        for i, o in enumerate(_S["opps"]):
            d.append(F.simulate(a, o, dt=SP.DT, seed=i * 7 + 1)["diff"])
    return d


def fit(points, H=None):
    """log(値打ち) = log a_i + e·log(一撃) [− log(1 + 一撃/H)] を最小二乗で解く。

    a_i は身体ごとの目盛り（身体で盤面の重さが違うので共通にしない）。
    e は**全身体で共通**の1つ。返すのは (e, 残差の標準偏差)。
    """
    by = {}
    for name, h, v in points:
        if v <= 0:
            continue
        y = math.log(v) + (math.log1p(h / H) if H else 0.0)
        by.setdefault(name, []).append((math.log(h), y))
    # 身体ごとに中心化すると a_i が消え、e だけの単回帰になる
    sxx = sxy = 0.0
    for rows in by.values():
        if len(rows) < 2:
            continue
        mx = statistics.mean(r[0] for r in rows)
        my = statistics.mean(r[1] for r in rows)
        for x, y in rows:
            sxx += (x - mx) ** 2
            sxy += (x - mx) * (y - my)
    if sxx <= 0:
        return float("nan"), float("nan")
    e = sxy / sxx
    res = []
    for rows in by.values():
        if len(rows) < 2:
            continue
        mx = statistics.mean(r[0] for r in rows)
        my = statistics.mean(r[1] for r in rows)
        res += [(y - my) - e * (x - mx) for x, y in rows]
    return e, statistics.pstdev(res)


def main():
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 3
    R.load_skills_into_field(); R.load_traits_into_field()
    coef = {n: skill_coef(n) for n in BODIES}
    jobs = [(n, None) for n in BODIES]
    plan = {}
    real = {}
    for n in BODIES:
        for h in HITS:
            p = max(1, int(round(h * 100.0 / coef[n])))   # 整数の威力%
            plan[(n, h)] = p
            real[(n, h)] = p * coef[n] / 100.0            # 丸めた後の**実際の**一撃
            jobs.append((n, p))
    print("{}枚 × 一撃{}段 ＝ {}案 × 土台12 × 性格12 × 種{}（1案 {}局）".format(
        len(BODIES), len(HITS), len(jobs), seeds, 12 * 12 * seeds), flush=True)
    print("身体（すべて大技段・消費300/初期140）")
    print("  {:<16}{:>4}{:>6}{:>9}{:>8}   {}".format("武将", "c", "兵種", "兵法係数", "傾き", "→ 威力%"))
    rows = {g["名前"]: g for g in R.generals()}
    for n in BODIES:
        g = rows[n]
        print("  {:<16}{:>4}{:>6}{:>9.1f}{:>8.3f}   {}".format(
            n, g["コスト"], g["兵種"], coef[n], float(g["知力傾き"]),
            " / ".join("{:d}".format(plan[(n, h)]) for h in HITS)))

    for n in BODIES:
        ps = [plan[(n, h)] for h in HITS]
        if len(set(ps)) != len(ps):
            raise SystemExit("{} の威力が丸めで重複した: {} — 梯子か身体を見直すこと".format(n, ps))
    jobs = list(dict.fromkeys(jobs))     # 念のため重複を落とす

    pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
    got = dict(zip(jobs, pool.map(_one, jobs)))
    pool.close()

    import json
    out = "/tmp/claude-0/damage_curve_raw.json"
    if "--dump" in sys.argv:
        out = sys.argv[sys.argv.index("--dump") + 1]
    json.dump({"{}|{}".format(k[0], k[1]): v for k, v in got.items()}, open(out, "w"))
    print("\n生データ: {}".format(out), flush=True)

    ALL = TP.game_index(seeds, range(TP.NBASE), range(TP.NPERS))
    rng = random.Random(4649)
    draws = [TP.cluster_draw(rng, seeds) for _ in range(600)]

    def val(name, h, ix):
        o = got[(name, None)]
        a = got[(name, plan[(name, h)])]
        return statistics.mean(a[i] - o[i] for i in ix)

    print("\n値打ち（兵法ありなしの残存差・1コスト点へは直さない）")
    print("{:<16}{:>10}{:>10}{:>10}{:>10}{:>10}".format(
        "武将", *["一撃{:.0f}".format(h) for h in HITS]))
    for n in BODIES:
        print("{:<16}".format(n) + "".join("{:>10.4f}".format(val(n, h, ALL)) for h in HITS))

    pts = [(n, real[(n, h)], val(n, h, ALL)) for n in BODIES for h in HITS]
    e_a, sd_a = fit(pts)
    best = None
    for H in [10 ** (2.0 + 0.05 * i) for i in range(61)]:      # 100 〜 100,000
        e, sd = fit(pts, H)
        if best is None or sd < best[2]:
            best = (H, e, sd)
    print("\n(a) 単一の冪            値打ち ∝ 一撃^{:.3f}          残差の標準偏差 {:.4f}".format(e_a, sd_a))
    print("(b) 大きいと寝る曲線     値打ち ∝ 一撃^{:.3f} ÷ (1+一撃/{:.0f})  残差の標準偏差 {:.4f}".format(
        best[1], best[0], best[2]))
    print("    改善 {:.1%}（値段の式はいまの冪 0.9512）".format(1 - best[2] / sd_a if sd_a else 0))

    def band(pts_, lo, hi):
        return [q for q in pts_ if lo <= q[1] <= hi]

    LO, MID, HI = HITS[0] * 0.9, HITS[2] * 1.1, HITS[-1] * 1.1
    e_lo, _ = fit(band(pts, LO, MID))
    e_hi, _ = fit(band(pts, HITS[2] * 0.9, HI))
    print("\n形を仮定せずに見る（低い側と高い側で別々に冪を出す）")
    print("  小さい一撃（〜{:.0f}）の冪 {:.3f}   大きい一撃（{:.0f}〜）の冪 {:.3f}   差 {:+.3f}".format(
        MID, e_lo, HITS[2] * 0.9, e_hi, e_hi - e_lo))

    boots, bl, bh, bdiff = [], [], [], []
    for ix in draws:
        p2 = [(n, real[(n, h)], val(n, h, ix)) for n in BODIES for h in HITS]
        boots.append(fit(p2)[0])
        a = fit(band(p2, LO, MID))[0]
        b = fit(band(p2, HITS[2] * 0.9, HI))[0]
        bl.append(a); bh.append(b); bdiff.append(b - a)
    q = lambda z, f: sorted(x for x in z if x == x)[int(f * len(z))]
    print("\n95%区間（**土台と相手をかたまりごと**選び直す・{}回）".format(len(draws)))
    print("  単一の冪        {:.3f}  [{:.3f}, {:.3f}]   （値段の式は 0.9512）".format(
        e_a, q(boots, .025), q(boots, .975)))
    print("  小さい一撃の冪   {:.3f}  [{:.3f}, {:.3f}]".format(e_lo, q(bl, .025), q(bl, .975)))
    print("  大きい一撃の冪   {:.3f}  [{:.3f}, {:.3f}]".format(e_hi, q(bh, .025), q(bh, .975)))
    print("  差（大−小）     {:+.3f}  [{:+.3f}, {:+.3f}]   負の割合 {:.1%}".format(
        e_hi - e_lo, q(bdiff, .025), q(bdiff, .975),
        sum(1 for x in bdiff if x < 0) / len(bdiff)))
    print("  ※ 割合＝再抽出のうち『大きい側の冪 < 小さい側の冪』と出た回の割合。")
    print("     頻度論のp値でも、仮説が正しい確率でもない。")

    print("\n身体ごとの冪（ばらけ具合。生の一撃だけで説明できるなら揃うはず）")
    for n in BODIES:
        e, _ = fit([(n, real[(n, h)], val(n, h, ALL)) for h in HITS])
        print("  {:<16}{:>7.3f}".format(n, e))
    print("CURVE DONE")


if __name__ == "__main__":
    main()
