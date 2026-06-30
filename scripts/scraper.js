// scripts/scraper.js
//
// Production scraper for Shopee /search pages. Connects to AdsPower-launched
// profile via CDP, paginates through keyword results, extracts per-product
// fields, writes JSON Lines to ./out/<keyword>-<timestamp>.jsonl.
//
// Anti-detection layer is provided by ./lib/anti-detection.js (shared with
// scrape-beliacosmetic.js and capture-pages.js).
//
// Usage:
//   CDP_URL=ws://127.0.0.1:62717/devtools/browser/... \
//   KEYWORD=laptop \
//   MAX_PAGES=3 \
//   node scripts/scraper.js

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');
const { parseCard } = require('./lib/parse-card');
const {
  jitter, sleep, ts,
  naturalMove, microWiggle, idle,
  naturalScroll, settleAndScroll, driftMouse,
} = require('./lib/anti-detection');

const CDP_URL = process.env.CDP_URL;
const KEYWORD = process.env.KEYWORD || 'laptop';
const MAX_PAGES = parseInt(process.env.MAX_PAGES || '3', 10);
const OUTPUT_DIR = process.env.OUTPUT_DIR || path.join(__dirname, '..', 'out');
const INTER_DELAY_MIN = parseInt(process.env.INTER_DELAY_MS_MIN || '4000', 10);
const INTER_DELAY_MAX = parseInt(process.env.INTER_DELAY_MS_MAX || '8000', 10);

if (!CDP_URL) {
  console.error('[scraper] FATAL: CDP_URL env required (e.g. ws://127.0.0.1:PORT/devtools/browser/ID)');
  process.exit(1);
}

// Connect to CDP and return a usable {context, page} or null if either is missing.
async function connectPage(cdpUrl) {
  const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 30_000 });
  const context = browser.contexts()[0];
  if (!context) return { browser, context: null, page: null };
  const page = context.pages()[0] || (await context.newPage());
  return { browser, context, page };
}

// Run an async IIFE and route both happy-path and error-path through the same
// outStream flush before exit. Previous version had a stream-flush race where
// the 'finish' listener was attached AFTER .end() and could miss the event.
function withFlushExit(outStream, exitCode) {
  return new Promise((resolve) => {
    outStream.on('error', (err) => {
      console.error('[scraper] stream error:', err.message);
      resolve();
    });
    outStream.on('finish', () => resolve());
    outStream.end();
  }).then(() => process.exit(exitCode));
}

// Open the listed search card on the current page and return the data shape
// that parseCard (and its consumers) expect.
async function extractCards(page) {
  return await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('[data-sqe="item"]'));
    return cards
      .map((c) => {
        const cardDiv = c.querySelector('div[role="group"][aria-label^="Product card:"]');
        const ariaLabel = cardDiv?.getAttribute('aria-label') || null;
        const url = c.querySelector('a[href*="-i."]')?.href?.split('?')[0] || null;
        return {
          name_from_aria: ariaLabel ? ariaLabel.replace(/^Product card:\s*/, '').trim() : null,
          product_url: url,
          innerText: c.innerText || '',
        };
      })
      .filter((c) => c.name_from_aria && c.innerText && c.innerText.trim().length > 5);
  });
}

(async () => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const outPath = path.join(OUTPUT_DIR, `${KEYWORD}-${ts()}.jsonl`);
  const outStream = fs.createWriteStream(outPath, { flags: 'a' });

  console.log('[scraper] CDP_URL     =', CDP_URL);
  console.log('[scraper] KEYWORD     =', KEYWORD);
  console.log('[scraper] MAX_PAGES   =', MAX_PAGES);
  console.log('[scraper] output      =', outPath);

  const { context, page } = await connectPage(CDP_URL);
  if (!context || !page) {
    console.error('[scraper] FATAL: CDP target has no context/page. Is the AdsPower profile open?');
    await withFlushExit(outStream, 1);
    return;
  }

  let totalExtracted = 0;
  let totalBlocked = 0;
  let fatalErr = null;

  try {
    for (let pageNum = 1; pageNum <= MAX_PAGES; pageNum++) {
      const url = `https://shopee.co.id/search?keyword=${encodeURIComponent(KEYWORD)}&page=${pageNum}`;
      console.log(`\n[scraper] page ${pageNum}/${MAX_PAGES}: ${url}`);

      await driftMouse(page);
      await sleep(jitter(1500, 3500));

      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
      console.log(`[scraper] status: ${resp?.status() ?? 'undefined'}`);

      await settleAndScroll(page);

      if (/\/verify\/traffic\/error/.test(page.url())) {
        console.log(`[scraper] BLOCKED on page ${pageNum} — anti-bot redirect. Stopping.`);
        totalBlocked++;
        break;
      }

      const realItems = await extractCards(page);
      const parsed = realItems.map((c) => parseCard(c, {
        source_shop: 'shopee-search',
        category: KEYWORD,
        page_number: pageNum,
      }));
      for (const it of parsed) outStream.write(JSON.stringify(it) + '\n');
      totalExtracted += parsed.length;
      console.log(`[scraper] extracted ${parsed.length} products on page ${pageNum}. total=${totalExtracted}`);

      if (pageNum < MAX_PAGES) {
        // Idle between pages so the inter-page delay looks like a human reading.
        const delay = jitter(INTER_DELAY_MIN, INTER_DELAY_MAX);
        console.log(`[scraper] inter-page delay ${delay}ms (with idle mouse activity) ...`);
        const chunks = 3;
        const per = Math.floor(delay / chunks);
        for (let i = 0; i < chunks; i++) {
          await idle(page, per);
          if (i < chunks - 1) await microWiggle(page);
        }
      }
    }
  } catch (err) {
    fatalErr = err;
  }

  // Log final stats FIRST so they appear even if process.exit races the stream.
  console.log('\n[scraper] done.');
  console.log(`[scraper] total extracted: ${totalExtracted}`);
  console.log(`[scraper] blocks hit:     ${totalBlocked}`);
  console.log(`[scraper] output:         ${outPath}`);
  if (fatalErr) {
    console.error('[scraper] FATAL:', fatalErr.message);
    if (fatalErr.stack) console.error(fatalErr.stack.split('\n').slice(0, 4).join('\n'));
    await withFlushExit(outStream, 1);
    return;
  }
  await withFlushExit(outStream, 0);
})();