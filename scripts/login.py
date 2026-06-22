"""Login script: open Camoufox, log in manually, save cookies.

Usage:
    python -m scripts.login                       # default URL: Shopee ID login
    python -m scripts.login --url <url>           # custom URL (sg/my/etc.)
    python -m scripts.login --output cookies.json # custom output path

Flow:
    1. If cookies.json already exists, ask user to confirm overwrite.
    2. Open Camoufox headful (Camoufox's anti-fingerprinting is critical
       for Shopee's Google SSO).
    3. Navigate to the login URL.
    4. Wait for user to log in (poll cookies until SPC_EC / SPC_F / etc. appear).
    5. Save cookies + close.

We poll for the presence of Shopee's session cookies (any of: SPC_EC, SPC_F,
SPC_R, SPC_T, SPC_T_ID, login_token) — once one is present, we know the user
has a valid session.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from camoufox import Camoufox

DEFAULT_LOGIN_URL = "https://shopee.co.id/buyer/login"
DEFAULT_OUTPUT = ROOT / "cookies.json"
DEFAULT_TIMEOUT_S = 600  # 10 min

# Cookie names that indicate a valid Shopee session
# IMPORTANT: SPC_F, SPC_T_ID are set by Shopee on the LOGIN page itself
# (for analytics / anti-bot), so they're NOT a reliable login indicator.
# SPC_EC (encrypted session) and SPC_U (user ID) are only set AFTER successful login.
SESSION_COOKIE_NAMES = [
    "SPC_EC",  # Encrypted session — strongest signal of real login
    "SPC_U",  # User ID — only after login
    "shopee_login_token",  # Login token — only after login
    "login_token",  # Variant
]
# Cookies that Shopee sets even on the login page (NOT sufficient alone)
PRE_LOGIN_COOKIES = ["SPC_F", "SPC_T_ID", "SPC_T", "SPC_R", "SPC_R_T_ID", "SPC_R_T_IV"]


def has_session_cookie(cookies: list[dict]) -> bool:
    """Return True if any session cookie name is present."""
    cookie_names = {c["name"] for c in cookies}
    return any(name in cookie_names for name in SESSION_COOKIE_NAMES)


def prompt_overwrite(path: Path) -> bool:
    """If file exists, ask user to confirm overwrite. Default = no."""
    if not path.exists():
        return True
    print(f"⚠️  {path} already exists.")
    try:
        answer = input("   Overwrite? [y/N] ").strip().lower()
    except EOFError:
        # Non-interactive: default to yes since user explicitly invoked
        print("y (non-interactive)")
        return True
    return answer in ("y", "yes")


def main_async(args) -> None:
    """Sync entry point (was async before — Camoufox sync API can't run inside asyncio.run)."""
    return _main_sync(args)


def _main_sync(args) -> None:
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if not prompt_overwrite(output):
        print("Aborted. Existing cookies kept.")
        return

    print(f"[login] launching Camoufox (headful={not args.headless}) ...")
    print(f"[login] URL:   {args.url}")
    print(f"[login] output: {output}")
    print()

    with Camoufox(headless=args.headless, humanize=True) as browser:
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="id-ID",
            timezone_id="Asia/Jakarta",
            geolocation={"latitude": -6.2088, "longitude": 106.8456},
            permissions=["geolocation"],
        )
        page = context.new_page()

        print(f"[login] navigating to {args.url} ...")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=30_000)
        except Exception as e:
            print(f"[login] warning: navigation failed: {e}", file=sys.stderr)

        # Wait for user to log in
        print()
        print("[login] 👉 Please complete the login in the browser window.")
        print("[login]    This script will detect your session and save cookies automatically.")
        print(f"[login]    Timeout: {args.timeout}s")
        print()

        start = time.monotonic()
        poll_interval = 2
        last_count = 0
        while (time.monotonic() - start) < args.timeout:
            time.sleep(poll_interval)
            try:
                cookies = context.cookies()
            except Exception:
                continue
            count = len(cookies)
            if count != last_count:
                print(f"   🍪 {count} cookies so far...")
                last_count = count
            if has_session_cookie(cookies):
                print()
                print("[login] ✅ Session cookie detected. Saving...")
                break
        else:
            print(f"[login] ⏱️  Timeout after {args.timeout}s. Saving what we have.")

        # Save
        storage = context.storage_state()
        output.write_text(json.dumps(storage, indent=2), encoding="utf-8")
        print(f"[login] 💾 Saved to {output}")
        cookie_names = sorted({c["name"] for c in storage.get("cookies", [])})
        session_cookies = [n for n in cookie_names if n in SESSION_COOKIE_NAMES]
        print(f"[login]    Total cookies: {len(cookie_names)}")
        print(f"[login]    Session cookies: {session_cookies}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Open Camoufox, log in to Shopee manually, save cookies."
    )
    parser.add_argument("--url", default=DEFAULT_LOGIN_URL, help="Login URL")
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT), help="Output path for cookies.json"
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_S, help="Timeout in seconds (default 600)"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run headless (NOT recommended for SSO)"
    )
    args = parser.parse_args()
    main_async(args)


if __name__ == "__main__":
    main()
