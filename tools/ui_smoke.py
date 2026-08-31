# -*- coding: utf-8 -*-
"""画面の煙検査（§7.91・§7.92）: 編成盤面を**実ブラウザで触って**確かめる。

    python3 tools/ui_smoke.py                # 1280 と 390 の両方で触る
    python3 tools/ui_smoke.py --keep         # 走らせたDBを消さない
    python3 tools/ui_smoke.py --allow-skip   # Playwright が無ければ素通り

なぜこれが要るか。編成盤面の初版は、node の検査も python の検算も全部緑の
まま、**満枠の編成が一切編集できない**状態で上がってきた。原因は捕捉フェーズの
pointerdown が一覧の札を押した瞬間に選択を消していたことで、これは構文検査でも
ソース文字列の照合でも捕まらない。§7.90 で書いたとおり「構文が通ることは
画面が描けること・触れることの証拠にならない」。画面に触る変更はここを通す。

**Playwright が無いときは既定で落ちる（終了コード1）。** 素通りして 0 を返すと、
「CIが緑」が「画面を検査した」の証拠にならない——検査していないことと、検査して
問題が無かったことは別物である。環境の都合で飛ばすなら --allow-skip を明示する。
入れ方:  pip install playwright && playwright install chromium
"""
import os, sys, time, shutil, tempfile, subprocess, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("SANGOKU_SMOKE_PORT", "8971"))
BASE = "http://127.0.0.1:{}".format(PORT)

# 盤面の中身を読む。名前は駒ではなく**枠**の下にあるので .fb-slot から引く。
NAMES = """() => [...document.querySelectorAll('#slots .fb-slot')].map((sl) => {
  const p = sl.querySelector('.fb-piece');
  const c = sl.querySelector('.fb-name');
  return p && p.classList.contains('occupied') ? c.textContent.trim() : null;
})"""


class Report:
    def __init__(self):
        self.fails = []

    def check(self, cond, msg):
        print(("○  " if cond else "×  ") + msg)
        if not cond:
            self.fails.append(msg)
        return cond


