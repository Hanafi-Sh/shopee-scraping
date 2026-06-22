# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**shopee-dommie** — a Shopee shop scraper that drives [Camoufox](https://github.com/daijro/camoufox) (stealth Firefox) to extract product data from rendered DOM. It's a deliberate alternative to pure-HTTP scraping: Shopee's anti-bot (`af-ac-enc-dat` SDK, per-endpoint captcha, sz-token session binding) makes API-only scraping infeasible, so we render the page and parse what we see.

Captcha is solved **manually by the user** in the browser window (no paid solver integration). Multi-shop mode keeps a single browser session alive across N shops, so captcha solves once at the start and is valid for all subsequent navigations.

## Quick reference

```bash
# Install (editable, so `python -m scripts.X` works)
pip install -e .

# Required: ~/.my_global_env/bin/python3 (system has camoufox, curl-cffi, playwright pre-installed)
PYTHON=/home/han/.my_global_env/bin/python3

# First time: log in to Shopee (saves cookies.json)
$PYTHON -m scripts.login

# Run a single shop (default: 10 products, ALL reviews per product)
$PYTHON -m scripts.scrape_shop --shop erigostore

# Cap reviews per product for faster runs (e.g. 10 reviews = ~1 page)
$PYTHON -m scripts.scrape_shop --shop erigostore --max-reviews 10

# Run multiple shops in one browser session (1 captcha solve)
$PYTHON -m scripts.scrape_shop --shop erigostore --shop wardahofficial --shop adidasindonesia

# From a file (one shop per line, # = comment)
$PYTHON -m scripts.scrape_shop --shops-from-file top_shops.txt

# Interactive mode (no args — prompts for shops)
$PYTHON -m scripts.scrape_shop

# Verify output structure
$PYTHON -m scripts.verify_goal --shop erigostore

# One-shot: clean blank-line artifacts from existing descriptions
$PYTHON -m scripts.clean_descriptions [--dry-run]
```

## Module architecture

```
shopee_dommie/                  # importable package
├── models.py            # Dataclasses: ProductSummary, ProductDetail, Review, ShopInfo
├── shop_page.py         # Shop page extractors (product list, shop info)
├── product_page.py      # Product detail + review extractors
├── captcha.py           # CaptchaDetector class (URL + DOM polling)
├── images.py            # curl_cffi image downloader (Shopee CDN auth)
└── persistence.py       # Atomic JSON + CSV writers (tmp + rename)

scripts/                       # CLI entry points
├── scrape_shop.py        # Main pipeline (multi-shop, interactive, error snapshots)
├── login.py              # Manual login → cookies.json
├── verify_goal.py        # Output structure validator
├── clean_descriptions.py # Retro-clean blank lines in existing data
├── smoke_browser.py      # DEV: open shop page, save HTML fixture
├── smoke_product.py      # DEV: open product page, save HTML fixture
└── live_test_product.py  # DEV: full live integration test
```

Data flow per product: navigate → `CaptchaDetector.wait_if_captcha()` → DOM extract → download images via curl_cffi → atomic write (JSON + CSV).

## Output structure

```
data/<shop_username>/
├── shop_info.json           # one per shop
├── products.json            # list of {itemid, shopid, name, url, image}
├── products_enriched.json   # list of full ProductDetail
├── products_enriched.csv    # same data, flat (1 row per product, nested fields JSON-stringified)
├── reviews/<itemid>.json    # list of Review
├── reviews/<itemid>.csv     # same reviews, flat
└── images/<itemid>/NN.webp  # downloaded product images
```

`tests/fixtures/` has captured HTML + debug snapshots (from the `save_error_snapshot` mechanism, which runs on any unhandled exception).

## Selectors (discovered empirically, 2026-06-22 — Shopee may change them)

