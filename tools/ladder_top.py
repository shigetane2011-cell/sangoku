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


def archetype_matrix(results, names, key=lambda n: n[:2]):
    """**型どうしの総当たり。** 順位表ではなく相性の表を出す。

    ラダーの勝率だけ見ていると「上位が同じ型で埋まる」を、稽古台の人選の
    話だと思ってしまう。だが**1つの型が他の全部に勝ち越しているなら、
    それは稽古台ではなく値付けの問題である**（型を足しても最強が1つなのは
    変わらない）。三すくみが有るか無いかを、ここで直接見る。
    """
    kinds = sorted({key(n) for n in names})
    win = defaultdict(Counter)      # win[A][B] = A が B に勝った数
    tot = defaultdict(Counter)
    for na, nb, diff in results:
        a, b = key(na), key(nb)
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


# 形の実験（§7.104）。**器を1つに固定して、形だけを変える。**
# 型どうしの相性表（field）は強弓が7型すべてに勝ち越すと出たが、強弓と重装
# だけが手練れの器（_meta_entry）で組まれ、他の6性格は好みの器で組まれる。
# あの表は「形の強さ」と「組み方の巧さ」を混ぜて測っている。ここでは全部を
# 手練れの器で組み、**陣形（前衛の枚数）と後衛へ回す予算だけ**を振る。
SHARES = (0.22, 0.34, 0.46, 0.58, 0.70)
FORM_MARK = {"鶴翼": "鶴", "魚鱗": "魚", "雁行": "雁"}


def shapes(shares=SHARES):
    """**どの陣形も同じ範囲で振る。** 陣形ごとに違う範囲を当てると、
    「その陣形が弱い」のか「その陣形に合わない配分しか試していない」のかが
    分けられない（初版は雁行だけ後ろ寄りの範囲しか見ていなかった）。
    """
    return tuple(
        D.Persona("{}{:02.0f}".format(FORM_MARK[form], share * 100),
                  {F.ARC: 1.0, F.INF: 1.0, F.CAV: 1.0},
                  {F.DPS: 1.0, F.BURST: 1.0, F.BAL: 1.0, F.SUP: 1.0, F.TANK: 1.0},
                  form, 0.5, rear_share=share)
        for form in ("鶴翼", "魚鱗", "雁行") for share in shares)


def cmd_shape(args):
    """器を固定し、形（陣形 × 後衛予算）だけで総当たりする。

    兵種・役割の好みは**全部 1.0 に揃える**。好みが残っていると「その形が
    強い」のか「その好みが強い」のかが混ざる。
    """
    cards = _cards()
    if args.no_formpair:
        F.FORM_PAIR.update({k: 0.0 for k in F.FORM_PAIR})
        print("**陣形の残差の相殺（FORM_PAIR）を切って測る**（陽性対照）")
    else:
        print("陣形の残差の相殺: {}".format(dict(F.FORM_PAIR)))
    ents = [("{}-{}".format(p.name, num), D.make_entry(cards, p, 1000 + i * 7 + num))
            for i, p in enumerate(shapes()) for num in range(1, args.members + 1)]
    for n, e in ents:
        errs = M.validate(e)
        if errs:
            print("組めていない:", n, errs)
            return 1
    names = [n for n, _ in ents]
    jobs = [(a, b, reg, sd)
            for a, b in itertools.combinations(ents, 2)
            for reg in range(len(M.REGULATIONS)) for sd in range(SEEDS)]
    print("測る: 形{}種 × {}人 = {}人の総当たり {}局".format(
        len(shapes()), args.members, len(ents), len(jobs)))
    res = Pool(args.jobs).map(_duel, jobs, chunksize=32)
    rate = _tally(res, names)
    print("\n── 形ごとの勝率（3人の平均）──")
    per = defaultdict(list)
    for n in names:
        per[n.split("-")[0]].append(rate[n])
    for k, v in sorted(per.items(), key=lambda kv: -statistics.mean(kv[1])):
        print("  {:6s} {:5.1f}%   （{}）".format(
            k, statistics.mean(v), " ".join("{:.0f}".format(x) for x in sorted(v))))
    archetype_matrix([(a.split("-")[0] + "00", b.split("-")[0] + "00", d)
                      for a, b, d in res], [k + "00" for k in per])
    return 0


