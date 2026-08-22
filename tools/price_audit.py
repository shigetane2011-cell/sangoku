"""全120枚の盤面残差を測る（単位＝コスト点）。

    python3 tools/price_audit.py            # 全部（4分ほど・4並列）

帳簿（CSV）と設計式の内部整合は `python3 -m sim.rosterdata` が見る。
こちらは**式そのものが盤面と合っているか**を見る別の計器。2026-08 の
監査で「一撃ダメージ威力1500%超の大技7枚だけが +3〜+6点 安売り」を
見つけた（§7.63）。値付けの式や段の重みを動かしたら、これを回し直す。

各札を「同コスト・同兵種・同役割の設計式どおりの合成カード」と入れ替えて
比べる。周囲5枠は合成4点で固定、枠の並びを回して左右を入れ替え、
反対称化した matchup_cost をとる。＋なら設計式の想定より強い。

併記する列:
  払った  … CSV の効果予算（技＋特性、ゲージ割引込み）
  素値    … ゲージ割引前の技の値段（消費100%・初期0 とみなした値）
仮説「ゲージ割引が過大」なら、残差は（素値−払った）に相関するはず。
"""
import sys, csv, json
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import field as F, match as M, design as D, rosterdata as R
from multiprocessing import Pool

# **実ゲームと同じ条件で測る。** TRAITS_ON の既定は False で、うっかり
# そのまま回すと特性持ちが「払った値段ぶんタダ働き」に見える（全120枚の
# 監査を3周、その状態でやってしまった — 陣頭族が一律 -0.7 沈んで見えた
# 正体）。対勢力（vs_魏/蜀/呉）だけは相手が合成カードなので依然空撃ち。
F.TRAITS_ON = True

CARDS = {c.name: c for c in M._roster_cards()}
ROWS = {r["名前"]: r for r in csv.DictReader(
    open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
         'sim', 'data', 'generals.csv'), encoding='utf-8-sig'))}
FILL = tuple(F._synth(4.0, F.CAV, F.BAL) for _ in range(5))
DT, ROT = 0.5, 6


def army(card):
    return F.Army((card,) + FILL, F.FORM_STANDARD)


def one(name):
    c = CARDS[name]
    base = F._synth(c.cost, c.typ, c.role)
    slope = F.cost_yardstick(DT)
    v = F.matchup_cost(army(c), army(base), slope, DT, ROT)
    return name, v


if __name__ == "__main__":
    names = list(CARDS)
    with Pool(4) as pool:
        res = dict(pool.map(one, names, chunksize=2))
    out = []
    for n, v in res.items():
        g = ROWS[n]
        sk = F.SKILL_INFO[g["必殺技"]]
        tgt = R._skill_target(g["必殺技"])
        naive = D.effect_value(sk, tgt)          # 割引前
        paid = float(g["効果予算"])               # 割引後（特性込み）
        tr = sum(D.trait_value(k) for k in R.traits_of(g))
        out.append({"name": n, "cost": float(g["コスト"]), "typ": g["兵種"],
                    "gauge": float(g["消費ゲージ%"]), "trait": g["固有特性"],
                    "resid": round(v, 3), "paid": round(paid, 3),
                    "naive": round(naive, 3), "trait_val": round(tr, 3),
                    "skill": g["必殺技"]})
    json.dump(out, open("price_audit.json", "w"), ensure_ascii=False, indent=1)
    out.sort(key=lambda r: -r["resid"])
    print("残差の上位（強すぎ）と下位（弱すぎ）")
    for r in out[:15] + [None] + out[-10:]:
        if r is None:
            print("   …")
            continue
        print("  %+6.2f  %-16s %2.0f点 %-3s 消費%3.0f%% %-9s 払%.2f 素%.2f"
              % (r["resid"], r["name"], r["cost"], r["typ"], r["gauge"],
                 r["trait"] or "-", r["paid"], r["naive"]))
    import statistics as st
    xs = [r["resid"] for r in out]
    print("全体: 中央値 %+.2f  平均 %+.2f  σ %.2f" % (st.median(xs), st.mean(xs), st.pstdev(xs)))
