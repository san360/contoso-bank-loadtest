"""Write root-only runner settings, preserving ACA identity across the login shell."""

import json
import os
from collections.abc import Mapping
from pathlib import Path

FORWARDED_ENVIRONMENT = (
    "PATH", "AZURE_EXTENSION_DIR",
    "IDENTITY_ENDPOINT", "IDENTITY_HEADER", "MSI_ENDPOINT", "MSI_SECRET", "IMDS_ENDPOINT",
)


def configure(path: Path, environment: Mapping[str, str]) -> None:
    forwarded = [
        f"{name}={environment[name]}" for name in FORWARDED_ENVIRONMENT
        if environment.get(name)
    ]
    config = (
        'concurrent = 2\ncheck_interval = 3\nshutdown_timeout = 90\n'
        '[[runners]]\nname = "aca-demo-shell"\n'
        f'url = {json.dumps(environment["GITLAB_URL"])}\n'
        f'token = {json.dumps(environment["GITLAB_RUNNER_TOKEN"])}\n'
        'executor = "shell"\nshell = "bash"\nrequest_concurrency = 2\n'
        'builds_dir = "/home/gitlab-runner/builds"\n'
        'cache_dir = "/home/gitlab-runner/cache"\n'
        f'environment = {json.dumps(forwarded)}\n'
    )
    with path.open("w", encoding="utf-8") as stream:
        stream.write(config)
    path.chmod(0o600)


if __name__ == "__main__":
    configure(Path("/run/demo-runner/config.toml"), os.environ)
