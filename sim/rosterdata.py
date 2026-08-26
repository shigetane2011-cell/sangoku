# -*- coding: utf-8 -*-
"""sim/data/*.csv（武将・必殺技・固有特性）の読み込みと検算。

**使う前に必ず `python3 sim/rosterdata.py` を通すこと。** データが狂っていると、
その上で測る勝率も値付けも全部嘘になる（§13 の「測定が嘘をついた」事例と同じ形）。

CSV は UTF-8 BOM 付きなので `utf-8-sig` で開く。BOM を無視すると先頭列の
キーが '\\ufeff名前' になり、**1行も読めないのに例外も出ない**（実測で踏んだ）。
"""

from __future__ import annotations

import csv
import math
import re
import os
import statistics as st
from collections import Counter
from typing import Dict, List

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# 兵種標準の攻撃間隔（§6.3）。CSV に列が無いのでここで補う。
INTERVAL = {"歩兵": 1.2, "騎兵": 1.1, "弓兵": 1.3}


def _load(name: str) -> List[Dict[str, str]]:
    with open(os.path.join(DATA, name), encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if any(v.strip() for v in r.values())]


def generals() -> List[Dict[str, str]]:
    return [r for r in _load("generals.csv") if r.get("名前")]


def skills() -> List[Dict[str, str]]:
    return [r for r in _load("skills.csv") if r.get("技名")]


def traits() -> List[Dict[str, str]]:
    return [r for r in _load("traits.csv") if r.get("キー")]


def senki_start() -> List[str]:
    """初期セット（戦記・§7.60）の人物名。generals.csv と突き合わせて検証する
    — データの誤字は起動時に大声で死ぬほうが、静かに40人未満で配るより安い。"""
    known = {g["人物"] for g in generals()}
    persons = [r["人物"] for r in _load("senki_start.csv") if r.get("人物")]
    bad = [p for p in persons if p not in known]
    if bad:
        raise ValueError("senki_start.csv に居ない人物: " + "・".join(bad))
    if len(set(persons)) != len(persons):
        raise ValueError("senki_start.csv に重複がある")
    return persons


def power(g: Dict[str, str]) -> float:
    """CSV の1行から総合値を計算する。**定義は `design.total_value` に一本化。**

        総合値 = 兵力 × √( 攻撃力/攻撃間隔 × (100+防御力)/100 )

    ここに定義を書き写していたせいで、設計式を直したときに片方だけ古い定義の
    まま残り、盤面と設計式の比が 8.7倍ずれた（実際に踏んだ）。**同じ量の定義を
    2箇所に持たないこと。**

    命中率・クリ率は使わない。位置ベースの盤面がどちらも持っていないからである
    （§7.8: 命中率 -X% は攻撃力 -X% と同じ意味になる）。
    """
    from . import design as D
    from . import field as F
    return D.total_value(float(g["兵力"]), float(g["攻撃力"]),
                         TYPE_MAP[g["兵種"]])


def affine_fit(rows) -> tuple:
    """総合値 ≒ A + B×コスト を最小二乗で当てる。

    §4.6 / sim/roster.py の設計は「枠の基礎価値 A ＋ コスト比例分 B×c」。
    1部隊は6枠固定なので、この形なら **合計コストが同じ編成は配分によらず
    総価値が等しくなる**（6A + B×合計コスト）。A が大きいほど安い札が得に見えるが、
    枠数が固定なので編成の総価値には効かない、というのが狙いである。
    """
    xs = [float(r["_cost"]) for r in rows]
    ys = [r["_power"] for r in rows]
    mx, my = st.mean(xs), st.mean(ys)
    b = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
         / sum((x - mx) ** 2 for x in xs))
    return my - b * mx, b


