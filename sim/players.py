# -*- coding: utf-8 -*-
"""sim/players.py -- 登録者の器

**3つに分ける。** 分けておくと、課金の形が変わってもゲーム側が壊れない。

    players     … ゲームが使う。**個人情報を持たない**（不透明IDと表示名だけ）
    identities  … 個人情報。メール・IdP の subject。ここだけを厳重に扱う
    billing     … 決済代行の顧客IDと権利状態。**カード情報は持たない**

編成・対戦・リプレイ・ランキングは `players.id` だけで回る。だから偵察の
スナップショット（§3.3）やリプレイ公開の設計で個人情報を気にしなくてよい。
退会も identities を消すだけで済み、対戦履歴の整合性は保たれる。

================================================================================
 いま SQLite なのはなぜか
================================================================================
プレイヤーがまだ居らず、中身は全部ダミー（足場）だからである。**人が入る前に
マネージドな DB と外部の認証へ移すこと。** 認証を外部へ出すとパスワードを自分で
持たなくて済む（持たないものは漏れない）。

DB ファイルは **git に入れない**（`sim/data/*.db` を .gitignore 済み）。

================================================================================
 課金の形（v0.6 で決めた）
================================================================================
月額。ガチャ無し。ゲーム内通貨を売らない。**当面は無課金で回す**ので、
`ACTIVE_PLANS` は free だけ。有料プランは形だけ定義してある。

規定回数を超えてレート対象マッチへ挑めるのは**有料**。設計は旧 DCI（Elo）式で、

  - 当たるのは同レーティング帯域
  - 勝てば上がり、**負ければ下がる**（相手は上がる）

**「回数を買える＝順位を買える」にはならない。** ゼロサムのレートで同レート帯と
当たる以上、多く挑んでも期待値は上がらず、実力が伴わなければ下がる。買えるのは
**挑戦権であって点数ではない**。§3.1 が潰したかった「在席時間が強さになる」は
回数そのものではなく**回数が一方向に効くこと**であり、下振れのある回数はそれに
当たらない。

**ただし順位表を「最高到達レート」で見せると買えてしまう。** 試行が多いほど運の
良い連勝を引く確率が上がる（盤面には σ=0.15 の乱数がある。§7.27）。**順位は現在
レートで付けること。** 最高到達を見せたい場合は、別枠の記録として扱う。

`plan` は権利の名前だけを持ち、**何が付くかはコード側の表**で決める。DB に
効果を書くと、プラン改定のたびに既存行の書き換えが要る。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

DATA = os.path.join(os.path.dirname(__file__), "data")
# SANGOKU_DB で差し替え可能（検証・公開用）。既定はリポジトリ内。
DB_PATH = os.environ.get("SANGOKU_DB", os.path.join(DATA, "players.db"))

HUMAN = "human"
DUMMY = "dummy"

# ダミーのメールは**予約ドメイン**にする（RFC 6761）。実在アドレスとぶつからず、
# 誤って送信処理へ流れても届きようがない。足場を撤去し忘れても事故にならない。
DUMMY_DOMAIN = "example.invalid"

RATING_START = 1500.0

# 1枚の武将にセットできる固有特性の数（§7.37）。**生まれつきの特性とは別枠。**
# 盤面（field.Unit）はまだ1つしか読まないので、いまは器だけを用意している。
TRAIT_SLOTS = 3

# プランごとの権利。`rated_per_day` は §3.1 の「レート変動上限」に当たる。
# 有料で増えるが、**増えるのは挑戦できる回数であって点数ではない**（上の注記）。
PLANS: Dict[str, Dict[str, int]] = {
    "free":    {"rated_per_day": 12, "practice_per_day": 6,  "entry_slots": 3},
    "monthly": {"rated_per_day": 36, "practice_per_day": 48, "entry_slots": 12},
}
# **いま提供しているプラン。** 当面は無課金で回すので free だけ。ここを見て
# 課金導線を出すこと。PLANS に定義があることと、売っていることは別である。
ACTIVE_PLANS: Tuple[str, ...] = ("free",)


@dataclass
class Player:
    id: str
    display_name: str
    kind: str
    rating: float
    prime_start: Optional[int] = None   # 主戦時間帯の開始（0-23）
    prime_hours: Optional[int] = None   # 同・長さ
    plan: str = "free"

    def is_dummy(self) -> bool:
        return self.kind == DUMMY


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
  id            TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('human','dummy')),
  rating        REAL NOT NULL,
  prime_start   INTEGER,
  prime_hours   INTEGER,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- 個人情報はここだけ。**ゲーム側からは参照しない。**
CREATE TABLE IF NOT EXISTS identities (
  player_id     TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
  email         TEXT NOT NULL,
  provider      TEXT,
  subject       TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
-- メールの重複登録を DB で止める。表計算では張れない種類の制約である。
CREATE UNIQUE INDEX IF NOT EXISTS ix_identities_email ON identities(email);
CREATE UNIQUE INDEX IF NOT EXISTS ix_identities_sub
  ON identities(provider, subject) WHERE provider IS NOT NULL;
-- 獲得した固有特性（1日1つ）。**どの武将へセットしたかは別の列で持つ。**
-- 未セットなら general_name が空。取り外しを許すかは未決なので、履歴は残す。
CREATE TABLE IF NOT EXISTS owned_traits (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  player_id     TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  trait_key     TEXT NOT NULL,
  general_name  TEXT NOT NULL DEFAULT '',
  slot          INTEGER,
  gained_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
-- **同じ武将の同じ枠には1つまで。** 枠は 0..TRAIT_SLOTS-1。
CREATE UNIQUE INDEX IF NOT EXISTS ix_owned_slot
  ON owned_traits(player_id, general_name, slot)
  WHERE general_name <> '';
CREATE INDEX IF NOT EXISTS ix_owned_player ON owned_traits(player_id);
-- 順位表ごとのレート（§7.35: BO1の3レギュ + BO3 で別々）。順位は表示時に
-- 現在レート順で付ける。games は可変K（§7.38）の入力。
CREATE TABLE IF NOT EXISTS ratings (
  player_id  TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  board      TEXT NOT NULL,
  rating     REAL NOT NULL,
  games      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (player_id, board)
);
-- 登録デッキ（レギュレーションごとに6枚）。カードは武将名を「、」区切り、
-- **並び順がそのまま配置**（前衛から）。formation は 標準/広く浅い/狭く深い。
CREATE TABLE IF NOT EXISTS decks (
  player_id  TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  regulation TEXT NOT NULL,
  cards      TEXT NOT NULL,
  formation  TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (player_id, regulation)
);
-- 順位表の巡カウンタ。組の告知と乱数種の導出に使う。
CREATE TABLE IF NOT EXISTS boards (
  name  TEXT PRIMARY KEY,
  round INTEGER NOT NULL DEFAULT 0
);
-- 対戦の記録。**種と組だけ持てばリプレイは何度でも再生できる**（§8.4）。
-- 勝敗は保存しない（種から決定的に再計算できる。二重に持つと食い違いが出る）。
CREATE TABLE IF NOT EXISTS matches (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  board      TEXT NOT NULL,
  round      INTEGER NOT NULL,
  pid_a      TEXT NOT NULL,
  pid_b      TEXT NOT NULL,
  seed       INTEGER NOT NULL,
  played_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_matches_board ON matches(board, round);
-- 告知済みの組（§3: 組む→告知→編成期間→戦う）。resolve で消費し matches へ。
CREATE TABLE IF NOT EXISTS pairings (
  board      TEXT NOT NULL,
  round      INTEGER NOT NULL,
  pid_a      TEXT NOT NULL,
  pid_b      TEXT NOT NULL,
  PRIMARY KEY (board, round, pid_a)
);
-- 兵符（BO1挑戦権・§7.43）。**残数と最終更新だけ持ち、回復は読むときに計算**
-- （cron が要らない。30分ごとに+1・上限10）。
CREATE TABLE IF NOT EXISTS tokens (
  player_id  TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
  count      INTEGER NOT NULL,
  updated_at INTEGER NOT NULL              -- unix秒
);
-- デッキ保存庫（§7.46）。名前を付けて何個でも取っておける。**登録（decks 表）
-- とは別物**: 保存は下書きでもよく、検証は登録の瞬間だけ行う。
CREATE TABLE IF NOT EXISTS saved_decks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  player_id  TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  regulation TEXT NOT NULL,
  cards      TEXT NOT NULL,
  formation  TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (player_id, regulation, name)
);
-- ⑨（§7.58）対戦の記録v2。**デッキの魚拓を持つ** — 後からデッキを変えても
-- リプレイが変わらない（旧 matches は登録デッキから再構成していて、差し替えで
-- 過去のリプレイごと変わった）。勝敗は保存しない（種＋魚拓から決定的に再計算）。
CREATE TABLE IF NOT EXISTS battles (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  mode       TEXT NOT NULL,             -- ranked / tenka / free / room
  board      TEXT NOT NULL,             -- 汜水関/官渡/赤壁/天下
  pid_a      TEXT NOT NULL,             -- ranked では挑んだ側
  pid_b      TEXT NOT NULL,
  seed       INTEGER NOT NULL,
  snap_a     TEXT NOT NULL,             -- デッキ魚拓（JSON）。'' は旧記録
  snap_b     TEXT NOT NULL,
  season     TEXT NOT NULL,             -- YYYY-MM
  played_at  INTEGER NOT NULL           -- unix秒
);
CREATE INDEX IF NOT EXISTS ix_battles_board ON battles(board, played_at);
CREATE INDEX IF NOT EXISTS ix_battles_pid ON battles(pid_a, board, played_at);
-- ルーム対戦（フリー・レート不変動）。番号を発行→相手が入力→即解決。
CREATE TABLE IF NOT EXISTS rooms (
  code       TEXT PRIMARY KEY,
  creator    TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  regulation TEXT NOT NULL,
  snap       TEXT NOT NULL,             -- 発行時点のデッキ魚拓
  created_at INTEGER NOT NULL,
  battle_id  INTEGER                    -- 成立したら battles.id
);
-- 定刻処理の台帳（天下の開催・月次リセット。**全部遅延評価** — cron が要らず、
-- 手元でもクラウドでも同じ動きになる。兵符の回復と同じ考え方）。
CREATE TABLE IF NOT EXISTS ledger (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- 順位表の毎時断面（表示用。レートの適用自体はマッチ即時 — 適用を毎時に
-- まとめると、その1時間内の連戦の順序で結果が変わる歪みが出る）。
CREATE TABLE IF NOT EXISTS standings_cache (
  board    TEXT PRIMARY KEY,
  hour_key TEXT NOT NULL,
  data     TEXT NOT NULL
);
-- 月次の魚拓（シーズン末の順位表と全デッキ）。リセットの前に必ず焼く。
CREATE TABLE IF NOT EXISTS archives (
  season TEXT NOT NULL,
  board  TEXT NOT NULL,
  data   TEXT NOT NULL,
  PRIMARY KEY (season, board)
);
-- 武将の解放（戦記の登用・§7.60）。**人物名で持つ**（カード名でなく）—
-- 同一人物の別バージョンが増えても登用は1回で済む。無い人物は使えない。
CREATE TABLE IF NOT EXISTS unlocks (
  player_id  TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  person     TEXT NOT NULL,
  source     TEXT NOT NULL,                -- start / senki:4-2 / migration / dev
  got_at     TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (player_id, person)
);
-- 決済代行の顧客IDと権利状態だけ。**カード情報は持たない。**
CREATE TABLE IF NOT EXISTS billing (
  player_id     TEXT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
  provider      TEXT,
  customer_id   TEXT,
  plan          TEXT NOT NULL DEFAULT 'free',
  valid_until   TEXT
);
"""


