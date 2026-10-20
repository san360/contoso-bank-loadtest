"""Scoped ALT REST calls, including binary uploads, without changing az account."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from tools.gitlab_demo.azure_http import AzureAPIError, ScopedLoadTest
from tools.gitlab_demo.common import DemoError

API_VERSION = "2026-04-01"


def trusted_artifact_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    valid_host = re.fullmatch(
        r"[a-z0-9]{3,24}\.(?:blob\.core\.windows\.net|z(?:[1-9]|[1-4][0-9]|50)\.blob\.storage\.azure\.net)",
        parsed.hostname or "",
    )
    if (parsed.scheme != "https" or not valid_host or parsed.username or parsed.password
            or parsed.port not in {None, 443} or parsed.fragment or parsed.path in {"", "/"}):
        raise DemoError("Azure returned an unexpected request-results artifact origin")
    return value


class LoadTest(ScopedLoadTest):
    def request(self, method: str, path: str, payload=None, *, data: bytes | None = None):
        if path.startswith("/") or "://" in path or "#" in path:
            raise DemoError("ALT path must be relative to its ARM-resolved endpoint")
        separator = "&" if "?" in path else "?"
        url = f"{self.endpoint}/{path}{separator}api-version={API_VERSION}"
        body = data if data is not None else (json.dumps(payload).encode() if payload is not None else None)
        content_type = "application/octet-stream" if data is not None else "application/merge-patch+json"
        request = urllib.request.Request(
            url, data=body, method=method,
            headers={"Authorization": "Bearer " + self._token, "Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                result = response.read()
        except urllib.error.HTTPError as error:
            # Never emit response bodies: run/file responses can contain signed artifact URLs.
            raise AzureAPIError(error.code, "AzureLoadTesting", f"{method} {path.split('?')[0]} failed") from None
        return json.loads(result) if result else None

    def optional(self, path: str):
        try:
            return self.request("GET", path)
        except AzureAPIError as error:
            if error.status == 404:
                return None
            raise
