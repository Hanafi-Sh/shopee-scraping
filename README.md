# shopee-dommie

Shopee shop scraper using [Camoufox](https://github.com/daijro/camoufox) for
stealth browser automation. Extracts product images, descriptions, and reviews
from any Shopee shop's catalog by driving a real browser through the rendered
DOM.

This project is the **post-rejection sibling** of the parent `shopee-scraping/`
project. The parent tried the "Camoufox for login + plain `requests` for the
scraping" approach, but Shopee's anti-bot (per-endpoint captcha, `af-ac-enc-dat`
session validation, Arkose Labs slider puzzles) blocks pure-HTTP scraping.
See the parent's `FINDINGS.md` for the full autopsy.

**shopee-dommie goes a different direction:** drive Camoufox for every step.
After solving the page-level captcha once, subsequent navigations render
products in the DOM and we extract from the rendered HTML.

## Why "dommie"

DOM-ee. As in **DOM extraction**. The "ee" is a deliberate play — like "buddy"
but for the DOM, and also a nod to how this scraper is the underdog that wins
by being slow, stealthy, and persistent rather than fast and clever.

## How it works

```
┌──────────────────────────────────────────────────────────┐
│  1. Launch Camoufox (stealth Firefox) with auth cookies  │
│  2. Navigate to shop URL — if captcha appears, PAUSE      │
│     and wait for user to solve manually                   │
│  3. Extract product list from rendered shop page          │
│  4. For each product (sequential, same tab):              │
│     - Navigate to product page (captcha-gated)            │
│     - Click "Dengan Komentar" filter (skip rating-only)   │
│     - Extract: name, price, sold, rating, description,    │
│       images, variants                                    │
│     - Paginate reviews (page numbers 1, 2, 3, ...)        │
│     - Download images via curl_cffi                       │
│     - Save atomically per product                         │
│  5. Move to next shop (same browser session)              │
└──────────────────────────────────────────────────────────┘
```

## Quick start

```bash
# 1. install (dependencies are already in ~/.my_global_env — see Installation)
pip install -e .

# 2. log in to Shopee (one-time per session, ~1-7 days)
python -m scripts.login

# 3a. interactive mode (no args — prompts for shops)
python -m scripts.scrape_shop
#   → Enter shops one per line. Comma-separated also accepted.
#   → Empty line starts scraping. Ctrl-D also works.

# 3b. scrape a single shop
python -m scripts.scrape_shop --shop erigostore --max-products 10

# 4. scrape multiple shops in one browser session (1 captcha solve)
python -m scripts.scrape_shop \
    --shop erigostore \
    --shop wardahofficial \
    --shop adidasindonesia \
    --max-products 10

# 5. scrape shops from a file (one shop per line, # = comment)
python -m scripts.scrape_shop --shops-from-file top_shops.txt

# 6. verify output structure
python -m scripts.verify_goal --shop erigostore --min-products 1 --min-reviews 1
```

## Output structure

```
data/<shop_username>/
├── shop_info.json            Shop metadata
├── products.json             Product list (id, name, url, image)
├── products_enriched.json    Full detail per product (description, price, etc.)
├── products_enriched.csv     Same data, flat for spreadsheets (Excel/pandas)
├── images/<itemid>/NN.<ext>  Product images
├── reviews/<itemid>.json     Reviews per product (list)
└── reviews/<itemid>.csv      Same reviews, flat for spreadsheets
```

CSV notes:
- UTF-8 with BOM (so Excel opens Indonesian text correctly)
- Nested fields (variants, images) are JSON-stringified in their cell
- One row per record (one row per product, one row per review)
- Disable with `--no-csv` if you only want JSON

## CLI reference

### `python -m scripts.scrape_shop`

