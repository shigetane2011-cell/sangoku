# -*- coding: utf-8 -*-
"""宝物18種の功を、実デッキの的で測り直す（§7.139・§7.145 の計器を tools/ へ）。

    python3 tools/treasure_price.py --preflight      # 土台5デッキの勝率だけ（五分帯か）
    python3 tools/treasure_price.py --preflight --caps 30,34,38   # 相手の上限を振って五分帯を探す
    python3 tools/treasure_price.py                  # 全条件（12性格×100種）
    python3 tools/treasure_price.py --quick          # 12性格×12種（動作確認）
    python3 tools/treasure_price.py --only t_seino,t_kinno

土台は §7.139 のまま: 強弓ベースの30点デッキ5種（d0＝魏・dcav＝騎兵枠・dshu＝蜀・
dgo＝呉・dgunyu＝群雄）。宝物は「同じデッキの宝物あり／なし」を**同じ相手・同じ種**で
対にして測る（差の分散が独立測定より桁で小さい）。功 = Δ勝率 × 10000 ÷ 4.86
（勝率 → コスト点 → ×100）。装備は本番の _apply_treasures と同じ変換。

**土台の勝率が五分帯に無いと為替が使えない**（§7.137 教訓1）。勢力デッキは弓トリオ
置換の素朴な構成が 8〜29% の大負け圏で、騎兵込みの後衛にしてようやく五分帯に入った。
盤面が動いたら先に --preflight で確かめること。

持ち主の選び方（§7.139 教訓）: 「その宝物が仕事をする札」に持たせないと計器が
空回りする。七星宝刀は満寵（対田豊で打ち消されているのは満寵の剛毅）、寄せは郝昭
（曹仁は生来 def_lean=1.0 で +0.3 がクランプに消える）。
条件ごとに JSON へ控えるので、途中で落ちても続きから走る。
scratchpad の tp_lib.py / tp_run.py / tp_report.py を1本にまとめたもの。
"""
import dataclasses
import json
import os
import statistics
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F          # noqa: E402
from sim import match as M          # noqa: E402
from sim import rosterdata as R     # noqa: E402
from sim import dummies as D        # noqa: E402
from sim import play as PL          # noqa: E402

RATE = 4.86
# 相手（性格パネル）のコスト上限。**デッキごとに変えて五分帯へ寄せる**（§7.153）。
# §7.139 は後衛の札を入れ替えて五分に寄せたが、それだと持ち主（郝昭が孟徳新書…）を
# 崩さずには動かせない。相手の上限は測定だけの調節で、あり/なしの差は同じ相手・同じ種で
# 取るので値付けには影響しない。--caps 30,33,36 で走らせて 50% に近い上限を選ぶ。
# 2026-09-04（§7.151 の盤面・12性格×12種）: d0 34→50.7% ／ dcav 32→57.6% ／
# dshu 34→47.9% ／ dgo 40→52.1%。上限30ではそれぞれ 69／62／76／84% で為替が効かない。
DECK_CAP = {"d0": 34.0, "dcav": 32.0, "dshu": 34.0, "dgo": 40.0, "dgunyu": 23.0}
OUT = os.environ.get("TREASURE_STORE", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".treasure_price_cache"))

