"""Take the website/README screenshots from a running demo server (see scripts/demo_data.py).

Usage: .venv/bin/python scripts/screenshots.py [http://127.0.0.1:8099] [docs/screenshots] [light|dark]
Requires Playwright with WebKit (pip install playwright && playwright install webkit).
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8099"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/screenshots")
SCHEME = sys.argv[3] if len(sys.argv) > 3 else "light"
OUT.mkdir(parents=True, exist_ok=True)

PAGES = [
    ("dashboard", "#/", 0),
    ("plan", "#/plan", 0),
    ("insights", "#/insights", 0),
    ("categorize", "#/categorize", 0),
    ("card", "#/cards/1", 0),
    ("transactions", "#/transactions", 0),
]

with sync_playwright() as p:
    browser = p.webkit.launch()
    page = browser.new_page(viewport={"width": 402, "height": 874}, device_scale_factor=2, color_scheme=SCHEME)
    for name, url, scroll in PAGES:
        page.goto(f"{BASE}/{url}")
        page.wait_for_timeout(2200)
        if scroll:
            page.locator("#scroller").evaluate(f"e => e.scrollTo(0, {scroll})")
            page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / f"{name}.png"))
        print("saved", name)
    browser.close()
