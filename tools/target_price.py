# -*- coding: utf-8 -*-
"""tools/target_price.py -- 「対象指定」の値札を、一撃の大きさごとに測る（§7.213）

    python3 tools/target_price.py                       # 既定の2枚 × 3対象 × 3威力
    python3 tools/target_price.py --card 満寵〔剛毅〕     # 1枚だけ
    python3 tools/target_price.py --seeds 3             # 種を増やす

================================================================================
 なぜこの計器が要るか
================================================================================
`design.TARGET_DMG_F` は対象指定ごとに**1つの数**を持つ。§7.100 の掃引で
実測した表だが、**一撃の大きさに依らない**という前提が入っている。

その前提が怪しい。`敵1体（残兵力が最少）` は定義上いちばん弱った隊を狙うので
損害が残兵で頭打ちになり、大きい一撃ほど余りが出る。逆に余りは大技段では
**余勢**（TRAMPLE・§7.76）で第二対象へ半分抜けるので、「捨てている」とも
言い切れない。**大きさ × 対象の相互作用**を測らないと、どちらの効果が勝つか
分からない。

================================================================================
 測り方（テストプレイの設計・§7.213）
================================================================================
- **同じ身体・同じ発動周期**のまま、**効果文の対象と威力だけ**を振る。
  武将を跨いで比べない（能力値・相手の防御が混ざる）。
- 小・中・大の一撃で比べる。
- 相手は12性格を3つの型に束ねて別々にも読む:
  **薄く広く**（弱った敵が散る）／**硬い前衛**／**後衛に火力**。
- 主に見るのは **勝率と残存差**。実損害・超過・余勢・討ち取り・撃破時刻は
  **理由の分析**に使う（超過率だけで値打ちを判定しない — 100の敵を落として
  900余らせる一撃が、1,000削って生かす一撃より有効なことがある）。
- 出す数字は **正面を錨にした比**。`適正係数 = 1.286 × Δ残存(対象) / Δ残存(正面)`。
  大きさを通して比がほぼ動かなければ**一定係数でよい**（＝表を差し替えるだけ）。
  大きさで動くなら**係数を段で持つ**必要がある。

土台と相手の組み方は `tools/skill_panel.py` と同じ（実カードの性格デッキ12・
1組1局）。ただし**土台と相手を独立に総当たり**する（skill_panel は土台bと
性格bを組にするので、相手の型だけを切り出せない）。
"""

from __future__ import annotations

import os
import statistics
import sys
from dataclasses import replace
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PANEL_BASES", "12")

from sim import design as DS          # noqa: E402
from sim import dummies as D          # noqa: E402
from sim import field as F            # noqa: E402
from sim import rosterdata as R       # noqa: E402
import tools.skill_panel as SP        # noqa: E402

ANCHOR = "敵1体（正面）"
TARGETS = (ANCHOR, "敵1体（残兵力が最少）", "敵前衛")

# 相手の型（テストプレイの指定）。12性格を3つへ束ねる。
FAMILY = {
    "薄く広く": ("先手", "均衡", "猛攻", "処刑"),
    "硬い前衛": ("鉄壁", "重装", "槍陣", "疾風"),
    "後衛に火力": ("強弓", "謀弩", "斉射", "軍師"),
}
OF_PERSONA = {p: fam for fam, ps in FAMILY.items() for p in ps}

# 既定の2枚。小さい札と大きい札で、傾向が身体に依らないかを見る。
CARDS = {
    "満寵〔剛毅〕": (200, 405, 900),      # c3・弓・威力405% が本番
    "太史慈〔神射〕": (400, 976, 2000),   # c9・同じ対象の大きい札
}

_S = {}


def _init(seeds):
    SP._init(12, seeds)
    _S["bases"] = SP._S["bases"]
    _S["opps"] = SP._S["opps"]
    _S["cards"] = SP._S["cards"]
    _S["seeds"] = seeds


