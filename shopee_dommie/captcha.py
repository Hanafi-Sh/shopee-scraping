"""CaptchaDetector — pause/resume coordination for Shopee anti-bot challenges.

When Shopee detects bot-like behavior, it redirects to one of:
- /verify/traffic?anti_bot_tracking_id=...&scene=crawler_item
- /verify/captcha?anti_bot_tracking_id=...&scene=crawler_item
These pages render an Arkose Labs slider-puzzle captcha that the user MUST
solve manually. Once solved, the page redirects back to the original URL.

Usage pattern (as a gate before risky actions):

    captcha = CaptchaDetector(page)
    await captcha.wait_if_captcha()        # blocks until captcha is gone
    page.goto(some_url)                    # or click, etc.
    await captcha.wait_if_captcha()        # check again after the action

Detected via:
1. URL pattern: contains '/verify/' or 'scene=crawler' or 'anti_bot_tracking_id'
2. DOM iframe: src contains 'captcha', 'arkoselab', 'funcaptcha', 'arkose'
3. DOM text: 'Verifikasi untuk melanjutkan', 'Geser untuk menyelesaikan puzzle', etc.

Tunables (constructor args):
- poll_interval_s: how often to check (default 1.5s)
- max_wait_s: hard timeout (default 300s = 5 min). After this, raises TimeoutError.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from playwright.async_api import Page

VERIFY_URL_PATTERNS = ["/verify/", "scene=crawler", "anti_bot_tracking_id"]
CAPTCHA_IFRAME_PATTERNS = ["captcha", "arkoselab", "funcaptcha", "arkose"]
CAPTCHA_TEXT_PATTERNS = [
    "Verifikasi untuk melanjutkan",
    "Verifikasi Anda",
    "Verify you are human",
    "Geser untuk menyelesaikan puzzle",
    "Slide to complete",
]


class CaptchaDetector:
    """Polling-based captcha detector. Stateful — tracks pause state."""

    def __init__(
        self,
        page: Page,
        poll_interval_s: float = 0.8,
        max_wait_s: float = 300.0,
        on_pause: Callable | None = None,
        on_resume: Callable | None = None,
    ):
        self.page = page
        self.poll_interval_s = poll_interval_s
        self.max_wait_s = max_wait_s
        self.on_pause = on_pause
        self.on_resume = on_resume
        self._in_pause = False

    def url_matches_captcha(self) -> bool:
        return any(p in self.page.url for p in VERIFY_URL_PATTERNS)

    async def dom_has_captcha(self) -> bool:
        for pat in CAPTCHA_IFRAME_PATTERNS:
            if await self.page.locator(f"iframe[src*='{pat}']").count() > 0:
                return True
        for text in CAPTCHA_TEXT_PATTERNS:
            if await self.page.locator(f"text={text}").count() > 0:
                return True
        return False

    async def is_captcha_state(self) -> bool:
        if self.url_matches_captcha():
            return True
        return await self.dom_has_captcha()

    async def wait_if_captcha(self) -> None:
        """Block until captcha is gone. Polls every poll_interval_s.

        First call when captcha is detected triggers on_pause callback.
        When captcha clears, triggers on_resume callback.

        Raises:
            TimeoutError: if captcha not solved within max_wait_s.
        """
        if not await self.is_captcha_state():
            return
        if not self._in_pause:
            print()
            print("=" * 70)
            print("🛑 CAPTCHA / VERIFY PAGE DETECTED")
            print(f"   URL: {self.page.url[:120]}")
            print()
            print("   → Look at the browser window that just opened")
            print("   → Solve the slider puzzle / image challenge")
            print("   → The page should redirect back to the shop automatically")
            print(f"   → This detector will wait up to {self.max_wait_s:.0f}s")
            print("=" * 70)
            print()
            self._in_pause = True
            if self.on_pause:
                self.on_pause()

        start = time.monotonic()
        while await self.is_captcha_state():
            if time.monotonic() - start > self.max_wait_s:
                raise TimeoutError(
                    f"Captcha not solved within {self.max_wait_s:.0f}s "
                    f"(current URL: {self.page.url[:120]})"
                )
            await asyncio.sleep(self.poll_interval_s)
            elapsed = time.monotonic() - start
            if int(elapsed) % 30 == 0 and int(elapsed) > 0:
                print(f"   ⏳ Still waiting for captcha solve... ({elapsed:.0f}s)")

        print(f"✅ Captcha solved. Resuming work. (URL: {self.page.url[:80]})")
        self._in_pause = False
        if self.on_resume:
            self.on_resume()
        # Brief settle time for the page to re-render after redirect
        await self.page.wait_for_timeout(3000)
