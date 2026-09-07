# -*- coding: utf-8 -*-
"""兵法（または身体）の値打ちを**本番 BO3 の登録の中で**測る（§7.184）。

`skill_panel.py` は「兵法あり−なしの段差」を「その札の1コスト点の傾き」で割る。
安くて全札に回せるが、割り算の分母が**その札のその土台での傾き**なので、札ごとに
2.9倍ちがう物差しで割ったコスト点が並ぶ。この道具は割り算をやめ、**実際に身体を
動かして**測る。官渡・赤壁・汜水関の登録（fixtures の official24）の中で、
その札が入っている部隊だけを読む。

二つの向きがある。**同じ兵法でも別の数が出る**（兵法と身体は足し算でなく掛け算）。

  --trade  a,b,c   **値付けの向き**。兵法を持ったまま身体を a,b,c 点削る。
                   名簿はこの向きで払わせている（効果予算を引いた残りが身体）ので、
                   値札を決めるならこちら。値打ち ＝ 段差 ÷ 実測の傾き。
  --ladder a,b,c   **置き換えの向き**。兵法を外して身体を a,b,c 点足し、元と同じ
                   残存差に戻る点を探す。「この兵法は身体で何点ぶんか」の答え。
                   届かなければ「身体では買えない」と出る（それも答え）。

    python3 tools/skill_value.py 諸葛亮〔臥龍〕 --trade 2,4,7
    python3 tools/skill_value.py 陸遜〔夷陵〕 --ladder 0,2,4,6,8 --out /tmp/lx

**天井と床に気をつける**: 登録の勝率が5割から離れていると残存差が詰まり、身体を
足しても引いても動かなくなる（実測: BO3 87.5% の登録では 1点あたり 0.004 しか
動かず、5割近くの登録の 0.013 の3分の1以下）。この道具は基準の勝率が 35〜65% の
外なら警告を出す。読むときは警告の有無を必ず見ること。
"""
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sim import bo3meter as B      # noqa: E402
from sim import design as DS       # noqa: E402
from sim import rosterdata as R    # noqa: E402
import balance_common as C         # noqa: E402

SEEDS = [0, 1, 2]
DT = 0.5
SAFE = (0.35, 0.65)     # 基準の勝率がこの外なら天井/床の警告


def move_body(card, points):
    """身体を points コスト点ぶん動かす（正＝削る・負＝足す）。

    `skill_panel._minus_one` と同じ作り: 効果に points 点多く払った設計から
    武力・知力を引き直し、stat_cost を points 下げる。**表示コストは据え置く**
    ので登録の合計コストは変わらず、本番の検証を通る。効果予算の上限
    （EFFECT_CAP）は外して引く — 当たっている札は上限を効かせると身体が動かない。
    """
    if not points:
        return card, None
    g = {r["名前"]: r for r in R.generals()}[card.name]
    d = R.to_design(g)
    d2 = DS.Design(**{**d.__dict__, "effect": (d.cost - card.stat_cost) + points})
    cap = DS.EFFECT_CAP
    DS.EFFECT_CAP = 99.0
    try:
        v = DS.derive(d2)
    finally:
        DS.EFFECT_CAP = cap
    return replace(card, stat_cost=card.stat_cost - points,
                   might=round(v["武力"], 1), wits=round(v["知力"], 1)), v


def _read(reps, specs, target):
    """その札が入っている部隊だけの残存差・勝率と、登録ぜんたいの BO3 勝率。"""
    diffs, wins, games, sw, sn = [], 0, 0, 0, 0
    for spec in specs:
        rep = reps[spec["name"]]
        for i, a in enumerate(spec["armies"]):
            if target in a["cards"]:
                r = rep["by_regulation"][i]
                diffs.append(r["mean_diff"]); wins += r["wins"]; games += r["games"]
        sw += rep["bo3"]["wins"]; sn += rep["bo3"]["series"]
    return sum(diffs) / len(diffs), wins / games, sw / sn


def _arm(target, skill_on, points, out, specs, opponents, idx0, seeds, dt, jobs_n):
    idx = dict(idx0)
    c = idx[target]
    if not skill_on:
        c = replace(c, skill="")
    c, v = move_body(c, points)
    idx[target] = c
    reps = {}
    for spec in specs:
        p = out / (spec["name"] + ".json") if out else None
        if p is not None and p.exists():
            reps[spec["name"]] = json.load(open(p, encoding="utf-8"))
            continue
        rep = B.measure(C.entry_from_spec(spec, idx), opponents, seeds, dt=dt,
                        jobs_n=jobs_n, name=spec["name"])
        if p is not None:
            C.write_json(p, rep)
        reps[spec["name"]] = rep
    return reps, v


