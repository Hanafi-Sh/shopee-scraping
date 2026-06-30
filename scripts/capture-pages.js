// scripts/capture-pages.js
//
// Walks through category × page combinations, saves the rendered HTML to
// ./html-pages/ so we can iterate the parser offline.
//
// Trade-off vs. scraper.js: still has anti-bot exposure during capture (every
// page navigation is one browser round-trip), but EVERYTHING after capture is
// offline. Once the HTML is on disk you can:
//   - tweak parseCard logic in scripts/lib/parse-card.js without re-fetching
//   - run parse-offline.js repeatedly
//   - diff Shopee markup across captures to detect layout changes
//
// Anti-detection layer: same naturalMove / microWiggle / idle / hoverAt /
// clickHumanly stack as scrape-beliacosmetic.js. We don't reuse the live
// scraper because we don't want the side effect of writing JSONL during
// capture; capture mode is intentionally write-only-to-disk.
//
// Usage:
//   CDP_URL=ws://... \
//   CATEGORIES="SKINCARE - SERUM & ESSENCE,SKINCARE - CLEANSER" \
//   MAX_PAGES=2 \
//   node scripts/capture-pages.js

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');
const {
  jitter, sleep, ts, slugify,
  naturalMove, microWiggle, idle, hoverAt,
  settleAndScroll,
} = require('./lib/anti-detection');

const CDP_URL = process.env.CDP_URL;
const STORE_URL = process.env.STORE_URL || 'https://shopee.co.id/beliacosmetic';
const CATEGORIES = (process.env.CATEGORIES
  || 'SKINCARE - SERUM & ESSENCE,SKINCARE - CLEANSER'
).split(',').map((s) => s.trim()).filter(Boolean);
const MAX_PAGES = parseInt(process.env.MAX_PAGES || '2', 10);
const OUTPUT_DIR = process.env.OUTPUT_DIR || path.join(__dirname, '..', 'html-pages');

if (!CDP_URL) {
  console.error('[capture] FATAL: CDP_URL env required.');
  process.exit(1);
}

// ---- Save logic ----
function saveHtml(outputDir, catSlug, pageNum, url, html) {
  const filename = path.join(outputDir, `${catSlug}-page${pageNum}.html`);
  fs.writeFileSync(filename, html, 'utf8');
  const meta = {
    category_slug: catSlug,
    page: pageNum,
    url,
    captured_at: new Date().toISOString(),
    bytes: Buffer.byteLength(html, 'utf8'),
  };
  fs.writeFileSync(filename + '.json', JSON.stringify(meta, null, 2));
  return { filename, bytes: meta.bytes };
}

