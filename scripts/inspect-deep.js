// scripts/inspect-deep.js
//
// Deep inspection: dump ALL cards on page 1 with their innerText, every <a>
// href, and which cards the current parser would fail on. Tells us why 9%
// of cards have missing fields.

const { chromium } = require('playwright-core');
const { IND_PRICE } = require('./lib/parse-card');

const CDP_URL =
  process.env.CDP_URL ||
  'ws://127.0.0.1:62717/devtools/browser/d8cdd5cd-f84b-4785-8d92-99f68a69beda';

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

(async () => {
  const browser = await chromium.connectOverCDP(CDP_URL, { timeout: 30_000 });
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  if (!page.url().includes('/search')) {
    await page.goto('https://shopee.co.id/search?keyword=laptop', { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await sleep(2500);
  }
  await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight * 0.3, behavior: 'smooth' }));
  await sleep(800);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
  await sleep(800);

  const result = await page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll('[data-sqe="item"]'));

    function extractForCard(card) {
      const cardDiv = card.querySelector('div[role="group"][aria-label^="Product card:"]');
      const ariaLabel = cardDiv?.getAttribute('aria-label') || null;
      const name = ariaLabel ? ariaLabel.replace(/^Product card:\s*/, '').trim() : null;
      const url = card.querySelector('a[href*="-i."]')?.href?.split('?')[0] || null;

      // Distinct <a> targets — one of them is the shop URL.
      const allLinks = Array.from(card.querySelectorAll('a')).map((a) => a.href);
      const shopLink = allLinks.find((h) => /\/shop\//.test(h)) || null;
      const shopNameAria = card.querySelector('a[href*="/shop/"]')?.getAttribute('aria-label') || null;

      const text = (card.innerText || '').trim();
      const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);

      // Parser-style extractions. Use the same regexes as lib/parse-card.js
      // so this script's diagnostic counts match the real parser.
      const rpIdx = lines.findIndex((l) => l === 'Rp');
      let priceCurrent = null;
      if (rpIdx >= 0) {
        for (let i = rpIdx + 1; i < lines.length; i++) {
          if (IND_PRICE.test(lines[i])) { priceCurrent = lines[i]; break; }
        }
      }
      const soldMatch = text.match(/(\d+(?:[.,]\d+)?(?:RB\+?|rb\+?)?)\s*Terjual/i);
      const soldIdx = lines.findIndex((l) => /[Tt][Ee][Rr][Jj][Uu][Aa][Ll]$/.test(l));
      const rating = (soldIdx > 0 && /^\d\.\d{1,2}$/.test(lines[soldIdx - 1]))
        ? parseFloat(lines[soldIdx - 1]) : null;

      const similarIdx = lines.findIndex((l) => l === 'Produk Serupa');
      const location = (similarIdx > 0 && !/^Rp$|^[\d.,]+$|^-?\d+%$/.test(lines[similarIdx - 1]))
        ? lines[similarIdx - 1] : null;

      const isComplete = !!(name && url && priceCurrent && soldMatch && location && rating);
      return {
        hasName: !!name, hasUrl: !!url, hasPrice: !!priceCurrent,
        hasRating: rating !== null, hasSold: !!soldMatch, hasLocation: !!location,
        shopLink, shopNameAria,
        isComplete,
        lines,
      };
    }

    return cards.map(extractForCard);
  });

  // Aggregate diagnostic.
  const fails = result.filter((r) => !r.isComplete);
  console.log(`total cards: ${result.length}`);
  console.log(`complete (all 6 fields): ${result.length - fails.length}`);
  console.log(`incomplete: ${fails.length}`);
  console.log();
  if (fails.length > 0) {
    console.log('=== INCOMPLETE CARDS (showing first 5) ===');
    fails.slice(0, 5).forEach((f, i) => {
      console.log(`\n--- card fail #${i + 1} ---`);
      console.log('  hasName:', f.hasName, 'hasUrl:', f.hasUrl, 'hasPrice:', f.hasPrice,
        'hasRating:', f.hasRating, 'hasSold:', f.hasSold, 'hasLocation:', f.hasLocation);
      console.log('  shopLink:', f.shopLink);
      console.log('  shopNameAria:', f.shopNameAria);
      console.log('  lines:');
      f.lines.forEach((l, idx) => console.log(`    [${idx}] ${l}`));
    });
  }
  console.log('\n=== shop link coverage across ALL cards ===');
  const withShop = result.filter((r) => r.shopLink).length;
  const withShopAria = result.filter((r) => r.shopNameAria).length;
  console.log(`has /shop/ link: ${withShop}/${result.length}`);
  console.log(`shop link with aria-label: ${withShopAria}/${result.length}`);
  console.log('\nsample shop hrefs:');
  result.filter((r) => r.shopLink).slice(0, 5).forEach((r) => {
    console.log(`  - ${r.shopNameAria || '(no aria)'}  →  ${r.shopLink}`);
  });

  process.exit(0);
})().catch((err) => {
  console.error('[inspect-deep] FATAL:', err.message);
  process.exit(1);
});