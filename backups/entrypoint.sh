#!/usr/bin/env bash
# Picks the job from BACKUP_MODE. Production runs `backup` on a cron schedule;
# the staging copy of this service runs `restore-drill` when redeployed.
set -euo pipefail

case "${BACKUP_MODE:-backup}" in
  backup)        exec "$(dirname "$0")/backup.sh" ;;
  restore-drill) exec "$(dirname "$0")/restore-drill.sh" ;;
  *) echo "Unknown BACKUP_MODE '${BACKUP_MODE}' (expected backup or restore-drill)" >&2; exit 2 ;;
esac
