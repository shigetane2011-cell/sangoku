# -*- coding: utf-8 -*-
"""固有特性の値段を、いまの盤面で測り直す（§7.152・特性の再較正）。

    python3 tools/trait_price.py              # 全部（12性格×60種）
    python3 tools/trait_price.py --quick      # 4性格×20種（動作確認）
    python3 tools/trait_price.py --seeds 40   # 種の数を変える
    python3 tools/trait_price.py --rear       # 後衛に載せて測る
    python3 tools/trait_price.py --only reserve,dirge   # 足した特性だけ追い測り

なぜ要るか。`python3 -m sim.field traits` は**釣り合った合成軍どうし1局**で
margin を読む計器で、§7.151 の盤面ではこれが**段になってしまった** — 誘発型
21種のうち15種が判で押したように 0.1559 と出る（呼応も殿も影武者も同じ値）。
決着が速くなって潰走の起きる回数が減ったため、「何かした / しない」の二値
しか残っていない。**同じ値の並びは計器を疑う合図**（§13）。

ここは相手を**実カードの性格パネル**（12性格 × N種・官渡30）に替える。
相手が局ごとに違うので段が崩れ、小さい特性も別々の値になる。

読み方は skill_price / effect_sweep と同じ（§7.117）:
  値段 = Δ残存差（対応のある平均） ÷ **その札に1コスト点足したときの傾き**
局所勾配を同じ盤・同じ枠で取るので、盤面の総コストも集中の縮みも相殺する。

**勝敗（score）では測れない。** 0/1 に丸まるので 80局では小さい特性が
1局も動かず、cheer も pursuit もぴったり 0.000% と出る。残存差（diff）で読む。

**この計器で測れないもの**:
  本陣（command） … 総崩れは負けた側の残存差を大きく振るので、残存差の平均で
    測ると**符号ごと嘘になる**（§7.52 に既述。この計器でも前衛 -34.5・後衛 -14.1
    と出るが、勝率では +5.3%）。**勝率の通貨**で別に測る（design.TRAIT_PRICE の注記）。
  宝物の常在型 … 通貨が「功」なので §7.53 の帯合わせで測る。
前衛・後衛の両方で測り、**高いほう＝安全側**を採る（既存の表と同じ約束）。
陣頭・馬前は前衛にしか効かないので後衛は 0.000 と出る（それでよい）。
"""
import json
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataclasses import replace                       # noqa: E402
from sim import field as F                            # noqa: E402
from sim import match as M                            # noqa: E402
from sim import rosterdata as R                       # noqa: E402
from sim import dummies as D                          # noqa: E402
from sim import design as DS                          # noqa: E402

DT = 0.5
DELTA = 2.0             # 局所勾配の振り幅（skill_price と同じ）
BASE = 5.0              # 車台のコスト
RATE = 4.86             # 勝点%/コスト点（参考表示にだけ使う）

# 隣に騎兵が要る特性（馬前）。土台の作り方を変える。
NEEDS_CAV_NEIGHBOR = {"cover"}


def _army(trait: str, cost: float = BASE, slot: int = 0,
          cav_neighbor: bool = False) -> "F.Army":
    """1枚だけが特性を持つ合成軍。slot の札のコストだけ動かせる（勾配用）。"""
    cards = []
    for i, role in enumerate(F.MIXED_ROLES):
        typ = F.CAV if (cav_neighbor and i == 1) else F.INF
        c = F._synth(cost if i == slot else BASE, typ, role)
        cards.append(replace(c, trait=(trait if i == slot else "")))
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _opps(personas, seeds):
    cards = M._roster_cards()
    return [D.make_entry(cards, p, s, caps=(("官渡", 30.0),)).units[0]
            for p in personas for s in range(seeds)]


_STATE = {}


def _init(personas_n, seeds):
    R.load_skills_into_field()
    R.load_traits_into_field()
    F.SKILLS_ON = F.TRAITS_ON = True
    _STATE["opps"] = _opps(D.PERSONAS[:personas_n], seeds)


def _one(job):
    trait, cost, slot, cav = job
    a = _army(trait, cost, slot, cav)
    return [F.simulate(a, o, dt=DT, seed=i * 7 + 1)["diff"]
            for i, o in enumerate(_STATE["opps"])]


def _load(path):
    try:
        return json.load(open(path))
    except Exception:
        return {}


def _jp(k):
    return F.TRAITS[k][4] if k in F.TRAITS else k


