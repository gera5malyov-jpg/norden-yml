#!/usr/bin/env python3
import json
import os
from typing import Any, Dict, Optional

import requests


class WebasystAPIError(RuntimeError):
    pass


class WebasystClient:
    """
    Webasyst API client for profikompany.ru.

    This installation currently rejects Authorization: Bearer, while the same
    valid token works as access_token. Therefore:
      * GET API methods: access_token is sent as a query parameter.
      * POST API methods: access_token is sent in the POST body.

    The token is never written to repository files or normal logs.
    """

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None, timeout: int = 60):
        self.base_url = (base_url or os.getenv("WEBASYST_BASE_URL") or "https://profikompany.ru").rstrip("/")
        self.token = (token or os.getenv("WEBASYST_API_TOKEN") or "").strip()
        self.timeout = timeout

        if not self.token:
            raise WebasystAPIError("WEBASYST_API_TOKEN is not set")

        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "norden-yml-webasyst-automation/1.0",
        })

    def _safe_error(self, response: requests.Response) -> str:
        try:
            payload = response.json()
            rendered = json.dumps(payload, ensure_ascii=False)
        except ValueError:
            rendered = response.text[:500]
        return rendered.replace(self.token, "***")

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

        request_params = dict(params or {})
        request_data = dict(data or {})

        if verb == "GET":
            # Bearer header is stripped/rejected by the current hosting setup.
            request_params["access_token"] = self.token
            request_params.setdefault("format", "json")
            response = self.session.get(
                url,
                params=request_params,
                timeout=self.timeout,
                allow_redirects=True,
            )
        elif verb == "POST":
            # Webasyst officially supports token transfer in the POST body.
            request_data["access_token"] = self.token
            request_params.setdefault("format", "json")
            response = self.session.post(
                url,
                params=request_params,
                data=request_data,
                files=files,
                timeout=self.timeout,
                allow_redirects=True,
            )
        else:
            raise ValueError(f"Unsupported HTTP method: {http_method}")

        try:
            payload = response.json()
        except ValueError as exc:
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
