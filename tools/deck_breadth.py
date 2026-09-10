# -*- coding: utf-8 -*-
"""編成の「選べる幅」を測る — 上限コストを変えると悩む余地がどれだけ増えるか。

    python3 tools/deck_breadth.py --caps 18,20,24
    python3 tools/deck_breadth.py --caps 18,20 --panel 8 --bases testplay_20260908

きっかけ（テストプレイ）: 「デッキに入れる武将を悩む選択肢が狭い」。

【何を測るか】土台の編成を1組決め、**枠を1つだけ別の武将に差し替える**手を
全部あげて、それぞれの強さを同じ相手へ当てる。出す数字は3つ:

  合法    … その上限で予算・置き場・人物の重複を満たす差し替えの枚数
  互角以上… 元の札と同じかそれ以上の点を取った枚数
  好手    … その枠の最良から `--band` 以内に収まった枚数（＝**悩める枚数**）

上限は `--caps` の各値で測り直す。**土台の編成は上限が変わっても同じ**にして
ある（余った点は本番どおり余剰ゲージへ入る）。こうすると「同じ編成を前にして
予算だけ変えたら、検討できる札が何枚増えるか」という、編成画面の体験そのものの
比較になる。native な上限20の編成がどうなるかは別の問い（探索が要る）。

【相手】在野24人（`pools.official24`）から等間隔に `--panel` 組。
**special48 と final_blind は使わない**（handoff の禁）。1組につき1局だけ打つ
（同じ対戦を繰り返さず、組み合わせの数で精度を出す — handoff §0）。
差し替えの比較は全部が同じ相手・同じ種の**対**なので、左右の偏りは相殺される。

【置き場】差し替えで置き場の規則を破るときは、**同じ編成内で1回だけ入れ替えて**
直せるならそうする（人が編成画面でやることと同じ）。直せなければその手は数えない。
"""
import argparse
import json
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F          # noqa: E402
from sim import match as M          # noqa: E402
from sim import rosterdata as R     # noqa: E402
import tools.balance_common as BC   # noqa: E402

DT = 0.5
FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "balance", "fixtures-v1.json")

_CARDS = None


def _boot():
    """子プロセスごとに名簿と特性を読み込む（Pool の初期化子）。"""
    global _CARDS
    if _CARDS is None:
        F.TRAITS.clear()
        R.load_traits_into_field()
        R.load_skills_into_field()
        F.TRAITS_ON = True
        _CARDS = {c.name: c for c in M._roster_cards()}
    return _CARDS


def arrange(names, form):
    """並びを置き場の規則に合わせる。**元の並びをできるだけ保つ。**

    そのままで通ればそのまま。だめなら「1回だけ入れ替える」を全部試す。
    それでもだめなら None（その差し替えは打てない）。
    """
    cards = _boot()
    fm = BC.FORM_BY_NAME[form] if isinstance(form, str) else form
    def ok(seq):
        return not M.placement_errors(F.Army(tuple(cards[n] for n in seq), fm))
    seq = list(names)
    if ok(seq):
        return tuple(seq)
    for i in range(len(seq)):
        for j in range(i + 1, len(seq)):
            t = list(seq)
            t[i], t[j] = t[j], t[i]
            if ok(t):
                return tuple(t)
    return None


def _score(job):
    """1つの編成を相手パネルへ当てて、残存差の平均を返す。"""
    names, form_name, cap, panel, seed0 = job
    cards = _boot()
    form = BC.FORM_BY_NAME[form_name]
    me = M.with_surplus(F.Army(tuple(cards[n] for n in names), form), cap)
    out = []
    for k, (fnames, fform) in enumerate(panel):
        foe = M.with_surplus(
            F.Army(tuple(cards[n] for n in fnames), BC.FORM_BY_NAME[fform]), cap)
        out.append(F.simulate(me, foe, DT, seed=seed0 + k)["diff"])
    return statistics.fmean(out)