def check() -> int:
    """CSV の検算。**盤面・設計式・CSV の3つが一致しているかを見る。**

    以前はここで「実力比% が能力値から再現できるか」を見ていたが、あれは
    **CSV の中だけで閉じた検算**だった。命中率とクリ率を掛けた総合値は盤面に
    存在しないので、通っても盤面が同じ強さで動く保証にならない。
    """
    from . import design as D
    from . import field as F
    load_skills_into_field()
    load_traits_into_field()
    G, S, T = generals(), skills(), traits()
    for g in G:
        g["_cost"] = int(g["コスト"])
        g["_power"] = power(g)
    bad = 0
    print(f"武将 {len(G)}枚 / 必殺技 {len(S)}種 / 固有特性 {len(T)}種")

    # --- 参照の整合 -------------------------------------------------------
    sk = {s["技名"]: s for s in S}
    tk = {t["キー"]: t for t in T}
    miss_s = [g["名前"] for g in G if g["必殺技"] not in sk]
    # 特性なし（空欄）は正常。空文字を「未定義」に数えると検算が常に NG 1 になる
    miss_t = sorted({g["固有特性"] for g in G
                     if g["固有特性"] and g["固有特性"] not in tk})
    unused = [s["技名"] for s in S if s["技名"] not in {g["必殺技"] for g in G}]
    cost_ng = [(g["名前"], g["コスト"], sk[g["必殺技"]]["コスト"])
               for g in G if g["必殺技"] in sk
               and g["コスト"] != sk[g["必殺技"]]["コスト"]]
    cnt = Counter(g["固有特性"] for g in G)
    cnt_ng = [(k, tk[k]["採用枚数"], cnt.get(k, 0)) for k in tk
              if int(tk[k]["採用枚数"]) != cnt.get(k, 0)]
    for label, v in (("必殺技が未定義", miss_s), ("固有特性が未定義", miss_t),
                     ("使われていない必殺技", unused),
                     ("必殺技とコストが不一致", cost_ng),
                     ("固有特性の採用枚数が不一致", cnt_ng)):
        print(f"  {label}: {v if v else 'なし'}")
        bad += len(v)

    # --- CSV の数字で盤面が動くか（読んだとおりに動くシートか） -------------
    form = F.FORM_STANDARD
    worst = ("", 0.0)
    for g, c in zip(G, to_cards([g["名前"] for g in G])):
        u = F.Unit(0, c, form, (0.0, 0.0), True)
        for col, got in (("兵力", u.men0), ("武力", u.might),
                         ("知力", u.wits), ("攻撃力", u.atk),
                         ("防御力", u.dfn)):
            want = float(g[col])
            e = abs(got - want) / max(abs(want), 1e-9) * 100.0
            if e > worst[1]:
                worst = ("{} の {}（表 {} / 盤面 {:.1f}）".format(
                    g["名前"], col, g[col], got), e)
    # 表は小数1桁で書くので、丸めぶんのずれは残る（攻撃力は武力・知力・兵力から
    # 導くので、3つの丸めが乗る）。0.5% を超えたら丸めでは説明できない。
    print("  CSV の数字で盤面が動くか: 最大ずれ {:.3f}%  {}".format(
        worst[1], worst[0] if worst[1] > 0.5 else "（丸めの範囲）"))
    if worst[1] > 0.5:
        print("    ★ シートと盤面が食い違う。regenerate() を走らせ直すこと")
        bad += 1

    # --- CSV が設計式の上に乗っているか -----------------------------------
    worst = ("", 0.0)
    for g in G:
        v = D.derive(to_design(g))
        for col, got in (("兵力", v["兵力"]), ("武力", v["武力"]),
                         ("知力", v["知力"]), ("攻撃力", v["攻撃力"])):
            want = float(g[col])
            e = abs(got - want) / max(abs(want), 1e-9) * 100.0
            if e > worst[1]:
                worst = ("{} の {}".format(g["名前"], col), e)
    print("  CSV が設計式と一致するか: 最大ずれ {:.3f}%  {}".format(
        worst[1], worst[0] if worst[1] > 0.5 else ""))
    if worst[1] > 0.5:
        print("    ★ 手で直したまま regenerate() を通していない可能性")
        bad += 1

    # --- 効果予算 ---------------------------------------------------------
    # **上限に当たった札は「値段どおりに payしていない」＝そのぶん強い。** 実測で
    # コスト5・威力1500%の計略を持たせると智将の残差が +0.423 コスト点まで開き、
    # コスト6（上限 2.70）へ上げると -0.063 に落ちた。上限に当たらない範囲では
    # 傾きの残差は ±0.08 に収まる。**直し方はコストを1段上げること**なので、
    # 収まるコストを名指しで出す（§7.24）。
    over = []
    for g in G:
        v = D.derive(to_design(g))
        if v["効果超過"] > 1e-9:
            need = v["効果予算"] + v["効果超過"]
            fit = math.ceil(need / D.EFFECT_CAP * 100.0 - 1e-6) / 100.0
            over.append((g["名前"], g["必殺技"], g["固有特性"], v["効果超過"],
                         float(g["コスト"]), fit))
    ng = [r for r in over if r[3] > D.EFFECT_OVER_OK]
    print("  効果予算の超過: {}（許容 {:.1f}点を超えるもの {}）".format(
        len(over) if over else "なし", D.EFFECT_OVER_OK, len(ng)))
    for n, s_, t_, x, c, fit in sorted(over, key=lambda r: -r[3]):
        print("    {:<16}{:<10}{:<10}超過 {:.2f}点  コスト{:.0f}→{:.1f}で収まる{}".format(
            n, s_, t_, x, c, fit, "  ★許容超え" if x > D.EFFECT_OVER_OK else ""))
    if over:
        print("    （許容内は設計上の見逃し。超えたらコストを上げる）")
    bad += len(ng)

    # --- 能力値 + 効果 = コスト か（§4.6 の本体） --------------------------
    # **総合値そのものを ±8% で見てはいけない。** 効果予算を導入した以上、同じ
    # コストでも技の強い札は能力値が低いのが正しい。見るべきは
    # 「能力値コスト + 効果予算 = 表示コスト」であり、これは上限に当たった札を
    # 除いて厳密に成り立つ。
    worst = ("", 0.0)
    for g in G:
        v = D.derive(to_design(g))
        got = float(g["能力値コスト"]) + v["効果予算"]
        e = abs(got - float(g["コスト"]))
        if e > worst[1]:
            worst = (g["名前"], e)
    print("  能力値コスト + 効果予算 = コスト: 最大ずれ {:.4f}点 {}".format(
        worst[1], worst[0] if worst[1] > 0.01 else ""))
    if worst[1] > 0.01:
        bad += 1

    print("  コスト帯ごとの 総合値のばらつき（**これは効果予算の幅である**）")
    for c in sorted({g["_cost"] for g in G}):
        v = [g["_power"] / c for g in G if g["_cost"] == c]
        m = st.mean(v)
        dev = max(abs(x - m) / m * 100 for x in v)
        eb = [D.derive(to_design(g))["効果予算"] for g in G if g["_cost"] == c]
        print("    コスト{:<3} {}枚  総合値 ±{:5.1f}%   効果予算 {:.2f}〜{:.2f}点"
              .format(c, len(v), dev, min(eb), max(eb)))

    # --- 配分によらず総価値が等しいか（設計の眼目） ------------------------
    A, B = affine_fit(G)
    print(f"  総合値 ≒ {A:.3f} + {B:.4f} × コスト  "
          f"（枠の基礎価値 + コスト比例分）")
    # **兵種をまたいで総合値を足さない。** ACT_COEF は「兵種を盤面で等しくする」
    # つまみなので、同じコストでも兵種が違えば総合値は違う。またいで足すと、
    # 測っているのはコストの加算性ではなく**その帯の兵種構成**になる（実際に
    # 弓兵の係数を 0.89→1.44 へ直したとき、幅が 1.7%→9.1% に見せかけで悪化した）。
    # **兵種をまたぐ通貨はコストそのもの**で、その加算性は盤面で測る
    # （`python3 -m sim.field cost`）。ここは1兵種の中だけで見る。
    main = Counter(g["兵種"] for g in G).most_common(1)[0][0]
    by = {}
    for c in sorted({g["_cost"] for g in G}):
        v = [g["_power"] for g in G if g["_cost"] == c and g["兵種"] == main]
        by[c] = st.mean(v) if v else st.mean(
            g["_power"] for g in G if g["_cost"] == c)
    plans = [("均等 5×6", [5] * 6), ("やや偏り 6/6/6/4/4/4", [6, 6, 6, 4, 4, 4]),
             ("偏り 7/7/4/4/4/4", [7, 7, 4, 4, 4, 4]),
             ("極端 10/10/4/2/2/2", [10, 10, 4, 2, 2, 2]),
             ("極端 10/9/8/1/1/1", [10, 9, 8, 1, 1, 1])]
    print("  6枠・合計コスト30 での総価値（{}のみ。配分によらず等しいのが狙い）"
          .format(main))
    vals = []
    for name, cs in plans:
        v = sum(by[c] for c in cs)
        vals.append(v)
        print(f"    {name:<22} {v:6.2f}")
    spread = (max(vals) - min(vals)) / st.mean(vals) * 100
    print(f"    → 幅 {spread:.1f}%（0% が理想。均等が最良で、偏るほど下がる）")

    print("\n" + ("検算 NG が {} 件".format(bad) if bad else "検算 OK"))
    return bad


