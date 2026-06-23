"""Extract product detail + reviews from a Shopee product page.

Selectors discovered from smoke test 2026-06-22 (erigostore / Erigo T-Shirt):

- Product name: from <title> (strip "Jual " + "| Shopee Indonesia")
- Current price: "Rp<digits>" pattern (smallest = current, larger = original)
- Discount: "-N%" badge near original price
- Rating: small text "4.8" near star icon
- Review count: "N Penilaian" text
- Sold: "1RB+ Terjual" / "100+ Terjual" / "1JT+ Terjual"
  - "RB" = ribu (thousand) = "K"
  - "JT" = juta (million) = "M"
- Description: <h2>Deskripsi Produk</h2> followed by <p> paragraphs
- Variants: buttons with class `selection-box-unselected` (size S/M/L/XL/XXL)
- Main image: srcset with @resize_w900_nl (strip suffix for original)
- Gallery: 4-5 thumbnails at bottom (v1: only main image, see KNOWN_LIMITATIONS)
- Reviews: items inside `.shopee-product-comment-list` (each is one review)
  - Author: element with class containing "username"
  - Rating: count of `.icon-rating-solid` (filled stars)
  - Comment: longest text node inside the review card
- Pagination: page number buttons (1, 2, 3, ...) + "..." to expand
  - Active page: `.shopee-button-solid--primary`
  - Other pages: `.shopee-button-no-outline`
  - "...": `.shopee-button-no-outline--non-click` (not clickable, just expands)

KNOWN LIMITATIONS (v1):
- Gallery: only main image extracted. Shopee lazy-loads thumbnails; clicking
  each would yield 5-10 unique images. Skipped for v1 to keep pipeline fast.
- Reviewer images: not extracted (low priority, adds DOM traversal).
- Stock count: extracted only if "Stok" text is present; many products skip this.
"""

from __future__ import annotations

import re

from playwright.async_api import Page

from .models import ProductDetail, Review

# Regex patterns
PRICE_RE = re.compile(r"Rp\s*([\d.]+)")
DISCOUNT_RE = re.compile(r"-?\s*(\d+)\s*%")
RATING_RE = re.compile(r"\b([0-5]\.\d)\b")
REVIEW_COUNT_RE = re.compile(r"(\d+(?:\.\d+)?[KkMm]?)\s*[Pp]enilaian")
SOLD_RE = re.compile(r"(\d+(?:\.\d+)?[KkMm]?|(\d+(?:\.\d+)?)\s*(?:RB|JT))\s*\+?\s*[Tt]erjual")
STOCK_RE = re.compile(r"[Ss]tok\s*[:\s]*(\d+)")
IMAGE_W900_RE = re.compile(r"(https://down-[^@]+)@resize_w900_nl")

# Indonesian number abbreviations
INDIANESIAN_SUFFIXES = {
    "RB": 1_000,  # ribu
    "JT": 1_000_000,  # juta
    "K": 1_000,
    "M": 1_000_000,
}


def parse_price(text: str) -> int | None:
    """Parse 'Rp54.000' or 'Rp 54.000' to int 54000. Returns None if not parseable."""
    if not text:
        return None
    m = PRICE_RE.search(text)
    if not m:
        return None
    # Remove dots (thousand separator)
    digits = m.group(1).replace(".", "").replace(",", "")
    try:
        return int(digits)
    except ValueError:
        return None


def parse_indonesian_count(text: str) -> int | None:
    """Parse '1.2RB', '500', '1JT', '2.5M', '10K+' to int.

    RB / K = ribu = 1,000
    JT / M = juta = 1,000,000
    Trailing '+' and other non-alphanumeric chars are stripped.
    """
    if not text:
        return None
    # Strip everything except digits, separators, and letters
    cleaned = re.sub(r"[^0-9.,A-Za-z]", "", text.strip().upper())
    if not cleaned:
        return None
    m = re.match(r"^(\d+(?:[.,]\d+)?)([A-Z]*)$", cleaned)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    suffix = m.group(2)
    multiplier = INDIANESIAN_SUFFIXES.get(suffix, 1)
    return int(value * multiplier)


