# -*- coding: utf-8 -*-
"""兵種の3辺を実デッキの7型で測る（§7.145・handoff §3 落とし穴16）。

    python3 tools/type_edges.py --tag now --members 8 --seeds 2            # いまの盤面
    python3 tools/type_edges.py --tag t1 --members 8 --seeds 2 --full      # 7型全部の総当たり
    python3 tools/type_edges.py --tag t2 --const "SUPPRESS_MAX=0.8;FOCUS=1.5" --act "cav=1.4;arc=0.45"

7つの型（ladder_top.COMBOS）を実デッキの器で組み、騎型{③⑤}・弓型{②⑥}・歩型{④⑦}の
3辺（騎→弓・弓→歩・歩→騎）の勝率差と、型ごとの勝率を出す。
つまみ: --const（field の定数を上書き）・--edge（三すくみ表 TYPE_EDGE_COST）・--act（ACT_COEF。
scratch に名簿を複製して regenerate してから測る — 実行時差し替えだけでは部分適用）。
**1型あたりの人数（--members）は「引いた札の組」の数で rng 固定。** 2組では過適合するので
8組以上で読む（§7.145）。出力は <out>/edge_<tag>.json（辺・型・組ごとの勝率・全局）。
測れないもの: 個札の値。土台は COMBOS の型に固定。
"""

import argparse, itertools, json, os, random, shutil, statistics, sys, time
from collections import Counter, defaultdict
from multiprocessing import Pool
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "tools"))
HERE = os.environ.get("TYPE_EDGES_OUT", os.path.join(REPO, "docs", "balance", "experiments"))
KIND = {"③鶴騎4弓2": "騎", "⑤鶴騎3歩1": "騎", "②雁2弓4": "弓", "⑥雁2弓3槍": "弓", "④鶴歩4弓2": "歩", "⑦魚3弓2槍": "歩", "①魚3弓3": "均"}
EDGES = (("騎", "弓"), ("弓", "歩"), ("歩", "騎"))
def parse_kv(s, conv=float):
    out = {}
    for part in (s or "").split(";"):
        if part.strip():
            k, v = part.split("="); out[k.strip()] = conv(v)
    return out
def apply_knobs(F, opt):
    for k, v in opt.get("const", {}).items():
        if ":" in k:      # 辞書の要素: TYPE_MEN_SPLIT:ARC=0.55（兵種は F の定数名で引く）
            name, key = k.split(":", 1)
            getattr(F, name)[getattr(F, key.upper(), key)] = v
            continue
        setattr(F, k, v)
    for k, v in opt["edge"].items():
        a, b = k.split(","); F.TYPE_EDGE_COST[(getattr(F, a.upper()), getattr(F, b.upper()))] = v
    for k, v in opt["act"].items():
        F.ACT_COEF[getattr(F, k.upper())] = v
    F.sync_type_atk()      # 6マスの兵種攻撃表を定数から作り直す（§7.151）
def _setup(opt):
    from sim import rosterdata as R, field as F, match as M
    if opt["data"]:
        R.DATA = opt["data"]; R._TILT_CACHE.clear()
    apply_knobs(F, opt)
    F.TRAITS.clear(); R.load_traits_into_field(); R.load_skills_into_field(); F.TRAITS_ON = F.SKILLS_ON = True
    return R, F, M
def build_entries(opt):
    R, F, M = _setup(opt)
    import ladder_top as L
    if opt.get("share") is not None: L.FRONT_SHARE_COMBO = opt["share"]
    cards = M._roster_cards(); ents = []
    for name, form_name, front, rear in L.COMBOS:
        for num in range(opt["members"]):
            rng = random.Random("combo/{}/{}".format(name, num)); units, used = [], set()
            for _lab, cap in M.REGULATIONS:
                a, why = L._combo_army(cards, form_name, front, rear, cap, rng, used)
                assert a is not None, (name, num, _lab, why)
                units.append(a)
            e = M.Entry(tuple(units), name=name); assert not M.validate(e), (name, M.validate(e))
            ents.append((name, "{}-{}".format(name, num), e))
    return ents
