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
# --shave 0,1,2,3,4,5 で広げられる。「挟めず」と出たら**幅の外に釣り合い点がある**
# ということなので、幅を広げて測り直すこと（王允で踏んだ・§7.222 (C)）。
BODIES = ("王双〔大刀〕", "荀攸〔謀主〕", "黄忠〔定軍山〕")
HIT = 1200.0                         # 生の一撃を揃える（§7.215 と同じ帯）

# --- --swap: 現行の札そのままで、対象だけ差し替える（§7.217）-----------------
# **一撃を揃えない。** 実際の威力・周期・副効果のまま、対象だけ 正面 へ振り替えて
# 「いまの割引（請求の差）が実測の差に見合うか」を直に見る。
SWAP_CARDS = ("甘寧〔錦帆賊〕", "魏延〔子午〕", "張任〔落鳳〕",
              "潘璋〔急襲〕", "孫尚香〔弓腰姫〕")
SWAP_TO = "敵1体（正面）"

# --- --curve: 身体をまたがずに、その身体の中で威力だけを振る（§7.216 ⑤ の宿題）------
# §7.216 ⑤ は「生の一撃1,200」に揃えるために王双を 950%（本番）→1435%、
# 黄忠を 881%→218% と**本番の外まで振って**比べていた。しかも身体1点の重さは
# 兵種で違う（§7.185）ので、**身体をまたいで額を比べること自体ができない。**
#
# ここでは向きを変える:
#   **1つの身体の中で威力だけを振り、「払える額 − 請求」が威力とともに動くか**を見る。
#   動かない（水平）なら、打撃の冪 DAMAGE_EXP は**その身体の範囲では正しい**。
#   身体どうしで**高さ**が違っても、それは §7.185 の「身体1点の重さ」の話であって
#   冪の話ではない。**高さは比べない。傾きだけを比べる。**
#
# 威力の梯子は**その兵種が本番で使っている範囲の中**に置く（本番の外へ出さない）。
#   騎兵 330〜3200% ／ 歩兵 331〜2900% ／ 弓兵 25〜976%（打撃を持つ72枚の実分布）
# 効果文は **`ダメージ 威力N%` の純打撃に差し替える**（副効果は威力で動かないので
# 傾きの邪魔になるだけ）。対象も全部 `敵1体（正面）`＝錨に揃える。
CURVE_BODIES = {
    "王双〔大刀〕":     (400, 900, 1800, 3000),   # 騎 c4・係数 83.6（§7.216 ⑤ で −2.0〜−3.2 側）
    "関平〔麒麟児〕":   (400, 900, 1800, 3000),   # 騎 c6・係数 94.5（同じ兵種・ほぼ同じ係数・違うコスト）
    "凌統〔公績〕":     (400, 900, 1800, 2900),   # 歩 c7・係数 153.6
    "黄忠〔定軍山〕":   (150, 350, 600, 880),     # 弓 c8・係数 549.2（§7.216 ⑤ で +0.12〜+0.53 側）
}
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
        elif isinstance(power, str):         # --ablate: 効果文そのものを渡す
            with F.unscaled():
                F.SKILL_INFO[sk] = F._parse_skill(power, target)
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


def _logfit(xs, ys):
    """log(値打ち) を log(威力) に回した傾き＝**実測の冪**。値札は DAMAGE_EXP。"""
    import math
    lx = [math.log(x) for x in xs]; ly = [math.log(y) for y in ys]
    mx = statistics.mean(lx); my = statistics.mean(ly)
    return (sum((a - mx) * (b - my) for a, b in zip(lx, ly))
            / sum((a - mx) ** 2 for a in lx))


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
    if "--shave" in sys.argv:
        global SHAVE
        SHAVE = tuple(float(x) for x in sys.argv[sys.argv.index("--shave") + 1].split(","))
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


