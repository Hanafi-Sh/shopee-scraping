"""Data models for shopee-dommie.

These are the structured records we extract from rendered Shopee DOM. They
map directly to the JSON files we write under data/<shop>/.

Conventions:
- All IDs are strings (Shopee's IDs exceed 32-bit int range).
- All prices are integers in IDR (no decimal, no thousand separators).
- Image URLs are full-size (we strip the `_tn` thumbnail suffix).
- The shop's own `shopid` is the FIRST number in `-i.{shopid}.{itemid}`.
  The per-product `itemid` is the SECOND number.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ProductSummary:
    """Lightweight record from the shop page — enough to identify a product."""

    shopid: str
    itemid: str
    name: str  # decoded from URL slug; rough but useful
    url: str  # absolute URL (we prepend https://shopee.co.id if relative)
    image_url: str | None = None  # primary thumbnail URL

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProductDetail:
    """Full record from a product detail page."""

    shopid: str
    itemid: str
    name: str
    price: int | None = None  # IDR
    original_price: int | None = None  # for discount display
    sold: int | None = None
    rating: float | None = None
    rating_count: int | None = None
    stock: int | None = None
    description: str = ""
    images: list[str] = field(default_factory=list)  # full-size URLs
    variants: list[dict] = field(default_factory=list)
    category: str | None = None
    shop_name: str | None = None
    url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Review:
    """One review entry."""

    review_id: str
    author: str
    rating: int
    comment: str
    posted_at: str  # ISO date or relative ("2 minggu lalu")
    images: list[str] = field(default_factory=list)  # reviewer-uploaded photos

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ShopInfo:
    """Shop-level metadata (header area of shop page)."""

    username: str  # e.g. "erigostore"
    name: str  # display name, e.g. "ERIGO Official Shop"
    is_official: bool = False
    is_mall: bool = False
    follower_count: int | None = None
    product_count: int | None = None
    rating: float | None = None
    response_rate: str | None = None
    response_time: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)