def _main() -> int:
    bad = check()
    print()
    bad += on_curve()
    return bad


# ============================================================================
# field.py への橋渡し
# ============================================================================
#
# **実カードはバランス測定に使わない。** 実編成をサンプルした勝率パネルは飽和して
# 測れない（§13）。統制した合成カードで測る。ここで作るのは実況・リプレイ用である。

ROLE_MAP = {"耐久": "tank", "均衡": "bal", "火力": "dps",
            "瞬発": "burst", "支援": "sup"}
TYPE_MAP = {"歩兵": "inf", "騎兵": "cav", "弓兵": "arc"}


# ---------------------------------------------------------------------------
# 知力の傾き — カード表記の 武勇・知略 から出す（§7.106）
# ---------------------------------------------------------------------------
# **傾きは総合値を動かさない**（design.py の検算[1]）。効くのは必殺技の係数と、
# 知力が守りに効く場面（混乱の抵抗・状態効果・「知力が最高/最低」の狙い撃ち）。
#
# 以前は5段階の名前で、しかも人物指定は16名だけ、残りは**必殺技の種別**から
# 自動で決めていた（計略を持つ札は自動で知力寄り）。その結果:
#
#   - 呂布（武勇100・知略26）と 張飛（武勇98・知略45）が同じ「勇将」に潰れる
#   - 「計略を持たされた猛将」が勝手に知力寄りになる
#   - 内部の武力・知力に**人物のイメージがほとんど入っていない**
#     （手書きの武勇/知略との順位相関 0.12〜0.55。実測）
#
# いまは カード表記の 武勇・知略 が原本で、内部の配分はそこから導く。
# **武勇・知略は「その人物の質」**（コストとは無関係。呂布100 と 樊建40 を
# 直接比べてよい）という定義で、表示と内部が食い違いようがなくなる。
#
#   k = 知力/武力 = (知略 ÷ 武勇) ^ TILT_POW を、プールの中央が 1.00 になるよう正規化
#
# TILT_POW は範囲を旧5段階（0.55〜1.90）へ合わせるための圧縮。生の比は
# 0.26〜6.79 と開きすぎていて、そのまま使うと知力が4桁に届く札が出る。
TILT_POW = 0.35

_TILT_CACHE: Dict[str, float] = {}


def tilt_of(g: Dict[str, str]):
    """1枚の知力の傾き k（＝知力÷武力）。**カード表記から導く。**

    中央を 1.00 に正規化するのでプール全体を見る必要があり、結果を憶えておく
    （`regenerate` が120枚ぶん呼ぶ）。CSV を書き換えたら `tilt_reset()`。
    """
    if not _TILT_CACHE:
        rows = generals()
        raw = {}
        for r in rows:
            mi = float(r.get("武勇") or 0.0)
            wi = float(r.get("知略") or 0.0)
            raw[r["名前"]] = ((wi / mi) ** TILT_POW
                             if mi > 0.0 and wi > 0.0 else 1.0)
        mid = st.median(raw.values()) if raw else 1.0
        _TILT_CACHE.update({k: v / mid for k, v in raw.items()})
    return _TILT_CACHE.get(g.get("名前", ""), 1.0)


def tilt_reset() -> None:
    """武勇・知略を書き換えたら呼ぶ（憶えた正規化を捨てる）。"""
    _TILT_CACHE.clear()


def _skill_target(name: str) -> str:
    for s in skills():
        if s["技名"] == name:
            return s["対象"]
    return ""


# 生まれつきの固有特性。**複数持てる形で読む。**
#
# CSV の「固有特性」列は「、」区切りで複数書ける（1つならこれまでどおり）。
# プレイヤーが獲得してセットする特性（`players.owned_traits`）とは別枠で、
# こちらはカードに固定で付いているもの。
#
# 盤面も複数読む（`field.Unit.traits`）。区切りの定義は field.TRAIT_SEP の1箇所。


def traits_of(g: Dict[str, str]) -> List[str]:
    """その武将が生まれつき持つ固有特性のキー（0個以上）。"""
    from . import field as F
    return list(F.trait_keys(g.get("固有特性") or ""))


