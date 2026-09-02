# -*- coding: utf-8 -*-
"""Reproducible balance instruments for 三国布陣.

Typical use::

    python3 tools/balance_suite.py run --profile quick
    python3 tools/balance_suite.py run --profile release \
        --output docs/balance/baselines/<commit>-release.json

The JSON is the evidence.  The adjacent Markdown is a human-readable
summary.  Every battle meter uses explicit seeds and both player sides.
"""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import math
import multiprocessing as mp
import os
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

try:  # script execution / module execution
    from . import balance_common as C
except ImportError:  # pragma: no cover - normal path for ``python tools/...``
    import balance_common as C

from sim import field as F
from sim import match as M
from sim import rosterdata as R
from tools import ladder_top as L


PROFILES = {
    # A change-time smoke test.  Historical full results remain in the fixture.
    "quick": {
        "target_seeds": range(600, 608),
        "pool_seeds": range(1),
        "archetype_members": 1,
        "archetype_seeds": range(1),
        "cadence_members": 1,
        "cadence_benchmarks": 2,
        "cadence_seeds": range(1),
    },
    # A normal balance checkpoint.
    "standard": {
        "target_seeds": range(600, 700),
        "pool_seeds": range(3),
        "archetype_members": 2,
        "archetype_seeds": range(2),
        "cadence_members": 3,
        "cadence_benchmarks": 6,
        "cadence_seeds": range(2),
    },
    # Release gate.  This is deliberately expensive and should not be tuned against.
    "release": {
        "target_seeds": range(600, 1100),
        "pool_seeds": range(10),
        "archetype_members": 3,
        "archetype_seeds": range(3),
        "cadence_members": 5,
        "cadence_benchmarks": 12,
        "cadence_seeds": range(3),
    },
}


def _timed_module_check(module: str) -> dict:
    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", module], cwd=C.ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240,
    )
    accepted = False
    accepted_note = ""
    if module == "sim.rosterdata" and proc.returncode != 0:
        # The current ledger deliberately carries exactly three recorded over-budget
        # exceptions (handoff §7.127).  rosterdata exits 1 for them, so treating every
        # non-zero code as a new regression makes the shared gate permanently red.
        marked = [line.strip() for line in proc.stdout.splitlines()
                  if "★許容超え" in line]
        known = ("諸葛恪〔元遜〕", "馬超〔錦馬超〕", "夏侯惇〔独眼〕")
        accepted = (len(marked) == 3 and all(any(name in line for line in marked)
                                             for name in known)
                    and "検算 NG が 3 件" in proc.stdout)
        if accepted:
            accepted_note = "既知の効果予算超過3枚だけ（handoff記録済み）"
    return {
        "module": module,
        "ok": proc.returncode == 0 or accepted,
        "returncode": proc.returncode,
        "accepted_known_exceptions": accepted,
        "accepted_note": accepted_note,
        "seconds": round(time.monotonic() - start, 2),
        "tail": "\n".join(proc.stdout.splitlines()[-12:]),
    }


def spec_report(data: Mapping, cards: Sequence[F.Card],
                run_modules: bool = True) -> dict:
    """Registration invariants and fixture drift checks."""
    idx = C.card_index(cards)
    errors: List[str] = []
    warnings: List[str] = []
    checked = 0

    # 版（§7.135）があるので枚数でなく**人物の数**で見る。同じ人物の別版は重複ではない
    people = [M.person_of(c) for c in cards]
    if len(set(people)) != 120:
        errors.append("名簿の人物数が120ではない: {}".format(len(set(people))))
    keys = [(M.person_of(c), R.version_of(c.name)) for c in cards]
    if len(set(keys)) != len(keys):
        errors.append("名簿に同一人物・同一版の重複がある")
    cost_counts = collections.Counter(int(c.cost) for c in cards)
    if cost_counts != collections.Counter({i: 12 for i in range(1, 11)}):
        warnings.append("コスト帯が従来の各12枚から変化: {}".format(dict(cost_counts)))

    for where, name, entry in C.all_fixture_entries(data, idx):
        checked += 1
        errs = M.validate(entry)
        if errs:
            errors.append("{} / {}: {}".format(where, name, "／".join(errs)))

    # Catch a stale snapshot even when the current cards still happen to be legal.
    for key, spec in data["sets"].items():
        entry = C.entry_from_spec(spec, idx)
        for i, (army, raw, (_reg, cap)) in enumerate(
                zip(entry.units, spec["armies"], M.REGULATIONS)):
            if abs(army.total_cost() - float(raw["total_cost"])) > 1e-9:
                errors.append("{} {}: fixture合計{} / 現在{}".format(
                    key, i, raw["total_cost"], army.total_cost()))
            if army.total_cost() > cap + 1e-9:
                errors.append("{} {}: 上限超過".format(key, i))

    blind = data["pools"]["final_blind"]
    if not blind["entries"]:
        warnings.append("final_blind は空。release判定前に別担当が封印データを追加する")
    if data["pools"]["special48"]["status"] != "retired_validation":
        errors.append("special48 の状態は retired_validation でなければならない")

    modules = []
    if run_modules:
        for module in ("sim.rosterdata", "sim.design"):
            try:
                result = _timed_module_check(module)
            except (subprocess.TimeoutExpired, OSError) as exc:
                result = {"module": module, "ok": False, "seconds": None,
                          "tail": str(exc)}
            modules.append(result)
            if not result["ok"]:
                errors.append("{} の検算が失敗".format(module))

    return {
        "ok": not errors,
        "fixture_entries_checked": checked,
        "roster_cards": len(cards),
        "cost_counts": dict(sorted(cost_counts.items())),
        "cadence_counts": C.counter_dict(cards, C.cadence),
        "errors": errors,
        "warnings": warnings,
        "module_checks": modules,
    }


