# -*- coding: utf-8 -*-
"""必殺技1つの**盤面での値打ち**を測り、式が請求している額と並べる（§7.112）。

    python3 tools/skill_price.py 張飛〔当陽橋〕 張郃〔巧変〕
    python3 tools/skill_price.py --all           # 全札（重い）

なぜ要るか。`one_ruler` は「この札は帯の真ん中よりN点弱い」までは言えるが、
**どこで損をしているかは言わない**。能力値が薄いのか、技が高いのか、特性が
効いていないのかが分からないと、直しようがない。ここは技だけを取り出す。

**測り方は §7.5 と同じ通貨。** 同じ札を2つ用意し、片方から必殺技だけを外して
差を取る。能力値・特性・槍・陣形はすべて同一なので、**差は技のぶんだけ**になる。
コスト点は `cost_yardstick` で割って出す（総コスト1点＝残存差 0.01993）。

**布陣は規則どおりに組む。** `one_ruler` と同じく行の中だけで回す。6枠へ
回すと弓兵が前衛に立つ登録不能な盤面を測ることになる（§7.96 の穴）。

読み方:

    請求  … `design.effect_value` が能力値から引いている額（＝技の値札）
    実測  … 盤面で技が返している額
    差    … 実測 - 請求。マイナスなら**払い過ぎ**（その札は損をしている）

差が -0.5 点を下回ったら値付けを疑う。ただし**1枚で結論しない** — 同じ器
（対象範囲・段・効果語）を使う他の札も測って、器の値段が高いのか、その札の
噛み合わせが悪いだけなのかを分けること（§13）。
"""
import sys, os, csv, statistics
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "sim", "data", "generals.csv")
FORMS = ("鶴翼", "魚鱗", "雁行")
NF = {"鶴翼": 4, "魚鱗": 3, "雁行": 2}


def _rows(form_name, card, in_front, ff, rf):
    nf, nr = NF[form_name], 6 - NF[form_name]
    if in_front:
        return [card] + [ff] * (nf - 1), [rf] * nr
    return [ff] * nf, [card] + [rf] * (nr - 1)


def _cost(A_rows, B_rows, form, slope, dt=0.5):
    from sim import field as G
    from sim import match as MM
    (af, ar), (bf, br) = A_rows, B_rows
    tot, n = 0.0, 0
    for i in range(3):
        A = G.Army(tuple(af[i:] + af[:i] + ar[i:] + ar[:i]), form)
        assert not MM.placement_errors(A), "規則に反する布陣を測ろうとした"
        for j in range(3):
            B = G.Army(tuple(bf[j:] + bf[:j] + br[j:] + br[:j]), form)
            assert not MM.placement_errors(B)
            tot += (G.margin(A, B, dt) - G.margin(B, A, dt)) / 2.0
            n += 1
    return (tot / n) / slope


def _refund(g, want):
    """必殺技を外し、その値札ぶんを能力値へ戻した同じ札（＝釣り合いの相手）。

    **能力値は設計式で引き直す。** `stat_cost` を上げるだけでは兵力しか動かない
    （武力・知力は CSV の値をそのまま持つ）ので、技代を返したことにならない。
    """
    import dataclasses
    from sim import design as D, field as F, rosterdata as R
    d = R.to_design(g)
    d = dataclasses.replace(d, effect=max(d.effect - want, 0.0))
    v = D.derive(d)
    return F.Card(
        cost=d.cost, stat_cost=d.cost - v["効果予算"], typ=d.typ,
        role=D.ROLE_KEY[d.role], name=g["名前"], trait=g["固有特性"],
        faction=g["勢力"], might=v["武力"], wits=v["知力"], skill="",
        lean=d.lean, def_lean=d.def_lean, spd_lean=d.spd_lean,
        floor_adj=d.floor_adj, spear=bool((g.get("槍") or "").strip()),
        gauge_cost=d.gauge_cost, gauge_rate=v["気勢"], gauge_init=d.gauge_init)


