# -*- coding: utf-8 -*-
"""登録の**前後比較**を本番の BO3 と同じ条件で測る（§7.175・`bo3_protocol: production_v1`）。

    python3 tools/bo3_compare.py --before set:chappy --after spec.json \\
        --opponents set:counter,set:chappy_prev_20260905,pool:official24 \\
        --seeds 0-9 [--dt 0.5] [--jobs 8] [--output out.json]

登録の指定:
  set:<名前>            docs/balance/fixtures-v1.json の名前つき集合
  pool:<名前>           同じ fixtures のプール（相手にだけ使える。全登録）
  <path>.json           {"name":..., "armies":[{"regulation","formation","cards"}×3]}
  player:<id>[@<db>]    本番の登録（sim.play.entry_of＝宝物の装備反映・検証まで本番経路）

前後で共通にするもの: 相手とその配置・マッチシード・左右（各シードで両方）・dt・宝物の条件。
登録は本番の検証（人物重複・配置・コスト上限）を通す。宝物は指定の登録に付いているもの
（player: 由来）をそのまま固定し、比較中に装備を変えない。宝物の無い登録どうしなら
「宝物なし」と明記する。

出力: BO3 の勝・敗・分・勝率、戦場ごとの勝・敗・分・勝率、戦場ごとの平均残存率差、
シリーズの勝敗が入れ替わった件数（前は敗北→後は勝利、その逆）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sim import bo3meter as B          # noqa: E402
from sim import match as M             # noqa: E402

try:
    from . import balance_common as C
except ImportError:                    # pragma: no cover - python tools/...
    import balance_common as C


def parse_seeds(text: str):
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def load_entry(token: str, data, idx, cards):
    """登録1つ。(名前, Entry, 宝物の記述)。"""
    if token.startswith("set:"):
        name, entry = C.named_set(data, token[4:], idx)
        return name, entry, "none"
    if token.startswith("player:"):
        import sim.play as PL
        import sim.players as P
        rest = token[7:]
        pid, db = (rest.split("@", 1) + [None])[:2] if "@" in rest else (rest, None)
        cx = P.connect(db) if db else P.connect()
        pl = P.get(cx, int(pid))
        if pl is None:
            raise SystemExit("登録者 id={} が居ない".format(pid))
        board, ok, errs = PL.entry_of(cx, cards, pl.id, pl.display_name)
        # 本番は盤面ごとに可否を持つ（BO1 は1デッキでも出られる）。BO3 の比較は3つ揃って
        # 全部合法なときだけ。宝物の装備反映（trait/hidden_trait・札モッド）も entry_of が済ませる
        if not all(ok.get(k) for k in ok) or errs or len(board.units_map) != len(M.REGULATIONS):
            raise SystemExit("{} の登録が本番の検証を通らない（3盤面とも合法が必要）: {}".format(
                pl.display_name, "／".join(errs) or str(ok)))
        entry = M.Entry(tuple(board.units_map[i] for i in range(len(M.REGULATIONS))),
                        name=pl.display_name)
        spec = B.entry_spec(entry)
        worn = {k: v for a in spec["armies"] for k, v in a["treasures"].items()}
        return pl.display_name, entry, ("none" if not worn else json.dumps(worn, ensure_ascii=False))
    path = Path(token)
    with path.open(encoding="utf-8") as fh:
        spec = json.load(fh)
    entry = C.entry_from_spec(spec, idx)
    return spec.get("name", path.stem), entry, "none"


def load_opponents(tokens, data, idx, cards):
    out = []
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("pool:"):
            out.extend(C.pool_entries(data, tok[5:], idx))
        else:
            name, entry, _t = load_entry(tok, data, idx, cards)
            out.append((name, entry))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="登録の前後比較（本番 BO3・条件固定）")
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--opponents", default="set:counter,set:chappy_prev_20260905,set:red2_20260906,set:cav_20260905")
    ap.add_argument("--seeds", default="0-9")
    ap.add_argument("--dt", type=float, default=B.DT)
    ap.add_argument("--jobs", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--output", type=Path, default=None, help="JSON の保存先（隣に同名の .md も書く）")
    args = ap.parse_args(argv)

    data = C.load_fixtures()
    cards = C.roster()
    idx = C.card_index(cards)
    b_name, before, b_tz = load_entry(args.before, data, idx, cards)
    a_name, after, a_tz = load_entry(args.after, data, idx, cards)
    opponents = load_opponents(args.opponents.split(","), data, idx, cards)
    seeds = parse_seeds(args.seeds)
    if b_tz != a_tz:
        print("注意: 前後で宝物の条件が違う（前 {} / 後 {}）。比較は拒む".format(b_tz, a_tz))
        return 2
    tz = b_tz
    print("前: {}／後: {}／相手 {} 登録／seed {} 個×左右2／dt {}／宝物 {}".format(
        b_name, a_name, len(opponents), len(seeds), args.dt, "なし" if tz == "none" else tz))
    rb = B.measure(before, opponents, seeds, dt=args.dt, jobs_n=args.jobs, name=b_name, treasures=tz)
    ra = B.measure(after, opponents, seeds, dt=args.dt, jobs_n=args.jobs, name=a_name, treasures=tz)
    cmp = B.compare(rb, ra)
    md = B.compare_markdown(cmp, b_name, a_name)
    print(md)
    if args.output:
        C.write_json(args.output, {"before": rb, "after": ra, "comparison": cmp})
        args.output.with_suffix(".md").write_text(md, encoding="utf-8")
        print("保存: {} / {}".format(args.output, args.output.with_suffix(".md")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
