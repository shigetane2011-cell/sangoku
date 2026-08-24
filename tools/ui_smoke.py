# -*- coding: utf-8 -*-
"""画面の煙検査（§7.91）: 編成盤面を**実ブラウザで触って**確かめる。

    python3 tools/ui_smoke.py             # 使い捨てのDBで起動して触る
    python3 tools/ui_smoke.py --keep      # 走らせたDBを消さない（後で覗く用）

なぜこれが要るか。編成盤面（FormationBoard）の初版は、node の検査も
python の検算も全部緑のまま、**満枠の編成が一切編集できない**状態で
上がってきた。原因は捕捉フェーズの pointerdown が一覧の札を押した瞬間に
選択を消していたことで、これは構文検査でもソース文字列の照合でも捕まらない。
§7.90 で書いたとおり「構文が通ること・CLIが動くことは、画面が描けること・
触れることの証拠にならない」。だから画面に触る変更は、ここを通してから出す。

Playwright が無ければ**何も測らずに 0 で抜ける**（環境の都合でCIを赤に
しないため）。入れるときは:  pip install playwright && playwright install chromium
"""
import os, sys, time, json, shutil, tempfile, subprocess, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("SANGOKU_SMOKE_PORT", "8971"))
BASE = "http://127.0.0.1:{}".format(PORT)


def _wait_up(timeout=90.0):
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
    """この環境の chromium を探す（playwright install 済みなら不要）。"""
    for cand in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",):
        if os.path.exists(cand):
            return pw.chromium.launch(executable_path=cand)
    return pw.chromium.launch()


NAMES = """() => [...document.querySelectorAll('#slots .fb-piece')]
  .map((x, i) => {
    const cap = x.parentElement.querySelector('.fb-name');
    return x.classList.contains('occupied') ? cap.textContent : null;
  })"""


def run():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright が無いので画面の煙検査は飛ばす"
              "（pip install playwright && playwright install chromium）")
        return 0

    datadir = tempfile.mkdtemp(prefix="sangoku-smoke-")
    srv = _boot(datadir)
    fails = []
    try:
        if not _wait_up():
            print("×  サーバが上がらない"); return 1
        with sync_playwright() as pw:
            br = _chromium(pw)
            ctx = br.new_context(viewport={"width": 1280, "height": 1000})
            page = ctx.new_page()
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.goto(BASE + "/", wait_until="networkidle")

            # 名乗る（鍵の無い手元用の口。名前だけで入れる）
            page.fill("#newname", "煙検査")
            page.click("#make")
            page.wait_for_timeout(1500)
            page.goto(BASE + "/deck", wait_until="networkidle")
            page.wait_for_selector("#slots .fb-piece", timeout=60000)

            def board():
                return page.evaluate(NAMES)

            def check(cond, msg):
                print(("○  " if cond else "×  ") + msg)
                if not cond:
                    fails.append(msg)

            check(len(board()) == 6, "盤面に6枠ある")

            # ── 一覧の札で枠を埋められる ──
            card = page.query_selector("#roster .card:not([disabled])")
            first = card.get_attribute("data-n")
            card.click(); page.wait_for_timeout(300)
            check(first in board(), "一覧の札を押すと空き枠に入る（{}）".format(first))

            # ── 駒どうしの入れ替え（タップ2回）──
            for _ in range(5):
                c = page.query_selector("#roster .card:not([disabled])")
                if not c:
                    break
                c.click(); page.wait_for_timeout(200)
            before = board()
            filled = [i for i, n in enumerate(before) if n]
            if len(filled) >= 2:
                a, b = filled[0], filled[-1]
                page.query_selector_all("#slots .fb-piece")[a].click()
                page.wait_for_timeout(200)
                page.query_selector_all("#slots .fb-piece")[b].click()
                page.wait_for_timeout(400)
                after = board()
                check(after[a] == before[b] and after[b] == before[a],
                      "駒をタップ2回で入れ替えられる")

            # ── 交代（駒を選ぶ→一覧の札）。初版が落ちていた道 ──
            before = board()
            spot = [i for i, n in enumerate(before) if n][0]
            page.query_selector_all("#slots .fb-piece")[spot].click()
            page.wait_for_timeout(250)
            cand = None
            for c in page.query_selector_all("#roster .card:not([disabled])"):
                if c.get_attribute("data-n") not in before:
                    cand = c; break
            if cand is None:
                check(False, "交代に使える札が一覧に無い（検査を組み直すこと）")
            else:
                name = cand.get_attribute("data-n")
                cand.click(); page.wait_for_timeout(400)
                check(board()[spot] == name,
                      "駒を選んで一覧の札を押すと交代する（{} → {}）".format(before[spot], name))

            # ── 枠から外せる ──
            before = board()
            spot = [i for i, n in enumerate(before) if n][0]
            rm = page.query_selector_all(".fb-remove")
            if not rm:
                check(False, "✕ で枠から外せる（外す手段が盤面に無い）")
            else:
                rm[0].click(); page.wait_for_timeout(400)
                check(board()[spot] is None, "✕ で枠から外せる")

            # ── 名前が読める ──
            # textContent では捕まらない。CSS の ellipsis は文字を消さないので、
            # DOM 上は「張宝〔地公将軍〕」のままでも画面には「張…」しか出ない。
            # 箱からの**溢れ**（縦横）で測る。
            caps = page.eval_on_selector_all(
                "#slots .fb-slot",
                "es => es.map(x => { const c = x.querySelector('.fb-name');"
                " if (!c || !c.textContent.trim()) return null;"
                " return [c.textContent.trim(),"
                "  c.scrollWidth <= c.clientWidth + 1 && c.scrollHeight <= c.clientHeight + 2]; })")
            shown = [c for c in caps if c and c[0] and c[0] != "空き枠"]
            check(bool(shown) and all(ok for _, ok in shown),
                  "駒の名前が箱に収まって読める（{}）".format(
                      "／".join(t for t, ok in shown[:3] if ok) or
                      "溢れている: " + "／".join(t for t, ok in shown[:3] if not ok)))

            # ── 陣形を変えても武将が消えない・順序が変わらない ──
            before = board()
            page.click('#formtabs button:has-text("鶴翼")'); page.wait_for_timeout(400)
            check(board() == before, "陣形を変えても6枠の中身と並びは動かない")

            check(not errs, "画面の例外なし{}".format("：" + "／".join(errs) if errs else ""))
            br.close()
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

    print("—" * 40)
    if fails:
        print("落ちた検査 {} 件".format(len(fails)))
        return 1
    print("画面の煙検査: 通った")
    return 0


if __name__ == "__main__":
    sys.exit(run())
