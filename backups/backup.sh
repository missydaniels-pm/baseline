#!/usr/bin/env bash
# Nightly backup: pg_dump -> check it -> encrypt -> upload to R2 -> check the upload.
#
# Every step is checked and ANY failure exits non-zero, so Railway marks the run
# failed. Nothing is uploaded unless the dump is complete and readable — a
# truncated dump that looks like a backup is worse than no backup.
#
# (If the size can't be parsed, the comparison below fails — closed, not open.)
#
# Uploads two encrypted objects under <environment>/ in the bucket:
#   baseline-<UTC stamp>.dump.gpg    the database (pg_dump custom format)
#   baseline-<UTC stamp>.counts.gpg  row count per table, for the restore drill
# The bucket's lifecycle rule deletes objects after 30 days; this script never
# deletes anything.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_env DATABASE_URL
setup_r2
setup_gpg

PREFIX="${RAILWAY_ENVIRONMENT_NAME:-unknown-env}"
STAMP="$(date -u +%Y-%m-%dT%H%M%SZ)"
NAME="baseline-${STAMP}"
WORK="$(mktemp -d)"
cleanup() { rm -rf "$WORK" "${GNUPGHOME:-}"; }
trap cleanup EXIT

log "Backup starting: ${PREFIX}/${NAME}"

# Counts before and after the dump. A table whose count is the same both times
# was not written to while the dump ran, so its count is exact for this dump.
# If it moved, the drill checks the restored count falls between the two.
table_counts "$DATABASE_URL" > "$WORK/before"
pg_dump "$DATABASE_URL" --format=custom --no-owner --no-acl --file="$WORK/db.dump"
table_counts "$DATABASE_URL" > "$WORK/after"
# Outer join: a table that appeared or vanished mid-dump is recorded as MISSING
# on one side, which makes the drill fail loudly rather than silently skip it.
join -a1 -a2 -e MISSING -o 0,1.2,2.2 "$WORK/before" "$WORK/after" > "$WORK/counts"   # "table before after"
[ -s "$WORK/counts" ] || die "row-count manifest is empty"

# pg_restore can read the archive's table of contents only if the dump is whole.
pg_restore --list "$WORK/db.dump" > /dev/null || die "dump failed its integrity check"
log "Dump OK: $(du -h "$WORK/db.dump" | cut -f1), $(wc -l < "$WORK/counts") tables"

encrypt_file "$WORK/db.dump" "$WORK/${NAME}.dump.gpg"
encrypt_file "$WORK/counts"  "$WORK/${NAME}.counts.gpg"
rm -f "$WORK/db.dump"   # don't keep the plaintext around longer than needed

for f in "${NAME}.dump.gpg" "${NAME}.counts.gpg"; do
  rclone copyto "$WORK/$f" "r2:${R2_BUCKET}/${PREFIX}/$f" --retries 3
  local_size="$(wc -c < "$WORK/$f" | tr -d ' ')"
  remote_size="$(rclone size --json "r2:${R2_BUCKET}/${PREFIX}/$f" | sed -E 's/.*"bytes": *([0-9]+).*/\1/')"
  [ "$local_size" = "$remote_size" ] || die "upload check failed for $f (local ${local_size} bytes, R2 ${remote_size:-none})"
done

log "Backup complete: ${PREFIX}/${NAME}.dump.gpg ($(wc -c < "$WORK/${NAME}.dump.gpg" | tr -d ' ') bytes, verified in R2)"
