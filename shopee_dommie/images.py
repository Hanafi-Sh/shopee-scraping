"""Image downloader for Shopee product images.

Shopee images are served from `down-id.img.susercontent.com` (or similar
regional CDNs). The CDN checks for valid Referer and (sometimes) cookies
before serving full-size images. We use curl_cffi with Chrome impersonation
to bypass simple TLS fingerprinting, and pass the browser session cookies to
match Shopee's auth state.

Key behaviors:
- Atomic write: download to .tmp, then rename to final path.
- Retry: 3 attempts with backoff (1s, 3s, 5s).
- Skips if destination already exists (idempotent — safe to re-run).
- Naming: `<itemid>/01.<ext>`, `<itemid>/02.<ext>`, ... where ext is webp/jpg.

NOTE: We strip the `_tn` thumbnail suffix in shop_page.py before passing
the URL here, so the downloaded image is full-size (~200-500KB for webp).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi.requests import AsyncSession
from playwright.async_api import Page

RETRY_DELAYS_S = [1.0, 3.0, 5.0]  # backoff between attempts
DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0"


def infer_extension(url: str) -> str:
    """Infer file extension from URL. Defaults to .jpg."""
    path = urlparse(url).path
    if path.endswith(".webp"):
        return "webp"
    if path.endswith(".png"):
        return "png"
    if path.endswith(".gif"):
        return "gif"
    return "jpg"


async def _get_browser_cookies(page: Page) -> list[dict]:
    """Extract cookies from the current browser context, in a format curl_cffi accepts."""
    try:
        cookies = await page.context.cookies()
        # Playwright cookies already have the right shape; pass through.
        return cookies
    except Exception:
        return []


async def download_image(
    url: str,
    dest_path: Path,
    page: Page | None = None,
    referer: str = "https://shopee.co.id/",
    max_retries: int = 3,
) -> bool:
    """Download one image to dest_path. Returns True on success.

    Args:
        url: Full image URL (already full-size, no _tn suffix).
        dest_path: Where to save (e.g. data/shop/images/12345/01.webp).
        page: Optional Playwright page — used to extract session cookies.
        referer: HTTP Referer header (Shopee's CDN checks this).
        max_retries: How many times to retry on failure.
    """
    if dest_path.exists() and dest_path.stat().st_size > 0:
        # Idempotent: skip if already downloaded
        return True

    cookies = await _get_browser_cookies(page) if page else []

    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            async with AsyncSession(impersonate="chrome") as session:
                response = await session.get(
                    url,
                    headers={
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Referer": referer,
                        "Accept": "image/avif,image/webp,image/png,image/svg+xml,image/*;q=0.8,*/*;q=0.5",
                    },
                    cookies={c["name"]: c["value"] for c in cookies} if cookies else {},
                    timeout=30,
                )
                if response.status_code == 200 and len(response.content) > 0:
                    # Atomic write: .tmp then rename
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
                    tmp_path.write_bytes(response.content)
                    tmp_path.rename(dest_path)
                    return True
                last_error = RuntimeError(
                    f"HTTP {response.status_code} ({len(response.content)} bytes)"
                )
        except Exception as e:
            last_error = e

        if attempt < max_retries - 1:
            await asyncio.sleep(RETRY_DELAYS_S[min(attempt, len(RETRY_DELAYS_S) - 1)])

    if last_error:
        print(f"   ⚠️  Failed to download {url[:80]}: {last_error}")
    return False


async def download_product_images(
    image_urls: list[str],
    itemid: str,
    out_dir: Path,
    page: Page | None = None,
) -> list[Path]:
    """Download all images for a product. Returns list of successfully saved paths.

    Args:
        image_urls: List of full-size image URLs.
        itemid: Product ID (used to create images/<itemid>/ subdirectory).
        out_dir: Root output directory (e.g. data/erigostore/).
        page: Optional Playwright page for cookies.
    """
    if not image_urls:
        return []

    saved: list[Path] = []
    images_dir = out_dir / "images" / itemid

    for idx, url in enumerate(image_urls, start=1):
        ext = infer_extension(url)
        dest = images_dir / f"{idx:02d}.{ext}"
        ok = await download_image(url, dest, page=page)
        if ok:
            saved.append(dest)

    return saved
