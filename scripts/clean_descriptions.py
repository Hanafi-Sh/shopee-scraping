"""One-shot cleanup: remove blank-line artifacts from existing descriptions.

Background:
    Older scrapes joined description paragraphs with "\\n\\n". Shopee often
    renders empty `<p></p>` between bullet items, which resulted in many
    consecutive blank lines in the saved text. This script cleans up
    already-scraped data so it matches the new (cleaner) behavior.

Behavior:
    - Walks all `data/*/products_enriched.json`
    - For each product, replaces runs of 2+ newlines with a single newline
      in the description field
    - Re-saves JSON atomically AND regenerates the CSV

Usage:
    python -m scripts.clean_descriptions
    python -m scripts.clean_descriptions --dry-run   # preview, don't write
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from shopee_dommie.persistence import (
    atomic_write_csv,
    atomic_write_json,
    load_products_list,
)


def clean_description(text: str) -> str:
    """Collapse runs of 2+ newlines into a single newline. Strip trailing whitespace."""
    if not text:
        return text
    # Replace any run of 2+ newlines (with optional whitespace between) with single \n
    cleaned = re.sub(r"\n\s*\n+", "\n", text)
    # Strip leading/trailing whitespace
    return cleaned.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up blank-line artifacts in existing product descriptions."
    )
    parser.add_argument(
        "--data-dir",
        default=str(ROOT / "data"),
        help="Path to the data directory (default: ./data)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing files",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"❌ Data directory not found: {data_dir}")
        sys.exit(1)

    total_shops = 0
    total_products = 0
    total_changed = 0
    total_examples = 0

    for shop_dir_path in sorted(data_dir.iterdir()):
        if not shop_dir_path.is_dir():
            continue
        json_path = shop_dir_path / "products_enriched.json"
        if not json_path.exists():
            continue

        total_shops += 1
        products = load_products_list(data_dir, shop_dir_path.name)
        # Note: load_products_list returns ProductSummary — re-load raw JSON for full fields
        import json

        raw = json.loads(json_path.read_text(encoding="utf-8"))

        for entry in raw:
            total_products += 1
            old_desc = entry.get("description", "") or ""
            new_desc = clean_description(old_desc)
            if old_desc != new_desc:
                total_changed += 1
                if total_examples < 3:
                    old_lines = old_desc.count("\n")
                    new_lines = new_desc.count("\n")
                    print(f"  ✏️  {shop_dir_path.name}/{entry.get('itemid', '?')}:")
                    print(f"      {old_lines} newlines → {new_lines} newlines")
                    total_examples += 1
                entry["description"] = new_desc

        if not args.dry_run and total_changed > 0:
            atomic_write_json(json_path, raw)
            # Regenerate CSV too
            csv_path = json_path.with_suffix(".csv")
            atomic_write_csv(csv_path, raw)

    print()
    print("📊 Summary:")
    print(f"   Shops processed:    {total_shops}")
    print(f"   Products processed: {total_products}")
    print(f"   Descriptions cleaned: {total_changed}")
    if args.dry_run:
        print("   (DRY RUN — no files written)")


if __name__ == "__main__":
    main()