def _wait_up(timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(BASE + "/", timeout=3).read(1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _boot(datadir):
    env = dict(os.environ)
    env["SANGOKU_PORT"] = str(PORT)
    env["SANGOKU_DB"] = os.path.join(datadir, "players.db")
    env.pop("SANGOKU_HOST", None)
    return subprocess.Popen([sys.executable, "-m", "sim.web"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def _chromium(pw):
    for cand in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",):
        if os.path.exists(cand):
            return pw.chromium.launch(executable_path=cand)
    return pw.chromium.launch()


def _login(page, name):
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#newname", timeout=120000)
    page.fill("#newname", name)
    page.click("#make")
    page.wait_for_timeout(1500)


def _open_deck(page):
    page.goto(BASE + "/deck", wait_until="domcontentloaded")
    page.wait_for_selector("#slots .fb-piece", timeout=120000)
    page.wait_for_timeout(400)


def _fill_board(page):
    """一覧の札で6枠を埋める。配置は**素早く2回**（§7.119。1回は詳細だけ）。

    .indeck（既に盤面に居る札）を掴むと2回押しが「外す」になるので除く。"""
    for _ in range(8):
        if all(page.evaluate(NAMES)):
            break
        c = page.query_selector("#roster .card:not([disabled]):not(.indeck)")
        if not c:
            break
        c.dblclick()
        page.wait_for_timeout(180)


# ── 触りかたの検査（幅ごとに同じことをする）─────────────────
def exercise(page, rep, label, touch):
    def board():
        return page.evaluate(NAMES)

    def tag(msg):
        return "[{}] {}".format(label, msg)

    _open_deck(page)
    _fill_board(page)
    rep.check(len(board()) == 6, tag("盤面に6枠ある"))
    rep.check(all(board()), tag("一覧の札で6枠が埋まる"))

    # 駒どうしの入れ替え（タップ2回）——指でもマウスでも常に効く正路
    before = board()
    page.query_selector_all("#slots .fb-piece")[0].click(); page.wait_for_timeout(200)
    page.query_selector_all("#slots .fb-piece")[5].click(); page.wait_for_timeout(400)
    after = board()
    rep.check(after[0] == before[5] and after[5] == before[0],
              tag("駒をタップ2回で入れ替えられる（前衛↔後衛）"))

    # 交代（駒を選ぶ→一覧の札）。初版が落ちていた道
    before = board()
    page.query_selector_all("#slots .fb-piece")[1].click(); page.wait_for_timeout(250)
    cand = None
    for c in page.query_selector_all("#roster .card:not([disabled]):not(.indeck)"):
        if c.get_attribute("data-n") not in before:
            cand = c
            break
    if not rep.check(cand is not None, tag("交代に使える札が一覧にある")):
        return
    name = cand.get_attribute("data-n")
    cand.click(); page.wait_for_timeout(400)
    rep.check(board()[1] == name, tag("駒を選んで一覧の札を押すと交代する"))

    # 選択の帯（指で押せる大きさの「外す」）
    before = board()
    page.query_selector_all("#slots .fb-piece")[2].click(); page.wait_for_timeout(250)
    bar = page.query_selector(".fb-bar-btn[data-bar='remove']")
    if rep.check(bar is not None, tag("選ぶと操作の帯が出る")):
        box = bar.bounding_box()
        rep.check(box["height"] >= 44,
                  tag("「枠から外す」が触りの目安44を満たす（{:.0f}px）".format(box["height"])))
        bar.click(); page.wait_for_timeout(400)
        rep.check(board()[2] is None, tag("帯の「枠から外す」で外れる"))
        _fill_board(page)

    # 隅の ✕
    before = board()
    rm = page.query_selector_all(".fb-remove")
    if rep.check(bool(rm), tag("✕ が盤面にある")):
        rm[0].click(); page.wait_for_timeout(400)
        rep.check(board()[0] is None, tag("✕ で枠から外せる"))
        _fill_board(page)

    # シングル＝詳細／素早く2回＝配置・解除（§7.119）
    rm = page.query_selector_all(".fb-remove")
    if rm:
        rm[0].click(); page.wait_for_timeout(300)          # 1枠空けて試す
    stay = board()
    cand = None
    for c in page.query_selector_all("#roster .card:not([disabled]):not(.indeck)"):
        if c.get_attribute("data-n") not in stay:
            cand = c
            break
    if rep.check(cand is not None, tag("単押しの検査に使える札がある")):
        nm = cand.get_attribute("data-n")
        cand.click(); page.wait_for_timeout(500)           # 2回目の窓が閉じるまで待つ
        rep.check(board() == stay, tag("一覧の札は1回では配置されない"))
        info = page.text_content("#cardinfo") or ""
        rep.check(nm in info, tag("1回押すと詳細欄がその武将になる"))
        cand.dblclick(); page.wait_for_timeout(400)
        rep.check(nm in board(), tag("素早く2回で配置される"))
        indeck = page.query_selector('#roster .card.indeck[data-n="{}"]'.format(nm))
        if rep.check(indeck is not None, tag("編成中の札が一覧で押せる")):
            indeck.click(); page.wait_for_timeout(500)
            rep.check(nm in board(), tag("編成中の札は1回では外れない"))
            indeck.dblclick(); page.wait_for_timeout(400)
            rep.check(nm not in board(), tag("編成中の札は素早く2回で外れる"))
        _fill_board(page)

    # 駒に触れると詳細欄がその武将になる（§7.119）
    first = board()[0]
    if rep.check(bool(first), tag("詳細の検査に使える駒がある")):
        page.query_selector_all("#slots .fb-piece")[0].click(); page.wait_for_timeout(300)
        info = page.text_content("#cardinfo") or ""
        rep.check(first in info, tag("駒に触れると詳細欄がその武将になる"))
        bar = page.query_selector(".fb-bar-btn[data-bar='cancel']")
        if bar:
            bar.click(); page.wait_for_timeout(200)        # 選択を残さない

    # キーボードだけで入れ替え（Enter で選ぶ→矢印で移す→Enter で確定）
    before = board()
    page.query_selector_all("#slots .fb-piece")[0].focus()
    page.keyboard.press("Enter"); page.wait_for_timeout(200)
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(200)
    page.keyboard.press("Enter"); page.wait_for_timeout(400)
    after = board()
    rep.check(after[0] == before[1] and after[1] == before[0],
              tag("キーボードだけで入れ替えられる"))

    # キーボードで外す（Delete）
    before = board()
    page.query_selector_all("#slots .fb-piece")[3].focus()
    page.keyboard.press("Delete"); page.wait_for_timeout(400)
    rep.check(board()[3] is None, tag("Delete で枠から外せる"))
    _fill_board(page)

    # 名前が読める。textContent では測れない（ellipsis は文字を消さない）ので
    # **箱からの溢れ**で見る
    caps = page.eval_on_selector_all(
        "#slots .fb-slot",
        "es => es.map(x => { const c = x.querySelector('.fb-name');"
        " if (!c || !c.textContent.trim()) return null;"
        " return [c.textContent.trim(),"
        "  c.scrollWidth <= c.clientWidth + 1 && c.scrollHeight <= c.clientHeight + 2]; })")
    shown = [c for c in caps if c and c[0] and c[0] != "空き枠"]
    rep.check(bool(shown) and all(ok for _, ok in shown),
              tag("駒の名前が箱に収まって読める（{}）".format(
                  "／".join(t for t, ok in shown[:2] if ok)
                  or "溢れ: " + "／".join(t for t, _ in shown[:2]))))

    # 駒の当たり（触りの目安44）
    box = page.query_selector("#slots .fb-piece").bounding_box()
    rep.check(min(box["width"], box["height"]) >= 44,
              tag("駒が触りの目安44を満たす（{:.0f}×{:.0f}）".format(box["width"], box["height"])))

    # 陣形を変えても中身と並びが動かない
    before = board()
    page.click('#formtabs button:has-text("鶴翼")'); page.wait_for_timeout(400)
    rep.check(board() == before, tag("陣形を変えても6枠の中身と並びは動かない"))

    # 横に溢れない
    rep.check(not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"),
        tag("横に溢れない"))

    if touch:
        _touch_checks(page, rep, tag, board)
    else:
        _drag_checks(page, rep, tag, board)


def _drag_checks(page, rep, tag, board):
    """マウスの実ドラッグ・盤外へ落とす・途中で切れる。"""
    before = board()
    ps = page.query_selector_all("#slots .fb-piece")
    r0, r4 = ps[0].bounding_box(), ps[4].bounding_box()
    page.mouse.move(r0["x"] + r0["width"] / 2, r0["y"] + r0["height"] / 2)
    page.mouse.down()
    page.mouse.move(r0["x"] + 40, r0["y"] + 12, steps=4)
    page.mouse.move(r4["x"] + r4["width"] / 2, r4["y"] + r4["height"] / 2, steps=10)
    page.mouse.up(); page.wait_for_timeout(450)
    after = board()
    rep.check(after[0] == before[4] and after[4] == before[0],
              tag("マウスのドラッグで入れ替わる"))
    rep.check(page.eval_on_selector_all(".fb-drag-proxy", "e => e.length") == 0,
              tag("ドラッグの影が残らない"))

    # 盤の外へ落とす → 何も起きない
    before = board()
    ps = page.query_selector_all("#slots .fb-piece")
    r0 = ps[0].bounding_box()
    page.mouse.move(r0["x"] + r0["width"] / 2, r0["y"] + r0["height"] / 2)
    page.mouse.down()
    page.mouse.move(r0["x"] + 40, r0["y"] + 10, steps=4)
    page.mouse.move(10, 10, steps=10)
    page.mouse.up(); page.wait_for_timeout(500)
    rep.check(board() == before, tag("盤の外へ落としても並びは変わらない"))
    rep.check(page.eval_on_selector_all(".fb-drag-proxy", "e => e.length") == 0,
              tag("盤外で離しても影が残らない"))

    # 途中で pointercancel（電話が鳴った等）→ 元に戻る
    before = board()
    ps = page.query_selector_all("#slots .fb-piece")
    r0 = ps[0].bounding_box()
    page.mouse.move(r0["x"] + r0["width"] / 2, r0["y"] + r0["height"] / 2)
    page.mouse.down()
    page.mouse.move(r0["x"] + 45, r0["y"] + 10, steps=4)
    page.evaluate("""() => {
      const ev = new PointerEvent('pointercancel', {pointerId: 1, bubbles: true});
      document.dispatchEvent(ev);
    }""")
    page.mouse.up(); page.wait_for_timeout(500)
    rep.check(board() == before, tag("途中で切れても並びは変わらない"))


def _touch_checks(page, rep, tag, board):
    """指のとき、駒の上から始めた**縦スクロールを盤面が奪わない**。"""
    page.query_selector("#slots").scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    before = board()
    top0 = page.evaluate("() => window.scrollY")
    box = page.query_selector("#slots .fb-piece").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.touchscreen.tap(1, 1)          # 選択を解いておく
    page.wait_for_timeout(200)
    # **振っている最中**を見る。離した後では、掴んでいても影は片付いていて
    # 並びも戻るので、始まったかどうかが分からない（実際それで見逃した）。
    page.evaluate("""([x, y]) => {
      const el = document.elementFromPoint(x, y);
      const mk = (type, cy) => new PointerEvent(type, {pointerId: 7, pointerType: 'touch',
        isPrimary: true, bubbles: true, cancelable: true, clientX: x, clientY: cy});
      el.dispatchEvent(mk('pointerdown', y));
      for (let d = 6; d <= 60; d += 6) document.dispatchEvent(mk('pointermove', y - d));
    }""", [cx, cy])
    page.wait_for_timeout(150)
    grabbed = (page.eval_on_selector_all(".fb-drag-proxy", "e => e.length")
               + page.eval_on_selector_all("#slots .drag-source", "e => e.length"))
    page.evaluate("""([x, y]) => {
      document.dispatchEvent(new PointerEvent('pointerup', {pointerId: 7,
        pointerType: 'touch', isPrimary: true, bubbles: true, cancelable: true,
        clientX: x, clientY: y - 60}));
    }""", [cx, cy])
    page.wait_for_timeout(400)
    rep.check(grabbed == 0, tag("指で縦に振ってもドラッグが始まらない"))
    rep.check(board() == before, tag("縦振りで並びが変わらない"))
    # 横に振ればちゃんと掴める
    before = board()
    ps = page.query_selector_all("#slots .fb-piece")
    b0, b1 = ps[0].bounding_box(), ps[1].bounding_box()
    page.evaluate("""([x0, y0, x1, y1]) => {
      const el = document.elementFromPoint(x0, y0);
      const mk = (type, cx, cy) => new PointerEvent(type, {pointerId: 8, pointerType: 'touch',
        isPrimary: true, bubbles: true, cancelable: true, clientX: cx, clientY: cy});
      el.dispatchEvent(mk('pointerdown', x0, y0));
      const n = 8;
      for (let i = 1; i <= n; i++)
        document.dispatchEvent(mk('pointermove', x0 + (x1 - x0) * i / n, y0));
      document.dispatchEvent(mk('pointerup', x1, y1));
    }""", [b0["x"] + b0["width"] / 2, b0["y"] + b0["height"] / 2,
           b1["x"] + b1["width"] / 2, b1["y"] + b1["height"] / 2])
    page.wait_for_timeout(500)
    after = board()
    rep.check(after[0] == before[1] and after[1] == before[0],
              tag("指で横に振れば掴んで入れ替えられる"))


# ── 読み取り専用の盤面（戦記の敵陣・リプレイ）─────────────
def onboard_check(page, rep):
    """初回の導入（§7.121）: 新規プレイヤーはホームでこの1枚だけを見る。"""
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    ob = page.query_selector(".onboard")
    if not rep.check(ob is not None, "新規プレイヤーに初回の導入が出る"):
        return
    txt = ob.text_content() or ""
    rep.check("まずは戦記の初戦へ" in txt and "負けながら自分の布陣を作る" in txt,
              "導入の文言が出ている")
    page.click("#ob-guide"); page.wait_for_timeout(300)
    gd = page.query_selector("#guide")
    rep.check(gd is not None and not gd.get_attribute("hidden"),
              "導入から軍略の手引きが開く")
    page.click("#guide-close"); page.wait_for_timeout(200)
    rep.check(page.query_selector(".onboard") is not None,
              "手引きを閉じても導入は残る")
    page.click("#ob-go")
    try:
        page.wait_for_selector("#foe-board .fb-piece", timeout=60000)
        rep.check(True, "「初陣へ」で初戦の戦前の間に着く")
    except Exception:
        rep.check(False, "「初陣へ」で初戦の戦前の間に着く")
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    rep.check(page.query_selector(".onboard") is None,
              "一度出発したら導入は出ない")


def council_check(page, rep, label):
    """軍議演習の入口は、敵魚拓がまだ無い新規でも崩れず表示される。"""
    page.goto(BASE + "/council", wait_until="domcontentloaded")
    page.wait_for_selector(".council-hero", timeout=60000)
    rep.check((page.text_content(".council-hero h2") or "").strip() == "軍議演習",
              "[{}] 軍議演習の題字が出る".format(label))
    rep.check(page.eval_on_selector_all(".enshu-pips i", "e => e.length") == 10,
              "[{}] 演習令が10枠で表示される".format(label))
    rep.check(page.query_selector("#enshu-max") is not None,
              "[{}] 無料MAXボタンがある".format(label))
    rep.check(page.query_selector('nav a[href="/council"]') is not None,
              "[{}] 共通ナビから軍議演習へ入れる".format(label))
    rep.check(not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"),
        "[{}] 軍議演習が横に溢れない".format(label))


def truce_check(page, rep, label):
    """天下の休戦令が卓上/携帯とも24枠・8枚で触れる。"""
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_timeout(600)
    if page.query_selector(".onboard") is not None:
        page.evaluate("() => fetch('/api/seen', {method:'POST',"
                      "headers:{'Content-Type':'application/json'},"
                      "body:JSON.stringify({key:'onboard'})})")
        page.wait_for_timeout(300)
        page.reload(wait_until="domcontentloaded")
    # 休戦令は畳んだ <details> の中にある（ホームを散らかさないため）。
    # **開いてから待つ** — 開かずに待つと「在るのに見えない」で60秒待って
    # 落ちる（実ブラウザで踏んだ。提供元は Playwright 無しで気づけなかった）。
    page.wait_for_selector("#truce-box", state="attached", timeout=60000)
    page.evaluate("() => { const d = document.querySelector('#truce-box');"
                  " if (d) d.open = true; }")
    page.wait_for_selector("#truce-editor", timeout=60000)
    rep.check(page.eval_on_selector_all(".truce-hour", "e => e.length") == 24,
              "[{}] 休戦令に24時刻が出る".format(label))
    rep.check(page.eval_on_selector_all(".truce-hour.on", "e => e.length") == 8,
              "[{}] 初期の休戦令が8枚選ばれている".format(label))
    sizes = page.eval_on_selector_all(
        ".truce-hour", "es => es.map(e => e.getBoundingClientRect().height)")
    rep.check(bool(sizes) and min(sizes) >= 44,
              "[{}] 時刻ボタンが触りの目安44を満たす（最小{:.0f}px）".format(
                  label, min(sizes) if sizes else 0))
    rep.check(page.eval_on_selector_all(
        '.truce-hour.on[aria-pressed="true"]', "e => e.length") == 8 and
        page.eval_on_selector_all(".truce-hour.on .truce-check",
                                  "es => es.every(e => e.textContent.includes('✓'))"),
        "[{}] 選択が色だけでなく✓とaria-pressedで分かる".format(label))
    rep.check("毎日8枚" in (page.text_content("#truce-box") or "") and
              "開催2時間前" in (page.text_content("#truce-box") or ""),
              "[{}] 枚数と締切が画面で読める".format(label))
    # 2日後は締切に掛からない。1枠を入れ替えて保存し、再描画後も同じ日・
    # 開いた設定欄・成功の言葉が残ることを触って確かめる。
    page.select_option("#truce-day", "2")
    page.wait_for_timeout(250)
    on_h = page.eval_on_selector(".truce-hour.on:not([disabled])", "e => e.dataset.hour")
    off_h = page.eval_on_selector(".truce-hour:not(.on):not([disabled])", "e => e.dataset.hour")
    page.click('.truce-hour[data-hour="{}"]'.format(on_h))
    page.click('.truce-hour[data-hour="{}"]'.format(off_h))
    page.click("#truce-save")
    page.wait_for_selector(".truce-notice.ok", timeout=60000)
    rep.check(page.eval_on_selector("#truce-box", "e => e.open"),
              "[{}] 保存後も休戦令の設定欄が開いている".format(label))
    rep.check("布告しました" in (page.text_content(".truce-notice.ok") or "") and
              page.input_value("#truce-day") == "2",
              "[{}] 保存成功と編集中の日付が残る".format(label))
    selected = page.eval_on_selector_all(
        ".truce-hour.on", "es => es.map(e => e.dataset.hour)")
    rep.check(len(selected) == 8 and off_h in selected and on_h not in selected,
              "[{}] 保存した8枠が再取得後も一致する".format(label))
    rep.check(not page.evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth"),
        "[{}] 休戦令が横に溢れない".format(label))


def readonly_checks(page, rep):
    page.goto(BASE + "/senki", wait_until="domcontentloaded")
    page.wait_for_timeout(1200)
    btn = page.query_selector(".senki-row button, .sk-row button, button:has-text('挑む')")
    if not rep.check(btn is not None, "戦記の戦前へ入れる"):
        return
    btn.click()
    page.wait_for_selector("#foe-board .fb-piece", timeout=60000)
    page.wait_for_timeout(600)
    rep.check(page.eval_on_selector("#foe-board", "e => e.className").find("readonly") >= 0,
              "敵陣の盤面は読み取り専用")
    rep.check(page.eval_on_selector_all("#foe-board .fb-piece[disabled]", "e => e.length") == 6,
              "敵陣の駒は押せない（disabled）")
    rep.check(page.eval_on_selector_all("#foe-board .fb-remove", "e => e.length") == 0,
              "敵陣に ✕ は出ない")
    before = page.eval_on_selector_all(
        "#foe-board .fb-name", "es => es.map(e => e.textContent)")
    page.query_selector_all("#foe-board .fb-piece")[0].click(force=True)
    page.query_selector_all("#foe-board .fb-piece")[3].click(force=True)
    page.wait_for_timeout(300)
    rep.check(page.eval_on_selector_all(
        "#foe-board .fb-name", "es => es.map(e => e.textContent)") == before,
        "敵陣は押しても動かない")
    # 敵札の中身（兵法・特性）が読める
    rep.check(page.eval_on_selector_all(".foe-detail .fc-skill", "e => e.length") == 6,
              "敵札の兵法が6枚ぶん読める")
    # 自軍・敵軍が同時に出ていて、線種で見分けられる（色だけに頼らない）
    rep.check(page.eval_on_selector_all(".army-zone.mine, .army-zone.foe", "e => e.length") >= 2,
              "自軍と敵軍の枠が同時に出る")
    styles = page.evaluate("""() => {
      const g = (s) => { const e = document.querySelector(s);
        return e ? getComputedStyle(e).borderStyle : null; };
      return [g('.army-zone.mine'), g('.army-zone.foe')];
    }""")
    rep.check(styles[0] != styles[1] and styles[1] == "dashed",
              "自軍は実線・敵軍は破線（{} / {}）".format(*styles))
    # 魏の枠色が指定どおり残っている
    gi = page.evaluate("""() => {
      const d = document.createElement('div');
      d.className = 'fb-piece gi'; document.body.appendChild(d);
      const c = getComputedStyle(d).borderTopColor; d.remove(); return c;
    }""")
    rep.check(gi.replace(" ", "") == "rgb(70,104,156)",
              "魏の枠色 #46689c が残っている（{}）".format(gi))


def rout_badges_check(page, rep, datadir):
    """真の壊滅（壊・ANNIHIL_UNIT=0.5%）だけが軍功帳のバッジに出て、苦戦
    （15%割れ）の時刻は詳報から外れているか（§7.49後記2・後記3）。

    経緯: 生きて崩れただけの隊が兵法を撃つとバグ報告になり（後記1）、崩
    （苦戦）と壊（真の壊滅）を別バッジにしたら今度は「15%割れの時刻に攻略的
    意味がないなら詳報に要るのか」と再指摘があり（後記2）、詳報からは壊だけ
    残して崩を外した（後記3）。苦戦の実況行（「大きく崩れながらも踏みとどまる」）
    自体は残っているので、そちらも消えていないか確認する。構文検査は
    テンプレートの描き分けを保証しない（§7.90）ので、ここは実ブラウザで
    DOMまで見る。
    """
    import json
    import sim.players as P, sim.match as M, sim.play as PL, sim.field as F
    me = page.evaluate("() => fetch('/api/state').then(r => r.json())").get("me")
    if not rep.check(me is not None, "崩・壊バッジ確認用のログイン済みプレイヤーが取れる"):
        return
    cx = P.connect(os.path.join(datadir, "players.db"))
    cards = {c.name: c for c in M._roster_cards()}
    # 陸抗側が圧倒し、敵側に「生きて崩れただけ」と「真に壊滅」の両方を
    # 確実に出すための組み合わせ（tools/test_restraint.py と同じ札）。
    strong = F.Army(tuple(cards[n] for n in (
        "陸抗〔羊陸之交〕", "賀斉〔山越討伐〕", "華雄〔汜水関〕",
        "郭淮〔雍涼〕", "周瑜〔赤壁〕", "荀彧〔王佐〕")), F.FORM_WIDE)
    weak = F.Army(tuple(cards[n] for n in (
        "夏侯淵〔神速〕", "趙雲〔長坂坡〕", "関平〔麒麟児〕",
        "韓当〔老弓〕", "馬謖〔幼常〕", "諸葛恪〔元遜〕")), F.FORM_WIDE)
    mid = P.record_battle(
        cx, "ranked", "赤壁", me["id"], me["id"], 1,
        json.dumps(PL.snap_army(strong), ensure_ascii=False),
        json.dumps(PL.snap_army(weak), ensure_ascii=False),
        "smoke", int(time.time()), "○")
    page.goto(BASE + "/replay?id={}".format(mid), wait_until="domcontentloaded")
    # 合戦詳録は既定で畳んだ <details> の中にある。開かないと中の表は
    # 存在しても不可視のまま（truce編集欄と同じ罠・§7.132の教訓）。
    page.wait_for_selector(".battle-detail", timeout=60000)
    page.evaluate("() => { document.querySelector('.battle-detail').open = true; }")
    page.wait_for_selector(".detail-table", timeout=60000)
    page.wait_for_timeout(500)
    wiped_n = page.eval_on_selector_all(".wiped", "es => es.length")
    rep.check(wiped_n > 0, "「壊」バッジ（真の壊滅・0.5%）がリプレイ画面に出る（{}件）".format(wiped_n))
    detail_txt = page.eval_on_selector(".detail-table", "e => e.textContent") or ""
    rep.check("崩" not in detail_txt,
              "詳報の軍功帳に「崩」（苦戦の時刻）は出ない（攻略的意味がないため）")
    report_txt = page.eval_on_selector("#report", "e => e.textContent") or ""
    rep.check("崩" not in report_txt,
              "戦果の一覧カードにも「崩」は出ない（詳報と同じ扱い）")
    log_txt = page.eval_on_selector("#log", "e => e.textContent") or ""
    rep.check("踏みとどまる" in log_txt,
              "苦戦の実況行は詳報から独立して残っている")


# ── リプレイの陣営札（同じ武将が両軍にいる場合）───────────
def detail_check(rep):
    """合戦詳録（§7.94）の帳簿が回っているか。数字は盤面の実測で釣り合いを見る。"""
    import sim.match as M, sim.play as PL
    cards = M._roster_cards()
    a, _ = PL.parse_deck(cards, "曹仁〔堅守〕、孫乾〔従事〕、糜竺〔子仲〕、"
                         "韓当〔老弓〕、樊建〔伝令〕、宗預〔使者〕", "鶴翼")
    b, _ = PL.parse_deck(cards, "周泰〔身代〕、丁奉〔雪中〕、黄蓋〔苦肉〕、"
                         "呂範〔子衡〕、荀攸〔謀主〕、王平〔無当〕", "雁行")
    d = PL.replay_data(a, b, 0.5, 777, True)
    rows = d["mine"] + d["foe"]
    rep.check(all(k in u for u in rows
                  for k in ("taken", "fires", "stun", "sup", "targets")),
              "詳録の列（被ダメ・発動・阻害・抑制・矛先）が全員ぶん出る")
    # 与えたものは受け取られている: 矛先の合計 ≈ 相手側の被ダメ合計
    # （同士討ちは矛先に入れず被ダメに入るので、被ダメ側が少し大きくてよい）
    given = sum(v for u in rows for _, v in u["targets"])
    taken = sum(u["taken"] for u in rows)
    rep.check(taken >= given > 0,
              "矛先の合計 {:.0f} ≦ 被ダメの合計 {:.0f}（帳尻が合う）".format(given, taken))
    rep.check(any(u["fires"] > 0 for u in rows), "兵法の発動回数が数えられている")


def replay_side_check(rep):
    import sim.match as M, sim.play as PL
    cards = M._roster_cards()
    dup = "曹仁〔堅守〕、孫乾〔従事〕、糜竺〔子仲〕、韓当〔老弓〕、樊建〔伝令〕、宗預〔使者〕"
    a, ea = PL.parse_deck(cards, dup, "鶴翼")
    b, eb = PL.parse_deck(cards, dup, "雁行")
    if not rep.check(not ea and not eb, "同名両軍の編成を組める"):
        return
    d = PL.replay_data(a, b, 0.5, 12345, True)
    marks = d.get("line_sides") or []
    if not rep.check(len(d["lines"]) == len(marks) and bool(marks),
                     "行と陣営札の本数が合う（{} / {}）".format(len(d["lines"]), len(marks))):
        return
    pairs = [(s, ln) for s, ln in zip(marks, d["lines"]) if "曹仁" in ln]
    mine = [ln for s, ln in pairs if s == "mine"]
    foe = [ln for s, ln in pairs if s == "foe"]
    rep.check(bool(mine) and bool(foe),
              "両軍の同名武将が自軍・敵軍に分かれる（自軍{}行・敵軍{}行）".format(
                  len(mine), len(foe)))
    ok = all(("先手" in ln) == (s == "mine")
             for s, ln in pairs if "先手" in ln or "後手" in ln)
    rep.check(ok, "陣営札が文中の軍名と食い違わない")
    # 同名の札は文中でも軍名で分かれている（§7.93。「曹仁〔堅守〕（蜀・先手軍）」）
    rep.check(all("曹仁〔堅守〕（" in ln for _, ln in pairs),
              "両軍にいる武将は名前のうしろに軍名が付く")


def run():
    allow_skip = "--allow-skip" in sys.argv
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if allow_skip:
            print("Playwright が無いので画面の煙検査は飛ばす（--allow-skip 指定）")
            return 0
        print("×  Playwright が無く、画面の煙検査を**していない**。")
        print("   pip install playwright && playwright install chromium")
        print("   環境の都合で飛ばすなら --allow-skip を明示すること"
              "（検査していないことと、検査して問題が無かったことは別物）。")
        return 1

    rep = Report()
    datadir = tempfile.mkdtemp(prefix="sangoku-smoke-")
    srv = _boot(datadir)
    try:
        if not _wait_up():
            print("×  サーバが上がらない")
            return 1
        with sync_playwright() as pw:
            br = _chromium(pw)
            for label, w, h, mob in (("卓上1280", 1280, 1000, False),
                                     ("携帯390", 390, 844, True)):
                ctx = br.new_context(viewport={"width": w, "height": h},
                                     is_mobile=mob, has_touch=mob,
                                     device_scale_factor=2 if mob else 1)
                page = ctx.new_page()
                errs = []
                page.on("pageerror", lambda e: errs.append(str(e)))
                _login(page, "煙検査" + label)
                if not mob:
                    onboard_check(page, rep)
                truce_check(page, rep, label)
                exercise(page, rep, label, mob)
                council_check(page, rep, label)
                if not mob:
                    readonly_checks(page, rep)
                    rout_badges_check(page, rep, datadir)
                rep.check(not errs, "[{}] 画面の例外なし{}".format(
                    label, "：" + "／".join(errs) if errs else ""))
                ctx.close()
            br.close()
        replay_side_check(rep)
        detail_check(rep)
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except Exception:
            srv.kill()
        if "--keep" in sys.argv:
            print("DB を残した: " + datadir)
        else:
            shutil.rmtree(datadir, ignore_errors=True)

    print("—" * 46)
    print("※ 実機（iOS Safari / Android Chrome）は**ここでは測れない**。"
          "指の検査は Chromium の触り模擬まで。")
    if rep.fails:
        print("落ちた検査 {} 件".format(len(rep.fails)))
        for f in rep.fails:
            print("   × " + f)
        return 1
    print("画面の煙検査: 通った")
    return 0


if __name__ == "__main__":
    sys.exit(run())