def _eligible(card: F.Card, row: str) -> bool:
    if row == "front":
        return card.typ in M.FRONT_TYPES
    return card.typ in M.REAR_TYPES or (card.typ == F.INF and card.spear)


def _pool_distribution(entries: Sequence[Tuple[str, M.Entry]],
                       cards: Sequence[F.Card], apply_thresholds: bool = True) -> dict:
    observed = collections.Counter()
    expected = collections.Counter({c.name: 0.0 for c in cards})
    slot_counts = collections.Counter()
    for _name, entry in entries:
        for army in entry.units:
            nf = army.form.n_front
            for pos, card in enumerate(army.cards):
                observed[card.name] += 1
                row = "front" if pos < nf else "rear"
                slot_counts[row] += 1
                eligible = [c for c in cards if _eligible(c, row)]
                share = 1.0 / len(eligible)
                for c in eligible:
                    expected[c.name] += share

    def card_attr(card: F.Card, dimension: str):
        if dimension == "troop":
            return C.TYPE_JP[card.typ]
        if dimension == "role":
            return C.ROLE_JP[card.role]
        if dimension == "faction":
            return card.faction
        if dimension == "cost":
            return str(int(card.cost))
        if dimension == "cadence":
            return C.cadence(card)
        raise KeyError(dimension)

    category = {}
    for dim in ("troop", "role", "faction", "cost", "cadence"):
        obs, exp = collections.Counter(), collections.Counter()
        for c in cards:
            key = card_attr(c, dim)
            obs[key] += observed[c.name]
            exp[key] += expected[c.name]
        category[dim] = {
            key: {
                "observed": obs[key],
                "row_conditioned_expected": round(exp[key], 3),
                "lift": round(obs[key] / exp[key], 3) if exp[key] else None,
            }
            for key in sorted(set(obs) | set(exp), key=str)
        }

    freq = [observed[c.name] for c in cards]
    lifts = []
    for c in cards:
        exp = expected[c.name]
        lifts.append({
            "name": c.name,
            "observed": observed[c.name],
            "expected": round(exp, 3),
            "lift": round(observed[c.name] / exp, 3) if exp else None,
            "troop": C.TYPE_JP[c.typ],
            "role": C.ROLE_JP[c.role],
            "cost": c.cost,
            "cadence": C.cadence(c),
        })
    high = sorted(lifts, key=lambda x: (-(x["lift"] or 0), x["name"]))[:15]
    low = sorted((x for x in lifts if x["expected"] >= 1.0),
                 key=lambda x: ((x["lift"] or 0), x["name"]))[:15]
    top_prevalence = max(freq, default=0) / max(len(entries), 1)
    warnings = []
    eff = C.effective_count(freq)
    if apply_thresholds and eff < 80:
        warnings.append("有効カード枚数 {:.1f} < 80".format(eff))
    if apply_thresholds and top_prevalence > 0.50:
        warnings.append("最多カードが編成の {:.1%} に出現 > 50%".format(top_prevalence))
    extreme = [x for x in lifts if x["expected"] >= 1.0 and
               ((x["lift"] or 0) < 0.3 or (x["lift"] or 0) > 2.5)]
    if apply_thresholds and extreme:
        warnings.append("行条件補正後も採用liftが0.3未満または2.5超: {}枚".format(len(extreme)))
    return {
        "entries": len(entries),
        "slots": sum(freq),
        "row_slots": dict(slot_counts),
        "coverage": sum(v > 0 for v in freq),
        "roster": len(freq),
        "gini": round(C.gini(freq), 4),
        "effective_cards": round(eff, 2),
        "top_card_prevalence": round(top_prevalence, 4),
        "category": category,
        "highest_card_lift": high,
        "lowest_card_lift": low,
        "warnings": warnings,
        "note": "expected は前後配置の合法性だけで条件付けた近似。コスト上限・人物重複・生成器の好みは含まない",
    }


