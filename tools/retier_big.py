# -*- coding: utf-8 -*-
"""決戦型の効果文を、**身体を動かさずに**太らせる（§7.264）。

段の重み（`design.TIER_WEIGHT["大技"]`）を下げると、同じ効果文の請求が安くなる。
そのまま `sync` すると**浮いたぶんは身体（兵力・攻撃力）へ返る** —— これは
テストプレイの決裁「身体に戻すんじゃなくて、効果を強くするとかで対応して」と
逆である。

この道具は、決戦型の札1枚ごとに

    新しい重みでの効果予算 ＝ **前の効果予算**

となる倍率を解いて効果文を書き換える。結果として

    身体（兵力・攻撃力）は動かない ／ 兵法だけが太る

**倍率は札ごとに解く**（一律に掛けない）。効果文の中には伸びない節（畏怖・見切り・
ゲージ阻害・打消し・代償など `_scale_effect` が触らないもの）があるので、
一律に掛けると**そういう節を多く持つ札ほど足りなくなる**。1枚ずつ解けば、
伸びない節のぶんは伸びる節が余計に背負って、予算はぴたりと戻る。

    # 1) design.TIER_WEIGHT["大技"] を新しい値へ書き換えてから
    python3 tools/retier_big.py --before <改修前の sim/data> [--apply]

`--apply` を付けるまで書き込まない（既定は下見）。
"""
import argparse
import csv
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from sim import design as D          # noqa: E402
from sim import field as F           # noqa: E402
from sim import rosterdata as R      # noqa: E402


def _rows(path, key):
    txt = open(path, encoding="utf-8-sig").read()
    return {r[key]: r for r in csv.DictReader(io.StringIO(txt))}


def _budget(effect, target, g, gc, gi):
    """その効果文の効果予算（**いまの `TIER_WEIGHT` で**）。"""
    sk = F._parse_skill(effect, target)
    return D.effect_value(sk, target, gc, gi,
                          kisei=float(g["ゲージ上昇率"]) / 100.0,
                          cost=float(g["コスト"]),
                          typ=R.TYPE_MAP[g["兵種"]], tilt=R.tilt_of(g))


# 秒数が整数の節（足止め・ゲージ阻害）は **1秒 → 2秒 で値打ちが2倍に跳ぶ**。
# 倍率を連続に動かしても予算が飛び越えてしまうので、跳んだ札はこの節を
# **据え置いて解き直す**（伸びるぶんは威力が背負う）。
FREEZE = r"(?:行動阻害|ゲージ阻害)\s*\d+秒"
TOL = 0.02


def _scaled(effect, m, freeze=False):
    if not freeze:
        return R._scale_effect(effect, m)
    keep = []

    def _hide(mo):
        keep.append(mo.group(0))
        return "\x01{}\x01".format(len(keep) - 1)

    import re as _re
    out = R._scale_effect(_re.sub(FREEZE, _hide, effect), m)
    return _re.sub(r"\x01(\d+)\x01", lambda mo: keep[int(mo.group(1))], out)


def _search(effect, target, g, gc, gi, want, freeze, lo=1.0, hi=6.0):
    if _budget(_scaled(effect, hi, freeze), target, g, gc, gi) < want:
        return hi
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if _budget(_scaled(effect, mid, freeze), target, g, gc, gi) < want:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _solve(effect, target, g, gc, gi, want):
    """効果予算が want に**いちばん近くなる**書き換えを返す。"""
    best = None
    for freeze in (False, True):
        m = _search(effect, target, g, gc, gi, want, freeze)
        for mm in (m, m * 0.999):        # 跳ぶ手前と跳んだ後の両方を見る
            txt = _scaled(effect, mm, freeze)
            got = _budget(txt, target, g, gc, gi)
            if best is None or abs(got - want) < abs(best[2] - want):
                best = (mm, txt, got)
        if abs(best[2] - want) <= TOL:
            break
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--before", required=True,
                    help="改修前の sim/data（前の効果予算を読む）")
    ap.add_argument("--apply", action="store_true", help="skills.csv へ書き込む")
    a = ap.parse_args()

    R.load_skills_into_field()
    old_g = _rows(os.path.join(a.before, "generals.csv"), "名前")
    old_s = _rows(os.path.join(a.before, "skills.csv"), "兵法名")
    rows = R.skills()
    owner = {g["兵法"]: g for g in R.generals() if g.get("兵法")}
    gc, gi = D.GAUGE_TIER["大技"]

    print("決戦型の効果文を太らせる（段の重み {:.3f}・身体は動かさない）".format(
        D.TIER_WEIGHT["大技"]))
    print("{:<16}{:>8}{:>8}{:>8}  {}".format("兵法", "前の予算", "倍率", "後の予算", "効果文"))
    n = 0
    short = []
    for r in rows:
        g = owner.get(r["兵法名"])
        if g is None:
            continue
        if R.tier_for(r, F.SKILL_INFO.get(r["兵法名"])) != "大技":
            continue
        want = float(old_g[g["名前"]]["効果予算"]) - sum(
            D.trait_value(k) for k in R.traits_of(g)) - (
                D.SPEAR_PRICE if (g.get("槍") or "").strip() else 0.0)
        base = old_s[r["兵法名"]]["効果"]
        m, new, got = _solve(base, r["対象"], g, gc, gi, want)
        if abs(got - want) > TOL:
            short.append("{}（{:+.3f}）".format(r["兵法名"], got - want))
        print("{:<16}{:>8.3f}{:>8.3f}{:>8.3f}  {}".format(
            r["兵法名"], want, m, got, new))
        r["効果"] = new
        n += 1
    if short:
        print("\n⚠ 前の予算へ戻しきれなかった札: " + "、".join(short)
              + "\n  → 手で節を足す／減らすこと")
    print("\n{}枚。{}".format(
        n, "書き込んだ。`python3 -m sim.rosterdata sync` を続けて走らせること"
        if a.apply else "下見だけ（--apply で書き込む）"))
    if a.apply:
        R._write("skills.csv", rows)


if __name__ == "__main__":
    main()