_ENTS = None
def _init(opt):
    global _ENTS, _OPT
    _OPT = opt; _ENTS = {n: e for _, n, e in build_entries(opt)}
def _duel(job):
    from sim import match as M
    na, nb, reg, seed = job
    return (na, nb, M.play_one(_ENTS[na], _ENTS[nb], reg, dt=0.5, seed=seed)["diff"])
def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--tag", required=True); ap.add_argument("--members", type=int, default=2)
    ap.add_argument("--seeds", type=int, default=3); ap.add_argument("--edge", default=""); ap.add_argument("--act", default="")
    ap.add_argument("--full", action="store_true"); ap.add_argument("--const", default="", help="F の定数を上書き: CAV_COVER=0;COVER_SHARE=0"); ap.add_argument("--share", type=float, default=None, help="計器側: 前衛へ回す予算の割合（既定 0.55）"); ap.add_argument("-j", type=int, default=4)
    a = ap.parse_args()
    opt = {"edge": parse_kv(a.edge), "act": parse_kv(a.act), "const": parse_kv(a.const), "members": a.members, "data": "", "share": a.share}
    if opt["act"]:
        scratch = os.path.join(HERE, "data_" + a.tag); shutil.rmtree(scratch, ignore_errors=True); shutil.copytree(os.path.join(REPO, "sim/data"), scratch)
        opt["data"] = scratch
        from sim import rosterdata as R, field as F
        R.DATA = scratch; R._TILT_CACHE.clear(); apply_knobs(F, opt); R.regenerate()
    ents = build_entries(opt); names = [n for _, n, _ in ents]; kind = {n: KIND[k] for k, n, _ in ents}
    pairs = []
    for (ka, na, _), (kb, nb, _) in itertools.combinations(ents, 2):
        if ka == kb: continue
        if not a.full and not ((kind[na], kind[nb]) in EDGES or (kind[nb], kind[na]) in EDGES): continue
        pairs.append((na, nb))
    from sim import match as M
    jobs = [(na, nb, reg, sd) for na, nb in pairs for reg in range(len(M.REGULATIONS)) for sd in range(a.seeds)]
    t0 = time.time()
    with Pool(a.j, initializer=_init, initargs=(opt,)) as p:
        res = p.map(_duel, jobs, chunksize=16)
    win, games = Counter(), Counter(); pw, pg = Counter(), Counter()
    for na, nb, diff in res:
        ka, kb = kind[na], kind[nb]
        for x, y, d in ((na, nb, diff), (nb, na, -diff)):
            games[x] += 1; pg[(kind[x], kind[y])] += 1
            if d > 0: win[x] += 1; pw[(kind[x], kind[y])] += 1
    per = defaultdict(list)
    for n in names:
        if games[n]: per[n.split("-")[0]].append(100.0 * win[n] / games[n])
    edges = {f"{x}→{y}": 100.0 * pw[(x, y)] / pg[(x, y)] - 50.0 for x, y in EDGES if pg[(x, y)]}
    out = {"tag": a.tag, "edge_knobs": opt["edge"], "act_knobs": opt["act"], "const": opt["const"], "share": a.share, "members": a.members, "seeds": a.seeds, "full": a.full,
           "edges": edges, "types": {k: statistics.mean(v) for k, v in per.items()}, "duels": len(jobs), "secs": round(time.time() - t0),
           "members_rate": {n: (100.0 * win[n] / games[n] if games[n] else None) for n in names},
           "rows": [[na, nb, round(d, 4)] for na, nb, d in res]}
    json.dump(out, open(os.path.join(HERE, f"edge_{a.tag}.json"), "w"), ensure_ascii=False, indent=1)
    print(f"[{a.tag}] 辺: " + "  ".join(f"{k} {v:+.1f}" for k, v in edges.items()) + f"   （{len(jobs)}局・{out['secs']}s）")
    print("  型: " + "  ".join(f"{k} {v:.1f}%" for k, v in sorted(out["types"].items(), key=lambda kv: -kv[1])))
if __name__ == "__main__":
    main()