def distribution_report(data: Mapping, cards: Sequence[F.Card]) -> dict:
    idx = C.card_index(cards)
    pools = {}
    for key in ("official24", "special48"):
        pools[key] = _pool_distribution(C.pool_entries(data, key, idx), cards)
        pools[key]["status"] = data["pools"][key]["status"]
    strong = [C.named_set(data, key, idx) for key in ("chappy", "counter")]
    pools["strong_sets"] = _pool_distribution(strong, cards, apply_thresholds=False)
    pools["strong_sets"]["status"] = "diagnostic_only"
    return {"pools": pools}


def _battle_job(job):
    pool, foe_name, candidate, foe, reg, seed, side = job
    if side == "A":
        diff = M.play_one(candidate, foe, reg, dt=0.5, seed=seed)["diff"]
    else:
        diff = -M.play_one(foe, candidate, reg, dt=0.5, seed=seed)["diff"]
    return pool, foe_name, reg, seed, side, diff


def _run_pool(pool: str, candidate: M.Entry,
              opponents: Sequence[Tuple[str, M.Entry]], seeds: Sequence[int],
              jobs_n: int) -> dict:
    jobs = [(pool, name, candidate, foe, reg, seed, side)
            for name, foe in opponents
            for reg in range(len(M.REGULATIONS))
            for seed in seeds for side in ("A", "B")]
    with mp.Pool(jobs_n) as workers:
        rows = workers.map(_battle_job, jobs, chunksize=16)
    by_reg = []
    for reg, (label, _cap) in enumerate(M.REGULATIONS):
        ds = [row[-1] for row in rows if row[2] == reg]
        by_reg.append({
            "regulation": label,
            "wins": sum(d > 0 for d in ds),
            "games": len(ds),
            "win_rate": round(sum(d > 0 for d in ds) / len(ds), 4),
            "mean_diff": round(statistics.mean(ds), 6),
            "min_diff": round(min(ds), 6),
            "max_diff": round(max(ds), 6),
        })
    lookup = {(foe, reg, seed, side): diff
              for _pool, foe, reg, seed, side, diff in rows}
    bo3 = []
    per_foe = []
    for foe, _entry in opponents:
        foe_ds = [r[-1] for r in rows if r[1] == foe]
        foe_bo3 = []
        for seed in seeds:
            for side in ("A", "B"):
                foe_bo3.append(sum(lookup[foe, reg, seed, side] > 0
                                   for reg in range(3)) >= 2)
        bo3.extend(foe_bo3)
        per_foe.append({
            "name": foe,
            "single_win_rate": round(sum(d > 0 for d in foe_ds) / len(foe_ds), 4),
            "mean_diff": round(statistics.mean(foe_ds), 6),
            "bo3_wins": sum(foe_bo3),
            "bo3_games": len(foe_bo3),
        })
    return {
        "opponents": len(opponents),
        "seeds": list(seeds),
        "sides": ["A", "B"],
        "single_wins": sum(row[-1] > 0 for row in rows),
        "single_games": len(rows),
        "bo3_wins": sum(bo3),
        "bo3_games": len(bo3),
        "by_regulation": by_reg,
        "worst_opponents": sorted(per_foe, key=lambda x: (x["bo3_wins"] / x["bo3_games"],
                                                            x["mean_diff"]))[:12],
    }


def battle_report(data: Mapping, cards: Sequence[F.Card], profile: Mapping,
                  jobs_n: int) -> dict:
    idx = C.card_index(cards)
    candidate_name, candidate = C.named_set(data, "counter", idx)
    chappy_name, chappy = C.named_set(data, "chappy", idx)
    pools = {
        "chappy": ([(chappy_name, chappy)], list(profile["target_seeds"])),
        "official24": (C.pool_entries(data, "official24", idx),
                       list(profile["pool_seeds"])),
        "special48": (C.pool_entries(data, "special48", idx),
                      list(profile["pool_seeds"])),
    }
    if data["pools"]["final_blind"]["entries"]:
        pools["final_blind"] = (C.pool_entries(data, "final_blind", idx),
                                list(profile["pool_seeds"]))
    return {
        "candidate": candidate_name,
        "pools": {key: _run_pool(key, candidate, opponents, seeds, jobs_n)
                  for key, (opponents, seeds) in pools.items()},
        "note": "全測定を左右両側で実行。special48 は retired_validation で盲検ではない",
    }


def _archetype_job(job):
    an, ae, bn, be, reg, seed, side = job
    if side == "A":
        diff = M.play_one(ae, be, reg, dt=0.5, seed=seed)["diff"]
    else:
        diff = -M.play_one(be, ae, reg, dt=0.5, seed=seed)["diff"]
    return an, bn, diff


