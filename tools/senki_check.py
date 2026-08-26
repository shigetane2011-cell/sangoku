# -*- coding: utf-8 -*-
"""戦記の難易度の坂を測る（§7.105）。

    python3 tools/senki_check.py                    # いまの設定で全52戦
    python3 tools/senki_check.py --depth 1.0 --pair=-6.1,4.5,5.8   # 旧設定と比べる

なぜ要るか。陣形の釣り合いを触ると**戦記の敵もまとめて動く**。敵デッキは
52戦のうち 雁行18・魚鱗19・鶴翼15 で、雁行を強くすれば雁行の戦が難しくなり、
鶴翼を弱くすれば鶴翼の戦が易しくなる。ラダーが健全になっても、**物語の坂が
崩れていたら直したことにならない。**

**進行をそのまま辿る。** 初期セットから始めて、1戦ずつ「軍師の草案」で挑み、
勝敗を測ってから登用を手持ちへ足す。途中で強くなる前提を飛ばして全解放で
測ると、序盤が実際より易しく出る。

**草案の強さは保証されていない**（`suggest_deck` の但し書き）。ここで測るのは
「押せば出陣できる案」の勝率であって、上手に組んだ場合の勝率ではない。
坂の**形**（どこで急に落ちるか）を見るための計器で、絶対値は目安である。

**草案は必ず引き直す。** 1通りだけで測ると、草案のアタリ・ハズレが難易度に
化ける（初版はこれで「52戦中34戦が25%以下」と出た。引き直すと同じ戦が
0% → 89% になる）。プレイヤーは案が弱ければ引き直すか自分で組むので、
**引き直した中の最良**をその戦の難易度として読む。全部の引きで勝てない戦
だけが本当の難所である。
"""
import argparse
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F          # noqa: E402
from sim import match as M          # noqa: E402
from sim import play as PL          # noqa: E402
from sim import rosterdata as R     # noqa: E402
from sim import senki as SK         # noqa: E402

SEEDS = 9


def _one(job):
    ua, foe, reg_i, seed = job
    r = M.play_one(PL.BoardEntry({reg_i: ua}), PL.BoardEntry({reg_i: foe}),
                   reg_i, 0.5, seed=seed)
    return 1.0 if r["winner"] == "A" else (0.5 if r["winner"] == "引き分け" else 0.0)


def run(jobs_n, drafts, draft_form=None):
    cards = M._roster_cards()
    unlocked = set(R.senki_start())
    out = []
    for b in SK.battles():
        rates = []
        for d in range(drafts):
            names, form = SK.suggest_deck(cards, unlocked, b, seed=b["i"] * 10 + d,
                                          form=draft_form)
            if not names:
                continue
            ua, _ = PL.parse_deck(cards, F.TRAIT_SEP.join(names), form)
            foe = SK.enemy_army(cards, b)
            reg_i = PL.REG_NAMES.index(b["board"])
            res = Pool(jobs_n).map(
                _one, [(ua, foe, reg_i, 1000 + b["i"] * 97 + s)
                       for s in range(SEEDS)])
            rates.append(100.0 * sum(res) / len(res))
        out.append((b, rates))
        # 勝てても負けても、進行としては登用が起きた先を見たい
        unlocked |= set(b["recruits"])
    return out


def main():
    ap = argparse.ArgumentParser(description="戦記の難易度の坂を測る")
    ap.add_argument("-j", "--jobs", type=int, default=4)
    ap.add_argument("--depth", type=float,
                    help="雁行の深さ（省略時はいまの設定）")
    ap.add_argument("--pair", help="(3,4),(4,2),(2,3) をカンマ区切り")
    ap.add_argument("--drafts", type=int, default=5,
                    help="草案を何通り引き直すか（既定5）")
    ap.add_argument("--draft-form", choices=["魚鱗", "鶴翼", "雁行"],
                    help="草案の陣形を固定する（省略時は敵と同じ陣形＝現状）")
    a = ap.parse_args()
    if a.depth is not None:
        F.FORM_DEPTH[2] = a.depth
    if a.pair:
        v = [float(x) for x in a.pair.split(",")]
        F.FORM_PAIR.update({(3, 4): v[0], (4, 2): v[1], (2, 3): v[2]})
    print("雁行の深さ {} / 陣形の相殺 {}".format(
        F.FORM_DEPTH[2], dict(F.FORM_PAIR)))
    print("軍師の草案で全戦に挑む（草案{}通り × 1通り{}局・進行どおりに登用）\n"
          .format(a.drafts, SEEDS))

    print("草案の陣形: {}\n".format(a.draft_form or "敵と同じ（現状）"))
    rows = run(a.jobs, a.drafts, a.draft_form)
    print("{:>3s} {:<16s} {:<5s} {:<5s} {:>7s} {:>7s}  {}".format(
        "戦", "戦名", "帯", "陣形", "最良", "平均", "草案ごと"))
    by_form, hard = {}, []
    for b, rates in rows:
        if not rates:
            print("{:>3d} {:<16s} {:<5s} {:<5s}  ← 草案が組めない".format(
                b["i"] + 1, b["title"], b["board"], b["form"]))
            continue
        best = max(rates)
        by_form.setdefault(b["form"], []).append(best)
        mark = "  ← どの草案でも勝てない" if best <= 35.0 else ""
        if mark:
            hard.append((b, best))
        print("{:>3d} {:<16s} {:<5s} {:<5s} {:>6.0f}% {:>6.0f}%  {}{}".format(
            b["i"] + 1, b["title"], b["board"], b["form"], best,
            statistics.mean(rates),
            " ".join("{:.0f}".format(x) for x in rates), mark))

    ok = [max(r) for _b, r in rows if r]
    print("\n全{}戦（最良の草案で）平均 {:.1f}%  中央 {:.1f}%".format(
        len(ok), statistics.mean(ok), statistics.median(ok)))
    print("**どの草案でも 35% 以下: {}戦**{}".format(
        len(hard), "" if not hard else
        "  — " + "、".join("{}({})".format(b["title"], b["form"]) for b, _r in hard)))
    print("敵の陣形ごと（最良の草案）:")
    for k, v in sorted(by_form.items(), key=lambda kv: -statistics.mean(kv[1])):
        print("  {:4s} n={:2d}  平均 {:.1f}%".format(k, len(v), statistics.mean(v)))


if __name__ == "__main__":
    main()
