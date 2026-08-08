// scripts/scrape-product-details.js
//
// For each product URL in the input JSONL, navigate to its detail page and
// extract: name, description, reviews[]. Anti-detection helpers shared via
// ./lib/anti-detection.js.
//
// Usage:
//   CDP_URL=ws://127.0.0.1:PORT/devtools/browser/ID \
//   PRODUCT_JSONL=./out/beliacosmetic-*.jsonl \
//   MAX_PRODUCTS=5 \
//   MAX_REVIEWS=5 \
//   node scripts/scrape-product-details.js

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright-core');
const {
  jitter, sleep, ts,
  naturalMove, microWiggle, idle, driftMouse,
  settleAndScroll,
} = require('./lib/anti-detection');

const CDP_URL = process.env.CDP_URL;
const PRODUCT_JSONL = process.env.PRODUCT_JSONL;
const MAX_PRODUCTS = parseInt(process.env.MAX_PRODUCTS || '5', 10);
const MAX_REVIEWS = parseInt(process.env.MAX_REVIEWS || '5', 10);
const OUTPUT_DIR = process.env.OUTPUT_DIR || path.join(__dirname, '..', 'out');

if (!CDP_URL) {
  console.error('[detail] FATAL: CDP_URL env required.');
  process.exit(1);
}
if (!PRODUCT_JSONL) {
  console.error('[detail] FATAL: PRODUCT_JSONL env required (path to JSONL with product_url field).');
  process.exit(1);
}

// === Connect + null guards (same pattern as the other scrapers) ===
async function connectPage(cdpUrl) {
  const browser = await chromium.connectOverCDP(cdpUrl, { timeout: 30_000 });
  const context = browser.contexts()[0];
  if (!context) return { browser, context: null, page: null };
  const page = context.pages()[0] || (await context.newPage());
  return { browser, context, page };
}

// === Description extraction (Shopee ID: div.MQQIj8) ===
async function extractDescription(page) {
  return await page.evaluate(() => {
    const el = document.querySelector('div.MQQIj8');
    if (!el) return null;
    return (el.innerText || '').trim();
  });
}

// === Reviews extraction (Shopee ID: div.product-ratings > grandchild divs) ===
//
// Structure discovered empirically on a Belia detail page:
//   div.product-ratings
//     div.z5hxuO (whole review list)
//       div.PuveAH (review 1, hash class changes per build)
//       div.ltWzqN (review 2)
//       ...
//
// We use a content-based filter instead of relying on the hash classes —
// find any descendant that starts with a username line + has "Variasi:" but
// stays under ~1500 chars (skipping the wrapper that has all reviews).
async function extractReviews(page, maxReviews) {
  return await page.evaluate((max) => {
    function parseReviewText(text) {
      const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);
      if (lines.length < 3) return null;
      const out = {
        username: lines[0] || null,
        variation: null,
        rating_dimension: null,
        comment: null,
        date: null,
        helpful: null,
      };
      // Walk lines and capture known patterns.
      const dateRe = /^\d{2}-\d{2}-\d{4}/;
      for (const line of lines) {
        if (line.startsWith('Variasi:')) out.variation = line.slice('Variasi:'.length).trim();
        else if (/^(Kualitas|Cocok Untuk|Aroma|Kegunaan|Efektivitas|Warna|Tekstur|Ukuran):/.test(line)) {
          out.rating_dimension = (out.rating_dimension || '') + (out.rating_dimension ? '; ' : '') + line;
        } else if (dateRe.test(line)) {
          out.date = line;
        } else if (/^\(\d+\)$/.test(line)) {
          out.helpful = parseInt(line.slice(1, -1), 10);
        }
      }
      // Comment = everything that's left over (between username and Lihat Lainnya / date).
      const lihatIdx = lines.findIndex((l) => l === 'Lihat Lainnya');
      const dateIdx = lines.findIndex((l) => dateRe.test(l));
      const endIdx = (lihatIdx >= 0 ? lihatIdx : (dateIdx >= 0 ? dateIdx : lines.length));
      const commentLines = lines
        .slice(1, endIdx)
        .filter((l) => !l.startsWith('Variasi:'))
        .filter((l) => !/^(Kualitas|Cocok Untuk|Aroma|Kegunaan|Efektivitas|Warna|Tekstur|Ukuran):/.test(l));
      out.comment = commentLines.join(' ').trim() || null;
      return out;
    }

    const section = document.querySelector('div.product-ratings');
    if (!section) return [];
    const all = Array.from(section.querySelectorAll('div'));
    const reviews = [];
    for (const el of all) {
      const t = (el.innerText || '').trim();
      if (
        t.length > 80 &&
        t.length < 1500 &&
        t.includes('Variasi:') &&
        !t.includes('Penilaian Produk')  // skip the section wrapper
      ) {
        const parsed = parseReviewText(t);
        if (parsed && parsed.username) reviews.push(parsed);
        if (reviews.length >= max) break;
      }
    }
    return reviews;
  }, maxReviews);
}

