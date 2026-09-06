# -*- coding: utf-8 -*-
"""BO3 の計器の共通部（§7.175・`bo3_protocol: production_v1`）。

**本番の `sim.match.play()` をそのまま呼ぶ。** 計器の側で3つの単戦を束ねて
BO3 を判定しない。本番の次の処理をそのまま使う:

  * 戦場ごとの種の導出（match_seed × 3 + 戦場番号）
  * コスト余りによる初期ゲージの補正（`with_surplus`。元の軍は書き換えない）
  * 各戦の勝敗・引き分け（`score`: 1／0.5／0）
  * シリーズの勝者（返ってくる `winner`）

勝者は `winner` から集計する。`diff > 0` や3戦の残存率差の合計から勝者を
決め直さない。単戦専用の計器は `M.play_one()` のままでよいが、その結果を
本番 BO3 の勝率として表示しない。

左右: 各マッチシード S で **候補 対 相手（S）** と **相手 対 候補（S）** の
両方を打ち、結果は必ず候補側の視点へ戻す。勝・敗・分は別に数え、「勝率」は
勝÷全シリーズ。引き分けを半勝にした指標は `point_rate`（得点率）と呼ぶ。

比較（`compare`）は、相手とその配置・マッチシード・左右・dt・宝物の条件が
同じ2つの計測だけを受け付け、BO3 と戦場ごとの勝・敗・分、戦場ごとの平均
残存率差、シリーズの勝敗が入れ替わった件数（前は敗北→後は勝利、その逆）を出す。
"""
from __future__ import annotations

import datetime as _dt
import multiprocessing as mp
import statistics
import subprocess
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

from . import field as F
from . import match as M

PROTOCOL = "production_v1"
DT = 0.5
SIDES = ("A", "B")
REG_LABELS = tuple(label for label, _cap in M.REGULATIONS)


# ----------------------------------------------------------------------------
# 1シリーズ
# ----------------------------------------------------------------------------

def game_outcome(score: float) -> str:
    """本番の各戦の判定（score 1／0.5／0）を 勝・敗・分 の印にする。"""
    if score > 0.5:
        return "W"
    if score < 0.5:
        return "L"
    return "D"


def play_series(cand: M.Entry, foe: M.Entry, seed: int, side: str,
                dt: float = DT) -> dict:
    """本番の BO3 を1シリーズ打ち、**候補側の視点**へ戻して返す。

    side "A" なら 候補 対 相手、"B" なら 相手 対 候補 を同じ match_seed で打つ。
    戦場ごとの種は本番が match_seed×3+戦場番号 で導く（ここでは触らない）。
    """
    if side == "A":
        r = M.play(cand, foe, dt=dt, seed=seed)
        outcome = {"A": "W", "B": "L"}.get(r["winner"], "D")
        sign = 1.0
    elif side == "B":
        r = M.play(foe, cand, dt=dt, seed=seed)
        outcome = {"B": "W", "A": "L"}.get(r["winner"], "D")
        sign = -1.0
    else:
        raise ValueError("side は A か B: {!r}".format(side))
    games = []
    for g in r["games"]:
        res = g["結果"]
        score = res["score"] if side == "A" else 1.0 - res["score"]
        ra, rb = (res["ra"], res["rb"]) if side == "A" else (res["rb"], res["ra"])
        games.append({
            "regulation": g["規定"],
            "outcome": game_outcome(score),
            "diff": sign * res["diff"],            # 盤面の差（勝敗の量・押し込み込み）
            "remain_diff": ra - rb,                # 残存率の差（候補 − 相手）
            "remain": ra, "foe_remain": rb,
            "t": res["t"], "reason": res["reason"],
        })
    return {"seed": seed, "side": side, "outcome": outcome,
            "match_diff": sign * r["diff"], "games": games}


def _series_job(args):
    key, cand, foe, seed, side, dt = args
    return key, play_series(cand, foe, seed, side, dt)