# ============================================================================
# --curve: 身体をまたがずに威力だけを振る（§7.216 ⑤ の宿題）
# ============================================================================
def curve_main():
    """**1つの身体の中で**威力を振って「払える額 − 請求」が動くかを見る。

    読み方（ここを外すと §7.216 ⑤ と同じ間違いになる）:
    - **傾き**（同じ身体の中で差が威力とともに動くか）が冪の話。
    - **高さ**（身体どうしの差の絶対値）は §7.185「身体1点の重さは兵種で違う」の話。
      **高さは比べない。**
    """
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 2
    want = sys.argv[sys.argv.index("--regs") + 1].split(",") if "--regs" in sys.argv else None
    regs = [(n, c) for n, c in M.REGULATIONS if not want or "{:g}".format(c) in want]
    if "--shave" in sys.argv:
        global SHAVE
        SHAVE = tuple(float(x) for x in sys.argv[sys.argv.index("--shave") + 1].split(","))
    bodies = dict(CURVE_BODIES)
    if "--bodies" in sys.argv:
        keep = sys.argv[sys.argv.index("--bodies") + 1].split(",")
        bodies = {k: v for k, v in bodies.items() if k in keep}
    R.load_skills_into_field(); R.load_traits_into_field()
    rows = {g["名前"]: g for g in R.generals()}

    def price_pow(g, power):
        """`ダメージ 威力N%`（対象は正面）を、その身体の呼び方で値付けする。"""
        with F.unscaled():
            sk = F._parse_skill("ダメージ 威力{:d}%".format(int(power)), SWAP_TO)
        return DS.effect_value(sk, SWAP_TO, float(g["消費ゲージ%"]), float(g["初期ゲージ"]),
                               kisei=float(g["ゲージ上昇率"]) / 100.0,
                               cost=float(g["コスト"]), typ=R.TYPE_MAP[g["兵種"]],
                               tilt=R.tilt_of(g))

    import tools.damage_curve as DC
    coef = {n: DC.skill_coef(n) for n in bodies}

    print("§7.216 ⑤ の宿題 — **身体をまたがず**、身体の中で威力だけを振る")
    print("  効果文は `ダメージ 威力N%` の純打撃・対象は全部 {}・削り幅{}・1案{}局\n".format(
        SWAP_TO, list(SHAVE), 12 * 12 * seeds))
    print("  {:<16}{:>4}{:>5}{:>8}{:>26}".format("身体", "c", "兵種", "兵法係数", "威力%（生の一撃）"))
    for n, pws in bodies.items():
        g = rows[n]
        print("  {:<16}{:>4}{:>5}{:>8.1f}   {}".format(
            n, g["コスト"], g["兵種"], coef[n],
            " / ".join("{}%({:.0f})".format(p, p / 100.0 * coef[n]) for p in pws)))
    print()

    out = {}
    for label, cap in regs:
        os.environ["PANEL_TOTAL"] = str(cap); SP.TOTAL = cap
        jobs = [(n, None, 0, 0.0) for n in bodies]
        jobs += [(n, SWAP_TO, int(p), d) for n, pws in bodies.items() for p in pws for d in SHAVE]
        jobs = list(dict.fromkeys(jobs))
        pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
        got = dict(zip(jobs, pool.map(_one, jobs)))
        pool.close(); pool.join()
        out["{:g}".format(cap)] = {"|".join(map(str, k)): v for k, v in got.items()}
        print("■ {}（上限 {:g}点）".format(label, cap), flush=True)
        curve_report(got, bodies, rows, price_pow, seeds)
        print()

    dump = (sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv
            else "/tmp/claude-0/skill_worth_curve.json")
    json.dump(out, open(dump, "w"))
    print("生データ: {}".format(dump))
    print("CURVE DONE")


