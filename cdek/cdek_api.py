#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional

import requests


class CdekApiError(RuntimeError):
    pass


class CdekClient:
    """Minimal CDEK API v2 client for GitHub Actions and integrations."""

    def __init__(self, client_id: str, client_secret: str, base_url: str = "https://api.cdek.ru/v2"):
        if not client_id or not client_secret:
            raise CdekApiError("CDEK_CLIENT_ID и CDEK_CLIENT_SECRET не заданы")
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self._token: Optional[str] = None
        self._token_expires_at = 0.0

    @classmethod
    def from_env(cls) -> "CdekClient":
        return cls(
            os.environ.get("CDEK_CLIENT_ID", "").strip(),
            os.environ.get("CDEK_CLIENT_SECRET", "").strip(),
            os.environ.get("CDEK_API_BASE_URL", "https://api.cdek.ru/v2").strip(),
        )

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        response = self.session.post(
            f"{self.base_url}/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=30,
        )
        if not response.ok:
            raise CdekApiError(
                f"Ошибка авторизации СДЭК: HTTP {response.status_code}: {response.text[:500]}"
            )

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise CdekApiError("СДЭК не вернул access_token")

        expires_in = int(payload.get("expires_in", 3600))
        self._token = token
        self._token_expires_at = time.time() + max(60, expires_in - 60)
        return token

    def _request(self, method: str, path: str, *, json_body: Optional[Dict[str, Any]] = None) -> Any:
        token = self._get_token()
        response = self.session.request(
            method,
            f"{self.base_url}/{path.lstrip('/')}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=json_body,
            timeout=45,
        )
        if not response.ok:
            raise CdekApiError(
                f"Ошибка СДЭК API {path}: HTTP {response.status_code}: {response.text[:1000]}"
            )
        if not response.content:
            return None
        return response.json()

    def auth_check(self) -> Dict[str, Any]:
        self._get_token()
        return {"status": "УСПЕШНО", "message": "Авторизация CDEK API v2 прошла успешно"}

    @staticmethod
    def _calculator_payload(
        from_code: int,
        to_code: int,
        weight_g: int,
        length_cm: int,
        width_cm: int,
        height_cm: int,
        order_type: int = 1,
    ) -> Dict[str, Any]:
        if min(weight_g, length_cm, width_cm, height_cm) <= 0:
            raise CdekApiError("Вес и габариты должны быть больше нуля")
        return {
            "type": order_type,
            "from_location": {"code": from_code},
            "to_location": {"code": to_code},
            "packages": [
                {
                    "weight": weight_g,
                    "length": length_cm,
                    "width": width_cm,
                    "height": height_cm,
                }
            ],
        }

    def tariff_list(
        self,
        from_code: int,
        to_code: int,
        weight_g: int,
        length_cm: int,
        width_cm: int,
        height_cm: int,
        order_type: int = 1,
    ) -> Any:
        payload = self._calculator_payload(
            from_code, to_code, weight_g, length_cm, width_cm, height_cm, order_type
        )
        return self._request("POST", "/calculator/tarifflist", json_body=payload)

    def tariff(
        self,
        tariff_code: int,
        from_code: int,
        to_code: int,
        weight_g: int,
        length_cm: int,
        width_cm: int,
        height_cm: int,
        order_type: int = 1,
    ) -> Any:
        payload = self._calculator_payload(
            from_code, to_code, weight_g, length_cm, width_cm, height_cm, order_type
        )
        payload["tariff_code"] = tariff_code
        return self._request("POST", "/calculator/tariff", json_body=payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CDEK API v2 utility")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("auth-check", help="Проверить авторизацию CDEK API")

    tariffs = sub.add_parser("tariff-list", help="Получить доступные тарифы")
    tariffs.add_argument("--from-code", type=int, required=True, help="Код города отправления СДЭК")
    tariffs.add_argument("--to-code", type=int, required=True, help="Код города назначения СДЭК")
    tariffs.add_argument("--weight", type=int, required=True, help="Вес, г")
    tariffs.add_argument("--length", type=int, required=True, help="Длина, см")
    tariffs.add_argument("--width", type=int, required=True, help="Ширина, см")
    tariffs.add_argument("--height", type=int, required=True, help="Высота, см")
    tariffs.add_argument("--type", type=int, default=1, choices=[1, 2], help="Тип заказа СДЭК")

    tariff = sub.add_parser("tariff", help="Рассчитать конкретный тариф")
    tariff.add_argument("--tariff-code", type=int, required=True)
    tariff.add_argument("--from-code", type=int, required=True)
    tariff.add_argument("--to-code", type=int, required=True)
    tariff.add_argument("--weight", type=int, required=True)
    tariff.add_argument("--length", type=int, required=True)
    tariff.add_argument("--width", type=int, required=True)
    tariff.add_argument("--height", type=int, required=True)
    tariff.add_argument("--type", type=int, default=1, choices=[1, 2])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        client = CdekClient.from_env()
        if args.command == "auth-check":
            result = client.auth_check()
        elif args.command == "tariff-list":
            result = client.tariff_list(
                args.from_code,
                args.to_code,
                args.weight,
                args.length,
                args.width,
                args.height,
                args.type,
            )
        else:
            result = client.tariff(
                args.tariff_code,
                args.from_code,
                args.to_code,
                args.weight,
                args.length,
                args.width,
                args.height,
                args.type,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CdekApiError, requests.RequestException, ValueError) as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
