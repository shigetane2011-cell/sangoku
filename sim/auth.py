# -*- coding: utf-8 -*-
"""sim/auth.py -- 外部認証（OIDC）と署名つきセッション（§7.118）

**パスワードを自分で持たない**（players.py 冒頭の方針）。ここが持つのは

    1. 署名つきセッション … `sid` クッキー。`pid` を HMAC で封をして渡す。
       旧実装の「ベタの pid クッキー」は、pid を知られた瞬間になりすませた
       （しかも /api/state が全員の pid を配っていた）。署名があれば、
       サーバの秘密鍵を持たない者は sid を作れない。
    2. OIDC のコードフロー … 既定は Google。認可コード + PKCE(S256) で
       トークンを取り、**ID トークンの署名は検証しない** — 代わりに
       userinfo エンドポイントへ問い合わせる。TLS で発行者と直接話すので
       署名検証と同じ保証が、RSA 実装なし（標準ライブラリのみ）で得られる。

サーバは**状態を持たない**。ログイン途中の state と PKCE verifier は
署名つきクッキーで往復させる（DB にセッション表を作らない。1プロセス
前提でも、再起動でログイン中の全員が落ちる作りにはしない）。

環境変数（公開モードで必須のものは web.py が起動時に検査する）:

    SANGOKU_SECRET             セッション署名の秘密鍵（32バイト以上の乱文字列）
    SANGOKU_BASE_URL           外から見えるURL（例 https://sangoku.example.com）
    SANGOKU_OIDC_CLIENT_ID     Google Cloud で登録した OAuth クライアントID
    SANGOKU_OIDC_CLIENT_SECRET 同シークレット
    SANGOKU_OIDC_AUTH_URL      （試験用）認可エンドポイントの差し替え
    SANGOKU_OIDC_TOKEN_URL     （試験用）トークンエンドポイントの差し替え
    SANGOKU_OIDC_USERINFO_URL  （試験用）userinfo エンドポイントの差し替え
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

# Google の固定エンドポイント（https://accounts.google.com/.well-known/openid-configuration）。
# 起動のたびに discovery を引かない（外へ出られない環境でも盤面は動くべき）。
# 試験ではモックIdPへ差し替える。
AUTH_URL = os.environ.get(
    "SANGOKU_OIDC_AUTH_URL", "https://accounts.google.com/o/oauth2/v2/auth")
TOKEN_URL = os.environ.get(
    "SANGOKU_OIDC_TOKEN_URL", "https://oauth2.googleapis.com/token")
USERINFO_URL = os.environ.get(
    "SANGOKU_OIDC_USERINFO_URL",
    "https://openidconnect.googleapis.com/v1/userinfo")
PROVIDER = os.environ.get("SANGOKU_OIDC_PROVIDER", "google")

CLIENT_ID = os.environ.get("SANGOKU_OIDC_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("SANGOKU_OIDC_CLIENT_SECRET", "")
BASE_URL = os.environ.get("SANGOKU_BASE_URL", "").rstrip("/")

# 秘密鍵。公開モードでは必須（web.py が検査）。手元では毎回生成でよい —
# 再起動でログインし直しになるだけで、手元の名乗りログインは残っている。
SECRET = os.environ.get("SANGOKU_SECRET", "") or secrets.token_urlsafe(32)
_KEY = hashlib.sha256(SECRET.encode()).digest()

SESSION_DAYS = 30          # セッションの寿命
LOGIN_WINDOW = 600         # ログイン往復（state クッキー）の寿命・秒


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes) -> str:
    return _b64(hmac.new(_KEY, payload, hashlib.sha256).digest())


def seal(obj: dict, ttl: int) -> str:
    """辞書へ期限を付けて封をする。値はクッキーに入る前提で URL 安全。"""
    body = dict(obj)
    body["exp"] = int(time.time()) + ttl
    payload = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    return _b64(payload) + "." + _sign(payload)


def unseal(token: str) -> dict | None:
    """封を検める。署名が違う・期限切れ・形が変、はすべて None。"""
    try:
        body_b64, sig = token.split(".", 1)
        payload = _unb64(body_b64)
        if not hmac.compare_digest(_sign(payload), sig):
            return None
        body = json.loads(payload)
        if int(body.get("exp", 0)) < time.time():
            return None
        return body
    except (ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- セッション
def session_cookie(pid: str, *, secure: bool) -> str:
    tok = seal({"pid": pid}, SESSION_DAYS * 86400)
    return ("sid={}; Path=/; Max-Age={}; HttpOnly; SameSite=Lax{}"
            .format(tok, SESSION_DAYS * 86400, "; Secure" if secure else ""))


def clear_cookie(*, secure: bool) -> str:
    return ("sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{}"
            .format("; Secure" if secure else ""))


def session_pid(cookie_header: str) -> str | None:
    """Cookie ヘッダから検証済みの pid を取り出す。無効なら None。"""
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "sid":
            body = unseal(v)
            if body and body.get("pid"):
                return str(body["pid"])
    return None


# ---------------------------------------------------------------- OIDC
def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and BASE_URL)


def redirect_uri() -> str:
    return BASE_URL + "/auth/callback"


def begin_login() -> tuple[str, str]:
    """(IdP へ飛ばすURL, 往復用の state クッキー) を返す。

    state はCSRF対策（コールバックが自分の始めたログインかを確かめる）、
    PKCE verifier はコード横取り対策。どちらも署名つきクッキーで持ち帰る。
    """
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    q = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    cookie = ("oidc={}; Path=/auth; Max-Age={}; HttpOnly; SameSite=Lax"
              .format(seal({"st": state, "vf": verifier}, LOGIN_WINDOW),
                      LOGIN_WINDOW))
    return AUTH_URL + "?" + q, cookie


def finish_login(query: dict, cookie_header: str) -> dict:
    """コールバックを検めて {sub, email, name} を返す。失敗は ValueError。"""
    blob = None
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "oidc":
            blob = unseal(v)
    if not blob:
        raise ValueError("ログインの往復が古いか、始めていない")
    if not query.get("state") or query.get("state") != blob.get("st"):
        raise ValueError("state が一致しない")
    code = query.get("code", "")
    if not code:
        raise ValueError("認可コードが無い")
    tok = _post_json(TOKEN_URL, {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code_verifier": blob["vf"],
    })
    access = tok.get("access_token", "")
    if not access:
        raise ValueError("トークン交換に失敗")
    req = urllib.request.Request(
        USERINFO_URL, headers={"Authorization": "Bearer " + access})
    with urllib.request.urlopen(req, timeout=10) as r:
        info = json.loads(r.read().decode())
    sub = str(info.get("sub", ""))
    if not sub:
        raise ValueError("userinfo に sub が無い")
    return {"sub": sub, "email": str(info.get("email", "")),
            "name": str(info.get("name", "")).strip()}


def _post_json(url: str, form: dict) -> dict:
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


# ---------------------------------------------------------------- 律速
class RateLimit:
    """IPごとの単純な窓。認証の口だけに使う（盤面のAPIには掛けない）。

    メモリだけで持つ（1プロセス前提・§7.118）。窓は素朴な固定窓で足りる —
    ここで守りたいのは総当たりと登録の乱造で、精密な流量制御ではない。
    """

    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self._hits: dict[str, list[int]] = {}

    def allow(self, key: str) -> bool:
        now = int(time.time())
        hits = [t for t in self._hits.get(key, ()) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 4096:      # 念のための上限（古い鍵を捨てる）
            self._hits = {k: v for k, v in self._hits.items()
                          if v and now - v[-1] < self.window}
        return True