def main():
    argv = sys.argv[1:]
    def val(flag, dflt=None):
        return argv[argv.index(flag) + 1] if flag in argv else dflt
    trade = [float(s) for s in val("--trade", "").split(",") if s]
    ladder = [float(s) for s in val("--ladder", "").split(",") if s]
    seeds = [int(s) for s in val("--seeds", "0,1,2").split(",")]
    dt = float(val("--dt", DT))
    jobs_n = int(os.environ.get("JOBS", "4"))
    out = Path(val("--out")) if "--out" in argv else None
    skip = {i + 1 for i, a in enumerate(argv) if a in ("--trade", "--ladder", "--seeds", "--dt", "--out")}
    names = [a for i, a in enumerate(argv) if i not in skip and not a.startswith("--")]
    if len(names) != 1 or not (trade or ladder):
        print(__doc__)
        return 2
    target = names[0]
    if not trade and not ladder:
        trade = [2.0, 4.0, 7.0]

    data = C.load_fixtures()
    idx0 = C.card_index(C.roster())
    if target not in idx0:
        raise SystemExit("名簿にない札: {}".format(target))
    specs, opps = [], []
    for spec in data["pools"]["official24"]["entries"]:
        names_in = [n for a in spec["armies"] for n in a["cards"]]
        (specs if target in names_in else opps).append(spec)
    if not specs:
        raise SystemExit("{} を含む登録が official24 に無い".format(target))
    opponents = [(s["name"], C.entry_from_spec(s, idx0)) for s in opps]
    plays = sum(1 for s in specs for a in s["armies"] if target in a["cards"])
    print("{}: 候補 {} 登録／相手 {} 登録／その札の部隊 {} 局・seeds {}・dt {:g}".format(
        target, len(specs), len(opponents), plays * len(opponents) * len(seeds) * 2, seeds, dt), flush=True)

    def arm(on, pts, tag):
        reps, v = _arm(target, on, pts, (out / tag) if out else None, specs,
                       opponents, idx0, seeds, dt, jobs_n)
        return _read(reps, specs, target), v

    (bd, bw, bs), _ = arm(True, 0.0, "base")
    warn = "" if SAFE[0] <= bs <= SAFE[1] else "  ← **天井/床の警告**: 登録の勝率が {:.0%}〜{:.0%} の外".format(*SAFE)
    print("基準（兵法あり・素の身体）: 残存差 {:+.4f}／その部隊の勝率 {:.1%}／BO3 {:.1%}{}".format(
        bd, bw, bs, warn), flush=True)
    (od, ow, os_), _ = arm(False, 0.0, "off0")
    gap = bd - od
    print("兵法なし（同じ身体）: 残存差 {:+.4f}／その部隊の勝率 {:.1%}／BO3 {:.1%}／**段差 {:+.4f}**".format(
        od, ow, os_, gap), flush=True)

    if trade:
        print()
        print("── 値付けの向き（兵法を持ったまま身体を削る）")
        print("{:>10}{:>10}{:>12}{:>12}{:>12}{:>10}".format("身体 -点", "兵力", "残存差", "基準との差", "1点あたり", "値打ち"))
        for x in trade:
            (d, w, s), v = arm(True, x, "cut{:g}".format(x))
            slope = (bd - d) / x
            print("{:>10.2f}{:>10,.0f}{:>+12.4f}{:>+12.4f}{:>12.4f}{:>10.2f}".format(
                x, v["兵力"], d, d - bd, slope, gap / slope if slope else float("nan")), flush=True)
        print("  値打ち = 段差 ÷ その削り幅の傾き。**この列が値札に使う数**。")
    if ladder:
        print()
        print("── 置き換えの向き（兵法を外して身体で買い戻す）")
        print("{:>10}{:>10}{:>12}{:>12}{:>10}".format("身体 +点", "兵力", "残存差", "基準との差", "BO3"))
        pts = []
        for x in ladder:
            (d, w, s), v = arm(False, -x, "add{:g}".format(x))
            pts.append((x, d))
            print("{:>10.2f}{:>10,.0f}{:>+12.4f}{:>+12.4f}{:>10.1%}".format(
                x, v["兵力"] if v else 0.0, d, d - bd, s), flush=True)
        star = None
        for (x0, d0), (x1, d1) in zip(pts, pts[1:]):
            if (d0 - bd) <= 0 < (d1 - bd):
                star = x0 + (x1 - x0) * (bd - d0) / (d1 - d0)
                break
        if star is None:
            print("  → 梯子の端（+{:g}点）でも釣り合わない（{:+.4f}）。身体では買い戻せない。".format(
                pts[-1][0], pts[-1][1] - bd))
        else:
            print("  → 釣り合う身体の量 **{:.2f} コスト点**".format(star))
    print("SKILL VALUE DONE")


if __name__ == "__main__":
    sys.exit(main() or 0)