def measure(job):
    """必殺技を「持つ札」と「値札ぶんを能力値へ戻した札」を突き合わせる。

    **これが値付けの検算そのもの。** プラスなら技のほうが得（安売り）、
    マイナスなら能力値のほうが得（払い過ぎ）。零なら値札が当たっている。

    技だけを外して**能力値はそのまま**にする測り方もあるが、それは「技の
    値打ち」を測るのであって「値札が当たっているか」ではない。値札が当たって
    いるかは、払った先（能力値）と突き合わせないと出ない。
    """
    import dataclasses
    name, form_name = job
    from sim import field as G
    from sim import match as MM
    G.TRAITS_ON = True                     # 実ゲームと同じ条件（§7.67）
    c = {x.name: x for x in MM._roster_cards()}[name]
    g = {r["名前"]: r for r in csv.DictReader(
        open(CSV, encoding="utf-8-sig"))}[name]
    form = G.Formation(n_front=NF[form_name], frontage=G.BASE_FRONTAGE)
    ff = G._synth(4.0, G.INF, G.BAL)
    rf = G._synth(4.0, G.ARC, G.DPS)
    in_front = c.typ != G.ARC              # 槍持ちも物差しは前衛側で当てる
    slope = G.cost_yardstick(0.5)
    bare = dataclasses.replace(c, skill="")          # 能力値そのまま
    back = _refund(g, charged(g))                    # 値札ぶんを能力値へ戻す
    A = _rows(form_name, c, in_front, ff, rf)
    return (name, form_name,
            _cost(A, _rows(form_name, bare, in_front, ff, rf), form, slope),
            _cost(A, _rows(form_name, back, in_front, ff, rf), form, slope))


def charged(g):
    """`design.effect_value` が請求している額（技のぶんだけ。特性・槍は除く）。"""
    from sim import design as D, field as F, rosterdata as R
    sk = F.SKILL_INFO.get(g["必殺技"])
    if not sk:
        return 0.0
    return D.effect_value(sk, R._skill_target(g["必殺技"]),
                          float(g["消費ゲージ%"]), float(g["初期ゲージ"]),
                          kisei=float(g["ゲージ上昇率"]) / 100.0,
                          cost=float(g["コスト"]), typ=R.TYPE_MAP[g["兵種"]],
                          tilt=R.tilt_of(g))


SWEEP_POWERS = (500, 800, 1100, 1500, 2000, 2600, 3300)
SWEEP_TARGETS = ("敵1体（兵力が最多）", "敵1列", "敵前衛", "敵後列")


def sweep_one(job):
    """車台に威力Pの大技だけを載せて、盤面での値打ちを測る（§7.112）。

    **能力値は下げない。** ここで測るのは「威力Pの技そのものの値打ち」で、
    値札を払わせると払った額が測定値へ混ざる（自己言及になる）。実カードが
    払い過ぎているかは main() 側の比較で見る。
    """
    import dataclasses
    power, target, form_name = job
    from sim import field as G
    from sim import match as MM
    G.TRAITS_ON = G.SKILLS_ON = True
    n = "＿掃引{}".format(power)
    G.SKILL_INFO[n] = G.Skill(power / 100.0, G._skill_kind("ダメージ", target))
    G.SKILL_TARGET[n] = target
    base = G._synth(SWEEP_COST, G.INF, G.BAL)
    form = G.Formation(n_front=NF[form_name], frontage=G.BASE_FRONTAGE)
    ff = G._synth(4.0, G.INF, G.BAL)
    rf = G._synth(4.0, G.ARC, G.DPS)
    hot = dataclasses.replace(base, skill=n, gauge_cost=300.0, gauge_init=140.0)
    cold = dataclasses.replace(base, skill="", gauge_cost=300.0, gauge_init=140.0)
    slope = G.cost_yardstick(0.5)
    return power, target, _cost(_rows(form_name, hot, True, ff, rf),
                               _rows(form_name, cold, True, ff, rf), form, slope)


SWEEP_COST = 5.0