// === Stream-flush helper used by all live scrapers in this project ===
function flushExit(outStream, exitCode) {
  return new Promise((resolve) => {
    outStream.on('error', () => resolve());
    outStream.on('finish', () => resolve());
    outStream.end();
  }).then(() => process.exit(exitCode));
}

// === Main loop ===
(async () => {
  if (!fs.existsSync(PRODUCT_JSONL)) {
    console.error(`[detail] FATAL: PRODUCT_JSONL file not found: ${PRODUCT_JSONL}`);
    process.exit(1);
  }
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  // Read input products.
  const products = [];
  for (const line of fs.readFileSync(PRODUCT_JSONL, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    try { products.push(JSON.parse(line)); } catch (_) {}
  }
  console.log(`[detail] input products:    ${products.length}`);
  console.log(`[detail] MAX_PRODUCTS:       ${MAX_PRODUCTS}`);
  console.log(`[detail] MAX_REVIEWS/product:${MAX_REVIEWS}`);

  const outPath = path.join(OUTPUT_DIR, `product-details-${ts()}.jsonl`);
  const outStream = fs.createWriteStream(outPath, { flags: 'a' });
  console.log(`[detail] output:            ${outPath}`);

  const { context, page } = await connectPage(CDP_URL);
  if (!context || !page) {
    console.error('[detail] FATAL: CDP target has no context/page.');
    await flushExit(outStream, 1);
    return;
  }

  let totalReviews = 0;
  let totalDescChars = 0;
  let fatalErr = null;
  let succeeded = 0;

  try {
    for (let i = 0; i < Math.min(MAX_PRODUCTS, products.length); i++) {
      const p = products[i];
      const url = (p.product_url || '').startsWith('/')
        ? 'https://shopee.co.id' + p.product_url
        : p.product_url;
      console.log(`\n[detail] [${i + 1}/${Math.min(MAX_PRODUCTS, products.length)}] ${p.name?.slice(0, 60)}...`);
      console.log(`         ${url?.slice(0, 80)}...`);

      if (!url) {
        console.log('         SKIP — no product_url');
        continue;
      }

      await driftMouse(page);
      await sleep(jitter(1500, 3500));

      try {
        const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60_000 });
        console.log(`         status: ${resp?.status() ?? 'undef'}`);
        await idle(page, jitter(1500, 2500));
        // Scroll progressively so lazy sections (description, reviews) mount.
        for (let s = 0; s < 10; s++) {
          await page.evaluate((frac) => window.scrollTo(0, document.body.scrollHeight * frac), s / 9);
          await sleep(jitter(300, 600));
          await microWiggle(page);
        }
        await settleAndScroll(page);

        const description = await extractDescription(page);
        const reviews = await extractReviews(page, MAX_REVIEWS);

        const record = {
          name: p.name || null,
          product_url: url,
          description: description || null,
          description_chars: description ? description.length : 0,
          reviews,
          reviews_count: reviews.length,
          source_shop: p.source_shop || 'beliacosmetic',
          extracted_at: new Date().toISOString(),
        };
        outStream.write(JSON.stringify(record) + '\n');
        succeeded++;
        totalReviews += reviews.length;
        totalDescChars += description ? description.length : 0;
        console.log(`         desc=${description ? description.length + ' chars' : 'NONE'}  reviews=${reviews.length}`);

        // Inter-product pause.
        if (i < Math.min(MAX_PRODUCTS, products.length) - 1) {
          const delay = jitter(2500, 4500);
          console.log(`         inter-product delay ${delay}ms ...`);
          const chunks = 3;
          const per = Math.floor(delay / chunks);
          for (let c = 0; c < chunks; c++) {
            await idle(page, per);
            if (c < chunks - 1) await microWiggle(page);
          }
        }
      } catch (innerErr) {
        console.log(`         per-product error: ${innerErr.message}`);
        if (/BLOCKED/.test(innerErr.message)) throw innerErr;
        // Non-block errors: log and continue with next product.
      }
    }
  } catch (err) {
    if (err && /BLOCKED/.test(err.message)) console.error('[detail] BLOCKED — anti-bot redirect. Stopping.');
    else fatalErr = err;
  }

  console.log('\n[detail] done.');
  console.log(`[detail] products processed:  ${succeeded}/${Math.min(MAX_PRODUCTS, products.length)}`);
  console.log(`[detail] total reviews:       ${totalReviews}`);
  console.log(`[detail] total desc chars:    ${totalDescChars}`);
  console.log(`[detail] output:              ${outPath}`);
  if (fatalErr) {
    console.error('[detail] FATAL:', fatalErr.message);
    await flushExit(outStream, 1);
    return;
  }
  await flushExit(outStream, 0);
})();