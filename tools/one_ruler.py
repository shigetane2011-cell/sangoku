# -*- coding: utf-8 -*-
"""共通の物差し（§7.96）: 全カードを**ひとつの基準**で測り、文脈を変えて重ねる。

    python3 tools/one_ruler.py            # 3文脈で測って一覧（書かない）
    python3 tools/one_ruler.py --json X   # 結果を X へ書き出す

なぜ要るか。従来の盤面監査（`tools/floor_patch.py`）は、各カードを**同コスト・
同兵種・同役割の合成カード**と比べる。基準がカードごとに違うので、
**役割ぐるみ・兵種ぐるみの歪みが原理的に見えない**。実際そこに2つ隠れていた:

- 非耐久の前衛が耐久に対して 0.5〜1.3 点損（近接寄りの文脈）
- 騎兵が歩兵に対して低コストで 0.3〜0.4 点損（雁行では 0.57 点損）

どちらも「同兵種・同役割の合成と比べる」限り見えない。ここでは全カードを
**同コストの「歩兵・耐久」合成ひとつ**と比べる。基準が一本なので、役割も
兵種も同じ物差しに乗る。

**文脈を3つ重ねるのが肝である。** 前衛の値打ちは周りの構成で符号が変わる
（§13: 符号が変わるものに値段を置かない）。1文脈で測って値段を動かすのは
禁じ手なので、近接ばかり・混成・弓が厚いの3つで測り、**3つとも負の札だけ**を
帯調整の候補とする。1つでも正なら、それは値段ではなく編成の知識の話である。
"""
import sys, os, csv, json
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "sim", "data", "generals.csv")
# 計器の癖で測れない札（floor_patch と同じ）
SKIP_TRAITS = {"command", "vs_wei", "vs_shu", "vs_go"}
CONTEXTS = ("近接ばかり", "混成", "弓が厚い")


def _fill(name):
    from sim import field as G
    if name == "近接ばかり":
        return (G._synth(4.0, G.INF, G.TANK), G._synth(4.0, G.INF, G.BAL),
                G._synth(4.0, G.INF, G.DPS), G._synth(4.0, G.CAV, G.BAL),
                G._synth(4.0, G.CAV, G.DPS))
    if name == "弓が厚い":
        return (G._synth(4.0, G.INF, G.TANK), G._synth(4.0, G.INF, G.BAL),
                G._synth(4.0, G.ARC, G.DPS), G._synth(4.0, G.ARC, G.DPS),
                G._synth(4.0, G.ARC, G.DPS))
    return (G._synth(4.0, G.INF, G.TANK), G._synth(4.0, G.INF, G.BAL),
            G._synth(4.0, G.CAV, G.BAL), G._synth(4.0, G.ARC, G.DPS),
            G._synth(4.0, G.ARC, G.DPS))


def measure(job):
    name, ctx = job
    from sim import field as G
    from sim import match as MM
    G.TRAITS_ON = True                 # 実ゲームと同じ条件で測る（§7.67）
    c = {x.name: x for x in MM._roster_cards()}[name]
    # 物差し: 同コストの歩兵・耐久（前衛の標準）。**カードによらず一本**
    ruler = G._synth(c.cost, G.INF, G.TANK)
    fill = _fill(ctx)
    a = G.Army((c,) + fill, G.FORM_STANDARD)
    b = G.Army((ruler,) + fill, G.FORM_STANDARD)
    return name, ctx, G.matchup_cost(a, b, G.cost_yardstick(0.5), 0.5, 4)


def targets(rows):
    sk = {r["技名"]: r["効果"] for r in csv.DictReader(
        open(CSV.replace("generals", "skills"), encoding="utf-8-sig"))}
    out = []
    for n, g in rows.items():
        if set((g["固有特性"] or "").split("、")) & SKIP_TRAITS:
            continue
        if "打消し" in sk.get(g["必殺技"], ""):
            continue
        out.append(n)
    return out


def main():
    rows = {g["名前"]: g for g in csv.DictReader(open(CSV, encoding="utf-8-sig"))}
    names = targets(rows)
    jobs = [(n, c) for n in names for c in CONTEXTS]
    print("測る: {} 枚 × {} 文脈 = {} 局".format(len(names), len(CONTEXTS), len(jobs)),
          flush=True)
    res = Pool(4).map(measure, jobs, chunksize=1)
    by = {}
    for n, c, v in res:
        by.setdefault(n, {})[c] = round(v, 3)

    data = []
    for n, vs in by.items():
        g = rows[n]
        data.append({"name": n, "cost": float(g["コスト"]), "typ": g["兵種"],
                     "role": g["役割"], "ctx": vs,
                     "min": min(vs.values()), "max": max(vs.values()),
                     "avg": round(sum(vs.values()) / len(vs), 3)})
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        json.dump(data, open(out, "w"), ensure_ascii=False, indent=1)
        print("書き出した:", out)

    def band(c):
        return "1" if c < 2 else ("2-4" if c < 5 else ("5-7" if c < 8 else "8-10"))

    from collections import defaultdict
    print("\n── 帯 × 兵種（3文脈の平均）──")
    agg = defaultdict(list)
    for r in data:
        agg[(band(r["cost"]), r["typ"])].append(r["avg"])
    for k in sorted(agg):
        v = agg[k]
        print("  %-5s %s n=%2d 平均%+6.2f" % (k[0], k[1], len(v), sum(v) / len(v)))

    print("\n── 帯 × 役割（3文脈の平均）──")
    agg = defaultdict(list)
    for r in data:
        agg[(band(r["cost"]), r["role"])].append(r["avg"])
    for k in sorted(agg):
        v = agg[k]
        print("  %-5s %s n=%2d 平均%+6.2f" % (k[0], k[1], len(v), sum(v) / len(v)))

    firm = [r for r in data if r["max"] < 0.0]
    swing = [r for r in data if r["min"] < -0.30 <= 0.0 < r["max"]]
    print("\n── 3文脈とも負（＝文脈によらず弱い。帯調整の候補）: {} 枚 ──"
          .format(len(firm)))
    for r in sorted(firm, key=lambda x: x["avg"]):
        print("  %+6.2f  %-16s %s%.0f・%-2s  [%s]" % (
            r["avg"], r["name"], r["typ"][0], r["cost"], r["role"],
            " ".join("%s%+.2f" % (c[0], r["ctx"][c]) for c in CONTEXTS)))
    print("\n── 符号が文脈で変わる（＝値段では直せない。手引きの話）: {} 枚 ──"
          .format(len(swing)))
    for r in sorted(swing, key=lambda x: x["min"])[:15]:
        print("  min%+6.2f max%+6.2f  %-16s %s%.0f・%s" % (
            r["min"], r["max"], r["name"], r["typ"][0], r["cost"], r["role"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