def _measure(pool, keys, slot, store):
    """1つの席（前衛／後衛）で全特性を測る。戻り値は key → (値段, Δ, ±95%, 違)。

    **1本ずつ控えへ書く。** 測定は30分を超えるが実行環境は入れ替わることが
    あるので、落ちても続きから走れるようにしておく（2回とばされた）。
    """
    jobs = [("", BASE, slot, False),
            ("", BASE + DELTA, slot, False),
            ("", BASE - DELTA, slot, False),
            ("", BASE, slot, True),
            ("", BASE + DELTA, slot, True),
            ("", BASE - DELTA, slot, True)]
    jobs += [(k, BASE, slot, k in NEEDS_CAV_NEIGHBOR) for k in keys]
    got = _load(store)
    # 控えの鍵は仕事の中身（特性・コスト・席・隣）。並びの番号にすると特性を
    # 1つ足しただけで全部ずれる。
    def key(j):
        return "{}|{}|{:.1f}|{}".format(slot, j[0], j[1], int(j[3]))
    todo = [j for j in jobs if key(j) not in got]
    if todo:
        print("  席{} 残り {}/{} 本".format(slot, len(todo), len(jobs)), flush=True)
        for j, col in zip(todo, pool.imap(_one, todo)):
            got[key(j)] = col
            json.dump(got, open(store, "w"))
    res = [got[key(j)] for j in jobs]
    base = {False: res[0], True: res[3]}
    slope = {}
    for j, cav in ((0, False), (3, True)):
        slope[cav] = (statistics.mean(res[j + 1])
                      - statistics.mean(res[j + 2])) / (2.0 * DELTA)
    out = {}
    for k, col in zip(keys, res[6:]):
        cav = k in NEEDS_CAV_NEIGHBOR
        d = [x - y for x, y in zip(col, base[cav])]
        md, n = statistics.mean(d), len(d)
        out[k] = (md / slope[cav], md,
                  1.96 * statistics.pstdev(d) / (n ** 0.5),
                  sum(1 for x in d if abs(x) > 1e-9), n)
    return slope, out


def main():
    quick = "--quick" in sys.argv
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv \
        else (20 if quick else 60)
    npers = 4 if quick else len(D.PERSONAS)
    seats = [("後衛", 4)] if "--rear" in sys.argv else (
        [("前衛", 0)] if "--front" in sys.argv else [("前衛", 0), ("後衛", 4)])

    R.load_skills_into_field()
    n_tr = R.load_traits_into_field()
    F.SKILLS_ON = F.TRAITS_ON = True
    keys = sorted(F.TRAITS) + ["vanguard", "cover", "vs_wei",
                               "drunk", "restraint"]
    if "--only" in sys.argv:        # 特性を足したときの追い測り
        keys = sys.argv[sys.argv.index("--only") + 1].split(",")
    print("特性 {}種（誘発 {} ＋ 常在）× 性格 {} × 種 {} ＝ 1案 {}局 × 席 {}"
          .format(len(keys), n_tr, npers, seeds, npers * seeds, len(seats)),
          flush=True)
    print("  本陣（command）はこの計器では測れない（残存差が符号ごと嘘になる）"
          " — 勝率の通貨で別測", flush=True)

    store = os.environ.get("TRAIT_STORE", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".trait_price_cache.json"))
    pool = Pool(int(os.environ.get("W", "4")), _init, (npers, seeds))
    got = {}
    for label, slot in seats:
        slope, out = _measure(pool, keys, slot, store)
        got[label] = out
        print("\n【{}】局所勾配 歩隣 {:.5f} / 騎隣 {:.5f}（残存差／コスト点）"
              .format(label, slope[False], slope[True]), flush=True)
        print("{:<12}{:>11}{:>10}{:>9}{:>10}".format(
            "特性", "Δ残存差", "±95%", "違った局", "値段"))
        for k in keys:
            v, md, ci, diff_n, n = out[k]
            print("{:<12}{:>+11.5f}{:>10.5f}{:>7}/{:<3}{:>10.3f}".format(
                _jp(k), md, ci, diff_n, n, v), flush=True)

    print()
    print("{:<12}{:>10}{:>10}{:>10}{:>10}{:>9}".format(
        "特性", "前衛", "後衛", "採る値", "いまの表", "差"))
    take = {}
    for k in keys:
        vs = [got[lab][k][0] for lab, _ in seats]
        v = max(vs)                       # 高いほう＝安全側（既存の表と同じ約束）
        take[k] = v
        old = DS.TRAIT_PRICE.get(k, 0.0)
        cells = ["{:>10.3f}".format(got[lab][k][0]) if lab in got else
                 "{:>10}".format("—") for lab in ("前衛", "後衛")]
        print("{:<12}{}{}{:>10.3f}{:>10.3f}{:>+9.3f}".format(
            _jp(k), cells[0], cells[1], v, old, v - old))
    print()
    print("  ±95% の 1コスト点は勝率 {:.2f}%（為替 {}）".format(RATE, RATE))
    print("  控え: {}（消せば測り直す）".format(store))
    print("  sim/design.py の TRAIT_PRICE へ貼る形（本陣は別測なので据え置き）:")
    for k, v in sorted(take.items(), key=lambda kv: -kv[1]):
        print("        {!r}: {:.4f},".format(k, v))
    print("TRAITPRICE DONE")


if __name__ == "__main__":
    main()
