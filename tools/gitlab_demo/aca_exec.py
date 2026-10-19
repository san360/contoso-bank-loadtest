"""Bounded noninteractive ACA administration using the Azure CLI's exec protocol.

Protocol reference: Azure/azure-cli containerapp/_ssh_utils.py and _clients.py.
Azure authorization tokens stay in memory and are never printed.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

from .common import DemoError, azure, read_json


def execute(resources: dict, script: str, *, timeout=900) -> str:
    import websocket

    subscription = resources["subscription_id"]
    group = resources["resource_group"]
    app = json.loads(azure([
        "containerapp", "show", "--name", "gitlab", "--resource-group", group, "-o", "json",
    ], subscription))
    revision = app["properties"]["latestRevisionName"]
    replicas = json.loads(azure([
        "containerapp", "replica", "list", "--name", "gitlab", "--resource-group", group,
        "--revision", revision, "-o", "json",
    ], subscription))
    if len(replicas) != 1:
        raise DemoError(f"Expected one GitLab replica for administration, got {len(replicas)}")
    replica = replicas[0]
    containers = replica["properties"]["containers"]
    container = next((item for item in containers if item["name"] == "gitlab"), None)
    if container is None:
        raise DemoError("GitLab container is not available for administration")
    endpoint = container["logStreamEndpoint"].split("/subscriptions/")[0]
    url = (
        endpoint.replace("https://", "wss://", 1)
        + f"/subscriptions/{subscription}/resourceGroups/{group}/containerApps/gitlab"
        + f"/revisions/{revision}/replicas/{replica['name']}/containers/gitlab/exec?command=bash"
    )
    token = json.loads(azure([
        "rest", "--method", "post", "--url",
        f"https://management.azure.com{app['id']}/getAuthToken?api-version=2025-07-01",
        "-o", "json",
    ], subscription))["properties"]["token"]
    marker = "__DEMO_" + uuid.uuid4().hex + "__"
    command = (
        "stty -echo; printf '\\n" + marker + "_START\\n'; "
        + "bash -c " + shlex.quote(script)
        + "; result=$?; printf '\\n" + marker + "_EXIT=%s\\n' \"$result\"; exit\n"
    )
    connection = websocket.create_connection(
        url, header=[f"Authorization: Bearer {token}"], timeout=15,
    )
    output = ""
    deadline = time.monotonic() + timeout
    try:
        connection.send_binary(b'\x00\x04{"Width":160,"Height":40}')
        connection.send_binary(b"\x00\x00" + command.encode())
        while time.monotonic() < deadline:
            try:
                frame = connection.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not frame:
                break
            if isinstance(frame, str):
                frame = frame.encode()
            if frame[0] == 2:
                raise DemoError("ACA exec proxy rejected the administration session")
            if len(frame) >= 2 and frame[0] == 0 and frame[1] in {1, 2}:
                output += frame[2:].decode("utf-8", errors="replace").replace("\r", "")
                matched = re.search(re.escape(marker) + r"_EXIT=(\d+)", output)
                if matched:
                    start = output.find(marker + "_START\n")
                    if start < 0:
                        raise DemoError("ACA exec output start marker is missing")
                    body = output[start + len(marker + "_START\n"):matched.start()].strip()
                    if int(matched.group(1)):
                        raise DemoError(f"GitLab administration failed (exit {matched.group(1)}): {body[-4000:]}")
                    return body
        raise DemoError("ACA administration did not return an explicit successful exit before timeout")
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--script", required=True, help="Presenter-authored administration script; never embed secrets")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        resources = read_json(args.private_dir / "resources.json")
        output = execute(resources, args.script, timeout=args.timeout)
        if args.output:
            args.output.write_text(output + "\n", encoding="utf-8")
            args.output.chmod(0o600)
            print(f"Private administration output saved: {args.output}")
        else:
            print(output)
        return 0
    except (DemoError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
