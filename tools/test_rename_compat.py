# -*- coding: utf-8 -*-
"""§7.176 名称整理の互換試験。

改名した札・兵法の旧名が、保存デッキ・陣容（リプレイ）・登用・登録セット・DB の
装備先で読み替えられること。性能（効果・対象・発動条件・ゲージ・能力値）が変わって
いないこと。名前を除いた戦闘結果が同じ盤で一致すること。
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sim import field as F             # noqa: E402
from sim import match as M             # noqa: E402
from sim import play as PL             # noqa: E402
from sim import players as P           # noqa: E402
from sim import rosterdata as R        # noqa: E402

CARDS = M._roster_cards()
IDX = {c.name: c for c in CARDS}


class Aliases(unittest.TestCase):
    def test_new_names_exist_and_old_names_gone(self):
        for old, new in R.CARD_ALIASES.items():
            self.assertIn(new, IDX)
            self.assertNotIn(old, IDX)
            self.assertEqual(R.canonical(old), new)
        for old, new in R.SKILL_ALIASES.items():
            self.assertIn(new, F.SKILL_INFO)
            self.assertNotIn(old, F.SKILL_INFO)

    def test_person_and_version_unchanged(self):
        # 人物は同じ。字号（版名）だけが変わる。版番号は1のまま
        self.assertEqual(M.person_of(IDX["張昭〔子布〕"]), "張昭")
        self.assertEqual(M.person_of(IDX["魯粛〔榻上策〕"]), "魯粛")
        self.assertEqual(M.person_of(IDX["凌統〔公績〕"]), "凌統")
        for n in R.CARD_ALIASES.values():
            self.assertEqual(R.version_of(n), 1)

    def test_to_cards_and_parse_deck_accept_old_names(self):
        for old, new in R.CARD_ALIASES.items():
            self.assertEqual(R.to_cards([old])[0].name, new)
        army, errs = PL.parse_deck(CARDS, "張昭〔文淵〕、魯粛〔塌上策〕、凌統〔断金〕、"
                                   "周泰〔身代〕、黄蓋〔苦肉〕、丁奉〔雪中〕", "魚鱗")
        self.assertEqual(errs, [])
        self.assertEqual([c.name for c in army.cards[:3]],
                         ["張昭〔子布〕", "魯粛〔榻上策〕", "凌統〔公績〕"])

    def test_old_snapshot_reconstructs(self):
        snap = {"form": "魚鱗", "cards": [{"n": "魯粛〔塌上策〕", "t": "chain"},
                                          {"n": "凌統〔断金〕", "t": "avenge"},
                                          {"n": "張昭〔文淵〕", "t": "relief"},
                                          {"n": "周泰〔身代〕", "t": "double"},
                                          {"n": "黄蓋〔苦肉〕", "t": "vanguard"},
                                          {"n": "丁奉〔雪中〕", "t": ""}]}
        army = PL.army_from_snap(CARDS, snap)
        self.assertEqual([c.name for c in army.cards[:3]],
                         ["魯粛〔榻上策〕", "凌統〔公績〕", "張昭〔子布〕"])

    def test_balance_common_index_has_old_keys(self):
        from tools import balance_common as C
        idx = C.card_index(CARDS)
        for old, new in R.CARD_ALIASES.items():
            self.assertIs(idx[old], idx[new])

    def test_senki_and_fixtures_use_new_names(self):
        import csv
        with open(os.path.join(R.DATA, "senki.csv"), encoding="utf-8-sig") as fh:
            text = fh.read()
        for old in R.CARD_ALIASES:
            self.assertNotIn(old, text)
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
                "docs", "balance", "fixtures-v1.json"), encoding="utf-8") as fh:
            fx = json.load(fh)
        txt = json.dumps(fx, ensure_ascii=False)
        for old in R.CARD_ALIASES:
            self.assertNotIn(old, txt)


class DbMigration(unittest.TestCase):
    def test_decks_and_treasures_are_renamed_on_connect(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            cx = P.connect(path)
            pl = P.register(cx, "改名試験")
            deck = "張昭〔文淵〕、周泰〔身代〕、黄蓋〔苦肉〕、丁奉〔雪中〕、韓当〔老弓〕、宗預〔使者〕"
            with cx:
                cx.execute("INSERT INTO decks (player_id, regulation, cards, formation) VALUES (?,?,?,?)",
                           (pl.id, "汜水関", deck, "魚鱗"))
                cx.execute("INSERT INTO saved_decks (player_id, name, regulation, cards, formation) VALUES (?,?,?,?,?)",
                           (pl.id, "控え", "汜水関", deck, "魚鱗"))
                cx.execute("INSERT INTO senki_decks (player_id, battle_i, cards, formation) VALUES (?,?,?,?)",
                           (pl.id, 3, deck, "魚鱗"))
                cx.execute("INSERT INTO owned_treasures (player_id, key, general_name, gained_at) VALUES (?,?,?,datetime('now'))",
                           (pl.id, "t_seiryu", "魯粛〔塌上策〕"))
            cx.close()
            cx = P.connect(path)         # 接続時の移行
            raw, form = P.decks_of(cx, pl.id)["汜水関"]
            self.assertIn("張昭〔子布〕", raw)
            self.assertNotIn("張昭〔文淵〕", raw)
            for table in ("saved_decks", "senki_decks"):
                txt = cx.execute("SELECT cards FROM {} WHERE player_id = ?".format(table), (pl.id,)).fetchone()[0]
                self.assertIn("張昭〔子布〕", txt)
                self.assertNotIn("張昭〔文淵〕", txt)
            self.assertEqual(P.treasures_on(cx, pl.id, "魯粛〔榻上策〕"), ["t_seiryu"])
            army, errs = PL.parse_deck(CARDS, raw, form)
            self.assertEqual(errs, [])


class PerformanceUnchanged(unittest.TestCase):
    """効果・対象・発動条件・ゲージ・能力値が名前以外は変わっていない。

    §7.177（改名の後）で魯粛の兵法の内容だけは意図して変えたので、その行は新しい値で持つ。
    """

    # 【§7.264】決戦型の効果文は、段の重みを 0.479 → 0.350 にしたぶん
    # **1.39倍ほど太らせてある**（身体は動かしていない）。ここは「改名で性能が
    # 変わっていないこと」の見張りなので、**意図して動かした値は追随させる**。
    EXPECT = {
        # §7.177 で内容を変えた（攻撃力 +5%（54秒）→ 攻防 +3%・追記で 72秒）。改名時の不変は 58a4fe5 で確認済み
        "榻上の策": ("味方全体", "攻撃力 +3%（72秒） + 防御力 +3%（72秒）", "150"),
        "逍遥津の奮戦": ("敵1体（正面）", "ダメージ 威力1167%", "300"),
        "濡須の督戦": ("味方後衛", "攻撃力 +13%（60秒）", "150"),
        "捨て身の迎撃": ("敵1体（前衛の主力）", "ダメージ 威力1321% + 反動 攻撃力 -10%（30秒）", "300"),
        "救主の奮戦": ("味方1体（残兵力が最少）", "回復 攻撃力の177%", "150"),
        # §7.180 で 45→73%、§7.183 で実測に合わせて 35% へ。改名時の不変は 58a4fe5 で確認済み
        "火船突入": ("敵1列", "継続ダメージ 威力48%（13秒）", "300"),
        # §7.197 で 敵1列の継続ダメージ＋命中率低下 → 敵前衛への一撃（孔明を10コストの瞬発へ）
        "借風火攻": ("敵前衛", "ダメージ 威力820%", "300"),
        "陳倉の備え": ("味方全体", "兵法防御 +25%（54秒）", "150"),
        "護軍の迎撃": ("敵1体（正面）", "ダメージ 威力115%", "75"),
        # §7.231 で 移動速度 の節を落とした（値札0の器を名簿から全部外した）
        "連営火攻": ("敵1列", "継続ダメージ 威力76%（14秒）", "300"),
    }

    def test_skill_rows(self):
        rows = {s["兵法名"]: s for s in R.skills()}
        for name, (target, effect, gauge) in self.EXPECT.items():
            self.assertEqual((rows[name]["対象"], rows[name]["効果"], rows[name]["消費ゲージ%"]),
                             (target, effect, gauge), name)

    def test_generals_keep_skill_link_and_stats(self):
        g = {r["名前"]: r for r in R.generals()}
        self.assertEqual(g["張昭〔子布〕"]["字号"], "子布")
        self.assertEqual(g["魯粛〔榻上策〕"]["兵法"], "榻上の策")
        self.assertEqual(g["凌統〔公績〕"]["兵法"], "逍遥津の奮戦")
        self.assertEqual(g["全琮〔護軍〕"]["兵法"], "護軍の迎撃")
        self.assertEqual(g["諸葛亮〔臥龍〕"]["兵法"], "借風火攻")
        # 数値（コスト・兵力・武力・知力・ゲージ）。改名（§7.176・58a4fe5）では 16766/383.0/468.7 のまま
        # だったことを確認済み。§7.177（兵法の内容）と §7.178（防御の単価）で効果予算が 0.534 → 0.930 に
        # 動き、**§7.187 で弓の兵種係数が 0.4262 → 0.61・§7.188 で 0.59 へ**（3人とも弓兵）。
        # §7.188 は単価も引き直しているので、兵法の中身しだいで札ごとに動き方が違う。
        # **§7.193 で弓の兵種係数が 0.587 → 0.6032**（兵法防御の単価を測り直した波及で
        # 3辺を詰め直した）。弓兵2人（魯粛・張昭）だけ下がり、歩兵の凌統は不動
        self.assertEqual((g["魯粛〔榻上策〕"]["コスト"], g["魯粛〔榻上策〕"]["兵力"], g["魯粛〔榻上策〕"]["武力"],
                          g["魯粛〔榻上策〕"]["知力"], g["魯粛〔榻上策〕"]["消費ゲージ%"]),
                         ("7", "11397", "260.4", "318.6", "150"))
        self.assertEqual((g["凌統〔公績〕"]["兵力"], g["凌統〔公績〕"]["武力"], g["凌統〔公績〕"]["固有特性"]),
                         ("7529", "153.6", "avenge"))
        self.assertEqual((g["張昭〔子布〕"]["兵力"], g["張昭〔子布〕"]["知力"], g["張昭〔子布〕"]["固有特性"]),
                         ("7345", "234.5", "relief"))

    def test_trait_display_rename_keeps_key_and_effect(self):
        cond, target, cap, sk, jp = F.TRAITS["disrupt"]
        self.assertEqual(jp, "攪乱")
        self.assertEqual((cond, target, cap), ("ally_skill", "敵全体", 3))
        self.assertEqual([k for k, _a, _s in sk.mods], ["atk"])
        # 弔旗・弔い合戦・影武者は名前も性能も据え置き（説明だけ整理）
        self.assertEqual(F.TRAITS["banner"][4], "弔旗")
        self.assertEqual(F.TRAITS["avenge"][4], "弔い合戦")
        self.assertEqual(F.TRAITS["double"][4], "影武者")
        t = {r["キー"]: r for r in R.traits()}
        self.assertIn("弔い合戦", t["banner"]["説明"])
        self.assertIn("弔旗", t["avenge"]["説明"])
        self.assertIn("肩代わり", t["double"]["説明"])

    def test_battle_identical_from_old_and_new_names(self):
        old = PL.army_from_snap(CARDS, {"form": "魚鱗", "cards": [
            {"n": "魯粛〔塌上策〕", "t": "chain"}, {"n": "凌統〔断金〕", "t": "avenge"},
            {"n": "張昭〔文淵〕", "t": "relief"}, {"n": "周泰〔身代〕", "t": "double"},
            {"n": "黄蓋〔苦肉〕", "t": "vanguard"}, {"n": "丁奉〔雪中〕", "t": ""}]})
        new, errs = PL.parse_deck(CARDS, "魯粛〔榻上策〕、凌統〔公績〕、張昭〔子布〕、周泰〔身代〕、黄蓋〔苦肉〕、丁奉〔雪中〕", "魚鱗")
        self.assertEqual(errs, [])
        foe, errs = PL.parse_deck(CARDS, "曹仁〔堅守〕、孫乾〔従事〕、糜竺〔子仲〕、韓当〔老弓〕、樊建〔伝令〕、宗預〔使者〕", "鶴翼")
        self.assertEqual(errs, [])
        for seed in (1, 2):
            a = F.simulate(old, foe, 0.5, seed=seed)
            b = F.simulate(new, foe, 0.5, seed=seed)
            self.assertEqual((a["score"], a["diff"], a["ra"], a["rb"], a["t"]),
                             (b["score"], b["diff"], b["ra"], b["rb"], b["t"]))


if __name__ == "__main__":
    unittest.main(verbosity=1)
