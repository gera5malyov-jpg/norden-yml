from dataclasses import dataclass, field
from typing import Any

@dataclass
class Product:
    supplier_sku: str
    sku: str
    name: str
    description: str = ""
    purchase_price: float | None = None
    price: float | None = None
    compare_price: float | None = None
    stock: float | None = None
    brand: str = ""
    category: str = ""
    images: list[str] = field(default_factory=list)
    characteristics: dict[str, Any] = field(default_factory=dict)

@dataclass
class ValidationReport:
    total: int = 0
    creates: int = 0
    updates: int = 0
    zero_stock: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False
