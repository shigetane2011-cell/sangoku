# -*- coding: utf-8 -*-
"""BO3 全体で「強い18人登録」を探す**赤チームの計器**（§7.148・テストプレイ提供・改修採用）。

    python3 tools/bo3_goodstuff_search.py --profile quick
    python3 tools/bo3_goodstuff_search.py --profile standard --jobs 3
    python3 tools/bo3_goodstuff_search.py --profile deep --seed 20260903 \\
        --output docs/balance/bo3-goodstuff.json

位置づけ:
    **再較正のたびに「壊れた18人構成」が生まれていないかを探す道具**であって、値付け
    調整の根拠には使わない（値札は skill_price / one_ruler / 実デッキの的で測る）。
    見つかった登録は「候補」であり、ラダーの相手が編成で応えれば崩れうる。

目的:
    - 汜水関18 / 官渡30 / 赤壁40 を同時に組む（3部隊18人・同一人物の別版も重複扱い）
    - 前衛/後衛の合法配置を守る（match.validate）
    - sim.match.play() の **BO3 勝敗を主目的**、3戦の残兵差合計は tanh で潰した補助指標
    - 宝物は探索しない（武将・陣形・配置だけ）

相手（旧版は official24 だけで、初期個体が世代0から BO3 100% に張り付いて探索が飽和した）:
    1. official24 の一部（在野の場・固定）
    2. **Hall of Fame の上位**（前世代までの強い登録。自分自身とは当てない）
       — 「在野を狩る探索」ではなく「強い18人登録どうしで勝つ探索」にする
    3. **dummies の12性格パネル**（薄く。特定の強者メタだけへの過適合を防ぐ）
    適応度 = 各群の BO3 勝率の重み付き平均 + 0.03·tanh(2·残兵差)

報告:
    - 上位候補どうしの **18人中の重複枚数** と **戦場別6人の重複枚数**、chappy／counter との重複
    - 最終候補は「上位から順に、既出と 18人中 max_overlap 枚以下の重なりのものだけ」を採る
      （同系統の微修正が並ぶのを避ける。省いた数も出す）
    - special48 は検証用だが**盲検ではない**（過去の調整に使用済み）。final_blind は空なので
      **release 判定には使わない**。

測れないもの:
    - 宝物込みの強さ・対人の読み合い・ラダーの相手が応えた後の強さ
    - 「この登録が強い理由」— それは skill_price / 型の総当たり（balance_suite archetype）で見る
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import multiprocessing as mp
import os
import random
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import field as F
from sim import match as M
from sim import dummies as D
from tools import balance_common as C


PROFILES = {
    "quick": {
        "population": 18, "generations": 10, "elite": 5, "mutations": (1, 2),
        "train_official": 6, "train_hall": 6, "train_personas": 4,
        "search_seeds": (0,),
        "validate_top": 4, "validate_seeds": (0, 1), "max_overlap": 14,
    },
    "standard": {
        "population": 30, "generations": 24, "elite": 8, "mutations": (1, 2, 3),
        "train_official": 10, "train_hall": 10, "train_personas": 6,
        "search_seeds": (0, 1),
        "validate_top": 6, "validate_seeds": (0, 1, 2, 3), "max_overlap": 14,
    },
    "deep": {
        "population": 48, "generations": 50, "elite": 12, "mutations": (1, 2, 3, 4),
        "train_official": 16, "train_hall": 16, "train_personas": 12,
        "search_seeds": (0, 1, 2),
        "validate_top": 8, "validate_seeds": tuple(range(8)), "max_overlap": 13,
    },
}
# 相手の群の重み（適応度）。在野を忘れず、強者メタへ寄せ、性格パネルは薄く。
GROUP_WEIGHT = {"official": 0.35, "hall": 0.45, "persona": 0.20}
HALL_CAP = 32                # Hall of Fame の保持数
FORM_NAME = {4: "鶴翼", 3: "魚鱗", 2: "雁行"}
# 対策札（§7.129/§7.130/§7.137）。自然選択されるかを報告するだけで、探索は偏らせない。
WATCH_CARDS = ("田豊〔剛直〕", "沮授〔監軍〕", "陸抗〔羊陸之交〕")


@dataclass(frozen=True)
class Metrics:
    matches: int
    match_win_rate: float          # 群の重み付き
    by_group: Dict[str, float]     # 群ごとの BO3 勝率
    mean_match_diff: float
    single_win_rate: Tuple[float, float, float]
    mean_diff_by_reg: Tuple[float, float, float]
    utility: float


def _form_name(form: F.Formation) -> str:
    return FORM_NAME.get(form.n_front, "前{}".format(form.n_front))


def _entry_key(entry: M.Entry) -> Tuple:
    return tuple((_form_name(a.form), tuple(c.name for c in a.cards)) for a in entry.units)


def _people(entry: M.Entry) -> set:
    return {M.person_of(c) for a in entry.units for c in a.cards}


def _people_by_reg(entry: M.Entry) -> List[set]:
    return [{M.person_of(c) for c in a.cards} for a in entry.units]


def overlap(a: M.Entry, b: M.Entry) -> Tuple[int, List[int]]:
    """18人中の重複枚数と、戦場別6人の重複枚数（人物で数える＝別版も同一）。"""
    total = len(_people(a) & _people(b))
    per = [len(x & y) for x, y in zip(_people_by_reg(a), _people_by_reg(b))]
    return total, per


def _eligible(card: F.Card, row: str) -> bool:
    if row == "front":
        return card.typ in M.FRONT_TYPES
    return card.typ in M.REAR_TYPES or (card.typ == F.INF and card.spear)


def _row_of(army: F.Army, slot: int) -> str:
    return "front" if slot < army.form.n_front else "rear"


def _legal_entry(entry: M.Entry) -> bool:
    return not M.validate(entry)


def _replace_army(entry: M.Entry, reg: int, army: F.Army) -> M.Entry:
    units = list(entry.units)
    units[reg] = army
    return M.Entry(tuple(units), name=entry.name)


def _replace_card(entry: M.Entry, cards: Sequence[F.Card], rng: random.Random) -> Optional[M.Entry]:
    """1枚だけ差し替える。戦場上限・人物重複・配置合法を守る。"""
    reg = rng.randrange(3)
    army = entry.unit(reg)
    slot = rng.randrange(M.UNIT_SIZE)
    row = _row_of(army, slot)
    old = army.cards[slot]
    cap = M.REGULATIONS[reg][1]
    used = _people(entry)
    used.discard(M.person_of(old))
    max_cost = cap - (army.total_cost() - old.cost)
    candidates = [c for c in cards
                  if M.person_of(c) not in used and c.name != old.name
                  and c.cost <= max_cost + 1e-9 and _eligible(c, row)]
    if not candidates:
        return None
    # 上限を使い切る方向をやや優先するが、満額は強制しない（余剰→初期ゲージも探索対象）。
    target = min(max_cost, old.cost + max(0.0, cap - army.total_cost()))
    candidates.sort(key=lambda c: abs(c.cost - target))
    new = rng.choice(candidates[: max(4, len(candidates) // 4)])
    picked = list(army.cards)
    picked[slot] = new
    out = _replace_army(entry, reg, F.Army(tuple(picked), army.form))
    return out if _legal_entry(out) else None


def _swap_between_armies(entry: M.Entry, rng: random.Random) -> Optional[M.Entry]:
    """異なる戦場の2枚を交換。18人の機会費用を直接探索する。"""
    r1, r2 = rng.sample(range(3), 2)
    a1, a2 = entry.unit(r1), entry.unit(r2)
    s1, s2 = rng.randrange(6), rng.randrange(6)
    c1, c2 = a1.cards[s1], a2.cards[s2]
    if not _eligible(c2, _row_of(a1, s1)) or not _eligible(c1, _row_of(a2, s2)):
        return None
    if a1.total_cost() - c1.cost + c2.cost > M.REGULATIONS[r1][1] + 1e-9:
        return None
    if a2.total_cost() - c2.cost + c1.cost > M.REGULATIONS[r2][1] + 1e-9:
        return None
    p1, p2 = list(a1.cards), list(a2.cards)
    p1[s1], p2[s2] = c2, c1
    units = list(entry.units)
    units[r1] = F.Army(tuple(p1), a1.form)
    units[r2] = F.Army(tuple(p2), a2.form)
    out = M.Entry(tuple(units), name=entry.name)
    return out if _legal_entry(out) else None


def _reorder_for_form(cards: Sequence[F.Card], form: F.Formation, rng: random.Random):
    """同じ6枚のまま別陣形へ合法に並べ直す。"""
    nf = form.n_front
    idxs = range(len(cards))
    legal = []
    for front_idx in itertools.combinations(idxs, nf):
        fs = set(front_idx)
        front = [cards[i] for i in idxs if i in fs]
        rear = [cards[i] for i in idxs if i not in fs]
        if all(_eligible(c, "front") for c in front) and all(_eligible(c, "rear") for c in rear):
            legal.append((front, rear))
    if not legal:
        return None
    front, rear = rng.choice(legal)
    rng.shuffle(front); rng.shuffle(rear)
    return tuple(front + rear)


def _change_formation(entry: M.Entry, rng: random.Random) -> Optional[M.Entry]:
    reg = rng.randrange(3)
    army = entry.unit(reg)
    forms = [f for f in C.FORM_BY_NAME.values() if f.n_front != army.form.n_front]
    rng.shuffle(forms)
    for form in forms:
        ordered = _reorder_for_form(army.cards, form, rng)
        if ordered is None:
            continue
        out = _replace_army(entry, reg, F.Army(ordered, form))
        if _legal_entry(out):
            return out
    return None


def _shuffle_positions(entry: M.Entry, rng: random.Random) -> Optional[M.Entry]:
    """同じ陣形・同じ6枚で列内の並びだけ変える。"""
    reg = rng.randrange(3)
    army = entry.unit(reg)
    nf = army.form.n_front
    picked = list(army.cards)
    row_slots = list(range(nf)) if rng.random() < 0.5 else list(range(nf, 6))
    if len(row_slots) < 2:
        return None
    i, j = rng.sample(row_slots, 2)
    picked[i], picked[j] = picked[j], picked[i]
    out = _replace_army(entry, reg, F.Army(tuple(picked), army.form))
    return out if _legal_entry(out) else None


def mutate(entry: M.Entry, cards: Sequence[F.Card], rng: random.Random, steps: int) -> M.Entry:
    cur = entry
    ops = (_replace_card, _swap_between_armies, _change_formation, _shuffle_positions)
    for _ in range(steps):
        for _attempt in range(20):
            op = rng.choices(ops, weights=(0.55, 0.20, 0.12, 0.13), k=1)[0]
            nxt = op(cur, cards, rng) if op is _replace_card else op(cur, rng)
            if nxt is not None and _entry_key(nxt) != _entry_key(cur):
                cur = nxt
                break
    return cur


def _duel_job(args):
    cid, group, cand, opp, seed, side = args
    if side == 0:
        r = M.play(cand, opp, dt=0.5, seed=seed)
        mpnt = 1.0 if r["wins_a"] > r["wins_b"] else (0.5 if r["wins_a"] == r["wins_b"] else 0.0)
        diff = r["diff"]; reg_diff = tuple(g["結果"]["diff"] for g in r["games"])
    else:
        r = M.play(opp, cand, dt=0.5, seed=seed)
        mpnt = 1.0 if r["wins_b"] > r["wins_a"] else (0.5 if r["wins_a"] == r["wins_b"] else 0.0)
        diff = -r["diff"]; reg_diff = tuple(-g["結果"]["diff"] for g in r["games"])
    return cid, group, mpnt, diff, reg_diff


def evaluate(entries: Sequence[M.Entry], opponents: Sequence[Tuple[str, M.Entry]],
             seeds: Sequence[int], jobs_n: int, weights: Mapping[str, float] = None) -> List[Metrics]:
    """opponents は (群, 登録) の列。自分自身（同じ鍵）とは当てない。"""
    weights = weights or GROUP_WEIGHT
    jobs = []
    keys = [_entry_key(e) for e in entries]
    for cid, cand in enumerate(entries):
        for group, opp in opponents:
            if _entry_key(opp) == keys[cid]:
                continue
            for seed in seeds:
                jobs.append((cid, group, cand, opp, seed, 0))
                jobs.append((cid, group, cand, opp, seed, 1))
    if jobs_n <= 1:
        rows = list(map(_duel_job, jobs))
    else:
        with mp.Pool(jobs_n) as pool:
            rows = pool.map(_duel_job, jobs, chunksize=16)
    by_id: Dict[int, list] = {i: [] for i in range(len(entries))}
    for row in rows:
        by_id[row[0]].append(row)
    out = []
    for cid in range(len(entries)):
        rs = by_id[cid]
        groups = {}
        for r in rs:
            groups.setdefault(r[1], []).append(r[2])
        by_group = {g: statistics.mean(v) for g, v in groups.items()}
        wsum = sum(weights.get(g, 0.0) for g in by_group)
        win = (sum(weights.get(g, 0.0) * v for g, v in by_group.items()) / wsum) if wsum > 0 else \
            statistics.mean(r[2] for r in rs)
        diffs = [r[3] for r in rs]
        regdiff = [[r[4][i] for r in rs] for i in range(3)]
        single = tuple(sum(1.0 if x > 0 else (0.5 if abs(x) <= 1e-12 else 0.0) for x in xs) / len(xs)
                       for xs in regdiff)
        mean_diff = statistics.mean(diffs)
        out.append(Metrics(matches=len(rs), match_win_rate=win, by_group=by_group,
                           mean_match_diff=mean_diff, single_win_rate=single,
                           mean_diff_by_reg=tuple(statistics.mean(xs) for xs in regdiff),
                           utility=win + 0.03 * math.tanh(mean_diff * 2.0)))
    return out


def _spec(entry: M.Entry) -> dict:
    armies = []
    for army, (reg, cap) in zip(entry.units, M.REGULATIONS):
        armies.append({
            "regulation": reg, "cap": cap, "formation": _form_name(army.form),
            "cost": army.total_cost(), "cards": [c.name for c in army.cards],
            "front": [c.name for c in army.cards[:army.form.n_front]],
            "rear": [c.name for c in army.cards[army.form.n_front:]],
            "types": {"歩": sum(c.typ == F.INF for c in army.cards), "騎": sum(c.typ == F.CAV for c in army.cards),
                      "弓": sum(c.typ == F.ARC for c in army.cards), "槍": sum(bool(c.spear) for c in army.cards)},
            "cadence": {k: sum(C.cadence(c) == k for c in army.cards) for k in ("手数", "標準", "大技")},
        })
    names = {c.name for a in entry.units for c in a.cards}
    return {"armies": armies, "watch_cards": [n for n in WATCH_CARDS if n in names]}


def _metrics_dict(m: Metrics) -> dict:
    return {"matches": m.matches, "bo3_win_rate": round(m.match_win_rate, 4),
            "by_group": {g: round(v, 4) for g, v in m.by_group.items()},
            "mean_match_diff": round(m.mean_match_diff, 6),
            "single_win_rate": [round(x, 4) for x in m.single_win_rate],
            "mean_diff_by_reg": [round(x, 6) for x in m.mean_diff_by_reg],
            "utility": round(m.utility, 6)}


def _load():
    data = C.load_fixtures()
    cards = C.roster()
    idx = C.card_index(cards)
    named = {k: C.named_set(data, k, idx)[1] for k in ("chappy", "counter") if k in data.get("sets", {})}
    official = [e for _n, e in C.pool_entries(data, "official24", idx)]
    special = [e for _n, e in C.pool_entries(data, "special48", idx)]
    final = [e for _n, e in C.pool_entries(data, "final_blind", idx)]
    return data, cards, named, official, special, final


def persona_entries(cards: Sequence[F.Card], n: int, seed: int) -> List[M.Entry]:
    """12性格を等間隔に n 人選び、3部隊18人の登録を作る（`dummies.make_entry`・規則どおり）。"""
    ps = list(D.PERSONAS)
    if n >= len(ps):
        chosen = ps
    else:
        step = len(ps) / n
        chosen = [ps[int(i * step)] for i in range(n)]
    return [D.make_entry(cards, p, seed) for p in chosen]


def _dedupe(entries: Iterable[M.Entry]) -> List[M.Entry]:
    seen, out = set(), []
    for e in entries:
        k = _entry_key(e)
        if k not in seen:
            seen.add(k); out.append(e)
    return out


def diverse_pick(ranked: Sequence[Tuple[M.Entry, Metrics]], top: int, max_overlap: int):
    """上位から順に、既出と 18人中 max_overlap 枚以下の重なりのものだけ採る。省いた数も返す。"""
    picked: List[Tuple[M.Entry, Metrics]] = []
    skipped = 0
    for e, m in ranked:
        if any(overlap(e, p)[0] > max_overlap for p, _ in picked):
            skipped += 1
            continue
        picked.append((e, m))
        if len(picked) >= top:
            break
    return picked, skipped


def search(args) -> dict:
    cfg = PROFILES[args.profile]
    data, cards, named, official, special, final_blind = _load()
    rng = random.Random(args.seed)
    starts = _dedupe(list(named.values()) + official)

    train_official = official[:]
    rng.shuffle(train_official)
    train_official = train_official[: min(cfg["train_official"], len(official))]
    personas = persona_entries(cards, cfg["train_personas"], args.seed)

    population = _dedupe(starts)
    while len(population) < cfg["population"]:
        child = mutate(rng.choice(population), cards, rng, rng.choice(cfg["mutations"]))
        population = _dedupe(population + [child])
    population = population[: cfg["population"]]

    # Hall of Fame: 初期は chappy／counter。以後は各世代の上位を積む（HALL_CAP まで）。
    hall: Dict[Tuple, Tuple[M.Entry, Metrics]] = {}
    hall_seed = list(named.values())
    history = []
    for gen in range(cfg["generations"]):
        hall_ranked = sorted(hall.values(), key=lambda x: x[1].utility, reverse=True)
        hall_opps = [e for e, _m in hall_ranked[: cfg["train_hall"]]] or hall_seed
        opponents = ([("official", e) for e in train_official]
                     + [("hall", e) for e in hall_opps]
                     + [("persona", e) for e in personas])
        metrics = evaluate(population, opponents, cfg["search_seeds"], args.jobs)
        ranked = sorted(zip(population, metrics),
                        key=lambda x: (x[1].utility, x[1].match_win_rate, x[1].mean_match_diff), reverse=True)
        for e, m in ranked[: max(cfg["elite"], 3)]:
            k = _entry_key(e)
            if k not in hall or m.utility > hall[k][1].utility:
                hall[k] = (e, m)
        if len(hall) > HALL_CAP:
            keep = sorted(hall.items(), key=lambda kv: kv[1][1].utility, reverse=True)[:HALL_CAP]
            hall = dict(keep)
        best_e, best_m = ranked[0]
        prev_best = history[-1]["best_key"] if history else None
        changed = prev_best is not None and _entry_key(best_e) != prev_best
        history.append({"generation": gen, "best": _metrics_dict(best_m), "best_key": _entry_key(best_e),
                        "changed": changed, "costs": [a.total_cost() for a in best_e.units],
                        "forms": [_form_name(a.form) for a in best_e.units],
                        "hall_size": len(hall), "hall_opponents": len(hall_opps)})
        g = best_m.by_group
        print("gen {:02d}  適応 {:5.1f}%  在野 {:5.1f}%  殿堂 {:5.1f}%  性格 {:5.1f}%  diff {:+.3f}  "
              "単戦 [{:3.0f},{:3.0f},{:3.0f}]  {}  {}{}".format(
                  gen, 100 * best_m.match_win_rate, 100 * g.get("official", float("nan")),
                  100 * g.get("hall", float("nan")), 100 * g.get("persona", float("nan")),
                  best_m.mean_match_diff, *(100 * x for x in best_m.single_win_rate),
                  "/".join("{:g}".format(a.total_cost()) for a in best_e.units),
                  "/".join(_form_name(a.form) for a in best_e.units), "  ←交代" if changed else ""), flush=True)
        elites = [e for e, _m in ranked[: cfg["elite"]]]
        parent_pool = [e for e, _m in ranked[: cfg["elite"] * 2]]
        next_pop = list(elites)
        tries = 0
        while len(next_pop) < cfg["population"] and tries < cfg["population"] * 100:
            tries += 1
            next_pop = _dedupe(next_pop + [mutate(rng.choice(parent_pool), cards, rng, rng.choice(cfg["mutations"]))])
        while len(next_pop) < cfg["population"]:
            next_pop = _dedupe(next_pop + [mutate(rng.choice(starts), cards, rng, rng.choice(cfg["mutations"]))])
        population = next_pop[: cfg["population"]]

    # 最終候補: 殿堂を official24 全部・性格パネル・殿堂どうしで再評価 → 多様性を見て採る
    finalists = [e for e, _m in sorted(hall.values(), key=lambda x: x[1].utility, reverse=True)[: cfg["validate_top"] * 3]]
    all_personas = persona_entries(cards, len(D.PERSONAS), args.seed)
    val_opps = ([("official", e) for e in official] + [("hall", e) for e in finalists]
                + [("persona", e) for e in all_personas])
    val_metrics = evaluate(finalists, val_opps, cfg["validate_seeds"], args.jobs)
    ranked_final = sorted(zip(finalists, val_metrics),
                          key=lambda x: (x[1].utility, x[1].match_win_rate, x[1].mean_match_diff), reverse=True)
    picked, skipped = diverse_pick(ranked_final, cfg["validate_top"], cfg["max_overlap"])
    top_entries = [e for e, _m in picked]
    special_metrics = evaluate(top_entries, [("special", e) for e in special], cfg["validate_seeds"], args.jobs,
                               weights={"special": 1.0}) if special else [None] * len(top_entries)
    blind_metrics = evaluate(top_entries, [("blind", e) for e in final_blind], cfg["validate_seeds"], args.jobs,
                             weights={"blind": 1.0}) if final_blind else [None] * len(top_entries)

    results = []
    for rank, ((entry, mv), ms, mb) in enumerate(zip(picked, special_metrics, blind_metrics), 1):
        ov = {}
        for k, e in named.items():
            t, per = overlap(entry, e)
            ov[k] = {"total": t, "per_reg": per}
        results.append({"rank": rank, "entry": _spec(entry), "validation": _metrics_dict(mv),
                        "special48": _metrics_dict(ms) if ms else None,
                        "final_blind": _metrics_dict(mb) if mb else None,
                        "overlap_with_named": ov})
    pair_overlap = []
    for i in range(len(top_entries)):
        for j in range(i + 1, len(top_entries)):
            t, per = overlap(top_entries[i], top_entries[j])
            pair_overlap.append({"a": i + 1, "b": j + 1, "total": t, "per_reg": per})
    for h in history:
        h.pop("best_key", None)
    return {
        "tool": "bo3_goodstuff_search", "profile": args.profile, "seed": args.seed, "jobs": args.jobs,
        "positioning": "再較正後に壊れた18人構成を探す赤チーム計器。値付け調整の根拠には使わない",
        "objective": "BO3 win rate (weighted: official24 / hall of fame / persona panel) primary; "
                     "mean 3-battle residual diff as a small tanh tie-breaker",
        "group_weight": GROUP_WEIGHT, "treasures": "not searched",
        "train": {"official": len(train_official), "hall_max": cfg["train_hall"], "personas": len(personas)},
        "search_seeds": list(cfg["search_seeds"]), "validation_seeds": list(cfg["validate_seeds"]),
        "max_overlap": cfg["max_overlap"], "skipped_as_same_lineage": skipped,
        "special48_warning": "retired_validation; already used in past tuning, not a true blind set",
        "final_blind_available": bool(final_blind),
        "history": history, "results": results, "pair_overlap": pair_overlap,
    }


def markdown(report: Mapping) -> str:
    lines = ["# BO3 グッドスタッフ18人探索（赤チーム計器）", "",
             "- 位置づけ: {}".format(report["positioning"]),
             "- profile: `{}` / seed: `{}`".format(report["profile"], report["seed"]),
             "- 目的関数: {}".format(report["objective"]),
             "- 相手: 在野 {official} + 殿堂 ≤{hall_max} + 性格 {personas}（重み {w}）".format(
                 **report["train"], w=report["group_weight"]),
             "- 宝物: **探索外**",
             "- special48: 検証用だが**盲検ではない**（過去の調整に使用済み）",
             "- final_blind: {}".format("あり" if report["final_blind_available"] else "空（release判定には使わない）"),
             "- 同系統として省いた候補: {}（18人中 {} 枚超の重なり）".format(
                 report["skipped_as_same_lineage"], report["max_overlap"]), ""]
    lines += ["## 世代ごとの最良（適応度＝重み付き BO3）", "", "| 世代 | 適応 | 在野 | 殿堂 | 性格 | 交代 | 陣形 |", "|---|---|---|---|---|---|---|"]
    for h in report["history"]:
        g = h["best"]["by_group"]
        lines.append("| {} | {:.1f}% | {:.1f}% | {:.1f}% | {:.1f}% | {} | {} |".format(
            h["generation"], 100 * h["best"]["bo3_win_rate"], 100 * g.get("official", 0), 100 * g.get("hall", 0),
            100 * g.get("persona", 0), "●" if h["changed"] else "", "/".join(h["forms"])))
    lines.append("")
    for row in report["results"]:
        v = row["validation"]; g = v["by_group"]
        lines += ["## #{}  検証 BO3 {:.1f}%（在野 {:.1f}／殿堂 {:.1f}／性格 {:.1f}）".format(
            row["rank"], 100 * v["bo3_win_rate"], 100 * g.get("official", 0), 100 * g.get("hall", 0), 100 * g.get("persona", 0)), ""]
        for a in row["entry"]["armies"]:
            lines += ["### {} — {} / cost {:g}".format(a["regulation"], a["formation"], a["cost"]),
                      "- 前: " + " / ".join(a["front"]), "- 後: " + " / ".join(a["rear"]),
                      "- 兵種: 歩{歩} 騎{騎} 弓{弓} 槍{槍} / 手数{手数} 標準{標準} 大技{大技}".format(**a["types"], **a["cadence"]), ""]
        lines.append("- 対策札: {}".format("・".join(row["entry"]["watch_cards"]) or "なし"))
        lines.append("- 単戦 [{:.1f}, {:.1f}, {:.1f}] / diff {:+.4f}".format(*(100 * x for x in v["single_win_rate"]), v["mean_match_diff"]))
        if row["special48"]:
            lines.append("- special48: BO3 {:.1f}% / 単戦 [{:.1f}, {:.1f}, {:.1f}]".format(
                100 * row["special48"]["bo3_win_rate"], *(100 * x for x in row["special48"]["single_win_rate"])))
        if row["final_blind"]:
            lines.append("- final_blind: BO3 {:.1f}%".format(100 * row["final_blind"]["bo3_win_rate"]))
        for k, ov in row["overlap_with_named"].items():
            lines.append("- {} との重複: 18人中 {}（戦場別 {}）".format(k, ov["total"], "/".join(map(str, ov["per_reg"]))))
        lines.append("")
    if report["pair_overlap"]:
        lines += ["## 上位候補どうしの重複（18人中／戦場別6人）", "", "| 組 | 18人中 | 汜水関 | 官渡 | 赤壁 |", "|---|---|---|---|---|"]
        for p in report["pair_overlap"]:
            lines.append("| #{}–#{} | {} | {} | {} | {} |".format(p["a"], p["b"], p["total"], *p["per_reg"]))
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", choices=PROFILES, default="quick")
    ap.add_argument("--seed", type=int, default=20260903)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--output", type=Path, default=ROOT / "docs" / "balance" / "bo3-goodstuff.json")
    args = ap.parse_args(argv)
    report = search(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md = args.output.with_suffix(".md")
    md.write_text(markdown(report) + "\n", encoding="utf-8")
    print("\nJSON:", args.output); print("Markdown:", md)
    if report["results"]:
        v = report["results"][0]["validation"]
        print("BEST 検証 BO3: {:.1f}%（在野 {:.1f}／殿堂 {:.1f}／性格 {:.1f}）".format(
            100 * v["bo3_win_rate"], *(100 * v["by_group"].get(k, 0) for k in ("official", "hall", "persona"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
