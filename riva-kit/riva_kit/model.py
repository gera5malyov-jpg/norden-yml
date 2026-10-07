from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class RivaCategory:
    source_id: str
    name: str
    parent_id: Optional[str] = None


@dataclass(frozen=True)
class RivaOffer:
    source_id: str
    group_id: str
    article: str
    kit_sku: str
    count: int
    in_stock: bool
    category_id: Optional[str]
    name: str
    description: str
    price: Optional[Decimal]
    currency: str
    barcode: str
    weight: str
    source_url: str
    images: list[str] = field(default_factory=list)
    params: dict[str, list[str]] = field(default_factory=dict)