async def get_text(locator) -> str:
    """Safely get text from a locator, returning '' if empty/error."""
    try:
        if await locator.count() == 0:
            return ""
        return (await locator.first.inner_text()).strip()
    except Exception:
        return ""


async def extract_product_name(page: Page) -> str:
    """Get product name from <title>, stripping Shopee boilerplate."""
    try:
        title = await page.title()
        # "Jual Erigo T-Shirt... | Shopee Indonesia" -> "Erigo T-Shirt..."
        name = re.split(r"\s*\|\s*", title)[0]
        name = re.sub(r"^Jual\s+", "", name)
        return name.strip()
    except Exception:
        return ""


async def extract_prices(page: Page) -> tuple[int | None, int | None]:
    """Extract (current_price, original_price) from page text.

    Strategy: find ALL 'Rp<num>' matches and pick the largest value. Real
    product prices are always the largest 'Rp' amount on the page — voucher
    amounts (Rp100, Rp200, Rp89.579, etc. from the mini-vouchers block)
    are smaller and lose the max() comparison.

    If multiple large prices exist (e.g. current + strikethrough original),
    current = smaller, original = larger.
    """
    try:
        # Exclude the mini-vouchers block entirely — that's where voucher
        # amounts (Rp100, Rp200, Rp89.579) live. Removing it from the body
        # text leaves only the actual product prices.
        body_text = await page.locator("body").inner_text()
        # Also try to evaluate the body without the mini-voucher section
        try:
            cleaned = await page.evaluate(
                """() => {
                    const clone = document.body.cloneNode(true);
                    clone.querySelectorAll('[class*=\"mini-voucher\"]')
                        .forEach(n => n.remove());
                    return clone.innerText;
                }"""
            )
            if cleaned and len(cleaned) >= len(body_text) * 0.5:
                body_text = cleaned
        except Exception:
            pass
    except Exception:
        return None, None
    raw_prices = [
        int(p.replace(".", "").replace(",", "")) for p in PRICE_RE.findall(body_text) if p
    ]
    if not raw_prices:
        return None, None
    # Find the largest "Rp" amount — this is the actual product price.
    # Voucher amounts are smaller and won't be picked.
    real_price = max(raw_prices)
    # If the largest amount is too small (< Rp10.000), the page is likely
    # degraded (only vouchers rendered). Wait and retry once — the real
    # price often appears a moment later.
    if real_price < 10_000:
        await page.wait_for_timeout(2000)
        body_text = await page.locator("body").inner_text()
        raw_prices = [
            int(p.replace(".", "").replace(",", "")) for p in PRICE_RE.findall(body_text) if p
        ]
        if not raw_prices:
            return None, None
        real_price = max(raw_prices)
    # For discount detection: if there's a 2nd price that's >= 30% of max,
    # treat it as the current price (strikethrough original is shown above)
    other = [p for p in raw_prices if p < real_price and p >= real_price * 0.3]
    if other:
        current = max(other)
        return current, real_price
    return real_price, None


