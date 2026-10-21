"""Loopback-only fixtures and helpers; never proxies to the demo deployment."""

from __future__ import annotations

import locust

import io
import json
import os
import secrets
import sys
from argparse import Namespace
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path
from time import monotonic
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs

from gevent.pywsgi import WSGIServer
from locust.env import Environment
from locust.event import Events
import requests

MIGRATIONS = Path(__file__).resolve().parents[1]
ROOT = MIGRATIONS.parent.parent
sys.path.insert(0, str(MIGRATIONS))

import jmeter_runtime
import retail_locustfile as retail
import secrets_locustfile as secrets_plan

CONTROL_VARIABLES = {
    "BASE_URL", "API_KEY", "ACCOUNT_ID", "ACCOUNTS_CSV", "PAGE_SIZE",
    "THINK_TIME_MS", "DEMO_LOG_SECRET"
}
SYNTHETIC_CSV = (
    "accountId,customerName,sortCode,segment\n"
    "FIXTURE-A,Fixture Customer A,00-00-01,retail\n"
    "FIXTURE-B,Fixture Customer B,00-00-02,premier\n"
    "FIXTURE-C,Fixture Customer C,00-00-03,business\n"
)


@contextmanager
def controlled_environment(**values: str) -> Iterator[None]:
    clean = {
        key: value for key, value in os.environ.items()
        if key not in CONTROL_VARIABLES and not key.startswith("LOCUST_")
    }
    clean.update(values)
    with patch.dict(os.environ, clean, clear=True):
        yield


class BankFixture:
    def __init__(self) -> None:
        self.api_key = secrets.token_urlsafe(24)
        self.records: list[dict[str, Any]] = []
        self.statuses: dict[str, int] = {}
        self.transfer_response: bytes | None = None
        self.redirect_balance = False
        self.server = WSGIServer(("127.0.0.1", 0), self.application, log=None)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> None:
        self.server.start()
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(f"{self.url}/health", timeout=(1, 1))
            response.raise_for_status()
        self.records.clear()

    def stop(self) -> None:
        self.server.stop(timeout=1)

    def application(
        self,
        environ: dict[str, Any],
        start_response: Callable[[str, list[tuple[str, str]]], object],
    ) -> list[bytes]:
        path = environ["PATH_INFO"]
        method = environ["REQUEST_METHOD"]
        length = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(length).decode("utf-8")
        if path.endswith("/balance") or path == "/fixture/redirected-balance":
            operation, default_status = "GetBalance", 200
        elif path.endswith("/transactions"):
            operation, default_status = "GetTransactions", 200
        elif path == "/api/transfers" and method == "POST":
            operation, default_status = "CreateTransfer", 201
        elif path.startswith("/api/transfers/"):
            operation, default_status = "GetTransferStatus", 200
        elif path.startswith("/api/statements/"):
            operation, default_status = "GenerateStatement", 200
        elif path == "/health":
            operation, default_status = "HealthCheck", 200
        else:
            operation, default_status = "Unknown", 404
        self.records.append({
            "operation": operation,
            "method": method,
            "path": path,
            "query": parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True),
            "body": body,
            "accept": environ.get("HTTP_ACCEPT"),
            "content_type": environ.get("CONTENT_TYPE"),
            "cookie": environ.get("HTTP_COOKIE", ""),
            "authorization_present": bool(environ.get("HTTP_AUTHORIZATION")),
            "authorization_matches": (
                environ.get("HTTP_AUTHORIZATION") == f"Bearer {self.api_key}"
            ),
            "peer_port": environ.get("REMOTE_PORT"),
            "time": monotonic(),
        })
        headers = [("Content-Type", "application/json")]
        status = self.statuses.get(operation, default_status)
        payload = json.dumps({"ok": True}).encode()
        if operation in {"GetBalance", "CreateTransfer"}:
            headers.append(("Set-Cookie", "fixture_session=local; Path=/"))
        if operation == "CreateTransfer":
            payload = (
                self.transfer_response
                if self.transfer_response is not None
                else json.dumps({"transaction_id": f"fixture-{len(self.records)}"}).encode()
            )
        if self.redirect_balance and path.endswith("/balance"):
            status = 302
            headers.append(("Location", "/fixture/redirected-balance"))
        if status in {204, 304}:
            payload = b""
        headers.append(("Content-Length", str(len(payload))))
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [payload]


def environment_for(user_class: type[locust.User], host: str, duration: int = 300) -> Environment:
    environment = Environment(
        user_classes=[user_class],
        host=host,
        events=Events(),
        parsed_options=Namespace(run_time=duration, num_users=1, spawn_rate=1.0),
        stop_timeout=2,
    )
    environment.create_local_runner()
    return environment


def instantiate(user_class: type[locust.User], environment: Environment) -> locust.User:
    with patch.object(user_class, "host", environment.host):
        return user_class(environment)


def stats_from_json(path: Path) -> dict[str, dict[str, Any]]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not all(
        isinstance(row, dict) and {"name", "num_requests", "num_failures"} <= row.keys()
        for row in records
    ):
        raise ValueError("Locust's final JSON statistics do not have the expected shape.")
    return {row["name"]: row for row in records}


def reference_decisions(count: int) -> list[tuple[bool, ...]]:
    executions = [0, 0, 0, 0]
    result = []
    for visit in range(1, count + 1):
        choices = tuple(
            (100.0 * executions[index] + 50.0) / visit < percent
            for index, percent in enumerate((60, 20, 15, 5))
        )
        for index, selected in enumerate(choices):
            executions[index] += int(selected)
        result.append(choices)
    return result


def source_properties() -> dict[str, str]:
    content = (ROOT / "loadtests" / "jmeter" / "user.properties").read_text()
    return {
        key.strip(): value.strip()
        for line in io.StringIO(content)
        if line.strip() and not line.lstrip().startswith("#")
        for key, value in [line.rstrip("\r\n").split("=", 1)]
    }