def to_design(g: Dict[str, str]):
    """CSV の1行を sim/design.py の設計指定へ写す。

    **CSV の兵力・攻撃力は使わない。** あの2列は旧ルール（レーンあり）の内部スケール
    で、この盤面のコスト曲線とは別物である。両方を混ぜると、兵力はコスト曲線で伸び、
    攻撃力は CSV の別スケールで伸びるので、**コストが二重に乗る**（実測：コスト10の
    札の総合値が設計式の 65% しかないのに、コスト1の札は 46% しかない、という形で
    傾きまで狂う）。コストだけを通貨として受け取り、能力値は設計式で引き直す。
    """
    from . import design as D
    from . import field as F
    gc = float(g["消費ゲージ%"])
    gi = float(g["初期ゲージ"])
    sk = F.SKILL_INFO.get(g["必殺技"])
    # 技の値段ぶんは能力値から引く（§7.5）。技が読み込まれていなければ 0 のまま。
    eff = (D.effect_value(sk, _skill_target(g["必殺技"]), gc, gi,
                          kisei=float(g["ゲージ上昇率"]) / 100.0,
                          cost=float(g["コスト"]), typ=TYPE_MAP[g["兵種"]],
                          tilt=tilt_of(g)) if sk else 0.0)
    eff += sum(D.trait_value(k) for k in traits_of(g))
    if (g.get("槍") or "").strip():
        eff += D.SPEAR_PRICE      # 槍の値札（§7.77）
    return D.Design(cost=float(g["コスト"]), typ=TYPE_MAP[g["兵種"]],
                    role=g["役割"], tilt=tilt_of(g),
                    gauge_cost=gc, gauge_init=gi, effect=eff,
                    lean=float(g.get("役割寄せ") or 0.0),
                    def_lean=float(g.get("防御寄せ") or 0.0),
                    spd_lean=float(g.get("速度寄せ") or 0.0),
                    floor_adj=float(g.get("床調整") or 0.0))


def to_cards(names=None):
    """武将名のリストから field.Card を作る。名前を省くと全部を返す。

    **CSV が一次、設計式は生成器。** 能力値は CSV の 武力・知力・能力値コスト を
    そのまま渡す。手で1枚だけ調整したいときは CSV を直せばよく、全体を引き直したい
    ときは `regenerate()` を走らせる。CSV と設計式が食い違っていないかは
    `check()` が見る。
    """
    from . import field as F
    idx = {g["名前"]: g for g in generals()}
    if names:
        # **無い名前は全部まとめて出す。** KeyError を1件ずつ潰させると、札の
        # 入れ替えで名指しリスト（実況の題材など）を直すのに何往復もかかる。
        miss = [n for n in names if n not in idx]
        if miss:
            raise KeyError("武将が見つからない: " + "、".join(miss))
    picks = [idx[n] for n in names] if names else list(idx.values())
    return [F.Card(
        cost=float(g["コスト"]),
        stat_cost=float(g["能力値コスト"]),
        typ=TYPE_MAP[g["兵種"]], role=ROLE_MAP[g["役割"]], name=g["名前"],
        trait=g["固有特性"], faction=g["勢力"], quote=g.get("台詞", ""),
        might=float(g["武力"]), wits=float(g["知力"]), skill=g["必殺技"],
        lean=float(g.get("役割寄せ") or 0.0),
        def_lean=float(g.get("防御寄せ") or 0.0),
        spd_lean=float(g.get("速度寄せ") or 0.0),
        floor_adj=float(g.get("床調整") or 0.0),
        spear=bool((g.get("槍") or "").strip()),
        gauge_cost=float(g["消費ゲージ%"]),
        # 気勢は知力とは独立の項目なので設計式で潰さない（§7.7）。
        gauge_rate=float(g["ゲージ上昇率"]) / 100.0,
        gauge_init=float(g["初期ゲージ"])) for g in picks]


def on_curve() -> int:
    """実カードがコスト曲線に乗っているか検算する。

    §13「新しい指標を作ったら、まず完全に均衡していたら何が出るかを計算する」。
    ここでの正解は「盤面での総合値 ÷ 設計式の総合値 が全コスト帯で 1.000」である。
    ずれていたら、CSV の別スケールがどこかから漏れて入っている。
    """
    from . import design as D
    from . import field as F
    form = F.Formation(n_front=3)
    idx = {g["名前"]: g for g in generals()}
    cards = to_cards()
    bad = 0
    print("実カードがコスト曲線に乗っているか（比 1.0000 が正解）")
    print("  {:<6}{:>6}{:>10}{:>10}{:>10}{:>10}".format(
        "コスト", "枚数", "盤面", "設計式", "比", "最大ずれ%"))
    for c in sorted({int(x.cost) for x in cards}):
        ps, ds = [], []
        for x in (x for x in cards if int(x.cost) == c):
            u = F.Unit(0, x, form, (0.0, 0.0), True)
            # **定義は design.total_value に一本化。** ここに式を書き写して
            # いたせいで、設計式を直したときに 8.7倍ずれた（実際に踏んだ）。
            # 鎧は Unit が実際に着ている値で数える（防御寄せ・§7.66）。
            ps.append(D.total_value(u.men0, u.atk, x.typ, u.dfn))
            ds.append(D.derive(to_design(idx[x.name]))["総合値"])
        dev = max(abs(p / d - 1.0) for p, d in zip(ps, ds)) * 100
        print("  {:<6}{:>6}{:>10.2f}{:>10.2f}{:>10.4f}{:>10.2f}{}".format(
            c, len(ps), st.mean(ps), st.mean(ds),
            st.mean(p / d for p, d in zip(ps, ds)), dev,
            "  ★" if dev > 0.5 else ""))
        if dev > 0.5:
            bad += 1
    print("\n" + ("乗っていない帯が {} 件".format(bad) if bad else "全帯がコスト曲線の上"))
    return bad


# ============================================================================
# CSV の書き出し（能力値を盤面が実際に使う値へ置き換える）
# ============================================================================