def connect(path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    cx.executescript(SCHEMA)
    _migrate_names(cx)
    return cx


# 盤面名の改名（§7.43: 低/中/高/統一 → 汜水関/官渡/赤壁/天下）。旧名の行を
# 読み替える。冪等なので毎回流してよい。
_NAME_MIGRATION = {"低コスト戦": "汜水関", "中コスト戦": "官渡",
                   "高コスト戦": "赤壁", "統一(BO3)": "天下"}


def _migrate_names(cx: sqlite3.Connection) -> None:
    with cx:
        for old, new in _NAME_MIGRATION.items():
            cx.execute("UPDATE decks SET regulation = ? WHERE regulation = ?",
                       (new, old))
            cx.execute("UPDATE ratings SET board = ? WHERE board = ?"
                       " AND NOT EXISTS (SELECT 1 FROM ratings r2"
                       "  WHERE r2.board = ? AND r2.player_id = ratings.player_id)",
                       (new, old, new))
            cx.execute("DELETE FROM ratings WHERE board = ?", (old,))
            cx.execute("UPDATE boards SET name = ? WHERE name = ?"
                       " AND NOT EXISTS (SELECT 1 FROM boards b2 WHERE b2.name = ?)",
                       (new, old, new))
            cx.execute("DELETE FROM boards WHERE name = ?", (old,))
            cx.execute("UPDATE matches SET board = ? WHERE board = ?", (new, old))


def new_id() -> str:
    """不透明なプレイヤーID。**連番にしない**（総数と登録順が漏れる）。"""
    return uuid.uuid4().hex


def register(cx: sqlite3.Connection, display_name: str, *, kind: str = HUMAN,
             email: Optional[str] = None, provider: Optional[str] = None,
             subject: Optional[str] = None, plan: str = "free",
             prime_start: Optional[int] = None,
             prime_hours: Optional[int] = None) -> Player:
    """登録する。**1つのトランザクションで3表を書く。**

    メールが重複していれば `sqlite3.IntegrityError` になり、players の行も
    残らない。表計算だと「片方だけ書けた」が起こるが、ここでは起こらない。
    """
    pid = new_id()
    with cx:
        cx.execute(
            "INSERT INTO players (id, display_name, kind, rating,"
            " prime_start, prime_hours) VALUES (?,?,?,?,?,?)",
            (pid, display_name, kind, RATING_START, prime_start, prime_hours))
        if email or subject:
            cx.execute(
                "INSERT INTO identities (player_id, email, provider, subject)"
                " VALUES (?,?,?,?)", (pid, email or "", provider, subject))
        cx.execute("INSERT INTO billing (player_id, plan) VALUES (?,?)",
                   (pid, plan))
    return Player(pid, display_name, kind, RATING_START,
                  prime_start, prime_hours, plan)


def entitlement(cx: sqlite3.Connection, player_id: str) -> Dict[str, int]:
    """そのプレイヤーの権利。**表はコード側**（DB にはプラン名だけ）。"""
    r = cx.execute("SELECT plan FROM billing WHERE player_id=?",
                   (player_id,)).fetchone()
    return dict(PLANS.get(r["plan"] if r else "free", PLANS["free"]))


def get(cx: sqlite3.Connection, player_id: str) -> Optional[Player]:
    r = cx.execute(
        "SELECT p.*, COALESCE(b.plan,'free') AS plan FROM players p"
        " LEFT JOIN billing b ON b.player_id=p.id WHERE p.id=?",
        (player_id,)).fetchone()
    return _row(r) if r else None


def leaderboard(cx: sqlite3.Connection, limit: int = 50) -> List[Player]:
    """順位表。**現在レートで並べる（最高到達では並べない）。**

    最高到達で並べると、レート対象マッチを多く買った人ほど運の良い連勝を
    引きやすく、**順位が買えてしまう**。現在レートなら、下振れも同じだけ
    反映されるので買えない。
    """
    q = ("SELECT p.*, COALESCE(b.plan,'free') AS plan FROM players p"
         " LEFT JOIN billing b ON b.player_id=p.id"
         " WHERE p.display_name <> '(退会)'"
         " ORDER BY p.rating DESC, p.id LIMIT ?")
    return [_row(r) for r in cx.execute(q, (limit,))]


def all_players(cx: sqlite3.Connection, kind: Optional[str] = None
                ) -> List[Player]:
    q = ("SELECT p.*, COALESCE(b.plan,'free') AS plan FROM players p"
         " LEFT JOIN billing b ON b.player_id=p.id")
    args: tuple = ()
    if kind:
        q += " WHERE p.kind=?"
        args = (kind,)
    return [_row(r) for r in cx.execute(q + " ORDER BY p.rating DESC", args)]


def email_of(cx: sqlite3.Connection, player_id: str) -> Optional[str]:
    """**個人情報を読む唯一の入口。** ここを通る箇所だけ監査すればよい。"""
    r = cx.execute("SELECT email FROM identities WHERE player_id=?",
                   (player_id,)).fetchone()
    return r["email"] if r else None


def forget(cx: sqlite3.Connection, player_id: str) -> None:
    """退会。**個人情報だけ消し、対戦履歴は残す。**

    players は不透明IDと表示名しか持たないので、identities を消せば個人は
    特定できなくなる。対戦相手のリプレイや順位表の整合性は保たれる。
    """
    with cx:
        cx.execute("DELETE FROM identities WHERE player_id=?", (player_id,))
        cx.execute("UPDATE players SET display_name='(退会)' WHERE id=?",
                   (player_id,))


def grant_trait(cx: sqlite3.Connection, player_id: str, key: str) -> int:
    """特性を1つ渡す（1日1つの獲得）。**未セットの状態で積む。**"""
    with cx:
        cur = cx.execute(
            "INSERT INTO owned_traits (player_id, trait_key) VALUES (?,?)",
            (player_id, key))
    return cur.lastrowid


def daily_onsho(cx: sqlite3.Connection, player_id: str, keys,
                today: str) -> Optional[str]:
    """本日の恩賞（1日1特性・§7.43）。まだなら1つ授けて鍵を返す。

    どれを授けるかは (player_id, 日付) から決定的に選ぶ。読み直しで
    引き直せない（リロードでガチャになるのを防ぐ）。"""
    import zlib
    r = cx.execute(
        "SELECT 1 FROM owned_traits WHERE player_id=? AND date(gained_at)=?",
        (player_id, today)).fetchone()
    if r is not None:
        return None
    keys = sorted(keys)
    key = keys[zlib.crc32((player_id + today).encode()) % len(keys)]
    grant_trait(cx, player_id, key)
    return key


def owned_traits(cx: sqlite3.Connection, player_id: str) -> List[Dict]:
    """獲得済みの恩賞ぜんぶ（セット状況つき）。"""
    return [dict(r) for r in cx.execute(
        "SELECT id, trait_key, general_name, slot, gained_at"
        " FROM owned_traits WHERE player_id=? ORDER BY id", (player_id,))]


def free_slot(cx: sqlite3.Connection, player_id: str,
              general_name: str) -> Optional[int]:
    """その武将の空いている軍功枠。無ければ None。"""
    used = {r["slot"] for r in cx.execute(
        "SELECT slot FROM owned_traits WHERE player_id=? AND general_name=?",
        (player_id, general_name))}
    for i in range(TRAIT_SLOTS):
        if i not in used:
            return i
    return None


def set_trait(cx: sqlite3.Connection, player_id: str, owned_id: int,
              general_name: str, slot: int) -> None:
    """獲得済みの特性を武将の枠へセットする。

    **枠は 0..TRAIT_SLOTS-1。** 同じ武将の同じ枠は UNIQUE 制約が止める。
    表計算では張れない種類の制約で、二重セットは静かに起きる。
    """
    if general_name == "":
        slot = None                      # 外す（未セットへ戻す）
    elif not 0 <= slot < TRAIT_SLOTS:
        raise ValueError("枠は 0〜{} まで".format(TRAIT_SLOTS - 1))
    with cx:
        cx.execute(
            "UPDATE owned_traits SET general_name=?, slot=?"
            " WHERE id=? AND player_id=?",
            (general_name, slot, owned_id, player_id))


def traits_on(cx: sqlite3.Connection, player_id: str,
              general_name: str) -> List[str]:
    """その武将にセットされている特性（枠の順）。"""
    return [r["trait_key"] for r in cx.execute(
        "SELECT trait_key FROM owned_traits"
        " WHERE player_id=? AND general_name=? ORDER BY slot",
        (player_id, general_name))]


def unset_traits(cx: sqlite3.Connection, player_id: str) -> List[Dict]:
    """まだどの武将にも付けていない特性。"""
    return [dict(r) for r in cx.execute(
        "SELECT id, trait_key, gained_at FROM owned_traits"
        " WHERE player_id=? AND general_name='' ORDER BY id", (player_id,))]


def board_ratings(cx: sqlite3.Connection, board: str
                  ) -> Dict[str, Tuple[float, int]]:
    """その順位表の全レート {player_id: (rating, games)}。"""
    return {r["player_id"]: (r["rating"], r["games"]) for r in cx.execute(
        "SELECT player_id, rating, games FROM ratings WHERE board = ?", (board,))}


def save_board_ratings(cx: sqlite3.Connection, board: str,
                       vals: Dict[str, Tuple[float, int]]) -> None:
    with cx:
        cx.executemany(
            "INSERT INTO ratings (player_id, board, rating, games)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(player_id, board)"
            " DO UPDATE SET rating = excluded.rating, games = excluded.games",
            [(pid, board, r, g) for pid, (r, g) in vals.items()])


def board_round(cx: sqlite3.Connection, board: str) -> int:
    r = cx.execute("SELECT round FROM boards WHERE name = ?", (board,)).fetchone()
    return r["round"] if r else 0


def bump_board_round(cx: sqlite3.Connection, board: str) -> int:
    """巡カウンタを1進めて、いま終えた巡の番号を返す。"""
    with cx:
        cx.execute("INSERT INTO boards (name, round) VALUES (?, 1)"
                   " ON CONFLICT(name) DO UPDATE SET round = round + 1", (board,))
    return board_round(cx, board) - 1


def set_deck(cx: sqlite3.Connection, player_id: str, regulation: str,
             cards: str, formation: str) -> None:
    with cx:
        cx.execute(
            "INSERT INTO decks (player_id, regulation, cards, formation)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(player_id, regulation) DO UPDATE SET"
            " cards = excluded.cards, formation = excluded.formation,"
            " updated_at = datetime('now')",
            (player_id, regulation, cards, formation))


def decks_of(cx: sqlite3.Connection, player_id: str) -> Dict[str, Tuple[str, str]]:
    """{レギュレーション: (カード名の「、」区切り, 陣形名)}。"""
    return {r["regulation"]: (r["cards"], r["formation"]) for r in cx.execute(
        "SELECT regulation, cards, formation FROM decks WHERE player_id = ?",
        (player_id,))}


def save_deck_as(cx: sqlite3.Connection, player_id: str, name: str,
                 regulation: str, cards: str, formation: str) -> None:
    """保存庫へ入れる。同じ名前なら上書き。"""
    with cx:
        cx.execute(
            "INSERT INTO saved_decks (player_id, name, regulation, cards,"
            " formation) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(player_id, regulation, name) DO UPDATE SET"
            " cards = excluded.cards, formation = excluded.formation,"
            " updated_at = datetime('now')",
            (player_id, name, regulation, cards, formation))


def saved_decks(cx: sqlite3.Connection, player_id: str) -> List[Dict]:
    return [dict(r) for r in cx.execute(
        "SELECT id, name, regulation, cards, formation FROM saved_decks"
        " WHERE player_id = ? ORDER BY regulation, name", (player_id,))]


def delete_saved_deck(cx: sqlite3.Connection, player_id: str,
                      deck_id: int) -> None:
    with cx:
        cx.execute("DELETE FROM saved_decks WHERE id = ? AND player_id = ?",
                   (deck_id, player_id))


# ---------------------------------------------------------------- 告知の組
def save_pairs(cx: sqlite3.Connection, board: str, rnd: int,
               pairs) -> None:
    with cx:
        cx.executemany(
            "INSERT OR IGNORE INTO pairings (board, round, pid_a, pid_b)"
            " VALUES (?, ?, ?, ?)",
            [(board, rnd, a, b) for a, b in pairs])


def load_pairs(cx: sqlite3.Connection, board: str, rnd: int):
    return [(r["pid_a"], r["pid_b"]) for r in cx.execute(
        "SELECT pid_a, pid_b FROM pairings WHERE board = ? AND round = ?"
        " ORDER BY pid_a", (board, rnd))]


def clear_pairs(cx: sqlite3.Connection, board: str, rnd: int) -> None:
    with cx:
        cx.execute("DELETE FROM pairings WHERE board = ? AND round = ?",
                   (board, rnd))


# ---------------------------------------------------------------- 兵符
HEIFU_CAP = 10
HEIFU_REGEN_SEC = 10 * 60   # ⑨で 30分→10分（挑戦ラダーの回転・§7.58）


def heifu(cx: sqlite3.Connection, player_id: str, now: int) -> Tuple[int, int]:
    """兵符の残数と、次の1枚まで何秒か。読むだけ（書かない）。"""
    r = cx.execute("SELECT count, updated_at FROM tokens WHERE player_id = ?",
                   (player_id,)).fetchone()
    if r is None:
        return HEIFU_CAP, 0
    n = r["count"] + (now - r["updated_at"]) // HEIFU_REGEN_SEC
    if n >= HEIFU_CAP:
        return HEIFU_CAP, 0
    return n, HEIFU_REGEN_SEC - (now - r["updated_at"]) % HEIFU_REGEN_SEC


def spend_heifu(cx: sqlite3.Connection, player_id: str, amount: int,
                now: int) -> bool:
    """兵符を減らす。足りなければ False（何も書かない）。

    updated_at は「最後に回復を数えた時刻」。回復をまず具現化（count へ足して
    時刻を回復の刻みぶん進める）してから引く。満タンなら時刻を now に置く
    （満タン中は回復が進まないので、使った瞬間が次の回復の起点）。"""
    r = cx.execute("SELECT count, updated_at FROM tokens WHERE player_id = ?",
                   (player_id,)).fetchone()
    if r is None:
        n, anchor = HEIFU_CAP, now
    else:
        ticks = (now - r["updated_at"]) // HEIFU_REGEN_SEC
        n = min(HEIFU_CAP, r["count"] + ticks)
        anchor = now if n >= HEIFU_CAP else r["updated_at"] + ticks * HEIFU_REGEN_SEC
    if n < amount:
        return False
    with cx:
        cx.execute("INSERT INTO tokens (player_id, count, updated_at)"
                   " VALUES (?, ?, ?)"
                   " ON CONFLICT(player_id) DO UPDATE SET"
                   " count = excluded.count, updated_at = excluded.updated_at",
                   (player_id, n - amount, anchor))
    return True


def refill_heifu(cx: sqlite3.Connection, player_id: str, now: int) -> None:
    """兵符を満タンへ（**手元の試験用**。公開版では出さない・§7.49）。"""
    with cx:
        cx.execute("INSERT INTO tokens (player_id, count, updated_at)"
                   " VALUES (?, ?, ?)"
                   " ON CONFLICT(player_id) DO UPDATE SET"
                   " count = excluded.count, updated_at = excluded.updated_at",
                   (player_id, HEIFU_CAP, now))


# ---------------------------------------------------------------- 解放（登用）
def unlocked(cx: sqlite3.Connection, player_id: str) -> set:
    """解放済みの人物名の集合。"""
    return {r["person"] for r in cx.execute(
        "SELECT person FROM unlocks WHERE player_id = ?", (player_id,))}


def unlock(cx: sqlite3.Connection, player_id: str, persons,
           source: str) -> int:
    """人物を解放する（登用）。既に持っていれば黙って何もしない。
    戻りは新しく増えた数。"""
    before = cx.execute("SELECT COUNT(*) AS n FROM unlocks WHERE player_id = ?",
                        (player_id,)).fetchone()["n"]
    with cx:
        cx.executemany(
            "INSERT OR IGNORE INTO unlocks (player_id, person, source)"
            " VALUES (?, ?, ?)", [(player_id, p, source) for p in persons])
    after = cx.execute("SELECT COUNT(*) AS n FROM unlocks WHERE player_id = ?",
                       (player_id,)).fetchone()["n"]
    return after - before


def record_battle(cx: sqlite3.Connection, mode: str, board: str,
                  pid_a: str, pid_b: str, seed: int,
                  snap_a: str, snap_b: str, season: str, now: int) -> int:
    with cx:
        cur = cx.execute(
            "INSERT INTO battles (mode, board, pid_a, pid_b, seed,"
            " snap_a, snap_b, season, played_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mode, board, pid_a, pid_b, seed, snap_a, snap_b, season, now))
    return int(cur.lastrowid)


def battles_of(cx: sqlite3.Connection, board: Optional[str] = None,
               pid: Optional[str] = None, limit: int = 40) -> List[Dict]:
    q = "SELECT * FROM battles WHERE 1=1"
    args: list = []
    if board:
        q += " AND board = ?"; args.append(board)
    if pid:
        q += " AND (pid_a = ? OR pid_b = ?)"; args += [pid, pid]
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in cx.execute(q, args)]


def fought_recently(cx: sqlite3.Connection, board: str, a: str, b: str,
                    now: int, window: int = 3600) -> bool:
    """同じ相手への連戦制限（§7.58: 1時間に1回まで。畑荒らし防止）。"""
    r = cx.execute(
        "SELECT 1 FROM battles WHERE board = ? AND mode = 'ranked'"
        " AND played_at > ? AND ((pid_a = ? AND pid_b = ?)"
        " OR (pid_a = ? AND pid_b = ?)) LIMIT 1",
        (board, now - window, a, b, b, a)).fetchone()
    return r is not None


def ledger_get(cx: sqlite3.Connection, key: str, default: str = "") -> str:
    r = cx.execute("SELECT value FROM ledger WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def ledger_set(cx: sqlite3.Connection, key: str, value: str) -> None:
    with cx:
        cx.execute("INSERT INTO ledger (key, value) VALUES (?, ?)"
                   " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                   (key, value))


def reset_ratings(cx: sqlite3.Connection) -> None:
    """月次リセット（§7.58）。**魚拓を焼いてから呼ぶこと。** games も消すので
    可変Kが月初を速く収束させる（全員K最大から数戦で実力帯へ）。"""
    with cx:
        cx.execute("DELETE FROM ratings")


def room_create(cx: sqlite3.Connection, creator: str, regulation: str,
                snap: str, now: int) -> str:
    import zlib
    for salt in range(1000):
        code = "{:06d}".format(
            zlib.crc32("{}/{}/{}".format(creator, now, salt).encode()) % 1000000)
        try:
            with cx:
                cx.execute("INSERT INTO rooms (code, creator, regulation,"
                           " snap, created_at) VALUES (?, ?, ?, ?, ?)",
                           (code, creator, regulation, snap, now))
            return code
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("ルーム番号を発行できない")


def room_get(cx: sqlite3.Connection, code: str) -> Optional[Dict]:
    r = cx.execute("SELECT * FROM rooms WHERE code = ?", (code,)).fetchone()
    return dict(r) if r else None


def room_close(cx: sqlite3.Connection, code: str, battle_id: int) -> None:
    with cx:
        cx.execute("UPDATE rooms SET battle_id = ? WHERE code = ?",
                   (battle_id, code))


def record_match(cx: sqlite3.Connection, board: str, rnd: int,
                 pid_a: str, pid_b: str, seed: int) -> None:
    with cx:
        cx.execute("INSERT INTO matches (board, round, pid_a, pid_b, seed)"
                   " VALUES (?, ?, ?, ?, ?)", (board, rnd, pid_a, pid_b, seed))


def matches_of(cx: sqlite3.Connection, board: str, limit: int = 40,
               pid: Optional[str] = None) -> List[Dict]:
    q = "SELECT * FROM matches WHERE board = ?"
    args: list = [board]
    if pid:
        q += " AND (pid_a = ? OR pid_b = ?)"
        args += [pid, pid]
    q += " ORDER BY round DESC, id DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in cx.execute(q, args)]


def _row(r: sqlite3.Row) -> Player:
    return Player(r["id"], r["display_name"], r["kind"], r["rating"],
                  r["prime_start"], r["prime_hours"], r["plan"])
