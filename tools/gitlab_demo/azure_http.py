"""Explicit-subscription ALT REST access without mutating a shared CLI profile."""

from __future__ import annotations

import json
import argparse
import sys
import subprocess
import urllib.error
import urllib.parse
from pathlib import Path

from .common import TEST_ID, DemoError, azure, http_json, https_origin, read_json, run
from tools.loadtest_gate import run_snapshot

ALT_AUDIENCE = "https://cnt-prod.loadtesting.azure.com"
ALT_API_VERSION = "2025-03-01-preview"

def scoped_token(subscription: str, tenant: str, audience: str) -> str:
    credential = json.loads(run([
        "az", "account", "get-access-token", "--subscription", subscription,
        "--resource", audience, "--only-show-errors", "-o", "json",
    ]))
    if credential.get("subscription") != subscription or credential.get("tenant") != tenant:
        raise DemoError("Azure CLI returned credentials for a different subscription or directory")
    return credential["accessToken"]


class AzureAPIError(DemoError):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(f"ALT HTTP {status} ({code}): {message}")


class ScopedLoadTest:
    def __init__(self, subscription: str, tenant: str, resource_id: str):
        if not resource_id.lower().startswith(f"/subscriptions/{subscription.lower()}/"):
            raise DemoError("ALT resource ID does not belong to the explicitly selected subscription")
        resource = json.loads(azure([
            "resource", "show", "--ids", resource_id, "-o", "json",
        ], subscription))
        endpoint = (resource.get("properties") or {}).get("dataPlaneURI")
        if not endpoint:
            raise DemoError("ARM did not return the selected ALT resource's dataPlaneURI")
        self.endpoint = https_origin(endpoint if endpoint.startswith("https://") else "https://" + endpoint)
        self._token = scoped_token(subscription, tenant, ALT_AUDIENCE)

    def request(self, method: str, path: str, payload=None):
        if path.startswith("/") or "://" in path:
            raise DemoError("ALT API path must be relative to the ARM-resolved endpoint")
        separator = "&" if "?" in path else "?"
        url = self.endpoint + "/" + path + separator + "api-version=" + ALT_API_VERSION
        try:
            return http_json(
                url, method=method, payload=payload,
                headers={
                    "Authorization": "Bearer " + self._token,
                    "Content-Type": "application/merge-patch+json" if method == "PATCH" else "application/json",
                },
            )
        except urllib.error.HTTPError as error:
            try:
                details = json.loads(error.read()).get("error", {})
            except json.JSONDecodeError:
                raise AzureAPIError(error.code, "NonJsonError", "Service returned a non-JSON error") from None
            raise AzureAPIError(
                error.code, str(details.get("code", "Unknown")),
                str(details.get("message", "No service error message")),
            ) from None

    def get_run(self, run_id: str) -> dict:
        return self.request("GET", "test-runs/" + urllib.parse.quote(run_id, safe=""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["run", "components", "metric-definitions"])
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--test-id", default=TEST_ID)
    args = parser.parse_args()
    try:
        resources = read_json(args.private_dir / "resources.json")
        client = ScopedLoadTest(
            resources["subscription_id"], resources["tenant_id"], resources["load_test_id"],
        )
        if args.operation == "run":
            if not args.run_id:
                raise DemoError("--run-id is required for a run read")
            payload = run_snapshot(client.get_run(args.run_id))
        else:
            route = "app-components" if args.operation == "components" else "server-metrics-config"
            payload = client.request("GET", f"tests/{urllib.parse.quote(args.test_id, safe='')}/{route}")
        print(json.dumps(payload, indent=2))
        return 0
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
