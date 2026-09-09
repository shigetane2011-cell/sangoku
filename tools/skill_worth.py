# -*- coding: utf-8 -*-
"""tools/skill_worth.py -- ④ 兵法の値打ちを「身体で払える額」として測る（§7.216）

    python3 tools/skill_worth.py                  # 3対象 × 身体3枚 × 18/30/40
    python3 tools/skill_worth.py --regs 30        # 1つだけ
    python3 tools/skill_worth.py --seeds 1        # 速く

================================================================================
 なぜ「身体を削る」向きで測るのか
================================================================================
§7.215 で残った曖昧さは「**錨づけの比では絶対の高さが決まらない**」だった。
7対象すべてが表より上に出たが、それは「正面が高すぎる」でも「他が全部安すぎる」
でも同じ見え方になる。解くには**コスト点での絶対量**が要る。

ただし取り方に**向き**がある（諸葛亮で分かったこと・テストプレイの指摘）:

    (A) 兵法を外して、身体でどれだけ買い戻せるか
    (B) 兵法の代金として、身体をどれだけ削れるか      ← **名簿の値付けはこちら**

**(A) と (B) は一致しない。** 名簿は「効果予算を取って、残りで身体を買う」ので、
測るべきは (B) である。ここでは (B) を直に測る:

    兵法なし・身体そのまま        … 基準の値打ち V0
    兵法あり・身体を d コスト点削る … 値打ち V(d)

    **V(d) = V0 となる d\\* が、その兵法の「身体で払える額」**

**局所の傾きで割らない。** 複数の削り幅で V(d) を取り、**釣り合う点を挟んで**
読む（傾きが削り幅で変わるなら、割り算は幅の取り方で答えが動く）。

削ると武力・知力も引き直される＝**兵法の一撃も弱くなる**。これは実際の名簿でも
起きることなので、そのまま込みで測る（§7.215 ③ の「請求が変われば身体も変わる」）。

================================================================================
 集計の作法（テストプレイの指示）
================================================================================
- **身体別・レギュレーション別を残す。共通倍率で均さない。**
- 誤差は**土台と相手をかたまりごと**選び直す（§7.213 の訂正）。
- 先行するのは **正面・知略が最高・残兵力が最少** の3対象。
- 最終判断は**値札変更後の再生成と本番BO3**まで見てから（この計器では決めない）。
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

TARGETS = ("敵1体（正面）", "敵1体（知略が最高）", "敵1体（残兵力が最少）")
SHAVE = (0.0, 1.0, 2.0, 3.0)        # 身体を削る幅（コスト点）。**釣り合う点を挟む**
BODIES = ("王双〔大刀〕", "荀攸〔謀主〕", "黄忠〔定軍山〕")
HIT = 1200.0                         # 生の一撃を揃える（§7.215 と同じ帯）

# --- --swap: 現行の札そのままで、対象だけ差し替える（§7.217）-----------------
# **一撃を揃えない。** 実際の威力・周期・副効果のまま、対象だけ 正面 へ振り替えて
# 「いまの割引（請求の差）が実測の差に見合うか」を直に見る。
SWAP_CARDS = ("甘寧〔錦帆賊〕", "魏延〔子午〕", "張任〔落鳳〕",
              "潘璋〔急襲〕", "孫尚香〔弓腰姫〕")
SWAP_TO = "敵1体（正面）"
# --cards 名1,名2,... で差し替えられる。**打撃を持つ札だけを渡すこと** —
# 打撃の無い札（混乱だけ・攻撃力デバフだけ）は打撃の掛け目 TARGET_DMG_F を
# 使わず、状態効果の掛け目 TARGET_FX_FOE で値付けされるので、この計器の
# 比較対象にならない（§7.220）。

_S = {}


def _init(seeds):
    SP._init(12, seeds)
    _S.update(bases=SP._S["bases"], opps=SP._S["opps"], cards=SP._S["cards"],
              rows={g["名前"]: g for g in R.generals()})


def _one(job):
    """power が None なら**名簿の威力・副効果のまま**（--swap）。0 は兵法なし。"""
    name, target, power, shave = job
    c = _S["cards"][name]
    row = [r for r in R.skills() if r["武将"] == name][0]
    sk = row["兵法名"]
    with F.unscaled():                       # 毎回 名簿へ戻す（工程の使い回し）
        F.SKILL_INFO[sk] = F._parse_skill(row["効果"], row["対象"])
    F.SKILL_TARGET[sk] = row["対象"]
    if target is None:
        c = replace(c, skill="")             # 基準: 兵法なし・身体そのまま
    else:
        if power is None:                    # --swap: 効果文はそのまま、対象だけ差し替え
            with F.unscaled():
                F.SKILL_INFO[sk] = F._parse_skill(row["効果"], target)
            F.SKILL_TARGET[sk] = target
        else:
            with F.unscaled():               # 威力は整数（落とし穴44）
                F.SKILL_INFO[sk] = F._parse_skill("ダメージ 威力{:d}%".format(power), target)
            F.SKILL_TARGET[sk] = target
        if shave > 0.0:
            c = SP._minus_one(c, _S["rows"][name], shave)   # 兵法は残す
    d = []
    for base in _S["bases"]:
        a = SP._swap_into(base, c)
        for i, o in enumerate(_S["opps"]):
            d.append(F.simulate(a, o, dt=SP.DT, seed=i * 7 + 1)["diff"])
    return d


def charged(name, target, power):
    """名簿の式が**この威力・この対象**から引く額（1枚のコスト点）。比較の相手。

    **札の本番の威力ではない**（生の一撃を揃えるために差し替えてある）ので、
    この欄は「その札がいま名簿で払っている額」ではない。**式が同じ盤面効果に
    いくら請求するか**を見るための欄である。
    """
    g = [x for x in R.generals() if x["名前"] == name][0]
    tw = DS.tier_weight(float(g["消費ゲージ%"]), float(g["初期ゲージ"]))
    return (DS.damage_price(power / 100.0 * tw) * DS.target_dmg_f(target)
            / DS.CARD_COST_RATE)


def main():
    import tools.damage_curve as DC
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 2
    want = sys.argv[sys.argv.index("--regs") + 1].split(",") if "--regs" in sys.argv else None
    regs = [(n, c) for n, c in M.REGULATIONS if not want or "{:g}".format(c) in want]
    R.load_skills_into_field(); R.load_traits_into_field()
    coef = {n: DC.skill_coef(n) for n in BODIES}
    pw = {n: max(1, int(round(HIT * 100.0 / coef[n]))) for n in BODIES}

    print("④ 兵法の値打ちを『身体で払える額』として測る（§7.216）")
    print("  対象{} × 身体{}枚 × レギュレーション{} × 削り幅{}".format(
        len(TARGETS), len(BODIES),
        "/".join("{:g}".format(c) for _, c in regs), list(SHAVE)))
    print("  生の一撃を {:.0f} に揃える・1案 {}局（土台12 × 性格12 × 種{}）\n".format(
        HIT, 12 * 12 * seeds, seeds))

    out = {}
    for label, cap in regs:
        os.environ["PANEL_TOTAL"] = str(cap); SP.TOTAL = cap
        jobs = [(n, None, 0, 0.0) for n in BODIES]
        jobs += [(n, t, pw[n], d) for n in BODIES for t in TARGETS for d in SHAVE]
        pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
        got = dict(zip(jobs, pool.map(_one, jobs)))
        pool.close(); pool.join()
        out["{:g}".format(cap)] = {"|".join(map(str, k)): v for k, v in got.items()}
        print("■ {}（上限 {:g}点）".format(label, cap), flush=True)
        report(got, pw, seeds)
        print()

    dump = (sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv
            else "/tmp/claude-0/skill_worth_raw.json")
    json.dump(out, open(dump, "w"))
    print("生データ: {}".format(dump))
    print("WORTH DONE")


def cross(vals, v0):
    """V(d) が V0 を横切る d を、隣り合う2点の直線で挟んで返す。

    **局所の傾きで割らない。** 横切らなければ None（端の外＝この幅では挟めない）。
    """
    for (d0, a), (d1, b) in zip(vals, vals[1:]):
        if (a - v0) * (b - v0) <= 0 and a != b:
            return d0 + (a - v0) * (d1 - d0) / (a - b)
    return None


def report(got, pw, seeds):
    ALL = TP.game_index(seeds, range(TP.NBASE), range(TP.NPERS))
    rng = random.Random(60606)
    draws = [TP.cluster_draw(rng, seeds) for _ in range(300)]

    def V(name, t, d, ix):
        return statistics.mean(got[(name, t, pw[name], d)][i] for i in ix)

    def V0(name, ix):
        return statistics.mean(got[(name, None, 0, 0.0)][i] for i in ix)

    print("  {:<16}{:<16}{:>9}{:>9}{:>18}{:>10}".format(
        "身体", "対象", "払える額", "式の請求", "95%区間", "釣り合い"))
    for name in BODIES:
        for t in TARGETS:
            vals = [(d, V(name, t, d, ALL)) for d in SHAVE]
            star = cross(vals, V0(name, ALL))
            ch = charged(name, t, pw[name])
            bs = []
            for ix in draws:
                x = cross([(d, V(name, t, d, ix)) for d in SHAVE], V0(name, ix))
                if x is not None:
                    bs.append(x)
            bs.sort()
            ci = ("[{:.2f}, {:.2f}]".format(bs[int(.025*len(bs))], bs[int(.975*len(bs))])
                  if len(bs) > 20 else "—")
            print("  {:<16}{:<16}{:>9}{:>9.2f}{:>18}{:>10}".format(
                name, t.replace("敵1体", ""),
                "{:.2f}".format(star) if star is not None else "挟めず",
                ch, ci,
                "{:+.2f}".format(star - ch) if star is not None else "—"))
        # 削り幅ごとの生の値（傾きが幅で変わるかを見る）
        print("    削り幅ごとの残存差  基準(兵法なし) {:+.4f}  |  ".format(V0(name, ALL))
              + "  ".join("{}: {}".format(
                  t.replace("敵1体", ""),
                  "/".join("{:+.4f}".format(V(name, t, d, ALL)) for d in SHAVE))
                  for t in TARGETS))




# ============================================================================
# --swap: 現行の5枚で、対象だけ 知略が最高 → 正面 に振り替える（§7.217）
# ============================================================================
def swap_main():
    """**いまの割引（請求の差）が、実測の差に見合うか**を直に見る。

    一撃を揃えない。**実際の威力・周期・副効果のまま**、対象だけ差し替える。
    比は正面側が小さいと暴れるので、**差も併記する**（テストプレイの指示）。
    副効果にも対象変更が効く札（甘寧の畏怖）は印を付け、
    **純打撃の対象倍率だけの値打ちと混同しない。**
    """
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 2
    want = sys.argv[sys.argv.index("--regs") + 1].split(",") if "--regs" in sys.argv else None
    regs = [(n, c) for n, c in M.REGULATIONS if not want or "{:g}".format(c) in want]
    R.load_skills_into_field(); R.load_traits_into_field()
    rows = {g["名前"]: g for g in R.generals()}
    # 【--fx の読み方・テストプレイの指摘】**打撃と状態効果は同じ土俵にない。**
    #   打撃    `dmg = SKILL_SCALE * burst * p_eff * coef / n`  … **n で割る**（総量の分配）
    #   状態効果 `for key, amt, secs in sk.mods:` 各体に amt      … **割らない**（満額の複製）
    # なので 打撃の 敵全体 0.992（正面の0.77倍＝総量ほぼ同じ）と、
    # 状態効果の 敵全体 4.964（敵1体の5.77倍＝ほぼ体数ぶん）は、
    # **「段差が大きい／小さい」と並べて比べてはいけない。**
    # 問うべきは「5.77倍は大きすぎるか」ではなく「**体数ぶんに見合うか**」。
    # 満額で6体に乗っても6倍にはならない向きの力（倒れかけの隊・まだ撃ち合って
    # いない後衛・戦闘中に減る体数）があるので、実測が 5.77 を下回れば取り過ぎ。
    fx = "--fx" in sys.argv      # 状態効果の掛け目（TARGET_FX_FOE）を測る（§7.221）
    if "--cards" in sys.argv:
        global SWAP_CARDS
        SWAP_CARDS = tuple(sys.argv[sys.argv.index("--cards") + 1].split(","))
        for n in SWAP_CARDS:
            has_dmg = F.SKILL_INFO[rows[n]["兵法"]].power > 0
            if fx and has_dmg:
                raise SystemExit("{} は打撃を持つ。--fx は状態効果だけの札に使う".format(n))
            if not fx and not has_dmg:
                raise SystemExit("{} は打撃を持たない。--fx を付けること".format(n))
    if fx:
        print("【--fx】状態効果の掛け目（TARGET_FX_FOE）を測る。"
              "敵全体 {:.3f} / 敵1体（正面）{:.3f} ＝ {:.2f}倍\n".format(
                  DS.TARGET_FX_FOE["敵全体"], DS.TARGET_FX_FOE["敵1体（正面）"],
                  DS.TARGET_FX_FOE["敵全体"] / DS.TARGET_FX_FOE["敵1体（正面）"]))

    def price(g, tgt):
        sk = F.SKILL_INFO[g["兵法"]]
        return DS.effect_value(sk, tgt, float(g["消費ゲージ%"]), float(g["初期ゲージ"]),
                               kisei=float(g["ゲージ上昇率"]) / 100.0,
                               cost=float(g["コスト"]), typ=R.TYPE_MAP[g["兵種"]],
                               tilt=R.tilt_of(g))

    print("§7.217 現行の5枚で、対象だけ 知略が最高 → 正面 に振り替える")
    print("  一撃は揃えない（実際の威力・周期・副効果のまま）・削り幅{}・1案{}局\n".format(
        list(SHAVE), 12 * 12 * seeds))
    print("  {:<16}{:>4}{:>8}{:>10}{:>10}{:>9}{:>7}   {}".format(
        "武将", "c", "威力%", "いまの請求", "正面なら", "請求の差", "比", "副効果"))
    ch = {}
    for n in SWAP_CARDS:
        g = rows[n]
        a, b = price(g, g and R._skill_target(g["兵法"])), price(g, SWAP_TO)
        ch[n] = (a, b)
        mods = F.SKILL_INFO[g["兵法"]].mods
        print("  {:<16}{:>4}{:>8.0f}{:>10.2f}{:>10.2f}{:>+9.2f}{:>7.2f}   {}".format(
            n, g["コスト"], F.SKILL_INFO[g["兵法"]].power * 100, a, b, a - b, a / b if b else 0,
            "あり（対象変更が効く）" if mods else "なし（純打撃）"))

    out = {}
    for label, cap in regs:
        os.environ["PANEL_TOTAL"] = str(cap); SP.TOTAL = cap
        jobs = [(n, None, 0, 0.0) for n in SWAP_CARDS]
        for n in SWAP_CARDS:
            for t in (R._skill_target(rows[n]["兵法"]), SWAP_TO):
                for d in SHAVE:
                    jobs.append((n, t, None, d))
        jobs = list(dict.fromkeys(jobs))
        pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
        got = dict(zip(jobs, pool.map(_one, jobs)))
        pool.close(); pool.join()
        out["{:g}".format(cap)] = {"|".join(map(str, k)): v for k, v in got.items()}
        print("\n■ {}（上限 {:g}点）".format(label, cap), flush=True)
        swap_report(got, rows, ch, seeds)

    dump = (sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv
            else "/tmp/claude-0/swap_raw.json")
    json.dump(out, open(dump, "w"))
    print("\n生データ: {}".format(dump))
    print("SWAP DONE")


def swap_report(got, rows, ch, seeds):
    ALL = TP.game_index(seeds, range(TP.NBASE), range(TP.NPERS))
    rng = random.Random(70707)
    draws = [TP.cluster_draw(rng, seeds) for _ in range(300)]

    def worth(name, t, ix):
        v0 = statistics.mean(got[(name, None, 0, 0.0)][i] for i in ix)
        vals = [(d, statistics.mean(got[(name, t, None, d)][i] for i in ix)) for d in SHAVE]
        return cross(vals, v0)

    print("  {:<16}{:>10}{:>10}{:>10}{:>19}{:>10}".format(
        "武将", "いま", "正面へ", "実測の差", "95%区間", "請求の差"))
    for name in SWAP_CARDS:
        cur = R._skill_target(rows[name]["兵法"])
        a, b = worth(name, cur, ALL), worth(name, SWAP_TO, ALL)
        bs = []
        for ix in draws:
            x, y = worth(name, cur, ix), worth(name, SWAP_TO, ix)
            if x is not None and y is not None:
                bs.append(x - y)
        bs.sort()
        ci = ("[{:+.2f}, {:+.2f}]".format(bs[int(.025 * len(bs))], bs[int(.975 * len(bs))])
              if len(bs) > 20 else "—")
        f = lambda v: "{:.2f}".format(v) if v is not None else "挟めず"
        print("  {:<16}{:>10}{:>10}{:>10}{:>19}{:>10.2f}".format(
            name, f(a), f(b),
            "{:+.2f}".format(a - b) if (a is not None and b is not None) else "—",
            ci, ch[name][0] - ch[name][1]))


if __name__ == "__main__":
    swap_main() if "--swap" in sys.argv else main()