def form_edges(cards, jobs_n, shares=(0.34, 0.46, 0.58), members=2):
    """3陣形の辺（陣形どうしの勝率）を測る。**形の配分は同じ範囲で振る。**"""
    ents = [("{}-{}".format(p.name, num), D.make_entry(cards, p, 1000 + i * 7 + num))
            for i, p in enumerate(shapes(shares)) for num in range(1, members + 1)]
    jobs = [(a, b, reg, sd)
            for a, b in itertools.combinations(ents, 2)
            for reg in range(len(M.REGULATIONS)) for sd in range(SEEDS)]
    res = Pool(jobs_n).map(_duel, jobs, chunksize=32)
    win, tot = defaultdict(Counter), defaultdict(Counter)
    for na, nb, diff in res:
        a, b = na[0], nb[0]          # 陣形の1文字（鶴・魚・雁）
        if a == b:
            continue
        tot[a][b] += 1; tot[b][a] += 1
        if diff > 0:
            win[a][b] += 1
        elif diff < 0:
            win[b][a] += 1
    return {(a, b): 100.0 * win[a][b] / tot[a][b]
            for a in "鶴魚雁" for b in "鶴魚雁" if a != b}, len(jobs)


def cmd_depth(args):
    """雁行の「深さ」を振って、素の盤面の偏りが下がるか測る（§7.104）。

    **陣形の相殺は切って測る。** 相殺は偏りを隠すための帳尻合わせなので、
    入れたままだと「深さが効いた」のか「相殺が効いている」のかが分からない。
    ここで見たいのは素の盤面が良くなるかどうか。
    """
    cards = _cards()
    base_pair, base_depth = dict(F.FORM_PAIR), dict(F.FORM_DEPTH)
    if args.pair:
        v = [float(x) for x in args.pair.split(",")]
        F.FORM_PAIR.update({(3, 4): v[0], (4, 2): v[1], (2, 3): v[2]})
        print("陣形の相殺: {}".format(dict(F.FORM_PAIR)))
    else:
        F.FORM_PAIR.update({k: 0.0 for k in F.FORM_PAIR})
        print("陣形の相殺は切って測る（素の盤面を見る）")
    print("狙い: 循環 魚鱗→鶴翼→雁行→魚鱗 の各辺 57%\n")
    print("{:>10s} {:>10s} {:>10s} {:>10s} {:>12s}".format(
        "雁行の深さ", "魚→鶴", "鶴→雁", "雁→魚", "後衛の伸び"))
    try:
        for mult in args.mult:
            F.FORM_DEPTH[2] = mult
            e, n = form_edges(cards, args.jobs, members=args.members)
            print("{:>9.2f}x {:>9.1f}% {:>9.1f}% {:>9.1f}% {:>11.0f}m   （{}局）".format(
                mult, e[("魚", "鶴")], e[("鶴", "雁")], e[("雁", "魚")],
                F.FORM_DEEP.extra_depth(), n))
            sys.stdout.flush()
    finally:
        F.FORM_PAIR.update(base_pair)
        F.FORM_DEPTH.update(base_depth)
    return 0


def cmd_formpair(args):
    """陣形の残差の相殺（FORM_PAIR）を振って、辺がどこまで動くか測る。

    **狙いへ届くつまみなのかを先に確かめる。** 届かないなら値付けの話では
    なく、盤面の仕掛けが足りないという話になる（§13: 崖の上に値段を置かない）。
    """
    cards = _cards()
    base = dict(F.FORM_PAIR)
    print("いまの値: {}".format(base))
    print("狙い: 循環 魚鱗→鶴翼→雁行→魚鱗 の各辺 57%（§7.55）\n")
    print("{:>26s} {:>10s} {:>10s} {:>10s}".format(
        "(3,4) (4,2) (2,3)", "魚→鶴", "鶴→雁", "雁→魚"))
    try:
        for trio in args.set:
            vals = [float(x) for x in trio.split(",")]
            F.FORM_PAIR.update({(3, 4): vals[0], (4, 2): vals[1], (2, 3): vals[2]})
            e, n = form_edges(cards, args.jobs, members=args.members)
            print("{:>26s} {:>9.1f}% {:>9.1f}% {:>9.1f}%   （{}局）".format(
                "{:+.1f} {:+.1f} {:+.1f}".format(*vals),
                e[("魚", "鶴")], e[("鶴", "雁")], e[("雁", "魚")], n))
            sys.stdout.flush()
    finally:
        F.FORM_PAIR.update(base)
    return 0


