#!/usr/bin/env python3
import json
import os
from typing import Any, Dict, Optional

import requests


class WebasystAPIError(RuntimeError):
    pass


class WebasystClient:
    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None, timeout: int = 60):
        self.base_url = (base_url or os.getenv("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/")
        self.token = token or os.getenv("WEBASYST_API_TOKEN")
        self.timeout = timeout

        if not self.token:
            raise WebasystAPIError("WEBASYST_API_TOKEN is not set")

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": "norden-yml-webasyst-automation/1.0",
        })

    def call(
        self,
        method: str,
        *,
        http_method: str = "GET",
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not method or "/" in method or method.startswith("."):
            raise ValueError("Invalid Webasyst API method")

        url = f"{self.base_url}/api.php/{method}"
        verb = http_method.upper()

        if verb == "GET":
            response = self.session.get(url, params=params or {}, timeout=self.timeout)
        elif verb == "POST":
            response = self.session.post(
                url,
                params=params or {},
                data=data or {},
                files=files,
                timeout=self.timeout,
            )
        else:
            raise ValueError(f"Unsupported HTTP method: {http_method}")

        try:
            payload = response.json()
        except ValueError as exc:
            snippet = response.text[:500].replace(self.token, "***")
            raise WebasystAPIError(
                f"Webasyst returned non-JSON response: HTTP {response.status_code}: {snippet}"
            ) from exc

        if response.status_code >= 400:
            raise WebasystAPIError(
                f"HTTP {response.status_code}: {json.dumps(payload, ensure_ascii=False)}"
            )

        if isinstance(payload, dict) and payload.get("error"):
            description = payload.get("error_description")
            suffix = f": {description}" if description else ""
            raise WebasystAPIError(f"{payload['error']}{suffix}")

        return payload
