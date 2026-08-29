# 公開手順 — 『三国布陣』を外へ出す（§7.118）

公開の関門（§7.42・handoff §0）は3つ。**この文書はその3つを順に潰す手順書**である。

1. **外部認証** — パスワードを自分で持たない → Google OIDC（実装済み・§7.118）
2. **マネージドDB** — 消えない・戻せる → SQLite + Litestream 常時複製（本書）
3. **裏口の撤去** — /api/dev_* と .dev-only → 公開モードで自動封鎖＋最終削除（本書末尾）

構成は**1プロセス**（`python3 -m sim.web`）+ 前段のTLS終端。どこのホストでも同じ形。

```
[ブラウザ] ──TLS── [Caddy / Fly proxy] ──HTTP── [sim.web :8035] ── players.db
                                                       └─ litestream ──> S3/R2/B2
```

## 1. 環境変数

| 変数 | 必須 | 中身 |
| --- | --- | --- |
| `SANGOKU_PUBLIC` | ✔ | `1` で公開モード。名乗りログイン停止・OIDCのみ・dev の口封鎖・Secure クッキー |
| `SANGOKU_SECRET` | ✔ | セッション署名鍵。`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` で作る。**漏れたら全セッションが偽造可能** — 環境変数でだけ渡し、リポジトリに書かない |
| `SANGOKU_BASE_URL` | ✔ | 外から見えるURL（例 `https://sangoku.example.com`）。OIDC の redirect_uri になる |
| `SANGOKU_OIDC_CLIENT_ID` | ✔ | Google Cloud の OAuth クライアントID（下記） |
| `SANGOKU_OIDC_CLIENT_SECRET` | ✔ | 同シークレット |
| `SANGOKU_DB` | ✔ | DBの置き場。**永続ボリューム上**にする（例 `/data/players.db`） |
| `SANGOKU_HOST` / `SANGOKU_PORT` | | 待ち受け。前段プロキシから見える所（例 `0.0.0.0` / `8035`） |

公開モードは欠けたまま起動しない（`main()` が列挙して落ちる）。

## 2. Google OIDC の登録（人手の作業・15分）

1. https://console.cloud.google.com/ → プロジェクト作成（例 `sangoku`）
2. 「APIとサービス → OAuth同意画面」 … 外部・アプリ名『三国布陣』・スコープは
   `openid email profile` だけ。**それ以上のスコープを足さない**
3. 「認証情報 → OAuthクライアントID → ウェブアプリケーション」
   - 承認済みリダイレクトURI: `https://<自分のドメイン>/auth/callback`
4. 出てきた ID とシークレットを `SANGOKU_OIDC_CLIENT_ID` / `_SECRET` へ

ローカルで通しを試すなら、リダイレクトURIに `http://localhost:8035/auth/callback`
を足し、`SANGOKU_BASE_URL=http://localhost:8035` で起動する（Google は
localhost の http を特例で許す）。

## 3. マネージドDB — SQLite + Litestream

DBはSQLiteのまま（1プロセス設計・§7.118 で決定）。「マネージド」の中身は
**消えない・戻せる**で、Litestream が WAL をオブジェクトストレージへ常時複製する。

```yaml
# /etc/litestream.yml
dbs:
  - path: /data/players.db
    replicas:
      - type: s3                      # S3 / Cloudflare R2 / Backblaze B2
        bucket: sangoku-db
        path: players
        endpoint: https://<R2等のエンドポイント>   # AWS S3 なら省略
        # 認証は LITESTREAM_ACCESS_KEY_ID / LITESTREAM_SECRET_ACCESS_KEY
```

- 起動順: `litestream replicate` をサーバと並走（下の systemd / Fly 例に含む）
- **復旧演習を公開前に1度やる**: `litestream restore -o /tmp/rest.db /data/players.db`
  → `SANGOKU_DB=/tmp/rest.db python3 -m sim.web` で中身を確かめる。
  やっていないバックアップは無いのと同じ。
- SQLite 側は既定で WAL を使う（players.connect）。`PRAGMA busy_timeout` 済み。

## 4. ホスティング（どちらでも可）

### 案A: VPS（さくら等）+ systemd + Caddy

```ini
# /etc/systemd/system/sangoku.service
[Unit]
Description=Sangoku web
After=network.target
[Service]
WorkingDirectory=/opt/sangoku
Environment=SANGOKU_PUBLIC=1 SANGOKU_HOST=127.0.0.1 SANGOKU_PORT=8035
Environment=SANGOKU_DB=/data/players.db
EnvironmentFile=/etc/sangoku.env          # SECRET と OIDC はここ（600・root）
ExecStart=/usr/bin/python3 -m sim.web
Restart=on-failure
[Install]
WantedBy=multi-user.target
```

```
# /etc/caddy/Caddyfile — TLSは全部Caddyがやる（Let's Encrypt自動）
sangoku.example.com {
    reverse_proxy 127.0.0.1:8035
}
```

litestream も同様に systemd 化（`litestream replicate -config /etc/litestream.yml`）。

### 案B: Fly.io

```toml
# fly.toml（骨子）
app = "sangoku"
[build]                     # Dockerfile: python:3.12-slim + litestream バイナリ
[env]
  SANGOKU_PUBLIC = "1"
  SANGOKU_HOST = "0.0.0.0"
  SANGOKU_DB = "/data/players.db"
[mounts]
  source = "sangoku_data"
  destination = "/data"
[http_service]
  internal_port = 8035
  force_https = true
```

秘密は `fly secrets set SANGOKU_SECRET=... SANGOKU_OIDC_CLIENT_ID=...`。
コンテナの起動は `litestream replicate -exec "python3 -m sim.web"`（親子で束ねる）。

## 5. 公開前の最終チェックリスト

- [ ] **裏口の物理削除**。公開モードで自動封鎖はされているが、公開コミットでは
      コードごと消す（handoff §0 の約束）:
      - `sim/web.py` の `/api/dev_senki` `/api/dev_onsho` `/api/dev_heifu`
        `/api/dev_enshu`
        `/api/dev_reset_record` `/api/dev_tenka` の各ブロックと `DEV_DOORS`
      - `sim/webui/app.js` の `dev-only` ボタン（軍議演習の無料MAXを含む）
      - `sim/webui/app.css` の `.dev-only` 規則
      - 消えたことの検査: `grep -rn "dev_\|dev-only" sim/web.py sim/webui/` が空
- [ ] `SANGOKU_SECRET` が48文字以上・リポジトリに無い（`git grep SANGOKU_SECRET` は
      ドキュメントだけ）
- [ ] 本人のDB（遊んだ記録）を公開サーバへ**持ち込まない** — 公開DBは空から
      （在野は初回起動で自動生成される）
- [ ] `litestream restore` の復旧演習を実施した
- [ ] `https://…/api/state` で `auth.mode == "oidc"` と `humans == []` を確認
- [ ] 名乗りログイン（POST /api/login）が 403 を返すことを確認
- [ ] `/api/dev_heifu` が 404 を返すことを確認
- [ ] `/api/dev_enshu` が 404 を返すことを確認
- [ ] 別ブラウザで Google ログイン → 戦記1戦 → リプレイ再生、の通し
- [ ] 利用規約・プライバシー表記（メールアドレスの保存先と削除手順 = identities
      1行の削除）を1枚置く

## 6. 受け入れ試験（自動）

モックIdPで OIDC の往復・封鎖・なりすまし耐性を通しで検める試験がある。
コードを触ったら回すこと。

```
python3 tools/test_auth.py     # 公開モード（18項目）
python3 tools/test_local.py    # 手元モードの互換（7項目）
```