# ---------------------------------------------------------------------------
# 組み合わせの三すくみ（§7.107）。**陣形だけでなく前衛の兵種と後衛の中身まで
# 決め打ちで組む。**
#
# 形の総当たり（cmd_shape）は兵種の好みを 1.0 に平らへ揃えていたので、
# 「鶴翼・騎兵4」と「鶴翼・歩兵4」を混ぜて平均していた。テストプレイの
# 見立て「①魚鱗歩3弓3 は ②雁行歩2弓4 に負け、②は ③鶴翼騎4弓2 に負け、
# ③は ④鶴翼歩4弓2 に負ける」の辺は、**あの計器では原理的に見えない。**
#
# 後衛の槍（§7.57）も入れる。規則は槍歩兵を後衛に許しているのに、在野の器は
# 「後衛はちょうど 6-前衛枚数 の弓兵」と決め打ちで組むので、**72部隊中0部隊**
# しか使っていなかった。組める道をここで作って、値打ちがあるのかを測る。
# ---------------------------------------------------------------------------
COMBOS = (
    ("①魚3弓3",   "魚鱗", (F.INF, F.INF, F.INF),          ("arc", "arc", "arc")),
    ("②雁2弓4",   "雁行", (F.INF, F.INF),                 ("arc",) * 4),
    ("③鶴騎4弓2", "鶴翼", (F.CAV,) * 4,                    ("arc", "arc")),
    ("④鶴歩4弓2", "鶴翼", (F.INF,) * 4,                    ("arc", "arc")),
    ("⑤鶴騎3歩1", "鶴翼", (F.CAV, F.CAV, F.CAV, F.INF),    ("arc", "arc")),
    ("⑥雁2弓3槍", "雁行", (F.INF, F.INF),                 ("arc", "arc", "arc", "spear")),
    ("⑦魚3弓2槍", "魚鱗", (F.INF, F.INF, F.INF),          ("arc", "arc", "spear")),
)
FRONT_SHARE_COMBO = 0.55     # 前衛へ回す予算（残りが後衛）


def _combo_army(cards, form_name, front, rear, cap, rng, used):
    """指定どおりの兵種で1部隊を組む。**規則を破る布陣は返さない。**"""
    form = D.FORM_BY_NAME[form_name]
    # **後衛に槍が要るなら、前衛は槍持ちを取らない。** 槍は9枚しか無く、
    # 3陣地ぶん（18人が別人物）取るので、前衛が食うと後衛が組めなくなる。
    keep_spear = "spear" in rear
    pool = {
        F.INF: [c for c in cards if c.typ == F.INF
                and not (keep_spear and c.spear)],
        F.CAV: [c for c in cards if c.typ == F.CAV],
        "arc": [c for c in cards if c.typ == F.ARC and c.might > 0],
        "spear": [c for c in cards if c.typ == F.INF and c.spear],
    }
    picks = []
    # 前衛: 枠ごとに「残り予算 ÷ 残り枠」を狙って引く（穴を空けない）
    left_f = cap * FRONT_SHARE_COMBO
    for k, t in enumerate(front):
        target = left_f / (len(front) - k)
        ok = [c for c in pool[t]
              if M.person_of(c) not in used and c.cost <= target + 1e-9]
        if not ok:
            return None, "前衛{}枠目（{}）が予算{:.1f}で埋まらない".format(
                k + 1, F.TYPE_JP.get(t, t), target)
        c = D._draw(rng, ok, lambda x: x.cost, D.META_POW)
        picks.append(c); used.add(M.person_of(c)); left_f -= c.cost
    # 後衛: **前衛と同じく「残り予算 ÷ 残り枠」を狙う。**
    #
    # 初版は残り予算いっぱいまで許して攻撃の重みで引いたので、最初の2枠が
    # 高い弓を独占して**最後の枠に1点札の穴**が空いた。槍を混ぜた側だけは
    # 槍の最安を取り置くので均等に配られ、結果として「槍 対 弓」ではなく
    # 「均等に配った陣 対 穴の空いた陣」を測ってしまった（⑦が①に 100%）。
    # §7.85 が前衛について記録している負け筋を、後衛で再現していた。
    left_r = cap - sum(c.cost for c in picks)
    for k, t in enumerate(rear):
        rest = rear[k + 1:]
        floor = 0.0
        for t2 in rest:
            av = [c.cost for c in pool[t2] if M.person_of(c) not in used]
            floor += min(av) if av else 99.0
        target = left_r / (len(rear) - k)      # 均等割りの狙い額
        room = min(left_r - floor, target)
        ok = [c for c in pool[t]
              if M.person_of(c) not in used and c.cost <= room + 1e-9]
        if not ok:      # 狙い額で埋まらなければ取り置きぶんだけ緩める
            ok = [c for c in pool[t] if M.person_of(c) not in used
                  and c.cost <= left_r - floor + 1e-9]
        if not ok:
            return None, "後衛{}枠目（{}）が残り{:.1f}で埋まらない".format(
                k + 1, t, left_r - floor)
        c = D._draw(rng, ok, D._hitting, D.META_POW)
        picks.append(c); used.add(M.person_of(c)); left_r -= c.cost
    army = F.Army(tuple(picks), form)
    errs = M.placement_errors(army)
    return (army, "") if not errs else (None, "／".join(errs))


