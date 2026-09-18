from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class LigaCategory:
    source_id: str
    name: str
    parent_id: Optional[str] = None


@dataclass(frozen=True)
class LigaOffer:
    source_id: str
    vendor_code: str
    kit_sku: str
    available: bool
    category_id: Optional[str]
    name: str
    description: str
    vendor: str
    price: Optional[Decimal]
    currency: str
    barcode: str
    weight: str
    dimensions: str
    source_url: str
    country_of_origin: str = ''
    manufacturer_warranty: bool = False
    images: list[str] = field(default_factory=list)
    params: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class FeedSnapshot:
    categories: dict[str, LigaCategory]
    offers: list[LigaOffer]
    complete: bool
