#!/usr/bin/env python3
import argparse
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List


class DalliApiError(RuntimeError):
    pass


class DalliClient:
    """Minimal Dalli API v1 client for the Saint Petersburg account."""

    def __init__(self, token: str, base_url: str = "https://spbapi.dalli-service.com/v1"):
        token = (token or "").strip()
        if not token:
            raise DalliApiError("DALLI_TOKEN не задан")
        self.token = token
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_env(cls) -> "DalliClient":
        return cls(
            os.environ.get("DALLI_TOKEN", ""),
            os.environ.get("DALLI_API_BASE_URL", "https://spbapi.dalli-service.com/v1"),
        )

    def _post_xml(self, path: str, root: ET.Element) -> ET.Element:
        body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        req = urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/xml; charset=utf-8",
                "Accept": "application/xml, text/xml, */*",
                "User-Agent": "megapolis-dalli-integration/1.0",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise DalliApiError(f"Dalli API HTTP {exc.code}: {raw[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise DalliApiError(f"Ошибка соединения с Dalli API: {exc}") from exc

        try:
            parsed = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise DalliApiError(f"Dalli вернул некорректный XML: {raw[:1000]!r}") from exc

        if parsed.get("error"):
            raise DalliApiError(
                f"Dalli API error={parsed.get('error')}: {parsed.get('errormsg', 'неизвестная ошибка')}"
            )
        return parsed

    def _auth(self, parent: ET.Element) -> None:
        ET.SubElement(parent, "auth", {"token": self.token})

    def delivery_cost(
        self,
        to_address: str,
        packages: List[Dict[str, Any]],
        *,
        order_price: float = 0.0,
        insurance_price: float = 0.0,
        partner: str = "DS",
        delivery_type: str = "KUR",
        sender_code: str = "",
        output_x2: bool = True,
        cash_by_card: bool = False,
        base_only: bool = False,
    ) -> Dict[str, Any]:
        if not to_address.strip():
            raise DalliApiError("Не указан адрес получателя")
        if not packages:
            raise DalliApiError("Не указаны грузоместа")

        root = ET.Element("deliverycost")
        self._auth(root)
        ET.SubElement(root, "partner").text = partner
        ET.SubElement(root, "to").text = to_address
        if sender_code:
            ET.SubElement(root, "sendercode").text = sender_code
        ET.SubElement(root, "price").text = f"{float(order_price):.2f}"
        ET.SubElement(root, "inshprice").text = f"{float(insurance_price):.2f}"
        ET.SubElement(root, "cashservices").text = "YES" if cash_by_card else "NO"
        ET.SubElement(root, "withouttax").text = "YES" if base_only else "NO"

        pnode = ET.SubElement(root, "packages")
        for pkg in packages:
            weight = float(pkg["weight_kg"])
            length = float(pkg["length_cm"])
            width = float(pkg["width_cm"])
            height = float(pkg["height_cm"])
            if min(weight, length, width, height) <= 0:
                raise DalliApiError("Вес и габариты грузоместа должны быть больше нуля")
            ET.SubElement(
                pnode,
                "package",
                {
                    "weight": f"{weight:g}",
                    "length": f"{length:g}",
                    "width": f"{width:g}",
                    "height": f"{height:g}",
                },
            )

        if output_x2:
            ET.SubElement(root, "output").text = "x2"
        else:
            ET.SubElement(root, "typedelivery").text = delivery_type

        parsed = self._post_xml("deliverycost", root)
        return self._delivery_cost_to_dict(parsed)

    @staticmethod
    def _delivery_cost_to_dict(root: ET.Element) -> Dict[str, Any]:
        base = {k: v for k, v in root.attrib.items()}
        prices = []
        for node in root.findall("price"):
            item = {k: v for k, v in node.attrib.items()}
            if "price" in item:
                try:
                    item["price"] = float(item["price"])
                except ValueError:
                    pass
            if "delivery_period" in item:
                try:
                    item["delivery_period"] = int(item["delivery_period"])
                except ValueError:
                    pass
            prices.append(item)

        if prices:
            base["prices"] = prices
        elif "price" in base:
            try:
                base["price"] = float(base["price"])
            except ValueError:
                pass
        return base


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dalli API v1 utility (Saint Petersburg)")
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth-check", help="Проверить токен через расчет доставки")
    auth.add_argument("--to", default="Санкт-Петербург, Невский проспект, 1")

    calc = sub.add_parser("delivery-cost", help="Рассчитать стоимость доставки")
    calc.add_argument("--to", required=True, help="Полный адрес получателя")
    calc.add_argument("--weight", type=float, required=True, help="Вес одного места, кг")
    calc.add_argument("--length", type=float, required=True, help="Длина, см")
    calc.add_argument("--width", type=float, required=True, help="Ширина, см")
    calc.add_argument("--height", type=float, required=True, help="Высота, см")
    calc.add_argument("--places", type=int, default=1, help="Количество одинаковых мест")
    calc.add_argument("--order-price", type=float, default=0.0)
    calc.add_argument("--insurance-price", type=float, default=0.0)
    calc.add_argument("--sender-code", default="")
    return parser


def main() -> int:
    import json

    args = build_parser().parse_args()
    try:
        client = DalliClient.from_env()
        if args.command == "auth-check":
            result = client.delivery_cost(
                args.to,
                [{"weight_kg": 1, "length_cm": 10, "width_cm": 10, "height_cm": 10}],
                output_x2=True,
            )
            result = {"status": "УСПЕШНО", "message": "Токен Dalli принят API", "response": result}
        else:
            if args.places < 1:
                raise DalliApiError("Количество мест должно быть не меньше 1")
            pkg = {
                "weight_kg": args.weight,
                "length_cm": args.length,
                "width_cm": args.width,
                "height_cm": args.height,
            }
            result = client.delivery_cost(
                args.to,
                [pkg.copy() for _ in range(args.places)],
                order_price=args.order_price,
                insurance_price=args.insurance_price,
                sender_code=args.sender_code,
                output_x2=True,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (DalliApiError, ValueError, KeyError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