def cmd_combo(args):
    import random
    cards = _cards()
    if args.spear is not None:
        F.SPEAR_REAR = args.spear
    ents = []
    for name, form_name, front, rear in COMBOS:
        for num in range(args.members):
            rng = random.Random("combo/{}/{}".format(name, num))
            units, used = [], set()
            for _lab, cap in M.REGULATIONS:
                a, why = _combo_army(cards, form_name, front, rear,
                                     cap, rng, used)
                if a is None:
                    print("組めない: {} 番{} {} — {}".format(name, num, _lab, why))
                    return 1
                units.append(a)
            ents.append(("{}-{}".format(name, num),
                         M.Entry(tuple(units), name=name)))
    for n, e in ents:
        errs = M.validate(e)
        if errs:
            print("規則違反:", n, errs)
            return 1
    names = [n for n, _ in ents]
    jobs = [(a, b, reg, sd)
            for a, b in itertools.combinations(ents, 2)
            for reg in range(len(M.REGULATIONS)) for sd in range(SEEDS)]
    print("測る: 組み合わせ{}種 × {}人 = {}人の総当たり {}局".format(
        len(COMBOS), args.members, len(ents), len(jobs)))
    print("陣形の相殺 {} / 雁行の深さ {} / 後衛の槍の威力 {}\n".format(
        dict(F.FORM_PAIR), F.FORM_DEPTH[2], F.SPEAR_REAR))
    res = Pool(args.jobs).map(_duel, jobs, chunksize=32)
    rate = _tally(res, names)
    per = defaultdict(list)
    for n in names:
        per[n.split("-")[0]].append(rate[n])
    print("── 組み合わせごとの勝率 ──")
    for k, v in sorted(per.items(), key=lambda kv: -statistics.mean(kv[1])):
        print("  {:10s} {:5.1f}%".format(k, statistics.mean(v)))
    archetype_matrix([(a.split("-")[0], b.split("-")[0], d) for a, b, d in res],
                     list(per), key=lambda n: n)
    return 0


def main():
    ap = argparse.ArgumentParser(description="在野ラダーの上位を測る")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("field", help="24人の総当たり")
    s.add_argument("--top", type=int, default=5)
    s.set_defaults(fn=cmd_field)
    s = sub.add_parser("combo", help="陣形×前衛の兵種×後衛の中身で三すくみを測る")
    s.add_argument("--members", type=int, default=2)
    s.add_argument("--spear", type=float,
                   help="後衛からの槍の威力（既定は field.SPEAR_REAR）")
    s.set_defaults(fn=cmd_combo)
    s = sub.add_parser("depth", help="雁行の深さを振って素の偏りを測る")
    s.add_argument("--mult", type=float, action="append", default=[])
    s.add_argument("--members", type=int, default=2)
    s.add_argument("--pair", help="陣形の相殺を同時に入れる（既定は切る）")
    s.set_defaults(fn=cmd_depth)
    s = sub.add_parser("formpair", help="陣形の相殺を振って辺の動きを測る")
    s.add_argument("--set", action="append", default=[],
                   help="(3,4),(4,2),(2,3) をカンマ区切りで。例 -6.1,4.5,5.8")
    s.add_argument("--members", type=int, default=2)
    s.set_defaults(fn=cmd_formpair)
    s = sub.add_parser("shape", help="器を固定して形だけで総当たりする")
    s.add_argument("--members", type=int, default=MEMBERS)
    s.add_argument("--no-formpair", action="store_true",
                   help="陣形の残差の相殺を切る（つまみが効いているかの対照）")
    s.set_defaults(fn=cmd_shape)
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
