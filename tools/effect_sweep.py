# -*- coding: utf-8 -*-
"""兵法の**器ごとの単価**を、いまの盤面で測り直す（§7.147・兵法の再較正）。

    python3 tools/effect_sweep.py            # 全探針（重い・約1時間）
    python3 tools/effect_sweep.py --quick    # 各器から1本ずつ（動作確認）
    python3 tools/effect_sweep.py --family damage heal   # 器を絞る

なぜ要るか。単価表（design.EFFECT_PRICE・DAMAGE_K・TARGET_*・TIER_WEIGHT）は旧盤面の
15通りパネルで当てた値で、§7.145 の再較正（兵種の層）を通っていない。実カードの
残差（`skill_price.py --all`）は「どの札が損か」は言うが「どの器の単価が古いか」は
器を1つずつ切り離さないと分からない。ここは **合成の車台に合成の兵法を1本だけ載せ**、
効果文をそのまま `field._parse_skill` に読ませて（実カードと同じ文法・同じ経路）、
盤面での値打ちと `design.effect_value` の請求を並べる。

**計器は skill_price.py と同じ**（§7.117）: 詰め物は車台と同コスト、局所勾配で換算、
布陣は規則どおり（弓の車台は後衛）、3陣形の平均。**能力値は下げない**（値打ちそのものを
測る。値札を払わせると払った額が測定値へ混ざる）。

読み方: 比 = 実測 ÷ 請求。1.0 なら単価が当たっている。器の中で比が量・対象・段に
よらず揃っていれば「単価を比で掛け直す」だけで直る。揃っていなければ形（冪・対象係数・
段の重み）の話なので、その軸を見る。
"""
import os, sys, statistics, dataclasses
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.skill_price import _rows, _cost, local_slope, NF, FORMS

TIERS = {"手数": (75.0, 0.0), "標準": (150.0, 60.0), "大技": (300.0, 140.0)}
E1 = "敵1体（正面）"; EL = "敵1列"; EF = "敵前衛"; EA = "敵全体"; ER = "敵後衛"
AF = "味方前衛"; AA = "味方全体"; AL = "味方1列"; AR = "味方後衛"; A1 = "味方1体（残兵力が最少）"; ME = "自分"

# (器, 効果文, 対象, 段, 車台コスト, 車台兵種)
P = []
def add(fam, eff, tgt, tier="標準", cost=5.0, typ="inf"):
    P.append((fam, eff, tgt, tier, cost, typ))
# 打ち切りダメージ: 威力の冪・対象・段・車台
for pw in (300, 600, 1000, 1500):
    add("damage", f"ダメージ 威力{pw}%", E1)
for t in (EL, EF, EA, ER):
    add("damage", "ダメージ 威力1000%", t)
for tier in ("手数", "大技"):
    add("damage", "ダメージ 威力1000%", E1, tier)
add("damage", "ダメージ 威力600%", E1, "手数")
add("damage", "ダメージ 威力2000%", E1, "大技"); add("damage", "ダメージ 威力2500%", EL, "大技")
add("damage", "ダメージ 威力1000%", E1, "標準", 10.0); add("damage", "ダメージ 威力1500%", E1, "大技", 10.0)
add("damage", "ダメージ 威力1000%", E1, "標準", 5.0, "arc"); add("damage", "ダメージ 威力1000%", EL, "標準", 8.0, "arc")
add("damage", "ダメージ 威力600%", EL, "手数", 5.0, "arc")
# 継続ダメージ
add("dot", "継続ダメージ 威力25%（12秒）", EA); add("dot", "継続ダメージ 威力50%（10秒）", EL)
add("dot", "継続ダメージ 威力30%（12秒）", EA, "標準", 5.0, "arc")
# 回復
for t in (AF, AA, A1):
    add("heal", "回復 攻撃力の200%", t)
add("heal", "回復 攻撃力の400%", AF); add("heal", "回復 攻撃力の200%", AF, "手数")
add("heal", "回復 攻撃力の200%", AA, "標準", 5.0, "arc"); add("heal", "回復 攻撃力の300%", AL, "標準", 8.0, "arc")
# 強化・弱体（攻撃・防御）
add("atk", "攻撃力 +20%（30秒）", AF); add("atk", "攻撃力 +20%（30秒）", AA); add("atk", "攻撃力 +20%（60秒）", AF)
add("atk", "攻撃力 -20%（30秒）", EF); add("atk", "攻撃力 -20%（30秒）", EA)
add("atk", "攻撃力 +20%（30秒）", AA, "標準", 5.0, "arc"); add("atk", "攻撃力 +15%（30秒）", AL, "手数", 5.0, "arc")
add("def", "防御力 +30%（30秒）", AF); add("def", "防御力 +30%（30秒）", AA); add("def", "防御力 -30%（30秒）", EF)
add("def", "防御力 +30%（30秒）", AA, "標準", 5.0, "arc")
# 足止め・混乱
add("stun", "行動阻害 3秒", EF); add("stun", "行動阻害 5秒", E1, "手数"); add("stun", "行動阻害 4秒", EL, "標準", 5.0, "arc")
add("chaos", "混乱 25%（14秒）", EF); add("chaos", "混乱 40%（20秒）", EL); add("chaos", "混乱 20%（30秒）", E1)
add("chaos", "混乱 25%（14秒）", EF, "標準", 5.0, "arc")
# 構え（兵法防御・通常攻撃防御・反射・打消し）
add("scut", "兵法防御 +30%（30秒）", AF); add("scut", "兵法防御 +30%（30秒）", AA); add("scut", "兵法防御 +30%（30秒）", AA, "標準", 5.0, "arc")
add("ncut", "通常攻撃防御 +15%（23秒）", AL); add("ncut", "通常攻撃防御 +15%（23秒）", AF); add("ncut", "通常攻撃防御 +15%（23秒）", AL, "標準", 5.0, "arc")
add("refl", "兵法反射 +30%（30秒）", AR, "標準", 5.0, "arc")
add("null", "兵法打消し（60秒）", AF); add("null", "兵法打消し（60秒）", AF, "標準", 7.0, "arc"); add("null", "兵法打消し（30秒）", AF)