# 書き出す列。**盤面が読まない列は置かない。**
# 旧 CSV には 命中率・クリ率 があったが、位置ベースの盤面はどちらも持っていない
# （§7.8: 命中率 -X% は攻撃力 -X% と同じ意味になる）。数字が載っていて効かない列は
# 「実力比% を再現できるか」の検算まで通ってしまうので、質が悪い。落とす。
COLUMNS = ["名前", "人物", "字号", "勢力", "コスト", "帯", "兵種", "役割",
           "兵力", "武力", "知力", "攻撃力", "防御力",
           "必殺技", "消費ゲージ%", "ゲージ上昇率", "初期ゲージ", "固有特性",
           "知力傾き", "効果予算", "能力値コスト", "総合値", "実力比%",
           # 演出列（§7.47・§9.4）。盤面は読まないが**表示の一次データ**なので
           # 書き出しで落とさない。⑤の書き戻しで一度落として Web の武将一覧を
           # 壊した（KeyError: 武勇）。extrasaction="ignore" は列を黙って捨てる。
           "台詞", "武勇", "知略",
           # 役割寄せ（§7.56）・槍（§7.57）・防御寄せ/速度寄せ（§7.66）。
           # 設計の入力（authored）なので落とさない。
           "役割寄せ", "槍", "防御寄せ", "速度寄せ", "床調整"]


def regenerate() -> int:
    """`sim/data/generals.csv` の能力値を設計式で引き直して上書きする。

    **読んだとおりに動くシートにするのが目的。** これまで CSV の 兵力・攻撃力 は
    旧ルール（レーンあり）の内部スケールで、`to_cards()` は一切読んでいなかった。
    シートを見た人が把握する強さと、盤面が実行する強さが別物だった。

    置き換えたあとは、**CSV が一次で設計式は生成器**になる。手で1枚だけ直したい
    ときは CSV を直せばよく、全体を引き直したいときはこれを走らせる。
    """
    from . import design as D
    from . import field as F
    load_skills_into_field()
    load_traits_into_field()
    rows = generals()
    out = []
    for g in rows:
        d = to_design(g)
        v = D.derive(d)
        r = {k: g.get(k, "") for k in COLUMNS if k in g}
        r.update({
            "兵力": "{:.0f}".format(v["兵力"]),
            "武力": "{:.1f}".format(v["武力"]),
            "知力": "{:.1f}".format(v["知力"]),
            "攻撃力": "{:.1f}".format(v["攻撃力"]),
            "防御力": "{:.1f}".format(v["防御力"]),
            "知力傾き": "{:.3f}".format(D.tilt_k(d.tilt)),
            "効果予算": "{:.3f}".format(v["効果予算"]),
            "能力値コスト": "{:.3f}".format(d.cost - v["効果予算"]),
            "総合値": "{:.4f}".format(v["総合値"]),
        })
        out.append(r)
    mean = st.mean(float(r["総合値"]) for r in out)
    for r in out:
        r["実力比%"] = "{:.0f}".format(float(r["総合値"]) / mean * 100.0)
    path = os.path.join(DATA, "generals.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    return len(out)


# ============================================================================
# 同期（カードを増やしたら、まずこれを走らせる）
# ============================================================================
#
# **手で書く列と、計算で決まる列を分ける。** 分けないと1枚足すたびに13列の導出値と
# 他ファイルの集計を手で合わせることになり、枚数が増えるほど破綻する。
#
#   手で書く（設計者の選択）
#     generals.csv  名前 人物 字号 勢力 コスト 兵種 役割 必殺技 固有特性
#                   知力傾き ゲージ上昇率
#     skills.csv    技名 武将 対象 効果
#     traits.csv    キー 名前 型 効果 備考
#
#   `sync()` が決める（手で触らない）
#     generals.csv  帯 兵力 武力 知力 攻撃力 防御力 消費ゲージ% 初期ゲージ
#                   効果予算 能力値コスト 総合値 実力比%
#     skills.csv    コスト 消費ゲージ% 効果数
#     traits.csv    採用枚数 持つ武将
#
# 導出値が空でも読めるようにしてあるので（`_num`）、**新しい札は手で書く列だけ
# 埋めて `sync()` を走らせればよい**。
BAND_OF = ((3.0, "低"), (6.0, "中"), (99.0, "高"))


def band_of(cost: float) -> str:
    for hi, name in BAND_OF:
        if cost <= hi:
            return name
    return BAND_OF[-1][1]


def _effect_count(text: str) -> int:
    """効果文に入っている効果の数。区切りは**前後に空白のある「 + 」だけ**。

    `\s*\+\s*` で切ってはいけない。「攻撃力 +9%（41秒）」の符号の + まで区切りに
    見えて、1つの効果が2つに数えられる（実際に数えた）。
    """
    return len([x for x in (text or "").split(" + ") if x.strip()])


def sync() -> Dict[str, int]:
    """導出値をすべて引き直す。**カードを増やしたら最初にこれ。**

    generals.csv だけでなく skills.csv・traits.csv の集計列も直す。片方だけ直すと
    `check()` が「必殺技とコストが不一致」「固有特性の採用枚数が不一致」を出すが、
    それはデータの誤りではなく**同期していないだけ**なので、人手で追わせない。

    戻り値は書き直した行数（generals / skills / traits）。
    """
    from . import design as D
    load_skills_into_field()
    load_traits_into_field()

    G = generals()
    owner = {g["必殺技"]: g for g in G if g.get("必殺技")}

    # --- skills.csv: コスト・消費ゲージ%・効果数 は武将側と段から決まる -------
    S = skills()
    for r in S:
        g = owner.get(r["技名"])
        if g is not None:
            r["コスト"] = g["コスト"]
        r["効果数"] = str(_effect_count(r.get("効果", "")))
        sk = _parse_for_tier(r)
        if sk is not None:
            gc, _gi = D.GAUGE_TIER[tier_of(sk)]
            r["消費ゲージ%"] = "{:.0f}".format(gc)
    _write("skills.csv", S)
    load_skills_into_field()

    # --- generals.csv: 帯・ゲージ・能力値一式 -------------------------------
    smap = {r["技名"]: r for r in S}
    for g in G:
        g["帯"] = band_of(float(g["コスト"]))
        r = smap.get(g.get("必殺技", ""))
        if r is not None:
            sk = _parse_for_tier(r)
            if sk is not None:
                gc, gi = D.GAUGE_TIER[tier_of(sk)]
                g["消費ゲージ%"] = "{:.0f}".format(gc)
                g["初期ゲージ"] = "{:.0f}".format(gi)
        g.setdefault("ゲージ上昇率", "100")
        if not str(g.get("ゲージ上昇率", "")).strip():
            g["ゲージ上昇率"] = "100"
    _write("generals.csv", G, COLUMNS)
    n_g = regenerate()

    # --- traits.csv: 採用枚数・持つ武将 は武将側から数える -------------------
    G = generals()
    T = traits()
    cnt = Counter(g.get("固有特性", "") for g in G)
    who: Dict[str, List[str]] = {}
    for g in G:
        k = g.get("固有特性", "")
        if k:
            who.setdefault(k, []).append(g["名前"])
    for r in T:
        k = r["キー"]
        r["採用枚数"] = str(cnt.get(k, 0))
        r["持つ武将"] = "、".join(who.get(k, []))
    _write("traits.csv", T)
    return {"generals": n_g, "skills": len(S), "traits": len(T)}


def _parse_for_tier(r: Dict[str, str]):
    from . import field as F
    try:
        return F._parse_skill(r.get("効果", ""), r.get("対象", ""))
    except Exception:
        return None


def _write(name: str, rows: List[Dict[str, str]], cols=None) -> None:
    """CSV を書き戻す。**列の順序は元のファイルを正とする**（勝手に並べ替えない）。"""
    if not rows:
        return
    if cols is None:
        cols = list(rows[0].keys())
    path = os.path.join(DATA, name)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})