def run_series(jobs: Sequence[tuple], jobs_n: int = 1, chunksize: int = 4) -> list:
    """(key, 候補, 相手, seed, side, dt) の列を打って (key, シリーズ) の列を返す。"""
    if jobs_n <= 1 or len(jobs) <= 1:
        return [_series_job(j) for j in jobs]
    with mp.Pool(jobs_n) as pool:
        return pool.map(_series_job, jobs, chunksize=chunksize)


# ----------------------------------------------------------------------------
# 条件と登録の記述
# ----------------------------------------------------------------------------

def check_entries(*named: Tuple[str, M.Entry]) -> None:
    """本番の検証（人物重複・配置・コスト上限）を通す。不備があれば例外。"""
    bad = []
    for name, entry in named:
        errs = M.validate(entry)
        if errs:
            bad.append("{}: {}".format(name, "／".join(errs)))
    if bad:
        raise ValueError("登録が本番の検証を通らない: " + " | ".join(bad))


def _form_name(form: F.Formation) -> str:
    return F.FORM_NAME.get(form.n_front, "前衛{}".format(form.n_front))


def treasures_of(card: F.Card) -> List[str]:
    keys = []
    for raw in (card.trait, getattr(card, "hidden_trait", "")):
        for k in F.trait_keys(raw or ""):
            if k.startswith("t_") and k not in keys:
                keys.append(k)
    return keys


def entry_spec(entry: M.Entry) -> dict:
    """登録の記述（陣形・配置・札・宝物）。報告に残す。"""
    armies = []
    for army, (label, cap) in zip(entry.units, M.REGULATIONS):
        armies.append({
            "regulation": label, "cap": cap, "formation": _form_name(army.form),
            "cost": round(army.total_cost(), 4),
            "surplus_ratio": round(M.surplus_ratio(army, cap), 4),
            "cards": [c.name for c in army.cards],
            "treasures": {c.name: treasures_of(c) for c in army.cards if treasures_of(c)},
        })
    return {"name": entry.name, "armies": armies}


def _commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, check=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                               text=True, check=True, timeout=10).stdout.strip()
        return out + ("+dirty" if dirty else "")
    except Exception:            # noqa: BLE001 - 記録の欠けは計測を止めない
        return "?"


