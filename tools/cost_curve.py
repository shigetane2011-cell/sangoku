# -*- coding: utf-8 -*-
"""札は「独り立ちして強い」のか「良い相棒と組んで強い」のか（帯 × 兵種）。

    python3 tools/cost_curve.py
    python3 tools/cost_curve.py --cap 20 --panel 24 --out X.json

きっかけ（テストプレイ）: 上限を上げたら兵種の3辺が動いた件について
「単にカタログオーバーの強い武将がコスト別・兵種別に偏ってるだけな可能性もあり、
その場合それを直す方が筋が良い」。あわせて体感の申し送り:

    馬の3-5弱い ／ 弓の3〜4は高コストと組まないと強くない（カードは強い）
    ／ 歩兵の2-4コストは独り立ちして強い

【なぜ one_ruler では足りないか】あちらは札を1枠に立たせて**1対1で**殴り合わせる。
「組んで強い」も「壁として線を保つ」も測っていない（計器自身が注記している）。
上の体感3つは全部その盲点の中にある。

【何を測るか】試す札を1枚だけ替え、**残り5枚の相棒の質を3段に振る**:

    相棒 1点ずつ（計5点）  … 埋め草だけの部隊。ここで強ければ**独り立ちして強い**
    相棒 2点ずつ（計10点）
    相棒 3点ずつ（計15点）… 良い相棒。ここでだけ強ければ**組んで強い**

同じ相手（在野）へ1組1局ずつ当て、札ごとに3つの点を出す。
**「独り立ち」＝相棒1点のときの点／「相棒への効き」＝相棒3点 − 相棒1点。**
上限20では相棒3点だと試す札は5点までしか入らないので、体感が指している帯
（騎3-5・弓3-4・歩2-4）はちょうど全部入る。

【行は分ける】前衛（歩・騎）と後衛（弓）は同じ枠に立てないので土台を別に組む。
兵種をまたいだ大小は読まない（one_ruler と同じ理由）。**同じ兵種の中の帯の差**と
**同じ札の相棒による差**だけを読む。

測れないもの: 兵法どうしの噛み合い（相棒は帯で選ぶだけで中身を選んでいない）・
実デッキの並び・宝物。
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
LEVELS = (1, 2, 3)
_C = None


def _boot():
    global _C
    if _C is None:
        F.TRAITS.clear(); R.load_traits_into_field(); R.load_skills_into_field()
        F.TRAITS_ON = True
        _C = {c.name: c for c in M._roster_cards()}
    return _C


def _score(job):
    names, cap, panel, seed0 = job
    cards = _boot()
    me = M.with_surplus(F.Army(tuple(cards[n] for n in names),
                               BC.FORM_BY_NAME["魚鱗"]), cap)
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
    person = {n: M.person_of(cards[n]) for n in cards}
    def cost(n): return int(float(g[n]["コスト"]))
    def typ(n): return g[n]["兵種"]
    def spear(n): return bool((g[n].get("槍") or "").strip())

    # 相棒（帯ごと）。前衛に置けるのは歩・騎、後衛に置けるのは弓と槍持ち歩兵。
    mates = {}
    for L in LEVELS:
        front = sorted(n for n in cards if cost(n) == L and typ(n) in ("歩兵", "騎兵"))
        rear = sorted(n for n in cards if cost(n) == L
                      and (typ(n) == "弓兵" or (typ(n) == "歩兵" and spear(n))))
        mates[L] = (front, rear)
        assert len(front) >= 4 and len(rear) >= 4, (L, len(front), len(rear))

    jobs, index = [], []
    for n in sorted(cards):
        t = typ(n)
        if t not in ("歩兵", "騎兵", "弓兵"):
            continue
        head = t in ("歩兵", "騎兵")          # 前衛で試すか後衛で試すか
        for L in LEVELS:
            fr, re = mates[L]
            # **前衛を先に取り、後衛はそこで使った人を除いて取る。**
            # 槍持ちの歩兵は前衛にも後衛にも置けるので、両方の並びの先頭に
            # 同じ人が来ることがある（3点の傅僉で実際に起きた）。素朴に
            # `[:2]` `[:3]` と切ると同じ人物が2枚になり、その帯が丸ごと
            # 落ちる（相棒3点の段が1枚しか残らなかった）。
            pick_f = [x for x in fr if person[x] != person[n]]
            taken = {person[n]}
            front = []
            for x in pick_f:
                if person[x] in taken:
                    continue
                front.append(x); taken.add(person[x])
                if len(front) >= (2 if head else 3):
                    break
            rear = []
            for x in re:
                if person[x] in taken:
                    continue
                rear.append(x); taken.add(person[x])
                if len(rear) >= (3 if head else 2):
                    break
            seq = ([n] + front + rear) if head else (front + rear + [n])
            if len(seq) != 6 or len(set(person[x] for x in seq)) != 6:
                continue
            army = F.Army(tuple(cards[x] for x in seq), BC.FORM_BY_NAME["魚鱗"])
            if M.placement_errors(army) or army.total_cost() > cap + 1e-9:
                continue
            index.append((n, t, cost(n), L))
            jobs.append((tuple(seq), cap, panel, a.seed))

    print("上限 {:g} ／ 相手 {}組 ／ {:,} 通り = {:,}局".format(
        cap, len(panel), len(jobs), len(jobs) * len(panel)))
    with Pool(a.jobs, initializer=_boot) as pool:
        vals = pool.map(_score, jobs, chunksize=4)

    yard = F.cost_yardstick(DT)
    sc = {}
    for (n, t, c, L), v in zip(index, vals):
        sc[(n, L)] = v
    rows = []
    for n in sorted(set(k[0] for k in sc)):
        t, c = typ(n), cost(n)
        r = {"name": n, "typ": t, "cost": c,
             **{"L{}".format(L): sc.get((n, L)) for L in LEVELS}}
        if r["L1"] is not None and r["L3"] is not None:
            r["need"] = (r["L3"] - r["L1"]) / yard
        rows.append(r)

    def cell(t, lo, hi, key):
        v = [r[key] for r in rows if r["typ"] == t and lo <= r["cost"] <= hi
             and r.get(key) is not None]
        return (statistics.median(v), len(v)) if v else (None, 0)

    BANDS = [(1, 2), (3, 5), (6, 8), (9, 10)]
    print("\n■ 独り立ち（相棒が1点ずつのときの残存差・中央値）"
          "  ＋ほど埋め草だけの部隊でも強い\n")
    print("| 兵種 | " + " | ".join("{}-{}点".format(*b) for b in BANDS) + " |")
    print("|---|" + "---:|" * len(BANDS))
    for t in ("歩兵", "騎兵", "弓兵"):
        cs = []
        for lo, hi in BANDS:
            m, k = cell(t, lo, hi, "L1")
            cs.append("—" if m is None else "{:+.3f}({})".format(m, k))
        print("| {} | {} |".format(t, " | ".join(cs)))

    print("\n■ 相棒への効き（相棒3点 − 相棒1点。**コスト点**）"
          "  大きいほど「良い相棒と組まないと強くない」\n")
    print("| 兵種 | " + " | ".join("{}-{}点".format(*b) for b in BANDS) + " |")
    print("|---|" + "---:|" * len(BANDS))
    for t in ("歩兵", "騎兵", "弓兵"):
        cs = []
        for lo, hi in BANDS:
            m, k = cell(t, lo, hi, "need")
            cs.append("—" if m is None else "{:+.2f}({})".format(m, k))
        print("| {} | {} |".format(t, " | ".join(cs)))
    print("\n（かっこ内は枚数。上限{:g}では相棒3点のとき試す札は5点までしか"
          "入らないので、6点以上は「相棒への効き」を出せない）".format(cap))

    if a.out:
        json.dump({"cap": cap, "yardstick": yard, "panel": len(panel),
                   "levels": list(LEVELS), "rows": rows},
                  open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("控え: " + a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
