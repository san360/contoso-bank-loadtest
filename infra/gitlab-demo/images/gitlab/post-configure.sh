#!/bin/bash
set -euo pipefail
umask 077

ready=0
for attempt in $(seq 1 120); do
    if code=$(curl --silent --show-error --max-time 10 --output /dev/null \
        --write-out '%{http_code}' 'http://127.0.0.1:80/-/readiness?all=1'); then
        if [ "$code" = "200" ]; then
            ready=1
            break
        fi
    fi
    sleep 5
done
if [ "$ready" -ne 1 ]; then
    echo "GitLab's nginx front door did not become ready after reconfiguration." >&2
    exit 1
fi

marker=/etc/gitlab/demo-bootstrap-complete
if [ -f "$marker" ]; then
    gitlab-rails runner /opt/demo/bootstrap.rb ensure-local-auth
else
    gitlab-rails runner /opt/demo/bootstrap.rb create-token
    touch "$marker"
    chmod 600 "$marker"
fi
unset GITLAB_BOOTSTRAP_TOKEN GITLAB_ROOT_PASSWORD
/opt/gitlab/embedded/bin/ruby /opt/demo/readiness.rb &