def curve_report(got, bodies, rows, price_pow, seeds):
    ALL = TP.game_index(seeds, range(TP.NBASE), range(TP.NPERS))
    rng = random.Random(80808)
    draws = [TP.cluster_draw(rng, seeds) for _ in range(300)]

    def V(name, p, d, ix):
        return statistics.mean(got[(name, SWAP_TO, int(p), d)][i] for i in ix)

    def V0(name, ix):
        return statistics.mean(got[(name, None, 0, 0.0)][i] for i in ix)

    print("  {:<16}{:>8}{:>10}{:>9}{:>9}{:>19}".format(
        "身体", "威力%", "払える額", "請求", "差", "95%区間(差)"))
    for name, pws in bodies.items():
        gaps = []
        for p in pws:
            vals = [(d, V(name, p, d, ALL)) for d in SHAVE]
            star = cross(vals, V0(name, ALL))
            ch = price_pow(rows[name], p)
            bs = []
            for ix in draws:
                x = cross([(d, V(name, p, d, ix)) for d in SHAVE], V0(name, ix))
                if x is not None:
                    bs.append(x - ch)
            bs.sort()
            ci = ("[{:+.2f}, {:+.2f}]".format(bs[int(.025*len(bs))], bs[int(.975*len(bs))])
                  if len(bs) > 20 else "—")
            gaps.append(None if star is None else star - ch)
            print("  {:<16}{:>8}{:>10}{:>9.2f}{:>9}{:>19}".format(
                name, p, "{:.2f}".format(star) if star is not None else "挟めず", ch,
                "{:+.2f}".format(star - ch) if star is not None else "—", ci))
        ok = [x for x in gaps if x is not None]
        if len(ok) >= 2:
            print("    → この身体の中での差の動き: {}  （幅 {:.2f}）".format(
                " → ".join("{:+.2f}".format(x) for x in ok), max(ok) - min(ok)))
        # 【差だけを見ない・§7.226 ②】`払える額 − 請求` は**比が一定でも請求が
        # 大きくなるほど広がる**（0.5倍の値札は、請求2点なら差1点・0.3点なら差0.15点）。
        # 差の広がりを冪の証拠にしないため、**比**と**冪**をここで併記する。
        rs, fitp, fitv = [], [], []
        for p in pws:
            vals = [(d, V(name, p, d, ALL)) for d in SHAVE]
            star = cross(vals, V0(name, ALL))
            if star is None or star <= 0:
                continue
            ch = price_pow(rows[name], p)
            rs.append(star / ch); fitp.append(p); fitv.append(star)
        if rs:
            print("    → 比（払える額÷請求）: {}  ".format(" → ".join("{:.2f}".format(x) for x in rs))
                  + ("｜実測の冪 {:.2f}（値札は {:.4f}）".format(_logfit(fitp, fitv), DS.DAMAGE_EXP)
                     if len(rs) >= 3 else ""))
        print()


# ============================================================================
# --ablate: 効果文から1つの節を抜いて、その節の値打ちを差で測る（§7.211 の宿題）
# ============================================================================
# `EFFECT_PRICE["spd"] = 0.0`（移動速度）と `["gauge"] = 0.0` は
# **「量ではなく発動時刻で決まるから線形の単価が置けない」**という理由で 0 に
# してある（design.effect_value の注記）。0 と決めたのではなく、**置けなかった**。
# ここでは値札を作るのではなく、**本番の札で本当に 0 なのか**を測る:
#
#     いまの効果文（節あり） … 払える額 A
#     その節を抜いた効果文   … 払える額 B
#     **A − B が、その節が実戦で稼いでいる額**（コスト点）。請求の差は 0.00。
#
# 抜くのは `+` で区切られた節のうち `--term` を含むものだけ。対象・周期・他の節は
# そのまま。**節を抜くと身体は動かない**（名簿の能力値は据え置きで効果文だけ差し替える）
# ので、A と B は同じ身体どうしの比較になる。
ABLATE_TERM = "移動速度"
# 敵を遅くする4枚と、自分を速くする5枚。**性質が違うので混ぜて平均しない。**
ABLATE_SLOW = ("司馬懿〔冢虎〕", "陸遜〔夷陵〕", "馬謖〔幼常〕", "徐庶〔元直〕")
ABLATE_FAST = ("張遼〔逍遥津〕", "徐晃〔長駆〕", "孫堅〔江東の虎〕",
               "公孫瓚〔白馬義従〕", "曹休〔千里駒〕")


def strip_term(effect: str, term: str) -> str:
    """`+` 区切りの節のうち term を含むものを落とす。"""
    # **区切りは " + "（前後に空白）。**素の "+" で割ると `移動速度 +25%（32秒）` の
    # 中の + まで割れて節が壊れる（一度踏んだ）。
    parts = [x.strip() for x in effect.split(" + ")]
    kept = [x for x in parts if term not in x]
    if not kept:
        raise SystemExit("節を全部落としてしまう: {}".format(effect))
    return " + ".join(kept)