def archetype_report(cards: Sequence[F.Card], profile: Mapping, jobs_n: int) -> dict:
    entries = []
    members = profile["archetype_members"]
    for base, form_name, front, rear in L.COMBOS:
        for num in range(members):
            rng = random.Random("balance-suite/combo/{}/{}".format(base, num))
            units, used = [], set()
            for _label, cap in M.REGULATIONS:
                army, why = L._combo_army(cards, form_name, front, rear, cap, rng, used)
                if army is None:
                    raise RuntimeError("{}-{} を組めない: {}".format(base, num, why))
                units.append(army)
            entry = M.Entry(tuple(units), name=base)
            errs = M.validate(entry)
            if errs:
                raise RuntimeError("{}-{}: {}".format(base, num, errs))
            entries.append((base, "{}-{}".format(base, num), entry))

    jobs = []
    for (ak, an, ae), (bk, bn, be) in itertools.combinations(entries, 2):
        if ak == bk:
            continue
        for reg in range(3):
            for seed in profile["archetype_seeds"]:
                for side in ("A", "B"):
                    jobs.append((an, ae, bn, be, reg, seed, side))
    with mp.Pool(jobs_n) as workers:
        rows = workers.map(_archetype_job, jobs, chunksize=16)

    kind_of = {name: kind for kind, name, _e in entries}
    wins, games = collections.Counter(), collections.Counter()
    pair_wins, pair_games = collections.Counter(), collections.Counter()
    for an, bn, diff in rows:
        ak, bk = kind_of[an], kind_of[bn]
        games[ak] += 1
        games[bk] += 1
        pair_games[(ak, bk)] += 1
        pair_games[(bk, ak)] += 1
        if diff > 0:
            wins[ak] += 1
            pair_wins[(ak, bk)] += 1
        elif diff < 0:
            wins[bk] += 1
            pair_wins[(bk, ak)] += 1
    kinds = [row[0] for row in L.COMBOS]
    rates = {k: wins[k] / games[k] for k in kinds}
    matrix = {
        a: {b: (None if a == b else round(pair_wins[(a, b)] / pair_games[(a, b)], 4))
            for b in kinds}
        for a in kinds
    }
    dominant = [a for a in kinds if all(a == b or matrix[a][b] > 0.5 for b in kinds)]
    return {
        "members_per_archetype": members,
        "seeds": list(profile["archetype_seeds"]),
        "sides": ["A", "B"],
        "games": len(rows),
        "rates": {k: round(v, 4) for k, v in sorted(rates.items(), key=lambda kv: -kv[1])},
        "width_points": round((max(rates.values()) - min(rates.values())) * 100, 2),
        "dominant_archetypes": dominant,
        "matrix": matrix,
        "note": "同型ミラーを除いた7型総当たり。全局を左右両側で測定",
    }


def _weighted_sample(rng: random.Random, pool: Sequence[F.Card], count: int,
                     target_cost: float) -> List[F.Card]:
    available = list(pool)
    picked = []
    for _ in range(count):
        if not available:
            return []
        weights = [math.exp(-0.55 * abs(c.cost - target_cost)) for c in available]
        card = rng.choices(available, weights=weights, k=1)[0]
        available.remove(card)
        picked.append(card)
    return picked


def _cadence_army(cards: Sequence[F.Card], reg: int, form_name: str,
                  hand_count: int, sample: int, tag: str) -> F.Army | None:
    """Build a full-cost legal army with exactly ``hand_count`` rapid cards."""
    form = C.FORM_BY_NAME[form_name]
    nf, nr = form.n_front, 6 - form.n_front
    cap = M.REGULATIONS[reg][1]
    hand = [c for c in cards if C.cadence(c) == "手数"]
    normal = [c for c in cards if C.cadence(c) != "手数"]
    pools = {
        "hf": [c for c in hand if _eligible(c, "front")],
        "hr": [c for c in hand if _eligible(c, "rear")],
        "nf": [c for c in normal if _eligible(c, "front")],
        "nr": [c for c in normal if _eligible(c, "rear")],
    }
    splits = [(hf, hand_count - hf)
              for hf in range(hand_count + 1)
              if hf <= nf and hand_count - hf <= nr
              and hf <= len(pools["hf"]) and hand_count - hf <= len(pools["hr"])]
    if not splits:
        return None
    rng = random.Random("cadence/{}/{}/{}/{}/{}".format(tag, reg, form_name,
                                                         hand_count, sample))
    best, best_cost, full_seen = None, -1.0, 0
    target = cap / 6.0
    for _ in range(3500):
        hf, hr = rng.choice(splits)
        front = _weighted_sample(rng, pools["hf"], hf, target)
        rear = _weighted_sample(rng, pools["hr"], hr, target)
        used = {M.person_of(c) for c in front + rear}
        front_pool = [c for c in pools["nf"] if M.person_of(c) not in used]
        more_front = _weighted_sample(rng, front_pool, nf - hf, target)
        used.update(M.person_of(c) for c in more_front)
        rear_pool = [c for c in pools["nr"] if M.person_of(c) not in used]
        more_rear = _weighted_sample(rng, rear_pool, nr - hr, target)
        picked = front + more_front + rear + more_rear
        if len(picked) != 6 or len({M.person_of(c) for c in picked}) != 6:
            continue
        cost = sum(c.cost for c in picked)
        if cost > cap + 1e-9:
            continue
        army = F.Army(tuple(picked), form)
        if M.placement_errors(army):
            continue
        if abs(cost - cap) < 1e-9:
            full_seen += 1
            # Reservoir sampling keeps exact-cost samples diverse across sample seeds.
            if rng.randrange(full_seen) == 0:
                best, best_cost = army, cost
        elif full_seen == 0 and cost > best_cost:
            best, best_cost = army, cost
    return best if best is not None and abs(best_cost - cap) < 1e-9 else None