async def extract_discount_percent(page: Page) -> int | None:
    """Extract discount percentage from '-N%' badge."""
    try:
        body_text = await page.locator("body").inner_text()
        m = DISCOUNT_RE.search(body_text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


async def extract_rating(page: Page) -> float | None:
    """Extract numeric rating (e.g. 4.8)."""
    try:
        body_text = await page.locator("body").inner_text()
        m = RATING_RE.search(body_text)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return None


async def extract_review_count(page: Page) -> int | None:
    """Extract total review count (e.g. 303 from '303 Penilaian')."""
    try:
        body_text = await page.locator("body").inner_text()
        m = REVIEW_COUNT_RE.search(body_text)
        if m:
            return parse_indonesian_count(m.group(1))
    except Exception:
        pass
    return None


async def extract_sold_count(page: Page) -> int | None:
    """Extract sold count from '1RB+ Terjual' or '100+ Terjual'."""
    try:
        body_text = await page.locator("body").inner_text()
        # Look for "X+ Terjual" or "X Terjual"
        m = re.search(r"(\d+(?:[.,]\d+)?(?:\s*(?:RB|JT|K|M))?)\+?\s*[Tt]erjual", body_text)
        if m:
            return parse_indonesian_count(m.group(1))
    except Exception:
        pass
    return None


async def extract_stock(page: Page) -> int | None:
    """Extract stock count. Returns None if not present."""
    try:
        body_text = await page.locator("body").inner_text()
        m = STOCK_RE.search(body_text)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return None


async def extract_description(page: Page) -> str:
    """Extract product description (text after 'Deskripsi Produk' h2)."""
    try:
        # Find the h2
        h2 = page.locator("h2", has_text="Deskripsi Produk")
        if await h2.count() == 0:
            return ""
        # Get the parent section, then find all <p>
        container = h2.first.locator("xpath=ancestor::section[1]")
        if await container.count() == 0:
            # Try going up to nearest div
            container = h2.first.locator("xpath=ancestor::div[contains(@class, '')][2]")
        paragraphs = container.first.locator("p")
        count = await paragraphs.count()
        if count == 0:
            # Fallback: get all <p> after the h2
            return await _extract_text_after_h2(page, "Deskripsi Produk")
        texts: list[str] = []
        for i in range(count):
            txt = (await paragraphs.nth(i).inner_text()).strip()
            if txt:
                texts.append(txt)
        # NOTE: we previously joined with "\n\n" but Shopee often renders empty
        # `<p></p>` between bullet items, producing double-blank lines in CSV/Excel.
        # Filter + single-newline keeps the visual structure without the blanks.
        return "\n".join(texts)
    except Exception:
        return ""


async def _extract_text_after_h2(page: Page, header_text: str) -> str:
    """Fallback: extract all <p> text after a section header."""
    try:
        h2 = page.locator(f"h2:has-text('{header_text}')")
        if await h2.count() == 0:
            return ""
        # Use evaluate to walk the DOM
        result = await h2.first.evaluate(
            """(h2) => {
                const section = h2.closest('section') || h2.parentElement;
                if (!section) return '';
                const ps = section.querySelectorAll('p');
                return Array.from(ps).map(p => p.innerText.trim()).filter(Boolean).join('\\n\\n');
            }"""
        )
        return result or ""
    except Exception:
        return ""


async def extract_main_image(page: Page) -> str | None:
    """Extract the main product image URL (full size, strip @resize suffix)."""
    try:
        # Get the main product image (usually the first srcset with @resize_w900)
        srcset = await page.locator("img[srcset*='@resize_w900_nl']").first.get_attribute("srcset")
        if srcset:
            # srcset format: "url1 1x, url2 2x"
            first_url = srcset.split(",")[0].strip().split()[0]
            # Strip @resize_w900_nl for full size
            return re.sub(r"@resize_w\d+_nl", "", first_url)
    except Exception:
        pass
    return None


async def extract_variants(page: Page) -> list[dict]:
    """Extract variant options (e.g. S, M, L, XL, XXL).

    Shopee uses buttons with class `selection-box-unselected` (or
    `selection-box-selected` for active) for size/color variants.
    """
    variants = []
    try:
        # Newer Shopee uses this class
        buttons = page.locator("button.selection-box-unselected, button.selection-box-selected")
        if await buttons.count() == 0:
            # Fallback: any button with short single-word text in selection area
            return []
        for i in range(await buttons.count()):
            text = (await buttons.nth(i).inner_text()).strip()
            if text and len(text) <= 20:
                variants.append({"name": text})
    except Exception:
        pass
    return variants


async def extract_product_detail(page: Page, shopid: str, itemid: str) -> ProductDetail:
    """Extract full product detail from the current product page.

    Args:
        page: Playwright page currently on a product detail URL.
        shopid, itemid: known identifiers (from the shop page).

    Returns:
        ProductDetail with all fields populated. Missing fields stay None / empty.
    """
    name = await extract_product_name(page)
    current_price, original_price = await extract_prices(page)
    discount = await extract_discount_percent(page)
    rating = await extract_rating(page)
    review_count = await extract_review_count(page)
    sold = await extract_sold_count(page)
    stock = await extract_stock(page)
    description = await extract_description(page)
    main_image = await extract_main_image(page)
    variants = await extract_variants(page)

    images = []
    if main_image:
        images.append(main_image)

    return ProductDetail(
        shopid=shopid,
        itemid=itemid,
        name=name,
        price=current_price,
        original_price=original_price,
        sold=sold,
        rating=rating,
        rating_count=review_count,
        stock=stock,
        description=description,
        images=images,
        variants=variants,
        url=page.url,
    )


async def click_review_page(page: Page, page_num: int) -> bool:
    """Click a numbered review page button (e.g. '3'). Returns True if clicked.

    CRITICAL: Numbered page buttons can collide with size variant buttons
    (e.g. size "28" for shoes). We MUST scope the search to the rating
    section to avoid clicking the wrong button. We also try the click in a
    loop because the rating section is below the product variant area
    in DOM order.

    Also: Shopee's pagination is collapsed when there are many pages, showing
    only `1, 2, ..., 25, 26, 27, 28, 29, ..., last`. Pages 3-24 don't exist
    in DOM until user clicks the first "..." button. Our caller is responsible
    for calling click_expand_pages() when this returns False.
    """
    pat = re.compile(rf"^\s*{page_num}\s*$")

    # Scope to the rating section. Multiple selectors in case Shopee uses
    # different container classes — we walk up from .shopee-product-comment-list
    # to find the closest .product-ratings ancestor (which holds the pagination).
    rating_section = page.locator(".product-ratings").last
    if await rating_section.count() == 0:
        # Fallback: walk up from comment list
        rating_section = page.locator(".product-rating-overview, .shopee-product-comment-list").last

    # Try button (most common) then a, with exact text match (no loose pattern
    # — loose patterns can match the "..." button)
    for sel in ["button", "a"]:
        base = rating_section.locator(sel)
        try:
            loc = base.filter(has_text=pat)
            cnt = await loc.count()
            for i in range(cnt):
                elem = loc.nth(i)
                text = (await elem.inner_text()).strip()
                if text == str(page_num):
                    await elem.click()
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=2000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(500)
                    return True
        except Exception:
            continue

    return False


async def click_next_page_chevron(page: Page) -> bool:
    """Click the right-arrow chevron button to navigate to the next review page.

    Shopee's review pagination has:
    - numbered buttons (1, 2, 3, ...) — only first 5 + last 5 visible by default
    - a '...' expand button to reveal middle pages
    - LEFT/RIGHT chevron icon buttons (`shopee-icon-button--left/right`) for
      prev/next navigation. These are always present, no expansion needed.

    Returns True if clicked. Caller should verify page change via first-card
    text comparison.
    """
    btn = page.locator("button.shopee-icon-button--right").last
    if await btn.count() == 0:
        return False
    try:
        await btn.click()
        await page.wait_for_timeout(1500)
        return True
    except Exception:
        return False


async def _capture_first_review_text(page: Page) -> str:
    """Get the first review card's text. Used to detect pagination change."""
    try:
        list_container = page.locator(".shopee-product-comment-list, .product-ratings__list")
        if await list_container.count() == 0:
            return ""
        return await list_container.first.evaluate(
            "(el) => el.children[0] ? el.children[0].innerText : ''"
        )
    except Exception:
        return ""


async def get_active_page_number(page: Page) -> int | None:
    """Get the currently active pagination page number.

    Shopee marks the active page with `shopee-button-solid--primary` class.
    Returns the page number as int, or None if no active page is found.

    This is the most reliable way to detect pagination state — the active
    page indicator always updates when the page changes.
    """
    try:
        btn = page.locator("button.shopee-button-solid--primary").last
        if await btn.count() == 0:
            return None
        text = (await btn.inner_text()).strip()
        return int(text) if text.isdigit() else None
    except Exception:
        return None


async def get_chevron_disabled(page: Page) -> bool:
    """Check if the next-page chevron is disabled.

    Shopee disables pagination buttons on the last page. If the right
    chevron is disabled, we've reached the end of pagination.
    """
    try:
        btn = page.locator("button.shopee-icon-button--right").last
        if await btn.count() == 0:
            return True
        # Shopee uses `disabled` attribute or class `--disabled`
        is_disabled = await btn.evaluate(
            "(el) => el.disabled || el.classList.contains('shopee-icon-button--disabled') || el.getAttribute('aria-disabled') === 'true'"
        )
        return bool(is_disabled)
    except Exception:
        return False


async def click_expand_pages(page: Page, side: str = "middle") -> bool:
    """Click a '...' button to expand the page number list.

    Shopee's pagination may have multiple "..." buttons:
    - First "..." (side="middle"): expands pages between early/late (e.g. shows 3-23)
    - Last "..." (side="end"): expands the very last pages (e.g. shows last 5)

    Returns True if clicked.
    """
    try:
        dots = page.locator("button:has-text('...')")
        if await dots.count() == 0:
            return False
        # Pick the right "..." by index
        if side == "middle":
            target_idx = 0
        elif side == "end":
            target_idx = await dots.count() - 1
        else:
            target_idx = 0
        if target_idx >= await dots.count():
            target_idx = 0
        await dots.nth(target_idx).click()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=2000)
        except Exception:
            await page.wait_for_timeout(300)
        return True
    except Exception:
        return False


