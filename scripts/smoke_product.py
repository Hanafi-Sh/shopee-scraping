"""Smoke test 2: from a shop page, click the first product, save the product
detail page HTML + screenshot. This is what we'll mine for product detail
selectors (title, price, description, image gallery, review arrow buttons).

Usage:
    python -m scripts.smoke_product                # default: erigostore

Flow:
    1. Navigate to shop page (handles captcha)
    2. Click the first product card
    3. Wait for product detail page
    4. Click "Lihat Selengkapnya" / "View More" for description (if visible)
    5. Save initial HTML + expanded HTML
    6. Save screenshots
    7. Discover review arrow selectors
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from camoufox import AsyncCamoufox

from shopee_dommie.shop_page import PRODUCT_LINK_SELECTOR

FIXTURES_DIR = ROOT / "tests" / "fixtures"
COOKIES_PATH = ROOT / "cookies.json"
PARENT_COOKIES = ROOT.parent / "cookies.json"
SHOP_URL = sys.argv[1] if len(sys.argv) > 1 else "https://shopee.co.id/erigostore"
CAPTCHA_SOLVE_TIMEOUT_S = 300

# Patterns for the description "expand" button (Bahasa + English)
EXPAND_BUTTON_TEXTS = [
    "Lihat Selengkapnya",
    "View More",
    "Lihat Lebih",
    "See More",
    "Show More",
    "Tampilkan Lebih",
    "Selengkapnya",
]

# Patterns for review pagination arrows (next / page numbers)
REVIEW_NEXT_TEXTS = ["next", "Next", "berikutnya", "Berikutnya", ">", "→", "›"]
REVIEW_PREV_TEXTS = ["prev", "Prev", "sebelumnya", "Sebelumnya", "<", "←", "‹"]

# Captcha detection — inlined here for now; will move to captcha.py in Task #6
_VERIFY_URL_PATTERNS = ["/verify/", "scene=crawler", "anti_bot_tracking_id"]
_CAPTCHA_IFRAME_PATTERNS = ["captcha", "arkoselab", "funcaptcha", "arkose"]
_CAPTCHA_TEXT_PATTERNS = [
    "Verifikasi untuk melanjutkan",
    "Verifikasi Anda",
    "Verify you are human",
    "Geser untuk menyelesaikan puzzle",
]


def _url_matches_captcha(url: str) -> bool:
    return any(p in url for p in _VERIFY_URL_PATTERNS)


async def _dom_has_captcha(page) -> bool:
    for pat in _CAPTCHA_IFRAME_PATTERNS:
        if await page.locator(f"iframe[src*='{pat}']").count() > 0:
            return True
    for text in _CAPTCHA_TEXT_PATTERNS:
        if await page.locator(f"text={text}").count() > 0:
            return True
    return False


async def _is_captcha_state(page) -> bool:
    if _url_matches_captcha(page.url):
        return True
    return await _dom_has_captcha(page)


async def _wait_for_captcha_solve(page, max_wait_s: int = CAPTCHA_SOLVE_TIMEOUT_S) -> bool:
    """Inline copy of shop_page.wait_for_captcha_solve for this standalone script."""
    poll_interval = 1.0
    waited = 0.0
    while waited < 30:
        if await _is_captcha_state(page):
            print()
            print("=" * 70)
            print("🛑 CAPTCHA / VERIFY PAGE DETECTED")
            print(f"   URL: {page.url[:120]}")
            print("   → Solve it in the browser window.")
            print(f"   → Waiting up to {max_wait_s}s...")
            print("=" * 70)
            print()
            waited2 = 0.0
            while waited2 < max_wait_s:
                await asyncio.sleep(2)
                waited2 += 2
                if not await _is_captcha_state(page):
                    print(f"✅ Captcha solved after {waited2:.0f}s.")
                    await page.wait_for_timeout(5000)
                    return True
                if int(waited2) % 30 == 0:
                    print(f"   ⏳ Still waiting... ({waited2:.0f}s / {max_wait_s}s)")
            print(f"⏱️  Timeout after {max_wait_s}s.")
            return False
        await asyncio.sleep(poll_interval)
        waited += poll_interval
    print(f"   ✅ No captcha in first {waited:.0f}s.")
    return False


async def _wait_for_react_render(page, max_wait_s: int = 30) -> bool:
    try:
        await page.wait_for_selector("div#main > *", timeout=max_wait_s * 1000, state="attached")
        return True
    except Exception:
        return False


def _load_storage_state() -> dict | None:
    if COOKIES_PATH.exists():
        return json.loads(COOKIES_PATH.read_text())
    if PARENT_COOKIES.exists():
        return json.loads(PARENT_COOKIES.read_text())
    return None


async def try_click_expand_description(page) -> bool:
    """Try to find and click the 'View More' button for the product description.

    Returns True if a button was found and clicked.
    """
    for text in EXPAND_BUTTON_TEXTS:
        loc = page.locator(f"text={text}")
        count = await loc.count()
        if count > 0:
            try:
                # Click the first one that's visible
                for i in range(count):
                    btn = loc.nth(i)
                    if await btn.is_visible():
                        await btn.click()
                        await page.wait_for_timeout(1500)
                        print(f"   Clicked expand button: {text!r}")
                        return True
            except Exception as e:
                print(f"   Failed to click {text!r}: {e}")
    return False


async def discover_review_pagination(page) -> dict:
    """Inspect the review section to find pagination controls."""
    findings: dict = {
        "next_button_candidates": [],
        "prev_button_candidates": [],
        "page_indicator_candidates": [],
    }
    for text in REVIEW_NEXT_TEXTS:
        count = await page.locator(f"text={text}").count()
        if count > 0:
            findings["next_button_candidates"].append({"text": text, "count": count})
    for text in REVIEW_PREV_TEXTS:
        count = await page.locator(f"text={text}").count()
        if count > 0:
            findings["prev_button_candidates"].append({"text": text, "count": count})
    # Common page indicator patterns: "1/12", "Halaman 1 dari 12"
    for pat in [r"\d+\s*/\s*\d+", r"Halaman\s+\d+\s+dari\s+\d+", r"Page\s+\d+\s+of\s+\d+"]:
        import re

        for m in re.finditer(pat, await page.content()):
            findings["page_indicator_candidates"].append(m.group(0))
    return findings


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    storage_state = _load_storage_state()
    print(f"🎯 Target shop: {SHOP_URL}")
    print()

    async with AsyncCamoufox(headless=False, humanize=True) as browser:
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            geolocation={"latitude": -6.2088, "longitude": 106.8456},
            permissions=["geolocation"],
            storage_state=storage_state,
        )
        page = await context.new_page()

        # Step 1: navigate to shop
        print("🌐 Step 1: navigate to shop...")
        try:
            await page.goto(SHOP_URL, wait_until="load", timeout=60_000)
        except Exception as e:
            print(f"⚠️  goto failed: {e}")

        # Step 2: handle captcha
        await _wait_for_captcha_solve(page, max_wait_s=CAPTCHA_SOLVE_TIMEOUT_S)
        await _wait_for_react_render(page, max_wait_s=30)
        await page.wait_for_timeout(2000)

        # Step 3: find first product link
        print("\n🎯 Step 3: click first product...")
        try:
            await page.wait_for_selector(PRODUCT_LINK_SELECTOR, timeout=30_000)
            first_link = page.locator(PRODUCT_LINK_SELECTOR).first
            href = await first_link.get_attribute("href")
            product_name_hint = href.split("/")[-1].split("-i.")[0] if href else "?"
            print(f"   First product href: {href[:100] if href else 'N/A'}")
            print(f"   Name hint: {product_name_hint[:60]}")
            await first_link.click()
        except Exception as e:
            print(f"⚠️  Failed to click first product: {e}")
            print("   → Falling back: navigate manually to a known product URL.")
            return

        # Step 4: wait for product page to load
        await _wait_for_captcha_solve(page, max_wait_s=CAPTCHA_SOLVE_TIMEOUT_S)
        await _wait_for_react_render(page, max_wait_s=30)
        await page.wait_for_timeout(3000)

        # Step 5: save initial product HTML + screenshot
        print("\n💾 Step 5: save product page HTML (initial)...")
        html = await page.content()
        html_path = FIXTURES_DIR / "product_page.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"   Saved: {html_path} ({len(html):,} chars)")
        png_path = FIXTURES_DIR / "product_page.png"
        await page.screenshot(path=str(png_path), full_page=False)
        print(f"   Saved: {png_path}")

        # Step 6: try to expand description
        print("\n🖱️  Step 6: try to expand description...")
        expanded = await try_click_expand_description(page)
        if expanded:
            await page.wait_for_timeout(2000)
            html2 = await page.content()
            html2_path = FIXTURES_DIR / "product_page_expanded.html"
            html2_path.write_text(html2, encoding="utf-8")
            print(f"   Saved expanded: {html2_path} ({len(html2):,} chars)")
            png2_path = FIXTURES_DIR / "product_page_expanded.png"
            await page.screenshot(path=str(png2_path), full_page=False)
        else:
            print("   No expand button found (or already expanded).")

        # Step 7: discover review pagination selectors
        print("\n🔍 Step 7: discover review pagination selectors...")
        # Scroll to review section first
        for scroll_text in ["Ulasan", "Review", "Penilaian", "Rating"]:
            loc = page.locator(f"text={scroll_text}")
            if await loc.count() > 0:
                try:
                    await loc.first.scroll_into_view_if_needed()
                    print(f"   Scrolled to: {scroll_text!r}")
                    break
                except Exception:
                    pass
        await page.wait_for_timeout(2000)

        review_findings = await discover_review_pagination(page)
        print("   Review pagination findings:")
        import json as _json

        print(_json.dumps(review_findings, indent=2))

        # Final diagnostic
        print("\n─── DIAGNOSTIC ───")
        print(f"Final URL:    {page.url[:120]}")
        print(f"Page title:   {await page.title()}")
        if await _is_captcha_state(page):
            print("❌ Still on captcha page.")


if __name__ == "__main__":
    asyncio.run(main())