def ablate_main():
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 2
    want = sys.argv[sys.argv.index("--regs") + 1].split(",") if "--regs" in sys.argv else None
    regs = [(n, c) for n, c in M.REGULATIONS if not want or "{:g}".format(c) in want]
    term = sys.argv[sys.argv.index("--term") + 1] if "--term" in sys.argv else ABLATE_TERM
    if "--shave" in sys.argv:
        global SHAVE
        SHAVE = tuple(float(x) for x in sys.argv[sys.argv.index("--shave") + 1].split(","))
    R.load_skills_into_field(); R.load_traits_into_field()
    rows = {g["名前"]: g for g in R.generals()}
    cards = (tuple(sys.argv[sys.argv.index("--cards") + 1].split(","))
             if "--cards" in sys.argv else ABLATE_SLOW + ABLATE_FAST)
    srow = {r["武将"]: r for r in R.skills()}

    def price(g, text):
        with F.unscaled():
            sk = F._parse_skill(text, R._skill_target(g["兵法"]))
        return DS.effect_value(sk, R._skill_target(g["兵法"]),
                               float(g["消費ゲージ%"]), float(g["初期ゲージ"]),
                               kisei=float(g["ゲージ上昇率"]) / 100.0,
                               cost=float(g["コスト"]), typ=R.TYPE_MAP[g["兵種"]],
                               tilt=R.tilt_of(g))

    print("『{}』の節を抜いて、その節が稼いでいる額を測る（§7.211 の宿題）".format(term))
    print("  削り幅{}・1案{}局・対象と周期は名簿のまま\n".format(list(SHAVE), 12 * 12 * seeds))
    print("  {:<18}{:>4}{:>7}{:>7}   {}".format("武将", "c", "節あり", "節なし", "抜いた節"))
    texts, ch = {}, {}
    for n in cards:
        g, r = rows[n], srow[n]
        full = r["効果"]
        cut = strip_term(full, term)
        assert cut != full, "{} に『{}』が無い".format(n, term)
        texts[n] = (full, cut)
        ch[n] = (price(g, full), price(g, cut))
        gone = [x.strip() for x in full.split(" + ") if term in x]
        print("  {:<18}{:>4}{:>7.2f}{:>7.2f}   {}".format(
            n, g["コスト"], ch[n][0], ch[n][1], " / ".join(gone)))
    print()

    out = {}
    for label, cap in regs:
        os.environ["PANEL_TOTAL"] = str(cap); SP.TOTAL = cap
        jobs = [(n, None, 0, 0.0) for n in cards]
        for n in cards:
            for t in texts[n]:
                for d in SHAVE:
                    jobs.append((n, R._skill_target(rows[n]["兵法"]), t, d))
        jobs = list(dict.fromkeys(jobs))
        pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
        got = dict(zip(jobs, pool.map(_one, jobs)))
        pool.close(); pool.join()
        out["{:g}".format(cap)] = {"|".join(map(str, k)): v for k, v in got.items()}
        print("■ {}（上限 {:g}点）".format(label, cap), flush=True)
        ablate_report(got, cards, rows, texts, ch, seeds)
        print()

    dump = (sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv
            else "/tmp/claude-0/skill_worth_ablate.json")
    json.dump(out, open(dump, "w"))
    print("生データ: {}".format(dump))
    print("ABLATE DONE")


def ablate_report(got, cards, rows, texts, ch, seeds):
    ALL = TP.game_index(seeds, range(TP.NBASE), range(TP.NPERS))
    rng = random.Random(90909)
    draws = [TP.cluster_draw(rng, seeds) for _ in range(300)]

    def worth(name, text, ix):
        tgt = R._skill_target(rows[name]["兵法"])
        v0 = statistics.mean(got[(name, None, 0, 0.0)][i] for i in ix)
        vals = [(d, statistics.mean(got[(name, tgt, text, d)][i] for i in ix)) for d in SHAVE]
        return cross(vals, v0)

    print("  {:<18}{:>9}{:>9}{:>10}{:>19}{:>9}".format(
        "武将", "節あり", "節なし", "節の値打ち", "95%区間", "請求の差"))
    for n in cards:
        full, cut = texts[n]
        a, b = worth(n, full, ALL), worth(n, cut, ALL)
        bs = []
        for ix in draws:
            x, y = worth(n, full, ix), worth(n, cut, ix)
            if x is not None and y is not None:
                bs.append(x - y)
        bs.sort()
        ci = ("[{:+.2f}, {:+.2f}]".format(bs[int(.025 * len(bs))], bs[int(.975 * len(bs))])
              if len(bs) > 20 else "—")
        f = lambda v: "{:.2f}".format(v) if v is not None else "挟めず"
        print("  {:<18}{:>9}{:>9}{:>10}{:>19}{:>9.2f}".format(
            n, f(a), f(b),
            "{:+.2f}".format(a - b) if (a is not None and b is not None) else "—",
            ci, ch[n][0] - ch[n][1]))