def do_sweep():
    """討ち取りの割増（design.KILL_PREMIUM）を規則どおりの布陣で測り直す。

    §7.63/§7.77 の較正は `matchup_cost` を6枠へ回して当てたので、**弓兵が
    前衛に立つ登録不能な盤面**が混ざっていた（§7.96 で見つけた穴）。以後
    迂回（§7.108）・陣形の深さ・知力の傾き（§7.110）も動いている。表の
    但し書き「エンジンの定数を動かしたら測り直すこと」の履行である。

    割増 = 実測(P) − 実測(500) × damage_price(P)/damage_price(500)
    （§7.63 と同じ取り方。威力500%の行を床にして、基礎の削り値段ぶんを
    比例で伸ばした残りを割増と読む）。
    """
    from sim import design as D
    jobs = [(p, t, f) for p in SWEEP_POWERS for t in SWEEP_TARGETS for f in FORMS]
    print("討ち取りの割増を測り直す（車台コスト{:.0f}・歩兵均衡・大技300/140）"
          .format(SWEEP_COST))
    print("  威力 {} × 対象 {} × 陣形 {} = {}局\n".format(
        len(SWEEP_POWERS), len(SWEEP_TARGETS), len(FORMS), len(jobs)), flush=True)
    res = Pool(4).map(sweep_one, jobs, chunksize=1)
    agg = {}
    for p, t, v in res:
        agg.setdefault((p, t), []).append(v)
    mean = {k: statistics.mean(v) for k, v in agg.items()}
    by_p = {p: statistics.mean([mean[(p, t)] for t in SWEEP_TARGETS])
            for p in SWEEP_POWERS}

    floor = by_p[500]
    d500 = D.damage_price(5.0)
    print("{:>6s}{:>9s}{:>9s}{:>9s}{:>9s}   {}".format(
        "威力%", "実測", "基礎ぶん", "割増(実測)", "割増(表)", "対象ごと"))
    for p in SWEEP_POWERS:
        base = floor * D.damage_price(p / 100.0) / d500
        got = by_p[p] - base
        want = D._interp1(D.KILL_PREMIUM, float(p)) if hasattr(D, "_interp1") \
            else _interp(D.KILL_PREMIUM, float(p))
        print("{:>6d}{:>9.3f}{:>9.3f}{:>9.3f}{:>9.3f}   {}".format(
            p, by_p[p], base, got, want,
            " ".join("{:.2f}".format(mean[(p, t)]) for t in SWEEP_TARGETS)))
    print("\n※ 割増(実測) は**この車台での1枚のコスト点**。表と符号・大小の向きが")
    print("   合っているかを見る。合っていなければ design.KILL_PREMIUM を書き替える。")
    return 0


def spear_one(job):
    """槍の値打ち＝**後衛へ置ける権利**をコスト点で測る（§7.112）。

    A と B は**同じ6枚**で、置き場所だけが違う:

        A … 札を後衛へ（槍が効く。`field.Unit` は `spear and not is_front` で
            しか槍を見ない）。空いた前衛枠は前衛の詰め物
        B … 札を前衛へ（槍は何もしない）。空いた後衛枠は後衛の詰め物

    札の集合が同一なので**零点が汚れない**。差がプラスなら後衛へ置けることに
    値打ちがあり、それが `design.SPEAR_PRICE` の払っているものである。
    """
    name, form_name = job
    from sim import field as G
    from sim import match as MM
    G.TRAITS_ON = True
    c = {x.name: x for x in MM._roster_cards()}[name]
    form = G.Formation(n_front=NF[form_name], frontage=G.BASE_FRONTAGE)
    ff = G._synth(4.0, G.INF, G.BAL)
    rf = G._synth(4.0, G.ARC, G.DPS)
    slope = G.cost_yardstick(0.5)
    return name, form_name, _cost(_rows(form_name, c, False, ff, rf),
                                  _rows(form_name, c, True, ff, rf),
                                  form, slope)


