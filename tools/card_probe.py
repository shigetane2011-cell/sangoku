# -*- coding: utf-8 -*-
"""**値札を払う前の「盤面での値打ち」**を測る（案の下調べ用・§7.248）。

`card_before_after.py` は CSV を書き換えて `sync` を通した**採用版**どうしを比べる。
だから測れるのは「請求を払ったあとの正味」で、**案を考えている段階では遅い** ——
兵法を大きくすれば効果予算が増え、そのぶん身体が痩せるので、
**器そのものが盤面でいくら返すのか**が見えない（§7.246 ④・落とし穴69）。

この計器は逆で、**Card を組み直すだけ**（CSV も sync も通さない）。だから

    正味 = ここで出る「盤面の値打ち」 − （請求の増分 × その札の1コスト点の勝率）

の**第1項だけ**を測る。第2項は `design.effect_value` と床調整の実測レートから
別に引く。2つを分けておくと、「器が良いのか、ただ予算を多く使っただけか」が
切り分けられる。

土台・相手・種は `card_before_after.py` と同じ（既定: 官渡30・魏の安い5枚・
12性格×N種・同じ種でペア）。勝率と**残存差**の両方を出す —— 10コストの札は
勝率が 93〜98% に張り付いて差が潰れるので、**裁定は残存差で行うこと**。

    python3 tools/card_probe.py spec.json

spec（JSON）:

    {"seeds": 20, "workers": 4,
     "cap_name": "官渡", "cap": 30.0,          // 省略可
     "base": ["文聘〔江夏〕", ...],             // 省略可（5枚）
     "routs": [0.30, 0.0],                    // 省略可。敗走線を差し替えて測る
     "variants": [
       {"card": "関羽〔漢寿亭侯〕", "label": "いま"},               // 1本目が基準
       {"card": "関羽〔漢寿亭侯〕", "label": "A 火力2倍",
        "patch": {"効果": "ダメージ 威力1200%", "対象": "敵1列"}},  // 兵法を差し替え
       {"card": "関羽〔漢寿亭侯〕", "label": "B 兵力+10%",
        "patch": {"floor_adj": 0.30}}                             // Card の欄を直接
     ]}

`patch` の「効果」「対象」は兵法の差し替え（別名で `SKILL_INFO` へ登録する）。
それ以外の鍵は `field.Card` の欄へそのまま入る（floor_adj・lean・gauge_cost など）。
"""
import json
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

BASE = ["文聘〔江夏〕", "曹仁〔堅守〕", "郝昭〔陳倉〕", "李典〔慎重〕", "満寵〔剛毅〕"]


def _run(job):
    name, label, patch, opt, rout = job
    import dataclasses
    from sim import rosterdata as R, field as F, match as M, dummies as D
    F.TRAITS.clear()
    R.load_traits_into_field()
    R.load_skills_into_field()
    F.TRAITS_ON = True
    if rout is not None:
        # 敗走線（ROUT_RATIO）を差し替えて測る口。**子プロセスの中だけ**で書き換える
        # ので本番には漏れない（Pool は maxtasksperchild=1 で使い捨て）。
        F.ROUT_RATIO = rout
    roster = M._roster_cards()
    cards = {c.name: c for c in roster}
    c = cards[name]
    if patch:
        patch = dict(patch)
        eff, tgt = patch.pop("効果", None), patch.pop("対象", None)
        if eff is not None or tgt is not None:
            alias = "{}|{}".format(c.skill, label)
            tgt = tgt or F.SKILL_TARGET[c.skill]
            F.SKILL_INFO[alias] = F._parse_skill(eff if eff is not None
                                                 else "", tgt)
            F.SKILL_TARGET[alias] = tgt
            patch["skill"] = alias
        c = dataclasses.replace(c, **patch)
        cards[name] = c
    base = [cards[n] for n in opt["base"]]
    rear = (c.typ == F.ARC or (c.typ == F.INF and c.spear))
    order = base + [c] if rear else [c] + base[1:3] + [base[0]] + base[3:]
    army = F.Army(tuple(order), F.FORM_STANDARD)
    errs = M.placement_errors(army)
    if errs:
        raise SystemExit("置けない並び（{}）: {}".format(name, "、".join(errs)))
    cap = (opt["cap_name"], opt["cap"])
    out = {}
    for persona in D.PERSONAS:
        for seed in range(opt["seeds"]):
            opp = D.make_entry(roster, persona, seed, caps=(cap,)).units[0]
            r = F.simulate(army, opp, dt=0.25, seed=seed * 7 + 1)
            me = [row for row in r["dealt_a"] if row[0] == name][0]
            out[(persona.name, seed)] = (r["score"], r["diff"],
                                         me[3] / max(me[4], 1e-9), me[2])
    return (label, rout), out


def _col(a, b, ks, i):
    """基準 a と案 b の差（平均と標準誤差）。i は out のタプルの位置。"""
    d = [b[k][i] - a[k][i] for k in ks]
    return (statistics.mean(a[k][i] for k in ks), statistics.mean(d),
            statistics.pstdev(d) / max(1, len(d) - 1) ** 0.5)


def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    opt = dict(seeds=spec.get("seeds", 20), base=spec.get("base", BASE),
               cap_name=spec.get("cap_name", "官渡"), cap=spec.get("cap", 30.0))
    routs = spec.get("routs", [None])
    jobs = [(v["card"], v["label"], v.get("patch"), opt, r)
            for v in spec["variants"] for r in routs]
    with Pool(spec.get("workers", 4), maxtasksperchild=1) as p:
        got = dict(p.imap_unordered(_run, jobs))
    print("※ 単戦（性格パネル・dt 0.25・片側）。**値札は払っていない**"
          " — 正味は請求の増分を別に引くこと（{}{:g}・12性格×{}種）".format(
              opt["cap_name"], opt["cap"], opt["seeds"]))
    print("{:<32}{:>7}{:>8}{:>8}{:>9}{:>9}{:>8}{:>8}{:>8}".format(
        "案", "敗走線", "勝率", "Δ勝率", "残存差", "Δ残存差", "±SE", "残存%", "与ダメ"))
    for rout in routs:
        ref = None
        for v in spec["variants"]:
            o = got[(v["label"], rout)]
            ks = sorted(o)
            if ref is None:
                ref = o
            w0, wm, _ = _col(ref, o, ks, 0)
            d0, dm, dse = _col(ref, o, ks, 1)
            print("{:<32}{:>7}{:>8.1%}{:>+8.2%}{:>9.4f}{:>+9.4f}{:>8.4f}"
                  "{:>8.1%}{:>8.0f}".format(
                      v["label"],
                      "—" if rout is None else ("なし" if not rout
                                                else "{:.0%}".format(rout)),
                      statistics.mean(o[k][0] for k in ks), wm,
                      statistics.mean(o[k][1] for k in ks), dm, dse,
                      statistics.mean(o[k][2] for k in ks),
                      statistics.mean(o[k][3] for k in ks)))
        print()


if __name__ == "__main__":
    main()
