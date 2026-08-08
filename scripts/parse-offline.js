// scripts/parse-offline.js
//
// Reads saved HTML files from a directory (default: ./html-pages/) and
// extracts product data using the shared parseCard logic. No browser required.
//
// Each HTML file is expected to be the full post-JS rendered HTML from
// capture-pages.js (or any other capture method). Files are matched against
// the same naming convention `<category-slug>-page<N>.html` (or any *.html).
//
// Output: JSONL to OUTPUT_DIR, one product per line. Optional metadata fields
// are inferred from the sidecar `*.html.json` files where present.
//
// Usage:
//   INPUT_DIR=./html-pages node scripts/parse-offline.js
//   INPUT_DIR=./html-pages OUTPUT_DIR=./out PARSE_VARIANT=strict node scripts/parse-offline.js

'use strict';

const fs = require('fs');
const path = require('path');
const cheerio = require('cheerio');
const { parseCard } = require('./lib/parse-card');

const INPUT_DIR = process.env.INPUT_DIR || path.join(__dirname, '..', 'html-pages');
const OUTPUT_DIR = process.env.OUTPUT_DIR || path.join(__dirname, '..', 'out');

function jitter(min, max) { return Math.floor(min + Math.random() * (max - min)); }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// Pull a single card's data out of an HTML string using cheerio (no browser).
// Mirrors what the live page.evaluate() extractor returns.
//
// IMPORTANT: cheerio's `.text()` concatenates ALL text nodes without separators,
// so the live scraper's parseCard (which expects innerText with newlines
// between block-level children) cannot consume the raw text. We emulate
// innerText here by walking the DOM and inserting \n at block boundaries.
const BLOCK_TAGS = new Set([
  'div', 'p', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'section', 'article', 'header', 'footer', 'main', 'aside', 'nav',
  'table', 'tr', 'td', 'th', 'ul', 'ol',
]);

// Tags whose text content should NOT contribute to innerText. Without this,
// Shopee's inline JSON config (in <script> or <script type="application/ld+json">)
// and CSS rules (in <style>) become lines that match `Rp` or `Terjual` regexes.
const SKIP_TAGS = new Set([
  'script', 'style', 'noscript', 'template',
]);

