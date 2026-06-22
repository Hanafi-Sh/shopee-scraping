r"""Extract product list from a Shopee shop page.

Selectors were discovered empirically from the smoke test (2026-06-22, after
solving the page-level captcha on erigostore). They are minimal and
attribute-based — Shopee's webpack class names are minified and change
frequently (e.g. `_displayContents_yazkc_21`), so we don't rely on them.

Key selectors:
- Product card link:     `a[href*="-i."]`  (relative or absolute)
- Item ID / Shop ID:     parsed from `href` via regex `-i\.(\d+)\.(\d+)`
  (shopid is the FIRST number, itemid is the SECOND — Shopee's URL convention)
- Product image:         `img[src*="img.susercontent.com"]` — strip `_tn` for full size
- Sold count text:       `text containing "Terjual"` near a price element
- Official Shop badge:   `text containing "Official Shop"`

Infinite scroll note: Shopee renders ~30-60 products per page and lazy-loads
more on scroll. We do NOT auto-scroll here — the caller decides when to scroll
(e.g. if --max-products > visible count).
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from playwright.async_api import Page

from .models import ProductSummary, ShopInfo

PRODUCT_LINK_SELECTOR = "a[href*='-i.']"
PRODUCT_HREF_RE = re.compile(r"-i\.(\d+)\.(\d+)")
SHOPEE_BASE = "https://shopee.co.id"
IMAGE_SELECTOR = "img[src*='img.susercontent.com']"
IMAGE_THUMBNAIL_SUFFIX_RE = re.compile(r"_(?:tn|tn\d+|sr\d+x\d+)(?=\.\w+$)")
SOLD_TEXT_RE = re.compile(r"(\d+(?:\.\d+)?[Kk]?)\s*[Tt]erjual")
PRODUCT_COUNT_RE = re.compile(r"(\d+(?:\.\d+)?[Kk]?)\s*[Pp]roduk")
FOLLOWER_COUNT_RE = re.compile(r"(\d+(?:\.\d+)?[KkMm]?)\s*[Pp]engikut|follower")


def parse_product_href(href: str) -> tuple[str, str] | None:
    """Extract (shopid, itemid) from a product link.

    Shopee URL format: /<slug>-i.{shopid}.{itemid}[?extraParams=...]
    - shopid is FIRST (constant per shop)
    - itemid is SECOND (unique per product)
    """
    m = PRODUCT_HREF_RE.search(href)
    if not m:
        return None
    return m.group(1), m.group(2)  # shopid, itemid


def decode_name_from_slug(href: str) -> str:
    """Turn a URL slug into a rough product name.

    Example: '/Erigo-T-Shirt-Griffin-Black-Kaos-Unisex-i.123.456'
         -> 'Erigo T Shirt Griffin Black Kaos Unisex'
    """
    slug = href.split("/")[-1]
    slug = re.split(r"-i\.\d+\.\d+", slug)[0]  # strip the -i.SHOOPID.ITEMID
    return slug.replace("-", " ").strip()


def strip_thumbnail_suffix(url: str) -> str:
    """Convert thumbnail URL to full-size by removing `_tn` etc.

    Example: '.../file/abc_tn.webp' -> '.../file/abc.webp'
    Example: '.../file/abc_tn@resize' -> '.../file/abc@resize'
    """
    return IMAGE_THUMBNAIL_SUFFIX_RE.sub("", url)


async def extract_product_card(page: Page, link_locator) -> ProductSummary | None:
    """Extract one ProductSummary from a product link locator."""
    href = await link_locator.get_attribute("href")
    if not href:
        return None
    parsed = parse_product_href(href)
    if not parsed:
        return None
    shopid, itemid = parsed
    abs_url = urljoin(SHOPEE_BASE + "/", href.lstrip("/"))

    # Try to get the image URL from the link or its first descendant
    image_url = None
    try:
        img = link_locator.locator(IMAGE_SELECTOR).first
        if await img.count() > 0:
            src = await img.get_attribute("src")
            if src:
                image_url = strip_thumbnail_suffix(src)
    except Exception:
        pass

    return ProductSummary(
        shopid=shopid,
        itemid=itemid,
        name=decode_name_from_slug(href),
        url=abs_url,
        image_url=image_url,
    )


async def extract_product_list(page: Page) -> list[ProductSummary]:
    """Extract all visible product cards from the current shop page.

    Deduplicates by (shopid, itemid). Does NOT auto-scroll — the caller can
    page.goto a different page or scroll for more.
    """
    # Wait for at least one product link
    try:
        await page.wait_for_selector(PRODUCT_LINK_SELECTOR, timeout=30_000)
    except Exception:
        return []

    links = await page.locator(PRODUCT_LINK_SELECTOR).all()
    products: list[ProductSummary] = []
    seen: set[tuple[str, str]] = set()

    for link in links:
        try:
            product = await extract_product_card(page, link)
        except Exception:
            continue
        if product is None:
            continue
        key = (product.shopid, product.itemid)
        if key in seen:
            continue
        seen.add(key)
        products.append(product)

    return products


def parse_compact_count(text: str) -> int | None:
    """Parse '1.2K', '500', '2.5M' to int. Returns None if not parseable."""
    if not text:
        return None
    text = text.strip().upper().replace(",", "").replace(".", "")
    m = re.match(r"^(\d+(?:\.\d+)?)([KM]?)$", text)
    if not m:
        return None
    value = float(m.group(1))
    suffix = m.group(2)
    if suffix == "K":
        value *= 1_000
    elif suffix == "M":
        value *= 1_000_000
    return int(value)


async def extract_shop_info(page: Page, username: str) -> ShopInfo:
    """Extract shop-level info from header area. Best-effort; missing fields stay None."""
    info = ShopInfo(username=username, name=username)

    # Title contains the display name, e.g. "Toko Online ERIGO Official Shop"
    try:
        title = await page.title()
        # Strip " - Produk Resmi Terlengkap..." suffix
        clean = re.sub(r"\s*-\s*Produk.*$", "", title)
        clean = re.sub(r"^Toko Online\s+", "", clean)
        if clean:
            info.name = clean
    except Exception:
        pass

    # Try to detect Official / Mall badges
    try:
        if await page.locator("text=Official Shop").count() > 0:
            info.is_official = True
        if await page.locator("text=Shopee Mall").count() > 0 or "Mall" in (await page.title()):
            info.is_mall = True
    except Exception:
        pass

    # Page body text contains the counts
    try:
        body_text = await page.locator("body").inner_text()
        m = PRODUCT_COUNT_RE.search(body_text)
        if m:
            info.product_count = parse_compact_count(m.group(1))
        m = FOLLOWER_COUNT_RE.search(body_text)
        if m:
            info.follower_count = parse_compact_count(m.group(1))
    except Exception:
        pass

    return info
