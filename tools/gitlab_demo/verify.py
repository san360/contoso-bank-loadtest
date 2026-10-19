"""Verify HTTPS, real local-password login and the project-specific runner."""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from .common import DemoError, GitLab, http_json, read_json, write_json
from .pipeline import now


class LoginForm(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and fields.get("name") == "authenticity_token":
            self.token = fields.get("value")


def local_login(url: str, password: str) -> str:
    cookies = http.cookiejar.CookieJar()
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    with browser.open(url + "/users/sign_in", timeout=90) as response:
        form = LoginForm()
        form.feed(response.read().decode("utf-8"))
    if not form.token:
        raise DemoError("GitLab did not return its local-auth CSRF login form")
    request = urllib.request.Request(
        url + "/users/sign_in",
        data=urllib.parse.urlencode({
            "authenticity_token": form.token,
            "user[login]": "root",
            "user[password]": password,
            "user[remember_me]": "0",
        }).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": url + "/users/sign_in"},
    )
    with browser.open(request, timeout=90) as response:
        if "/users/sign_in" in response.url:
            raise DemoError("GitLab rejected the actual local username/password login")
        response.read()
    with browser.open(url + "/api/v4/user", timeout=90) as response:
        user = json.loads(response.read())
    if user.get("username") != "root":
        raise DemoError("Local login did not establish the expected root user session")
    return user["username"]


def verify(folder: Path, *, wait_seconds: int = 900) -> dict:
    credentials = read_json(folder / "secrets.json")
    resources = read_json(folder / "resources.json")
    url = resources["gitlab_url"]
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            readiness = http_json(url + "/-/readiness?all=1")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            if time.monotonic() >= deadline:
                raise DemoError(f"Public HTTPS GitLab readiness did not succeed: {error}") from None
            time.sleep(15)
        else:
            break
    username = local_login(url, credentials["gitlab_root_password"])
    report = {
        "checked_at": now(), "gitlab_url": url, "https_verified": True,
        "readiness": readiness, "local_password_login": username,
    }
    config_path = folder / "gitlab.json"
    if config_path.exists():
        config = read_json(config_path)
        client = GitLab(url, credentials["project_token"], config["project_id"])
        deadline = time.monotonic() + 300
        while True:
            runner = client.request("GET", f"runners/{config['runner_id']}")
            if runner.get("status") == "online":
                break
            if time.monotonic() >= deadline:
                raise DemoError(f"Project runner did not become online: {runner.get('status')}")
            time.sleep(10)
        report.update(
            project_url=config["project_url"],
            main_sha=client.get("/repository/branches/main")["commit"]["id"],
            runner_id=config["runner_id"], runner_status=runner["status"],
            gitlab_version=client.request("GET", "version").get("version"),
        )
    write_json(folder / "access-check.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.private_dir, wait_seconds=args.wait_seconds), indent=2))
        return 0
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