(async () => {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  console.log('[capture] CDP_URL    =', CDP_URL);
  console.log('[capture] STORE_URL  =', STORE_URL);
  console.log('[capture] CATEGORIES =', CATEGORIES);
  console.log('[capture] MAX_PAGES  =', MAX_PAGES);
  console.log('[capture] OUTPUT_DIR =', OUTPUT_DIR);

  const browser = await chromium.connectOverCDP(CDP_URL, { timeout: 30_000 });
  const context = browser.contexts()[0];
  if (!context) {
    console.error('[capture] FATAL: CDP target has no context. Is the AdsPower profile open?');
    process.exit(1);
  }
  const page = context.pages()[0];
  if (!page) {
    console.error('[capture] FATAL: CDP context has no page.');
    process.exit(1);
  }

  let blocked = false;

  // STEP A: open store
  console.log('\n[capture] STEP A: open store');
  await idle(page, jitter(1500, 3500));
  await page.goto(STORE_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await idle(page, jitter(1200, 2200));
  await settleAndScroll(page);
  if (/\/verify\/traffic\/error/.test(page.url())) {
    console.log('[capture] BLOCKED — aborting.');
    blocked = true;
    process.exit(0);
  }

  // STEP B: click Produk
  console.log('\n[capture] STEP B: click Produk tab');
  await idle(page, jitter(700, 1500));
  const produkOk = await page.evaluate(() => {
    const all = Array.from(document.querySelectorAll('a, button, [role="tab"], div, span'));
    const produk = all.find((el) => {
      const t = (el.innerText || '').trim();
      return t === 'Produk' || t === 'Produk\nNew' || t.startsWith('Produk\n');
    });
    if (!produk) return false;
    const r = produk.getBoundingClientRect();
    produk.dataset.__cx = String(r.left + r.width / 2);
    produk.dataset.__cy = String(r.top + r.height / 2);
    return true;
  });
  if (!produkOk) { console.log('[capture] Produk tab not found.'); process.exit(0); }
  await hoverAt(page,
    Number(await page.evaluate(() => document.querySelector('[data-__cx]')?.dataset.__cx || 720)),
    Number(await page.evaluate(() => document.querySelector('[data-__cx]')?.dataset.__cy || 450)),
  );
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

  let totalSaved = 0;

  // STEP C: walk categories and pages, save HTML
  for (const catName of CATEGORIES) {
    const catSlug = slugify(catName);
    console.log(`\n[capture] STEP C: open category "${catName}" (slug: ${catSlug})`);
    await idle(page, jitter(700, 1500));
    await microWiggle(page);

    const located = await page.evaluate((name) => {
      const target = name.toLowerCase();
      const anchors = Array.from(document.querySelectorAll('a.navbar-with-more-menu__item, a[class*="navbar-with-more-menu"]'));
      const match = anchors.find((a) => (a.innerText || '').toLowerCase().includes(target));
      if (!match) return { ok: false };
      match.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return new Promise((resolve) => setTimeout(() => {
        const r = match.getBoundingClientRect();
        resolve({ ok: true, href: match.href, text: (match.innerText || '').trim().slice(0, 50), x: r.left + r.width / 2, y: r.top + r.height / 2 });
      }, 700));
    }, catName);

    if (!located.ok) { console.log('[capture] category not found — skipping.'); continue; }

    await hoverAt(page, located.x, located.y);
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
      console.log('[capture] BLOCKED — stopping.');
      blocked = true;
      break;
    }

    for (let pageNum = 1; pageNum <= MAX_PAGES; pageNum++) {
      console.log(`[capture]   saving ${catSlug}-page${pageNum} ...`);

      // Capture full rendered HTML.
      const html = await page.content();
      const url = page.url();
      const { filename, bytes } = saveHtml(OUTPUT_DIR, catSlug, pageNum, url, html);
      console.log(`[capture]   wrote ${filename} (${(bytes/1024).toFixed(1)} KB)`);
      totalSaved++;

      // Click pagination for next page, unless we're at the last desired page.
      if (pageNum < MAX_PAGES) {
        await idle(page, jitter(1800, 3000));
        await microWiggle(page);

        const clicked = await page.evaluate((next) => {
          const btn = Array.from(document.querySelectorAll('button')).find(
            (b) => (b.innerText || '').trim() === String(next) && b.offsetParent !== null,
          );
          if (!btn) return { ok: false };
          btn.scrollIntoView({ block: 'center', behavior: 'smooth' });
          return new Promise((resolve) => setTimeout(() => {
            const r = btn.getBoundingClientRect();
            resolve({ ok: true, label: btn.innerText.trim(), x: r.left + r.width / 2, y: r.top + r.height / 2 });
          }, 700));
        }, pageNum + 1);
        if (!clicked.ok) {
          console.log('[capture]   no next-page button — stopping this category.');
          break;
        }
        await hoverAt(page, clicked.x, clicked.y);
        await sleep(jitter(80, 220));
        await page.evaluate((next) => {
          const btn = Array.from(document.querySelectorAll('button')).find(
            (b) => (b.innerText || '').trim() === String(next) && b.offsetParent !== null,
          );
          if (btn) btn.click();
        }, pageNum + 1);
        await microWiggle(page);
        const delay = jitter(4500, 7500);
        console.log(`[capture]   inter-page delay ${delay}ms (chunked idle)`);
        const chunks = 3;
        const per = Math.floor(delay / chunks);
        for (let i = 0; i < chunks; i++) {
          await idle(page, per);
          if (i < chunks - 1) await microWiggle(page);
        }
      }
    }
  }

  console.log('\n[capture] done.');
  console.log(`[capture] total saved: ${totalSaved}`);
  console.log(`[capture] blocks:      ${blocked ? 1 : 0}`);
  console.log(`[capture] output:      ${OUTPUT_DIR}`);
  process.exit(0);
})().catch((err) => {
  console.error('[capture] FATAL:', err.message);
  if (err.stack) console.error(err.stack.split('\n').slice(0, 4).join('\n'));
  process.exit(1);
});