function syntheticInnerText($, node) {
  let out = '';
  function walk(n, skipDepth = 0) {
    if (skipDepth > 0 && n.type === 'text') return; // skip text under SKIP_TAGS
    if (n.type === 'text') { out += n.data; return; }
    if (n.type !== 'tag') return;
    if (n.tagName === 'br') { out += '\n'; return; }
    const isBlock = BLOCK_TAGS.has(n.tagName);
    if (isBlock) out += '\n';
    const childSkip = SKIP_TAGS.has(n.tagName) ? skipDepth + 1 : skipDepth;
    $(n).contents().each((_, c) => walk(c, childSkip));
    if (isBlock) out += '\n';
  }
  walk(node);
  // Collapse internal whitespace within each line, drop empty lines.
  // Normalize NBSP to space first so the regex/parser downstream sees plain
  // word boundaries.
  return out
    .split('\n')
    .map((l) => l.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .map((l) => l.replace(/^Rp(\d)/, 'Rp\n$1'))
    .join('\n');
}

function extractCardData($, $card) {
  const cardDiv = $card.find('div[role="group"][aria-label^="Product card:"]').first();
  const ariaLabel = cardDiv.attr('aria-label');
  const nameFromAria = ariaLabel
    ? ariaLabel.replace(/^Product card:\s*/, '').trim()
    : null;
  const productUrl = $card.find('a[href*="-i."]').first().attr('href') || null;
  const innerText = syntheticInnerText($, $card.get(0));
  // For /shop pages, the inner div with role="group" aria-label is nested one
  // level deeper than expected. Fall back to first line of synthetic innerText.
  return {
    name_from_aria: nameFromAria || (innerText.split('\n')[0] || null),
    product_url: productUrl,
    innerText,
  };
}

function extractPageCategorySlug(file) {
  // Try to read sidecar metadata (.html.json) for canonical category.
  try {
    const meta = JSON.parse(fs.readFileSync(file + '.json', 'utf8'));
    return meta;
  } catch (_) {
    return null;
  }
}

function inferCategorySlugFromFilename(file) {
  // "<slug>-page<n>.html" → "<slug>"
  const base = path.basename(file, '.html');
  const m = base.match(/^(.+?)-page\d+$/);
  return m ? m[1] : base;
}

function inferPageFromFilename(file) {
  const base = path.basename(file, '.html');
  const m = base.match(/-page(\d+)$/);
  return m ? parseInt(m[1], 10) : 1;
}

(async () => {
  if (!fs.existsSync(INPUT_DIR)) {
    console.error(`[offline] FATAL: INPUT_DIR not found: ${INPUT_DIR}`);
    process.exit(1);
  }
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });

  const files = fs.readdirSync(INPUT_DIR)
    .filter((f) => f.endsWith('.html') && !f.endsWith('.html.json'))
    .map((f) => path.join(INPUT_DIR, f))
    .sort();

  if (files.length === 0) {
    console.error(`[offline] no .html files in ${INPUT_DIR}`);
    process.exit(1);
  }

  console.log('[offline] INPUT_DIR  =', INPUT_DIR);
  console.log('[offline] OUTPUT_DIR =', OUTPUT_DIR);
  console.log('[offline] files      =', files.length);

  const outPath = path.join(OUTPUT_DIR,
    `offline-${path.basename(INPUT_DIR)}-${Date.now()}.jsonl`);
  const outStream = fs.createWriteStream(outPath, { flags: 'a' });

  let grandTotal = 0;
  for (const file of files) {
    const html = fs.readFileSync(file, 'utf8');
    const $ = cheerio.load(html);

    const meta = extractPageCategorySlug(file);
    const catSlug = (meta && meta.category_slug) || inferCategorySlugFromFilename(file);
    const pageNum = (meta && meta.page) || inferPageFromFilename(file);

    // Two extract strategies, mirroring the live scraper:
    //   (1) /search pages: <li data-sqe="item">
    //   (2) /shop/<store> pages: <li class="shop-search-result-view__item col-xs-2-4">
    //       This BEM class is the stable identifier for the main product grid;
    //       the previous `[class*="hover:shadow-hover"]` selector also matched
    //       product cards in the "Kamu Mungkin Suka" carousel above the grid
    //       (6 duplicates per page). User-provided selector is authoritative.
    const $dataSqe = $('[data-sqe="item"]');
    let $cards;
    if ($dataSqe.length > 0) {
      $cards = $dataSqe;
    } else {
      $cards = $('.shop-search-result-view__item.col-xs-2-4');
    }

    const pageCards = [];
    $cards.each((_, card) => {
      const data = extractCardData($, $(card));
      if (data.name_from_aria && data.innerText.trim().length > 5) {
        pageCards.push(data);
      }
    });

    console.log(`[offline] ${path.basename(file)} → ${pageCards.length} cards (category=${catSlug}, page=${pageNum})`);

    for (const c of pageCards) {
      const parsed = parseCard(c, {
        source_shop: 'beliacosmetic',
        category: catSlug,
        page_number: pageNum,
      });
      outStream.write(JSON.stringify(parsed) + '\n');
      grandTotal++;
    }
  }

  outStream.end(() => {
    console.log('\n[offline] done.');
    console.log(`[offline] total extracted: ${grandTotal}`);
    console.log(`[offline] output:          ${outPath}`);
    process.exit(0);
  });
})().catch((err) => {
  console.error('[offline] FATAL:', err.message);
  if (err.stack) console.error(err.stack.split('\n').slice(0, 4).join('\n'));
  process.exit(1);
});