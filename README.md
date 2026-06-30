# shopee-scraper

Scrape Shopee `/search` result pages using an AdsPower SunBrowser profile as the
execution environment, driven from Node.js via Playwright's CDP connect.

> **What this is**: a small, transparent scraper that leans heavily on AdsPower
> to handle browser fingerprinting and session isolation. Cookies / fingerprint
> / IP all live inside the profile; the scraper just navigates and extracts.
>
> **What this is not**: a full Shopee buyer-data pipeline, a CAPTCHA solver, or
> an account-rotator. Single profile, single login, single IP — within those
> limits it works for keyword research and small/mid volume extraction.

---

## How it works (architecture)

```
┌──────────────────────────────────────────────────────────────────┐
│  AdsPower app     localhost:50325 (LocalAPI HTTP)                │
│  ┌────────────────┐   POST /api/v2/browser-profile/start         │
│  │   SunBrowser   │   ←──────────────────┐                        │
│  │   profile UI   │                      │                        │
│  └────────────────┘                      │                        │
│         │ CDP ws://...                    │                        │
└─────────┼──────────────────────────────────┼───────────────────────┘
          │                                  │
          ▼                                  │
┌──────────────────────────────┐   start/profile ws URL via curl
│  Node.js (this repo)         │                       │
│  ┌────────────────────────┐  │   ◄───────────────────┘
│  │ scripts/scraper.js     │  │
│  │   chromium.connect     │  │
│  │   OverCDP(wsUrl)      │  │
│  └────────────────────────┘  │
│         │ behavior-sim        │
│         │ pre-nav delay       │
│         │ mouse drift         │
│         │ scroll 25→70→top    │
└─────────┼──────────────────────┘
          │ page.evaluate()
          ▼
┌──────────────────────────────┐
│  Shopee /search?keyword=...  │
│  renders in real Chromium 148│
│  with logged-in cookies     │
└──────────────────────────────┘
```

The scraper **does not launch** a Chromium instance — it connects to one that
AdsPower already started, so all fingerprint / cookie / TLS work is handled by
the AdsPower profile config and the running browser session.

---

## Prerequisites

- **macOS** (development) — `darwin` only because that's what we tested.
  Windows / Linux work in principle if AdsPower runs there.
- **Node.js 18+** (we develop on Node 26.4.0).
- **AdsPower app** — must be running with the Local API enabled
  (Settings → API → port `50325`). Get the API key from the same panel.
- **One AdsPower profile** — we use `k1e2mtxo` (named "Default Profile" in
  this repo). Fingerprint should match the host machine closely enough to
  avoid obvious mismatch signals.
- **A real Shopee login** in the profile. Anonymous sessions get blocked at
  deep navigation (anything past the homepage).

---

## Setup

```bash
# from this directory
npm install

# edit .mcp.json — replace API_KEY with your AdsPower LocalAPI key
# (the file is gitignored; never commit it)
```

If the profile does not yet have the right fingerprint, update it once:

```bash
ADS_API_KEY=...  # already in .mcp.json
curl -X POST http://localhost:50325/api/v2/browser-profile/update \
  -H "Authorization: Bearer $ADS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "profile_id":"YOUR_PROFILE_ID",
    "fingerprint_config":{
      "ua":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
      "automatic_timezone":"0",
      "timezone":"Asia/Jakarta",
      "language_switch":"0",
      "language":["id-ID","en-US","en"],
      "webrtc":"disabled",
      "canvas":"1","audio":"1","webgl_image":"1",
      "media_devices":"2",
      "media_devices_num":{"audioinput_num":"1","videoinput_num":"1","audiooutput_num":"1"},
      "client_rects":"1"
    }
  }'
```

