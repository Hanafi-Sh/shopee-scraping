// scripts/lib/parse-card.js
//
// Shared parser for Shopee product cards. Used by:
//   - scraper.js               (online, /search pages)
//   - scrape-beliacosmetic.js  (online, /shop/<store>/<collection>)
//   - parse-offline.js         (offline, reads saved HTML files)
//
// Single source of truth: any regex tweak here applies to all three.
//
// Input shape (what we accept):
//   { name_from_aria, product_url, innerText }
//
// Output: structured record with name / price / discount / rating /
// sold_count_raw / location.

'use strict';

const IND_PRICE = /^\d{1,3}(?:\.\d{3})+$/; // Indonesian price: 1.800 / 200.000 / 99.999

/**
 * @param {{ name_from_aria: string|null, product_url: string|null, innerText: string }} cardData
 * @param {{ shop_name?: string|null, source_shop?: string|null, category?: string|null, page_number?: number|null }} [ctx]
 */
function parseCard(cardData, ctx = {}) {
  const nameFromAria = cardData.name_from_aria || null;
  const url = cardData.product_url || null;

  const text = (cardData.innerText || '').trim();
  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);

  const out = {
    name: nameFromAria || lines[0] || null,
    product_url: url,
    price_current: null,
    price_original: null,
    discount_pct: null,
    rating: null,
    sold_count_raw: null,
    location: null,
    shop_name: ctx.shop_name || null,
    source_shop: ctx.source_shop || null,
    category: ctx.category || null,
    page_number: ctx.page_number != null ? ctx.page_number : null,
    extracted_at: new Date().toISOString(),
  };

  // ----- price + discount -----
  // Pattern: lines include "Rp" then a price ("X.XXX.XXX" or "XXX.XXX").
  // If a "-X%" marker appears, the next numeric line matching IND_PRICE is
  // price_original.
  const rpIdx = lines.findIndex((l) => l === 'Rp');
  if (rpIdx >= 0) {
    for (let i = rpIdx + 1; i < lines.length; i++) {
      if (IND_PRICE.test(lines[i])) {
        out.price_current = lines[i];
        for (let j = i + 1; j < Math.min(i + 5, lines.length); j++) {
          const l = lines[j];
          if (/^-\d+%$/.test(l)) {
            out.discount_pct = parseInt(l.replace(/[^\d]/g, ''), 10);
            for (let k = j + 1; k < Math.min(j + 4, lines.length); k++) {
              if (IND_PRICE.test(lines[k])) {
                out.price_original = lines[k];
                break;
              }
            }
          } else if (l === 'Rp') break;
        }
        break;
      }
    }
  }

  // ----- rating + sold count -----
  // Sold: "41 Terjual" / "1RB+ terjual" — note lowercase 't' on shop pages,
  // and Shopee may use "TERJUAL" all-caps via CSS text-transform.
  const soldMatch = text.match(/(\d+(?:[.,]\d+)?(?:RB\+?|rb\+?)?)\s*Terjual/i);
  if (soldMatch) {
    out.sold_count_raw = soldMatch[1];
    // Match the same case classes as the regex above (lower / Title / upper).
    const soldIdx = lines.findIndex((l) => /[Tt][Ee][Rr][Jj][Uu][Aa][Ll]$/.test(l));
    // Rating can be one or two decimals (e.g. "5.0" or "4.90" or "4.50").
    if (soldIdx > 0 && /^\d\.\d{1,2}$/.test(lines[soldIdx - 1])) {
      out.rating = parseFloat(lines[soldIdx - 1]);
    }
  }

  // ----- location -----
  // Shopee's /search result cards have a "Produk Serupa" anchor that sits
  // right after the city/town name. /shop category cards do NOT show location
  // at all — the lines after sold_count are badges ("Hadiah Gratis", "Pilih
  // Lokal", "Star+", etc.), not cities. To avoid mis-tagging those badges as
  // location, we only set location when we find the "Produk Serupa" anchor.
  const similarIdx = lines.findIndex((l) => l === 'Produk Serupa');
  if (similarIdx > 0) {
    const cand = lines[similarIdx - 1];
    if (cand && !/^Rp$|^[\d.,]+$|^-?\d+%$|^Produk Terlaris$|^Produk Tertentu$/.test(cand)) {
      out.location = cand;
    }
  }
  // No fallback: shop-page cards lack a city field, and falling back picks
  // promotional badges ("Hadiah Gratis", "Pilih Lokal", "Star+", …) which
  // isn't useful data.

  return out;
}

module.exports = { parseCard, IND_PRICE };