FRONT = ["文聘〔江夏〕", "曹仁〔堅守〕", "郝昭〔陳倉〕"]
DECKS = {
    "d0":     FRONT + ["李典〔慎重〕", "満寵〔剛毅〕", "貂蝉〔傾国〕"],
    "dcav":   FRONT + ["李典〔慎重〕", "曹洪〔救主〕", "貂蝉〔傾国〕"],
    "dshu":   FRONT + ["黄忠〔定軍山〕", "関平〔麒麟児〕", "樊建〔伝令〕"],
    "dgo":    FRONT + ["甘寧〔錦帆賊〕", "孫尚香〔弓腰姫〕", "全琮〔護軍〕"],
    # 群雄は §7.151 の盤面で魏前衛＋群雄後衛が 6〜26% にしかならず（後衛をどう
    # 入れ替えても・田豊を入れても）、上限22まで下げてやっと五分。前衛ごと群雄に
    # 組み替えて上限23で 48.6%（2026-09-04）。玉璽の条件（群雄3人以上）は自明に成立。
    "dgunyu": ["公孫瓚〔白馬義従〕", "華雄〔汜水関〕", "紀霊〔三尖刀〕",
               "貂蝉〔傾国〕", "陳宮〔公台〕", "王允〔連環計〕"],
}
# 条件 = (デッキ, 宝物キー, 持ち主)
CONDS = {
    "d0_motoku":      ("d0", "t_motoku",    "郝昭〔陳倉〕"),   # 前衛の主力=最多兵
    "d0_rendo":       ("d0", "t_rendo",     "李典〔慎重〕"),   # 主砲の弓
    "d0_iten":        ("d0", "t_iten",      "文聘〔江夏〕"),   # 魏3は成立済み
    "d0_seiryu":      ("d0", "t_seiryu",    "郝昭〔陳倉〕"),   # 白兵の武力へ
    "d0_hakuusen":    ("d0", "t_hakuusen",  "貂蝉〔傾国〕"),   # 知力撃ちの主砲へ
    "d0_teki":        ("d0", "t_teki",      "曹仁〔堅守〕"),   # 4割を割る壁
    "d0_kinno":       ("d0", "t_kinno",     "満寵〔剛毅〕"),   # 生き残る後衛
    "d0_gyokutai":    ("d0", "t_gyokutai",  "満寵〔剛毅〕"),
    "d0_shichisei":   ("d0", "t_shichisei", "満寵〔剛毅〕"),   # 打ち消されている札に
    "d0_gentetsu":    ("d0", "t_gentetsu",  "郝昭〔陳倉〕"),   # 寄せに伸びしろのある札に
    "d0_keiki":       ("d0", "t_keiki",     "郝昭〔陳倉〕"),
    "d0_seino":       ("d0", "t_seino",     "曹仁〔堅守〕"),
    "d0_mokgyu":      ("d0", "t_mokgyu",    "満寵〔剛毅〕"),   # 後衛のみの札
    "d0_toko":        ("d0", "t_toko",      "文聘〔江夏〕"),   # 演出＝零点対照
    "dcav_sekitoba":  ("dcav", "t_sekitoba", "曹洪〔救主〕"),
    "dshu_shokkin":   ("dshu", "t_shokkin", "黄忠〔定軍山〕"),
    "dgo_sonshi":     ("dgo", "t_sonshi",   "孫尚香〔弓腰姫〕"),
    "dgunyu_gyokuji": ("dgunyu", "t_gyokuji", "貂蝉〔傾国〕"),
}
_S = {}


def _init():
    if not F.SKILL_INFO:
        R.load_skills_into_field()
    if not F.TRAITS:
        R.load_traits_into_field()
    F.TRAITS_ON = True
    _S["all"] = {c.name: c for c in M._roster_cards()}
    _S["cards"] = M._roster_cards()
    for name, deck in DECKS.items():
        tot = sum(_S["all"][n].cost for n in deck)
        assert tot == 30.0, (name, tot)


def _equip(names, key, holder):
    """本番の _apply_treasures と同じ変換（trait+hidden 合流→札モッド）。"""
    out = []
    for n in names:
        c = _S["all"][n]
        if key is not None and n == holder:
            ks = list(F.trait_keys(c.trait)) + [key]
            c = dataclasses.replace(c, trait=F.TRAIT_SEP.join(ks), hidden_trait=key)
            c = PL.apply_treasure_card_mods(c)
        out.append(c)
    return out


def _one(job):
    cond, npers, seeds, cap = job
    deck, key, holder = cond
    army = F.Army(tuple(_equip(DECKS[deck], key, holder)), F.FORM_STANDARD)
    rows = []
    for persona in D.PERSONAS[:npers]:
        for seed in range(seeds):
            opp = D.make_entry(_S["cards"], persona, seed,
                               caps=(("官渡", cap),)).units[0]
            rows.append([persona.name, seed,
                         F.simulate(army, opp, dt=0.25, seed=seed * 7 + 1)["score"]])
    return rows