def candidates(names, slot, cap, cards):
    """枠 slot を差し替えられる札を全部あげる（予算・人物・置き場）。"""
    others = [n for i, n in enumerate(names) if i != slot]
    rest = sum(cards[n].cost for n in others)
    pids = {M.person_of(cards[n]) for n in others}
    out = []
    for n, c in cards.items():
        if n in others or M.person_of(c) in pids:
            continue
        if rest + c.cost > cap + 1e-9:
            continue
        out.append(n)
    return sorted(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--caps", default="18,20,24")
    ap.add_argument("--panel", type=int, default=12)
    ap.add_argument("--bases", default="testplay_20260908,"
                                       "counter_testplay_20260908,cav_20260905")
    ap.add_argument("--bands", default="0.25,0.5,1.0",
                    help="好手とみなす帯（コスト点。最良からこの差の中に入る枚数を数える）")
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)

    caps = [float(x) for x in a.caps.split(",")]
    cards = _boot()
    data = json.load(open(FIX, encoding="utf-8"))

    # 相手パネル: 在野24人から等間隔に（special48 と final_blind は使わない）
    ent = data["pools"]["official24"]["entries"]
    step = max(1, len(ent) // a.panel)
    panel = [(tuple(e["armies"][0]["cards"]), e["armies"][0]["formation"])
             for e in ent[::step]][:a.panel]

    bases = []
    for key in a.bases.split(","):
        arm = data["sets"][key]["armies"][0]
        bases.append((key, tuple(arm["cards"]), arm["formation"]))

    print("相手パネル {}組（在野24人から等間隔）／ 土台 {}組 ／ 上限 {}".format(
        len(panel), len(bases), "・".join("{:g}".format(c) for c in caps)))
    for k, n, f in bases:
        print("  土台 {:<26} {} 計{:g}点  {}".format(
            k, f, sum(cards[x].cost for x in n), " / ".join(n)))

    jobs, index = [], []
    # ① 雑音の見積り: 土台を種ちがいで5回
    for bi, (k, n, f) in enumerate(bases):
        for cap in caps:
            for r in range(5):
                index.append(("noise", bi, cap, r, None))
                jobs.append((n, f, cap, panel, a.seed + 1000 * r))
    # ② 土台そのもの、③ 全ての1枚差し替え
    for bi, (k, n, f) in enumerate(bases):
        for cap in caps:
            index.append(("base", bi, cap, None, None))
            jobs.append((n, f, cap, panel, a.seed))
            for slot in range(len(n)):
                for cand in candidates(n, slot, cap, cards):
                    if cand == n[slot]:
                        continue
                    seq = list(n)
                    seq[slot] = cand
                    fixed = arrange(seq, f)
                    if fixed is None:
                        continue
                    index.append(("swap", bi, cap, slot, cand))
                    jobs.append((fixed, f, cap, panel, a.seed))

    print("\n打つ編成 {:,} 通り × 相手{}組 = {:,}局".format(
        len(jobs), len(panel), len(jobs) * len(panel)))
    with Pool(a.jobs, initializer=_boot) as pool:
        vals = pool.map(_score, jobs, chunksize=8)

    res = {}
    for (kind, bi, cap, slot, cand), v in zip(index, vals):
        res.setdefault((bi, cap), {"noise": [], "base": None, "swap": {}})
        r = res[(bi, cap)]
        if kind == "noise":
            r["noise"].append(v)
        elif kind == "base":
            r["base"] = v
        else:
            r["swap"].setdefault(slot, []).append((cand, v))

    # 物差し: コスト1点が残存差でいくつか（§7.156 と同じ物差し）
    yard = F.cost_yardstick(DT)
    bands = [float(x) for x in a.bands.split(",")]
    noises = [statistics.pstdev(r["noise"]) for r in res.values() if len(r["noise"]) > 1]
    noise = statistics.fmean(noises) if noises else 0.0
    print("\n物差し: コスト1点 = 残存差 {:.4f}".format(yard))
    print("雑音（同じ編成・種ちがい5回のばらつき）: 残存差 {:.4f} = {:.2f} コスト点"
          .format(noise, noise / yard))

    out = {"yardstick": yard, "noise": noise, "bands": bands,
           "panel": len(panel), "caps": caps, "rows": []}
    rows = []
    for bi, (k, n, f) in enumerate(bases):
        for cap in caps:
            r = res[(bi, cap)]
            cur = r["base"]
            legal = better = 0
            good = {b: 0 for b in bands}
            names_in_band = {b: set() for b in bands}
            per_slot = []
            for slot in range(len(n)):
                sw = r["swap"].get(slot, [])
                best = max([v for _, v in sw] + [cur])
                lg = len(sw)
                bt = sum(1 for _, v in sw if v >= cur)
                legal += lg; better += bt
                gd = {}
                for b in bands:
                    hit = [nm for nm, v in sw if v >= best - b * yard]
                    gd[b] = len(hit)
                    good[b] += len(hit)
                    names_in_band[b].update(hit)
                per_slot.append({
                    "slot": slot, "card": n[slot], "cost": cards[n[slot]].cost,
                    "legal": lg, "better": bt,
                    "good": {str(b): gd[b] for b in bands},
                    "best": best, "base": cur,
                    "top": [(nm, round(v, 4)) for nm, v in
                            sorted(sw, key=lambda t: -t[1])[:6]]})
            row = {"key": k, "cap": cap, "cards": list(n), "formation": f,
                   "legal": legal, "better": better,
                   "good": {str(b): good[b] for b in bands},
                   "distinct": {str(b): len(names_in_band[b]) for b in bands},
                   "slots": per_slot}
            rows.append(row); out["rows"].append(row)

    bl = "・".join("{:g}".format(b) for b in bands)
    print("\n■ 1枚差し替えの幅（6枠の合計）  好手の帯 = 最良から {} コスト点以内\n".format(bl))
    head = "| 土台 | 上限 | 合法 | 元より良い | " + " | ".join(
        "好手 {:g}点".format(b) for b in bands) + " |"
    print(head)
    print("|---|---:|---:|---:|" + "---:|" * len(bands))
    for r in rows:
        print("| {} | {:g} | {} | {} | {} |".format(
            r["key"], r["cap"], r["legal"], r["better"],
            " | ".join(str(r["good"][str(b)]) for b in bands)))

    print("\n■ 枠ごとの好手の数（帯 {:g} コスト点）\n".format(bands[len(bands)//2]))
    mid = bands[len(bands)//2]
    for r in rows:
        print("  {} 上限{:g}:  ".format(r["key"], r["cap"]) + "  ".join(
            "{}({:g})={}".format(p["card"].split("〔")[0], p["cost"],
                                 p["good"][str(mid)]) for p in r["slots"]))

    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("\n控え: " + a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