def do_spear():
    from sim import design as D
    from sim import field as G
    # **旧設定と比べられるようにする**（§13「撤回は測り直してから」）。
    # SPEAR_PRICE=0.75 は §7.77 に SPEAR_REAR=0.5・SPEAR_GUARD=False で当てた値。
    #   python3 tools/skill_price.py --spear --rear 0.5 --guard 0
    if "--rear" in sys.argv:
        G.SPEAR_REAR = float(sys.argv[sys.argv.index("--rear") + 1])
    if "--guard" in sys.argv:
        G.SPEAR_GUARD = bool(int(sys.argv[sys.argv.index("--guard") + 1]))
    names = [g["名前"] for g in csv.DictReader(open(CSV, encoding="utf-8-sig"))
             if (g.get("槍") or "").strip()]
    jobs = [(n, f) for n in names for f in FORMS]
    print("槍の値打ちを測り直す（同じ6枚・置き場所だけが違う）")
    print("  値札 design.SPEAR_PRICE = {:.2f}  ／  いまの盤面: "
          "SPEAR_REAR={} SPEAR_GUARD={}\n".format(
              D.SPEAR_PRICE, __import__("sim.field", fromlist=["x"]).SPEAR_REAR,
              __import__("sim.field", fromlist=["x"]).SPEAR_GUARD), flush=True)
    res = Pool(4).map(spear_one, jobs, chunksize=1)
    by = {}
    for n, f, v in res:
        by.setdefault(n, {})[f] = v
    print("{:<16s}{:>6s}{:>9s}{:>9s}   {}".format(
        "武将", "コスト", "後衛の得", "値札との差", "陣形ごと（鶴/魚/雁）"))
    rows = {g["名前"]: g for g in csv.DictReader(open(CSV, encoding="utf-8-sig"))}
    got = []
    for n in sorted(names, key=lambda x: float(rows[x]["コスト"])):
        vs = by[n]
        v = statistics.mean(vs.values())
        got.append(v)
        print("{:<16s}{:>6.0f}{:>9.3f}{:>+9.3f}   {}".format(
            n, float(rows[n]["コスト"]), v, v - D.SPEAR_PRICE,
            " ".join("{:+.2f}".format(vs[f]) for f in FORMS)))
    print("\n後衛の得: 中央 {:.3f}  平均 {:.3f}  幅 {:.2f}〜{:.2f}".format(
        statistics.median(got), statistics.mean(got), min(got), max(got)))
    print("→ 値札 {:.2f} との差の中央 {:+.3f}".format(
        D.SPEAR_PRICE, statistics.median(got) - D.SPEAR_PRICE))
    return 0


TRAIT_TRIALS = 400


def trait_one(job):
    """実デッキの総当たりで、特性を1枚に載せた差を測る（§7.113）。

    **`sim/field.py traits` では測れない特性がある。** あちらは釣り合った合成軍
    なので**誰も全滅しない**。`self_dead` を条件に持つ特性はそこで必ず 0.0000 と
    出るが、実デッキでは隊の全滅は普通に起きる（92%の戦で・平均21%の隊が・
    戦の22%地点で）。条件が現実に起きる場ででしか値段は付かない。
    """
    import random, dataclasses
    key, row, trial = job
    from sim import field as G
    from sim import match as MM
    G.TRAITS_ON = G.SKILLS_ON = True
    cards = MM._roster_cards()
    by = {}
    for c in cards:
        by.setdefault(c.typ, []).append(c)
    rng = random.Random(9000 + trial)
    fn = rng.choice(list(NF))
    nf, nr = NF[fn], 6 - NF[fn]
    form = G.Formation(n_front=nf, frontage=G.BASE_FRONTAGE)

    # **デッキは1組だけ引いて、有り/無しの2つへ写す。** 引き直すと有り側と
    # 無し側で中身が変わり、特性の差が編成のばらつきに埋もれる（初版はこれで
    # 標準誤差 0.37 に対し値 0.42 と、測っていないに等しかった）。
    front = [rng.choice(by[G.INF] + by[G.CAV]) for _ in range(nf)]
    rear = [rng.choice(by[G.ARC]) for _ in range(nr)]

    def army(mark):
        f, r = list(front), list(rear)
        if mark:
            xs = f if row == "前衛" else r
            # **上へ重ねる。** `trait=key` と書くと元の特性を消してしまい、
            # 測っているのは「新しい特性 − 消した特性」になる（初版はこれで
            # 陣頭持ちを引き当て、値段が -0.20 と負に出た）。
            old = xs[0].trait
            xs[0] = dataclasses.replace(
                xs[0], trait=(old + G.TRAIT_SEP + key) if old else key)
        return G.Army(tuple(f + r), form)

    A, B = army(True), army(False)
    # 反対称化。同じ種で左右を入れ替え、席の有利不利を打ち消す。
    d = (G.simulate(A, B, 0.5, seed=100 + trial)["diff"]
         - G.simulate(B, A, 0.5, seed=100 + trial)["diff"]) / 2.0
    return d / G.cost_yardstick(0.5)