# ============================================================================
# --spd: 移動速度の値打ちを「いつ効くか」と「兵種」で切り分ける（§7.228 の続き）
# ============================================================================
# テストプレイの指摘（2026-09-09）:
#   「スタート時からの変更と、後から変更で価値が違うはず。兵種によっても。」
# `design.effect_value` の注記も同じことを言っている（初期ゲージ0 なら 0.00・
# 初期ゲージ100（開幕発動）なら 1.60 という**発動時刻の崖**）。
# §7.228 は**本番の札をそのまま**測ったので、9枚がそれぞれ別の段・別の兵種・別の対象で、
# **どの条件で 0 だったのかが分かれていない。** ここで2つの因子だけを振って切り分ける。
#
# **読み方の約束（§7.185・§7.226 ⑤）**: 身体1点の重さは兵種で違うので、
# **兵種をまたいで「どちらが大きい」とは言えない。** 言えるのは
#   (i) **同じ身体の中**で発動時刻を変えたときの動き
#   (ii) **0 か 0 でないか**（0 はどの単位でも 0 なので兵種をまたいで比べられる）
SPD_EFFECT = "移動速度 -25%（40秒）"       # 徐庶・司馬懿と同じ量・秒
SPD_TARGET = "敵前衛"                      # 突っ込んでくる側を遅くする形（徐庶と同じ）
SPD_BODIES = ("凌統〔公績〕", "関平〔麒麟児〕", "黄忠〔定軍山〕")   # 歩 c7 / 騎 c6 / 弓 c8
# 消費300 に固定して初期ゲージだけ振る。300 なら開幕発動、0 なら一番遅い。
SPD_GAUGE = ((300.0, 300.0, "開幕"), (300.0, 140.0, "中盤（本番の大技段）"),
             (300.0, 0.0, "遅め"))


def _one_spd(job):
    """job = (身体, 効果文 or None, 削り幅, 消費ゲージ, 初期ゲージ)。

    効果文が None なら**兵法なし**（基準 V0）。段は身体の札へ直接かぶせる。
    **`_minus_one` は名簿の行から引く**ので段の差し替えは身体の作り直しには効かない。
    ここでは同じ身体の中で段だけを変えて比べるので、そのずれは**両方に同じだけ乗る**。
    """
    name, text, shave, gc, gi = job
    c = _S["cards"][name]
    row = [r for r in R.skills() if r["武将"] == name][0]
    sk = row["兵法名"]
    if text is None:
        c = replace(c, skill="")
    else:
        with F.unscaled():
            F.SKILL_INFO[sk] = F._parse_skill(text, SPD_TARGET)
        F.SKILL_TARGET[sk] = SPD_TARGET
        if shave > 0.0:
            c = SP._minus_one(c, _S["rows"][name], shave)
        c = replace(c, skill=sk, gauge_cost=gc, gauge_init=gi)
    d = []
    for base in _S["bases"]:
        a = SP._swap_into(base, c)
        for i, o in enumerate(_S["opps"]):
            d.append(F.simulate(a, o, dt=SP.DT, seed=i * 7 + 1)["diff"])
    return d