def _retire_gauge(text: str) -> str:
    """ゲージを操る効果を、値段の付く普通の効果へ差し替える（§7.18）。

    **ゲージ軸は価値を運べない**というのが実測の結論である。1戦の発動が1〜2回しか
    ない設計では、ゲージは資源ではなく**日程**であり、

    - 付与（ゲージを直接進める）は段差になる。味方の閾値を越えれば丸ごと1回ぶん、
      越えなければ 0。実測で消費の 2/8/16% は発動を1回も増やさず、40% で突然
      2回になった（+2.4コスト点）。崖の上には置かない（§13）。
    - 気勢（溜まる速さの率）は**負の値段**になる。実測 -0.049（+10%・30秒）〜
      -0.167（+40%）。秒数を倍にしても値は動かない（30秒と60秒が同値）ので、
      効いているのは「発動が早まる」ことだけである。大技を早く撃たせると、相手が
      まだ健在なぶん取りこぼす。**支援のつもりが妨害になっている。**

    そこで、攻撃力の強化へ移す。量は `rebalance_std` が値段に合わせて解く。
    """
    m = re.search(r"ゲージ付与\s*自然増加の(\d+)秒ぶん", text)
    if not m:
        return text
    return re.sub(r"ゲージ付与\s*自然増加の\d+秒ぶん",
                  "攻撃力 +{:.0f}%（20秒）".format(float(m.group(1))), text)


def tier_of(skill) -> str:
    """必殺技をゲージの段へ割り当てる（`design.GAUGE_TIER`）。

    **中身で決める。** 打撃は「1発を大きく、遅く、1回だけ」にしたいので大技へ。
    強化・妨害・回復は掛かっている時間が価値なので標準へ。ゲージ付与だけの技は
    早く何度も回らないと意味がないので手数へ。
    """
    if skill.power > 0.0:
        return "大技"
    if skill.heal > 0.0:
        return "標準"
    # ゲージ付与は**手数の段に置いてはいけない**。安く何度も撃てる札が高い札の
    # ゲージを進めると、進めたぶんがまた技になって返ってくる。実測で「消費の40%を
    # 味方全体へ」を手数の段（4回）に置くと 5.80コスト点、80%なら 21.8 まで飛んだ。
    # 標準（2回）に置き、割合を小さく持つ。
    return "標準"


