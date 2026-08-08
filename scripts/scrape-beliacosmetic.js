// scripts/scrape-beliacosmetic.js
//
// Per the user's explicit instruction we use CLICK (sidebar anchor + pagination
// element) instead of page.goto(). Each click is preceded/followed by jitter
// delay + mouse drift so behaviour looks human.
//
// Workflow per category:
//   - click sidebar anchor matching the category name
//   - wait for products to render
//   - extract page 1 of products
//   - click next-page element (BUTTON or A with role=button styling)
//   - wait, extract, repeat up to MAX_PAGES
//
// Configure via env:
//   CDP_URL         required
//   STORE_URL       default https://shopee.co.id/beliacosmetic
//   CATEGORIES      default "SKINCARE - SERUM & ESSENCE,SKINCARE - CLEANSER"
//   MAX_PAGES       default 3
//   OUTPUT_DIR      default ./out
//   INTER_DELAY_MS_MIN/MAX  page-to-page delay defaults 4000/8000

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');
const { parseCard } = require('./lib/parse-card');
const {
  jitter, sleep, ts,
  naturalMove, microWiggle, idle, hoverAt, driftMouse,
  settleAndScroll,
  measureAfterScroll, findByText,
} = require('./lib/anti-detection');

const CDP_URL = process.env.CDP_URL;
const STORE_URL = process.env.STORE_URL || 'https://shopee.co.id/beliacosmetic';
const SHOP_NAME = 'Belia Cosmetic';
const SHOP_HANDLE = 'beliacosmetic';
const CATEGORIES = (process.env.CATEGORIES
  || 'SKINCARE - SERUM & ESSENCE,SKINCARE - CLEANSER'
).split(',').map((s) => s.trim()).filter(Boolean);
const MAX_PAGES = parseInt(process.env.MAX_PAGES || '3', 10);
const OUTPUT_DIR = process.env.OUTPUT_DIR || path.join(__dirname, '..', 'out');
const INTER_DELAY_MIN = parseInt(process.env.INTER_DELAY_MS_MIN || '4000', 10);
const INTER_DELAY_MAX = parseInt(process.env.INTER_DELAY_MS_MAX || '8000', 10);

if (!CDP_URL) {
  console.error('[belia] FATAL: CDP_URL env required.');
  process.exit(1);
}

// Connect to CDP with null guards — the previous version crashed with
// "Cannot read properties of undefined (reading 'pages')" if the AdsPower
// profile was mid-launch when the script ran.
async function connectPage(cdpUrl) {
  const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 30_000 });
  const context = browser.contexts()[0];
  if (!context) return { browser, context: null, page: null };
  const page = context.pages()[0] || (await context.newPage());
  return { browser, context, page };
}

// Find the "Produk" tab — it is an <a> for /shop pages, with an optional
// "New" badge (innerText becomes "Produk\nNew"). Returns the centered
// viewport coords so the caller can move the mouse there.
async function findTabCoords(page, label) {
  return await findByText(
    page,
    'a, button, [role="tab"], div, span',
    (el) => {
      const t = (el.innerText || '').trim();
      return t === label || t === label + '\nNew' || t.startsWith(label + '\n');
    },
  ).then(async (el) => {
    if (!el) return null;
    return await page.evaluate((e) => {
      const r = e.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }, el);
  });
}

// Click with hover-pause + post-click drift. Locates by CSS + text predicate,
// scrolls into view, hovers, then clicks. Falls back to direct .click() via
// the locator (handles SPA DOM updates where hover coords went stale).
async function clickByTextHumanly(page, selector, textPredicate) {
  await microWiggle(page);
  await measureAfterScroll(page, selector.replace(/.*\s/, '')); // best-effort scroll into view
  const el = await findByText(page, selector, textPredicate);
  if (!el) return { ok: false };

  // hover-then-click: scroll into view via Playwright's locator (more robust
  // than our scrollIntoView for SPAs that lazy-render).
  await el.scrollIntoViewIfNeeded().catch(() => {});
  const box = await el.boundingBox();
  if (!box) return { ok: false };
  const cx = box.x + box.width / 2 + (Math.random() - 0.5) * Math.min(8, box.width * 0.3);
  const cy = box.y + box.height / 2 + (Math.random() - 0.5) * Math.min(8, box.height * 0.3);
  await hoverAt(page, cx, cy);
  await sleep(jitter(80, 220));
  await el.click();
  await microWiggle(page);
  return { ok: true };
}

// Products on Shopee shop pages render with the stable BEM class
// `.shop-search-result-view__item.col-xs-2-4`. The previous
// `[class*="hover:shadow-hover"]` selector also matched product cards in
// the "Kamu Mungkin Suka" recommendation carousel above the main grid
// (6 duplicates per page). User-provided selector is authoritative for the
// main product grid only.
const PRODUCT_SELECTOR = '.shop-search-result-view__item.col-xs-2-4';

