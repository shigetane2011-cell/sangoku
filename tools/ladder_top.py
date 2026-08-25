# -*- coding: utf-8 -*-
"""在野ラダーの上位を測る（§7.103）。

    python3 tools/ladder_top.py field      # 24人の総当たり（勝率と重なり）
    python3 tools/ladder_top.py cand       # 候補の型を場へぶつける
    python3 tools/ladder_top.py cand -k 突撃

なぜ要るか。§7.102 で在野24人を総当たりしたら、**上位3枠が強弓01-03 で
占められ**、しかも 強弓02 と 強弓03 は3陣地とも**6枚まるごと同じデッキ**
だった。「上位がバラける」を目で確かめるのは無理なので、次の2つを一緒に
測る計器を置く:

    強さ  … 24人の場に対する勝率（帯ごと・全体）
    多様さ… 上位の面々のデッキがどれだけ重なっているか（6枚中の共通数）

**強いだけの型は足さない。** 上位に入っても既存の上位と同じ札を並べるなら、
段は増えても登る味は増えない。逆に、バラけていても勝率が場の平均どまりなら
「上位」ではない。両方を見て初めて採否が決まる。

**場は固定して比べる。** 候補を測るときの相手は常に「今の24人」であって、
候補どうしを当てない。相手が変わると勝率の意味が変わる（§13: 基線は
測り直してから比べる）。候補は場に**入れずに**測り、採用した後で改めて
場ごと測り直す。
"""
import argparse
import itertools
import os
import statistics
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import dummies as D          # noqa: E402
from sim import field as F            # noqa: E402
from sim import match as M            # noqa: E402

SEEDS = 3
MEMBERS = 3          # 1性格あたりの人数（在野は8性格×3＝24）


# ---------------------------------------------------------------------------
# 候補の型。**ここは実験台**で、採用したものだけ sim/dummies.py へ移す。
# ---------------------------------------------------------------------------
CANDIDATES = (
    # 騎兵で前を厚く、後ろは最低限。**壁の耐久にこだわらない**（そもそも
    # 騎兵に耐久役は1枚も無い）。強弓の裏返しに当たる型。
    D.Persona("突騎", {F.CAV: 2.4, F.INF: 0.8, F.ARC: 0.5},
              {F.DPS: 2.4, F.BURST: 2.2, F.BAL: 1.0, F.TANK: 0.6, F.SUP: 0.3},
              "鶴翼", 0.7, rear_share=0.28, wall_tank=1.0),
    # 壁2枚で受けて弓4枚を積む。強弓（壁3・弓3）をさらに後ろへ振った型。
    D.Persona("連弩", {F.ARC: 2.4, F.INF: 1.2, F.CAV: 0.4},
              {F.DPS: 2.4, F.BURST: 2.0, F.BAL: 1.0, F.SUP: 0.8, F.TANK: 0.5},
              "雁行", 0.6, rear_share=0.62, wall_tank=1.6),
    # 高コストへ極端に寄せる（手練れの器を使わない）。少数精鋭が通るか。
    D.Persona("精鋭", {F.INF: 1.2, F.CAV: 1.2, F.ARC: 1.2},
              {F.DPS: 1.6, F.TANK: 1.4, F.BAL: 1.2, F.BURST: 1.2, F.SUP: 0.8},
              "魚鱗", 0.95),
    # 薄く広く（手練れの器を使わない）。安い札を並べて枚数で受ける。
    D.Persona("雑兵", {F.INF: 1.4, F.CAV: 1.1, F.ARC: 1.0},
              {F.TANK: 1.6, F.BAL: 1.6, F.SUP: 1.2, F.DPS: 1.0, F.BURST: 0.6},
              "鶴翼", 0.05),
)


def _cards():
    return M._roster_cards()


def field_entries(cards):
    """今の在野24人。**DB を読まない**（名前の規則から組み直せる）。"""
    out = []
    for i, p in enumerate(D.PERSONAS):
        for num in range(1, MEMBERS + 1):
            out.append(("{}{:02d}".format(p.name, num),
                        D.make_entry(cards, p, D.deck_seed(i, num))))
    return out


def _duel(job):
    (na, ea), (nb, eb), reg, seed = job
    return (na, nb, M.play_one(ea, eb, reg, dt=0.5, seed=seed)["diff"])


def _tally(results, names):
    win, games = Counter(), Counter()
    for na, nb, diff in results:
        games[na] += 1
        games[nb] += 1
        if diff > 0:
            win[na] += 1
        elif diff < 0:
            win[nb] += 1
    return {n: 100.0 * win[n] / games[n] for n in names if games[n]}


def overlap(entries):
    """デッキの重なり。**6枚中いくつ同じか**を全ペアで見る。"""
    out = {}
    for (na, ea), (nb, eb) in itertools.combinations(entries, 2):
        same = [len(set(c.name for c in ea.units[i].cards)
                    & set(c.name for c in eb.units[i].cards))
                for i in range(len(M.REGULATIONS))]
        out[(na, nb)] = same
    return out


