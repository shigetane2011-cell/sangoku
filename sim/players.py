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
月額。ガチャ無し。ゲーム内通貨を売らない。

**レート対象マッチの数は課金で変えない。** §3.1 は「在席時間が強さになる」のを
潰すために作られており、同じ理屈は金にも当てはまる。回数を金で増やせるなら
「編成研究を競う」が「課金額を競う」に置き換わる。売るのは**練習戦の回数**と
研究の道具（編成スロット、リプレイの保持期間）で、どれもレートに触らない。

`plan` は権利の名前だけを持ち、**何が付くかはコード側の表**で決める。DB に
効果を書くと、プラン改定のたびに既存行の書き換えが要る。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional

DATA = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DATA, "players.db")

HUMAN = "human"
DUMMY = "dummy"

# ダミーのメールは**予約ドメイン**にする（RFC 6761）。実在アドレスとぶつからず、
# 誤って送信処理へ流れても届きようがない。足場を撤去し忘れても事故にならない。
DUMMY_DOMAIN = "example.invalid"

RATING_START = 1500.0

# プランごとの権利。**レート対象マッチ数は全プラン同じ**（公平性の床）。
# 増やすのは練習戦と研究の道具だけ。
PLANS: Dict[str, Dict[str, int]] = {
    "free":    {"rated_per_day": 12, "practice_per_day": 6,  "entry_slots": 3},
    "monthly": {"rated_per_day": 12, "practice_per_day": 48, "entry_slots": 12},
}


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
    return cx


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


def _row(r: sqlite3.Row) -> Player:
    return Player(r["id"], r["display_name"], r["kind"], r["rating"],
                  r["prime_start"], r["prime_hours"], r["plan"])
