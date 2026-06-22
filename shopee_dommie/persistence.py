"""Atomic JSON + CSV writers for shopee-dommie output.

Output structure (under data/<shop>/):
    shop_info.json           Shop metadata (one per shop)
    products.json            Product list (itemid, shopid, name, url, image)
    products_enriched.json   Full detail per product (description, price, etc.)
    products_enriched.csv    Same data, flat for spreadsheets/pandas
    reviews/<itemid>.json    Reviews for one product (list)
    reviews/<itemid>.csv     Same reviews, flat for spreadsheets/pandas
    images/<itemid>/NN.webp  Product images

All writes are atomic: write to <path>.tmp, then rename. This guarantees
that a crash mid-write never leaves a half-written file.

CSV notes:
- UTF-8 with BOM (utf-8-sig) so Excel opens it correctly with non-ASCII
- Nested fields (variants, images, etc.) are JSON-stringified in their cell
- One row per record (one row per product, one row per review)
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .models import ProductDetail, ProductSummary, Review, ShopInfo


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to path atomically. Creates parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp_path.rename(path)


def atomic_write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str] | None = None) -> None:
    """Write CSV atomically. Rows are dicts; fieldnames auto-detected from first row if not given.

    - UTF-8 with BOM (utf-8-sig) for Excel compatibility
    - List/dict values are JSON-stringified in their cell
    - Missing keys become empty cells
    """
    rows = list(rows)
    if not rows:
        # Empty file: still write a header if provided
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8-sig")
        return

    if fieldnames is None:
        # Use the first row's keys, then add any keys from later rows in order
        seen: list[str] = []
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.append(k)
        fieldnames = seen

    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=fieldnames,
        extrasaction="ignore",  # skip keys not in fieldnames
        quoting=csv.QUOTE_MINIMAL,
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({k: _stringify(r.get(k, "")) for k in fieldnames})

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(buf.getvalue(), encoding="utf-8-sig")
    tmp_path.rename(path)


def shop_dir(out_root: Path, shop_username: str) -> Path:
    return out_root / shop_username


def save_shop_info(out_root: Path, shop: ShopInfo) -> Path:
    path = shop_dir(out_root, shop.username) / "shop_info.json"
    atomic_write_json(path, shop.to_dict())
    return path


def save_products_list(out_root: Path, shop_username: str, products: list[ProductSummary]) -> Path:
    """Save the lightweight products.json (just identifiers + names)."""
    path = shop_dir(out_root, shop_username) / "products.json"
    atomic_write_json(path, [p.to_dict() for p in products])
    return path


def load_products_list(out_root: Path, shop_username: str) -> list[ProductSummary]:
    """Load products.json if it exists, else empty list."""
    path = shop_dir(out_root, shop_username) / "products.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [ProductSummary(**item) for item in data]


def save_enriched_product(
    out_root: Path,
    shop_username: str,
    detail: ProductDetail,
    *,
    also_csv: bool = True,
) -> Path:
    """Append/update one enriched product. Loads existing file, replaces if same itemid, else appends.

    If also_csv=True (default), also writes a flat CSV next to the JSON file
    with the same records.
    """
    path = shop_dir(out_root, shop_username) / "products_enriched.json"
    existing: list[dict] = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = []

    new_entry = detail.to_dict()
    # Replace if itemid already present, else append
    for i, entry in enumerate(existing):
        if entry.get("itemid") == detail.itemid:
            existing[i] = new_entry
            break
    else:
        existing.append(new_entry)

    atomic_write_json(path, existing)

    if also_csv:
        csv_path = path.with_suffix(".csv")
        atomic_write_csv(csv_path, existing)

    return path


def save_reviews(
    out_root: Path,
    shop_username: str,
    itemid: str,
    reviews: list[Review],
    *,
    also_csv: bool = True,
) -> Path:
    path = shop_dir(out_root, shop_username) / "reviews" / f"{itemid}.json"
    records = [r.to_dict() for r in reviews]
    atomic_write_json(path, records)
    if also_csv:
        csv_path = path.with_suffix(".csv")
        atomic_write_csv(csv_path, records)
    return path
