# -*- coding: utf-8 -*-
"""効果の向き先の監査（§7.93）: 技が**誰に当たっているか**を実測する。

    python3 tools/target_audit.py          # 技と固有特性を全部見る
    python3 tools/target_audit.py --all    # 問題の無いものも一行ずつ出す

きっかけは実況の陣営札の取り違え（§7.92）だった。あれは画面が文章を読んで
当てていただけの表示の話で盤面は無事だったが、**「盤面のほうも向き先を
間違えていないか」は、無事だと言うだけでは足りない**。読んで確かめるのでは
なく、1発ずつ撃って**実際に誰の値が動いたか**を数える。

やり方: 同じ合成カード6枚ずつの2軍を組み、A軍の1枚にその技だけを持たせて
`_apply_skill` を1回通す。戦闘は回さない（殴り合いが混じると、味方への
バフが敵の被害を動かして向き先が読めなくなる）。前後で12隊ぶんの値を
突き合わせ、**動いた隊がどちらの軍か**を見る。

盤面の規約（field.py の注記）はこうなっている:
  - 対象文字列が「味方／自分」なら対象は自軍、そうでなければ敵軍。
  - 補正は**符号が向き先を決める**。プラスは自分（味方対象なら味方）へ、
    マイナスは対象へ。したがって
      * 敵対象＋プラス補正 → **撃ち手に乗る**（自己バフ。規約どおり）
      * 味方対象＋マイナス補正 → **どこにも入らない**（書き損じ）
  - ダメージ・継続ダメージ・混乱・行動阻害は対象の側へ。
  - 回復は対象の側へ。**つまり敵対象の技に回復を書くと敵が回復する。**

この道具が探すのは、その規約と CSV の書き方が食い違っている札である。
"""
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F        # noqa: E402
from sim import rosterdata as R   # noqa: E402


def _fingerprint(u):
    """1隊ぶんの「状態の写し」。ここが動けば何かが入ったということ。"""
    return (round(u.men, 6),
            tuple(sorted((k, round(a, 6)) for _, k, a, _ in u.effects)),
            round(u.chaos, 6), round(u.gauge, 6),
            tuple(sorted((k, round(a, 6)) for _, k, a, _s in u.overtime)))


def _changes(before, after, units, tag):
    """動いた隊を (軍, 位置, 何が, 新しく載った補正) の形で返す。"""
    out = []
    for i, u in enumerate(units):
        b, a = before[i], after[i]
        if b == a:
            continue
        what, mods = [], []
        if abs(a[0] - b[0]) > 1e-6:
            what.append("兵力" + ("減" if a[0] < b[0] else "増"))
        for k, amt in a[1]:
            if (k, amt) not in b[1]:
                mods.append((k, amt))
                what.append("阻害" if k == "stun"
                            else "{}{:+.0%}".format(k, amt))
        if a[2] > b[2]:
            what.append("混乱")
        if a[3] != b[3]:
            what.append("ゲージ")
        for k, amt in a[4]:
            if (k, amt) not in b[4]:
                what.append("延焼" if k == "dot" else "持続回復")
        out.append((tag, i, "・".join(what) or "変化", mods))
    return out


def probe(skill, target, caster_index=0):
    """技を1発だけ通して、動いた隊を集める。戦闘は回さない。"""
    army = F.flat_army()
    ua, ub = F.build(army, 1), F.build(army, -1)
    for u in ua + ub:
        u.gauge = 0.0
        # **半分まで削っておく。** 満タンだと回復が「上限で切られて0」になり、
        # 「効果文に中身があるのに何も動かない」と誤診する（実際に4件そう出た）。
        # 計器のほうの穴であって、札の穴ではない。
        u.men = u.men0 * 0.5
    caster = ua[caster_index]
    before_a = [_fingerprint(u) for u in ua]
    before_b = [_fingerprint(u) for u in ub]
    F._apply_skill(caster, skill, target, ua, ub, 0.0, src="監査")
    F._expire(ua + ub, 0.0)
    after_a = [_fingerprint(u) for u in ua]
    after_b = [_fingerprint(u) for u in ub]
    moved = (_changes(before_a, after_a, ua, "自軍")
             + _changes(before_b, after_b, ub, "敵軍"))
    return moved, caster_index


def declared(sk, effect_text):
    """効果文が「何かを起こす」と言っているか（黙って消えたかの判定に使う）。"""
    return bool(sk.power or sk.heal or sk.mods or sk.wits_mods or sk.self_mods
                or sk.sac)


