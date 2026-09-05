# -*- coding: utf-8 -*-
"""実カード1枚の兵法（または特性）の値打ちを、実デッキの的で測る（§7.117・§7.153）。

    python3 tools/skill_panel.py 郝昭〔陳倉〕 蒋琬〔社稷〕          # 兵法を外した差
    python3 tools/skill_panel.py --trait 袁紹〔盟主〕 費禕〔大将軍〕   # 特性を外した差
    python3 tools/skill_panel.py --strip-trait 陸抗〔羊陸之交〕        # 特性を両案から外して兵法だけ
    python3 tools/skill_panel.py --effect 諸葛恪〔元遜〕 "ダメージ 威力1000%" 諸葛恪〔元遜〕  # 案の測定
    python3 tools/skill_panel.py --trait --trait-effect hakuba "移動速度 +30%（20秒）|enemy_retreat で発動 / 対象 自分 / 1戦3回まで" 公孫瓚〔白馬義従〕
    python3 tools/skill_panel.py --quick ...                          # 4性格×20種

土台: その札 ＋ 合成の詰め物5枚（総コスト30・詰め物は同コスト・弓は後衛、
歩騎は前衛）。相手は実カードの性格パネル（12性格 × N種・官渡30）。
「あり／なし」を**同じ相手・同じ種**で対にして測る（差の分散が桁で小さい）。

読み方は2つの通貨で出す:
  勝率の通貨 … Δ勝率 ÷ 4.86（§7.52。土台が50%付近のときだけ信用できる）
  残存差の通貨 … Δ残存差 ÷ 局所勾配（詰め物1枚に ±2 コスト点。§7.117）
両方が揃えば信用してよい。土台の勝率が 30% や 70% だと勝率の通貨は効きにくい。

なぜ要るか。`skill_price.py` は合成軍どうしの1局で陣形ごとに測るので、
勾配が退化する（車台×陣形）で +51／−8／+15 のように暴れる（効果予算の許容超え
4枚がまさにそれだった）。相手が局ごとに違うパネルなら段にならない。
`campaign/reprice/skill_panel.py`（handoff §3 の記述）は作業場ごと失われていたので
ここへ作り直した。1本ずつ控えへ書くので、途中で落ちても続きから走る。
"""
import json
import os
import statistics
import sys
from dataclasses import replace
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F          # noqa: E402
from sim import match as M          # noqa: E402
from sim import rosterdata as R     # noqa: E402
from sim import dummies as D        # noqa: E402
from sim import design as DS        # noqa: E402

DT = 0.25
RATE = 4.86
TOTAL = 30.0
DELTA = 2.0
_S = {}


def _apply_override(override):
    """効果文の差し替え（案の測定用）: 札名 → 効果文。本番の器で読み直す。
    鍵が "trait:<キー>" なら特性の差し替え（値は "効果文|備考"）。"""
    import re
    for name, eff in (override or {}).items():
        if name.startswith("trait:"):
            key = name[6:]
            text, note = (eff.split("|", 1) + [""])[:2]
            cond, target, cap, _sk, jp = F.TRAITS[key]
            m = re.search(r"(\w+) で発動", note); cond = m.group(1) if m else cond
            m = re.search(r"対象 ([^/]+)", note); target = m.group(1).strip() if m else target
            m = re.search(r"1戦(\d+)回", note); cap = int(m.group(1)) if m else cap
            with F.unscaled():
                F.TRAITS[key] = (cond, target, cap, F._parse_skill(text, target), jp)
            continue
        sk = [r for r in R.skills() if r["武将"] == name][0]
        F.SKILL_INFO[sk["兵法名"]] = F._parse_skill(eff, sk["対象"])


def _init(npers, seeds, override=None):
    R.load_skills_into_field()
    R.load_traits_into_field()
    F.SKILLS_ON = F.TRAITS_ON = True
    for k, v in (override or {}).items():
        if k.startswith("const:"):       # 診断用: field の定数を振る（相手にも掛かる）
            setattr(F, k[6:], float(v))
    if hasattr(F, "sync_type_atk"):
        F.sync_type_atk()
    cards = M._roster_cards()       # ここで兵法表が読み直されることがある
    _S["cards"] = {c.name: c for c in cards}
    _S["opps"] = [D.make_entry(cards, p, s, caps=(("官渡", TOTAL),)).units[0]
                  for p in D.PERSONAS[:npers] for s in range(seeds)]
    _S["override"] = override
    _apply_override(override)       # **名簿を読んだ後に**差し替える（前は上書きされて空振りした）