def shape(entry):
    """デッキの姿を一行で。前衛の割合・兵種・平均コスト。"""
    rows = []
    for i, (_lab, cap) in enumerate(M.REGULATIONS):
        a = entry.units[i]
        nf = a.form.n_front
        front = sum(c.cost for c in a.cards[:nf])
        typ = Counter(F.TYPE_JP[c.typ] for c in a.cards)
        rows.append((front / max(sum(c.cost for c in a.cards), 1e-9),
                     typ, max(c.cost for c in a.cards)))
    return rows


def cmd_field(args):
    cards = _cards()
    ents = field_entries(cards)
    names = [n for n, _ in ents]
    jobs = [(a, b, reg, sd)
            for a, b in itertools.combinations(ents, 2)
            for reg in range(len(M.REGULATIONS))
            for sd in range(SEEDS)]
    print("測る: {}人の総当たり {}局".format(len(ents), len(jobs)))
    res = Pool(args.jobs).map(_duel, jobs, chunksize=32)
    rate = _tally(res, names)

    print("\n── 勝率 ──")
    order = sorted(names, key=lambda n: -rate[n])
    for n in order:
        print("  {:8s} {:5.1f}%".format(n, rate[n]))
    vals = sorted(rate.values())
    print("  幅 {:.1f}  標準偏差 {:.1f}".format(vals[-1] - vals[0],
                                             statistics.pstdev(vals)))

    top = order[:args.top]
    print("\n── 上位{}人の顔ぶれ ──".format(args.top))
    kinds = Counter(n[:2] for n in top)
    print("  型: " + "  ".join("{}×{}".format(k, v) for k, v in kinds.items()))
    ov = overlap([(n, e) for n, e in ents if n in top])
    print("  デッキの重なり（3陣地それぞれ、6枚中）:")
    for (a, b), same in sorted(ov.items()):
        mark = "  ← 同一" if all(x == M.UNIT_SIZE for x in same) else ""
        print("    {:8s} × {:8s}  {}{}".format(a, b, same, mark))
    dup = sum(1 for s in ov.values() if all(x == M.UNIT_SIZE for x in s))
    print("  丸ごと同じ組: {} / {}".format(dup, len(ov)))

    archetype_matrix(res, names)
    return 0


def archetype_matrix(results, names):
    """**型どうしの総当たり。** 順位表ではなく相性の表を出す。

    ラダーの勝率だけ見ていると「上位が同じ型で埋まる」を、稽古台の人選の
    話だと思ってしまう。だが**1つの型が他の全部に勝ち越しているなら、
    それは稽古台ではなく値付けの問題である**（型を足しても最強が1つなのは
    変わらない）。三すくみが有るか無いかを、ここで直接見る。
    """
    kinds = sorted({n[:2] for n in names})
    win = defaultdict(Counter)      # win[A][B] = A が B に勝った数
    tot = defaultdict(Counter)
    for na, nb, diff in results:
        a, b = na[:2], nb[:2]
        if a == b:
            continue
        tot[a][b] += 1
        tot[b][a] += 1
        if diff > 0:
            win[a][b] += 1
        elif diff < 0:
            win[b][a] += 1

    print("\n── 型どうしの勝率（行が列に勝つ%）──")
    print("        " + "".join("{:>7s}".format(k) for k in kinds))
    for a in kinds:
        row = ["{:>7s}".format("--" if a == b else
                               "{:.0f}".format(100.0 * win[a][b] / tot[a][b]))
               for b in kinds]
        beat = sum(1 for b in kinds
                   if a != b and win[a][b] > tot[a][b] - win[a][b])
        print("  {:6s}".format(a) + "".join(row)
              + "   勝ち越し {}/{}".format(beat, len(kinds) - 1))

    top = [a for a in kinds
           if all(a == b or win[a][b] * 2 > tot[a][b] for b in kinds)]
    print("\n  **全ての型に勝ち越している型: {}**".format(
        "、".join(top) if top else "無し（三すくみが成立している）"))
    if top:
        print("  → 稽古台の人選ではなく**値付けの問題**。型を足しても最強は"
              "1つのまま。")
    return top