Clear any prior cookies/cache before first run if the profile has been used for
other scraping (Shopee's reputation flag is sticky per-profile):

```bash
curl -X POST http://localhost:50325/api/v2/browser-profile/delete-cache \
  -H "Authorization: Bearer $ADS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"profile_id":["YOUR_PROFILE_ID"],"type":["cookie","history","indexeddb","local_storage","image_file","extension_cache"]}'
```

Then start the profile (capture the ws URL — you need it for every script):

```bash
curl -X POST http://localhost:50325/api/v2/browser-profile/start \
  -H "Authorization: Bearer $ADS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"profile_id":"YOUR_PROFILE_ID"}'
# → response.data.ws.puppeteer = "ws://127.0.0.1:PORT/devtools/browser/UUID"
```

**Manual login gate** — open the SunBrowser window that just spawned, log in
to Shopee once, leave it open. Cookies persist in profile storage across runs.

---

## Usage

All scripts accept `CDP_URL` from the environment. Set it once per shell.

### `npm run probe` — connectivity + behavior-sim smoke test

```bash
CDP_URL="ws://127.0.0.1:PORT/devtools/browser/UUID" \
  npm run probe
```

Navigates to `https://shopee.co.id`, applies the same anti-detection behavior
as the scraper, captures `body[0:600]`, `UA`, screenshot. Override target with
`TARGET_URL` and `SCREENSHOT_PATH` env vars.

```bash
CDP_URL=... TARGET_URL="https://shopee.co.id/search?keyword=laptop" \
  npm run probe
```

### `npm run scrape` — production scraper

```bash
CDP_URL=... \
  KEYWORD=laptop \
  MAX_PAGES=3 \
  npm run scrape
```

Output is JSON Lines at `./out/<KEYWORD>-<TIMESTAMP>.jsonl` (one product per line).

Env knobs:

| Var                  | Default | Purpose                                  |
|----------------------|---------|------------------------------------------|
| `CDP_URL`            | —       | **required.** ws URL from `start` API   |
| `KEYWORD`            | `laptop`| Search term                              |
| `MAX_PAGES`          | `3`     | Pagination depth                         |
| `OUTPUT_DIR`         | `./out` | Where to write JSONL                     |
| `INTER_DELAY_MS_MIN` | `4000`  | Min delay between pages (ms)             |
| `INTER_DELAY_MS_MAX` | `8000`  | Max delay between pages (ms)             |

### `npm run inspect` / `npm run inspect:deep`

DOM introspection aids when Shopee changes their markup. `inspect` shows the
shape of the first 3 cards including `aria-label` / `href` / unique classes;
`inspect:deep` aggregates coverage diagnostics and reports which fields fail
across all 60 cards on the first search page.

---

## Output format

```json
{
  "name": "Laptop Asus Vivobook Go 14 E410KA Intel Pentium Silver N6000 RAM 4GB 512GB SSD 14.0 FHD Win11Home",
  "product_url": "https://shopee.co.id/Laptop-Asus-Vivobook-Go-14-E410KA-...-i.933196574.19271889732",
  "price_current": "4.498.000",
  "price_original": null,
  "discount_pct": 1,
  "rating": 4.9,
  "sold_count_raw": "62",
  "location": "Kab. Bogor",
  "shop_name": null,
  "extracted_at": "2026-06-30T00:57:35.563Z"
}
```

Field nullability:

| Field             | Nullable | Why                                                     |
|-------------------|----------|---------------------------------------------------------|
| `name`            | never    | from `aria-label`                                       |
| `product_url`     | never    | from `a[href*="-i."]`                                   |
| `price_current`   | never    | from `Rp\n<number>` block, formatted `X.XXX.XXX`        |
| `price_original`  | often    | Shopee search cards don't show original price          |
| `discount_pct`    | often    | only when Shopee shows `-X%`                            |
| `rating`          | often    | new listings have no rating yet                         |
| `sold_count_raw`  | often    | new listings have no sold count yet; format: `"41"`, `"1RB+"`, `"289"` |
| `location`        | rarely   | last meaningful city line in innerText                  |
| `shop_name`       | always   | **not in card DOM** — see Limitations                   |

---

## Anti-detection — why each layer matters

Shopee uses multi-signal detection (UA fingerprint, TLS / HTTP/2 fingerprint,
session cookies, behavior velocity). Single-layer bypass is fragile. The
combination here was empirically validated against `https://shopee.co.id` and
`https://shopee.co.id/search?keyword=laptop` in June 2026:

| Layer            | What it does                                        | What it defeats           |
|------------------|-----------------------------------------------------|---------------------------|
| CDP connect      | Playwright is a *client*, doesn't launch the browser| `navigator.webdriver=true`|
| Profile FP       | macOS UA matching Chromium 148, Canvas/Audio noise   | Plain UA mismatch         |
| Cookies          | Persistent login in profile storage                 | Anonymous / "untrusted"   |
| Pre-nav delay    | Random 1.5–3.5s before `goto()`                     | Instant-request signature |
| Mouse drift      | Cursor parked near viewport center pre & post load  | Dead-cursor signature     |
| Scroll-around    | 25% → 70% → top post-load                           | Read-no-scroll signature  |
| Inter-page delay | 4–8s random between pages                            | Page-rate signature       |

**Observed** — before all layers: `verify/traffic/error` on every navigation.
After all layers: real Shopee results, page 1 + 2 pass with no blocks.

---

## Limitations

- **No `shop_name` per product** — Shopee's `/search` cards don't expose shop
  links (verified 0/60 cards have `/shop/` href). Options to fix later:
  - Call Shopee internal API `/api/v4/search/search_items` from Node (faster
    but subject to API-level anti-bot; needs proper headers + cookies).
  - Click each product → load detail page → scrape shop_name (1 page load per
    item, multiplies runtime).
  - Left as `null` for now.
- **Single profile / single IP** — no rotation. Shopee rate-limits per-profile
  / per-IP, so this caps how much you can extract in a session.
- **No CAPTCHA handling** — if Shopee serves a CAPTCHA during a session, the
  scraper will record the block and stop. CAPTCHA solving is out of scope.
- **No retry-back-off strategy beyond `MAX_PAGES`** — extending to many pages
  in one run risks accumulating Shopee traffic signals. For long extractions,
  prefer multiple short runs with profile restart + sleep between.
- **Disc format is `X.XXX.XXX`** — Indonesian shop uses that; other regions
  use different separators. Parser would need a region flag.

---

## Security

- `.mcp.json` contains the AdsPower LocalAPI key in plaintext. It's in
  `.gitignore` so commits won't leak it, but anyone with file access can read
  it. **Rotate the key after your debug session.**
- Don't commit the `out/` directory if it contains your real search history —
  keyword lists can be revealing.
- Don't reuse the same profile for both personal browsing and scraping — the
  cookies / fingerprint cross-contamination will trip Shopee's anti-bot faster
  than either activity alone.

---

## Files

| Path                          | Purpose                                                       |
|-------------------------------|---------------------------------------------------------------|
| `scripts/probe.js`            | Connectivity + behavior-sim smoke test (output PNG + stdout)  |
| `scripts/scraper.js`          | Production paginated scraper (writes JSONL)                   |
| `scripts/inspect-dom.js`      | Single-card DOM shape discovery                               |
| `scripts/inspect-deep.js`     | Coverage diagnostics + shop-link scan across all cards        |
| `.mcp.json`                   | MCP server config (gitignored)                                |
| `.gitignore`                  | Excludes `.mcp.json`, `out/`, `node_modules/`, screenshots    |
| `out/<KEYWORD>-*.jsonl`       | Scraped data                                                  |
| `.agents/skills/adspower-browser/` | Local install of the AdsPower skill (CLI + docs)        |

---

## Future work

- Hook Shopee's internal `/api/v4/search/search_items` to get `shop_name` and
  faster extraction.
- Multi-profile rotation with shared cookies pool.
- Optional proxy support — pass `PROXY_URL` env; update `user_proxy_config` on
  the profile via `update-browser`.
- Auto-relogin — script detects logged-out state and pauses for manual login.
- Output formats: CSV / SQLite / push to API.