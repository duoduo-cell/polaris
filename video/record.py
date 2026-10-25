"""Record the synthetic demo (port 3010) scene by scene for the submission video."""
import shutil
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1])
ONLY = set(sys.argv[2:])  # optional scene names to (re)record
BASE = "http://127.0.0.1:3010"
SIZE = {"width": 1600, "height": 900}
TASK = ("在合成演示库找上海Agent日常实习，每周最多4天，最多列2岗。展开第一岗学历、论文、出勤和实习月数与我的情况，解释日期。"
        "再给同公司一道面经原题和一道JD自测题，各给答题要点，列出待核实项。")
REASON = "二面挂：可靠性评测讲得不够具体，没有量化重试成功率"


def api(page, method, path, body=None):
    return page.evaluate("""async ([method, path, body]) => {
        const r = await fetch('/api/' + path, {method, headers: {'Content-Type': 'application/json'},
                                               body: body ? JSON.stringify(body) : undefined});
        return [r.status, (await r.text()).slice(0, 200)];
    }""", [method, path, body])


def smooth_scroll(page, total, steps=40, pause=0.08):
    # Scroll whichever element actually scrolls (the chat column has its own scroller).
    for _ in range(steps):
        page.evaluate("""(dy) => {
            const els = [...document.querySelectorAll('*')]
                .filter(e => e.scrollHeight > e.clientHeight + 40 && getComputedStyle(e).overflowY !== 'visible');
            const el = els.sort((a, b) => b.clientHeight - a.clientHeight)[0] || document.scrollingElement;
            el.scrollBy(0, dy); window.scrollBy(0, dy);
        }""", total / steps)
        time.sleep(pause)


def scene(browser, name, fn):
    if ONLY and name not in ONLY:
        return
    tmp = OUT / f"_{name}"
    shutil.rmtree(tmp, ignore_errors=True)
    context = browser.new_context(viewport=SIZE, record_video_dir=str(tmp), record_video_size=SIZE)
    page = context.new_page()
    started = time.time()
    fn(page)
    page.wait_for_timeout(800)
    context.close()
    video = next(tmp.glob("*.webm"))
    target = OUT / f"{name}.webm"
    target.unlink(missing_ok=True)
    video.rename(target)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{name}: {time.time() - started:.1f}s recorded")


def prepare(browser):
    context = browser.new_context(viewport=SIZE)
    page = context.new_page()
    page.goto(BASE + "/watches", wait_until="networkidle")
    print("reset", api(page, "POST", "demo/reset"))
    for changes in ({"status": "applied"}, {"status": "interview", "stage": "二面"}, {"status": "rejected", "note": REASON}):
        body = {"request_id": str(uuid.uuid4()), "changes": changes}
        print("followup", changes["status"], api(page, "POST", "jobs/J-2/followup", body)[0])
    context.close()


def chat(page):
    page.goto(BASE + "/chat", wait_until="networkidle")
    page.get_by_text("新对话").first.click()
    page.wait_for_timeout(1500)
    box = page.locator("textarea").first
    box.click()
    box.type(TASK, delay=25)
    page.wait_for_timeout(600)
    box.press("Enter")
    page.wait_for_selector("text=/思考了 [0-9.]+ 秒/", timeout=240_000)
    page.wait_for_timeout(1500)
    # Back to the top of the answer, then read down through it.
    page.evaluate("document.querySelectorAll('*').forEach(el => { if (el.scrollHeight > el.clientHeight + 40) el.scrollTop = 0; })")
    page.wait_for_timeout(1200)
    smooth_scroll(page, 2600, steps=70, pause=0.12)
    page.wait_for_timeout(1200)


def watch(page):
    page.goto(BASE + "/watches", wait_until="networkidle")
    page.wait_for_timeout(1200)
    for _ in range(5):
        page.get_by_role("button", name="推进一步").click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1800)
    smooth_scroll(page, 500, steps=15, pause=0.08)
    page.wait_for_timeout(800)
    page.get_by_role("button", name="现在写报告").first.click()
    for _ in range(40):
        page.wait_for_timeout(2000)
        if page.get_by_text("写报告中").count() == 0 and page.get_by_text("每天 21:00 以后第一次检查时会写一份").count() == 0:
            break
    page.wait_for_timeout(1500)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(800)
    smooth_scroll(page, 1400, steps=50, pause=0.12)
    page.wait_for_timeout(1000)


def review(page):
    page.goto(BASE + "/review", wait_until="networkidle")
    page.wait_for_timeout(1000)
    page.get_by_placeholder("城市").type("上海", delay=80)
    page.locator("input[placeholder^='方向']").type("Agent", delay=80)
    page.get_by_placeholder("每周最多几天").type("4", delay=80)
    if page.get_by_label("只看大厂").is_checked():
        page.get_by_label("只看大厂").uncheck()
    page.get_by_role("button", name="看看").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)
    smooth_scroll(page, 900, steps=35, pause=0.12)
    page.wait_for_timeout(1000)


def apps(page):
    page.goto(BASE + "/applications", wait_until="networkidle")
    page.wait_for_timeout(2500)
    smooth_scroll(page, 450, steps=20, pause=0.1)
    page.wait_for_timeout(2000)
    page.goto(BASE + "/traces", wait_until="networkidle")
    page.wait_for_timeout(3000)
    smooth_scroll(page, 400, steps=20, pause=0.1)
    page.wait_for_timeout(1500)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=True)
    if not ONLY or "prepare" in ONLY:
        prepare(browser)
    scene(browser, "s2_chat", chat)
    scene(browser, "s3_watch", watch)
    scene(browser, "s4_review", review)
    scene(browser, "s5_apps", apps)
    browser.close()