def _scale_effect(text: str, m: float) -> str:
    """効果文の量を m 倍する。**％は50で頭打ちにし、あふれた分は秒数へ回す。**

    §6.5 が「1つの能力への補正合計は ±50%」と決めているので、%だけを伸ばすと
    上限に当たって予算が消える。%×秒 を保てば価値は同じである。
    """
    def pw(mo):
        return "威力{:.0f}%".format(float(mo.group(1)) * m)

    def hp(mo):
        return "回復 攻撃力の{:.0f}%".format(float(mo.group(1)) * m)

    def stun(mo):
        # 行動阻害も同名では重ならない。秒数で払う（もともと秒数の効果なので同じ）。
        return "行動阻害 {:.0f}秒".format(float(mo.group(1)) * m)

    def gauge(mo):
        return "ゲージ付与 自然増加の{:.0f}秒ぶん".format(float(mo.group(1)) * m)

    def kisei(mo):
        return "気勢 {}{:.0f}%（{:.0f}秒）".format(
            mo.group(1), float(mo.group(2)), float(mo.group(3)) * m)

    def chaos(mo):
        # 混乱も**秒数で払う**。理由は下の mod と同じ（同名は重ならず大きい方が
        # 残る）ことに加えて、値段が量に対して線形でないため。混乱の値段は
        # `design.chaos_equiv` を通してから線形になるので、量を m 倍しても
        # 価値は m 倍にならない。秒数なら値段はそのまま比例する。
        return "混乱 {:g}%（{:.0f}秒）".format(float(mo.group(1)),
                                             float(mo.group(2)) * m)

    def mod(mo):
        # **量ではなく秒数を伸ばす。** §6.5 の同名規則で、同じ技を何度撃っても
        # 効果は重ならず「大きい方」だけが残る。だから回数を減らしたぶん量を
        # 増やすと二重取りになる（実測で堅忍 +0.28・一喝 +0.33 コスト点ぶん強く
        # なった）。価値は「どれだけの時間かかっていたか」なので、秒数で払う。
        stat, sign, val, sec = (mo.group(1), mo.group(2),
                                float(mo.group(3)), float(mo.group(4)))
        return "{} {}{:.0f}%（{:.0f}秒）".format(stat, sign, val, sec * m)

    text = re.sub(r"威力(\d+)%", pw, text)
    text = re.sub(r"回復\s*攻撃力の(\d+)%", hp, text)
    text = re.sub(r"行動阻害\s*(\d+)秒", stun, text)
    text = re.sub(r"ゲージ付与\s*自然増加の(\d+)秒ぶん", gauge, text)
    text = re.sub(r"気勢\s*([+-])(\d+)%（(\d+)秒）", kisei, text)
    text = re.sub(r"混乱\s*(\d+(?:\.\d+)?)%（(\d+)秒）", chaos, text)
    text = re.sub(r"(攻撃力|命中率|防御力|移動速度)\s*([+-])(\d+)%（(\d+)秒）",
                  mod, text)
    return text


