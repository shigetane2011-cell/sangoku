# -*- coding: utf-8 -*-
"""コスト1点が買える強さは、帯によって同じか（兵種ごと）。

    python3 tools/cost_curve.py
    python3 tools/cost_curve.py --cap 20 --panel 24

きっかけ（テストプレイ）: 上限を上げたら兵種の3辺が動いた件について
「単にカタログオーバーの強い武将がコスト別・兵種別に偏ってるだけな可能性もあり、
その場合それを直す方が筋が良い」。

【何を測るか】土台の5枚を固定し、**残り1枠へ名簿の全札を順に入れて**同じ相手へ当てる。
横軸をその札のコスト、縦軸を残存差にすると、**同じ兵種の中でコスト1点がいくら買うか**の
曲線が出る。曲線がまっすぐなら帯ごとの偏りは無い。折れていれば、折れているところの札が
割安（または割高）である。

【行は分ける】前衛（歩・騎）と後衛（弓）は同じ枠に立てないので**別の土台・別の曲線**にする
（one_ruler と同じ理由。混ぜると兵種ぐるみの歪みが曲線の折れに化ける）。

【one_ruler との違い】one_ruler は各札を**同コストの合成札**と比べるので、合成側の
コスト曲線そのものが曲がっていると差が 0 に見えてしまう。ここは**実測の札どうしを
同じ枠で並べる**ので、曲線の形が直接出る。

測れないもの: 束ねたときの働き（壁として線を保つ・兵法の噛み合い）。1枠ぶんの差だけ。
"""
import argparse, json, os, statistics, sys
from collections import defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import field as F          # noqa: E402
from sim import match as M          # noqa: E402
from sim import rosterdata as R     # noqa: E402
import tools.balance_common as BC   # noqa: E402

DT = 0.5
FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "docs", "balance", "fixtures-v1.json")
_C = None


def _boot():
    global _C
    if _C is None:
        F.TRAITS.clear(); R.load_traits_into_field(); R.load_skills_into_field()
        F.TRAITS_ON = True
        _C = {c.name: c for c in M._roster_cards()}
    return _C


def _score(job):
    names, form, cap, panel, seed0 = job
    cards = _boot()
    me = M.with_surplus(F.Army(tuple(cards[n] for n in names),
                               BC.FORM_BY_NAME[form]), cap)
    out = []
    for k, (fn, ff) in enumerate(panel):
        foe = M.with_surplus(F.Army(tuple(cards[n] for n in fn),
                                    BC.FORM_BY_NAME[ff]), cap)
        out.append(F.simulate(me, foe, DT, seed=seed0 + k)["diff"])
    return statistics.fmean(out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=0.0, help="0 なら汜水関の上限")
    ap.add_argument("--panel", type=int, default=24)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--out", default="")
    a = ap.parse_args(argv)
    cards = _boot()
    cap = a.cap or dict(M.REGULATIONS)["汜水関"]
    data = json.load(open(FIX, encoding="utf-8"))
    ent = data["pools"]["official24"]["entries"]
    step = max(1, len(ent) // a.panel)
    panel = [(tuple(e["armies"][0]["cards"]), e["armies"][0]["formation"])
             for e in ent[::step]][:a.panel]

    g = {x["名前"]: x for x in R.generals()}
    def typ(n): return g[n]["兵種"]
    def spear(n): return bool((g[n].get("槍") or "").strip())
    # 土台5枚（どれも2点・魚鱗 前3/後3）。試す枠は前衛の1枠 or 後衛の1枠。
    # 前衛の土台: 歩2枚 + 後衛の弓3枚 ／ 後衛の土台: 前衛の歩3枚 + 弓2枚
    two = [n for n in cards if int(g[n]["コスト"]) == 2]
    inf2 = [n for n in two if typ(n) == "歩兵" and not spear(n)][:3]
    arc2 = [n for n in two if typ(n) == "弓兵"][:3]
    assert len(inf2) >= 3 and len(arc2) >= 3, (len(inf2), len(arc2))
    FRONT_BASE = inf2[:2] + arc2[:3]     # 試す枠は前衛の先頭
    REAR_BASE = inf2[:3] + arc2[:2]      # 試す枠は後衛の末尾

    jobs, index = [], []
    for n in sorted(cards):
        t = typ(n)
        if t in ("歩兵", "騎兵"):
            seq = [n] + FRONT_BASE; row = "前衛"
        elif t == "弓兵":
            seq = REAR_BASE + [n]; row = "後衛"
        else:
            continue
        if n in seq[1:] if row == "前衛" else n in seq[:-1]:
            continue
        army = F.Army(tuple(cards[x] for x in seq), BC.FORM_BY_NAME["魚鱗"])
        if M.placement_errors(army):
            continue
        if army.total_cost() > cap + 1e-9:
            continue
        index.append((n, t, row, int(g[n]["コスト"])))
        jobs.append((tuple(seq), "魚鱗", cap, panel, a.seed))

    print("上限 {:g} ／ 相手 {}組 ／ 測る札 {} 枚 = {:,}局".format(
        cap, len(panel), len(jobs), len(jobs) * len(panel)))
    print("前衛の土台: {}".format(" ".join(x.split("〔")[0] for x in FRONT_BASE)))
    print("後衛の土台: {}".format(" ".join(x.split("〔")[0] for x in REAR_BASE)))
    with Pool(a.jobs, initializer=_boot) as pool:
        vals = pool.map(_score, jobs, chunksize=4)

    yard = F.cost_yardstick(DT)
    by = defaultdict(list)
    rows = []
    for (n, t, row, c), v in zip(index, vals):
        by[(t, c)].append(v)
        rows.append({"name": n, "typ": t, "row": row, "cost": c, "score": v})
    print("\n■ 兵種ごとの「コスト k の札を1枚入れたときの残存差」中央値"
          "（コスト1点 = 残存差 {:.4f}）\n".format(yard))
    print("| 兵種 | " + " | ".join("{}点".format(k) for k in range(1, 11)) + " |")
    print("|---|" + "---:|" * 10)
    med = {}
    for t in ("歩兵", "騎兵", "弓兵"):
        cells = []
        for k in range(1, 11):
            v = by.get((t, k), [])
            m = statistics.median(v) if v else None
            med[(t, k)] = m
            cells.append("—" if m is None else "{:+.3f}".format(m))
        print("| {} | {} |".format(t, " | ".join(cells)))

    print("\n■ 1点あたりいくら買えるか（隣の帯との差 ÷ 1点。**コスト点**に直した）\n")
    print("| 兵種 | " + " | ".join("{}→{}".format(k, k + 1) for k in range(1, 10)) + " |")
    print("|---|" + "---:|" * 9)
    for t in ("歩兵", "騎兵", "弓兵"):
        cells = []
        for k in range(1, 10):
            a1, b1 = med[(t, k)], med[(t, k + 1)]
            cells.append("—" if a1 is None or b1 is None
                         else "{:+.2f}".format((b1 - a1) / yard))
        print("| {} | {} |".format(t, " | ".join(cells)))
    print("\n1.00 なら「1点ぶんきっちり」。**1より大きい帯は割安（お買い得）**、"
          "小さい帯は割高。")

    if a.out:
        json.dump({"cap": cap, "yardstick": yard, "panel": len(panel), "rows": rows},
                  open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n控え: " + a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
