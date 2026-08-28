# -*- coding: utf-8 -*-
"""Shared plumbing for the reproducible balance instruments.

The helpers in this module deliberately keep three things together:

* the exact deck snapshot used by a measurement;
* legality checks using the same rules as registration;
* a manifest describing code, CSVs, constants, seeds, sides, and opponents.

Without those three, two reports with the same label (for example "ladder width")
can measure different populations and cannot safely be compared.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "docs" / "balance" / "fixtures-v1.json"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim import design as D  # noqa: E402
from sim import field as F  # noqa: E402
from sim import match as M  # noqa: E402

FORM_BY_NAME = {
    "鶴翼": F.FORM_WIDE,
    "魚鱗": F.FORM_STANDARD,
    "雁行": F.FORM_DEEP,
}
TYPE_JP = {F.INF: "歩兵", F.CAV: "騎兵", F.ARC: "弓兵"}
ROLE_JP = {
    F.TANK: "耐久",
    F.BAL: "均衡",
    F.DPS: "火力",
    F.BURST: "瞬発",
    F.SUP: "支援",
}


def load_fixtures(path: Path = FIXTURES) -> dict:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema_version") != 1:
        raise ValueError("未知の fixtures schema: {}".format(data.get("schema_version")))
    return data


def roster() -> Tuple[F.Card, ...]:
    """Load cards through the real-game path (traits and generated stats included)."""
    return tuple(M._roster_cards())


def card_index(cards: Sequence[F.Card]) -> Dict[str, F.Card]:
    return {c.name: c for c in cards}


def army_from_spec(spec: Mapping, cards: Mapping[str, F.Card]) -> F.Army:
    try:
        form = FORM_BY_NAME[spec["formation"]]
        picked = tuple(cards[name] for name in spec["cards"])
    except KeyError as exc:
        raise ValueError("fixture が現在の名簿を参照できない: {}".format(exc)) from exc
    army = F.Army(picked, form)
    errs = M.placement_errors(army)
    if errs:
        raise ValueError("{}: {}".format(spec.get("regulation", "?"), "／".join(errs)))
    return army


def entry_from_spec(spec: Mapping, cards: Mapping[str, F.Card]) -> M.Entry:
    entry = M.Entry(tuple(army_from_spec(a, cards) for a in spec["armies"]),
                    name=spec.get("name", ""))
    errs = M.validate(entry)
    if errs:
        raise ValueError("{}: {}".format(spec.get("name", "?"), "／".join(errs)))
    return entry


def named_set(data: Mapping, key: str, cards: Mapping[str, F.Card]) -> Tuple[str, M.Entry]:
    spec = data["sets"][key]
    return spec.get("name", key), entry_from_spec(spec, cards)


def pool_entries(data: Mapping, key: str,
                 cards: Mapping[str, F.Card]) -> List[Tuple[str, M.Entry]]:
    return [(spec["name"], entry_from_spec(spec, cards))
            for spec in data["pools"][key]["entries"]]


def all_fixture_entries(data: Mapping, cards: Mapping[str, F.Card]
                        ) -> Iterator[Tuple[str, str, M.Entry]]:
    for key, spec in data["sets"].items():
        yield "set:" + key, spec.get("name", key), entry_from_spec(spec, cards)
    for pool, p in data["pools"].items():
        for spec in p["entries"]:
            yield "pool:" + pool, spec["name"], entry_from_spec(spec, cards)


def cadence(card: F.Card) -> str:
    """Return the explicit gauge tier used by the generated card."""
    return D.GAUGE_TIER_NAME.get(card.gauge_cost, "個別({:g})".format(card.gauge_cost))


def cards_of(entry: M.Entry) -> List[F.Card]:
    return [card for army in entry.units for card in army.cards]


def gini(values: Sequence[float]) -> float:
    xs = sorted(float(x) for x in values)
    if not xs or sum(xs) == 0:
        return 0.0
    n, total = len(xs), sum(xs)
    return sum((2 * i - n - 1) * x for i, x in enumerate(xs, 1)) / (n * total)


def effective_count(values: Sequence[float]) -> float:
    total = float(sum(values))
    if total <= 0:
        return 0.0
    probs = [v / total for v in values if v > 0]
    return 1.0 / sum(p * p for p in probs)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state() -> dict:
    def run(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        commit = run("rev-parse", "HEAD")
        branch = run("branch", "--show-current") or "(detached)"
        dirty = bool(run("status", "--porcelain"))
    except (OSError, subprocess.CalledProcessError):
        commit, branch, dirty = "unknown", "unknown", True
    return {"commit": commit, "branch": branch, "dirty": dirty}


def balance_constants() -> dict:
    """Critical knobs whose value changes the meaning of a result."""
    return {
        "GAUGE_PER_SEC": F.GAUGE_PER_SEC,
        "GAUGE_PER_DEAL": F.GAUGE_PER_DEAL,
        "GAUGE_PER_TAKE": F.GAUGE_PER_TAKE,
        "GAUGE_ON_ROUT": F.GAUGE_ON_ROUT,
        "TRAMPLE": F.TRAMPLE,
        "TRAMPLE_TIER_COST": F.TRAMPLE_TIER_COST,
        "SURPLUS_PER_COST": M.SURPLUS_PER_COST,
        "SURPLUS_CAP": M.SURPLUS_CAP,
        "FORM_DEPTH_2": F.FORM_DEPTH[2],
        "FORM_PAIR": {"{}>{}".format(a, b): v for (a, b), v in F.FORM_PAIR.items()},
    }


def manifest(command: str, profile: str, protocol: Mapping,
             fixture_data: Mapping, dt: float = 0.5) -> dict:
    csvs = {}
    for name in ("generals.csv", "skills.csv", "traits.csv"):
        path = ROOT / "sim" / "data" / name
        csvs[name] = sha256(path)
    return {
        "created_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "command": command,
        "profile": profile,
        "git": git_state(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "dt": dt,
        "csv_sha256": csvs,
        "fixture_file": str(FIXTURES.relative_to(ROOT)),
        "fixture_source": fixture_data.get("source", {}),
        "constants": balance_constants(),
        "protocol": dict(protocol),
    }


def write_json(path: Path, data: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def counter_dict(items: Iterable, key) -> dict:
    return dict(sorted(Counter(key(x) for x in items).items(), key=lambda kv: str(kv[0])))