def _one(job):
    """1つの案を全部の土台 × 全部の相手で打つ。**土台と相手は独立**。

    工程は使い回されるので、**毎回まず名簿の効果文へ戻してから**差し替える。
    戻さないと前の案の対象がそのまま残る（一度踏んだ）。
    """
    name, target, power = job
    c = _S["cards"][name]
    row = [r for r in R.skills() if r["武将"] == name][0]
    sk = row["兵法名"]
    with F.unscaled():                       # ← 毎回リセット（工程の使い回し対策）
        F.SKILL_INFO[sk] = F._parse_skill(row["効果"], row["対象"])
    F.SKILL_TARGET[sk] = row["対象"]
    if target is None:                       # 兵法なしの対照
        c = replace(c, skill="")
    elif target == "__yard__":               # 物差し: 名簿のまま能力値を3コスト点削る
        rows = {g["名前"]: g for g in R.generals()}
        c = SP._minus_one(c, rows[name], 3.0)
    elif target == "__asis__":               # 物差しの相方: 名簿のまま
        pass
    else:
        with F.unscaled():
            F.SKILL_INFO[sk] = F._parse_skill("ダメージ 威力{}%".format(power), target)
        F.SKILL_TARGET[sk] = target
    w, d, diag = [], [], []
    for base in _S["bases"]:
        a = SP._swap_into(base, c)
        for i, o in enumerate(_S["opps"]):
            casts = []
            r = F.simulate(a, o, dt=SP.DT, seed=i * 7 + 1, casts=casts)
            w.append(r["score"]); d.append(r["diff"])
            dmg = over = spill = kills = n = 0.0
            tk = []
            for x in casts:
                if x["who"] == name and x["kind"] == "兵法":
                    n += 1
                    dmg += x["damage"]; over += x["over"]
                    spill += sum(v[1] for v in x["spill"])
                    if x["kills"]:
                        kills += len(x["kills"]); tk.append(x["t"])
            diag.append((D.PERSONAS[i // _S["seeds"]].name, n, dmg, over, spill, kills,
                         statistics.mean(tk) if tk else None))
    return w, d, diag


def _agg(diag, keep=None):
    rows = [x for x in diag if keep is None or OF_PERSONA.get(x[0]) == keep]
    n = sum(x[1] for x in rows)
    tk = [x[6] for x in rows if x[6] is not None]
    return {"発動": n,
            "実損害": sum(x[2] for x in rows), "超過": sum(x[3] for x in rows),
            "余勢": sum(x[4] for x in rows), "討取": sum(x[5] for x in rows),
            "撃破時刻": statistics.mean(tk) if tk else float("nan")}


def _idx(diag, keep):
    return [i for i, x in enumerate(diag) if keep is None or OF_PERSONA.get(x[0]) == keep]


def main():
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 2
    want = sys.argv[sys.argv.index("--card") + 1] if "--card" in sys.argv else None
    cards = {want: CARDS[want]} if want else dict(CARDS)

    jobs = []
    for name, powers in cards.items():
        jobs.append((name, None, 0))              # 兵法なし（Δ の土台）
        jobs.append((name, "__asis__", 0))        # 名簿のまま（物差しの相方）
        jobs.append((name, "__yard__", 0))        # 物差し（能力値を3コスト点削る）
        for t in TARGETS:
            for p in powers:
                jobs.append((name, t, p))
    print("{}案 × 土台12 × 性格12 × 種{} ＝ 1案 {}局".format(
        len(jobs), seeds, 12 * 12 * seeds), flush=True)

    pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
    got = dict(zip(jobs, pool.map(_one, jobs)))
    pool.close()
    if "--dump" in sys.argv:
        # 生の勝率・残存差を書き出す。**測り直さずに読み直せる**ようにするため
        # （誤差の取り方を変えるたびに20分回すのは無駄）。
        import json
        out = sys.argv[sys.argv.index("--dump") + 1]
        json.dump({"|".join(map(str, k)): {"w": v[0], "d": v[1]} for k, v in got.items()},
                  open(out, "w"))
        print("生データを書き出した: {}".format(out), flush=True)

    def ratio_ci(xa, xb, ya, yb):
        """比 mean(xa−xb)／mean(ya−yb) の95%幅。**対にして取る。**

        分子と分母は**同じ土台・同じ相手・同じ種の同じ局**なので、独立として
        誤差を足し合わせると桁で緩くなる（初回の測定では ±0.85 と出て、
        1マスも判定できなかった）。デッキと相手のばらつきは両方に同じだけ
        乗るので、局ごとの残差 `A_i − R×B_i` で取れば消える。

            R = 平均A / 平均B、  SE(R) = sd(A − R·B) / (√n × |平均B|)

        **1つのマスだけで裁定しないための欄**であることは変わらない。
        """
        A = [a - b for a, b in zip(xa, xb)]
        B = [a - b for a, b in zip(ya, yb)]
        n = len(A)
        ma, mb = statistics.mean(A), statistics.mean(B)
        if not ma or not mb:
            return float("nan")
        r = ma / mb
        res = [a - r * b for a, b in zip(A, B)]
        return 1.96 * statistics.pstdev(res) / (n ** 0.5) / abs(mb)

    for name, powers in cards.items():
        _, doff, _ = got[(name, None, 0)]
        _, dasis, _ = got[(name, "__asis__", 0)]
        _, dyard, _ = got[(name, "__yard__", 0)]
        # 物差しは skill_panel と同じ取り方（**同じ札**の能力値を3コスト点削った差÷3）。
        yard = statistics.mean(a - b for a, b in zip(dasis, dyard)) / 3.0
        print("\n{} — 土台12 × 性格12 × 種{}（1案 {}局）・1コスト点の残存差 {:+.4f}".format(
            name, seeds, len(doff), yard))
        print("{:<16}{:>7}{:>9}{:>10}{:>9}{:>9}{:>8}{:>8}{:>9}{:>8}".format(
            "対象", "威力", "勝率", "Δ勝率", "Δ残存", "コスト点", "実損害", "超過率", "適正係数", "±"))
        anchor = {}
        for t in TARGETS:
            for p in powers:
                w, d, g = got[(name, t, p)]
                dd = statistics.mean(a - b for a, b in zip(d, doff))
                woff = got[(name, None, 0)][0]
                dw = statistics.mean(a - b for a, b in zip(w, woff))
                a = _agg(g)
                if t == ANCHOR:
                    anchor[p] = dd
                coef = DS.TARGET_DMG_F[ANCHOR] * dd / anchor[p] if anchor.get(p) else float("nan")
                cci = (0.0 if t == ANCHOR else
                       DS.TARGET_DMG_F[ANCHOR] * ratio_ci(d, doff, got[(name, ANCHOR, p)][1], doff))
                print("{:<16}{:>6}%{:>9.1%}{:>+10.2%}{:>+9.4f}{:>9.2f}{:>8.0f}{:>8.1%}{:>9.3f}{:>8.3f}".format(
                    t.replace("敵1体", ""), p, statistics.mean(w), dw, dd,
                    dd / yard if yard else float("nan"),
                    a["実損害"], a["超過"] / (a["実損害"] + a["超過"]) if a["実損害"] else 0,
                    coef, cci))
        print("  いまの表: 正面 {:.3f} / 残兵力が最少 {:.3f} / 敵前衛 {:.3f}".format(
            DS.TARGET_DMG_F["敵1体（正面）"], DS.TARGET_DMG_F["敵1体（残兵力が最少）"],
            DS.TARGET_DMG_F["敵前衛"]))

        # --- 相手の型ごと ---
        print("\n  相手の型ごとの適正係数（正面＝{:.3f} を錨に）".format(DS.TARGET_DMG_F[ANCHOR]))
        print("  {:<16}{:>7}{:>12}{:>12}{:>12}".format("対象", "威力", *FAMILY))
        for t in TARGETS[1:]:
            for p in powers:
                cells = []
                for fam in FAMILY:
                    ix = _idx(got[(name, t, p)][2], fam)
                    da = statistics.mean(got[(name, t, p)][1][i] - doff[i] for i in ix)
                    db = statistics.mean(got[(name, ANCHOR, p)][1][i] - doff[i] for i in ix)
                    cells.append(DS.TARGET_DMG_F[ANCHOR] * da / db if db else float("nan"))
                print("  {:<16}{:>6}%{:>12.3f}{:>12.3f}{:>12.3f}".format(
                    t.replace("敵1体", ""), p, *cells))

        # --- 理由の分析 ---
        print("\n  理由の分析（1発あたり・全局）")
        print("  {:<16}{:>7}{:>9}{:>9}{:>9}{:>8}{:>10}".format(
            "対象", "威力", "実損害", "超過", "余勢", "討取", "撃破時刻"))
        for t in TARGETS:
            for p in powers:
                a = _agg(got[(name, t, p)][2])
                n = max(a["発動"], 1)
                print("  {:<16}{:>6}%{:>9.0f}{:>9.0f}{:>9.0f}{:>8.2f}{:>10.1f}".format(
                    t.replace("敵1体", ""), p, a["実損害"] / n, a["超過"] / n,
                    a["余勢"] / n, a["討取"] / n, a["撃破時刻"]))
    print("\nTARGET DONE")


if __name__ == "__main__":
    main()