def audit_one(name, target, sk, effect_text):
    """1つぶんの診断。(重さ, 見出し, 明細) を返す。重さ 0 は問題なし。"""
    moved, ci = probe(sk, target)
    ally_target = ("味方" in target) or ("自分" in target)
    sides = {s for s, _, _, _ in moved}
    notes, level = [], 0

    hurt_own = [m for m in moved if m[0] == "自軍" and m[1] != ci
                and ("兵力減" in m[2] or "延焼" in m[2])]
    heal_foe = [m for m in moved if m[0] == "敵軍"
                and ("兵力増" in m[2] or "持続回復" in m[2])]
    # **符号の向き。** 味方に乗るのはプラスだけ、敵に乗るのはマイナスだけ。
    # 例外は撃ち手自身への反動（self_mods）で、これは自分へのマイナスを
    # 書ける唯一の口である（§7.64）。
    buff_foe = [(m[0], m[1], k, a) for m in moved if m[0] == "敵軍"
                for k, a in m[3] if a > 0.0]
    debuff_ally = [(m[0], m[1], k, a) for m in moved
                   if m[0] == "自軍" and m[1] != ci
                   for k, a in m[3] if a < 0.0]

    if ally_target and (sk.power > 0.0):
        level = 2
        notes.append("味方対象なのに威力を持つ → **味方を殴る**")
    if (not ally_target) and sk.heal > 0.0:
        level = 2
        notes.append("敵対象なのに回復を持つ → **敵を癒す**")
    if hurt_own:
        level = 2
        notes.append("味方が傷んだ: " + "／".join(
            "{}{}".format(s, i) for s, i, _, _ in hurt_own))
    if heal_foe:
        level = 2
        notes.append("敵が回復した: " + "／".join(
            "{}{}".format(s, i) for s, i, _, _ in heal_foe))
    if buff_foe:
        level = 2
        notes.append("敵に**プラスの補正**が乗った: " + "／".join(
            "{}{}{:+.0%}".format(s, i, a) for s, i, k, a in buff_foe))
    if debuff_ally:
        level = 2
        notes.append("撃ち手以外の味方に**マイナスの補正**が乗った: " + "／".join(
            "{}{}{:+.0%}".format(s, i, a) for s, i, k, a in debuff_ally))

    if ally_target:
        bad = [m for m, a, s in sk.mods if a < 0.0]
        if bad:
            level = max(level, 2)
            notes.append("味方対象にマイナス補正（{}）→ **どこにも入らない**"
                         .format("・".join(bad)))
        if sk.wits_mods:
            level = max(level, 2)
            notes.append("味方対象に知力比の弱体 → **どこにも入らない**")
        if any(k in ("chaos", "stun") for k, _, _ in sk.mods):
            level = max(level, 2)
            notes.append("味方対象に混乱／行動阻害 → **どこにも入らない**")

    if declared(sk, effect_text) and not moved:
        level = max(level, 2)
        notes.append("効果文に中身があるのに**誰の値も動かなかった**")

    if ally_target and "敵軍" in sides:
        level = max(level, 2)
        notes.append("味方対象なのに敵軍が動いた")
    if (not ally_target) and sk.power > 0.0 and "敵軍" not in sides:
        level = max(level, 2)
        notes.append("敵対象で威力があるのに敵軍が動かなかった")

    # 規約どおりだが知っておきたいもの
    if (not ally_target) and any(a > 0.0 for _, a, _ in sk.mods):
        level = max(level, 1)
        notes.append("敵対象＋プラス補正 → 撃ち手への自己バフ（規約どおり）")
    return level, notes, moved