def _cadence_job(job):
    reg, form_name, k, candidate, benchmark, seed, side = job
    cap = M.REGULATIONS[reg][1]
    aa, bb = M.with_surplus(candidate, cap), M.with_surplus(benchmark, cap)
    if side == "A":
        diff = F.simulate(aa, bb, 0.5, seed=seed)["diff"]
    else:
        diff = -F.simulate(bb, aa, 0.5, seed=seed)["diff"]
    return reg, form_name, k, diff


def cadence_report(cards: Sequence[F.Card], profile: Mapping, jobs_n: int) -> dict:
    members = profile["cadence_members"]
    bench_n = profile["cadence_benchmarks"]
    generated = collections.defaultdict(list)
    missing = []
    for reg in range(3):
        for form_name in ("鶴翼", "魚鱗", "雁行"):
            max_hand = C.FORM_BY_NAME[form_name].n_front + min(
                6 - C.FORM_BY_NAME[form_name].n_front,
                sum(1 for c in cards if C.cadence(c) == "手数" and _eligible(c, "rear")),
            )
            for k in range(max_hand + 1):
                for n in range(members):
                    army = _cadence_army(cards, reg, form_name, k, n, "candidate")
                    if army is None:
                        missing.append("{} {} 手数{} #{}".format(
                            M.REGULATIONS[reg][0], form_name, k, n))
                    else:
                        generated[(reg, form_name, k)].append(army)
            for n in range(bench_n):
                army = _cadence_army(cards, reg, form_name, 0, n + 1000, "benchmark")
                if army is not None:
                    generated[(reg, form_name, "bench")].append(army)

    jobs = []
    for (reg, form_name, k), armies in generated.items():
        if k == "bench":
            continue
        benches = generated[(reg, form_name, "bench")]
        for candidate in armies:
            for benchmark in benches:
                for seed in profile["cadence_seeds"]:
                    for side in ("A", "B"):
                        jobs.append((reg, form_name, k, candidate, benchmark, seed, side))
    with mp.Pool(jobs_n) as workers:
        rows = workers.map(_cadence_job, jobs, chunksize=16)

    strata = {}
    for (reg, form_name, k), armies in generated.items():
        if k == "bench":
            continue
        ds = [d for r, f, kk, d in rows if (r, f, kk) == (reg, form_name, k)]
        if not ds:
            continue
        key = "{}|{}|{}".format(M.REGULATIONS[reg][0], form_name, k)
        strata[key] = {
            "regulation": M.REGULATIONS[reg][0],
            "formation": form_name,
            "hand_count": k,
            "candidate_armies": len(armies),
            "benchmark_armies": len(generated[(reg, form_name, "bench")]),
            "games": len(ds),
            "win_rate": round(sum(d > 0 for d in ds) / len(ds), 4),
            "mean_diff": round(statistics.mean(ds), 6),
        }

    aggregate = {}
    for k in sorted({v["hand_count"] for v in strata.values()}):
        rows_k = [v for v in strata.values() if v["hand_count"] == k]
        aggregate[str(k)] = {
            "strata": len(rows_k),
            "mean_win_rate": round(statistics.mean(v["win_rate"] for v in rows_k), 4),
            "mean_diff": round(statistics.mean(v["mean_diff"] for v in rows_k), 6),
        }
    marginal = {}
    for k in range(1, 7):
        deltas = []
        for v in strata.values():
            if v["hand_count"] != k:
                continue
            prev = next((x for x in strata.values()
                         if x["regulation"] == v["regulation"]
                         and x["formation"] == v["formation"]
                         and x["hand_count"] == k - 1), None)
            if prev:
                deltas.append(v["win_rate"] - prev["win_rate"])
        if deltas:
            marginal[str(k)] = {
                "comparable_strata": len(deltas),
                "mean_delta_points": round(100 * statistics.mean(deltas), 2),
            }
    warnings = []
    late = [v["mean_delta_points"] for k, v in marginal.items() if int(k) >= 4]
    if sum(x > 5 for x in late) >= 2:
        warnings.append("手数4枚以降も+5pt超の限界効果が複数続く。密度シナジーを要監視")
    if missing:
        warnings.append("満額で成立しない（または探索できない）手数密度層が{}件".format(len(missing)))
    return {
        "hand_cards": [c.name for c in cards if C.cadence(c) == "手数"],
        "members_per_stratum": members,
        "benchmark_armies_per_stratum": bench_n,
        "seeds": list(profile["cadence_seeds"]),
        "sides": ["A", "B"],
        "strata": strata,
        "aggregate": aggregate,
        "marginal": marginal,
        "warnings": warnings,
        "generation_failures": missing[:30],
        "note": ("同じ戦場・陣形・満額コスト内で、手数0枚の固定ベンチへ当てる。"
                 "札の人物差も含む実戦密度計器。1部隊だけの局所試験で、3部隊18人の"
                 "人物重複禁止による配分コストは含まない"),
    }