async def click_review_filter(page: Page, filter_text: str) -> bool:
    """Click a review filter chip by its visible text.

    Shopee's review section has filter chips at the top:
    - "Semua" (All)
    - "Dengan Foto/Video" / "Dengan Media" (With Photo/Video)
    - "5 Bintang" / "4 Bintang" / etc. (specific star counts)
    - "Dengan Komentar" / "dengan komentar" (With Comments) — only reviews that have text

    Chips use class `product-rating-overview__filter` (with `--active` for
    the currently selected one). Clicking a chip re-renders the review list
    filtered to that category.

    IMPORTANT: Shopee's chip text case is inconsistent across pages —
    some shops use "Dengan Komentar" (capitalized) and others "dengan komentar"
    (lowercase). We try both, with the count-suffix ("(N)") stripped since
    Shopee includes the review count in parens.

    Returns True if the filter was found and clicked.
    """
    candidates = [filter_text, filter_text.lower(), filter_text.upper()]
    # Strip "(N)" suffix if present (Shopee includes review count)
    base = re.sub(r"\s*\(\d+\)\s*$", "", filter_text)
    candidates.extend([base, base.lower(), base.upper()])
    # Dedupe while preserving order
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    for cand in candidates:
        try:
            chip = page.locator(f"div.product-rating-overview__filter:has-text('{cand}')")
            if await chip.count() > 0:
                await chip.first.click()
                # Wait for AJAX to settle after filter change
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=2000)
                except Exception:
                    await page.wait_for_timeout(300)
                return True
        except Exception:
            continue
    return False