| Element | Selector |
|---|---|
| Product card link | `a[href*="-i."]` (relative or absolute) |
| Product name | `<title>` strip "Jual " + " \| Shopee Indonesia" |
| Description | `h2:has-text("Deskripsi Produk")` then sibling `<p>` (joined `\n`, NOT `\n\n`) |
| Variants (size) | `button.selection-box-unselected` (S/M/L/XL/XXL) |
| Main image | `img[srcset*="@resize_w900_nl"]` strip suffix |
| Reviews | children of `.shopee-product-comment-list` |
| Review author | `[class*="username"]` |
| Review stars | `.shopee-rating-stars__lit` count (clamp 0-5; ignore `.icon-rating-solid` — matches like icons too) |
| Review filter chip | `div.product-rating-overview__filter:has-text("...")` |
| Pagination | page number buttons (1, 2, 3, ...) + "..." to expand |

## Critical gotchas (took multiple debugging rounds — don't re-introduce)

1. **`SPC_EC` is the real session cookie, not `SPC_F` or `SPC_T_ID`.** Shopee sets `SPC_F` + `SPC_T_ID` on the LOGIN page itself (for tracking/anti-bot), before login completes. The post-login indicator is `SPC_EC` or `SPC_U`. Login script checks for these.

2. **`AsyncCamoufox` takes `storage_state` in `new_context()`, NOT in browser launch.** Camoufox's `AsyncCamoufox(**launch_options)` only takes browser launch options. Cookies go on `browser.new_context(storage_state=...)`.

3. **`wait_until="domcontentloaded"`** (not `"load"`) for product pages. The `"load"` event waits for ALL subresources (images, trackers) which can take 5-10s per page for Shopee. We only need HTML for DOM extraction.

4. **Error snapshots must be INSIDE the `async with` block.** Once `async with AsyncCamoufox` exits, the page is closed and `page.screenshot()` fails. Wrap the main pipeline in an inner try/except so `save_error_snapshot` runs while the page is still alive.

5. **Indentation in Python `async with` is load-bearing.** A single mis-indented line drops the body outside the `async with`, causing "Target page, context or browser has been closed" errors on the first `page.goto()`.

6. **`.icon-rating-solid` is NOT just stars** — it also matches like icons. Use `.shopee-rating-stars__lit` (clamped 0-5) for review star counts.

7. **Empty `<p></p>` between bullets in Shopee descriptions.** Originally joined with `\n\n` which produced ugly double-blank lines. Now join with `\n` after filtering empty `<p>`. `scripts/clean_descriptions.py` retro-fixes existing data.

8. **Camoufox sync API can't run inside `asyncio.run`.** If you see `Playwright Sync API inside the asyncio loop`, you're mixing sync `Camoufox(...)` with `asyncio.run`. Use one or the other, not both. `scripts/login.py` uses sync; `scripts/scrape_shop.py` uses async.

## Conventions

- **All file writes are atomic** — write to `<path>.tmp`, then `os.rename`. See `atomic_write_json` and `atomic_write_csv` in `persistence.py`. New writers should follow this.
- **CLI output is `print()`, library code uses functions returning data** — not `logging`. This is a single-user CLI tool; structured logging would be over-engineering.
- **Dataclasses are frozen** for value objects (immutability per `common/coding-style.md`).
- **No test suite.** Pure functions are smoke-tested inline. Browser-dependent code is verified via `scripts/smoke_*.py` scripts that save HTML fixtures. If you add complex logic, add inline smoke tests in `if __name__ == "__main__":` blocks.
- **Captcha is on the user, not the script.** `CaptchaDetector.wait_if_captcha()` is the gate. Don't add workarounds that try to bypass captcha (no paid solver, no ML, no stealth tricks beyond Camoufox itself).

## Multi-shop mode (key feature)

```bash
$PYTHON -m scripts.scrape_shop --shop A --shop B --shop C
```

Opens Camoufox once, loops through shops sequentially in the SAME tab, captcha solves once at the start. Per-shop errors are caught and saved as debug snapshots; the loop continues to the next shop rather than dying. This is much better than running N separate `scrape_shop` invocations, because each new browser session risks re-triggering captcha.

If `--shop` and `--shops-from-file` are both empty, the script enters interactive mode: prompts for shops one per line, comma-separated entries accepted, empty line ends input. Ctrl-D also works.