def _army(card, filler_bump=0.0):
    """札 ＋ 詰め物5枚。詰め物のうち1枚（前衛の歩兵）を bump ぶん動かせる（勾配用）。"""
    fc = (TOTAL - card.cost) / 5.0
    front = [F._synth(fc + filler_bump, F.INF, F.BAL), F._synth(fc, F.INF, F.BAL)]
    rear = [F._synth(fc, F.ARC, F.DPS), F._synth(fc, F.ARC, F.DPS)]
    if card.typ == F.ARC:
        cards = front + [F._synth(fc, F.INF, F.BAL)] + [card] + rear
    else:
        cards = [card] + front + rear + [F._synth(fc, F.ARC, F.DPS)]
    return F.Army(tuple(cards), F.FORM_STANDARD)


def _one(job):
    name, mode, bump = job
    _apply_override(_S.get("override"))     # 念のため局ごとにも当てる
    c = _S["cards"][name]
    if _S.get("strip"):
        # 特性を**両方の案から**外す。自分の兵法で発動する特性（節制＝自身の
        # 初回兵法後）は、兵法を外すと特性まで消えて差が混ざるため。
        c = replace(c, trait="")
    g = (_S.get("override") or {}).get("gauge:" + name)
    if g:                                # 診断用: 段（消費, 初期）を振る
        gc, gi = (float(x) for x in g.split(","))
        c = replace(c, gauge_cost=gc, gauge_init=gi)
    if mode == "off":
        c = replace(c, skill="") if not _S.get("trait") else replace(c, trait="")
    a = _army(c, bump)
    w, d = [], []
    for i, o in enumerate(_S["opps"]):
        r = F.simulate(a, o, dt=DT, seed=i * 7 + 1)
        w.append(r["score"]); d.append(r["diff"])
    return w, d


