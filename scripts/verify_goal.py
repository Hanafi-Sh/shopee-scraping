"""Verify that a scraped shop has the expected output structure.

Usage:
    python -m scripts.verify_goal --shop erigostore
    python -m scripts.verify_goal --shop erigostore --data-dir data --min-products 3

Checks:
    data/<shop>/shop_info.json        exists
    data/<shop>/products.json         has >= min_products entries
    data/<shop>/products_enriched.json has >= min_products entries with description
    data/<shop>/reviews/<itemid>.json  at least 1 file with >= 5 reviews
    data/<shop>/images/<itemid>/01.*  at least 1 image file >= 1KB

Exit code 0 if all checks pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Shopee scraper output structure")
    parser.add_argument("--shop", required=True, help="Shop username (directory name)")
    parser.add_argument("--data-dir", default=str(ROOT / "data"), help="Data root")
    parser.add_argument("--min-products", type=int, default=1, help="Min products required")
    parser.add_argument("--min-reviews", type=int, default=5, help="Min reviews per file")
    parser.add_argument(
        "--min-image-size", type=int, default=1024, help="Min image file size in bytes"
    )
    args = parser.parse_args()

    shop_dir = Path(args.data_dir) / args.shop
    print(f"🔍 Verifying: {shop_dir}")
    print()

    failures = []

    # 1. shop_info.json
    info_path = shop_dir / "shop_info.json"
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        print(f"  ✅ shop_info.json: {info.get('name', '?')}")
    else:
        failures.append("shop_info.json missing")
        print("  ❌ shop_info.json: MISSING")

    # 2. products.json
    products_path = shop_dir / "products.json"
    if products_path.exists():
        products = json.loads(products_path.read_text(encoding="utf-8"))
        if len(products) >= args.min_products:
            print(f"  ✅ products.json: {len(products)} products (>= {args.min_products})")
        else:
            failures.append(f"products.json has {len(products)} < {args.min_products}")
            print(f"  ❌ products.json: {len(products)} products (< {args.min_products})")
    else:
        failures.append("products.json missing")
        print("  ❌ products.json: MISSING")

    # 3. products_enriched.json
    enriched_path = shop_dir / "products_enriched.json"
    if enriched_path.exists():
        enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
        with_desc = [p for p in enriched if p.get("description")]
        if len(with_desc) >= args.min_products:
            print(
                f"  ✅ products_enriched.json: {len(with_desc)} with description (>= {args.min_products})"
            )
        else:
            failures.append(
                f"products_enriched.json: {len(with_desc)} with desc < {args.min_products}"
            )
            print(
                f"  ❌ products_enriched.json: {len(with_desc)} with desc (< {args.min_products})"
            )
    else:
        failures.append("products_enriched.json missing")
        print("  ❌ products_enriched.json: MISSING")

    # 4. reviews/<itemid>.json
    reviews_dir = shop_dir / "reviews"
    if reviews_dir.exists():
        review_files = list(reviews_dir.glob("*.json"))
        with_enough = []
        for rf in review_files:
            data = json.loads(rf.read_text(encoding="utf-8"))
            if isinstance(data, list) and len(data) >= args.min_reviews:
                with_enough.append((rf, len(data)))
        if with_enough:
            print(
                f"  ✅ reviews/: {len(review_files)} files, {len(with_enough)} with >= {args.min_reviews} reviews"
            )
            for rf, n in with_enough[:3]:
                print(f"     - {rf.name}: {n} reviews")
        else:
            failures.append(f"reviews/ has no file with >= {args.min_reviews} reviews")
            print(
                f"  ❌ reviews/: {len(review_files)} files, none with >= {args.min_reviews} reviews"
            )
    else:
        failures.append("reviews/ directory missing")
        print("  ❌ reviews/: MISSING")

    # 5. images/<itemid>/01.*
    images_dir = shop_dir / "images"
    if images_dir.exists():
        subdirs = [d for d in images_dir.iterdir() if d.is_dir()]
        valid_subdirs = []
        for sd in subdirs:
            first_img = next(iter(sd.iterdir()), None) if list(sd.iterdir()) else None
            if first_img and first_img.stat().st_size >= args.min_image_size:
                valid_subdirs.append((sd, first_img))
        if valid_subdirs:
            print(
                f"  ✅ images/: {len(subdirs)} product dirs, {len(valid_subdirs)} with image >= {args.min_image_size} bytes"
            )
            for sd, img in valid_subdirs[:3]:
                print(f"     - {sd.name}/{img.name}: {img.stat().st_size:,} bytes")
        else:
            failures.append(f"images/ has no product dir with image >= {args.min_image_size} bytes")
            print(f"  ❌ images/: {len(subdirs)} product dirs, none with valid image")
    else:
        failures.append("images/ directory missing")
        print("  ❌ images/: MISSING")

    print()
    if failures:
        print(f"❌ {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        sys.exit(1)
    else:
        print("✅ All checks pass. Output is well-formed.")


if __name__ == "__main__":
    main()
