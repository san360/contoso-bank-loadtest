#!/bin/bash
set -euo pipefail
umask 077
gitlab-backup create STRATEGY=copy
gitlab-ctl backup-etc
find /var/opt/gitlab/backups /etc/gitlab/config_backup -maxdepth 1 -type f -exec chmod 600 {} \;
echo "Application and separate configuration backups completed on the persistent share."