async def is_review_filter_active(page: Page, filter_text: str) -> bool:
    """Check if a filter chip is currently active (has the --active modifier)."""
    candidates = [filter_text, filter_text.lower(), filter_text.upper()]
    base = re.sub(r"\s*\(\d+\)\s*$", "", filter_text)
    candidates.extend([base, base.lower(), base.upper()])
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    for cand in candidates:
        try:
            chip = page.locator(f"div.product-rating-overview__filter--active:has-text('{cand}')")
            if await chip.count() > 0:
                return True
        except Exception:
            continue
    return False


async def extract_review_from_card(card, with_comments_only: bool = False) -> Review | None:
    """Extract one Review from a review card locator (a direct child of .shopee-product-comment-list).

    Args:
        card: Playwright locator for the review card.
        with_comments_only: If True, skip reviews with empty/very short comments
            (defensive — Shopee's "Dengan Komentar" filter sometimes leaks
            rating-only entries through).
    """
    try:
        # IMPORTANT: Shopee's review card contains BOTH the customer comment
        # AND the seller's reply. The seller reply is rendered inside a `<div
        # class="QSiE2A">` element (Shopee's obfuscated class name — stable
        # across sessions as of 2026-06-23). If we naively pick the longest
        # line in the card, we get the seller template ("Halo Kak! Terima
        # kasih...") instead of the customer's actual review.
        #
        # Fix: clone the card, strip the seller-reply block, then read text.
        # This guarantees we extract text only from the customer side.
        # NOTE: we use ONLY the specific `.QSiE2A` class. Broader selectors
        # like `[class*="seller-reply"]` can over-match (e.g. ancestor
        # containers) and accidentally remove the customer comment too.
        try:
            text = await card.evaluate(
                """(el) => {
                    const clone = el.cloneNode(true);
                    clone.querySelectorAll('.QSiE2A').forEach(n => n.remove());
                    return clone.innerText;
                }"""
            )
        except Exception:
            text = await card.inner_text()
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if not lines:
            return None

        # Author: look for username element first. The element's innerText
        # often concatenates username + date + variation, so we extract just
        # the leading token (username is the part before the first digit run
        # or date marker).
        author = ""
        username_loc = card.locator("[class*='username']")
        if await username_loc.count() > 0:
            author = (await username_loc.first.inner_text()).strip()
        if not author and lines:
            author = lines[0]
        # Truncate at the first "|", " 20" (date start), or other UI markers
        for sep in ["|", " 20", "Laporkan", "respon"]:
            idx = author.find(sep)
            if idx > 0:
                author = author[:idx].strip()
                break
        # If author still contains a date pattern (YYYY-MM-DD or YYYY/MM/DD
        # or any 4-digit-year followed by digits), truncate at the year.
        # Use negative lookbehind to avoid matching digits in usernames like
        # 'user_2026' (the underscore between "user" and "2026" wouldn't be
        # a word boundary).
        m_year = re.search(r"(?<!\d)(19|20)\d{2}", author)
        if m_year:
            author = author[: m_year.start()].strip()

        # Rating: count filled stars inside this specific review card.
        # Priority: `.shopee-rating-stars__lit` (each is one filled star, max 5 per review).
        # Fallback: `.icon-rating-solid` is broader (matches like icons too — don't trust it).
        stars = await card.locator(".shopee-rating-stars__lit").count()
        if stars == 0:
            stars = await card.locator(".icon-rating-solid").count()
            # Cap at 5 — anything more means we matched something else (likes, etc.)
            if stars > 5:
                stars = 0
        # Clamp to valid range
        stars = max(0, min(stars, 5))

        # Date: looks for "N hari/minggu/bulan/lalu" or a date. Truncate at
        # any subsequent "|" or text marker (Shopee adds variation info that
        # belongs in a separate field).
        posted_at = ""
        date_pattern = re.compile(
            r"(\d+\s*(?:hari|minggu|bulan|tahun|jam)\s*(?:lalu|yang\s+lalu)?|202[0-9])",
            re.IGNORECASE,
        )
        for line in lines:
            if date_pattern.search(line):
                posted_at = line
                break
        if not posted_at:
            for line in lines:
                if re.search(r"hari|minggu|bulan|tahun|2024|2025|2026", line, re.IGNORECASE):
                    posted_at = line
                    break
        # Truncate at "|" (Shopee appends variation info after date)
        idx = posted_at.find("|")
        if idx > 0:
            posted_at = posted_at[:idx].strip()

        # Comment: longest text line (excluding author/date).
        # Shopee's review text concatenates username + date + "| Variasi:" +
        # actual comment into a single line. The actual comment is the part
        # AFTER the last "|". Also strip trailing seller-reply label and
        # report button if present.
        comment = ""
        # Use substring match (not equality) because posted_at was truncated
        # at "|", so the line containing the date+variation no longer equals
        # posted_at exactly — it's a SUPERSTRING of posted_at.
        candidates = [l for l in lines if author not in l and posted_at not in l and len(l) > 15]
        if candidates:
            comment = max(candidates, key=len)
        # If the comment line still contains "|", it's a date+variation
        # prefix concatenated with the real comment. Split and take the
        # last (longest) segment.
        if "|" in comment:
            parts = [p.strip() for p in comment.split("|")]
            tail = max(parts[1:], key=len) if len(parts) > 1 else parts[-1]
            if len(tail) > 15:
                comment = tail
        # Strip trailing seller-reply label and report button if present
        for marker in ["respon penjual", "Laporkan Penyalahgunaan", "Membantu?"]:
            idx = comment.find(marker)
            if idx > 0:
                comment = comment[:idx].strip()

        # Defensive: if filter was on, skip reviews with empty/very short comments
        if with_comments_only and len(comment.strip()) < 10:
            return None

        return Review(
            review_id=f"r-{hash(text) & 0xFFFFFFFF:08x}",
            author=author,
            rating=stars or 5,
            comment=comment,
            posted_at=posted_at,
            images=[],
        )
    except Exception:
        return None


