"""Unit test for extract_review_from_card logic — runs without browser.

Replicates the JS evaluate (clone + remove .QSiE2A) using BeautifulSoup, then
runs the Python regex/truncation logic to verify author + comment cleanup.

If this test passes, the LIVE Playwright version should too (the logic is
the same; only the DOM source differs).

Usage:
    /home/han/.my_global_env/bin/python3 -m scripts.test_review_extraction
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from bs4 import BeautifulSoup

# Replicate extract_review_from_card's Python logic
DATE_PATTERN = re.compile(
    r"(\d+\s*(?:hari|minggu|bulan|tahun|jam)\s*(?:lalu|yang\s+lalu)?|202[0-9])",
    re.IGNORECASE,
)


def extract_from_card_html(card_html: str) -> dict:
    """Replicate extract_review_from_card's logic on raw card HTML."""
    # JS-equivalent: clone + remove .QSiE2A, then get innerText
    soup = BeautifulSoup(card_html, "html.parser")
    for el in soup.select(".QSiE2A"):
        el.decompose()
    text = soup.get_text("\n", strip=True)
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return {"author": "", "comment": "", "posted_at": "", "lines": lines}

    # Author extraction
    author = lines[0] if lines else ""
    # First try: find [class*='username']
    username_el = soup.select_one("[class*='username']")
    if username_el:
        author = username_el.get_text(strip=True)
    # Truncate author at "|", " 20", etc.
    for sep in ["|", " 20", "Laporkan", "respon"]:
        idx = author.find(sep)
        if idx > 0:
            author = author[:idx].strip()
            break
    # Truncate at year (negative lookbehind for digit)
    m_year = re.search(r"(?<!\d)(19|20)\d{2}", author)
    if m_year:
        author = author[: m_year.start()].strip()

    # Date extraction
    posted_at = ""
    for line in lines:
        if DATE_PATTERN.search(line):
            posted_at = line
            break
    if not posted_at:
        for line in lines:
            if re.search(r"hari|minggu|bulan|tahun|2024|2025|2026", line, re.IGNORECASE):
                posted_at = line
                break
    # Truncate posted_at at "|"
    idx = posted_at.find("|")
    if idx > 0:
        posted_at = posted_at[:idx].strip()

    # Comment extraction — use substring match (not equality) since posted_at
    # was truncated at "|" but lines still contain the date+variation.
    candidates = [l for l in lines if author not in l and posted_at not in l and len(l) > 15]
    comment = max(candidates, key=len) if candidates else ""
    if "|" in comment:
        parts = [p.strip() for p in comment.split("|")]
        tail = max(parts[1:], key=len) if len(parts) > 1 else parts[-1]
        if len(tail) > 15:
            comment = tail
    for marker in ["respon penjual", "Laporkan Penyalahgunaan", "Membantu?"]:
        idx = comment.find(marker)
        if idx > 0:
            comment = comment[:idx].strip()

    return {"author": author, "comment": comment, "posted_at": posted_at}


def main():
    html_path = ROOT / "tests" / "fixtures" / "diag_next_btn" / "01_after_filter.html"
    html = html_path.read_text()
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select("[data-cmtid]")
    print(f"Found {len(cards)} review cards with data-cmtid\n")

    results = []
    for i, card in enumerate(cards):
        card_html = str(card)
        result = extract_from_card_html(card_html)
        results.append(result)
        print(f"=== Card {i + 1} (cmtid={card.get('data-cmtid')}) ===")
        print(f"  author:    {result['author']!r}")
        print(f"  posted_at: {result['posted_at']!r}")
        print(
            f"  comment:   {result['comment'][:100]!r}{'...' if len(result['comment']) > 100 else ''}"
        )
        print()

    # Validation
    print("=" * 70)
    print("VALIDATION:")
    print("=" * 70)
    errors = 0
    for i, r in enumerate(results):
        # Author should not contain 4-digit year (date glued)
        if re.search(r"(?<!\d)(19|20)\d{2}", r["author"]):
            print(f"  ✗ Card {i + 1} author still has date: {r['author']!r}")
            errors += 1
        # Posted_at should not contain "|"
        if "|" in r["posted_at"]:
            print(f"  ✗ Card {i + 1} posted_at has '|': {r['posted_at']!r}")
            errors += 1
        # Comment should not start with "Variasi:"
        if r["comment"].startswith("Variasi:"):
            print(f"  ✗ Card {i + 1} comment still has 'Variasi:' prefix: {r['comment'][:80]!r}")
            errors += 1
        # Comment should not contain "Halo Kak" or "Terima kasih" (shop reply template)
        if "Halo Kak" in r["comment"] or "Terima kasih" in r["comment"]:
            print(f"  ✗ Card {i + 1} comment is shop reply template: {r['comment'][:80]!r}")
            errors += 1
        # Comment should be non-empty
        if not r["comment"]:
            print(f"  ✗ Card {i + 1} comment is empty")
            errors += 1
        # Author should be non-empty
        if not r["author"]:
            print(f"  ✗ Card {i + 1} author is empty")
            errors += 1

    if errors == 0:
        print(f"\n✅ ALL {len(results)} CARDS PASS validation")
        print("   - No date in author field")
        print("   - No '|' in posted_at")
        print("   - No 'Variasi:' prefix in comment")
        print("   - No shop reply template in comment")
        print("   - All authors and comments non-empty")
    else:
        print(f"\n❌ {errors} validation errors")
        sys.exit(1)


if __name__ == "__main__":
    main()
