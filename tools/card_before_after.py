# -*- coding: utf-8 -*-
"""札の前後比較を**同じ環境**で測る（handoff §3 落とし穴13・§7.142 訂正）。

改修前CSVと改修後CSVを別々に読んで2回測ると、相手の候補（dummies が名簿から
組む）と土台の変化が差に混ざる — 触っていない札でも +3.5〜5.8 動いた。
この計器は**改修前の札そのもの**（--before のCSVの行から Card を組み、兵法は
別名で登録）を今の環境（今の名簿・今の土台）へ置き、今の札と同じ (性格, 種) で
ペア比較する。

  python3 tools/card_before_after.py --before /path/to/old/sim/data 陳到〔白毦〕 陳武〔廬江〕
  （改修前CSVの取り出し: mkdir old && git archive <commit> sim/data | tar -x -C old --strip-components=2）

位置は合法な側へ自動で決める（槍・弓は後衛、騎兵・槍なし歩兵は前衛左で土台の
先頭を後衛へ）。土台の先頭は後衛に置ける札（槍か弓）にすること。並びは
`match.placement_errors` で検める。測れないもの: 土台に依存しない絶対値。
"""
import argparse, csv, io, os, statistics, sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
BASE = ["文聘〔江夏〕", "曹仁〔堅守〕", "郝昭〔陳倉〕", "李典〔慎重〕", "満寵〔剛毅〕"]


def _rows(path, key):
    text = open(path, encoding="utf-8-sig").read()
    return {r[key]: r for r in csv.DictReader(io.StringIO(text))}


def _old_card(before, name):
    from sim import rosterdata as R, field as F
    g = _rows(os.path.join(before, "generals.csv"), "名前")[name]
    sk = _rows(os.path.join(before, "skills.csv"), "兵法名")[g["兵法"]]
    alias = g["兵法"] + "(前)"
    F.SKILL_INFO[alias] = F._parse_skill(sk["効果"], sk["対象"])
    F.SKILL_TARGET[alias] = sk["対象"]
    return F.Card(
        cost=float(g["コスト"]), stat_cost=float(g["能力値コスト"]),
        typ=R.TYPE_MAP[g["兵種"]], role=R.ROLE_MAP[g["役割"]], name=g["名前"],
        trait=g["固有特性"], faction=g["勢力"], quote=g.get("台詞", ""),
        might=float(g["武力"]), wits=float(g["知力"]),
        fame_wits=float(g.get("知略") or 0.0), skill=alias,
        lean=float(g.get("役割寄せ") or 0.0),
        def_lean=float(g.get("防御寄せ") or 0.0),
        spd_lean=float(g.get("速度寄せ") or 0.0),
        floor_adj=float(g.get("床調整") or 0.0),
        spear=bool((g.get("槍") or "").strip()),
        gauge_cost=float(g["消費ゲージ%"]),
        gauge_rate=float(g["ゲージ上昇率"]) / 100.0,
        gauge_init=float(g["初期ゲージ"]))


def _run(job):
    name, which, opt = job
    from sim import rosterdata as R, field as F, match as M, dummies as D
    F.TRAITS.clear(); R.load_traits_into_field(); R.load_skills_into_field()
    F.TRAITS_ON = True
    roster = M._roster_cards()
    cards = {c.name: c for c in roster}
    c = cards[name] if which == "now" else _old_card(opt["before"], name)
    base = [cards[n] for n in opt["base"]]
    pos = opt["pos"]
    if pos == "auto":
        pos = "rear" if (c.typ == "arc" or (c.typ == "inf" and c.spear)) else "front"
    order = base + [c] if pos == "rear" else [c] + base[1:3] + [base[0]] + base[3:]
    army = F.Army(tuple(order), F.FORM_STANDARD)
    errs = M.placement_errors(army)
    if errs:
        raise SystemExit("置けない並び: " + "、".join(errs))
    cap = (opt["cap_name"], opt["cap"])
    out = {}
    for persona in D.PERSONAS:
        for seed in range(opt["seeds"]):
            opp = D.make_entry(roster, persona, seed, caps=(cap,)).units[0]
            out[(persona.name, seed)] = F.simulate(
                army, opp, dt=0.25, seed=seed * 7 + 1)["score"]
    return name, which, pos, out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cards", nargs="+")
    ap.add_argument("--before", required=True, help="改修前の sim/data の場所")
    ap.add_argument("--pos", choices=("auto", "rear", "front"), default="auto")
    ap.add_argument("--cap", default="官渡:30", help="戦場:上限（既定 官渡:30）")
    ap.add_argument("--seeds", type=int, default=40, help="性格ごとの種の数（12性格×N）")
    ap.add_argument("--base", default=",".join(BASE), help="土台5枚（カンマ区切り）")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    cap_name, cap = a.cap.split(":")
    opt = dict(before=a.before, pos=a.pos, cap_name=cap_name, cap=float(cap),
               seeds=a.seeds, base=a.base.split(","))
    jobs = [(x, w, opt) for x in a.cards for w in ("before", "now")]
    got = {}
    # Pool は _roster_cards() より先に作る（handoff §3 落とし穴11 の逆: 親は名簿を
    # 読まない。子が各自で読む）
    with Pool(a.workers, maxtasksperchild=1) as p:
        for name, which, pos, out in p.imap_unordered(_run, jobs):
            got[(name, which)] = (pos, out)
    print(f"{'武将':12s} 置き {'改修前':>7s} {'今':>7s} {'Δ勝率':>8s} {'±SE':>6s}  読み"
          f"   （{cap_name}{cap}・12性格×{a.seeds}種・同じ環境でペア）")
    for x in a.cards:
        pos, b = got[(x, "before")]; _, n = got[(x, "now")]
        ks = sorted(set(b) & set(n)); d = [n[k] - b[k] for k in ks]
        m = statistics.mean(d); se = statistics.pstdev(d) / max(1, len(d) - 1) ** 0.5
        tag = "同水準" if abs(m) < 2 * se else ("強く" if m > 0 else "弱く")
        wb = statistics.mean(b[k] for k in ks); wn = statistics.mean(n[k] for k in ks)
        print(f"{x:12s} {'後衛' if pos == 'rear' else '前衛'} {wb:7.1%} {wn:7.1%} {m:+8.2%} {se:6.2%}  {tag}")


if __name__ == "__main__":
    main()
