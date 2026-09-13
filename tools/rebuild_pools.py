# -*- coding: utf-8 -*-
"""在野（official24・special48）を今の名簿で組み直す（§7.195）。

    official24 … 12性格 × 2人（01/02）＝ 24。**今のラダーに実際に居る顔ぶれ**
    special48  … 12性格 × 3人（11/12/13）＝ 36 ＋ 実験4型 × 3人（01〜03）＝ 12 → 48

旧版は「8性格 × 3人」で、§7.133 で足した4性格（先手・処刑・謀弩・槍陣）が
official に居なかった。作り直しのついでに**12性格を両方の集合へ**入れる。
実験4型（突騎・連弩・精鋭・雑兵）は tools/ladder_top.py の候補（常設ではない型）。
"""
import collections, json, io, sys
sys.path.insert(0, "/home/user/sangoku"); sys.path.insert(0, "/home/user/sangoku/tools")
from sim import dummies as D, field as F, match as M, rosterdata as R   # noqa: E402
import ladder_top as L                                                  # noqa: E402

FX = "/home/user/sangoku/docs/balance/fixtures-v1.json"
FORM_NAME = {v: k for k, v in D.FORM_BY_NAME.items()}


def spec(name, entry):
    armies = []
    for army, (label, cap) in zip(entry.units, M.REGULATIONS):
        armies.append(collections.OrderedDict([
            ("regulation", label), ("cap", float(cap)),
            ("formation", FORM_NAME[army.form]),
            ("total_cost", round(sum(c.cost for c in army.cards), 1)),
            ("cards", [c.name for c in army.cards])]))
    return collections.OrderedDict([("name", name), ("armies", armies)])


def main():
    R.load_skills_into_field(); R.load_traits_into_field()
    F.SKILLS_ON = F.TRAITS_ON = True
    cards = M._roster_cards()
    official, special = [], []
    for i, p in enumerate(D.PERSONAS):
        for num in (1, 2):
            official.append(spec("{}{:02d}".format(p.name, num),
                                 D.make_entry(cards, p, D.deck_seed(i, num))))
        for num in (11, 12, 13):
            special.append(spec("別{}{}".format(p.name, num),
                                D.make_entry(cards, p, D.deck_seed(i, num))))
    for j, p in enumerate(L.CANDIDATES):
        for num in (1, 2, 3):
            special.append(spec("試{}{:02d}".format(p.name, num),
                                D.make_entry(cards, p, D.deck_seed(len(D.PERSONAS) + j, num))))
    print("official {} / special {}".format(len(official), len(special)))

    fx = json.load(io.open(FX, encoding="utf-8"), object_pairs_hook=collections.OrderedDict)
    fx["pools"]["official24"]["description"] = (
        "今の名簿で組み直した在野（§7.195）。**12性格 × 2人**（sim/dummies.PERSONAS・"
        "make_entry・deck_seed の再現スナップショット）。旧版は8性格×3人で §7.133 の4性格が抜けていた")
    fx["pools"]["official24"]["entries"] = official
    fx["pools"]["special48"]["status"] = "validation"
    fx["pools"]["special48"]["description"] = (
        "今の名簿で組み直した別の引き（§7.195）。**12性格 × 3人（別・11〜13）＝36** ＋ "
        "**実験4型 × 3人（試・突騎/連弩/精鋭/雑兵。tools/ladder_top.CANDIDATES）＝12**。"
        "旧版は破陣の最終選定に使用済みで retired だったが、中身を作り直したので封は切り直し")
    fx["pools"]["special48"]["entries"] = special
    fx["source"]["pool_note"] = (
        "2026-09-07 §7.195: official24・special48 を**今の名簿で全部組み直した**。"
        "旧版は commit 8986fc8（2026-08-28）の凍結で、その後の再較正（§7.145 兵種の層・"
        "§7.151 弓・§7.154 騎兵のコスト・§7.157/§7.188 値札・§7.187 矢数・§7.190〜§7.193 回復と兵法防御）を"
        "1つも反映していなかった。**過去の勝率とは比べないこと**（テストプレイの決定「過去の数字に意味はない」）")
    json.dump(fx, io.open(FX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("REBUILD DONE")


if __name__ == "__main__":
    main()
