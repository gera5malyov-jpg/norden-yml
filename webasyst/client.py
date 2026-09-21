#!/usr/bin/env python3
import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests


class WebasystAPIError(RuntimeError):
    pass


def _flatten_form(value: Any, prefix: str = "") -> List[Tuple[str, str]]:
    """Flatten nested Python dict/list values into PHP-style form keys."""
    out: List[Tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}[{key}]" if prefix else str(key)
            out.extend(_flatten_form(item, name))
        return out
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            name = f"{prefix}[{index}]"
            out.extend(_flatten_form(item, name))
        return out
    if value is None:
        return out
    if isinstance(value, bool):
        value = "1" if value else "0"
    out.append((prefix, str(value)))
    return out


class WebasystClient:
    """
    Webasyst API client for profikompany.ru.

    The hosting configuration currently rejects Authorization: Bearer while
    accepting the same access token through Webasyst's access_token parameter.
    GET methods therefore receive access_token in the query; POST methods
    receive it in the request body. The token is never committed to the repo.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: int = 90,
        min_request_interval: float = 0.45,
    ):
        self.base_url = (base_url or os.getenv("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/")
        self.token = (token or os.getenv("WEBASYST_API_TOKEN") or "").strip()
        self.timeout = timeout
        self.min_request_interval = max(0.0, float(min_request_interval))
        self._last_request_at = 0.0

        if not self.token:
            raise WebasystAPIError("WEBASYST_API_TOKEN is not set")

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "norden-yml-webasyst-automation/1.0",
        })

    def _pace(self) -> None:
        delay = self.min_request_interval - (time.monotonic() - self._last_request_at)
        if delay > 0:
            time.sleep(delay)
        self._last_request_at = time.monotonic()

    def _safe_error(self, response: requests.Response) -> str:
        try:
            payload = response.json()
            rendered = json.dumps(payload, ensure_ascii=False)
        except ValueError:
            rendered = response.text[:800]
        return rendered.replace(self.token, "***")

    def call(
        self,
        method: str,
        *,
        http_method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Any] = None,
    ) -> Any:
        if not method or "/" in method or method.startswith("."):
            raise ValueError("Invalid Webasyst API method")

        url = f"{self.base_url}/api.php/{method}"
        verb = http_method.upper()
        request_params: Dict[str, Any] = dict(params or {})
        request_params.setdefault("format", "json")

        for attempt in range(10):
            self._pace()

            if verb == "GET":
                request_params["access_token"] = self.token
                response = self.session.get(
                    url,
                    params=request_params,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            elif verb == "POST":
                request_data: Dict[str, Any] = dict(data or {})
                request_data["access_token"] = self.token
                encoded_data = _flatten_form(request_data)
                response = self.session.post(
                    url,
                    params=request_params,
                    data=encoded_data,
                    files=files,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
            else:
                raise ValueError(f"Unsupported HTTP method: {http_method}")

            if response.status_code == 429:
                wait = response.headers.get("Retry-After")
                try:
                    delay = float(wait) if wait else min(60.0, 3.0 * (attempt + 1))
                except ValueError:
                    delay = min(60.0, 3.0 * (attempt + 1))
                time.sleep(delay)
                continue

            if response.status_code >= 500:
                time.sleep(min(30.0, 2.0 ** attempt))
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise WebasystAPIError(
                    f"Webasyst returned non-JSON response: HTTP {response.status_code}: {self._safe_error(response)}"
                ) from exc

            if response.status_code >= 400:
                raise WebasystAPIError(
                    f"HTTP {response.status_code}: {self._safe_error(response)}"
                )

            if isinstance(payload, dict) and payload.get("error"):
                description = payload.get("error_description")
                suffix = f": {description}" if description else ""
                raise WebasystAPIError(f"{payload['error']}{suffix}")

            return payload

        raise WebasystAPIError(f"Webasyst retries exhausted for {method}")
