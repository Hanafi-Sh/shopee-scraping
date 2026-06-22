"""shopee-dommie: Shopee shop scraper via Camoufox DOM extraction.

A post-rejection sibling of the parent shopee-scraping/ project. Where the
parent tried pure-HTTP requests and got blocked by Shopee's anti-bot
(af-ac-enc-dat validation, per-endpoint captcha, sz-token session binding),
this project drives Camoufox for every step — extracting from the rendered
DOM after solving the page-level captcha manually.

Public API (re-exported for convenience):
- ProductDetail, ProductSummary, Review, ShopInfo (data models)
- CaptchaDetector (pause/resume coordination)
- atomic_write_json (file utilities)

Internal modules (sub-package):
- shopee_dommie.shop_page    — Shop page extractors
- shopee_dommie.product_page — Product detail + review extractors
- shopee_dommie.images       — curl_cffi image downloader
- shopee_dommie.persistence  — Atomic JSON writers
- shopee_dommie.captcha      — CaptchaDetector class
- shopee_dommie.models       — Data models
"""

from .captcha import CaptchaDetector
from .models import ProductDetail, ProductSummary, Review, ShopInfo
from .persistence import atomic_write_json

__version__ = "0.2.0"
__all__ = [
    "CaptchaDetector",
    "ProductDetail",
    "ProductSummary",
    "Review",
    "ShopInfo",
    "atomic_write_json",
]
