#!/bin/bash
set -euo pipefail
umask 077

cp /opt/demo/gitlab.rb /etc/gitlab/gitlab.rb
export GITLAB_POST_RECONFIGURE_SCRIPT=/opt/demo/post-configure.sh
# Keep the pinned image's real boot command as PID 1 for its normal signal handling.
exec /assets/init-container