def one(job):
    fam, eff, tgt, tier, cost, typ, form_name, slope = job
    from sim import field as G, design as D, match as MM
    G.TRAITS_ON = G.SKILLS_ON = True
    n = "＿探針"
    sk = G._parse_skill(eff, tgt)
    G.SKILL_INFO[n] = sk; G.SKILL_TARGET[n] = tgt
    gc, gi = TIERS[tier]
    T = {"inf": G.INF, "arc": G.ARC}[typ]
    role = G.BAL if T == G.INF else G.DPS
    base = G._synth(cost, T, role)
    hot = dataclasses.replace(base, skill=n, gauge_cost=gc, gauge_init=gi)
    cold = dataclasses.replace(base, skill="", gauge_cost=gc, gauge_init=gi)
    ff = G._synth(cost, G.INF, G.BAL); rf = G._synth(cost, G.ARC, G.DPS)
    in_front = T != G.ARC
    form = G.Formation(n_front=NF[form_name], frontage=G.BASE_FRONTAGE)
    from tools.skill_price import _cost_raw
    raw = _cost_raw(_rows(form_name, hot, in_front, ff, rf), _rows(form_name, cold, in_front, ff, rf), form)
    want = D.effect_value(sk, tgt, gc, gi, kisei=1.0, cost=cost, typ=T, tilt="中庸")
    return (fam, eff, tgt, tier, cost, typ, form_name, raw, slope, want)


def main():
    from sim import field as G
    G.TRAITS_ON = G.SKILLS_ON = True
    probes = list(P)
    if "--family" in sys.argv:
        fams = set(sys.argv[sys.argv.index("--family") + 1:])
        probes = [p for p in probes if p[0] in fams]
    if "--quick" in sys.argv:
        seen = set(); q = []
        for p in probes:
            if p[0] not in seen:
                seen.add(p[0]); q.append(p)
        probes = q
    # 局所勾配は (車台コスト, 兵種, 陣形) ごとに1回
    slopes = {}
    for (_, _, _, _, cost, typ) in probes:
        for f in FORMS:
            k = (cost, typ, f)
            if k in slopes:
                continue
            T = {"inf": G.INF, "arc": G.ARC}[typ]
            ff = G._synth(cost, G.INF, G.BAL); rf = G._synth(cost, G.ARC, G.DPS)
            slopes[k] = local_slope(G._synth(cost, T, G.BAL if T == G.INF else G.DPS), f, ff, rf, in_front=(T != G.ARC))
    print("局所勾配: " + " ".join(f"{c:.0f}{t}/{f}={s:.4f}" for (c, t, f), s in sorted(slopes.items())), flush=True)
    jobs = [p + (f, slopes[(p[4], p[5], f)]) for p in probes for f in FORMS]
    print(f"探針 {len(probes)} × 陣形 3 = {len(jobs)} 局", flush=True)
    res = Pool(int(os.environ.get("SWEEP_WORKERS", "4"))).map(one, jobs, chunksize=1)
    agg = {}
    for fam, eff, tgt, tier, cost, typ, f, raw, slope, want in res:
        agg.setdefault((fam, eff, tgt, tier, cost, typ), {})[f] = (raw, slope, want)
    print()
    # **陣形の合算は勾配の重み付き**（Σ残存差 ÷ Σ勾配）。雁行の前衛（後衛4枠が決める）や
    # 鶴翼の後衛（後衛2枠）は局所勾配が 1/10 に落ち、その陣形だけで割ると値が10倍に化ける。
    # 生の残存差を足してから勾配の和で割れば「3陣形を通して、能力値の何点ぶんか」になる。
    print("{:<6}{:<26}{:<16}{:<4}{:>5}{:>4}{:>8}{:>8}{:>7}   {}".format("器", "効果", "対象", "段", "車台", "種", "実測", "請求", "比", "陣形別(鶴/魚/雁・勾配1/10未満は*)"))
    fam_ratios = {}
    for key in sorted(agg, key=lambda k: (P.index(k) if k in P else 0)):
        fam, eff, tgt, tier, cost, typ = key
        by = agg[key]
        got = sum(v[0] for v in by.values()) / sum(v[1] for v in by.values())
        want = next(iter(by.values()))[2]
        ratio = got / want if abs(want) > 1e-9 else float("nan")
        fam_ratios.setdefault(fam, []).append(ratio)
        smax = max(v[1] for v in by.values())
        cells = []
        for f in FORMS:
            if f in by:
                raw, sl, _ = by[f]
                cells.append("{:+.2f}{}".format(raw / sl if sl > 1e-9 else float("nan"), "*" if sl < smax / 10 else ""))
        print("{:<6}{:<26}{:<16}{:<4}{:>5.0f}{:>4}{:>8.3f}{:>8.3f}{:>7.2f}   {}".format(
            fam, eff, tgt, tier, cost, typ, got, want, ratio, " ".join(cells)), flush=True)
    print()
    print("器ごとの比（実測÷請求）: 平均 / 中央値 / 幅")
    for fam, rs in fam_ratios.items():
        rs = [r for r in rs if r == r]
        print("  {:<6} {:5.2f} / {:5.2f} / {:.2f}〜{:.2f}  (n={})".format(
            fam, statistics.mean(rs), statistics.median(rs), min(rs), max(rs), len(rs)))
    print("SWEEP DONE")


if __name__ == "__main__":
    main()
