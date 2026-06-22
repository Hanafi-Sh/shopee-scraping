"""Diagnostic: investigate review pagination behavior.

Goals:
1. Open a product page, apply "Dengan Komentar" filter
2. Count reviews on the page (both filtered list & "all reviews" if accessible)
3. Inspect the pagination UI: are page numbers buttons? a "..." expand button?
4. Try clicking page 2 and see if the filter persists
5. Save HTML + a debug report to tests/fixtures/diag_<timestamp>/

Captcha is handled by the existing CaptchaDetector (you'll need to solve it
once at the start of the run).
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from camoufox import AsyncCamoufox

from shopee_dommie.captcha import CaptchaDetector
from shopee_dommie.product_page import (
    click_expand_pages,
    click_review_filter,
    click_review_page,
    is_review_filter_active,
)
from shopee_dommie.shop_page import extract_product_list

COOKIES_PATH = ROOT / "cookies.json"
PARENT_COOKIES = ROOT.parent / "cookies.json"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
SHOP_URL = "https://shopee.co.id/erigostore"


def load_storage_state():
    if COOKIES_PATH.exists():
        return json.loads(COOKIES_PATH.read_text())
    if PARENT_COOKIES.exists():
        return json.loads(PARENT_COOKIES.read_text())
    return None


async def inspect_pagination(page) -> dict:
    """Inspect the pagination UI: what buttons exist, what's their text, are they clickable?"""
    findings = {}
    # All buttons in the rating section
    rating_section = page.locator(".product-ratings, .product-rating-overview")
    if await rating_section.count() == 0:
        # Try the parent of the comment list
        rating_section = page.locator(".shopee-product-comment-list").locator(
            "xpath=ancestor::div[3]"
        )
    buttons = rating_section.first.locator("button")
    btn_count = await buttons.count()
    findings["total_buttons_in_section"] = btn_count
    btn_texts = []
    for i in range(min(btn_count, 30)):
        try:
            t = (await buttons.nth(i).inner_text()).strip()
            if t:
                btn_texts.append(t)
        except Exception:
            pass
    findings["button_texts"] = btn_texts

    # Are there `<a>` tags? (Shopee sometimes uses anchors for pagination)
    anchors = rating_section.first.locator("a")
    anchor_texts = []
    for i in range(min(await anchors.count(), 30)):
        try:
            t = (await anchors.nth(i).inner_text()).strip()
            href = await anchors.nth(i).get_attribute("href")
            if t:
                anchor_texts.append({"text": t, "href": href})
        except Exception:
            pass
    findings["anchor_texts"] = anchor_texts

    # Count review cards BEFORE clicking any filter
    list_container = page.locator(".shopee-product-comment-list, .product-ratings__list")
    if await list_container.count() > 0:
        try:
            n = await list_container.first.evaluate("(el) => el.children.length")
            findings["review_cards_before_filter"] = n
        except Exception:
            findings["review_cards_before_filter"] = "?"

    # Check for "Semua" (All) review count text — Shopee shows "X Penilaian" total
    body = await page.locator("body").inner_text()
    import re

    matches = re.findall(r"(\d+(?:[,.]\d+)?[KkMm]?)\s*[Pp]enilaian", body)
    findings["body_penilaian_matches"] = matches[:5]

    return findings