def _pct(wins: int, games: int) -> str:
    return "{:.1f}%".format(100 * wins / games) if games else "-"


def compare_reports(current: Mapping, baseline: Mapping) -> dict:
    """Compare only like-for-like sections and refuse silent protocol drift."""
    warnings = []
    compatible = True
    cm, bm = current["manifest"], baseline["manifest"]
    for key in ("profile", "dt"):
        if cm.get(key) != bm.get(key):
            compatible = False
            warnings.append("manifest {} が不一致: {} / {}".format(
                key, cm.get(key), bm.get(key)))
    if cm.get("protocol") != bm.get("protocol"):
        compatible = False
        warnings.append("protocol（seed・左右・相手集合）が不一致。勝率差を時系列比較しない")

    out = {"compatible_protocol": compatible, "warnings": warnings,
           "baseline_commit": bm.get("git", {}).get("commit"), "battle": {},
           "distribution": {}, "archetypes": {}, "cadence": {}}
    if compatible and current.get("battle") and baseline.get("battle"):
        for key in sorted(set(current["battle"]["pools"]) & set(baseline["battle"]["pools"])):
            now, old = current["battle"]["pools"][key], baseline["battle"]["pools"][key]
            nrate = now["single_wins"] / now["single_games"]
            orate = old["single_wins"] / old["single_games"]
            nb3 = now["bo3_wins"] / now["bo3_games"]
            ob3 = old["bo3_wins"] / old["bo3_games"]
            out["battle"][key] = {
                "single_delta_points": round(100 * (nrate - orate), 2),
                "bo3_delta_points": round(100 * (nb3 - ob3), 2),
            }
            if nrate - orate < -0.05:
                warnings.append("{} 単戦が基準から{:+.1f}pt".format(key, 100 * (nrate - orate)))
    if current.get("distribution") and baseline.get("distribution"):
        for key in sorted(set(current["distribution"]["pools"]) &
                          set(baseline["distribution"]["pools"])):
            now = current["distribution"]["pools"][key]
            old = baseline["distribution"]["pools"][key]
            out["distribution"][key] = {
                "effective_cards_delta": round(now["effective_cards"] - old["effective_cards"], 2),
                "gini_delta": round(now["gini"] - old["gini"], 4),
            }
    if compatible and current.get("archetypes") and baseline.get("archetypes"):
        nw, ow = current["archetypes"]["width_points"], baseline["archetypes"]["width_points"]
        out["archetypes"] = {
            "width_delta_points": round(nw - ow, 2),
            "new_dominant": sorted(set(current["archetypes"]["dominant_archetypes"]) -
                                   set(baseline["archetypes"]["dominant_archetypes"])),
        }
        if nw - ow > 10:
            warnings.append("7型の幅が基準から{:+.1f}pt".format(nw - ow))
        if out["archetypes"]["new_dominant"]:
            warnings.append("全型へ勝ち越す新しい型: {}".format(
                "、".join(out["archetypes"]["new_dominant"])))
    if compatible and current.get("cadence") and baseline.get("cadence"):
        for key in sorted(set(current["cadence"]["aggregate"]) &
                          set(baseline["cadence"]["aggregate"]), key=int):
            now = current["cadence"]["aggregate"][key]["mean_win_rate"]
            old = baseline["cadence"]["aggregate"][key]["mean_win_rate"]
            out["cadence"][key] = {"win_rate_delta_points": round(100 * (now - old), 2)}
    return out


