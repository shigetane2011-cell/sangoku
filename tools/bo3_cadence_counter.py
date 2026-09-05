# -*- coding: utf-8 -*-
"""BO3で手数型8枚を3/3/2配分した登録と、対策札の面数を測る。

同一人物を3部隊で再利用できない実際の18人登録で、対策を0/1/2/3戦場に
置いたときのBO3への効きを測る。対策枝は同コスト・同兵種・同役割の札と
交換し、札差を含む実戦上の機会費用も測定へ含める。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import multiprocessing as mp
import random
import statistics
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import field as F
from sim import match as M
from tools import balance_common as C
from tools import balance_suite as B


PROFILES = {
    "quick": {"members": 2, "seeds": range(8)},
    "standard": {"members": 3, "seeds": range(16)},
}
RAPID_SPLIT = (3, 3, 2)
RAPID_ASSIGNMENTS = (
    ("鶴翼", ("全琮〔護軍〕", "楽綝〔揚州〕", "紀霊〔三尖刀〕"), ()),
    ("魚鱗", ("陳武〔廬江〕", "高順〔陥陣〕"), ("李典〔慎重〕",)),
    ("魚鱗", ("夏侯淵〔神速〕",), ("韓当〔老弓〕",)),
)
SWAPS = (
    ("賈詡〔毒士〕", "沮授〔監軍〕", "持重"),
    ("貂蝉〔傾国〕", "田豊〔剛直〕", "打消し"),
    ("張飛〔当陽橋〕", "陸抗〔羊陸之交〕", "節制"),
)


def _weighted_pick(rng, pool, n, target):
    return B._weighted_sample(rng, pool, n, target)


def _rapid_entry(cards: Sequence[F.Card], sample: int) -> M.Entry:
    idx = C.card_index(cards)
    rapid_people = {M.person_of(c) for c in cards if C.cadence(c) == "手数"}
    used, armies = set(), []
    for reg, (form_name, fixed_front_names, fixed_rear_names) in enumerate(
            RAPID_ASSIGNMENTS):
        form = C.FORM_BY_NAME[form_name]
        fixed_front = [idx[n] for n in fixed_front_names]
        fixed_rear = [idx[n] for n in fixed_rear_names]
        need_front = form.n_front - len(fixed_front)
        need_rear = 6 - form.n_front - len(fixed_rear)
        cap = M.REGULATIONS[reg][1]
        fixed_cost = sum(c.cost for c in fixed_front + fixed_rear)
        rng = random.Random("bo3-rapid/{}/{}".format(sample, reg))
        allowed = [c for c in cards if C.cadence(c) != "手数"
                   and M.person_of(c) not in used
                   and M.person_of(c) not in rapid_people]
        front_pool = [c for c in allowed if B._eligible(c, "front")]
        rear_pool = [c for c in allowed if B._eligible(c, "rear")]
        target = (cap - fixed_cost) / max(need_front + need_rear, 1)
        army = None
        for _ in range(30000):
            front = _weighted_pick(rng, front_pool, need_front, target)
            taken = {M.person_of(c) for c in front}
            rear = _weighted_pick(rng, [c for c in rear_pool
                                        if M.person_of(c) not in taken],
                                  need_rear, target)
            picked = fixed_front + front + fixed_rear + rear
            if len(picked) != 6 or len({M.person_of(c) for c in picked}) != 6:
                continue
            candidate = F.Army(tuple(picked), form)
            if abs(candidate.total_cost() - cap) < 1e-9 and not M.placement_errors(candidate):
                army = candidate
                break
        if army is None:
            raise RuntimeError("{} の手数登録を満額にできない".format(
                M.REGULATIONS[reg][0]))
        armies.append(army)
        used.update(M.person_of(c) for c in army.cards)
    entry = M.Entry(tuple(armies), name="手数8枚-{}".format(sample))
    rapid = [c for a in armies for c in a.cards if C.cadence(c) == "手数"]
    if len(rapid) != 8 or M.validate(entry):
        raise RuntimeError("手数登録が不正: " + "／".join(M.validate(entry)))
    return entry


def _army_with_anchor(cards, reg, form_name, anchor_name, used_people,
                      banned_people, sample):
    idx = C.card_index(cards)
    anchor = idx[anchor_name]
    form = C.FORM_BY_NAME[form_name]
    nf, nr = form.n_front, 6 - form.n_front
    row = "front" if reg == 2 else "rear"
    need_front, need_rear = ((nf - 1, nr) if row == "front" else (nf, nr - 1))
    cap = M.REGULATIONS[reg][1]
    rng = random.Random("bo3-defense/{}/{}/{}".format(sample, reg, form_name))
    allowed = [c for c in cards
               if M.person_of(c) not in used_people
               and (M.person_of(c) not in banned_people or c.name == anchor_name)
               and C.cadence(c) != "手数" and c.name != anchor_name]
    front_pool = [c for c in allowed if B._eligible(c, "front")]
    rear_pool = [c for c in allowed if B._eligible(c, "rear")]
    target = max((cap - anchor.cost) / 5.0, 1.0)
    for _ in range(30000):
        front = _weighted_pick(rng, front_pool, need_front, target)
        taken = {M.person_of(c) for c in front}
        rear = _weighted_pick(rng, [c for c in rear_pool
                                    if M.person_of(c) not in taken],
                              need_rear, target)
        picked = ([anchor] + front + rear) if row == "front" else (front + [anchor] + rear)
        if len(picked) != 6 or len({M.person_of(c) for c in picked}) != 6:
            continue
        army = F.Army(tuple(picked), form)
        if abs(army.total_cost() - cap) < 1e-9 and not M.placement_errors(army):
            return army
    raise RuntimeError("{} {} で満額部隊を生成できない".format(
        M.REGULATIONS[reg][0], anchor_name))


def _defense_entry(cards, sample):
    forms = ("鶴翼", "魚鱗", "鶴翼")
    banned = {M.person_of(c) for c in cards if C.cadence(c) == "手数"}
    for base, counter, _label in SWAPS:
        banned.add(M.person_of(C.card_index(cards)[base]))
        banned.add(M.person_of(C.card_index(cards)[counter]))
    used, armies = set(), []
    for reg, ((base, _counter, _label), form) in enumerate(zip(SWAPS, forms)):
        army = _army_with_anchor(cards, reg, form, base, used, banned, sample)
        armies.append(army)
        used.update(M.person_of(c) for c in army.cards)
    entry = M.Entry(tuple(armies), name="対策台-{}".format(sample))
    if M.validate(entry):
        raise RuntimeError("対策台が不正: " + "／".join(M.validate(entry)))
    return entry


def _variant(entry, cards, mask):
    armies = []
    for reg, (army, (base, counter, _label)) in enumerate(zip(entry.units, SWAPS)):
        use = counter if mask & (1 << reg) else base
        picked = tuple(cards[use] if c.name == base else c for c in army.cards)
        armies.append(dataclasses.replace(army, cards=picked))
    out = M.Entry(tuple(armies), name="対策{:03b}".format(mask))
    if M.validate(out):
        raise RuntimeError("{}: {}".format(out.name, "／".join(M.validate(out))))
    return out


def _job(args):
    rapid, defense, mask, seed, side = args
    if side == 0:
        result = M.play(defense, rapid, dt=0.5, seed=seed)
        wins, diff = result["wins_a"], result["diff"]
        game_diffs = [g["結果"]["diff"] for g in result["games"]]
    else:
        result = M.play(rapid, defense, dt=0.5, seed=seed)
        wins, diff = result["wins_b"], -result["diff"]
        game_diffs = [-g["結果"]["diff"] for g in result["games"]]
    return mask, wins > 1.5, diff, tuple(game_diffs)


def _fire_job(args):
    rapid, defense, seed, side, mult = args
    F.RESTRAINT_NATURAL_MULT = mult
    if side == 0:
        result = M.play_one(defense, rapid, 2, dt=0.5, seed=seed)["結果"]
        rapid_fires, defense_fires, diff = (
            result["fires_b"], result["fires_a"], result["diff"])
    else:
        result = M.play_one(rapid, defense, 2, dt=0.5, seed=seed)["結果"]
        rapid_fires, defense_fires, diff = (
            result["fires_a"], result["fires_b"], -result["diff"])
    rapid_names = {c.name for c in rapid.unit(2).cards if C.cadence(c) == "手数"}
    hand_n = sum(n for name, n in rapid_fires if name in rapid_names)
    return mult, diff, hand_n, sum(n for _name, n in rapid_fires), sum(
        n for _name, n in defense_fires)


def fire_probe(cards, rapid_entries, defense_bases, seeds: Iterable[int], jobs_n):
    idx = C.card_index(cards)
    adopted = F.RESTRAINT_NATURAL_MULT
    mults = (1.0, adopted)
    jobs = []
    for rapid in rapid_entries:
        for base in defense_bases:
            defense = _variant(base, idx, 4)
            for seed in seeds:
                for side in (0, 1):
                    for mult in mults:
                        jobs.append((rapid, defense, seed, side, mult))
    with mp.Pool(jobs_n) as pool:
        rows = pool.map(_fire_job, jobs, chunksize=8)
    out = {}
    for mult in mults:
        rs = [r for r in rows if r[0] == mult]
        out["{:.2f}".format(mult)] = {
            "games": len(rs),
            "defense_win_rate": round(sum(r[1] > 0 for r in rs) / len(rs), 4),
            "mean_diff": round(statistics.mean(r[1] for r in rs), 6),
            "rapid_hand_fires": round(statistics.mean(r[2] for r in rs), 3),
            "rapid_all_fires": round(statistics.mean(r[3] for r in rs), 3),
            "defense_all_fires": round(statistics.mean(r[4] for r in rs), 3),
        }
    F.RESTRAINT_NATURAL_MULT = adopted
    return out


def run(profile, jobs_n):
    cfg = PROFILES[profile]
    cards = C.roster()
    idx = C.card_index(cards)
    rapid_entries = [_rapid_entry(cards, n) for n in range(cfg["members"])]
    defense_bases = [_defense_entry(cards, n) for n in range(cfg["members"])]
    variants = [[_variant(e, idx, mask) for mask in range(8)] for e in defense_bases]
    jobs = []
    for rapid in rapid_entries:
        for group in variants:
            for mask, defense in enumerate(group):
                for seed in cfg["seeds"]:
                    jobs.extend(((rapid, defense, mask, seed, 0),
                                 (rapid, defense, mask, seed, 1)))
    with mp.Pool(jobs_n) as pool:
        rows = pool.map(_job, jobs, chunksize=8)

    by_mask: Dict[str, dict] = {}
    for mask in range(8):
        rs = [r for r in rows if r[0] == mask]
        by_mask[format(mask, "03b")] = {
            "coverage": mask.bit_count(),
            "counters": [SWAPS[i][2] for i in range(3) if mask & (1 << i)],
            "bo3_games": len(rs),
            "defense_bo3_win_rate": round(sum(r[1] for r in rs) / len(rs), 4),
            "mean_match_diff": round(statistics.mean(r[2] for r in rs), 6),
            "single_win_rate_by_reg": [round(
                sum(r[3][reg] > 0 for r in rs) / len(rs), 4) for reg in range(3)],
            "mean_diff_by_reg": [round(
                statistics.mean(r[3][reg] for r in rs), 6) for reg in range(3)],
        }
    by_coverage = {}
    for coverage in range(4):
        rs = [r for r in rows if r[0].bit_count() == coverage]
        by_coverage[str(coverage)] = {
            "subsets": sum(1 for m in range(8) if m.bit_count() == coverage),
            "bo3_games": len(rs),
            "defense_bo3_win_rate": round(sum(r[1] for r in rs) / len(rs), 4),
            "mean_match_diff": round(statistics.mean(r[2] for r in rs), 6),
        }
    return {
        "profile": profile,
        "restraint_natural_mult": F.RESTRAINT_NATURAL_MULT,
        "rapid_split": list(RAPID_SPLIT),
        "rapid_cards": [c.name for c in cards if C.cadence(c) == "手数"],
        "swaps": [{"regulation": M.REGULATIONS[i][0], "base": a,
                   "counter": b, "label": label}
                  for i, (a, b, label) in enumerate(SWAPS)],
        "seeds": list(cfg["seeds"]),
        "sides": ["defense-A", "defense-B"],
        "by_mask": by_mask,
        "by_coverage": by_coverage,
        "restraint_causal": fire_probe(
            cards, rapid_entries, defense_bases, cfg["seeds"], jobs_n),
    }


def markdown(report: Mapping) -> str:
    lines = [
        "# BO3手数密度×対策面数", "",
        "- profile: `{}` / 手数配分 `3/3/2` / 節制の自然増加 `×{:.2f}`".format(
            report["profile"], report["restraint_natural_mult"]),
        "- 同コスト・同兵種・同役割の札と対策札を差し替え、全subsetを左右両側で測定",
        "", "| 対策面数 | subset数 | BO3数 | 守備側BO3勝率 | 平均match diff |",
        "|---:|---:|---:|---:|---:|",
    ]
    for k, v in report["by_coverage"].items():
        lines.append("| {} | {} | {} | {:.1f}% | {:+.4f} |".format(
            k, v["subsets"], v["bo3_games"], 100 * v["defense_bo3_win_rate"],
            v["mean_match_diff"]))
    lines += ["", "## subset別", "",
              "| mask(赤壁官渡汜水関) | 対策 | BO3勝率 | 汜水関 | 官渡 | 赤壁 | 平均diff |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for mask, v in report["by_mask"].items():
        rates = v["single_win_rate_by_reg"]
        lines.append("| {} | {} | {:.1f}% | {:.1f}% | {:.1f}% | {:.1f}% | {:+.4f} |".format(
            mask, "・".join(v["counters"]) or "なし",
            100 * v["defense_bo3_win_rate"], *(100 * x for x in rates),
            v["mean_match_diff"]))
    lines += ["", "※ maskは右から 汜水関=持重 / 官渡=打消し / 赤壁=節制。", "",
              "## 節制の因果（同じ陸抗で効果だけON/OFF）", "",
              "| 自然増加 | 赤壁数 | 守備勝率 | 手数札発動 | 手数側全発動 | 守備側全発動 | 平均diff |",
              "|---:|---:|---:|---:|---:|---:|---:|"]
    for mult, v in report["restraint_causal"].items():
        lines.append("| ×{} | {} | {:.1f}% | {:.2f} | {:.2f} | {:.2f} | {:+.4f} |".format(
            mult, v["games"], 100 * v["defense_win_rate"],
            v["rapid_hand_fires"], v["rapid_all_fires"],
            v["defense_all_fires"], v["mean_diff"]))
    lines.append("")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--profile", choices=PROFILES, default="standard")
    p.add_argument("--jobs", type=int, default=max(mp.cpu_count() - 1, 1))
    p.add_argument("--restraint-mult", type=float, default=F.RESTRAINT_NATURAL_MULT)
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    if not 0.0 < args.restraint_mult <= 1.0:
        p.error("--restraint-mult は 0 より大きく 1 以下")
    F.RESTRAINT_NATURAL_MULT = args.restraint_mult
    report = run(args.profile, args.jobs)
    text = markdown(report)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        args.output.with_suffix(".md").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
