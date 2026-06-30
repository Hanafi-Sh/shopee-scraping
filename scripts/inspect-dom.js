// scripts/inspect-dom.js
//
// Deeper inspection: pull name from aria-label, product URL from <a>, and dump
// the unique data-sqe attributes inside each card so we can build stable selectors.

const { chromium } = require('playwright-core');

const CDP_URL =
  process.env.CDP_URL ||
  'ws://127.0.0.1:62717/devtools/browser/d8cdd5cd-f84b-4785-8d92-99f68a69beda';

const TARGET_URL = process.env.TARGET_URL || 'https://shopee.co.id/search?keyword=laptop';

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

(async () => {
  const browser = await chromium.connectOverCDP(CDP_URL, { timeout: 30_000 });
  const context = browser.contexts()[0];
  const page = context.pages()[0];

  if (!page.url().includes('/search')) {
    console.log('[inspect] navigating to', TARGET_URL);
    await page.goto(TARGET_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await sleep(2000);
  }
  await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight * 0.3, behavior: 'smooth' }));
  await sleep(800);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
  await sleep(800);

  const result = await page.evaluate(() => {
    const cards = document.querySelectorAll('[data-sqe="item"]');

    // Walk each card and gather everything we might want.
    const items = [];
    for (let i = 0; i < Math.min(cards.length, 6); i++) {
      const card = cards[i];

      // 1. Name from aria-label of inner div that starts with "Product card:"
      const cardDiv = card.querySelector('div[role="group"][aria-label^="Product card:"]');
      const ariaLabel = cardDiv?.getAttribute('aria-label') || null;
      const name = ariaLabel ? ariaLabel.replace(/^Product card:\s*/, '').trim() : null;

      // 2. Product URL from <a> whose href matches /-i.<digits>.<digits>/
      const a = card.querySelector('a[href*="-i."]');
      const productUrl = a ? a.href.split('?')[0] : null; // strip tracking params

      // 3. All unique [data-sqe] attributes inside card with their text.
      const sqeItems = Array.from(card.querySelectorAll('[data-sqe]')).map((el) => ({
        key: el.getAttribute('data-sqe'),
        text: (el.innerText || '').trim().slice(0, 80),
        tag: el.tagName.toLowerCase(),
      }));

      // 4. All classes inside card that contain common Shopee field markers.
      const fieldClasses = new Set();
      card.querySelectorAll('[class*="price"], [class*="name"], [class*="sold"], [class*="rating"], [class*="shop-"], [class*="discount"]').forEach((el) => {
        el.className.split(/\s+/).forEach((c) => {
          if (/price|name|sold|rating|shop|discount/.test(c)) fieldClasses.add(c);
        });
      });

      items.push({
        idx: i,
        name,
        productUrl,
        sqeItems,
        fieldClasses: Array.from(fieldClasses).slice(0, 30),
        fullText: (card.innerText || '').slice(0, 400),
      });
    }

    return {
      totalCards: cards.length,
      items,
    };
  });

  console.log(JSON.stringify(result, null, 2));
  process.exit(0);
})().catch((err) => {
  console.error('[inspect] FATAL:', err.message);
  process.exit(1);
});