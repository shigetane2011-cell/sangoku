# -*- coding: utf-8 -*-
"""tools/target_by_reg.py -- ② 対象指定の差を、レギュレーション別に測る（§7.215）

    python3 tools/target_by_reg.py                 # 18/30/40 の3つ
    python3 tools/target_by_reg.py --seeds 1       # 速く
    python3 tools/target_by_reg.py --regs 30       # 1つだけ

================================================================================
 設計（テストプレイの指示）
================================================================================
- **同じ身体・配置・周期で対象だけを変える。** 威力は身体ごとに「生の一撃
  （威力 × 兵法係数）が同じ」になるよう解く。§7.213 で対象の値打ちが一撃の
  大きさで動きうると出たので、**大きさを揃えないと対象の比較にならない**。
- **身体ごとの差を保持して集計する。** 群（前衛系・後衛系）で束ねるのは
  **分析のためだけ**で、値付けの分岐にはまだ使わない。
- **18・30・40 のレギュレーション別に出す。** 各レギュレーションの中では
  同じ身体・配置・周期・相手・種で対象だけを変える。
  **平均だけでなく、レギュレーションごとに優劣が逆転するかを見る。**
- 誤差は**土台と相手をかたまりごと**選び直す（§7.213 の訂正）。

【この計器が測っていないこと】レギュレーション別の値札は**作らない**。
差が出たら「共通の値札の誤り」なのか「その札の得意な条件」なのかを、
本番BO3での採用と戦力配分まで見て判断する（テストプレイの指示）。
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PANEL_BASES", "12")

from multiprocessing import Pool     # noqa: E402
from dataclasses import replace      # noqa: E402

from sim import design as DS         # noqa: E402
from sim import field as F           # noqa: E402
from sim import match as M           # noqa: E402
from sim import rosterdata as R      # noqa: E402
import tools.skill_panel as SP       # noqa: E402
import tools.target_price as TP      # noqa: E402

ANCHOR = "敵1体（正面）"
TARGETS = (ANCHOR, "敵1体（残兵力が最少）", "敵1体（前衛の主力）",
           "敵1体（知略が最高）", "敵1体（知略が最低）",
           "敵1列", "敵前衛", "敵全体")
# 身体4枚（前衛系2・後衛系2、コスト帯を散らす）。**群は分析用**。
BODIES = (("王双〔大刀〕", "前衛系"), ("関平〔麒麟児〕", "前衛系"),
          ("荀攸〔謀主〕", "後衛系"), ("黄忠〔定軍山〕", "後衛系"))
HIT = 1200.0        # 生の一撃を揃える（盤面の実分布 37〜4,889・中央880 の中）

_S = {}


def _init(seeds):
    SP._init(12, seeds)
    _S.update(bases=SP._S["bases"], opps=SP._S["opps"], cards=SP._S["cards"])


def _one(job):
    name, target, power = job
    c = _S["cards"][name]
    row = [r for r in R.skills() if r["武将"] == name][0]
    sk = row["兵法名"]
    with F.unscaled():                      # 毎回 名簿へ戻す（工程の使い回し）
        F.SKILL_INFO[sk] = F._parse_skill(row["効果"], row["対象"])
    F.SKILL_TARGET[sk] = row["対象"]
    if target is None:
        c = replace(c, skill="")
    else:
        with F.unscaled():                  # 威力は整数（落とし穴44）
            F.SKILL_INFO[sk] = F._parse_skill("ダメージ 威力{:d}%".format(power), target)
        F.SKILL_TARGET[sk] = target
    d = []
    for base in _S["bases"]:
        a = SP._swap_into(base, c)
        for i, o in enumerate(_S["opps"]):
            d.append(F.simulate(a, o, dt=SP.DT, seed=i * 7 + 1)["diff"])
    return d


def main():
    import tools.damage_curve as DC
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 2
    want = sys.argv[sys.argv.index("--regs") + 1].split(",") if "--regs" in sys.argv else None
    regs = [(n, c) for n, c in M.REGULATIONS if not want or "{:g}".format(c) in want]
    R.load_skills_into_field(); R.load_traits_into_field()
    coef = {n: DC.skill_coef(n) for n, _ in BODIES}
    power = {n: max(1, int(round(HIT * 100.0 / coef[n]))) for n, _ in BODIES}

    print("② 対象{}種 × 身体{}枚 × レギュレーション{}  （生の一撃を {:.0f} に揃える）".format(
        len(TARGETS), len(BODIES), "/".join("{:g}".format(c) for _, c in regs), HIT))
    print("  1案 {}局（土台12 × 性格12 × 種{}）・同じ土台・同じ相手・同じ種で対象だけを変える\n".format(
        12 * 12 * seeds, seeds))
    print("  {:<16}{:>6}{:>6}{:>9}{:>8}".format("身体", "群", "兵種", "兵法係数", "威力%"))
    rows = {g["名前"]: g for g in R.generals()}
    for n, grp in BODIES:
        print("  {:<16}{:>6}{:>6}{:>9.1f}{:>8}".format(
            n, grp, rows[n]["兵種"], coef[n], power[n]))

    out = {}
    for label, cap in regs:
        os.environ["PANEL_TOTAL"] = str(cap)
        SP.TOTAL = cap
        jobs = [(n, None, 0) for n, _ in BODIES]
        jobs += [(n, t, power[n]) for n, _ in BODIES for t in TARGETS]
        pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
        got = dict(zip(jobs, pool.map(_one, jobs)))
        pool.close(); pool.join()
        out["{:g}".format(cap)] = {"|".join(map(str, k)): v for k, v in got.items()}
        print("\n■ {}（上限 {:g}点）".format(label, cap), flush=True)
        report(got, coef, power, seeds)

    dump = (sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv
            else "/tmp/claude-0/target_by_reg_raw.json")
    json.dump(out, open(dump, "w"))
    print("\n生データ: {}".format(dump))
    print("REG DONE")


def report(got, coef, power, seeds):
    ALL = TP.game_index(seeds, range(TP.NBASE), range(TP.NPERS))
    rng = random.Random(31337)
    draws = [TP.cluster_draw(rng, seeds) for _ in range(400)]

    def dd(name, t, ix):
        o = got[(name, None, 0)]; a = got[(name, t, power[name])]
        return statistics.mean(a[i] - o[i] for i in ix)

    hdr = "{:<16}".format("身体") + "".join(
        "{:>13}".format(t.replace("敵1体", "").replace("（", "").replace("）", ""))
        for t in TARGETS)
    print(hdr)
    print("-" * len(hdr))
    for name, grp in BODIES:
        anch = dd(name, ANCHOR, ALL)
        cells = []
        for t in TARGETS:
            k = DS.TARGET_DMG_F[ANCHOR] * dd(name, t, ALL) / anch if anch else float("nan")
            if t == ANCHOR:
                cells.append("{:>13}".format("1.286 錨"))
                continue
            bs = sorted(x for x in (
                DS.TARGET_DMG_F[ANCHOR] * dd(name, t, ix) / dd(name, ANCHOR, ix)
                for ix in draws) if x == x)
            half = (bs[int(.975 * len(bs))] - bs[int(.025 * len(bs))]) / 2.0
            cells.append("{:>8.2f}±{:<4.2f}".format(k, half))
        print("{:<16}".format(name) + "".join(cells))
    print("{:<16}".format("いまの表") + "".join(
        "{:>13.3f}".format(DS.TARGET_DMG_F[t]) for t in TARGETS))


if __name__ == "__main__":
    main()
