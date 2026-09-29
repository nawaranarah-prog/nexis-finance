"""End-to-end UI workflow check (optional; needs `pip install playwright` and Microsoft Edge or Chromium).

Drives the running application (Vite dev server on :5173, API on :8000) through the research workflow:
inspect asset → create portfolio → estimate risk → run backtest → inspect trades → train ML model →
check registry → generate report → export trades. Fails on any console error, page exception or
HTTP >= 500 response.

    backend/.venv/Scripts/python scripts/e2e_ui_check.py [--base http://localhost:5173] [--headed]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5173")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    problems: list[str] = []
    name = f"E2E Portfolio {int(time.time())}"

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="msedge", headless=not args.headed)
        except Exception:  # fall back to a Playwright-managed Chromium
            browser = p.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(viewport={"width": 1500, "height": 1000}, accept_downloads=True)
        page: Page = ctx.new_page()
        page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
        expect.set_options(timeout=120_000)

        def step(msg: str) -> None:
            print(f"==> {msg}", flush=True)

        step("Overview")
        page.goto(f"{args.base}/")
        expect(page.get_by_text("Market snapshot")).to_be_visible()
        expect(page.get_by_text("DEMO / SYNTHETIC DATA MODE").first).to_be_visible()

        step("Inspect asset TCH2")
        page.goto(f"{args.base}/asset-research?symbol=TCH2")
        expect(page.get_by_text("Synthetic Technology 02 · equity")).to_be_visible()
        expect(page.locator(".js-plotly-plot").first).to_be_visible()

        step("Global search")
        page.get_by_label("Global search").fill("FIN3")
        expect(page.locator(".pop-item").first).to_be_visible()
        page.keyboard.press("Escape")

        step("Create portfolio")
        page.goto(f"{args.base}/portfolio-lab")
        page.get_by_role("button", name="New portfolio").click()
        builder = page.locator("section.card", has=page.get_by_text("New portfolio", exact=True))
        builder.locator("input.input").first.fill(name)
        for sym in ("TCH1", "FIN1", "UTL1", "HLT2"):
            builder.get_by_label("Search assets").fill(sym)
            builder.locator(".checklist label", has_text=sym).first.locator("input").check()
        builder.get_by_label("Search assets").fill("")
        builder.locator("select").first.select_option("min_variance")
        builder.get_by_role("button", name="Preview allocation").click()
        expect(builder.get_by_text("Proposed weights")).to_be_visible()
        builder.get_by_role("button", name="Save portfolio").click()
        expect(page.locator("h2", has_text=name)).to_be_visible()
        expect(page.get_by_text("Benchmark comparison")).to_be_visible()

        step("Estimate risk")
        page.goto(f"{args.base}/risk")
        page.get_by_role("button", name="Estimate risk").click()
        expect(page.get_by_text("Estimates by methodology")).to_be_visible()
        expect(page.get_by_text("Kupiec POF")).to_be_visible()

        step("Run backtest")
        page.goto(f"{args.base}/backtesting?strategy=momentum")
        page.get_by_role("button", name="Run backtest").click()
        expect(page.get_by_text("Trade log")).to_be_visible()
        rows = page.locator("section.card", has=page.get_by_text("Trade log")).locator("tbody tr")
        expect(rows.first).to_be_visible()
        print(f"    trade rows on first page: {rows.count()}")

        step("Export trades CSV")
        with page.expect_download() as dl:
            page.get_by_role("button", name="Trades CSV").click()
        path = dl.value.path()
        head = Path(path).read_text().splitlines()[0]
        assert head.startswith("id,signal_date,date,symbol"), head
        print(f"    downloaded {dl.value.suggested_filename}")

        step("Train ML model")
        page.goto(f"{args.base}/machine-learning")
        form = page.locator("section.card", has=page.get_by_text("New volatility experiment"))
        for label in ("Random forest", "Gradient boosting"):
            form.get_by_label(label).uncheck()
        form.get_by_role("button", name="Train and evaluate").click()
        expect(page.get_by_text("Evaluation metrics")).to_be_visible()
        expect(page.get_by_text("preferred").first).to_be_visible()

        step("Research registry")
        page.goto(f"{args.base}/experiments")
        expect(page.locator("tbody tr").first).to_be_visible()

        step("Generate report")
        page.goto(f"{args.base}/reports")
        page.locator("section.card", has=page.get_by_text("Report builder")).locator("select").first.select_option(label=name)
        with page.expect_download(timeout=180_000) as dl:
            page.get_by_role("button", name="Generate PDF report").click()
        pdf = Path(dl.value.path()).read_bytes()
        assert pdf[:4] == b"%PDF", "report is not a PDF"
        print(f"    report {dl.value.suggested_filename}: {len(pdf) / 1024:.0f} KB")

        step("System health")
        page.goto(f"{args.base}/system")
        expect(page.get_by_text("Record counts")).to_be_visible()
        browser.close()

    if problems:
        print("\nPROBLEMS:")
        for pr in problems:
            print("  -", pr)
        return 1
    print("\nEnd-to-end UI workflow passed with no console errors or server errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