async function extractCards(page) {
  return await page.evaluate((sel) => {
    let cards = Array.from(document.querySelectorAll(sel));
    if (cards.length === 0) {
      // Last-resort fallback: scan for an element with a direct-child product
      // anchor. O(N) over all DOM nodes — guarded by child-count + text
      // length to keep noise low. Prefer the hover:shadow-hover selector.
      cards = Array.from(document.querySelectorAll('*')).filter((el) => {
        if (el.children.length === 0 || el.children.length > 40) return false;
        const hasProductLink = !!el.querySelector(':scope > a[href*="-i."]');
        if (!hasProductLink) return false;
        const text = (el.innerText || '').trim();
        return /\bRp\b/.test(text) && text.length > 5 && text.length < 800;
      });
    }
    return cards
      .map((c) => {
        const cardDiv = c.querySelector('div[role="group"][aria-label^="Product card:"]');
        const ariaLabel = cardDiv?.getAttribute('aria-label') || null;
        const url = c.querySelector('a[href*="-i."]')?.href?.split('?')[0] || null;
        const innerLines = (c.innerText || '').split('\n').map((l) => l.trim()).filter(Boolean);
        const nameFromAria = ariaLabel ? ariaLabel.replace(/^Product card:\s*/, '').trim() : null;
        return {
          name_from_aria: nameFromAria || innerLines[0] || null,
          product_url: url,
          innerText: c.innerText || '',
        };
      })
      .filter((c) => c.name_from_aria && c.innerText && c.innerText.trim().length > 5);
  }, PRODUCT_SELECTOR);
}

