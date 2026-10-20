#!/bin/bash
set -euo pipefail
umask 077
mkdir -p /run/demo-runner /home/gitlab-runner/builds /home/gitlab-runner/cache
chown -R gitlab-runner:gitlab-runner /home/gitlab-runner
python /opt/configure-runner.py
unset GITLAB_RUNNER_TOKEN
exec gitlab-runner run --config /run/demo-runner/config.toml \
    --working-directory /home/gitlab-runner --user gitlab-runner