def spd_main():
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv else 2
    want = sys.argv[sys.argv.index("--regs") + 1].split(",") if "--regs" in sys.argv else None
    regs = [(n, c) for n, c in M.REGULATIONS if not want or "{:g}".format(c) in want]
    # **削り幅に −1 を入れること。** ここの統計量は「交点そのもの」で、`cross` は
    # 0 以上しか返せないため、0〜3 だけで測ると**再抽出の分布が 0 で床を打ち**、
    # 「95%区間が 0 を外した」が**必ず成り立ってしまう**（一度踏んだ）。
    # −1 は「身体を1点太らせる」＝ `_minus_one(c, row, -1.0)` で、交点が負にもなれる。
    shave = (tuple(float(x) for x in sys.argv[sys.argv.index("--shave") + 1].split(","))
             if "--shave" in sys.argv else (-1.0, 0.0, 1.0, 2.0, 3.0))
    R.load_skills_into_field(); R.load_traits_into_field()
    rows = {g["名前"]: g for g in R.generals()}

    print("『{}』（対象 {}）を、**発動時刻 × 持ち手の兵種**で振る（§7.228 の続き）".format(
        SPD_EFFECT, SPD_TARGET))
    print("  請求はどの組でも 0.00（EFFECT_PRICE['spd'] = 0.0）。削り幅{}・1案{}局".format(
        list(shave), 12 * 12 * seeds))
    print("  **兵種をまたいで大小は比べない**（身体1点の重さが違う・§7.185）。"
          "見るのは『同じ身体の中での動き』と『0 か 0 でないか』。\n")
    print("  身体: " + " / ".join("{}（{} c{}）".format(
        n, rows[n]["兵種"], int(float(rows[n]["コスト"]))) for n in SPD_BODIES))
    print()

    out = {}
    for label, cap in regs:
        os.environ["PANEL_TOTAL"] = str(cap); SP.TOTAL = cap
        jobs = [(n, None, 0.0, 300.0, 0.0) for n in SPD_BODIES]
        jobs += [(n, SPD_EFFECT, d, gc, gi)
                 for n in SPD_BODIES for gc, gi, _ in SPD_GAUGE for d in shave]
        jobs = list(dict.fromkeys(jobs))
        pool = Pool(int(os.environ.get("W", "8")), _init, (seeds,))
        got = dict(zip(jobs, pool.map(_one_spd, jobs)))
        pool.close(); pool.join()
        out["{:g}".format(cap)] = {"|".join(map(str, k)): v for k, v in got.items()}
        print("■ {}（上限 {:g}点）".format(label, cap), flush=True)
        spd_report(got, shave, seeds)
        print()

    dump = (sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv
            else "/tmp/claude-0/skill_worth_spd.json")
    json.dump(out, open(dump, "w"))
    print("生データ: {}".format(dump))
    print("SPD DONE")


def spd_report(got, shave, seeds):
    ALL = TP.game_index(seeds, range(TP.NBASE), range(TP.NPERS))
    rng = random.Random(12321)
    draws = [TP.cluster_draw(rng, seeds) for _ in range(300)]

    def V0(name, ix):
        return statistics.mean(got[(name, None, 0.0, 300.0, 0.0)][i] for i in ix)

    def worth(name, gc, gi, ix):
        vals = [(d, statistics.mean(got[(name, SPD_EFFECT, d, gc, gi)][i] for i in ix))
                for d in shave]
        return cross(vals, V0(name, ix))

    def raw(name, gc, gi, ix):
        """削らないままの残存差の増分（盤面の量そのもの）。単位を通さない 0 の検査。"""
        return (statistics.mean(got[(name, SPD_EFFECT, 0.0, gc, gi)][i] for i in ix)
                - V0(name, ix))

    # 【0 の検査は「残存差」で行う】交点（コスト点）は `cross` が 0 以上しか返せないので、
    # **再抽出の分布が 0 で床を打ち、「区間が 0 を外した」が必ず成り立ってしまう**（一度踏んだ）。
    # 削り幅に −1 を入れて負も取れるようにしたが、値打ちがほぼ 0 の組では
    # やはり 0 の近くへ潰れる。**床の無い量＝削らないままの残存差の増分**を主にする。
    print("  {:<16}{:<20}{:>10}{:>19}{:>12}{:>11}".format(
        "身体", "発動時刻", "残存差の増分", "95%区間", "0 を含むか", "値打ち(点)"))
    for name in SPD_BODIES:
        for gc, gi, lab in SPD_GAUGE:
            r = raw(name, gc, gi, ALL)
            bs = sorted(raw(name, gc, gi, ix) for ix in draws)
            lo, hi = bs[int(.025*len(bs))], bs[int(.975*len(bs))]
            x = worth(name, gc, gi, ALL)
            print("  {:<16}{:<20}{:>+10.4f}{:>19}{:>12}{:>11}".format(
                name, "{}（初期{:.0f}）".format(lab, gi), r,
                "[{:+.4f}, {:+.4f}]".format(lo, hi),
                "含む" if lo <= 0 <= hi else "**含まない**",
                "{:+.2f}".format(x) if x is not None else "挟めず"))
        print()


if __name__ == "__main__":
    if "--spd" in sys.argv:
        spd_main()
    elif "--ablate" in sys.argv:
        ablate_main()
    elif "--curve" in sys.argv:
        curve_main()
    elif "--swap" in sys.argv:
        swap_main()
    else:
        main()
