"""【廃止】旧・全120枚の盤面残差（再現専用）。

    python3 tools/price_audit.py                  # 理由を表示して停止
    python3 tools/price_audit.py --legacy-invalid # 旧数値の再現だけ

この計器の ``army(card)`` は測定札を魚鱗の先頭へ置き、さらに
``field.matchup_cost`` が6枠すべてへ回す。したがって弓兵を前衛へ置くなど、
登録不能な布陣が混ざることが判明した。出力はカード調整の根拠に使わない。

合法な個札比較は ``tools/one_ruler.py``、前線維持を含む兵種・編成比較は
``tools/balance_suite.py archetype`` を使う。

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

# **実ゲームと同じ条件で測る（明示）。** M._roster_cards() も立てるが、
# 順序や別経路に依存しないようここでも書く。素の field を import しただけの
# 計器は特性が既定オフになり、特性の対プローブが +0.00 を返す罠がある
# （実際に踏んで一度誤診した・§7.67）。
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
    if "--legacy-invalid" not in sys.argv:
        print("この計器は不合法配置を含むため廃止しました。")
        print("個札: python3 tools/one_ruler.py --json one-ruler.json")
        print("部隊: python3 tools/balance_suite.py archetype --profile quick")
        print("旧結果の再現だけは --legacy-invalid を明示してください。")
        raise SystemExit(2)
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
