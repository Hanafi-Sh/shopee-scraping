"""Smoke test: launch Camoufox, navigate to a shop, save HTML fixture + screenshot.

Usage:
    python -m scripts.smoke_browser                       # default: erigostore
    python -m scripts.smoke_browser https://shopee.co.id/shopeeofficial

Purpose:
    - Verify Camoufox + cookies flow works on this machine
    - Capture real Shopee HTML so we can discover DOM selectors empirically
    - Save a baseline screenshot for visual reference

Captcha reality (June 2026):
    Shopee uses Arkose Labs slider-puzzle captcha. It fires:
    1. On first navigation in a session (page-level)
    2. As a per-endpoint challenge (subsequent API calls)
    The captcha appears with a delay (Shopee's anti-bot fires AFTER the page
    shell loads) so a 5s grace period is unreliable. We continuously poll.

User interaction required:
    When captcha appears, the script prints a clear banner and waits up to
    5 minutes for you to solve it in the browser window. The page should
    redirect back to the shop automatically after solve.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from camoufox import AsyncCamoufox

FIXTURES_DIR = ROOT / "tests" / "fixtures"
COOKIES_PATH = ROOT / "cookies.json"
PARENT_COOKIES = ROOT.parent / "cookies.json"
SHOP_URL = sys.argv[1] if len(sys.argv) > 1 else "https://shopee.co.id/erigostore"

# URL patterns that mean "we got redirected to a captcha/verify page"
VERIFY_URL_PATTERNS = ["/verify/", "scene=crawler", "anti_bot_tracking_id"]
# DOM markers for captcha iframes (Arkose Labs uses funcaptcha)
CAPTCHA_IFRAME_PATTERNS = ["captcha", "arkoselab", "funcaptcha", "arkose"]
# Text that appears ON the captcha page (Indonesian + English)
CAPTCHA_TEXT_PATTERNS = [
    "Verifikasi untuk melanjutkan",
    "Verifikasi Anda",
    "Verify you are human",
    "Geser untuk menyelesaikan puzzle",
    "Slide to complete",
]
CAPTCHA_SOLVE_TIMEOUT_S = 300  # 5 min for user to solve


def load_storage_state() -> dict | None:
    """Load cookies from own file, falling back to parent's."""
    if COOKIES_PATH.exists():
        print(f"📂 Using own cookies: {COOKIES_PATH}")
        return json.loads(COOKIES_PATH.read_text())
    if PARENT_COOKIES.exists():
        print(f"📂 Falling back to parent cookies: {PARENT_COOKIES}")
        return json.loads(PARENT_COOKIES.read_text())
    print("⚠️  No cookies found — browser will open to logged-out state.")
    return None


def url_matches_captcha(url: str) -> bool:
    """True if the URL itself looks like a captcha/verify challenge."""
    return any(p in url for p in VERIFY_URL_PATTERNS)


async def dom_has_captcha(page) -> bool:
    """True if a captcha iframe or challenge text is visible in the DOM."""
    for pat in CAPTCHA_IFRAME_PATTERNS:
        loc = page.locator(f"iframe[src*='{pat}']")
        if await loc.count() > 0:
            return True
    for text in CAPTCHA_TEXT_PATTERNS:
        loc = page.locator(f"text={text}")
        if await loc.count() > 0:
            return True
    return False


async def is_captcha_state(page) -> bool:
    """Combined URL + DOM check for captcha state."""
    if url_matches_captcha(page.url):
        return True
    return await dom_has_captcha(page)


async def wait_for_captcha_solve(page, max_wait_s: int = CAPTCHA_SOLVE_TIMEOUT_S) -> bool:
    """Continuously poll for captcha; when found, wait for it to be solved.

    Returns True if captcha was detected and presumably solved. False if no
    captcha was ever seen during the polling period.
    """
    print("🔍 Polling for captcha / verify page (no grace period)...")
    poll_interval = 1.0
    waited = 0.0
    while waited < 30:  # poll for first 30s looking for captcha appearance
        if await is_captcha_state(page):
            return await _wait_for_user_solve(page, max_wait_s)
        await asyncio.sleep(poll_interval)
        waited += poll_interval
    print(f"   ✅ No captcha appeared in {waited:.0f}s window.")
    return False


async def _wait_for_user_solve(page, max_wait_s: int) -> bool:
    """Block until the captcha is no longer present (user solved it)."""
    print()
    print("=" * 70)
    print("🛑 CAPTCHA / VERIFY PAGE DETECTED")
    print(f"   URL: {page.url[:120]}")
    print()
    print("   → Look at the browser window that just opened")
    print("   → Solve the slider puzzle / image challenge")
    print("   → The page should redirect back to the shop automatically")
    print(f"   → This script will wait up to {max_wait_s}s for that to happen")
    print()
    print("=" * 70)
    print()

    waited = 0.0
    poll_interval = 2.0
    while waited < max_wait_s:
        await asyncio.sleep(poll_interval)
        waited += poll_interval
        if not await is_captcha_state(page):
            print(f"✅ Captcha solved after {waited:.0f}s. Page is now: {page.url[:80]}")
            # Wait for redirect chain to complete + React to re-render
            await page.wait_for_timeout(6000)
            return True
        if int(waited) % 30 == 0:
            print(f"   ⏳ Still waiting for captcha solve... ({waited:.0f}s / {max_wait_s}s)")

    print(f"⏱️  Timeout after {max_wait_s}s — saving current state anyway.")
    return False


async def wait_for_react_render(page, max_wait_s: int = 30) -> bool:
    """Wait for React to mount into div#main."""
    try:
        await page.wait_for_selector("div#main > *", timeout=max_wait_s * 1000, state="attached")
        return True
    except Exception:
        return False


async def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    storage_state = load_storage_state()
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

        print(f"🌐 Navigating to {SHOP_URL}...")
        try:
            await page.goto(SHOP_URL, wait_until="load", timeout=60_000)
        except Exception as e:
            print(f"⚠️  goto failed: {e}")

        # Watch for captcha (up to 5 min for user to solve)
        await wait_for_captcha_solve(page, max_wait_s=CAPTCHA_SOLVE_TIMEOUT_S)

        # Wait for React to render real content
        rendered = await wait_for_react_render(page, max_wait_s=30)
        # Buffer for post-render network calls
        await page.wait_for_timeout(3000)

        # Save HTML
        html = await page.content()
        html_path = FIXTURES_DIR / "shop_page.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"💾 Saved HTML: {html_path} ({len(html):,} chars)")

        # Save screenshot
        png_path = FIXTURES_DIR / "shop_page.png"
        await page.screenshot(path=str(png_path), full_page=False)
        print(f"📸 Saved screenshot: {png_path}")

        # Diagnostic
        url_now = page.url
        title = await page.title()
        product_links = await page.locator("a[href*='-i.'][href*='shopee']").count()
        main_children = await page.locator("div#main > *").count()
        print()
        print("─── DIAGNOSTIC ───")
        print(f"Final URL:    {url_now[:120]}")
        print(f"Page title:   {title}")
        print(f"div#main children: {main_children}")
        print(f"Product-like links (a[href*='-i.']): {product_links}")
        print(f"React rendered: {rendered}")
        print()
        if url_matches_captcha(url_now):
            print("❌ Still on captcha page. Re-run after solving in browser.")
        elif product_links == 0:
            print("⚠️  No product links found. Page may be loading or different region.")
        else:
            print("✅ Looks like we have a valid product grid. Ready for Step 3.")


if __name__ == "__main__":
    asyncio.run(main())