def _path(name, n, cap):
    return os.path.join(OUT, "{}_{}_{:g}.json".format(name, n, cap))


def _paired(a, b):
    ks = sorted(set(a) & set(b))
    d = [a[k] - b[k] for k in ks]
    m = statistics.mean(d)
    se = statistics.pstdev(d) / max(len(d) - 1, 1) ** 0.5 if len(d) > 1 else 0.0
    return m, se, len(d), statistics.mean(a[k] for k in ks), statistics.mean(b[k] for k in ks)


def main():
    quick = "--quick" in sys.argv
    pre = "--preflight" in sys.argv
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv \
        else (12 if (quick or pre) else 100)
    npers = len(D.PERSONAS)
    only = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
    os.makedirs(OUT, exist_ok=True)
    _init()

    conds = {} if pre else {k: v for k, v in CONDS.items() if only is None or v[1] in only}
    need_decks = set(DECKS) if pre else {v[0] for v in conds.values()}
    n = npers * seeds
    # --caps 30,33,36: 土台だけを上限を変えて測る（五分帯へ寄せる上限を探す）
    scan = [float(x) for x in sys.argv[sys.argv.index("--caps") + 1].split(",")] \
        if "--caps" in sys.argv else None
    jobs = []
    for d in sorted(need_decks):
        for cap in (scan or [DECK_CAP[d]]):
            jobs.append(("{}_base".format(d), (d, None, None), cap))
    if not pre:
        jobs += [(k, c, DECK_CAP[c[0]]) for k, c in conds.items()]
    todo = [j for j in jobs if not os.path.exists(_path(j[0], n, j[2]))]
    print("条件 {} 本（うち未着 {}）× 12性格 × {}種 ＝ {}局／本".format(
        len(jobs), len(todo), seeds, n), flush=True)
    if todo:
        pool = Pool(int(os.environ.get("W", "4")), _init)
        for (k, _, cap), rows in zip(todo, pool.imap(_one, [(c, npers, seeds, cap) for _, c, cap in todo])):
            json.dump(rows, open(_path(k, n, cap), "w"))
            print("  済 {} 上限{:g}".format(k, cap), flush=True)

    load = lambda k, cap: {(r[0], r[1]): r[2] for r in json.load(open(_path(k, n, cap)))}
    print()
    print("== 土台デッキの勝率（n={}）— 五分帯（35〜65%）から外れたら為替が効かない ==".format(n))
    for d in sorted(need_decks):
        cells = []
        for cap in (scan or [DECK_CAP[d]]):
            w = statistics.mean(load("{}_base".format(d), cap).values())
            cells.append("上限{:g}: {:5.1%}{}".format(cap, w, "" if 0.35 <= w <= 0.65 else "★"))
        print("  {:<7} {}".format(d, "   ".join(cells)))
    if pre:
        print("PREFLIGHT DONE")
        return
    bases = {d: load("{}_base".format(d), DECK_CAP[d]) for d in sorted(need_decks)}
    rows_t = {r["キー"]: r for r in R.treasures()}
    print()
    print("== 宝物の実測（対の Δ勝率 → 功） ==")
    print("{:<16}{:<10}{:>9}{:>8}{:>8}{:>6}{:>7}".format("条件", "宝物", "Δ勝率", "±SE", "実測功", "現行", "差"))
    for k, (deck, key, holder) in conds.items():
        m, se, cnt, wa, wb = _paired(load(k, DECK_CAP[deck]), bases[deck])
        cur = int(rows_t[key]["功"])
        print("{:<16}{:<10}{:>+9.2%}{:>8.2%}{:>8.0f}{:>6}{:>+7.0f}".format(
            k, rows_t[key]["名前"], m, se, m * 10000.0 / RATE, cur, m * 10000.0 / RATE - cur))
    print()
    print("  丸め方（§7.139）: 20以上は5刻み・20未満は実測整数・負は0。控え: {}".format(OUT))
    print("TREASURE DONE")


if __name__ == "__main__":
    main()