| Flag | Default | Description |
|------|---------|-------------|
| `--shop` | (required*) | Username, full URL, or numeric shopid. Can be passed multiple times. |
| `--shops-from-file` | (alt) | Path to text file with one shop per line. |
| `--cookies` | `cookies.json` | Path to Shopee session cookies. |
| `--out-dir` | `data` | Output root directory. |
| `--max-products` | 10 | Stop after N successful products per shop. |
| `--max-reviews` | unlimited | Per-product review cap. Set lower (e.g. `--max-reviews 10`) to speed up scraping when you only need a sample. |
| `--no-images` | off | Skip image downloads. |
| `--no-reviews` | off | Skip review collection. |
| `--all-reviews` | off | Disable "Dengan Komentar" filter (include rating-only reviews). |
| `--no-csv` | off | Skip CSV export (only write JSON). Default: write both JSON and CSV. |

\* One of `--shop` (1+ times) or `--shops-from-file` is required.

### `python -m scripts.login`

| Flag | Default | Description |
|------|---------|-------------|
| `--url` | `https://shopee.co.id/buyer/login` | Login URL. |
| `--output` | `cookies.json` | Where to save cookies. |
| `--timeout` | 600 | Seconds to wait for manual login. |
| `--headless` | off | Run Camoufox headless (NOT recommended for SSO). |

### `python -m scripts.verify_goal`

| Flag | Default | Description |
|------|---------|-------------|
| `--shop` | (required) | Shop username to verify. |
| `--data-dir` | `data` | Data root. |
| `--min-products` | 1 | Min products required in `products.json`. |
| `--min-reviews` | 5 | Min reviews per file in `reviews/`. |
| `--min-image-size` | 1024 | Min image file size in bytes. |

## Installation

Dependencies are pre-installed in the global Python environment at
`~/.my_global_env/`. The `pip install -e .` step is mostly for the package
metadata (so `python -m scripts.X` works).

```bash
# clone or navigate to this project
cd /home/han/Desktop/Rumah/Computer\ Science/Playground/shopee-scraping/my-own

# install (editable, so scripts/scrape_shop.py is importable)
pip install -e .

# verify Camoufox + curl_cffi available
python -c "import camoufox, curl_cffi; print('OK')"
```

## Captcha handling

Shopee uses **Arkose Labs slider-puzzle captcha** (visible at the start of a
fresh session, or as a per-endpoint challenge). This scraper handles it by
**pausing and waiting for the user to solve manually** — there's no automated
solver, because:

