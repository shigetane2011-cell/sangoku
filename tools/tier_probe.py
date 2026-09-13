# -*- coding: utf-8 -*-
"""段の重み（`design.TIER_WEIGHT`）を**実測で検算する**（§7.264）。

値付けは「同じ効果文でも、決戦型は標準型の 0.479 倍しか返さない」と置いている
（`TIER_WEIGHT`）。撃つ回数が少ないぶんを割り引く、という意味である。
**その 0.479 が本当かを、盤面で測る。**

測り方は3つ並べるだけ。**身体は3つとも同じ**（`card_probe` と同じで値札は払わない）:

    A  撃たない（ゲージを天文学的な値にして一度も発動させない）＝ その札の素の強さ
    B  同じ効果文を**標準型**（150／初期60）で
    C  同じ効果文を**決戦型**（300／初期140）で ＝ いまの姿

    実測の段の重み ＝ （C − A）／（B − A）

これが 0.479 より**大きければ決戦型を安く売りすぎ**（買った人が得をする）、
**小さければ高く売りすぎ**（買った人が損をする）。

    python3 tools/tier_probe.py --cards "馬岱〔追撃〕,凌統〔公績〕" --seeds 20

**裁定は残存差で行うこと。** 高コストの札は勝率が 93〜98% に張り付いて差が潰れる
（`card_probe` の但し書きと同じ）。勝率の列は目安として並べてある。
"""
import argparse
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from card_probe import _run                      # noqa: E402

# 土台は `card_probe` の既定から**文聘を外した**もの（文聘は §7.263 で作り替えた
# 当事者。先頭は後衛に置ける札＝槍か弓であること）。
BASE = ["周泰〔身代〕", "曹仁〔堅守〕", "郝昭〔陳倉〕", "李典〔慎重〕", "満寵〔剛毅〕"]

NEVER = {"gauge_cost": 1.0e9, "gauge_init": 0.0}   # A: 一度も撃たない
STD = {"gauge_cost": 150.0, "gauge_init": 60.0}    # B: 標準型
BIG = {"gauge_cost": 300.0, "gauge_init": 140.0}   # C: 決戦型

WIN, DIFF = 0, 1        # _run が返すタプルの位置（勝率・残存差）


def _mean(o, i):
    return statistics.mean(v[i] for v in o.values())


def _se(a, b, i):
    ks = sorted(set(a) & set(b))
    d = [b[k][i] - a[k][i] for k in ks]
    return statistics.pstdev(d) / max(1, len(d) - 1) ** 0.5


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cards", required=True, help="カンマ区切りの武将名")
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--base", default=",".join(BASE))
    ap.add_argument("--cap", default="官渡:30")
    a = ap.parse_args()
    cap_name, cap = a.cap.split(":")
    names = [x for x in a.cards.split(",") if x]
    opt = dict(seeds=a.seeds, base=a.base.split(","), cap_name=cap_name,
               cap=float(cap), pos="auto", consts=None)
    jobs = []
    for n in names:
        for tag, patch in (("A", NEVER), ("B", STD), ("C", BIG)):
            jobs.append((n, "{}|{}".format(n, tag), dict(patch), opt, None))
    with Pool(a.workers, maxtasksperchild=1) as p:
        got = {k[0]: v for k, v in p.imap_unordered(_run, jobs)}

    print("※ 単戦（性格パネル・dt 0.25・片側）。**身体は3つとも同じ**（値札を払っていない）"
          "・{}{:g}・12性格×{}種".format(cap_name, float(cap), a.seeds))
    print("{:<18}{:>8}{:>8}{:>8}{:>9}{:>9}{:>8}{:>10}{:>10}{:>8}".format(
        "武将", "撃たず", "標準型", "決戦型", "標準の値", "決戦の値", "重み",
        "標準(残存)", "決戦(残存)", "重み"))
    rows = []
    for n in names:
        A, B, C = (got["{}|{}".format(n, t)] for t in "ABC")
        aw, bw, cw = (_mean(x, WIN) for x in (A, B, C))
        ad, bd, cd = (_mean(x, DIFF) for x in (A, B, C))
        gbw, gcw = bw - aw, cw - aw
        gbd, gcd = bd - ad, cd - ad
        def _w(gb, gc):
            return "—" if abs(gb) < 1e-9 else "{:.3f}".format(gc / gb)
        print("{:<18}{:>8.1%}{:>8.1%}{:>8.1%}{:>9.2%}{:>9.2%}{:>8}"
              "{:>10.4f}{:>10.4f}{:>8}".format(
                  n, aw, bw, cw, gbw, gcw, _w(gbw, gcw), gbd, gcd,
                  _w(gbd, gcd)))
        rows.append((n, gbw, gcw, gbd, gcd))

    # **通貨としては「平均」で取る**（TIER_WEIGHT の注記）。何戦もすれば合計は
    # 平均×戦数に寄るので、**1枚ずつの比を平均するのではなく、値打ちの合計どうしの
    # 比**を出す（値打ちの小さい札の比が暴れて要約を壊すのを防ぐ）。
    sbw = sum(r[1] for r in rows); scw = sum(r[2] for r in rows)
    sbd = sum(r[3] for r in rows); scd = sum(r[4] for r in rows)
    print("\n実測の段の重み（{}枚の値打ちの合計どうしの比）".format(len(rows)))
    print("  勝率で  : {:.3f}   （標準の合計 {:.2%}／決戦の合計 {:.2%}）".format(
        scw / sbw if sbw else float("nan"), sbw, scw))
    print("  残存差で: {:.3f}   （標準の合計 {:.4f}／決戦の合計 {:.4f}）".format(
        scd / sbd if sbd else float("nan"), sbd, scd))
    ind = sorted(r[2] / r[1] for r in rows if abs(r[1]) > 0.01)
    if ind:
        print("  1枚ずつの比（勝率・標準の値打ちが1pt以上の{}枚）: 中央 {:.3f}／"
              "幅 {:.3f}〜{:.3f}".format(len(ind), statistics.median(ind),
                                       ind[0], ind[-1]))
    import sim.design as D                       # noqa: E402
    print("いまの値札: TIER_WEIGHT['大技'] = {:.3f}"
          "（手数 {:.3f}／標準 {:.3f}）".format(
              D.TIER_WEIGHT["大技"], D.TIER_WEIGHT["手数"], D.TIER_WEIGHT["標準"]))


if __name__ == "__main__":
    main()
