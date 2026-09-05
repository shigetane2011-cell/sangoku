# -*- coding: utf-8 -*-
"""実カード1枚の兵法（または特性）の値打ちを、実デッキの的で測る（§7.117・§7.153）。

    python3 tools/skill_panel.py 郝昭〔陳倉〕 蒋琬〔社稷〕          # 兵法を外した差
    python3 tools/skill_panel.py --trait 袁紹〔盟主〕 費禕〔大将軍〕   # 特性を外した差
    python3 tools/skill_panel.py --strip-trait 陸抗〔羊陸之交〕        # 特性を両案から外して兵法だけ
    python3 tools/skill_panel.py --effect 諸葛恪〔元遜〕 "ダメージ 威力1000%" 諸葛恪〔元遜〕  # 案の測定
    python3 tools/skill_panel.py --effect 周倉〔刀持ち〕 "兵法打消し 1発（30秒）|味方後衛" --gauge 周倉〔刀持ち〕 150,60 --cap 18 周倉〔刀持ち〕  # 対象と段も替えた案
    python3 tools/skill_panel.py --trait --trait-effect hakuba "移動速度 +30%（20秒）|enemy_retreat で発動 / 対象 自分 / 1戦3回まで" 公孫瓚〔白馬義従〕
    python3 tools/skill_panel.py --quick ...                          # 4性格×20種
    python3 tools/skill_panel.py --real-base 李典〔慎重〕                # 土台を実カードの性格デッキに

土台: その札 ＋ 合成の詰め物5枚（総コスト30・詰め物は同コスト・弓は後衛、
歩騎は前衛）。相手は実カードの性格パネル（12性格 × N種・官渡30）。
「あり／なし」を**同じ相手・同じ種**で対にして測る（差の分散が桁で小さい）。

物差しは**その札自身の1コスト点**（§7.156）。同じ札の能力値をちょうど1コスト点ぶん
削った版（兵法・特性・寄せ・詰め物は同じ）を同じ相手に当て、
  兵法の値打ち（コスト点） ＝ Δ（あり−なし） ÷ Δ（あり−1点削り）
で出す。勝率でも残存差でも同じ式なので、定数（4.86 勝点%/点）も詰め物の勾配も
要らない。以前の物差し（Δ勝率÷4.86・Δ残存差÷詰め物の局所勾配）は、詰め物の
1点が 4.86 の半分（2.47 勝点%）しか効かず、値を約2倍に膨らませていた（§7.156）。
`--filler-slope` で旧物差しも並べて出せる。

`--real-base` は土台を合成の詰め物ではなく**実カードの性格デッキ**にする（§7.158 の切り分け用）。
性格デッキ（既定6組・`--bases N`）の同じ兵種で最もコストの近い1枚をその札に差し替え、
1組の土台につき相手 240÷6 組を当てる（合計は同じ 240局・あり/なし/1点削りは同じ組で対）。
合成の土台で「弓の1点が騎の1点の 0.4倍」と出たのが土台のせいか弓の性質かを見る。

相手の数（性格 12 × 種 N）は**組み合わせの数**であって同じ対戦の繰り返しではない。
種ごとに相手の編成が変わり、1組の対戦は1局しか打たない。既定 N=20（240組）。
差が誤差の幅に収まるときだけ `--seeds 60`。

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
RATE = 4.86          # 旧物差し（--filler-slope）だけが使う
TOTAL = 30.0
DELTA = 2.0
_S = {}


def _minus_one(card, g):
    """同じ札から能力値をちょうど1コスト点ぶん削った札（物差し用）。

    盤面は 兵力 を stat_cost から、攻撃力 を 武力・知力 から作るので、
    stat_cost を 1 下げ、武力・知力 は「効果に1点多く払った設計」から引き直す。
    Card.cost は据え置く（詰め物の額と役割の混ぜ方が動かないように）。
    効果予算の上限（EFFECT_CAP）は外して引く — 上限に当たっている札は、
    上限を効かせると能力値が1点ぶん減らない。"""
    d = R.to_design(g)
    paid = d.cost - card.stat_cost          # 名簿がその札の効果に払っている額
    d2 = DS.Design(**{**d.__dict__, "effect": paid + 1.0})
    cap = DS.EFFECT_CAP
    DS.EFFECT_CAP = 99.0
    try:
        v = DS.derive(d2)
    finally:
        DS.EFFECT_CAP = cap
    return replace(card, stat_cost=card.stat_cost - 1.0,
                   might=round(v["武力"], 1), wits=round(v["知力"], 1))


def _apply_override(override):
    """効果文の差し替え（案の測定用）: 札名 → 効果文。本番の器で読み直す。
    鍵が "trait:<キー>" なら特性の差し替え（値は "効果文|備考"）。"""
    import re
    for name, eff in (override or {}).items():
        if name.startswith(("const:", "gauge:")):
            continue                         # 定数・段の診断は _init / _one が扱う
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
        # "効果文|対象" なら対象も差し替える（構えの案は対象＝味方後衛などで値打ちが決まる）
        text, tgt = (eff.split("|", 1) + [""])[:2]
        tgt = tgt.strip() or sk["対象"]
        F.SKILL_INFO[sk["兵法名"]] = F._parse_skill(text, tgt)
        F.SKILL_TARGET[sk["兵法名"]] = tgt


def _init(npers, seeds, override=None):
    global TOTAL
    TOTAL = float(os.environ.get("PANEL_TOTAL", TOTAL))   # --cap は子プロセスへ環境で渡す
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
    _S["rows"] = {g["名前"]: g for g in R.generals()}
    _S["opps"] = [D.make_entry(cards, p, s, caps=(("官渡", TOTAL),)).units[0]
                  for p in D.PERSONAS[:npers] for s in range(seeds)]
    nb = int(os.environ.get("PANEL_BASES", "0"))
    if nb > 0:                      # --real-base: 土台の性格デッキ（相手とは別の種）
        _S["bases"] = [D.make_entry(cards, D.PERSONAS[b % npers], 5000 + b,
                                    caps=(("官渡", TOTAL),)).units[0] for b in range(nb)]
        if os.environ.get("PANEL_BARE_BASE"):
            # --bare-base: 土台の味方から固有特性を全部外す（測る札は残す）。味方の
            # 誘発型（呼応・号令など「味方の兵法で発動」）が、測る札の兵法の値打ちに
            # 乗っていないかを切り分ける（§7.158 追記3）。
            _S["bases"] = [F.Army(tuple(replace(c, trait="", hidden_trait="") for c in a.cards), a.form)
                           for a in _S["bases"]]
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


def _swap_into(base, card):
    """実カードの土台へ差し替える。同じ人物がいればその枠、なければ同じ兵種で
    コストが最も近い枠（前衛/後衛は兵種で決まるので位置も正しく入る）。"""
    me = M.person_of(card)
    cards = list(base.cards)
    idx = [i for i, c in enumerate(cards) if M.person_of(c) == me]
    if not idx:
        same = [i for i, c in enumerate(cards) if c.typ == card.typ]
        pool = same or list(range(len(cards)))
        idx = [min(pool, key=lambda i: (abs(cards[i].cost - card.cost), i))]
    cards[idx[0]] = card
    return F.Army(tuple(cards), base.form)


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
    elif mode == "m1":                   # 物差し: 能力値を1コスト点ぶん削った同じ札
        c = _minus_one(c, _S["rows"][name])
    w, d = [], []
    bases = _S.get("bases")
    if bases:                            # 実カードの土台: 土台ごとに相手を分ける
        per = len(_S["opps"]) // len(bases)
        for b, base in enumerate(bases):
            a = _swap_into(base, c)
            for i in range(b * per, (b + 1) * per):
                r = F.simulate(a, _S["opps"][i], dt=DT, seed=i * 7 + 1)
                w.append(r["score"]); d.append(r["diff"])
        return w, d
    a = _army(c, bump)
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
    filler = "--filler-slope" in sys.argv   # 旧物差し（詰め物 ±2 点）も並べる
    if "--cap" in sys.argv:                 # 戦場の総コスト（既定 30＝官渡。18 なら汜水関の的）
        global TOTAL
        TOTAL = float(sys.argv[sys.argv.index("--cap") + 1])
        os.environ["PANEL_TOTAL"] = str(TOTAL)
    real_base = "--real-base" in sys.argv   # 土台を実カードの性格デッキに
    nbases = int(sys.argv[sys.argv.index("--bases") + 1]) if "--bases" in sys.argv else 6
    bare = "--bare-base" in sys.argv       # 土台の味方の特性を外す（--real-base と併用）
    if real_base:
        os.environ["PANEL_BASES"] = str(nbases)
        if bare:
            os.environ["PANEL_BARE_BASE"] = "1"
        filler = False                      # 詰め物が無いので旧物差しは出せない
    seeds = int(sys.argv[sys.argv.index("--seeds") + 1]) if "--seeds" in sys.argv \
        else 20
    npers = 4 if quick else len(D.PERSONAS)
    # 旗の引数は**位置で**外す。値で外すと --effect の札名と測る札名が同じ文字列の
    # とき両方消えて「0枚」になる（一度踏んだ）。
    skip = set()
    width = {"--seeds": 1, "--effect": 2, "--trait-effect": 2, "--const": 1, "--gauge": 2,
             "--bases": 1, "--cap": 1}
    for i, a in enumerate(sys.argv):        # 同じ旗が複数回出ても全部の引数を除く
        if a in width:
            skip.update(range(i + 1, i + 1 + width[a]))
    names = [a for i, a in enumerate(sys.argv) if i > 0 and i not in skip
             and not a.startswith("--")]
    store = os.environ.get("PANEL_STORE", os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".skill_panel_cache.json"))
    try:
        got = json.load(open(store))
    except Exception:
        got = {}
    what = "特性" if trait else ("兵法（特性なし）" if strip else "兵法")
    if real_base:
        what += "・実カードの土台{}{}".format(nbases, "・特性なし" if bare else "")
    if TOTAL != 30.0:
        what += "・総コスト{:g}".format(TOTAL)
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
    # 札の能力値も鍵に入れる（regenerate で引き直すと同じ効果文でも別の札になる）。
    for g in R.generals():
        fp[g["名前"]] = "{}|c{}/s{}/m{}/w{}".format(
            fp.get(g["名前"], ""), g["コスト"], g["能力値コスト"], g["武力"], g["知力"])
    print("{} 枚の{}を外した差 × 性格 {} × 種 {} ＝ 1案 {}局（1組1局・組み合わせ {} 通り）".format(
        len(names), what, npers, seeds, npers * seeds, npers * seeds), flush=True)
    variants = [("on", 0.0), ("off", 0.0), ("m1", 0.0)]
    if filler:
        variants += [("on", DELTA), ("on", -DELTA)]
    jobs = []
    for n in names:
        for mode, bump in variants:
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
    rows = {g["名前"]: g for g in R.generals()}
    print()
    hdr = "{:<14}{:>6}{:>8}{:>8}{:>7}{:>9}{:>9}{:>7}{:>7}{:>7}{:>7}".format(
        "武将", "土台勝率", "Δ勝率", "1点Δ勝", "勝率点", "Δ残存差", "1点Δ残", "残存点", "±", "請求", "釣り合い")
    if filler:
        hdr += "{:>8}{:>8}".format("旧勝率", "旧残存")
    print(hdr)
    for n in names:
        key = lambda mode, bump: "{}|{}|{}|{}|{}|{}".format(n, what, mode, bump, npers * seeds, fp.get(n, ""))
        won, don = got[key("on", 0.0)]
        woff, doff = got[key("off", 0.0)]
        wm1, dm1 = got[key("m1", 0.0)]
        dw = [a - b for a, b in zip(won, woff)]       # 兵法の差（勝率）
        sw = [a - b for a, b in zip(won, wm1)]        # 1コスト点の差（勝率）
        dd = [a - b for a, b in zip(don, doff)]       # 兵法の差（残存差）
        sd = [a - b for a, b in zip(don, dm1)]        # 1コスト点の差（残存差）
        n_ = len(dw)
        mw, ms = statistics.mean(dw), statistics.mean(sw)
        md, msd = statistics.mean(dd), statistics.mean(sd)
        ci = lambda xs: 1.96 * statistics.pstdev(xs) / (n_ ** 0.5)
        v_win = mw / ms if abs(ms) > 1e-9 else float("nan")
        v_diff = md / msd if abs(msd) > 1e-9 else float("nan")
        # 比の誤差（両方の相対誤差を足し合わせる近似）
        rel = ((ci(dd) / md) ** 2 + (ci(sd) / msd) ** 2) ** 0.5 if md and msd else float("nan")
        g = rows[n]
        ks = [k.strip() for k in (g["固有特性"] or "").replace("／", "/").split("/") if k.strip()]
        if trait:
            charged = sum(DS.trait_value(k, g["人物"]) for k in ks)
        else:
            charged = float(g["効果予算"]) - sum(DS.trait_value(k, g["人物"]) for k in ks)
        line = "{:<14}{:>7.1%}{:>+8.2%}{:>+8.2%}{:>+7.2f}{:>+9.4f}{:>+9.4f}{:>+7.2f}{:>7.2f}{:>7.2f}{:>+7.2f}".format(
            n, statistics.mean(won), mw, ms, v_win, md, msd, v_diff,
            abs(v_diff * rel) if rel == rel else float("nan"), charged, v_diff - charged)
        if filler:
            _, dhi = got[key("on", DELTA)]
            _, dlo = got[key("on", -DELTA)]
            slope = (statistics.mean(dhi) - statistics.mean(dlo)) / (2.0 * DELTA)
            line += "{:>+8.2f}{:>+8.2f}".format(mw * 100.0 / RATE,
                                                md / slope if abs(slope) > 1e-9 else float("nan"))
        print(line, flush=True)
    print()
    print("  1点Δ勝・1点Δ残 = 同じ札の能力値を1コスト点ぶん削ったときの差（物差し）。")
    print("  勝率点・残存点 = 兵法の差 ÷ 物差し（＝コスト点）。± は残存点の誤差（95%）。")
    print("  請求 = 名簿がその札から引いている額（{}のぶん）。釣り合い = 残存点 − 請求。"
          "マイナスが払い過ぎ".format(what))
    print("  控え: {}".format(store))
    print("PANEL DONE")


if __name__ == "__main__":
    main()
