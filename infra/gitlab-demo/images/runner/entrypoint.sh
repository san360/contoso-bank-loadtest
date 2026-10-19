#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /run/demo-runner /home/gitlab-runner/builds /home/gitlab-runner/cache
chown -R gitlab-runner:gitlab-runner /home/gitlab-runner
python - <<'PY'
import json
import os
from pathlib import Path

config = (
    'concurrent = 2\ncheck_interval = 3\nshutdown_timeout = 90\n'
    '[[runners]]\nname = "aca-demo-shell"\n'
    f'url = {json.dumps(os.environ["GITLAB_URL"])}\n'
    f'token = {json.dumps(os.environ["GITLAB_RUNNER_TOKEN"])}\n'
    'executor = "shell"\nshell = "bash"\nrequest_concurrency = 2\n'
    'builds_dir = "/home/gitlab-runner/builds"\n'
    'cache_dir = "/home/gitlab-runner/cache"\n'
)
path = Path("/run/demo-runner/config.toml")
path.write_text(config)
path.chmod(0o600)
PY
unset GITLAB_RUNNER_TOKEN
exec gitlab-runner run --config /run/demo-runner/config.toml \
    --working-directory /home/gitlab-runner --user gitlab-runner
