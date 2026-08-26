# -*- coding: utf-8 -*-
"""共通の物差し（§7.96）: 全カードを**行ごとにひとつの基準**で測る。

    python3 tools/one_ruler.py             # 3陣形で測って一覧（書かない）
    python3 tools/one_ruler.py --json X    # 結果を X へ書き出す

なぜ要るか。従来の盤面監査（`tools/floor_patch.py`）は、各カードを**同コスト・
同兵種・同役割の合成カード**と比べる。基準がカードごとに違うので、
**役割ぐるみ・兵種ぐるみの歪みが原理的に見えない**。ここでは基準を一本にする。

**ただし前衛と後衛は別の市場である。** 規則（match.placement_errors）で
前衛は歩兵・騎兵だけ、後衛は弓兵と槍持ち歩兵だけと決まっているので、
弓兵と歩兵は**同じ枠に立てない**。同じ物差しに乗せること自体が成り立たない。
よって物差しは行ごとに置く:

    前衛の札（歩兵・騎兵）→ 同コストの「歩兵・耐久」と同じ枠で比べる
    後衛の札（弓兵）      → 同コストの「弓兵・均衡」と同じ枠で比べる

**文脈は陣形で振る。** 鶴翼(前4/後2)・魚鱗(3/3)・雁行(2/4) で前後の枚数が
変わり、前衛の値打ちの符号はここで変わる（§13: 符号が変わるものに値段を
置かない）。3陣形とも負の札だけを帯調整の候補にし、1つでも正なら値段では
なく編成の知識の話として分ける。

**枠の回転は行の中だけに閉じる。** `field.matchup_cost` は札を6枠すべてへ
回すので、弓兵が前衛に立ち歩兵が後衛に立つ——**登録できない布陣を測って
しまう**（初版はこれで弓兵が軒並み -2.7 と出た。札ではなく計器の穴）。
ここでは行の中だけで回し、組んだ布陣は毎回 `placement_errors` に通して
確かめる。**規則に反する盤面は一局も測らない。**

================================================================================
 【重要】この計器が測れないもの — 兵種ごとの読みに但し書きが要る（§7.108）
================================================================================
**「線を保つ」役目が評価に入らない。** ここは札を同じ枠に立たせて1対1で
殴り合わせるので、「壁として何秒もつか」「破れたら後ろが裸になるか」という
**部隊としての役目**を見ていない。1枚の斬り合いの強さだけを測る計器である。

そのため**騎兵を構造的に過大評価する**。実測（2026-08-26・§7.108 で迂回を
直す**前**の数字。直したあとも読みの向きは変わらない）:

    帯 × 兵種（この計器）        コスト5-7 騎兵 +1.75 / コスト8-10 騎兵 +3.83
    盤面（陣形×兵種の総当たり）   ③鶴翼騎4弓2 が 15.8% で最下位

**札としては最もお買い得と読むのに、束ねると最下位。** 理由は騎兵が防御 42.3
（歩兵 56.6）で、しかも**耐久役の札が1枚も無い**（近接の耐久21枚は全部歩兵）
こと。規則で前衛は近接だけなので騎兵デッキは必ず騎兵で壁をやらされるが、
その適性が無い。1対1では勝ち、線を保つ仕事では負ける。

**この読みに従って騎兵へ帯調整（値上げ）をかけると、すでに最下位の兵種を
さらに沈める。** 実際そうしかけた。

したがって:

  - **兵種ぐるみの読み（帯 × 兵種）は、そのまま値付けの根拠にしない。**
    盤面の総当たり（`tools/ladder_top.py combo`）と突き合わせること
  - **札1枚ずつの帯調整（帯 × 役割・個別の札）はこの計器で決めてよい。**
    同じ行・同じコストの中での比較なので、役目の違いが相殺される
  - 兵種の釣り合いを疑ったら、ここではなく**部隊で測る**

§13 の「計器の適用範囲を外れたところで使うと、正しく測って間違った結論に
至る」の実例である。計器が壊れているのではない — 測れないものを読もうと
したこちらの誤り。
"""
import sys, os, csv, json
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "sim", "data", "generals.csv")
SKIP_TRAITS = {"command"}          # 計器の癖で測れない（陣頭指揮）
# 対勢力（vs_wei / vs_shu / vs_go）は §7.111 まで SKIP_TRAITS に入っていた。
# 合成カードが勢力を持たないので**絶対に発火せず**、測ると「払っているのに
# 何も返らない札」に見えるため。結果、これを持つ13枚は帯調整の点検にも
# 一度も乗っていなかった（関羽・司馬懿・周瑜を含む）。
#
# **相手に勢力を持たせて測る。** 敵が全員その勢力なら必ず当たり、別の勢力なら
# 当たらない。プールの勢力比で重みを付けて平均すれば、**実戦での期待値**になる。
# 群雄にはどの対勢力も当たる（field._vs_hits・CSV の備考）。
FACTION_MIX = (("魏", 33), ("蜀", 33), ("呉", 31), ("群雄", 23))   # プールの実数
FORMS = ("鶴翼", "魚鱗", "雁行")
NF = {"鶴翼": 4, "魚鱗": 3, "雁行": 2}