// Find the next-page element. Shopee renders pagination as <button> on most
// skins but <a> with role=button styling on others; we accept either, and
// also accept links whose text content is exactly the next page number.
async function findNextPageClickable(page, pageNum) {
  const target = String(pageNum + 1);
  return await page.evaluate((t) => {
    const isClickable = (el) => el.tagName === 'BUTTON' || el.tagName === 'A';
    const visible = (el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const match = Array.from(document.querySelectorAll('button, a'))
      .find((el) => isClickable(el) && visible(el) && ((el.innerText || '').trim() === t));
    if (!match) return null;
    const r = match.getBoundingClientRect();
    return { tag: match.tagName, x: r.left + r.width / 2, y: r.top + r.height / 2, label: target };
  }, target);
}

async function clickNextPage(page, pageNum) {
  const info = await findNextPageClickable(page, pageNum);
  if (!info) return { ok: false };
  await hoverAt(page, info.x, info.y);
  await sleep(jitter(80, 220));
  await page.evaluate((t) => {
    const isClickable = (el) => el.tagName === 'BUTTON' || el.tagName === 'A';
    const match = Array.from(document.querySelectorAll('button, a'))
      .find((el) => isClickable(el) && ((el.innerText || '').trim() === t));
    if (match) match.click();
  }, String(pageNum + 1));
  await microWiggle(page);
  return { ok: true, info };
}

// Flush outStream and exit; called on both happy and error paths.
function flushExit(outStream, exitCode) {
  return new Promise((resolve) => {
    outStream.on('error', () => resolve());
    outStream.on('finish', () => resolve());
    outStream.end();
  }).then(() => process.exit(exitCode));
}

(async () => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  const outPath = path.join(OUTPUT_DIR, `beliacosmetic-${ts()}.jsonl`);
  const outStream = fs.createWriteStream(outPath, { flags: 'a' });

  console.log('[belia] CDP_URL    =', CDP_URL);
  console.log('[belia] STORE_URL  =', STORE_URL);
  console.log('[belia] CATEGORIES =', CATEGORIES);
  console.log('[belia] MAX_PAGES  =', MAX_PAGES);
  console.log('[belia] output     =', outPath);

  const { context, page } = await connectPage(CDP_URL);
  if (!context || !page) {
    console.error('[belia] FATAL: CDP target has no context/page. Is the AdsPower profile open?');
    await flushExit(outStream, 1);
    return;
  }

  let blocked = false;
  let grandTotal = 0;
  let fatalErr = null;

  try {
    // ----- STEP A: open store -----
    console.log('\n[belia] STEP A: open store');
    await idle(page, jitter(1500, 3500));
    await page.goto(STORE_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await idle(page, jitter(1200, 2200));
    await settleAndScroll(page);
    if (/\/verify\/traffic\/error/.test(page.url())) {
      throw new Error('BLOCKED on store page');
    }

    // ----- STEP B: click "Produk" tab -----
    console.log('\n[belia] STEP B: click Produk tab');
    await idle(page, jitter(800, 1800));
    await microWiggle(page);
    const produkCoords = await findTabCoords(page, 'Produk');
    if (!produkCoords) {
      throw new Error('Produk tab not found');
    }
    await hoverAt(page, produkCoords.x, produkCoords.y);
    await sleep(jitter(80, 220));
    await page.evaluate(() => {
      const all = Array.from(document.querySelectorAll('a, button, [role="tab"], div, span'));
      const produk = all.find((el) => {
        const t = (el.innerText || '').trim();
        return t === 'Produk' || t === 'Produk\nNew' || t.startsWith('Produk\n');
      });
      if (produk) produk.click();
    });
    await microWiggle(page);
    await idle(page, jitter(2200, 3800));
    await settleAndScroll(page);

    // ----- STEP C: for each target category, click sidebar + paginate -----
    for (const catName of CATEGORIES) {
      console.log(`\n[belia] STEP C: open category "${catName}"`);
      await idle(page, jitter(700, 1500));
      await microWiggle(page);

      const catResult = await page.evaluate((name) => {
        const target = name.toLowerCase();
        const anchors = Array.from(document.querySelectorAll('a.navbar-with-more-menu__item, a[class*="navbar-with-more-menu"]'));
        const match = anchors.find((a) => (a.innerText || '').toLowerCase().includes(target));
        if (!match) return { ok: false, totalCandidates: anchors.length };
        match.scrollIntoView({ block: 'center', behavior: 'smooth' });
        return new Promise((resolve) => setTimeout(() => {
          const r = match.getBoundingClientRect();
          resolve({
            ok: true,
            href: match.href,
            text: (match.innerText || '').trim().slice(0, 50),
            x: r.left + r.width / 2,
            y: r.top + r.height / 2,
          });
        }, 700));
      }, catName);

      console.log('[belia] category location:', catResult);
      if (!catResult.ok) {
        console.log('[belia] category not found in sidebar — skipping.');
        continue;
      }

      await hoverAt(page, catResult.x, catResult.y);
      await sleep(jitter(80, 220));
      await page.evaluate((name) => {
        const target = name.toLowerCase();
        const anchors = Array.from(document.querySelectorAll('a.navbar-with-more-menu__item, a[class*="navbar-with-more-menu"]'));
        const match = anchors.find((a) => (a.innerText || '').toLowerCase().includes(target));
        if (match) match.click();
      }, catName);
      await microWiggle(page);

      await idle(page, jitter(2200, 3800));
      await settleAndScroll(page);

      if (/\/verify\/traffic\/error/.test(page.url())) {
        throw new Error('BLOCKED after category click');
      }

      const ctxRecord = {
        shop_name: SHOP_NAME,
        shop_handle: SHOP_HANDLE,
        category: catName,
        source_shop: SHOP_HANDLE,
      };

      for (let pageNum = 1; pageNum <= MAX_PAGES; pageNum++) {
        console.log(`\n[belia]   page ${pageNum}/${MAX_PAGES} in category "${catName}"`);

        const items = await extractCards(page);
        const parsed = items.map((c) => parseCard(c, { ...ctxRecord, page_number: pageNum }));
        for (const it of parsed) outStream.write(JSON.stringify(it) + '\n');
        grandTotal += parsed.length;
        console.log(`[belia]   extracted ${parsed.length}, grandTotal=${grandTotal}`);

        if (pageNum < MAX_PAGES) {
          await idle(page, jitter(1800, 3000));
          await microWiggle(page);
          const clicked = await clickNextPage(page, pageNum);
          console.log('[belia]   pagination click:', clicked);
          if (!clicked.ok) {
            console.log('[belia]   no more pagination elements visible — stopping.');
            break;
          }
          const delay = jitter(INTER_DELAY_MIN, INTER_DELAY_MAX);
          console.log(`[belia]   inter-page delay ${delay}ms (with idle mouse activity) ...`);
          const chunks = 3;
          const per = Math.floor(delay / chunks);
          for (let i = 0; i < chunks; i++) {
            await idle(page, per);
            if (i < chunks - 1) await microWiggle(page);
          }
        }
      }
    }
  } catch (err) {
    if (err && /BLOCKED/.test(err.message)) blocked = true;
    else fatalErr = err;
  }

  console.log('\n[belia] done.');
  console.log(`[belia] grand total: ${grandTotal}`);
  console.log(`[belia] blocks:      ${blocked ? 1 : 0}`);
  console.log(`[belia] output:      ${outPath}`);
  if (fatalErr) {
    console.error('[belia] FATAL:', fatalErr.message);
    if (fatalErr.stack) console.error(fatalErr.stack.split('\n').slice(0, 4).join('\n'));
    await flushExit(outStream, 1);
    return;
  }
  await flushExit(outStream, 0);
})();