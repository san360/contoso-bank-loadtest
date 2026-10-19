#!/bin/bash
set -euo pipefail
: "${SOURCE_NFS:?}"
: "${BACKUP_ID:?}"
: "${CONFIG_ARCHIVE:?}"
: "${EXPECTED_APP_SHA256:?}"
: "${EXPECTED_SECRETS_SHA256:?}"
: "${EXPECTED_MARKER_SHA256:?}"
: "${GITLAB_EXTERNAL_URL:?}"

test -f /opt/gitlab-demo/source-zero-replicas-verified
cloud-init status --wait >/var/log/gitlab-cloud-init-check.log 2>&1
test "$(findmnt -n -o FSTYPE /srv/gitlab)" = ext4
test ! -f /srv/gitlab/migration/restore-started
install -d -m 0700 /srv/gitlab/migration
if ! mountpoint -q /mnt/gitlab-source; then
  install -d -m 0755 /mnt/gitlab-source
  timeout 90s mount -t nfs4 -o ro,vers=4.1,soft,timeo=50,retrans=2 "$SOURCE_NFS" /mnt/gitlab-source
fi
findmnt -n -o OPTIONS /mnt/gitlab-source | grep -Eq '(^|,)ro(,|$)'
archive="$BACKUP_ID"_gitlab_backup.tar
install -d -m 0700 /srv/gitlab/data/backups
cp "/mnt/gitlab-source/data/backups/$archive" "/srv/gitlab/data/backups/$archive"
printf '%s  %s\n' "$EXPECTED_APP_SHA256" "/srv/gitlab/data/backups/$archive" | sha256sum --check --status
tar -xOf "/mnt/gitlab-source/config/config_backup/$CONFIG_ARCHIVE" /etc/gitlab/gitlab-secrets.json > /srv/gitlab/config/gitlab-secrets.json
chmod 0600 /srv/gitlab/config/gitlab-secrets.json
printf '%s  %s\n' "$EXPECTED_SECRETS_SHA256" /srv/gitlab/config/gitlab-secrets.json | sha256sum --check --status
cp /mnt/gitlab-source/data/demo-persistence-marker /srv/gitlab/data/demo-persistence-marker
printf '%s  %s\n' "$EXPECTED_MARKER_SHA256" /srv/gitlab/data/demo-persistence-marker | sha256sum --check --status
printf 'GITLAB_EXTERNAL_URL=%s\n' "$GITLAB_EXTERNAL_URL" > /opt/gitlab-demo/.env
chmod 0600 /opt/gitlab-demo/.env
cd /opt/gitlab-demo
docker compose -f compose.yaml config --quiet
docker compose -f compose.yaml pull > /var/log/gitlab-image-pull.log 2>&1
image=$(docker compose -f compose.yaml config --images)
entrypoint=$(docker image inspect --format '{{json .Config.Entrypoint}}' "$image")
command=$(docker image inspect --format '{{json .Config.Cmd}}' "$image")
test "$entrypoint" = null
test "$command" = '["/assets/init-container"]'
docker run --rm --entrypoint /bin/bash "$image" -ec 'test -x /assets/init-container; bash -n /assets/init-container; test -x /opt/gitlab/bin/gitlab-backup'
docker compose -f compose.yaml up -d
initial_restarts=$(docker inspect --format '{{.RestartCount}}' gitlab)
ready=false
for attempt in $(seq 1 120); do
  if [ "$(docker inspect --format '{{.RestartCount}}' gitlab)" -gt "$initial_restarts" ]; then
    echo "Stock GitLab restarted before readiness; inspect vendor startup log before retry."
    exit 1
  fi
  if docker exec gitlab curl --silent --fail --max-time 5 'http://127.0.0.1/-/readiness?all=1' >/dev/null; then
    ready=true
    break
  fi
  sleep 10
done
test "$ready" = true
docker exec gitlab chown git:git "/var/opt/gitlab/backups/$archive"
trap 'docker exec gitlab gitlab-ctl start puma; docker exec gitlab gitlab-ctl start sidekiq' EXIT
docker exec gitlab gitlab-ctl stop puma
docker exec gitlab gitlab-ctl stop sidekiq
date -u +%FT%TZ > /srv/gitlab/migration/restore-started
docker exec -e GITLAB_ASSUME_YES=1 gitlab gitlab-backup restore "BACKUP=$BACKUP_ID" > /srv/gitlab/migration/restore.log 2>&1
docker exec gitlab gitlab-rake gitlab:doctor:secrets > /srv/gitlab/migration/secrets-doctor.log 2>&1
docker exec gitlab gitlab-rails runner 'abort "Public signups unexpectedly enabled" if ApplicationSetting.current.signup_enabled'
docker exec gitlab gitlab-ctl start puma
docker exec gitlab gitlab-ctl start sidekiq
trap - EXIT
ready=false
for attempt in $(seq 1 60); do
  if docker exec gitlab curl --silent --fail --max-time 5 'http://127.0.0.1/-/readiness?all=1' >/dev/null; then
    ready=true
    break
  fi
  sleep 10
done
test "$ready" = true
printf '%s  %s\n' "$EXPECTED_SECRETS_SHA256" /srv/gitlab/config/gitlab-secrets.json | sha256sum --check --status
printf '%s  %s\n' "$EXPECTED_MARKER_SHA256" /srv/gitlab/data/demo-persistence-marker | sha256sum --check --status
date -u +%FT%TZ > /srv/gitlab/migration/restore-completed
echo "DEMO_VM_RESTORE=completed_on_managed_ext4_disk"