def _rows(form_name, front_card, rear_card, ff, rf):
    """前衛列・後衛列を作る。front_card / rear_card は先頭へ置く測定対象。"""
    nf, nr = NF[form_name], 6 - NF[form_name]
    front = [front_card] + [ff] * (nf - 1) if front_card else [ff] * nf
    rear = [rear_card] + [rf] * (nr - 1) if rear_card else [rf] * nr
    return front, rear


def _cost(A_rows, B_rows, form, slope, dt=0.5):
    """行の中だけで回して反対称化する。左右の偏りは打ち消え、中身の差が残る。"""
    from sim import field as G
    from sim import match as MM
    (af, ar), (bf, br) = A_rows, B_rows
    tot, n = 0.0, 0
    for i in range(3):
        A = G.Army(tuple(af[i:] + af[:i] + ar[i:] + ar[:i]), form)
        errs = MM.placement_errors(A)
        assert not errs, "規則に反する布陣を測ろうとした: {}".format(errs)
        for j in range(3):
            B = G.Army(tuple(bf[j:] + bf[:j] + br[j:] + br[:j]), form)
            assert not MM.placement_errors(B)
            tot += (G.margin(A, B, dt) - G.margin(B, A, dt)) / 2.0
            n += 1
    return (tot / n) / slope


VS_KEYS = ("vs_wei", "vs_shu", "vs_go")


def _tint(rows, faction):
    """行の札に勢力を塗る。**対勢力が当たるかどうかは相手の勢力で決まる。**"""
    import dataclasses
    front, rear = rows
    f = lambda xs: [dataclasses.replace(x, faction=faction) for x in xs]
    return f(front), f(rear)


def _value(c, A, B, form, slope):
    """A（測る札を含む軍）と B（物差し）の差をコスト点で返す。"""
    from sim import field as G
    if not (set(G.trait_keys(c.trait)) & set(VS_KEYS)):
        return _cost(A, B, form, slope)
    # 対勢力を持つ札は**相手の勢力を振って重み付き平均**（§7.111）。
    # 物差しの側（B）も同じ勢力に塗る — 塗らないと「勢力を持つ軍 対 持たない軍」
    # を測ることになり、測りたいもの以外が差に混ざる。
    tot = wsum = 0.0
    for fac, w in FACTION_MIX:
        tot += _cost(_tint(A, fac), _tint(B, fac), form, slope) * w
        wsum += w
    return tot / wsum


def measure(job):
    name, form_name = job
    from sim import field as G
    from sim import match as MM
    G.TRAITS_ON = True                     # 実ゲームと同じ条件（§7.67）
    c = {x.name: x for x in MM._roster_cards()}[name]
    form = G.Formation(n_front=NF[form_name], frontage=G.BASE_FRONTAGE)
    ff = G._synth(4.0, G.INF, G.BAL)       # 前衛の詰め物
    rf = G._synth(4.0, G.ARC, G.DPS)       # 後衛の詰め物
    slope = G.cost_yardstick(0.5)
    vals = []
    if c.typ != G.ARC:                     # 前衛に置ける（歩兵・騎兵）
        ruler = G._synth(c.cost, G.INF, G.TANK)     # 前衛の物差し
        vals.append(_value(c, _rows(form_name, c, None, ff, rf),
                           _rows(form_name, ruler, None, ff, rf), form, slope))
    if c.typ == G.ARC or c.spear:          # 後衛に置ける（弓兵・槍持ち歩兵）
        ruler = G._synth(c.cost, G.ARC, G.BAL)      # 後衛の物差し
        vals.append(_value(c, _rows(form_name, None, c, ff, rf),
                           _rows(form_name, None, ruler, ff, rf), form, slope))
    # **槍持ちは両方の行で測って良いほうを採る**（§7.112）。槍は
    # `field.Unit.__init__` の `if card.spear and not is_front:` でしか効かない
    # ので、前衛でだけ測ると **0.75コスト点（SPEAR_PRICE）を払って何も返らない札**
    # に見える。置き場所を選ぶのはプレイヤーなので、良いほうがその札の値打ち。
    return name, form_name, max(vals)