async def extract_reviews(
    page: Page,
    *,
    captcha=None,
    max_reviews: int | None = None,
    with_comments_only: bool = True,
) -> list[Review]:
    """Extract reviews from the current product page.

    Strategy:
    1. Scroll to review section
    2. If with_comments_only, click the "Dengan Komentar" filter chip first.
       This filters out rating-only reviews (which have no text and no value
       for sentiment analysis), saving significant time.
    3. Read all visible review cards (children of .shopee-product-comment-list)
    4. Click next page button until max_reviews reached or no more pages
    5. Click "..." to expand page list if available

    Args:
        page: Product page with review section.
        captcha: CaptchaDetector instance. MANDATORY in production — Shopee can
            re-trigger captcha mid-extraction (e.g. after filter/pagination
            clicks). If provided, captcha.wait_if_captcha() is called before
            every risky action. If None, no captcha gate (DEV/headless only).
        max_reviews: Stop after this many reviews (None = all).
        with_comments_only: If True, click "Dengan Komentar" filter first to
            skip rating-only reviews. Default True (saves time).

    Returns:
        List of Review records.
    """

    async def _gate() -> None:
        """Re-check captcha at the start of every risky section."""
        if captcha is not None:
            await captcha.wait_if_captcha()

    await _gate()
    # Scroll to review section. Shopee lazy-loads reviews — they're below the
    # description and may not be in the initial viewport. We do an explicit
    # page scroll to ensure the section is rendered before clicking filter.
    scrolled = False
    for scroll_text in ["Penilaian Produk", "Ulasan", "Review", "Penilaian"]:
        loc = page.locator(f"text={scroll_text}")
        if await loc.count() > 0:
            try:
                # Re-find the locator each time to avoid stale-element issues
                # (the page can re-render between scroll attempts)
                for _attempt in range(3):
                    try:
                        # First try: scroll the locator into view
                        await loc.first.scroll_into_view_if_needed(timeout=3000)
                        scrolled = True
                        break
                    except Exception:
                        # Fallback: explicit page scroll in 600px steps
                        await page.evaluate("window.scrollBy(0, 600)")
                        await page.wait_for_timeout(500)
                        # Re-find (the text element may have re-mounted)
                        loc = page.locator(f"text={scroll_text}")
                        if await loc.count() == 0:
                            break
                if scrolled:
                    break
            except Exception:
                pass

    # If the section header still wasn't found, fall back to scrolling
    # all the way to the bottom of the page (Shopee renders reviews there).
    if not scrolled:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(2000)

    # Wait until the review list container is in the DOM (and not empty if possible)
    try:
        await page.wait_for_selector(
            ".shopee-product-comment-list, .product-ratings__list",
            timeout=5000,
            state="attached",
        )
    except Exception:
        pass

    # Apply filter (only if not already active).
    # The chip text case varies across Shopee pages (capitalized on some,
    # lowercase on others), so click_review_filter tries both internally.
    if with_comments_only and not await is_review_filter_active(page, "Dengan Komentar"):
        await _gate()
        clicked = await click_review_filter(page, "Dengan Komentar")
        if clicked:
            print("   🔍 Filtered: Dengan Komentar (skips rating-only reviews)")
        else:
            print("   ⚠️  'Dengan Komentar' filter not found, collecting all reviews")
        # Re-gate after the click — Shopee often re-evaluates after filter change
        await _gate()

    all_reviews: list[Review] = []
    seen_hashes: set[int] = set()
    current_page = 1
    max_pages = 50  # hard cap to prevent infinite loops

    while current_page <= max_pages:
        # Find the review list container
        list_container = page.locator(".shopee-product-comment-list, .product-ratings__list")
        count = await list_container.count()
        if count == 0:
            break

        # Each review card has data-cmtid attribute (Shopee's stable identifier).
        # We use this selector instead of `> *` to skip any non-card sibling divs
        # that Shopee might insert (e.g. section headers, banners). Falls back to
        # direct children if data-cmtid isn't found (some Shopee deployments
        # might not use it).
        child_count = 0
        for selector in [
            ":scope > [data-cmtid]",
            "> [data-cmtid]",
            "[data-cmtid]",
            "> *",
        ]:
            try:
                if (
                    selector.startswith(":scope")
                    or selector.startswith(">")
                    or selector.startswith("[")
                ):
                    child_count = await list_container.first.evaluate(
                        f"(el) => el.querySelectorAll('{selector}').length"
                    )
                if child_count > 0:
                    break
            except Exception:
                continue
        if child_count == 0:
            break

        # Pick the actual selector we ended up using
        active_selector = "> *"
        for selector in ["> [data-cmtid]", "[data-cmtid]", "> *"]:
            try:
                cnt = await list_container.first.evaluate(
                    f"(el) => el.querySelectorAll('{selector}').length"
                )
                if cnt > 0:
                    active_selector = selector
                    child_count = cnt
                    break
            except Exception:
                continue

        for i in range(child_count):
            if max_reviews and len(all_reviews) >= max_reviews:
                return all_reviews
            try:
                card = list_container.first.locator(active_selector).nth(i)
                review = await extract_review_from_card(card, with_comments_only=with_comments_only)
            except Exception:
                continue
            if review is None:
                continue
            # Dedup by review_id (cmtid-derived hash)
            h = hash(review.review_id)
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            all_reviews.append(review)

        if max_reviews and len(all_reviews) >= max_reviews:
            break

        # Try to go to next page. Shopee's review pagination UI (as of
        # 2026-06-23): numbered buttons (1, 2, ..., 5, "...", last) + LEFT/RIGHT
        # chevron icon buttons (`shopee-icon-button--left/right`). The chevron
        # is the most reliable — always present, no need to expand "...".
        # Exhaustion detection: track the active page button
        # (`shopee-button-solid--primary`). If after clicking chevron the
        # active page number doesn't increment, we're stuck.
        await _gate()
        current_page += 1
        prev_active = await get_active_page_number(page)

        # First check: chevron disabled (last page reached)
        if await get_chevron_disabled(page):
            print(f"   ⏹  Pagination exhausted at page {current_page - 1} (chevron disabled)")
            return all_reviews

        clicked = await click_next_page_chevron(page)
        if not clicked:
            print(f"   ⏹  Pagination exhausted at page {current_page - 1} (no chevron clickable)")
            return all_reviews
        # Re-gate in case Shopee triggered captcha on the click
        await _gate()
        # Wait for AJAX to settle
        await page.wait_for_timeout(2000)

        # Verify page change via active page indicator
        new_active = await get_active_page_number(page)
        if prev_active is not None and new_active is not None and new_active <= prev_active:
            print(
                f"   ⏹  Pagination exhausted at page {current_page - 1} "
                f"(active page {prev_active} didn't increment, still {new_active})"
            )
            return all_reviews

        # Also check first-card text as belt-and-suspenders
        new_first_review = await _capture_first_review_text(page)
        if prev_active is None and not new_first_review:
            print(f"   ⏹  Pagination exhausted at page {current_page - 1} (list empty)")
            return all_reviews

    return all_reviews
