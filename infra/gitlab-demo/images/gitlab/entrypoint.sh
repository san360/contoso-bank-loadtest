#!/bin/bash
set -euo pipefail
umask 077

cp /opt/demo/gitlab.rb /etc/gitlab/gitlab.rb

bootstrap_local_auth() {
    ready=0
    for attempt in $(seq 1 150); do
        if curl --fail --silent http://127.0.0.1:8080/-/readiness >/dev/null; then
            ready=1
            break
        fi
        sleep 10
    done
    if [ "$ready" -ne 1 ]; then
        echo "GitLab did not become locally ready within 25 minutes." >&2
        return 1
    fi

    gitlab-rails runner /opt/demo/bootstrap.rb
    unset GITLAB_BOOTSTRAP_TOKEN GITLAB_ROOT_PASSWORD

    # ACA routes no public traffic until the bootstrap has disabled signups.
    exec /opt/gitlab/embedded/bin/ruby -rsocket -e '
      server = TCPServer.new("0.0.0.0", 9090)
      loop do
        client = server.accept
        client.gets
        client.write "HTTP/1.1 200 OK\r\nContent-Length: 6\r\nConnection: close\r\n\r\nready\n"
        client.close
      end
    '
}

bootstrap_local_auth &
# Keep the pinned image's real boot command as PID 1 for its normal signal handling.
exec /assets/init-container