def render_markdown(report: Mapping) -> str:
    m = report["manifest"]
    checks = report.get("checks")
    lines = [
        "# 三国布陣 バランス計器レポート",
        "",
        "- commit: `{}`{}".format(m["git"]["commit"][:12], "（dirty）" if m["git"]["dirty"] else ""),
        "- profile: `{}` / dt `{}`".format(m["profile"], m["dt"]),
        "- 実行時刻: {}".format(m["created_at_utc"]),
        "- fixture: `{}`（元commit `{}`）".format(m["fixture_file"],
                                                  m["fixture_source"].get("commit", "?")),
        "",
    ]
    if checks:
        lines += ["## 仕様・登録検算", "",
                  "**{}** — fixture {}編成、名簿{}枚。".format(
                      "PASS" if checks["ok"] else "FAIL",
                      checks["fixture_entries_checked"], checks["roster_cards"]), ""]
        for e in checks["errors"]:
            lines.append("- ❌ " + e)
        for w in checks["warnings"]:
            lines.append("- ⚠️ " + w)
        lines.append("")

    battle = report.get("battle")
    if battle:
        lines += ["## 固定サンプルへの実戦", "",
                  "候補: **{}**。すべて同じseedを左右両側で測定。".format(battle["candidate"]), "",
                  "| 相手集合 | 単戦 | BO3 |", "|---|---:|---:|"]
        for key, p in battle["pools"].items():
            lines.append("| {} | {}/{} ({}) | {}/{} ({}) |".format(
                key, p["single_wins"], p["single_games"],
                _pct(p["single_wins"], p["single_games"]),
                p["bo3_wins"], p["bo3_games"], _pct(p["bo3_wins"], p["bo3_games"])))
        lines += ["", "`special48` は最終調整に使用済みのため、現在は盲検ではありません。", ""]

    dist = report.get("distribution")
    if dist:
        lines += ["## 採用分布", "",
                  "| 集合 | 編成 | 網羅 | 有効カード | Gini | 警告 |",
                  "|---|---:|---:|---:|---:|---|"]
        for key, p in dist["pools"].items():
            lines.append("| {} | {} | {}/{} | {:.1f} | {:.3f} | {} |".format(
                key, p["entries"], p["coverage"], p.get("roster", 120), p["effective_cards"], p["gini"],
                "／".join(p["warnings"]) or "なし"))
        lines += ["", "liftは前衛・後衛の合法枠で補正済み。ただしコスト上限と生成器の好みは別軸です。", ""]

    arche = report.get("archetypes")
    if arche:
        lines += ["## 7型の総当たり", "",
                  "幅 **{:.1f}pt**／全型へ勝ち越す型: **{}**".format(
                      arche["width_points"], "、".join(arche["dominant_archetypes"]) or "なし"), "",
                  "| 型 | 勝率 |", "|---|---:|"]
        for k, v in arche["rates"].items():
            lines.append("| {} | {:.1f}% |".format(k, 100 * v))
        lines.append("")

    cadence = report.get("cadence")
    if cadence:
        lines += ["## 手数型の密度", "",
                  "| 1部隊の手数枚数 | 比較可能な層 | 対・手数0の平均勝率 | 平均差 |",
                  "|---:|---:|---:|---:|"]
        for k, v in cadence["aggregate"].items():
            lines.append("| {} | {} | {:.1f}% | {:+.4f} |".format(
                k, v["strata"], 100 * v["mean_win_rate"], v["mean_diff"]))
        if cadence["marginal"]:
            lines += ["", "限界効果（同じ戦場・陣形で前段との差）: " + "、".join(
                "{}枚 {:+.1f}pt".format(k, v["mean_delta_points"])
                for k, v in cadence["marginal"].items())]
        for w in cadence["warnings"]:
            lines.append("- ⚠️ " + w)
        lines.append("")
        lines.append("※ 1部隊だけの局所密度。手数札をここへ集中すると他2戦場で使えない、"
                     "という18人登録の機会費用は別途残ります。")
        lines.append("")

    hist = report.get("historical")
    if hist:
        lines += ["## 8986fc8で保存した従来結果", "",
                  "この欄は比較用の保存記録です。新計器は左右両側なので、条件を揃えず差分扱いしません。", ""]
        h = hist
        lines += [
            "- 対チャッピー: 汜水関 {}/{}、官渡 {}/{}、赤壁 {}/{}".format(
                h["chappy"][0]["wins"], h["chappy"][0]["games"],
                h["chappy"][1]["wins"], h["chappy"][1]["games"],
                h["chappy"][2]["wins"], h["chappy"][2]["games"]),
            "- 公式24: 単戦 {}/{}、BO3 {}/{}".format(
                h["official"]["single_wins"], h["official"]["single_games"],
                h["official"]["bo3_wins"], h["official"]["bo3_games"]),
            "- 未見・特殊48: 単戦 {}/{}、BO3 {}/{}".format(
                h["unseen"]["single_wins"], h["unseen"]["single_games"],
                h["unseen"]["bo3_wins"], h["unseen"]["bo3_games"]),
            "",
        ]
    comp = report.get("comparison")
    if comp:
        lines += ["## 基準レポートとの差", "",
                  "比較protocol: **{}**／基準commit `{}`".format(
                      "一致" if comp["compatible_protocol"] else "不一致",
                      (comp.get("baseline_commit") or "?")[:12]), ""]
        for key, v in comp["battle"].items():
            lines.append("- {}: 単戦 {:+.1f}pt / BO3 {:+.1f}pt".format(
                key, v["single_delta_points"], v["bo3_delta_points"]))
        if comp["archetypes"]:
            lines.append("- 7型の幅: {:+.1f}pt".format(
                comp["archetypes"]["width_delta_points"]))
        for w in comp["warnings"]:
            lines.append("- ⚠️ " + w)
        lines.append("")
    lines += ["## 読み方", "",
              "- 単一の総合点へ潰さない。登録検算・個札・採用分布・型相性・手数密度は別の計器。",
              "- 個札は `tools/one_ruler.py`、兵種と前線維持はこのレポートの型総当たりで確認する。",
              "- `tools/price_audit.py` の旧結果は不合法配置を含むため採否に使わない。",
              ""]
    return "\n".join(lines)


