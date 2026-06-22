"""Scrape a Shopee shop end-to-end.

Usage:
    python -m scripts.scrape_shop --shop erigostore --max-products 10
    python -m scripts.scrape_shop --shop https://shopee.co.id/erigostore
    python -m scripts.scrape_shop --shop 30203584

Flow:
    1. Smart-parse shop arg (username / URL / numeric shopid)
    2. Auto-detect cookies.json; if missing, launch inline login
    3. Open Camoufox with stealth options + cookies
    4. Initialize CaptchaDetector as a gate before risky actions
    5. Navigate to shop, extract shop info + product list
    6. For each product (sequential):
       - Navigate to product page
       - captcha.wait_if_captcha() before & after
       - Extract detail + reviews
       - Download images
       - Save (atomic per-product)
    7. Summary at end

CLI flags:
    --shop           Username, full URL, or numeric shopid (required)
    --cookies        Path to cookies.json (default: ./cookies.json)
    --out-dir        Output root (default: ./data)
    --max-products   Stop after N successful products (default: 10)
    --max-reviews    Max reviews per product (default: unlimited)
    --no-images      Skip image download
    --no-reviews     Skip review collection
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
DEBUG_DIR = ROOT / "tests" / "fixtures" / "debug"
FIXTURES_DIR = ROOT / "tests" / "fixtures"

from camoufox import AsyncCamoufox

from shopee_dommie.captcha import CaptchaDetector
from shopee_dommie.images import download_product_images
from shopee_dommie.models import ProductSummary
from shopee_dommie.persistence import (
    save_enriched_product,
    save_products_list,
    save_reviews,
    save_shop_info,
)
from shopee_dommie.product_page import extract_product_detail, extract_reviews
from shopee_dommie.shop_page import (
    PRODUCT_LINK_SELECTOR,
    extract_product_list,
    extract_shop_info,
)

SHOPIFY_BASE = "https://shopee.co.id"
COOKIES_PATH = ROOT / "cookies.json"
PARENT_COOKIES = ROOT.parent / "cookies.json"


def parse_shop_arg(shop: str) -> tuple[str, str]:
    """Smart parse: accept username, full URL, or numeric shopid.

    Returns (shop_url, username_for_path).
    - "erigostore" -> ("https://shopee.co.id/erigostore", "erigostore")
    - "https://shopee.co.id/erigostore" -> ("https://shopee.co.id/erigostore", "erigostore")
    - "30203584" -> ("https://shopee.co.id/shop/30203584", "shop_30203584") — fallback path

    Note: Shopee's shop URLs use the username form for normal shops. Numeric
    shopid-only access works via /shop/<id> but may not always resolve.
    """
    s = shop.strip()
    if s.startswith("http://") or s.startswith("https://"):
        # Full URL: extract username from path
        m = re.search(r"/([\w.-]+)/?(?:\?|$)", s)
        username = m.group(1) if m else "shop"
        return s, username
    if re.fullmatch(r"\d+", s):
        # Numeric shopid
        return f"{SHOPIFY_BASE}/shop/{s}", f"shop_{s}"
    # Treat as username
    return f"{SHOPIFY_BASE}/{s}", s


async def save_error_snapshot(page, error: Exception, label: str = "error") -> Path | None:
    """MANDATORY: save HTML + screenshot + context BEFORE browser closes.

    Called from the main try/finally wrapper. Writes to tests/fixtures/debug/
    with a timestamped subdirectory. Always returns the path (or None if save
    failed for some reason).
    """
    if page is None:
        return None
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = DEBUG_DIR / f"{ts}_{label}"
        out_dir.mkdir(parents=True, exist_ok=True)

        # 1. Screenshot (viewport + full page)
        try:
            await page.screenshot(path=str(out_dir / "screenshot.png"), full_page=False)
            await page.screenshot(path=str(out_dir / "screenshot_full.png"), full_page=True)
        except Exception as e:
            (out_dir / "screenshot_error.txt").write_text(str(e), encoding="utf-8")

        # 2. HTML (rendered DOM after JS)
        try:
            html = await page.content()
            (out_dir / "page.html").write_text(html, encoding="utf-8")
        except Exception as e:
            (out_dir / "html_error.txt").write_text(str(e), encoding="utf-8")

        # 3. URL + title + body text
        try:
            url = page.url
            title = await page.title()
            body = await page.locator("body").inner_text()
            meta = {
                "url": url,
                "title": title,
                "body_first_500": body[:500] if body else "",
                "body_length": len(body) if body else 0,
            }
            (out_dir / "meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            (out_dir / "meta_error.txt").write_text(str(e), encoding="utf-8")

        # 4. The exception itself
        (out_dir / "exception.txt").write_text(
            f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
            encoding="utf-8",
        )

        print()
        print("=" * 70)
        print(f"📸 ERROR SNAPSHOT saved: {out_dir}")
        print("   - screenshot.png + screenshot_full.png")
        print("   - page.html")
        print("   - meta.json (URL, title, body preview)")
        print("   - exception.txt")
        print("=" * 70)
        print()
        return out_dir
    except Exception as e:
        print(f"⚠️  save_error_snapshot itself failed: {e}")
        return None


def load_storage_state(cookies_path: Path) -> dict | None:
    """Load cookies from specified path, fallback to parent's if own missing."""
    if cookies_path.exists():
        print(f"📂 Using own cookies: {cookies_path}")
        return json.loads(cookies_path.read_text())
    if PARENT_COOKIES.exists() and cookies_path == COOKIES_PATH:
        print(f"📂 Falling back to parent cookies: {PARENT_COOKIES}")
        return json.loads(PARENT_COOKIES.read_text())
    return None


