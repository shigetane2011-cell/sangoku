# -*- coding: utf-8 -*-
"""床の保証（§7.68）: 総赤字が -0.5 点を下回る札に床調整を書く。

    python3 tools/floor_patch.py          # 測る→書く→同期→もう一周（計2周）
    python3 tools/floor_patch.py --check  # 測って一覧だけ（書かない）

方針（テストプレイの決定・2026-08-22）: 必殺技・固有特性・素の能力の
**総合**が、コストに対して -0.5 点より弱い札を作らない。単価の再較正
（task #20）が完了するまでの繋ぎで、補正は「床調整」列（兵力の上乗せ・
0以上・上限10%）としてシートに見える形で置く。手で書かない — この道具が
測って書き、余りが出れば剥がす。

計器の癖で測れない札は触らない:
- 本陣（command）: 総崩れの罰だけが素で出る。値段は勝率通貨で較正済み（§7.52）。
- 対勢力（vs_魏/蜀/呉）: 相手が合成カードだと空撃ちで安く見える。
- 必殺技打消し（田豊）: 相手が合成カードだと打ち消す価値のある大技が
  飛んでこず、式の19%しか実測に出ない（実デッキでこそ光る）。
"""
import sys, os, csv, io, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sim import field as F
from sim import match as M
from multiprocessing import Pool

F.TRAITS_ON = True          # 実ゲームと同じ条件で測る（§7.67）
FLOOR = -0.5
CAP = 0.10                  # 床調整の上限（兵力+10%）
SKIP_TRAITS = {"command", "vs_wei", "vs_shu", "vs_go"}
CSV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "sim", "data", "generals.csv")
FILL = tuple(F._synth(4.0, F.CAV, F.BAL) for _ in range(5))


def measure(name):
    cards = {c.name: c for c in M._roster_cards()}
    c = cards[name]
    base = F._synth(c.cost, c.typ, c.role)
    a = F.Army((c,) + FILL, F.FORM_STANDARD)
    b = F.Army((base,) + FILL, F.FORM_STANDARD)
    return name, F.matchup_cost(a, b, F.cost_yardstick(0.5), 0.5, 6)


def audit(names):
    with Pool(4) as pool:
        return dict(pool.map(measure, names, chunksize=2))


def load_rows():
    return list(csv.DictReader(open(CSV, encoding='utf-8-sig')))


def write_adj(adj):
    raw = open(CSV, encoding='utf-8-sig').read().splitlines()
    hdr = raw[0].lstrip("﻿").split(',')
    if "床調整" not in hdr:
        hdr.append("床調整")           # 初回は列ごと足す
    i = hdr.index("床調整")
    rd = csv.reader(io.StringIO("\n".join(raw[1:])))
    buf = io.StringIO(); wr = csv.writer(buf, lineterminator="\n")
    for row in rd:
        while row and len(row) < len(hdr):
            row.append("")
        if row and row[0] in adj:
            row[i] = "%.3f" % adj[row[0]] if adj[row[0]] > 1e-9 else ""
        wr.writerow(row)
    open(CSV, 'w', encoding='utf-8-sig').write(",".join(hdr) + "\n" + buf.getvalue())


def sync():
    from sim import rosterdata as R
    R.sync()
    M._roster_cards.cache_clear() if hasattr(M._roster_cards, "cache_clear") else None


def main():
    check_only = "--check" in sys.argv
    rows = {g["名前"]: g for g in load_rows()}
    targets = [n for n, g in rows.items()
               if not (set(g["固有特性"].split("、")) & SKIP_TRAITS
                       if g["固有特性"] else False)
               and "打消し" not in (g.get("必殺技") or "")]
    # 技名でなく効果で見る（打消しは技名に出ないことがある）
    import io as _io
    sk = {r["技名"]: r["効果"] for r in csv.DictReader(
        open(CSV.replace("generals", "skills"), encoding='utf-8-sig'))}
    targets = [n for n in targets if "打消し" not in sk.get(rows[n]["必殺技"], "")]
    print("測る: %d 枚（計器の癖で除外 %d 枚）" % (len(targets), len(rows) - len(targets)),
          flush=True)
    res = audit(targets)
    low = {n: v for n, v in res.items() if v < FLOOR}
    print("床割れ %d 枚:" % len(low), flush=True)
    for n, v in sorted(low.items(), key=lambda kv: kv[1]):
        print("  %+6.2f  %s" % (v, n), flush=True)
    if check_only:
        return
    for it in range(3):
        if not low:
            break
        rows = {g["名前"]: g for g in load_rows()}   # 前の周の床調整を読み直す
        adj = {}
        for n, v in low.items():
            g = rows[n]
            cost = float(g["コスト"])
            cur = float(g.get("床調整") or 0.0)
            # 兵力1%の値打ち ≒ 0.097 × コスト/5 点（実測の傾きをコストで伸ばす）
            slope = 0.097 * cost / 5.0
            need = (FLOOR - v) / slope / 100.0
            adj[n] = min(cur + need, CAP)
        write_adj(adj)
        sync()
        print("床調整を書いた（%d 枚）→ 測り直し" % len(adj), flush=True)
        res2 = audit(list(low))
        for n, v in sorted(res2.items(), key=lambda kv: kv[1]):
            mark = "" if v >= FLOOR else ("  ★上限でも届かない" if adj[n] >= CAP - 1e-9
                                          else "  ↻続投")
            print("  %+6.2f  %-16s 床調整 %.1f%%%s" % (v, n, adj[n] * 100, mark),
                  flush=True)
        low = {n: v for n, v in res2.items()
               if v < FLOOR and adj[n] < CAP - 1e-9}
    print("done", flush=True)


if __name__ == "__main__":
    main()
