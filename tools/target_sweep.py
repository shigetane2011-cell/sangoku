# -*- coding: utf-8 -*-
"""対象の値段を**対戦の集まり**で測る（§7.99）。

    python3 tools/target_sweep.py            # 全対象を18通りの相手で測る
    python3 tools/target_sweep.py --new      # 新しい選択子と錨だけ（速い）

なぜ「代表的な1戦」で測らないか。§7.20 で段ごとの値段を測ったときと同じ理由で、
**同じ技が相手しだいで何倍も開く**。特に狙い撃ちの選択子（知力が最高／最低・
兵力が最少）は、相手に突出した札が居るかどうかで値打ちが変わる——1戦で測って
決め打つと、いちばん値付けしたい相手のときにいちばん外れる。

**振る軸は3つ**: 相手の性格（耐久寄り／均衡／火力寄り）・陣形（鶴翼・魚鱗・
雁行）・知力の散らばり（平ら／軍師が1人）。3×3×2 = 18通り。

**布陣は規則どおりに組む**（前衛は歩兵・騎兵、後衛は弓兵）。§7.96 で
`matchup_cost` が登録できない布陣を測っていたのを踏んだので、ここでも
`match.placement_errors` に通してから測る。

**既存の対象も同じ掃引で測る。** 新しい行だけを別の較正で入れると、表の中で
単位が混ざる。既存の錨（敵1体（正面）・敵1列・敵前衛・敵全体）を同じ run で
測り、そこからの比で新しい行を置けるようにする。
"""
import sys, os, statistics
from dataclasses import replace
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEST = "＿試験技"
NF = {"鶴翼": 4, "魚鱗": 3, "雁行": 2}
# 相手の性格（前衛に置く役割の並び）
PERSONA = {
    "耐久寄り": ("tank", "tank", "tank", "bal"),
    "均衡":     ("tank", "bal", "bal", "dps"),
    "火力寄り": ("bal", "dps", "dps", "dps"),
}
FOE_TARGETS = ["敵1体（兵力が最多）", "敵1体（兵力が最少）", "敵1体（正面）",
               "敵1体（残兵力が最少）", "敵1体（知力が最高）", "敵1体（知力が最低）",
               "敵1列", "敵前衛", "敵後衛", "敵全体"]
NEW = ("敵1体（兵力が最少）", "敵1体（知力が最高）", "敵1体（知力が最低）")
ANCHOR = ("敵1体（兵力が最多）", "敵1体（正面）", "敵1列", "敵前衛", "敵全体")


def _army(G, persona, form_name, wise, with_skill, target):
    """規則どおりの布陣を組む。wise=True なら後衛の1枚だけ知力を突出させる。"""
    nf = NF[form_name]
    roles = PERSONA[persona]
    front, rear = [], []
    for i in range(nf):
        r = {"tank": G.TANK, "bal": G.BAL, "dps": G.DPS}[roles[i % len(roles)]]
        front.append(G._synth(G.BASE_COST, G.INF if i % 2 == 0 else G.CAV, r))
    for i in range(6 - nf):
        rear.append(G._synth(G.BASE_COST, G.ARC, G.DPS if i else G.BAL))
    cards = front + rear
    # 知力の散らばりが狙い撃ちの値打ちを決めるので、そこを軸にする。
    # **平らを「全員同値」にしてはいけない。** 同値だと max も min も先頭を
    # 返すだけで、「知力が最高」と「最低」が同じ隊を指す——測定ではなく
    # 引き分けになる（初版がこれで、両者の値が小数3桁まで一致した）。
    # 平らは「なだらかに散る」、軍師入りは「1人だけ突出」で対比させる。
    if wise:
        cards = [replace(c, might=80.0, wits=(220.0 if i == len(cards) - 1 else 70.0))
                 for i, c in enumerate(cards)]
    else:
        cards = [replace(c, might=80.0, wits=85.0 + 6.0 * i)
                 for i, c in enumerate(cards)]
    # **対照は「技なし」でなければならない。** 合成カードは既定で標準技を
    # 持っているので、撃ち手だけ差し替えるときに対照側の技を消し忘れると、
    # 「試験技 対 標準技」を測ることになる（実際それで弱体の値段が負に出た）。
    # 能力値の払い（stat_cost）も両方 0 に揃える。
    cards[0] = replace(cards[0], skill=(TEST if with_skill is not None else ""),
                       stat_cost=0.0)
    if with_skill is not None:
        G.SKILL_INFO[TEST] = with_skill
        G.SKILL_TARGET[TEST] = target
    form = G.Formation(n_front=nf, frontage=G.BASE_FRONTAGE)
    return G.Army(tuple(cards), form)


def cell(job):
    persona, form_name, wise, target, effect = job
    from sim import field as G
    from sim import match as MM
    G.SKILLS_ON = True
    G.TRAITS_ON = False
    sk = (G.Skill(power=5.0, kind="melee") if effect == "打撃"
          else G.Skill(mods=(("atk", -0.10, 30.0),)))
    a = _army(G, persona, form_name, wise, sk, target)
    b = _army(G, persona, form_name, wise, None, target)
    for army in (a, b):
        errs = MM.placement_errors(army)
        assert not errs, "規則に反する布陣: {}".format(errs)
    dt = 0.5
    ys = G.cost_yardstick(dt)
    # 左右を入れ替えて反対称化（席順の偏りを消す）
    v = (G.margin(a, b, dt) - G.margin(b, a, dt)) / 2.0 / ys
    return target, effect, wise, v


def main():
    targets = list(NEW + ANCHOR) if "--new" in sys.argv else FOE_TARGETS
    jobs = [(p, f, w, t, e)
            for p in PERSONA for f in NF for w in (False, True)
            for t in targets for e in ("打撃", "弱体")]
    print("測る: 対象{} × 相手{}通り × 効果2 = {} 局".format(
        len(targets), len(PERSONA) * len(NF) * 2, len(jobs)), flush=True)
    res = Pool(4).map(cell, jobs, chunksize=4)

    from collections import defaultdict
    agg = defaultdict(list)
    split = defaultdict(list)
    for t, e, w, v in res:
        agg[(t, e)].append(v)
        split[(t, e, w)].append(v)

    for effect in ("打撃", "弱体"):
        print("\n── {}（コスト点。18通りの相手で）──".format(effect))
        print("  {:<22}{:>8}{:>8}{:>8}{:>8}{:>7}   {:>8}{:>8}".format(
            "対象", "平均", "中央", "最小", "最大", "幅", "知力平ら", "軍師入り"))
        for t in targets:
            v = agg[(t, effect)]
            flat = split[(t, effect, False)]
            wise = split[(t, effect, True)]
            print("  {:<22}{:>8.3f}{:>8.3f}{:>8.3f}{:>8.3f}{:>6.1f}x   {:>8.3f}{:>8.3f}".format(
                t, statistics.mean(v), statistics.median(v), min(v), max(v),
                (max(v) / min(v)) if min(v) > 0.01 else float("inf"),
                statistics.mean(flat), statistics.mean(wise)))

    print("\n── 錨からの比（既存の表へ入れるとき、この比で置く）──")
    for effect in ("打撃", "弱体"):
        base = statistics.mean(agg[("敵1体（正面）", effect)])
        print("  {}: 敵1体（正面）を 1.00 としたとき".format(effect))
        for t in targets:
            print("    {:<22}{:>7.2f}".format(
                t, statistics.mean(agg[(t, effect)]) / base if base else 0.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
