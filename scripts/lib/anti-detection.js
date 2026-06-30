// scripts/lib/anti-detection.js
//
// Shared anti-detection helpers. Was duplicated across scrape-beliacosmetic.js,
// capture-pages.js, scraper.js, and probe.js — kept in sync by hand until
// it wasn't (settleAndScroll had already drifted). Single source of truth.

'use strict';

// ---- low-level utilities --------------------------------------------------

function jitter(min, max) {
  return Math.floor(min + Math.random() * (max - min));
}
function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
function ts() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
}
function slugify(s) {
  return String(s)
    .toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 60);
}

// ---- mouse movement -------------------------------------------------------

// Move cursor along a Bezier-ish curve with smooth sub-steps. Stores the
// final position on window.__mx / __my so subsequent calls continue from
// the current location (humans don't teleport their cursor).
async function naturalMove(page, targetX, targetY) {
  const cur = await page.evaluate(() => {
    if (typeof window.__mx !== 'number') window.__mx = 720;
    if (typeof window.__my !== 'number') window.__my = 450;
    return { x: window.__mx, y: window.__my };
  });
  const steps = 10 + Math.floor(Math.random() * 8); // 10–17
  const dx = targetX - cur.x;
  const dy = targetY - cur.y;
  const arc = (Math.random() - 0.5) * Math.min(80, Math.hypot(dx, dy) * 0.25);
  const perpX = -dy * 0.2 + arc;
  const perpY = dx * 0.2 + arc;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const bez = 4 * t * (1 - t);
    await page.mouse.move(cur.x + dx * t + perpX * bez, cur.y + dy * t + perpY * bez);
    await sleep(jitter(15, 45));
  }
  await page.evaluate(({ x, y }) => { window.__mx = x; window.__my = y; }, { x: targetX, y: targetY });
}

// Tiny micro-movement near the current cursor position. Reads viewport
// size from inside the page to avoid cross-process round-trip for the value.
async function microWiggle(page) {
  const cur = await page.evaluate(() => ({
    x: typeof window.__mx === 'number' ? window.__mx : window.innerWidth / 2,
    y: typeof window.__my === 'number' ? window.__my : window.innerHeight / 2,
    vw: window.innerWidth,
    vh: window.innerHeight,
  }));
  const nx = Math.max(20, Math.min(cur.vw - 20, cur.x + (Math.random() - 0.5) * 60));
  const ny = Math.max(20, Math.min(cur.vh - 20, cur.y + (Math.random() - 0.5) * 60));
  await naturalMove(page, nx, ny);
}

// Idle "thinking" pause with several micro-wiggles.
async function idle(page, totalMs) {
  const start = Date.now();
  while (Date.now() - start < totalMs) {
    await microWiggle(page);
    await sleep(jitter(40, 140));
  }
}

// Hover at a coordinate with a brief pre-click pause.
async function hoverAt(page, x, y) {
  await naturalMove(page, x, y);
  await sleep(jitter(180, 520));
}

// Backward-compat: drift mouse to a random viewport-relative "looking" position.
async function driftMouse(page) {
  const vp = await page.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }));
  const cx = Math.floor(vp.w * (0.4 + Math.random() * 0.2));
  const cy = Math.floor(vp.h * (0.4 + Math.random() * 0.2));
  await naturalMove(page, cx, cy);
}

// ---- scroll ----------------------------------------------------------------

async function naturalScroll(page, targetY, { smooth = true } = {}) {
  await page.evaluate(
    ({ y, sm }) => window.scrollTo({ top: y, behavior: sm ? 'smooth' : 'auto' }),
    { y: targetY, sm: smooth },
  );
}

// Variable scroll sequence: smooth / overshoot / correct, with idle drift.
async function settleAndScroll(page) {
  const totalDuration = jitter(2200, 3200);
  const start = Date.now();
  while (Date.now() - start < totalDuration) {
    await microWiggle(page);
    await sleep(jitter(80, 220));
  }
  const h = await page.evaluate(() => document.body.scrollHeight);
  await naturalScroll(page, Math.round(h * 0.25), { smooth: true });
  await sleep(jitter(350, 650));
  await microWiggle(page);
  if (Math.random() < 0.5) {
    await naturalScroll(page, Math.round(h * 0.78), { smooth: true });
    await sleep(jitter(300, 500));
    await naturalScroll(page, Math.round(h * 0.6), { smooth: true });
  } else {
    await naturalScroll(page, Math.round(h * 0.55), { smooth: true });
  }
  await sleep(jitter(350, 600));
  await microWiggle(page);
  await naturalScroll(page, 0, { smooth: true });
  await sleep(jitter(250, 450));
}

// ---- DOM helpers -----------------------------------------------------------

// Scroll an element into view, wait for smooth scroll to settle, return
// its centered viewport coordinates. Used by hover-then-click sequences.
async function measureAfterScroll(page, selector, scrollMs = 700) {
  return await page.evaluate(
    ({ sel, sm }) => new Promise((resolve) => {
      const el = document.querySelector(sel);
      if (!el) return resolve(null);
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setTimeout(() => {
        const r = el.getBoundingClientRect();
        resolve({
          x: r.left + r.width / 2,
          y: r.top + r.height / 2,
          text: (el.innerText || '').trim().slice(0, 60),
        });
      }, sm);
    }),
    { sel: selector, sm: scrollMs },
  );
}

// Find a clickable element matching a CSS selector + text predicate.
async function findByText(page, selector, textPredicate) {
  return await page.evaluate(
    ({ sel, fnSrc }) => {
      // eslint-disable-next-line no-new-func
      const fn = new Function('return (' + fnSrc + ')')();
      const els = Array.from(document.querySelectorAll(sel));
      return els.find((el) => fn(el)) || null;
    },
    { sel: selector, fnSrc: textPredicate.toString() },
  );
}

module.exports = {
  jitter,
  sleep,
  ts,
  slugify,
  naturalMove,
  microWiggle,
  idle,
  hoverAt,
  driftMouse,
  naturalScroll,
  settleAndScroll,
  measureAfterScroll,
  findByText,
};