def build_report(command: str, profile_name: str, jobs_n: int,
                 run_modules: bool = True) -> dict:
    profile = PROFILES[profile_name]
    data = C.load_fixtures()
    cards = C.roster()
    sections = {"run": {"check", "distribution", "battle", "archetype", "cadence"},
                "check": {"check"}, "distribution": {"distribution"},
                "battle": {"battle"}, "archetype": {"archetype"},
                "cadence": {"cadence"}}[command]
    protocol = {
        "sections": sorted(sections),
        "battle_target_seeds": list(profile["target_seeds"]) if "battle" in sections else [],
        "battle_pool_seeds": list(profile["pool_seeds"]) if "battle" in sections else [],
        "sides": ["A", "B"] if sections & {"battle", "archetype", "cadence"} else [],
        "opponent_pools": ["chappy", "official24", "special48"] if "battle" in sections else [],
    }
    report = {
        "schema_version": 1,
        "manifest": C.manifest(command, profile_name, protocol, data),
        "historical": data["historical_verification"],
    }
    if "check" in sections:
        report["checks"] = spec_report(data, cards, run_modules=run_modules)
    if "distribution" in sections:
        report["distribution"] = distribution_report(data, cards)
    if "battle" in sections:
        report["battle"] = battle_report(data, cards, profile, jobs_n)
    if "archetype" in sections:
        report["archetypes"] = archetype_report(cards, profile, jobs_n)
    if "cadence" in sections:
        report["cadence"] = cadence_report(cards, profile, jobs_n)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="再現可能なバランス計器")
    ap.add_argument("command", choices=("run", "check", "distribution", "battle",
                                         "archetype", "cadence"), nargs="?", default="run")
    ap.add_argument("--profile", choices=tuple(PROFILES), default="quick")
    ap.add_argument("-j", "--jobs", type=int, default=min(8, os.cpu_count() or 4))
    ap.add_argument("--output", type=Path, help="JSON保存先。隣に同名Markdownも保存")
    ap.add_argument("--baseline", type=Path,
                    help="同じprofileの過去JSON。protocol一致時だけ差分を出す")
    ap.add_argument("--no-module-checks", action="store_true",
                    help="sim.rosterdata / sim.design の検算を省く")
    args = ap.parse_args(argv)
    report = build_report(args.command, args.profile, max(1, args.jobs),
                          run_modules=not args.no_module_checks)
    if args.baseline:
        base_path = args.baseline if args.baseline.is_absolute() else C.ROOT / args.baseline
        with base_path.open(encoding="utf-8") as fh:
            report["comparison"] = compare_reports(report, json.load(fh))
    markdown = render_markdown(report)
    print(markdown)
    if args.output:
        out = args.output if args.output.is_absolute() else C.ROOT / args.output
        C.write_json(out, report)
        md = out.with_suffix(".md")
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(markdown + "\n", encoding="utf-8")
        def shown(path: Path) -> str:
            try:
                return str(path.relative_to(C.ROOT))
            except ValueError:
                return str(path)
        print("保存: {} / {}".format(shown(out), shown(md)))
    return 0 if report.get("checks", {"ok": True})["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