def do_trait():
    key = sys.argv[sys.argv.index("--trait") + 1]
    print("特性 {} の値段を実デッキで測る（{}戦 × 前衛/後衛）".format(
        key, TRAIT_TRIALS), flush=True)
    print("※ `python3 -m sim.field traits` は釣り合った合成軍なので誰も全滅せず、")
    print("   self_dead を条件に持つ特性は必ず 0.0000 と出る。ここは実デッキで測る。\n")
    for row in ("前衛", "後衛"):
        res = Pool(4).map(trait_one, [(key, row, i) for i in range(TRAIT_TRIALS)],
                          chunksize=8)
        print("  {} に載せた場合  値段 {:+.4f} コスト点   （n={}・標準誤差 {:.4f}）"
              .format(row, statistics.mean(res), len(res),
                      statistics.pstdev(res) / len(res) ** 0.5), flush=True)
    return 0


def _interp(table, x):
    if x <= table[0][0]:
        return table[0][1]
    for (x0, y0), (x1, y1) in zip(table, table[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return table[-1][1]


def main():
    from sim import rosterdata as R
    R.load_skills_into_field()
    R.load_traits_into_field()
    if "--sweep" in sys.argv:
        return do_sweep()
    if "--spear" in sys.argv:
        return do_spear()
    if "--trait" in sys.argv:
        return do_trait()
    rows = {g["名前"]: g for g in csv.DictReader(open(CSV, encoding="utf-8-sig"))}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = list(rows) if "--all" in sys.argv else args
    if not names:
        print(__doc__)
        return 2
    miss = [n for n in names if n not in rows]
    if miss:
        print("武将が見つからない:", "、".join(miss))
        return 1
    names = [n for n in names if rows[n]["必殺技"]]

    jobs = [(n, f) for n in names for f in FORMS]
    print("測る: {} 枚 × {} 陣形（技だけを外した同じ札との差）\n".format(
        len(names), len(FORMS)), flush=True)
    res = Pool(4).map(measure, jobs, chunksize=1)
    by, fair = {}, {}
    for n, f, v, w in res:
        by.setdefault(n, {})[f] = v
        fair.setdefault(n, {})[f] = w

    print("{:<16s}{:>4s} {:<12s}{:>7s}{:>7s}{:>8s}   {}".format(
        "武将", "消費", "必殺技", "請求", "値打ち", "釣り合い", "釣り合い（鶴/魚/雁）"))
    out = []
    for n in sorted(names, key=lambda x: float(rows[x]["コスト"])):
        g, vs, fs = rows[n], by[n], fair[n]
        want = charged(g)
        got = statistics.mean(vs.values())
        bal = statistics.mean(fs.values())
        out.append((n, want, got, bal))
        print("{:<16s}{:>4.0f} {:<12s}{:>7.3f}{:>7.3f}{:>+8.3f}   {}".format(
            n, float(g["消費ゲージ%"]), g["必殺技"], want, got, bal,
            " ".join("{:+.2f}".format(fs[f]) for f in FORMS)))
    if len(out) > 1:
        d = [x[3] for x in out]
        print("\n釣り合いの中央 {:+.3f}  平均 {:+.3f}  幅 {:+.2f}〜{:+.2f}".format(
            statistics.median(d), statistics.mean(d), min(d), max(d)))
    print("\n請求 = 値札（design.effect_value）／値打ち = 能力値を据え置いて技だけ外した差")
    print("釣り合い = 値札ぶんを能力値へ戻した同じ札との差。**マイナスが払い過ぎ**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
