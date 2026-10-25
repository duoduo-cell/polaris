"""Render the one-page portfolio PDFs (site/portfolio*.html -> site/polaris-portfolio-*.pdf).

Usage: .venv/Scripts/python.exe scripts/build_portfolio.py
Uses the system Edge through Playwright; falls back to Playwright's own Chromium if Edge is missing.
"""
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGES = {"portfolio.html": "polaris-portfolio-zh.pdf", "portfolio-en.html": "polaris-portfolio-en.pdf"}


def main() -> None:
    # The page uses the README screenshots; the Pages workflow copies them the same way.
    for image in (ROOT / "docs" / "img").glob("demo-*.png"):
        shutil.copy(image, SITE / "img" / image.name)
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge")
        except Exception:  # noqa: BLE001
            browser = p.chromium.launch()
        page = browser.new_page()
        for source, target in PAGES.items():
            page.goto((SITE / source).as_uri(), wait_until="load")
            page.pdf(path=str(SITE / target), format="A4", print_background=True, prefer_css_page_size=True)
            pages = page.evaluate("Math.ceil(document.documentElement.scrollHeight / (297 / 25.4 * 96 - 2 * 11 / 25.4 * 96))")
            print(f"{target}: about {pages} page(s)")
        browser.close()


if __name__ == "__main__":
    main()