- Shopee's `af-ac-enc-dat` SDK runs in a sandboxed iframe (hard to reverse)
- Per-endpoint captchas require fresh session context (can't be reused)
- Paid services like SadCaptcha exist ($3/1000 solves) but are not integrated

**Key insight:** captcha only appears on a *fresh session* (new tab, new
browser, lost cookies). If you keep the same browser open and just navigate
via `page.goto()`, captcha rarely re-triggers. The multi-shop mode
(`--shop A --shop B --shop C`) exploits this — one captcha solve at the
start, all subsequent shops use the same valid session.

When captcha appears, the script:

1. Detects `/verify/` URL or Arkose iframe
2. Prints a clear banner asking you to solve in the browser window
3. Polls every 1.5s for up to 5 minutes
4. Resumes automatically once you solve and the page redirects back

## Error snapshots

Any unhandled exception during scraping triggers a **mandatory snapshot**
before the browser closes:

```
tests/fixtures/debug/<timestamp>_<label>/
├── screenshot.png         (viewport)
├── screenshot_full.png    (full page)
├── page.html              (rendered DOM)
├── meta.json              (URL, title, body preview)
└── exception.txt          (full traceback)
```

This means you can always inspect what state the page was in when something
went wrong, even if the script exits with a traceback.

## Selectors (discovered empirically, 2026-06-22)

| Element | Selector |
|---------|----------|
| Product card link | `a[href*="-i."]` (relative or absolute) |
| Product name | `<title>` strip "Jual " + " \| Shopee Indonesia" |
| Description | `h2:has-text("Deskripsi Produk")` then sibling `<p>` |
| Variants (size) | `button.selection-box-unselected` (S/M/L/XL/XXL) |
| Main image | `img[srcset*="@resize_w900_nl"]` strip suffix |
| Reviews | children of `.shopee-product-comment-list` |
| Review author | `[class*="username"]` |
| Review stars | `.shopee-rating-stars__lit` count (clamp 0-5) |
| Review filter chip | `div.product-rating-overview__filter:has-text("...")` |
| Pagination | page number buttons (1, 2, 3, ...) + "..." to expand |

## Project structure

```
my-own/
├── pyproject.toml                # Package metadata
├── README.md                     # This file
├── cookies.json                  # Your Shopee session (gitignored)
├── data/<shop>/                  # Scraped output
├── shopee_dommie/                # Main package
│   ├── __init__.py
│   ├── models.py                 # ProductDetail, ProductSummary, Review, ShopInfo
│   ├── shop_page.py              # Shop page extractors
│   ├── product_page.py           # Product detail + review extractors
│   ├── captcha.py                # CaptchaDetector class
│   ├── images.py                 # curl_cffi image downloader
│   └── persistence.py            # Atomic JSON writers
├── scripts/                      # CLI entry points
│   ├── login.py                  # Manual login + cookie save
│   ├── scrape_shop.py            # Main scraping pipeline
│   ├── verify_goal.py            # Output structure checker
│   ├── smoke_browser.py          # DEV: smoke test for shop page
│   ├── smoke_product.py          # DEV: smoke test for product page
│   └── live_test_product.py      # DEV: live integration test
└── tests/                        # Saved HTML fixtures + debug snapshots
    └── fixtures/
        ├── shop_page.html        # Captured shop page
        ├── product_page.html     # Captured product page
        └── debug/                # Error snapshots
```

## Known limitations (v0.2.0)

- **Captcha is manual.** No automated solver. User must be present.
- **Gallery images: only main image.** Shopee lazy-loads additional
  thumbnails; clicking through them would yield 5-10 unique images but
  adds significant time. Skipped for v1.
- **Reviewer-uploaded images: not extracted.** Low priority, adds DOM traversal.
- **Stock count: best-effort.** Only extracted if "Stok" text is visible;
  many products skip this field.
- **Variants: names only.** Full variant data (price per SKU, stock per SKU)
  would require extra API calls. Not implemented.
- **ID region only.** Multi-region support is stubbed but not configured.

## Lessons learned (what took multiple debugging rounds)

1. **AsyncCamoufox takes `storage_state` in `new_context()`, not in launch.**
   The first attempt failed because we passed cookies to browser launch.
2. **`wait_until="domcontentloaded"` is too early.** Shopee renders the
   page shell, then React hydrates. Use `wait_until="load"` and explicitly
   wait for `div#main > *` to appear.
3. **Shopee's per-endpoint captcha fires AFTER the page shell loads.** A 5s
   grace period is unreliable — use continuous polling instead.
4. **Cookie detection is tricky.** `SPC_F` and `SPC_T_ID` are set by
   Shopee on the LOGIN page itself, not after login. The real signal is
   `SPC_EC` (encrypted session) or `SPC_U` (user ID).
5. **Indentation in Python async context managers is critical.** A single
   mis-indented line drops the entire body outside the `async with`,
   causing "browser closed" errors.
6. **Error snapshots must be inside the `async with`.** By the time the
   outer `try/except` runs, the browser is closed and `page.screenshot()`
   fails.
7. **Don't trust `.icon-rating-solid` for star counts** — it matches like
   icons too. Use `.shopee-rating-stars__lit` (specific to rating display,
   max 5 per review) and clamp to 0-5.

## License

Personal-use project. Respect Shopee's Terms of Service, don't hammer their
infrastructure (the built-in rate limiter is conservative), and consider the
impact on their servers. The author is not responsible for any account
actions resulting from misuse.