async def main():
    storage_state = load_storage_state()
    if not storage_state:
        print("❌ No cookies.json found. Run scripts/login.py first.")
        sys.exit(1)

    diag_dir = FIXTURES_DIR / f"diag_{int(time.time())}"
    diag_dir.mkdir(parents=True, exist_ok=True)

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
        captcha = CaptchaDetector(page, max_wait_s=300)

        # Navigate to shop
        print(f"🌐 Navigating to {SHOP_URL}...")
        await page.goto(SHOP_URL, wait_until="domcontentloaded", timeout=30_000)
        await captcha.wait_if_captcha()
        try:
            await page.wait_for_selector(".shopee-product-rating", timeout=10_000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        # Get first product
        products = await extract_product_list(page)
        if not products:
            print("❌ No products found.")
            return
        first = products[0]
        print(f"\n🎯 First product: {first.name[:60]}")
        print(f"   URL: {first.url[:100]}")

        # Navigate to product page
        await captcha.wait_if_captcha()
        await page.goto(first.url, wait_until="domcontentloaded", timeout=30_000)
        await captcha.wait_if_captcha()
        try:
            await page.wait_for_selector("div#main > *", timeout=10_000, state="attached")
        except Exception:
            pass
        await page.wait_for_timeout(3000)

        # Scroll to reviews
        for text in ["Penilaian Produk", "Ulasan", "Review", "Penilaian"]:
            loc = page.locator(f"text={text}")
            if await loc.count() > 0:
                await loc.first.scroll_into_view_if_needed()
                await page.wait_for_timeout(2000)
                break

        # Inspect pagination BEFORE filter
        print("\n📋 BEFORE filter (showing all reviews):")
        before = await inspect_pagination(page)
        print(f"  Total buttons in section: {before['total_buttons_in_section']}")
        print(f"  Button texts (first 20): {before['button_texts'][:20]}")
        print(f"  Anchor texts: {before['anchor_texts'][:10]}")
        print(f"  Review cards visible: {before.get('review_cards_before_filter', '?')}")
        print(f"  Body 'X Penilaian' matches: {before['body_penilaian_matches']}")
        await page.screenshot(path=str(diag_dir / "01_before_filter.png"), full_page=False)

        # Save HTML
        html = await page.content()
        (diag_dir / "01_before_filter.html").write_text(html, encoding="utf-8")
        (diag_dir / "01_before_filter.json").write_text(
            json.dumps(before, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Now apply "Dengan Komentar" filter
        print("\n🔍 Applying 'Dengan Komentar' filter...")
        if not await is_review_filter_active(page, "Dengan Komentar"):
            clicked = await click_review_filter(page, "Dengan Komentar")
            print(f"   Click result: {clicked}")
        else:
            print("   Already active")
        await page.wait_for_timeout(3000)

        # Inspect pagination AFTER filter
        print("\n📋 AFTER filter (Dengan Komentar):")
        after = await inspect_pagination(page)
        print(f"  Total buttons in section: {after['total_buttons_in_section']}")
        print(f"  Button texts: {after['button_texts']}")
        print(f"  Anchor texts: {after['anchor_texts'][:10]}")
        print(f"  Review cards visible: {after.get('review_cards_before_filter', '?')}")
        await page.screenshot(path=str(diag_dir / "02_after_filter.png"), full_page=False)
        (diag_dir / "02_after_filter.html").write_text(html, encoding="utf-8")
        (diag_dir / "02_after_filter.json").write_text(
            json.dumps(after, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        # Try clicking page 2 and see what happens
        print("\n🔄 Trying to click page 2...")
        clicked_2 = await click_review_page(page, 2)
        print(f"   click_review_page(2) returned: {clicked_2}")
        await page.wait_for_timeout(3000)

        if clicked_2:
            after_2 = await inspect_pagination(page)
            print(f"   After page-2 click, button texts: {after_2['button_texts']}")
            print(
                f"   After page-2 click, review cards: {after_2.get('review_cards_before_filter', '?')}"
            )
            await page.screenshot(path=str(diag_dir / "03_after_page2.png"), full_page=False)
            (diag_dir / "03_after_page2.html").write_text(await page.content(), encoding="utf-8")
            (diag_dir / "03_after_page2.json").write_text(
                json.dumps(after_2, indent=2, ensure_ascii=False), encoding="utf-8"
            )

            # Check if filter is still active
            filter_still_active = await is_review_filter_active(page, "Dengan Komentar")
            print(f"   Filter still active after page 2? {filter_still_active}")

        # Also try the "..." expand
        print("\n🔄 Trying to click '...' (expand pages)...")
        clicked_dots = await click_expand_pages(page)
        print(f"   click_expand_pages returned: {clicked_dots}")
        await page.wait_for_timeout(2000)
        if clicked_dots:
            after_dots = await inspect_pagination(page)
            print(f"   After '...' click, button texts: {after_dots['button_texts']}")
            await page.screenshot(path=str(diag_dir / "04_after_dots.png"), full_page=False)

        print(f"\n💾 Diagnostics saved to: {diag_dir}")
        print("   Files: 01_before_filter.*, 02_after_filter.*, 03_after_page2.*, 04_after_dots.*")

        # Print summary
        print("\n" + "=" * 70)
        print("DIAGNOSIS SUMMARY")
        print("=" * 70)
        print(f"Total reviews on product (from page body): {before['body_penilaian_matches']}")
        print(f"Reviews visible without filter: {before.get('review_cards_before_filter', '?')}")
        print(
            f"Reviews visible with 'Dengan Komentar' filter: {after.get('review_cards_before_filter', '?')}"
        )
        print(f"Filter active chip: {await is_review_filter_active(page, 'Dengan Komentar')}")
        print(f"Pagination buttons (after filter): {after['button_texts']}")
        print(f"Page-2 clickable: {clicked_2}")
        if clicked_2:
            print(f"After page-2: filter still active? {filter_still_active}")


if __name__ == "__main__":
    asyncio.run(main())