def targets(rows):
    sk = {r["技名"]: r["効果"] for r in csv.DictReader(
        open(CSV.replace("generals", "skills"), encoding="utf-8-sig"))}
    out = []
    for n, g in rows.items():
        if set((g["固有特性"] or "").split("、")) & SKIP_TRAITS:
            continue
        if "打消し" in sk.get(g["必殺技"], ""):
            continue
        # 槍持ちの歩兵は前後どちらにも置けるが、物差しは前衛側で当てる
        out.append(n)
    return out


def main():
    rows = {g["名前"]: g for g in csv.DictReader(open(CSV, encoding="utf-8-sig"))}
    names = targets(rows)
    jobs = [(n, f) for n in names for f in FORMS]
    print("測る: {} 枚 × {} 陣形 = {} 局（規則どおりの布陣だけ）".format(
        len(names), len(FORMS), len(jobs)), flush=True)
    res = Pool(4).map(measure, jobs, chunksize=1)
    by = {}
    for n, f, v in res:
        by.setdefault(n, {})[f] = round(v, 3)

    data = []
    for n, vs in by.items():
        g = rows[n]
        data.append({"name": n, "cost": float(g["コスト"]), "typ": g["兵種"],
                     "role": g["役割"], "row": "後衛" if g["兵種"] == "弓兵" else "前衛",
                     "ctx": vs, "min": min(vs.values()), "max": max(vs.values()),
                     "avg": round(sum(vs.values()) / len(vs), 3)})
    if "--json" in sys.argv:
        out = sys.argv[sys.argv.index("--json") + 1]
        json.dump(data, open(out, "w"), ensure_ascii=False, indent=1)
        print("書き出した:", out)

    def band(c):
        return "1" if c < 2 else ("2-4" if c < 5 else ("5-7" if c < 8 else "8-10"))

    from collections import defaultdict
    for label, key in (("帯 × 兵種", lambda r: (band(r["cost"]), r["typ"])),
                       ("帯 × 役割（前衛の札だけ）",
                        lambda r: (band(r["cost"]), r["role"]) if r["row"] == "前衛" else None)):
        print("\n── {}（3陣形の平均。物差しは行ごと）──".format(label))
        agg = defaultdict(list)
        for r in data:
            k = key(r)
            if k:
                agg[k].append(r["avg"])
        for k in sorted(agg):
            v = agg[k]
            print("  %-5s %-4s n=%2d 平均%+6.2f" % (k[0], k[1], len(v), sum(v) / len(v)))
        if label == "帯 × 兵種":
            # **間違いが起きるのは表を読むとき**なので、docstring だけでなく
            # ここにも出す（§7.108・§13）。
            print("  ※ この段は**値付けの根拠にしない**。ここは札を同じ枠に立たせて")
            print("     1対1で殴り合わせるので、「線を保つ」役目が入っていない。")
            print("     実例（2026-08-26・§7.108 で迂回を直す前）: 騎兵はここで")
            print("     +3.83（最もお買い得）と出たが、盤面では騎兵4枚の前衛が")
            print("     47% の戦で全滅し、型としては 15.8% で最下位だった。")
            print("     騎兵に耐久役の札は1枚も無く、壁の適性が無いまま壁をやらされる。")
            print("     直したあとも読みの向きは同じ（この段は役目を見ていない）。")
            print("     **兵種の釣り合いは部隊で測る** → tools/ladder_top.py combo")

    firm = [r for r in data if r["max"] < 0.0]
    swing = [r for r in data if r["min"] < -0.25 <= 0.0 < r["max"]]
    print("\n── 3陣形とも負（＝陣形によらず弱い。帯調整の候補）: {} 枚 ──"
          .format(len(firm)))
    for r in sorted(firm, key=lambda x: x["avg"]):
        print("  %+6.2f  %-16s %s%.0f・%-2s %s  [%s]" % (
            r["avg"], r["name"], r["typ"][0], r["cost"], r["role"], r["row"],
            " ".join("%s%+.2f" % (f[0], r["ctx"][f]) for f in FORMS)))
    print("\n── 符号が陣形で変わる（＝値段では直せない。手引きの話）: {} 枚 ──"
          .format(len(swing)))
    for r in sorted(swing, key=lambda x: x["min"])[:20]:
        print("  min%+6.2f max%+6.2f  %-16s %s%.0f・%s  [%s]" % (
            r["min"], r["max"], r["name"], r["typ"][0], r["cost"], r["role"],
            " ".join("%s%+.2f" % (f[0], r["ctx"][f]) for f in FORMS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