def positive_control():
    """**陽性対照**: わざと向き先を壊した札を通して、計器が赤くなるか見る。
    見つからなかったという報せは、見つけられることを確かめて初めて意味を持つ
    （§13）。"""
    print("陽性対照（わざと壊して、監査が気づくかを見る）")
    cases = [
        ("味方対象に威力", "味方全体", F.Skill(power=3.0, kind="melee"), True),
        ("敵対象に回復", "敵全体", F.Skill(heal=1.5, kind="melee"), True),
        ("味方対象にマイナス補正", "味方全体",
         F.Skill(mods=(("atk", -0.10, 30.0),)), True),
        ("味方対象に混乱", "味方全体",
         F.Skill(mods=(("chaos", 0.5, 30.0),)), True),
        ("味方対象に知力比の弱体", "味方全体",
         F.Skill(wits_mods=(("atk", -0.10, 30.0),)), True),
        ("中身のない札", "敵全体", F.Skill(), False),
        ("まっとうな敵ダメージ", "敵全体", F.Skill(power=3.0, kind="melee"), False),
        ("まっとうな味方回復", "味方全体", F.Skill(heal=1.5, kind="melee"), False),
        ("まっとうな味方バフ", "味方全体",
         F.Skill(mods=(("atk", 0.10, 30.0),)), False),
        ("まっとうな敵弱体", "敵全体",
         F.Skill(mods=(("atk", -0.10, 30.0),)), False),
        ("撃ち手への反動（自分へのマイナス）", "敵全体",
         F.Skill(power=3.0, kind="melee", self_mods=(("def", -0.15, 20.0),)), False),
    ]
    ok = True
    for label, target, sk, want_bad in cases:
        level, notes, _ = audit_one(label, target, sk, "")
        got_bad = level >= 2
        hit = got_bad == want_bad
        ok = ok and hit
        print("  {} {:<22} {:<8} {}".format(
            "○" if hit else "×", label, target,
            ("赤" if got_bad else "緑") + "（期待: " + ("赤" if want_bad else "緑") + "）"))
        if got_bad and notes:
            print("        " + notes[0])
    # 向き先そのものが壊れた場合。**今の実装では書き方では作れない**
    #（符号で行き先が決まるので）。盤面の側が将来壊れたときに気づけるよう、
    # 対象の選び方を裏返して赤くなることを確かめておく。
    real = F._skill_targets

    def flipped(target, u, foe, own):
        # 2つの池をそのまま入れ替える。_skill_targets は「味方」なら own から、
        # そうでなければ foe から選ぶので、これで両方向とも裏返る。
        return real(target, u, own, foe)

    F._skill_targets = flipped
    try:
        for label, target, sk in (
                ("盤面が裏返った・味方バフ", "味方全体",
                 F.Skill(mods=(("atk", 0.10, 30.0),))),
                ("盤面が裏返った・敵弱体", "敵全体",
                 F.Skill(mods=(("atk", -0.10, 30.0),))),
                ("盤面が裏返った・味方回復", "味方全体",
                 F.Skill(heal=1.5, kind="melee"))):
            level, notes, _ = audit_one(label, target, sk, "")
            hit = level >= 2
            ok = ok and hit
            print("  {} {:<22} {:<8} {}".format(
                "○" if hit else "×", label, target,
                ("赤" if hit else "緑") + "（期待: 赤）"))
            if notes:
                print("        " + notes[0])
    finally:
        F._skill_targets = real
    print("—" * 60)
    return ok


def audit_traits(show_all):
    """固有特性も**同じ器**（_apply_skill）を通るので、同じ物差しで測る。"""
    R.load_traits_into_field()
    print("固有特性 {} 件を1発ずつ通す。".format(len(F.TRAITS)))
    bad, info = [], []
    for key, (cond, target, cap, sk, jp) in F.TRAITS.items():
        level, notes, moved = audit_one(jp, target, sk, "")
        line = "{:<10} {:<8} {:<16} {}".format(jp, key, target, cond)
        if level >= 2:
            bad.append((jp, notes))
            print("×  " + line)
            for nt in notes:
                print("      " + nt)
        elif level == 1:
            info.append(jp)
            if show_all:
                print("・  " + line + "  " + notes[0])
        elif show_all:
            print("○  " + line + "  → " + "／".join(
                "{}{}".format(sd, i) for sd, i, _, _ in moved[:4]))
    return bad, info


def run():
    show_all = "--all" in sys.argv
    if not positive_control():
        print("×  監査そのものが壊れている（陽性対照が期待どおりに出ない）。")
        return 1
    R.load_skills_into_field()
    rows = R.skills()
    bad, info = [], []
    print("技 {} 件を1発ずつ通して、動いた隊を数える。".format(len(rows)))
    for sk_row in rows:
        name = sk_row["技名"]
        target = sk_row["対象"]
        sk = F.SKILL_INFO[name]
        level, notes, moved = audit_one(name, target, sk, sk_row["効果"])
        line = "{:<12} {:<16} {}".format(name, target, sk_row["効果"][:44])
        if level >= 2:
            bad.append((name, target, notes, moved))
            print("×  " + line)
            for nt in notes:
                print("      " + nt)
        elif level == 1:
            info.append((name, target, notes))
            if show_all:
                print("・  " + line)
                for nt in notes:
                    print("      " + nt)
        elif show_all:
            print("○  " + line + "  → " + "／".join(
                "{}{}".format(s, i) for s, i, _, _ in moved[:4]))

    print("—" * 60)
    tbad, tinfo = audit_traits(show_all)
    bad += [(n, "", nt, []) for n, nt in tbad]
    print("—" * 60)
    if info:
        print("規約どおりだが知っておくもの {} 件"
              "（敵対象＋プラス補正＝撃ち手への自己バフ）:".format(len(info)))
        for name, target, _ in info:
            print("   ・{}（{}）".format(name, target))
    if tinfo:
        print("固有特性で同じ形のもの: " + "・".join(tinfo))
    if bad:
        print("向き先が疑わしい {} 件".format(len(bad)))
        return 1
    print("向き先の食い違いは無し。**技も特性も、対象文字列どおりの側にだけ入っている。**")
    return 0


if __name__ == "__main__":
    sys.exit(run())