def retier() -> int:
    """必殺技をゲージの段へ割り当て直し、威力を予算が変わらないように直す。

    **予算 = 発動回数 × 効果量 を保つ。** 回数が半分になるなら効果量を倍にする。
    変わるのは刻みだけで、技の総価値は動かない。

    これをやる理由は見せ方である。1発が相手の 1〜6% しか削らないと、実況で
    「呂布〔飛将〕、無双乱舞。敵3隊に299人の損害。」と出て名前負けする（§7.12）。
    """
    from . import design as D
    from . import field as F
    load_skills_into_field()
    rows = skills()
    gs = {g["必殺技"]: g for g in generals()}
    n = 0
    for r in rows:
        # 旧「ゲージ付与」を気勢へ移してから段を決める（§7.18）
        r["効果"] = _retire_gauge(r["効果"])
        sk = F._parse_skill(r["効果"], r["対象"])
        F.SKILL_INFO[r["技名"]] = sk
        t = tier_of(sk)
        gc, gi = D.GAUGE_TIER[t]
        old_w = D.tier_weight(float(r["消費ゲージ%"]))
        new_w = D.tier_weight(gc, gi)
        m = max(old_w, 1e-6) / max(new_w, 1e-6)
        r["効果"] = _scale_effect(r["効果"], m)
        r["消費ゲージ%"] = "{:.0f}".format(gc)
        n += 1
    with open(os.path.join(DATA, "skills.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # 武将側の消費ゲージ・初期ゲージも段に合わせる
    load_skills_into_field()
    gen = generals()
    for g in gen:
        t = tier_of(F.SKILL_INFO[g["必殺技"]])
        gc, gi = D.GAUGE_TIER[t]
        g["消費ゲージ%"] = "{:.0f}".format(gc)
        g["初期ゲージ"] = "{:.0f}".format(gi)
    with open(os.path.join(DATA, "generals.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(gen[0].keys()))
        w.writeheader()
        w.writerows(gen)
    return n


# 大技の目標値段（コスト点）。**枠の基礎価値 + コスト比例分**の形にする（§4.6 と同じ）。
# コスト比例だけにすると、コスト1の技が 0.074 まで削られて「安い札の個性は技」という
# 性格が消える。基礎価値を置くと、安い札は今の値段のまま、高い札ほど技が主役になる。
#
#   コスト1  0.27   コスト5  0.75   コスト10  1.35
#
# 入れ替え前は 0.29〜1.07 とコストにほとんど乗っていなかった（呂布の無双乱舞が
# 0.191 で、関羽の青龍偃月 0.421 の半分以下だった）。
BIG_BASE = 0.15
BIG_SLOPE = 0.12


def _skill_target_value(g: Dict[str, str], cost: float) -> float:
    """その札の必殺技に持たせたい値段（コスト点）。

    **固有特性のぶんを先に引く。** 特性は札ごとに固定のデータなので、あふれたときに
    譲れるのは技の側しかない。陣頭（1.35コスト点）を持つコスト3の札は、上限
    （0.45×3 ＋ 許容0.5 ＝ 1.85）のうち技に回せるのが 0.50 しかない。
    ここを見ないと、引き直したあとに予算超過が出る（実測で陳到が +0.51 で超えた）。
    """
    from . import design as D
    # 許容ぎりぎりに置くと、効果文を整数へ丸めたぶんで超える（実測 +0.51）。
    # 少し余裕を残す。
    room = (D.EFFECT_CAP * cost + D.EFFECT_OVER_OK * 0.85
            - D.trait_value(g["固有特性"]))
    return max(min(BIG_BASE + BIG_SLOPE * cost, room), 0.0)


def rebalance_big() -> int:
    """大技40種の威力を、人物の格（コスト）に見合う値へ引き直す。

    **予算は自動で釣り合う。** 威力を上げたぶん効果予算が増え、その分だけ能力値が
    下がる（§7.8）。強さは動かず、技と能力値の配分だけが変わる。

    値段は威力について線形なので、状態効果ぶんを引いてから割れば解ける。
    """
    from . import design as D
    from . import field as F
    load_skills_into_field()
    rows = skills()
    G = {g["必殺技"]: g for g in generals()}
    gc, gi = D.GAUGE_TIER["大技"]
    fr = D.tier_weight(gc, gi)
    n = 0
    for r in rows:
        name = r["技名"]
        sk = F.SKILL_INFO[name]
        if tier_of(sk) != "大技" or name not in G:
            continue
        cost = float(G[name]["コスト"])
        target = _skill_target_value(G[name], cost)
        tc = D.tilt_coef(sk.kind, TYPE_MAP[G[name]["兵種"]], tilt_of(G[name]))
        nt = D.target_n(r["対象"])
        # 状態効果ぶん（威力とは無関係な部分）を先に引く
        mods = 0.0
        for k, a, sec in sk.mods:
            if k == "stun":
                mods += D.EFFECT_PRICE["stun"] * sec * nt
            elif k in ("atk", "def"):
                mods += D.EFFECT_PRICE[k] * abs(a) * 100.0 * sec * nt
        want = target * D.CARD_COST_RATE / max(fr, 1e-9) - mods
        if sk.dur > 0.0:            # 継続。威力は毎秒の量
            power = want / D.EFFECT_PRICE["dot"] / sk.dur / max(tc, 1e-6)
        else:                       # 打ち切りは威力に線形
            power = want / D.EFFECT_PRICE["damage"] / max(tc, 1e-6)
        # 下限。**継続と打ち切りで桁が違う**（継続の威力は毎秒の量なので、
        # 威力50%×13秒 は総威力650%にあたる）。打ち切りと同じ 0.5 を当てると
        # 継続系がすべて下限に張り付き、目標より 0.34〜0.49 コスト点も高く出ていた。
        power = max(power, 0.05 if sk.dur > 0.0 else 0.5)
        r["効果"] = re.sub(r"威力\d+%", "威力{:.0f}%".format(power * 100.0),
                          r["効果"], count=1)
        n += 1
    with open(os.path.join(DATA, "skills.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return n


def rebalance_std() -> int:
    """標準の段（強化・妨害・回復）の効果量を、大技と同じ線へ乗せる。

    段ごとに別の線を引くと、段をまたいだところで値段が不連続になる。技の値段は
    **段によらず 0.15 + 0.12 × コスト** に揃える。

    解くのは秒数（と回復量）。**％ではなく秒数で払う**のは §7.13 と同じ理由で、
    §6.5 の ±50% 上限に当たると量を増やしても価値が増えないためである。
    """
    from . import design as D
    from . import field as F
    load_skills_into_field()
    rows = skills()
    G = {g["必殺技"]: g for g in generals()}
    gc, gi = D.GAUGE_TIER["標準"]
    n = 0
    for r in rows:
        name = r["技名"]
        sk = F.SKILL_INFO[name]
        if tier_of(sk) != "標準" or name not in G:
            continue
        cost = float(G[name]["コスト"])
        target = _skill_target_value(G[name], cost)
        cur = D.effect_value(sk, r["対象"], gc, gi,
                             typ=TYPE_MAP[G[name]["兵種"]], tilt=tilt_of(G[name]))
        if cur <= 1e-9:
            continue
        m = target / cur
        r["効果"] = _scale_effect(r["効果"], m)
        n += 1
    with open(os.path.join(DATA, "skills.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return n


def retire_gauge_traits() -> int:
    """固有特性のゲージ付与も普通の効果へ差し替える（§7.18）。号令・弔い合戦・呼応。"""
    rows = traits()
    n = 0
    for t in rows:
        new = _retire_gauge(t["効果"])
        if new != t["効果"]:
            t["効果"] = new
            n += 1
    with open(os.path.join(DATA, "traits.csv"), "w",
              encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return n


def load_traits_into_field() -> int:
    """固有特性を field.py へ読み込む。**必殺技とまったく同じ器を使う。**

    CSV の効果文が同じ文法（「攻撃力 +12%（20秒）」「回復 攻撃力の130%」
    「ゲージ付与 自然増加の2秒ぶん」）なので、専用の表を持つ理由が無い。
    備考から発動条件・対象・回数上限を取り出す。

    以前 field.py は6種だけをハードコードしていて、**残り13種は読み込むだけで
    何もしていなかった**（採用枚数で数えると80枚中47枚が無効の特性を持っていた）。
    """
    from . import field as F
    F.TRAITS.clear()
    n = 0
    for t in traits():
        if t["型"] != "誘発":
            continue            # 常在型は field.py 側で個別に扱う
        note = t["備考"]
        m = re.search(r"(\w+) で発動", note)
        cond = m.group(1) if m else ""
        m = re.search(r"対象 ([^/]+)", note)
        target = m.group(1).strip() if m else "自分"
        m = re.search(r"1戦(\d+)回", note)
        cap = int(m.group(1)) if m else 1
        F.TRAITS[t["キー"]] = (cond, target, cap,
                               F._parse_skill(t["効果"], target), t["名前"])
        n += 1
    return n


def load_skills_into_field() -> int:
    """必殺技の威力・種別・対象を field.py へ読み込む。"""
    from . import field as F
    n = 0
    for sk in skills():
        F.SKILL_INFO[sk["技名"]] = F._parse_skill(sk["効果"], sk["対象"])
        F.SKILL_TARGET[sk["技名"]] = sk["対象"]
        n += 1
    return n


def by_cost(cost: int):
    return [g["名前"] for g in generals() if int(g["コスト"]) == cost]


if __name__ == "__main__":
    import sys as _sys
    # `sync` … カードを増やしたら最初にこれ。導出値と集計列を全部引き直す。
    # 引数なし … 検算だけ（データは書き換えない）。
    if len(_sys.argv) > 1 and _sys.argv[1] == "sync":
        n = sync()
        print("同期した: 武将{generals}枚 / 必殺技{skills}種 / 固有特性{traits}種"
              .format(**n))
        raise SystemExit(1 if _main() else 0)
    raise SystemExit(1 if _main() else 0)
