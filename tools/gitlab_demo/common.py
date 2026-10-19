from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEMO_BRANCH = "demo/merchant-enrichment"
FEATURE_FILE = "src/transaction_enrichment.py"
TEST_ID = "contoso-gitlab-transactions"


class DemoError(RuntimeError):
    pass


class APIError(DemoError):
    def __init__(self, method: str, path: str, status: int):
        self.status = status
        super().__init__(f"GitLab {method} {path} returned HTTP {status}")


def run(arguments: list[str], *, timeout: int = 120, env=None) -> str:
    executable = shutil.which(arguments[0])
    if executable is None:
        raise DemoError(f"Required executable is missing: {arguments[0]}")
    result = subprocess.run(
        [executable, *arguments[1:]], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, env=env, cwd=ROOT,
    )
    if result.returncode:
        raise DemoError(f"{arguments[0]} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def required(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise DemoError(f"Required environment variable is missing: {name}")
    return value


def sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise DemoError("A full lowercase 40-character commit SHA is required")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2)
        stream.write("\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def contract_digest(root: Path = ROOT) -> str:
    folder = root / "loadtests" / "gitlab-demo"
    yaml = (folder / "transactions.yaml").read_text(encoding="utf-8")
    profile = read_json(folder / "transactions.json")
    canonical = yaml + "\n" + json.dumps(profile, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def https_origin(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise DemoError("An HTTPS origin without credentials, path, query or fragment is required")
    return value.rstrip("/")


def http_json(url: str, *, payload=None, headers=None, method=None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
    return json.loads(body) if body else None


class GitLab:
    def __init__(self, url: str, token: str, project_id: int | str | None = None):
        self.url = https_origin(url)
        self.token = token
        self.project_id = project_id

    @property
    def project(self) -> str:
        if self.project_id is None:
            raise DemoError("GitLab project ID has not been configured")
        return f"projects/{urllib.parse.quote(str(self.project_id), safe='')}"

    def request(self, method: str, path: str, payload=None, *, raw: bool = False):
        if path.startswith("/") or "://" in path:
            raise DemoError("GitLab API path must be relative to this instance")
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"{self.url}/api/v4/{path}", data=data, method=method,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            # API bodies can echo secret variable values. Never include them in logs.
            raise APIError(method, path, error.code) from None
        return body if raw else (json.loads(body) if body else None)

    def get(self, suffix: str = ""):
        return self.request("GET", self.project + suffix)


def azure(arguments: list[str], subscription: str, *, timeout: int = 180) -> str:
    return run(
        ["az", *arguments, "--subscription", subscription, "--only-show-errors"],
        timeout=timeout,
    )