def main():
    quick = "--quick" in sys.argv
    trait = "--trait" in sys.argv
    strip = "--strip-trait" in sys.argv     # 兵法を測るとき特性を両案から外す
    override = {}
    if "--effect" in sys.argv:              # --effect 札名 効果文（案の測定）
        i = sys.argv.index("--effect")
        override[sys.argv[i + 1]] = sys.argv[i + 2]
    if "--trait-effect" in sys.argv:        # --trait-effect キー "効果文|備考"（特性の案）
        i = sys.argv.index("--trait-effect")
        override["trait:" + sys.argv[i + 1]] = sys.argv[i + 2]
    for i, a in enumerate(sys.argv):
        if a == "--const":                  # --const 名前=値（field の定数・診断用）
            k, v = sys.argv[i + 1].split("=", 1); override["const:" + k] = v
        if a == "--gauge":                  # --gauge 札名 消費,初期（段の診断用）
            override["gauge:" + sys.argv[i + 1]] = sys.argv[i + 2]
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv \
        else (20 if quick else 60)
    npers = 4 if quick else len(D.PERSONAS)
    # 旗の引数は**位置で**外す。値で外すと --effect の札名と測る札名が同じ文字列の
    # とき両方消えて「0枚」になる（一度踏んだ）。
    skip = set()
    for flag, n in (("--seeds", 1), ("--effect", 2), ("--trait-effect", 2), ("--const", 1), ("--gauge", 2)):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            skip.update(range(i + 1, i + 1 + n))
    names = [a for i, a in enumerate(sys.argv) if i > 0 and i not in skip
             and not a.startswith("--")]
    store = os.environ.get("PANEL_STORE", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".skill_panel_cache.json"))
    try:
        got = json.load(open(store))
    except Exception:
        got = {}
    what = "特性" if trait else ("兵法（特性なし）" if strip else "兵法")
    if override:
        what += "＝" + "／".join("{}:{}".format(k, v) if k.startswith(("const:", "gauge:")) else v
                                for k, v in override.items())
    # 控えの鍵に**名簿の効果文**を入れる。名簿（skills.csv）を書き換えて測り直す
    # とき、鍵が同じだと古い控えを読んでしまう（1150% にしたのに 1300% の値が出た）。
    R.load_skills_into_field(); R.load_traits_into_field()
    if trait:
        # 特性は**定義まで**鍵に入れる。名前だけだと中身を変えても古い控えを返す
        # （白馬を移動速度→馬上回避に作り替えたとき、同じ数字が出て気付いた）。
        tdef = {t["キー"]: "{}/{}/{}".format(t["型"], t["効果"], t["備考"]) for t in R.traits()}
        consts = {"hakuba": getattr(F, "HAKUBA_COVER", None), "cover": F.COVER_SHARE,
                  "vanguard": F.VANGUARD_MEN, "command": (F.COMMAND_MEN, F.COMMAND_ROUT),
                  "restraint": getattr(F, "RESTRAINT_NATURAL_MULT", None),
                  "drunk": getattr(F, "DRUNK_CHAOS", None)}
        fp = {}
        for g in R.generals():
            ks = [k.strip() for k in (g["固有特性"] or "").replace("／", "/").split("/") if k.strip()]
            fp[g["名前"]] = ";".join("{}={}|{}".format(k, tdef.get(k, ""), consts.get(k, "")) for k in ks)
    else:
        fp = {r["武将"]: r["効果"] for r in R.skills()}
    print("{} 枚の{}を外した差 × 性格 {} × 種 {} ＝ 1案 {}局".format(
        len(names), what, npers, seeds, npers * seeds), flush=True)
    jobs = []
    for n in names:
        for mode, bump in (("on", 0.0), ("off", 0.0), ("on", DELTA), ("on", -DELTA)):
            k = "{}|{}|{}|{}|{}|{}".format(n, what, mode, bump, npers * seeds, fp.get(n, ""))
            if k not in got:
                jobs.append((k, (n, mode, bump)))
    if jobs:
        _S["trait"] = trait
        _S["strip"] = strip
        pool = Pool(int(os.environ.get("W", "4")), _init, (npers, seeds, override))
        for (k, _), v in zip(jobs, pool.imap(_one, [j for _, j in jobs])):
            got[k] = v
            json.dump(got, open(store, "w"))
            print("  済 {}".format(k), flush=True)
    cards = {c.name: c for c in M._roster_cards()}
    rows = {g["名前"]: g for g in R.generals()}
    print()
    print("{:<16}{:>6}{:>8}{:>9}{:>8}{:>9}{:>9}{:>9}{:>9}".format(
        "武将", "土台勝率", "Δ勝率", "±95%", "勝率通貨", "Δ残存差", "残存通貨", "請求", "釣り合い"))
    for n in names:
        key = lambda mode, bump: "{}|{}|{}|{}|{}|{}".format(n, what, mode, bump, npers * seeds, fp.get(n, ""))
        won, don = got[key("on", 0.0)]
        woff, doff = got[key("off", 0.0)]
        _, dhi = got[key("on", DELTA)]
        _, dlo = got[key("on", -DELTA)]
        slope = (statistics.mean(dhi) - statistics.mean(dlo)) / (2.0 * DELTA)
        dw = [a - b for a, b in zip(won, woff)]
        dd = [a - b for a, b in zip(don, doff)]
        n_ = len(dw)
        mw = statistics.mean(dw); ci = 1.96 * statistics.pstdev(dw) / (n_ ** 0.5)
        md = statistics.mean(dd)
        v_win = mw * 100.0 / RATE
        v_diff = md / slope if abs(slope) > 1e-9 else float("nan")
        g = rows[n]
        if trait:
            ks = [k.strip() for k in (g["固有特性"] or "").replace("／", "/").split("/") if k.strip()]
            charged = sum(DS.trait_value(k, g["人物"]) for k in ks)
        else:
            ks = [k.strip() for k in (g["固有特性"] or "").replace("／", "/").split("/") if k.strip()]
            charged = float(g["効果予算"]) - sum(DS.trait_value(k, g["人物"]) for k in ks)
        print("{:<16}{:>7.1%}{:>+8.2%}{:>8.2%}{:>+8.2f}{:>+9.4f}{:>+9.2f}{:>9.2f}{:>+9.2f}".format(
            n, statistics.mean(won), mw, ci, v_win, md, v_diff, charged, v_diff - charged),
            flush=True)
    print()
    print("  請求 = 名簿がその札から引いている額（{}のぶん）。釣り合い = 残存通貨 − 請求。"
          "マイナスが払い過ぎ".format(what))
    print("  控え: {}".format(store))
    print("PANEL DONE")


if __name__ == "__main__":
    main()
