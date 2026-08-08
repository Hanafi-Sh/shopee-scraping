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

// Smoothstep: S-curve velocity profile, 0→1→0. Used to slow sub-steps at
// the start/end of a mouse path (humans accelerate mid-curve, decelerate at
// the target — like reaching for a coffee cup).
function easeInOut(t) {
  return t * t * (3 - 2 * t);
}

// Move cursor along a Bezier-ish curve with S-curve velocity + variable
// sub-step durations. Stores the final position on window.__mx / __my so
// subsequent calls continue from the current location.
async function naturalMove(page, targetX, targetY) {
  const cur = await page.evaluate(() => {
    if (typeof window.__mx !== 'number') window.__mx = 720;
    if (typeof window.__my !== 'number') window.__my = 450;
    return { x: window.__mx, y: window.__my };
  });
  const steps = 12 + Math.floor(Math.random() * 8); // 12–19 sub-steps
  const dx = targetX - cur.x;
  const dy = targetY - cur.y;
  const arc = (Math.random() - 0.5) * Math.min(80, Math.hypot(dx, dy) * 0.25);
  const perpX = -dy * 0.2 + arc;
  const perpY = dx * 0.2 + arc;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const e = easeInOut(t);                  // position via S-curve
    const bez = 4 * t * (1 - t);             // arc weight (peak mid-path)
    const x = cur.x + dx * e + perpX * bez;
    const y = cur.y + dy * e + perpY * bez;
    // Variable sub-step dwell: shortest mid-curve, longest at endpoints.
    // Mid-curve ~18ms, endpoints ~45ms. The longer pauses at start/end
    // are why human mousing looks "natural" instead of mechanical.
    const dwell = 18 + 27 * Math.abs(0.5 - t) * 2;
    await page.mouse.move(x, y);
    await sleep(jitter(dwell * 0.7, dwell * 1.3));
  }
  await page.evaluate(({ x, y }) => { window.__mx = x; window.__my = y; }, { x: targetX, y: targetY });
}

// Tiny micro-movement near the current cursor position. Used to keep the
// mouse active during idle waits so it never sits dead.
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

// Continuous drift along a single chained Bezier path — replaces the old
// "idle = loop of microWiggle + sleep" which produced a visible stop-and-go
// pulse (move 30px, pause 50ms, move 30px, pause 50ms…). This version picks
// 3–5 random waypoints and runs the cursor through them with only short
// transition pauses between segments, no parking between sub-moves.
async function continuousDrift(page, totalMs) {
  const numSegments = 3 + Math.floor(Math.random() * 3); // 3–5 waypoints
  const perSegment = Math.max(200, Math.floor(totalMs / numSegments));
  for (let s = 0; s < numSegments; s++) {
    const target = await page.evaluate(() => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      return {
        x: Math.floor(w * (0.25 + Math.random() * 0.5)),
        y: Math.floor(h * (0.25 + Math.random() * 0.5)),
      };
    });
    await naturalMove(page, target.x, target.y);
    if (s < numSegments - 1) {
      // Brief settle between segments — small, not a full stop.
      await sleep(jitter(80, 220));
    } else {
      // Last segment: spend the remaining time idling in place.
      const remaining = perSegment - 200;
      if (remaining > 0) await sleep(remaining);
    }
  }
}

// Idle "thinking" pause — now uses continuousDrift so motion never parks.
async function idle(page, totalMs) {
  await continuousDrift(page, totalMs);
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

// Scroll via mouse wheel events instead of window.scrollTo. Real users scroll
// with the wheel; programmatic scrollTo is mechanical and bypasses browser
// event-loop timings that anti-bot systems measure.
async function naturalScroll(page, targetY, { smooth = true } = {}) {
  const currentY = await page.evaluate(() => window.scrollY);
  const delta = targetY - currentY;
  if (Math.abs(delta) < 50) {
    await page.mouse.wheel(0, delta);
    return;
  }
  // Simulate a scroll wheel burst: many small ticks with random delays so the
  // page renders frames between ticks. Smooth scroll mode = ~15ms gaps,
  // instant mode = ~5ms.
  const stepSize = 80 + Math.floor(Math.random() * 80); // 80–160 px per tick
  const numSteps = Math.max(1, Math.ceil(Math.abs(delta) / stepSize));
  const gapMin = smooth ? 30 : 10;
  const gapMax = smooth ? 80 : 25;
  for (let i = 0; i < numSteps; i++) {
    const remaining = delta - i * Math.sign(delta) * stepSize;
    const step = Math.sign(delta) * Math.min(stepSize, Math.abs(remaining));
    await page.mouse.wheel(0, step);
    await sleep(jitter(gapMin, gapMax));
  }
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
  easeInOut,
  naturalMove,
  microWiggle,
  continuousDrift,
  idle,
  hoverAt,
  driftMouse,
  naturalScroll,
  settleAndScroll,
  measureAfterScroll,
  findByText,
};