def ensure_cookies(cookies_path: Path) -> dict:
    """Ensure cookies.json exists. If not, raise a clear error."""
    storage = load_storage_state(cookies_path)
    if storage is None:
        print()
        print("❌ No cookies.json found.")
        print(f"   Expected at: {cookies_path}")
        print(f"   Or fallback:  {PARENT_COOKIES}")
        print()
        print("   Run the login script first:")
        print("     python -m scripts.login")
        print()
        sys.exit(1)
    return storage


async def scrape_one_product(
    page,
    captcha: CaptchaDetector,
    product: ProductSummary,
    out_dir: Path,
    shop_username: str,
    *,
    collect_reviews_enabled: bool = True,
    download_images_enabled: bool = True,
    max_reviews: int | None = None,
    with_comments_only: bool = True,
    also_csv: bool = True,
) -> tuple[bool, str]:
    """Scrape one product: navigate, extract, save. Returns (success, message)."""
    try:
        await captcha.wait_if_captcha()
        # OPTIMIZATION: use domcontentloaded (HTML ready) instead of load
        # (waits for all images/scripts). DOM extraction doesn't need images.
        await page.goto(product.url, wait_until="domcontentloaded", timeout=30_000)
        await captcha.wait_if_captcha()
        # Wait for React hydration (Shopee renders after DOM ready)
        try:
            await page.wait_for_selector("div#main > *", timeout=10_000, state="attached")
        except Exception:
            pass
        # Wait for product detail to actually render (price, title visible)
        try:
            await page.wait_for_selector(
                "[class*='price'], h1, .product-detail",
                timeout=5000,
                state="attached",
            )
        except Exception:
            pass
        # OPTIMIZATION: removed 2s hard wait — smart waits above are enough

        # Extract detail
        detail = await extract_product_detail(page, product.shopid, product.itemid)

        # Extract reviews
        reviews = []
        if collect_reviews_enabled:
            reviews = await extract_reviews(
                page,
                max_reviews=max_reviews,
                with_comments_only=with_comments_only,
            )

        # Download images
        image_paths: list[Path] = []
        if download_images_enabled and detail.images:
            image_paths = await download_product_images(
                detail.images, product.itemid, out_dir / shop_username, page=page
            )

        # Save (atomic, per-product, JSON + optional CSV)
        save_enriched_product(out_dir, shop_username, detail, also_csv=also_csv)
        if reviews:
            save_reviews(out_dir, shop_username, product.itemid, reviews, also_csv=also_csv)

        # Brief delay between products (anti-bot courtesy)
        delay = random.uniform(1.0, 2.0)
        await asyncio.sleep(delay)

        return True, (
            f"  ✓ {detail.name[:50]}  "
            f"price=Rp{detail.price:,}  "
            f"reviews={len(reviews)}  "
            f"images={len(image_paths)}"
            if detail.price
            else f"  ✓ {detail.name[:50]}  reviews={len(reviews)}  images={len(image_paths)}"
        )
    except Exception as e:
        return False, f"  ✗ {product.name[:50]}  error: {e}"


