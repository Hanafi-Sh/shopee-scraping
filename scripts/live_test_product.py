"""Live integration test for shopee_dommie.product_page.

Navigates to a shop, clicks first product, runs extract_product_detail and
extract_reviews, and prints results. Use this to verify the product_page
module works end-to-end with real Shopee data.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from camoufox import AsyncCamoufox

from shopee_dommie.product_page import (
    extract_product_detail,
    extract_reviews,
)
from shopee_dommie.shop_page import (
    PRODUCT_LINK_SELECTOR,
    parse_product_href,
)

# Captcha detection inlined (will be replaced by shopee_dommie.captcha in Task #6)
_VERIFY_URL_PATTERNS = ["/verify/", "scene=crawler", "anti_bot_tracking_id"]
_CAPTCHA_IFRAME_PATTERNS = ["captcha", "arkoselab", "funcaptcha", "arkose"]
_CAPTCHA_TEXT_PATTERNS = [
    "Verifikasi untuk melanjutkan",
    "Verifikasi Anda",
    "Verify you are human",
    "Geser untuk menyelesaikan puzzle",
]
COOKIES_PATH = ROOT / "cookies.json"
PARENT_COOKIES = ROOT.parent / "cookies.json"
SHOP_URL = sys.argv[1] if len(sys.argv) > 1 else "https://shopee.co.id/erigostore"


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


async def _wait_for_captcha_solve(page, max_wait_s: int = 300) -> bool:
    poll_interval = 1.0
    waited = 0.0
    while waited < 30:
        if await _is_captcha_state(page):
            print()
            print("🛑 CAPTCHA / VERIFY PAGE — solve in browser, up to 300s")
            print()
            waited2 = 0.0
            while waited2 < max_wait_s:
                await asyncio.sleep(2)
                waited2 += 2
                if not await _is_captcha_state(page):
                    print(f"✅ Captcha solved after {waited2:.0f}s.")
                    await page.wait_for_timeout(5000)
                    return True
            return False
        await asyncio.sleep(poll_interval)
        waited += poll_interval
    return False


def _load_storage_state() -> dict | None:
    if COOKIES_PATH.exists():
        return json.loads(COOKIES_PATH.read_text())
    if PARENT_COOKIES.exists():
        return json.loads(PARENT_COOKIES.read_text())
    return None


async def main() -> None:
    storage_state = _load_storage_state()
    print(f"🎯 Target shop: {SHOP_URL}\n")

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

        print("🌐 Step 1: navigate to shop...")
        await page.goto(SHOP_URL, wait_until="load", timeout=60_000)
        await _wait_for_captcha_solve(page)
        await page.wait_for_selector(PRODUCT_LINK_SELECTOR, timeout=30_000)
        await page.wait_for_timeout(2000)

        print("🎯 Step 2: click first product...")
        first_link = page.locator(PRODUCT_LINK_SELECTOR).first
        href = await first_link.get_attribute("href")
        parsed = parse_product_href(href)
        if not parsed:
            print(f"⚠️  Could not parse href: {href}")
            return
        shopid, itemid = parsed
        print(f"   shopid={shopid}  itemid={itemid}")
        await first_link.click()
        await page.wait_for_load_state("load", timeout=30_000)
        await _wait_for_captcha_solve(page)
        await page.wait_for_timeout(3000)

        print("\n📋 Step 3: extract_product_detail...")
        detail = await extract_product_detail(page, shopid, itemid)
        print(json.dumps(detail.to_dict(), indent=2, ensure_ascii=False))

        print("\n📋 Step 4: extract_reviews (no limit, full pagination)...")
        reviews = await extract_reviews(page, max_reviews=None)
        print(f"   Found {len(reviews)} reviews")
        for r in reviews[:3]:
            print(f"   - [{r.rating}★] {r.author}: {r.comment[:80]}")

        print("\n─── SUMMARY ───")
        print(f"Name:        {detail.name}")
        print(f"Price:       Rp{detail.price:,}" if detail.price else "Price:       None")
        print(
            f"Original:    Rp{detail.original_price:,}"
            if detail.original_price
            else "Original:    None"
        )
        print(f"Sold:        {detail.sold}")
        print(f"Rating:      {detail.rating}")
        print(f"Reviews:     {detail.rating_count}")
        print(f"Stock:       {detail.stock}")
        print(f"Images:      {len(detail.images)}")
        print(f"Variants:    {[v['name'] for v in detail.variants]}")
        print(f"Description: {len(detail.description)} chars")
        print(f"Reviews collected: {len(reviews)}")


if __name__ == "__main__":
    asyncio.run(main())