def cmd_cand(args):
    """候補を**場へ入れずに**測る。相手は常に今の24人。"""
    cards = _cards()
    field = field_entries(cards)
    picks = [p for p in CANDIDATES if not args.kind or p.name in args.kind]
    if not picks:
        print("その名前の候補は無い:", args.kind)
        return 1

    # 基線: 今の在野が同じ場に対して出す勝率（自分自身とは当てない）
    print("測る: 候補{} + 在野24 それぞれ × 相手24 × 3陣地 × 種{}".format(
        len(picks) * MEMBERS, SEEDS))
    trial = []
    for p in picks:
        for num in range(1, MEMBERS + 1):
            seed = D.deck_seed(len(D.PERSONAS) + CANDIDATES.index(p), num)
            trial.append(("{}{:02d}".format(p.name, num),
                          D.make_entry(cards, p, seed)))

    jobs = [(t, f, reg, sd)
            for t in trial + field
            for f in field
            for reg in range(len(M.REGULATIONS))
            for sd in range(SEEDS)
            if t[0] != f[0]]
    res = Pool(args.jobs).map(_duel, jobs, chunksize=32)

    # 挑戦側だけの勝率（場の側は相手として何度も出るので混ぜない）
    win, games = Counter(), Counter()
    for na, nb, diff in res:
        win[na] += 1 if diff > 0 else 0
        games[na] += 1
    rate = {n: 100.0 * win[n] / games[n] for n in games}

    field_names = [n for n, _ in field]
    base = sorted((rate[n] for n in field_names), reverse=True)
    print("\n── 場の基線（在野24人が同じ場へ出した勝率）──")
    print("  最高 {:.1f}%  上位3 {:.1f}%  中央 {:.1f}%  最低 {:.1f}%".format(
        base[0], statistics.mean(base[:3]), statistics.median(base), base[-1]))

    print("\n── 候補 ──")
    idx = dict(field)
    for name, e in trial:
        near = sorted(field_names,
                      key=lambda f: -max(len(set(c.name for c in e.units[i].cards)
                                             & set(c.name for c in idx[f].units[i].cards))
                                         for i in range(len(M.REGULATIONS))))[:1]
        same = [len(set(c.name for c in e.units[i].cards)
                    & set(c.name for c in idx[near[0]].units[i].cards))
                for i in range(len(M.REGULATIONS))]
        sh = shape(e)
        print("  {:8s} 勝率 {:5.1f}%   前衛の割合 {}   最高コスト {}".format(
            name, rate[name],
            "/".join("{:.0f}%".format(100 * r[0]) for r in sh),
            "/".join(str(int(r[2])) for r in sh)))
        print("           兵種 {}   最も似た在野 {} と {}枚重なり".format(
            "/".join("".join("{}{}".format(k, v) for k, v in sorted(r[1].items()))
                     for r in sh), near[0], same))
    return 0


def cmd_meta(args):
    """手練れの引きの尖り（META_POW）を掃引する。

    **場は動かさない。** 相手は META_POW に依らない6性格18人に固定する
    （手練れを相手に混ぜると、つまみを回すたび場も動いて比べられない）。
    """
    cards = _cards()
    field = [("{}{:02d}".format(p.name, num), D.make_entry(cards, p, D.deck_seed(i, num)))
             for i, p in enumerate(D.PERSONAS) if p.name not in D.META_PERSONAS
             for num in range(1, MEMBERS + 1)]
    print("場（固定）: {}人 — {}".format(
        len(field), " ".join(sorted({n[:2] for n, _ in field}))))
    was = D.META_POW
    try:
        for pow_ in args.pow:
            D.META_POW = pow_
            trial = [("{}{:02d}".format(p.name, num),
                      D.make_entry(cards, p, D.deck_seed(i, num)))
                     for i, p in enumerate(D.PERSONAS) if p.name in D.META_PERSONAS
                     for num in range(1, MEMBERS + 1)]
            jobs = [(t, f, reg, sd) for t in trial for f in field
                    for reg in range(len(M.REGULATIONS)) for sd in range(SEEDS)]
            res = Pool(args.jobs).map(_duel, jobs, chunksize=32)
            win, games = Counter(), Counter()
            for na, _nb, diff in res:
                win[na] += 1 if diff > 0 else 0
                games[na] += 1
            ov = overlap(trial)
            same = [x for k, v in ov.items() if k[0][:2] == k[1][:2] for x in v]
            print("\nMETA_POW = {:.1f}   （{}局）".format(pow_, len(jobs)))
            for n, _e in trial:
                print("   {:8s} {:5.1f}%".format(n, 100.0 * win[n] / games[n]))
            print("   同じ型の3人の重なり: 平均 {:.1f}/6  最大 {}  丸ごと同じ {}組"
                  .format(statistics.mean(same), max(same),
                          sum(1 for k, v in ov.items()
                              if k[0][:2] == k[1][:2] and all(x == M.UNIT_SIZE for x in v))))
            sys.stdout.flush()
    finally:
        D.META_POW = was
    return 0


def main():
    ap = argparse.ArgumentParser(description="在野ラダーの上位を測る")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("field", help="24人の総当たり")
    s.add_argument("--top", type=int, default=5)
    s.set_defaults(fn=cmd_field)
    s = sub.add_parser("meta", help="手練れの引きの尖りを掃引する")
    s.add_argument("--pow", type=float, action="append", default=[])
    s.set_defaults(fn=cmd_meta)
    s = sub.add_parser("cand", help="候補の型を場へぶつける")
    s.add_argument("-k", "--kind", action="append", default=[])
    s.set_defaults(fn=cmd_cand)
    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()
