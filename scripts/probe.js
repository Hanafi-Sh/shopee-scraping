// scripts/probe.js
//
// Anti-detection probe: connect Playwright via CDP to AdsPower-launched profile
// (k1e2mtxo), navigate to a Shopee URL with human-like behavior, capture proof.
//
// Human-like layer:
//   1. Pre-nav delay (1.5–3.5s) + pre-nav mouse drift (cursor settles near center)
//   2. After load: small mouse moves → mid-scroll → near-bottom-scroll → back to top
//   3. Post-scroll settle pause before screenshot
//
// We deliberately avoid instant goto→screenshot patterns that anti-bot heuristics
// (Shopee / DataDome / PerimeterX) flag as headless scraper signatures.
//
// Connection: CDP via Playwright `connectOverCDP`. We do NOT call
// `browser.close()` — that would kill the AdsPower profile. Profile stays open
// across runs so we can iterate quickly.

const { chromium } = require('playwright-core');

const CDP_URL =
  process.env.CDP_URL ||
  'ws://127.0.0.1:57079/devtools/browser/b14d510a-920b-496f-a12b-6ba2a0e8784c';

const TARGET_URL = process.env.TARGET_URL || 'https://shopee.co.id';
const SCREENSHOT_PATH =
  process.env.SCREENSHOT_PATH || 'shopee-via-sunbrowser.png';

// Random delay helpers (uniform between min..max ms).
function jitter(min, max) {
  return Math.floor(min + Math.random() * (max - min));
}

async function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Pick a random viewport-relative point near the centre — humans don't park the
// cursor at (0,0); they rest it roughly where they're reading.
async function driftMouseToCenter(page) {
  const vp = page.viewportSize() || { width: 1440, height: 900 };
  const cx = Math.floor(vp.width * (0.4 + Math.random() * 0.2));
  const cy = Math.floor(vp.height * (0.4 + Math.random() * 0.2));
  await page.mouse.move(cx, cy);
}

async function settle(page, totalMs) {
  // Break the wait into a few small mouse drifts so the cursor never appears
  // parked for the entire settle window.
  const slices = 3;
  const per = Math.floor(totalMs / slices);
  for (let i = 0; i < slices; i++) {
    await driftMouseToCenter(page);
    await sleep(per);
  }
}

async function scrollAround(page) {
  // Human-style read-and-scroll: 25% → 70% → back to top.
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'auto' }));
  await sleep(250);
  await page.evaluate(() =>
    window.scrollTo({ top: document.body.scrollHeight * 0.25, behavior: 'smooth' }),
  );
  await sleep(450);
  await page.evaluate(() =>
    window.scrollTo({ top: document.body.scrollHeight * 0.7, behavior: 'smooth' }),
  );
  await sleep(450);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
  await sleep(300);
}

(async () => {
  console.log('[probe] CDP_URL  =', CDP_URL);
  console.log('[probe] TARGET   =', TARGET_URL);
  console.log('[probe] Screenshot →', SCREENSHOT_PATH);

  const browser = await chromium.connectOverCDP(CDP_URL, { timeout: 30_000 });
  console.log('[probe] connected. contexts =', browser.contexts().length);

  const context = browser.contexts()[0];
  if (!context) {
    throw new Error('No browser context available from CDP target');
  }

  const existingPages = context.pages();
  const page =
    existingPages.find((p) => p.url() === 'about:blank') ||
    existingPages[0] ||
    (await context.newPage());

  console.log('[probe] current page URL before nav =', page.url());

  // Capture page-level errors (404 of sub-resources, etc.) so we can spot ad-hoc
  // failures vs anti-bot blocks. We log the first 5 only to avoid flooding.
  let pageErrorCount = 0;
  page.on('console', (msg) => {
    const t = msg.type();
    if (t === 'error' || t === 'warning') {
      if (pageErrorCount < 3) console.log(`[page:${t}]`, msg.text().slice(0, 200));
    }
  });
  page.on('pageerror', (err) => {
    pageErrorCount++;
    if (pageErrorCount <= 3) console.log('[page:error]', err.message.slice(0, 200));
  });

  // ---------- Anti-detection phase 1: pre-nav drift + delay ----------
  await driftMouseToCenter(page);
  const preNavDelay = jitter(1500, 3500);
  console.log(`[probe] pre-nav drift + ${preNavDelay}ms delay ...`);
  await sleep(preNavDelay);

  // ---------- Navigate ----------
  console.log('[probe] navigating ...');
  const navResp = await page.goto(TARGET_URL, {
    waitUntil: 'domcontentloaded',
    timeout: 60_000,
  });
  console.log('[probe] domcontentloaded. status =', navResp?.status());

  // ---------- Anti-detection phase 2: post-load scroll + drift ----------
  // Settle for ~4s with mouse drift (was 6s before — kept as 4s because the
  // post-nav scroll/drift also take time and we don't want to over-wait).
  console.log('[probe] post-load settle + scroll behavior ...');
  await settle(page, 2_500);
  await scrollAround(page);
  await driftMouseToCenter(page);
  await sleep(800);

  const finalUrl = page.url();
  const title = await page.title();
  console.log('[probe] final URL  =', finalUrl);
  console.log('[probe] title      =', title);

  const bodyText = await page.evaluate(() =>
    (document.body && document.body.innerText ? document.body.innerText : '').slice(0, 600),
  );
  console.log('[probe] body[0:600] =');
  console.log(bodyText.split('\n').map((l) => '  | ' + l).join('\n'));

  const ua = await page.evaluate(() => navigator.userAgent);
  console.log('[probe] UA         =', ua);
  console.log('[probe] pageErrorCount =', pageErrorCount);

  await page.screenshot({ path: SCREENSHOT_PATH, fullPage: false });
  console.log('[probe] screenshot →', SCREENSHOT_PATH);

  console.log('[probe] done. profile remains open (browser.close() intentionally NOT called).');
  process.exit(0);
})().catch((err) => {
  console.error('[probe] FATAL:', err.message);
  if (err.stack) console.error(err.stack.split('\n').slice(0, 4).join('\n'));
  process.exit(1);
});