def conditions(seeds: Iterable[int], sides: Sequence[str] = SIDES, dt: float = DT,
               treasures: str = "none", extra: Mapping | None = None) -> dict:
    """計測条件。比較はこれが一致する2つの間でだけ行う。"""
    cond = {
        "bo3_protocol": PROTOCOL,
        "engine": "sim.match.play",
        "dt": dt,
        "seeds": list(seeds),
        "sides": list(sides),
        "treasures": treasures,        # "none"＝宝物なし。装備込みなら装備の記述
        "commit": _commit(),
        "created_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    if extra:
        cond.update(extra)
    return cond


# ----------------------------------------------------------------------------
# 集計
# ----------------------------------------------------------------------------

def _rate(n: int, total: int) -> float:
    return round(n / total, 4) if total else 0.0


def _tally(outcomes: Sequence[str]) -> dict:
    w = sum(o == "W" for o in outcomes)
    l_ = sum(o == "L" for o in outcomes)
    d = sum(o == "D" for o in outcomes)
    n = len(outcomes)
    return {"wins": w, "losses": l_, "draws": d, "games": n,
            "win_rate": _rate(w, n), "point_rate": _rate(w + 0.5 * d, n) if n else 0.0}


def aggregate(records: Sequence[dict]) -> dict:
    """シリーズの記録（opponent を添えた play_series の戻り）を集計する。"""
    series = _tally([r["outcome"] for r in records])
    series = {"series": series.pop("games"), **series}
    series["mean_match_diff"] = round(statistics.mean(r["match_diff"] for r in records), 6) if records else 0.0
    by_reg = []
    for i, label in enumerate(REG_LABELS):
        gs = [r["games"][i] for r in records if len(r["games"]) > i]
        t = _tally([g["outcome"] for g in gs])
        diffs = [g["diff"] for g in gs]
        rem = [g["remain_diff"] for g in gs]
        by_reg.append({
            "regulation": label, **t,
            "mean_diff": round(statistics.mean(diffs), 6) if diffs else 0.0,
            "min_diff": round(min(diffs), 6) if diffs else 0.0,
            "max_diff": round(max(diffs), 6) if diffs else 0.0,
            "mean_remain_diff": round(statistics.mean(rem), 6) if rem else 0.0,
        })
    singles = _tally([g["outcome"] for r in records for g in r["games"]])
    per_opp: Dict[str, list] = {}
    for r in records:
        per_opp.setdefault(r["opponent"], []).append(r)
    opponents = []
    for name, rs in per_opp.items():
        t = _tally([r["outcome"] for r in rs])
        t = {"series": t.pop("games"), **t}
        opponents.append({
            "name": name, **t,
            "mean_match_diff": round(statistics.mean(r["match_diff"] for r in rs), 6),
            "single": _tally([g["outcome"] for r in rs for g in r["games"]]),
        })
    opponents.sort(key=lambda x: (x["win_rate"], x["point_rate"], x["mean_match_diff"]))
    return {"bo3": series, "single": singles, "by_regulation": by_reg,
            "per_opponent": opponents}


def measure(cand: M.Entry, opponents: Sequence[Tuple[str, M.Entry]],
            seeds: Iterable[int], sides: Sequence[str] = SIDES, dt: float = DT,
            jobs_n: int = 1, name: str = "", treasures: str = "none",
            validate: bool = True, keep_records: bool = True) -> dict:
    """候補1つを相手の集合へ当てる（本番 BO3・両側・候補視点）。"""
    seeds = list(seeds)
    if validate:
        check_entries((name or cand.name or "candidate", cand), *opponents)
    jobs = [((oname, seed, side), cand, foe, seed, side, dt)
            for oname, foe in opponents for seed in seeds for side in sides]
    rows = run_series(jobs, jobs_n)
    records = []
    for (oname, seed, side), s in rows:
        s["opponent"] = oname
        records.append(s)
    out = {
        "candidate": name or cand.name or "",
        "spec": entry_spec(cand),
        "opponents": [{"name": n, "spec": entry_spec(e)} for n, e in opponents],
        "conditions": conditions(seeds, sides, dt, treasures),
        **aggregate(records),
    }
    if keep_records:
        out["records"] = [{"opponent": r["opponent"], "seed": r["seed"], "side": r["side"],
                           "outcome": r["outcome"], "match_diff": round(r["match_diff"], 6),
                           "games": [g["outcome"] for g in r["games"]],
                           "remain_diffs": [round(g["remain_diff"], 6) for g in r["games"]]}
                          for r in records]
    return out


# ----------------------------------------------------------------------------
# 前後の比較
# ----------------------------------------------------------------------------

_COND_KEYS = ("bo3_protocol", "dt", "seeds", "sides", "treasures")


def compare(before: Mapping, after: Mapping) -> dict:
    """同じ条件で測った2つの計測を比べる。条件が違えば例外。"""
    cb, ca = before["conditions"], after["conditions"]
    for k in _COND_KEYS:
        if cb.get(k) != ca.get(k):
            raise ValueError("計測条件が違う（{}: {} / {}）ので比較しない".format(k, cb.get(k), ca.get(k)))
    ob = [(o["name"], o["spec"]["armies"]) for o in before["opponents"]]
    oa = [(o["name"], o["spec"]["armies"]) for o in after["opponents"]]
    if ob != oa:
        raise ValueError("相手（名前・配置）が違うので比較しない")
    if "records" not in before or "records" not in after:
        raise ValueError("シリーズごとの記録が無い（keep_records=True で測る）")
    kb = {(r["opponent"], r["seed"], r["side"]): r for r in before["records"]}
    ka = {(r["opponent"], r["seed"], r["side"]): r for r in after["records"]}
    if set(kb) != set(ka):
        raise ValueError("シリーズの組（相手・seed・左右）が一致しない")
    flips = {"L->W": 0, "W->L": 0, "D->W": 0, "W->D": 0, "L->D": 0, "D->L": 0, "same": 0}
    reg_flips = [{"regulation": lab, "L->W": 0, "W->L": 0, "D->W": 0, "W->D": 0,
                  "L->D": 0, "D->L": 0, "same": 0} for lab in REG_LABELS]
    for key in sorted(kb, key=lambda k: (k[0], k[1], k[2])):
        b, a = kb[key], ka[key]
        tag = "same" if b["outcome"] == a["outcome"] else "{}->{}".format(b["outcome"], a["outcome"])
        flips[tag] = flips.get(tag, 0) + 1
        for i, (gb, ga) in enumerate(zip(b["games"], a["games"])):
            tag = "same" if gb == ga else "{}->{}".format(gb, ga)
            reg_flips[i][tag] = reg_flips[i].get(tag, 0) + 1
    bb, ab = before["bo3"], after["bo3"]
    out = {
        "conditions": {k: ca.get(k) for k in _COND_KEYS},
        "commits": {"before": cb.get("commit"), "after": ca.get("commit")},
        "bo3": {"before": bb, "after": ab,
                "win_rate_delta_points": round(100 * (ab["win_rate"] - bb["win_rate"]), 2),
                "point_rate_delta_points": round(100 * (ab["point_rate"] - bb["point_rate"]), 2)},
        "series_flips": flips,
        "by_regulation": [],
        "notes": [],
    }
    for i, lab in enumerate(REG_LABELS):
        rb, ra = before["by_regulation"][i], after["by_regulation"][i]
        out["by_regulation"].append({
            "regulation": lab, "before": rb, "after": ra,
            "win_rate_delta_points": round(100 * (ra["win_rate"] - rb["win_rate"]), 2),
            "mean_remain_diff_delta": round(ra["mean_remain_diff"] - rb["mean_remain_diff"], 6),
            "flips": reg_flips[i],
        })
    rem_up = sum(x["mean_remain_diff_delta"] for x in out["by_regulation"])
    if rem_up > 0 and out["bo3"]["win_rate_delta_points"] < 0:
        out["notes"].append("平均残存率差は改善したが BO3 勝率は下がった（両方をそのまま示す）")
    if rem_up < 0 and out["bo3"]["win_rate_delta_points"] > 0:
        out["notes"].append("平均残存率差は悪化したが BO3 勝率は上がった（両方をそのまま示す）")
    return out


# ----------------------------------------------------------------------------
# 報告（Markdown）
# ----------------------------------------------------------------------------

def _pct(x: float) -> str:
    return "{:.1f}%".format(100 * x)


def conditions_lines(cond: Mapping) -> List[str]:
    seeds = cond.get("seeds") or []
    rng = ("{}〜{}（{}個）".format(min(seeds), max(seeds), len(seeds)) if seeds else "なし")
    return [
        "- 計測方式: `{}`（本番 `sim.match.play` をそのまま使用・戦場ごとの種は match_seed×3+戦場番号）".format(cond.get("bo3_protocol")),
        "- commit: `{}`".format(cond.get("commit", "?")),
        "- マッチシード: {}／左右: {}／dt: {}".format(rng, "・".join(cond.get("sides") or []), cond.get("dt")),
        "- 宝物: **{}**".format("なし" if cond.get("treasures") == "none" else cond.get("treasures")),
        "- 勝率＝勝÷全シリーズ（引き分けは別に数える）。得点率＝(勝＋0.5×分)÷全",
    ]


def measure_markdown(rep: Mapping, title: str = "BO3 計測") -> str:
    b = rep["bo3"]
    L = ["# {}".format(title), "", "候補: **{}**".format(rep.get("candidate") or "?"), ""]
    L += conditions_lines(rep["conditions"])
    L += ["", "## BO3", "", "| 勝 | 敗 | 分 | 勝率 | 得点率 | 平均 match diff |", "|---:|---:|---:|---:|---:|---:|",
          "| {} | {} | {} | {} | {} | {:+.4f} |".format(b["wins"], b["losses"], b["draws"], _pct(b["win_rate"]),
                                                      _pct(b["point_rate"]), b["mean_match_diff"]),
          "", "## 戦場別", "", "| 戦場 | 勝 | 敗 | 分 | 勝率 | 平均残存率差 | 平均 diff |", "|---|---:|---:|---:|---:|---:|---:|"]
    for r in rep["by_regulation"]:
        L.append("| {} | {} | {} | {} | {} | {:+.4f} | {:+.4f} |".format(
            r["regulation"], r["wins"], r["losses"], r["draws"], _pct(r["win_rate"]),
            r["mean_remain_diff"], r["mean_diff"]))
    L += ["", "## 相手別（弱い順）", "", "| 相手 | 勝 | 敗 | 分 | 勝率 | 平均 match diff |", "|---|---:|---:|---:|---:|---:|"]
    for o in rep["per_opponent"]:
        L.append("| {} | {} | {} | {} | {} | {:+.4f} |".format(
            o["name"], o["wins"], o["losses"], o["draws"], _pct(o["win_rate"]), o["mean_match_diff"]))
    L += ["", "## 候補の編成", ""]
    for a in rep["spec"]["armies"]:
        tz = "／".join("{}:{}".format(k, "・".join(v)) for k, v in a["treasures"].items()) or "なし"
        L.append("- {} {} {:g}点（余り {:.0%}）: {}　宝物: {}".format(
            a["regulation"], a["formation"], a["cost"], a["surplus_ratio"], " / ".join(a["cards"]), tz))
    L.append("")
    return "\n".join(L)


def compare_markdown(cmp: Mapping, before_name: str = "前", after_name: str = "後") -> str:
    b, a = cmp["bo3"]["before"], cmp["bo3"]["after"]
    L = ["# BO3 前後比較", ""]
    L += conditions_lines({**cmp["conditions"], "commit": "{} → {}".format(cmp["commits"]["before"], cmp["commits"]["after"])})
    L += ["", "## BO3", "", "| | 勝 | 敗 | 分 | 勝率 | 得点率 |", "|---|---:|---:|---:|---:|---:|",
          "| {} | {} | {} | {} | {} | {} |".format(before_name, b["wins"], b["losses"], b["draws"], _pct(b["win_rate"]), _pct(b["point_rate"])),
          "| {} | {} | {} | {} | {} | {} |".format(after_name, a["wins"], a["losses"], a["draws"], _pct(a["win_rate"]), _pct(a["point_rate"])),
          "| 差 | | | | {:+.1f}pt | {:+.1f}pt |".format(cmp["bo3"]["win_rate_delta_points"], cmp["bo3"]["point_rate_delta_points"]),
          "", "シリーズの入れ替わり: 前は敗北→後は勝利 **{}**、前は勝利→後は敗北 **{}**、敗→分 {}、分→勝 {}、勝→分 {}、分→敗 {}、同じ {}".format(
              cmp["series_flips"]["L->W"], cmp["series_flips"]["W->L"], cmp["series_flips"]["L->D"],
              cmp["series_flips"]["D->W"], cmp["series_flips"]["W->D"], cmp["series_flips"]["D->L"], cmp["series_flips"]["same"]),
          "", "## 戦場別", "", "| 戦場 | 前 勝/敗/分 | 後 勝/敗/分 | 勝率の差 | 平均残存率差 前 | 後 | 差 | 敗→勝 | 勝→敗 |",
          "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for r in cmp["by_regulation"]:
        rb, ra, fl = r["before"], r["after"], r["flips"]
        L.append("| {} | {}/{}/{} | {}/{}/{} | {:+.1f}pt | {:+.4f} | {:+.4f} | {:+.4f} | {} | {} |".format(
            r["regulation"], rb["wins"], rb["losses"], rb["draws"], ra["wins"], ra["losses"], ra["draws"],
            r["win_rate_delta_points"], rb["mean_remain_diff"], ra["mean_remain_diff"], r["mean_remain_diff_delta"],
            fl["L->W"], fl["W->L"]))
    for n in cmp["notes"]:
        L.append("- ⚠️ " + n)
    L.append("")
    return "\n".join(L)