def interactive_collect_shops() -> list[str]:
    """Prompt the user for shop args one per line. Empty line ends input.

    Returns an empty list if input is unavailable (non-interactive TTY).
    """
    print()
    print("=" * 70)
    print("🎯 INTERACTIVE MODE — no shop args provided")
    print("   Enter Shopee shop usernames, full URLs, or numeric shopids.")
    print("   One per line. Press Enter on empty line to start scraping.")
    print("=" * 70)
    shops: list[str] = []
    while True:
        try:
            line = input(f"  Shop {len(shops) + 1} (or Enter to start): ").strip()
        except EOFError:
            # Ctrl-D / piped input ended without data
            print()
            break
        if not line:
            break
        # Allow comma-separated entries (e.g. "shop1, shop2, shop3")
        for piece in line.split(","):
            piece = piece.strip()
            if piece:
                shops.append(piece)
        print(f"    → added (total so far: {len(shops)})")
    return shops


def collect_shops(args) -> list[str]:
    """Collect shop arguments from --shop (repeatable) and --shops-from-file."""
    shops: list[str] = []
    if args.shop:
        shops.extend(args.shop)
    if args.shops_from_file:
        path = Path(args.shops_from_file)
        if not path.exists():
            print(f"❌ shops-from-file not found: {path}")
            sys.exit(1)
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                shops.append(line)
    if not shops:
        print(
            "❌ No shops provided. Use --shop <username> (repeatable) or --shops-from-file <path>."
        )
        sys.exit(1)
    return shops


async def _scrape_one_shop(
    page,
    captcha: CaptchaDetector,
    shop_url: str,
    shop_username: str,
    args,
    out_dir: Path,
) -> tuple[int, int]:
    """Scrape ONE shop. Returns (success_count, skip_count)."""

    # Step 1: navigate to shop
    print("🌐 Navigating to shop...")
    try:
        await page.goto(shop_url, wait_until="load", timeout=60_000)
    except Exception as e:
        print(f"⚠️  goto failed: {e}")
    await captcha.wait_if_captcha()

    # Wait for products — but Shopee sometimes shows captcha as a non-redirect
    # overlay that doesn't change URL. So we poll captcha during the wait
    # and re-check if we don't see products within 30s.
    products_visible = False
    for attempt in range(3):
        try:
            await page.wait_for_selector(PRODUCT_LINK_SELECTOR, timeout=30_000)
            products_visible = True
            break
        except Exception:
            # Re-check captcha (might have appeared as overlay)
            await captcha.wait_if_captcha()
            # Save debug screenshot so we can see what's on the page
            debug_path = FIXTURES_DIR / f"debug_no_products_{attempt}.png"
            try:
                await page.screenshot(path=str(debug_path), full_page=False)
                print(f"   📸 Debug screenshot: {debug_path}")
                url_now = page.url
                title = await page.title()
                body_text = await page.locator("body").inner_text()
                print(f"   URL: {url_now[:100]}")
                print(f"   Title: {title[:80]}")
                print(f"   Body (first 200): {body_text[:200]}")
            except Exception:
                pass
    if not products_visible:
        print("❌ No product links found after 3 attempts. Skipping this shop.")
        return 0, 0
    await page.wait_for_timeout(2000)

    # Step 2: extract shop info + product list
    print("\n📋 Extracting shop info...")
    shop_info = await extract_shop_info(page, shop_username)
    save_shop_info(out_dir, shop_info)
    print(
        f"   {shop_info.name} | products={shop_info.product_count} | followers={shop_info.follower_count}"
    )

    print("\n📋 Extracting product list...")
    products = await extract_product_list(page)
    save_products_list(out_dir, shop_username, products)
    print(f"   Found {len(products)} products")
    if not products:
        print("❌ No products found. Skipping this shop.")
        return 0, 0

    # Step 3: limit to max_products
    if args.max_products and len(products) > args.max_products:
        products = products[: args.max_products]

    # Step 4: scrape each product (sequential)
    print(f"\n🔄 Scraping {len(products)} products (sequential)...\n")
    success_count = 0
    skip_count = 0
    for i, product in enumerate(products, start=1):
        print(f"[{i}/{len(products)}] {product.name[:60]}")
        ok, msg = await scrape_one_product(
            page,
            captcha,
            product,
            out_dir,
            shop_username,
            collect_reviews_enabled=not args.no_reviews,
            download_images_enabled=not args.no_images,
            max_reviews=args.max_reviews,
            with_comments_only=not args.all_reviews,
            also_csv=not args.no_csv,
        )
        print(msg)
        if ok:
            success_count += 1
        else:
            skip_count += 1

    return success_count, skip_count


