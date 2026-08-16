# -*- coding: utf-8 -*-
"""sim/data/*.csv（武将80枚・必殺技80種・固有特性19種）の読み込みと検算。

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


def power(g: Dict[str, str]) -> float:
    """総合値 = 実効耐久 × 実効火力（§4.6）。

    実効耐久 = 兵力 × (100 + 防御力) / 100
    実効火力 = 攻撃力 × 命中率 × クリティカル期待値 / 攻撃間隔

    §6.1「強さに効く数値をひとつでも式から落とすと、その値が高い兵種が予算外の
    優位を持つ」。命中・クリ・攻撃間隔を必ず入れる。
    """
    men = float(g["兵力"])
    atk = float(g["攻撃力"])
    dfn = float(g["防御力"])
    hit = float(g["命中率"]) / 100.0
    crit = float(g["クリ率"]) / 100.0
    dur = men * (100.0 + dfn) / 100.0
    fire = atk * hit * (1.0 + 0.5 * crit) / INTERVAL[g["兵種"]]
    return dur * fire / 1e5


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
    miss_t = sorted({g["固有特性"] for g in G if g["固有特性"] not in tk})
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

    # --- 実力比% が能力値から再現できるか ---------------------------------
    k = st.mean(float(g["実力比%"]) / g["_power"] for g in G)
    err = [abs(float(g["実力比%"]) - k * g["_power"]) / float(g["実力比%"]) * 100
           for g in G]
    print(f"  実力比% ≒ 総合値 × {k:.2f}  （最大誤差 {max(err):.2f}% / "
          f"平均 {st.mean(err):.2f}%）")
    if max(err) > 10.0:
        print("    ★ 実力比% が能力値から再現できない。列か式のどちらかが誤り")
        bad += 1

    # --- §4.6 の ±8% -----------------------------------------------------
    print("  コスト帯ごとの ばらつき（§4.6 は ±8%以内）")
    for c in sorted({g["_cost"] for g in G}):
        v = [g["_power"] / c for g in G if g["_cost"] == c]
        m = st.mean(v)
        dev = max(abs(x - m) / m * 100 for x in v)
        mark = "  ★超過" if dev > 8.0 else ""
        print(f"    コスト{c:<3} {len(v)}枚  ±{dev:5.1f}%{mark}")
        if dev > 8.0:
            bad += 1

    # --- 配分によらず総価値が等しいか（設計の眼目） ------------------------
    A, B = affine_fit(G)
    print(f"  総合値 ≒ {A:.3f} + {B:.4f} × コスト  "
          f"（枠の基礎価値 + コスト比例分）")
    by = {}
    for c in sorted({g["_cost"] for g in G}):
        by[c] = st.mean(g["_power"] for g in G if g["_cost"] == c)
    plans = [("均等 5×6", [5] * 6), ("やや偏り 6/6/6/4/4/4", [6, 6, 6, 4, 4, 4]),
             ("偏り 7/7/4/4/4/4", [7, 7, 4, 4, 4, 4]),
             ("極端 10/10/4/2/2/2", [10, 10, 4, 2, 2, 2]),
             ("極端 10/9/8/1/1/1", [10, 9, 8, 1, 1, 1])]
    print("  6枠・合計コスト30 での総価値（配分によらず等しいのが設計の狙い）")
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
# 知力の傾き（sim/design.py の WITS_TILT のキー）
# ---------------------------------------------------------------------------
# CSV に知力の列が無いので、ここで補う。**傾きは総合値を動かさない**（design.py の
# 検算[1]）ので、値付けには一切効かない。効くのは必殺技の係数だけである。
#
# 既定は必殺技の種別から弱めに置く。計略で撃つ札は知力が乗るので知力寄り、
# 武技で撃つ札は武力寄り。強い傾き（智将・勇将）は**人物として明らかな札にだけ**
# 手で置く。残りは設計者が後から埋める前提の暫定値である。
#
# 以前ここにあった WITS_BY_ROLE（支援1.45・火力0.70…）は廃棄した。あれは役割から
# 知力を機械的に導く根拠のない placeholder で、しかも武力の側を解き直していなかった
# ため、**役割ごとに総合値が -12.3% 〜 +15.8% ずれていた**（実測）。
TILT_BY_SKILL = {"scheme": "才幹", "area": "中庸", "melee": "武辺"}

TILT_BY_NAME = {
    "諸葛亮": "智将", "司馬懿": "智将", "周瑜": "智将", "郭嘉": "智将",
    "賈詡": "智将", "陸遜": "智将", "荀彧": "智将", "龐統": "智将",
    "呂布": "勇将", "関羽": "勇将", "張飛": "勇将", "許褚": "勇将",
    "典韋": "勇将", "馬超": "勇将", "顔良": "勇将", "文醜": "勇将",
}


def tilt_of(g: Dict[str, str]) -> str:
    """1枚の知力の傾きを決める。人物指定が最優先、無ければ必殺技の種別から。"""
    from . import field as F
    t = TILT_BY_NAME.get(g.get("人物", ""))
    if t:
        return t
    sk = {s["技名"]: s for s in skills()}.get(g["必殺技"])
    if not sk:
        return "中庸"
    return TILT_BY_SKILL.get(F._skill_kind(sk["効果"], sk["対象"]), "中庸")


def _skill_target(name: str) -> str:
    for s in skills():
        if s["技名"] == name:
            return s["対象"]
    return ""


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
    eff = D.effect_value(sk, _skill_target(g["必殺技"]), gc, gi) if sk else 0.0
    eff += D.trait_value(g["固有特性"])
    return D.Design(cost=float(g["コスト"]), typ=TYPE_MAP[g["兵種"]],
                    role=g["役割"], tilt=tilt_of(g),
                    gauge_cost=gc, gauge_init=gi, effect=eff)


def to_cards(names=None):
    """武将名のリストから field.Card を作る。名前を省くと全80枚を返す。"""
    from . import design as D
    from . import field as F  # 遅延 import（rosterdata 単体でも検算できるように）
    idx = {g["名前"]: g for g in generals()}
    picks = [idx[n] for n in names] if names else list(idx.values())
    out = []
    for g in picks:
        v = D.derive(to_design(g))
        out.append(F.Card(
            cost=float(g["コスト"]),
            stat_cost=float(g["コスト"]) - v["効果予算"],
            typ=TYPE_MAP[g["兵種"]],
            role=ROLE_MAP[g["役割"]], name=g["名前"],
            trait=g["固有特性"], faction=g["勢力"],
            might=v["武力"], wits=v["知力"], skill=g["必殺技"],
            gauge_cost=float(g["消費ゲージ%"]),
            # 気勢だけは CSV の値を使う。知力とは独立の項目なので設計式で潰さない。
            gauge_rate=float(g["ゲージ上昇率"]) / 100.0,
            gauge_init=float(g["初期ゲージ"])))
    return out


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
            ps.append(u.men0 * (100.0 + u.dfn) / 100.0 * u.atk / u.interval / 1e5)
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
    raise SystemExit(1 if _main() else 0)