async def main_async(args) -> None:
    cookies_path = Path(args.cookies)
    storage_state = ensure_cookies(cookies_path)
    out_dir = Path(args.out_dir)
    shops = collect_shops(args)

    print(f"📦 {len(shops)} shop(s) queued:")
    for s in shops:
        print(f"   - {s}")
    print(f"   Output root: {out_dir}")
    print()

    page = None
    try:
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

            # Loop through all shops in the same browser session.
            # Captcha is solved ONCE (on the first navigation) and the session
            # stays valid for subsequent shop navigations — same tab, same cookies.
            total_success = 0
            total_skip = 0
            for idx, shop_arg in enumerate(shops, start=1):
                shop_url, shop_username = parse_shop_arg(shop_arg)
                print()
                print("=" * 70)
                print(f"🏪 Shop {idx}/{len(shops)}: {shop_username}")
                print(f"   URL: {shop_url}")
                print(f"   Output: {out_dir / shop_username}")
                print("=" * 70)
                try:
                    success, skip = await _scrape_one_shop(
                        page, captcha, shop_url, shop_username, args, out_dir
                    )
                    total_success += success
                    total_skip += skip
                    print()
                    print(f"   ✅ {shop_username}: {success} succeeded, {skip} skipped")
                except Exception as e:
                    # One shop failing shouldn't kill the whole batch.
                    # Save snapshot for debugging but continue to next shop.
                    print(f"   ❌ {shop_username} failed: {e}")
                    await save_error_snapshot(page, e, label=f"shop_{shop_username}_error")
                    total_skip += 1

            print()
            print("=" * 70)
            print("📊 ALL SHOPS COMPLETE")
            print(f"   Total: {len(shops)} shops")
            print(f"   Products: {total_success} succeeded, {total_skip} skipped")
            print(f"   Output: {out_dir}")
            print("=" * 70)
    except Exception as e:
        # Outer error handler. Page may already be closed by the async with exit.
        await save_error_snapshot(page, e, label="top_level_error")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape a Shopee shop via Camoufox DOM extraction."
    )
    parser.add_argument(
        "--shop",
        action="append",
        required=False,
        help="Username, full URL, or numeric shopid. Can be passed multiple times: --shop A --shop B",
    )
    parser.add_argument("--cookies", default=str(COOKIES_PATH), help="Path to cookies.json")
    parser.add_argument("--out-dir", default=str(ROOT / "data"), help="Output root directory")
    parser.add_argument(
        "--max-products",
        type=int,
        default=10,
        help="Stop after N successful products (default: 10)",
    )
    parser.add_argument("--max-reviews", type=int, default=None, help="Max reviews per product")
    parser.add_argument("--no-images", action="store_true", help="Skip image downloads")
    parser.add_argument("--no-reviews", action="store_true", help="Skip review collection")
    parser.add_argument(
        "--all-reviews",
        action="store_true",
        help="Disable the 'Dengan Komentar' filter (include rating-only reviews). Default: only reviews with text.",
    )
    parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Skip CSV export. Default: write both JSON and CSV (one row per record).",
    )
    parser.add_argument(
        "--shops-from-file",
        type=str,
        default=None,
        help="Path to a text file with one shop per line. Alternative to passing --shop multiple times.",
    )
    args = parser.parse_args()

    # Interactive mode: if no shops were given via CLI, prompt for them.
    if not args.shop and not args.shops_from_file:
        interactive_shops = interactive_collect_shops()
        if not interactive_shops:
            print("❌ No shops provided. Exiting.")
            print("   Usage examples:")
            print("     python -m scripts.scrape_shop --shop erigostore")
            print("     python -m scripts.scrape_shop --shop A --shop B")
            print("     python -m scripts.scrape_shop --shops-from-file shops.txt")
            sys.exit(1)
        args.shop = interactive_shops

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
