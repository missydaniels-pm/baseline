#!/usr/bin/env bash
# Restore drill: prove a backup can actually be restored.
#
# Downloads the newest backup (or DRILL_OBJECT), decrypts it, restores it into a
# brand-new throwaway database on the DRILL_DATABASE_URL server, compares every
# table's row count with the counts recorded at backup time, then DROPS the
# throwaway database — pass or fail — so real data never stays on staging.
# Only counts are logged, never row content.
#
# Variables:
#   DRILL_DATABASE_URL   server to restore onto (staging's Postgres)
#   DRILL_SOURCE_PREFIX  which environment's backups to test (default: production)
#   DRILL_OBJECT         optional: a specific .dump.gpg name instead of the newest
set -euo pipefail
source "$(dirname "$0")/lib.sh"

require_env DRILL_DATABASE_URL
setup_r2
setup_gpg

SOURCE="${DRILL_SOURCE_PREFIX:-production}"
WORK="$(mktemp -d)"
DRILL_DB="restore_drill_$(date -u +%Y%m%d%H%M%S)"

# Point a URL at a different database: swap the path, keep any ?query.
base="${DRILL_DATABASE_URL%%\?*}"
query=""; [ "$base" != "$DRILL_DATABASE_URL" ] && query="?${DRILL_DATABASE_URL#*\?}"
server="${base%/*}"
# Simple string split, so refuse anything it could misread (e.g. a "/" in the
# password) rather than point the drill at the wrong database. Railway's
# generated URLs always pass.
[[ "$server" =~ ^postgres(ql)?://[^/]+$ ]] || die "DRILL_DATABASE_URL isn't in the expected postgresql://user:pass@host:port/db form"
DRILL_URL="${server}/${DRILL_DB}${query}"
ADMIN_URL="${server}/postgres${query}"   # to create/drop the drill database from outside it

created=0
cleanup() {
  if [ "$created" = 1 ]; then
    psql "$ADMIN_URL" -X -q -v ON_ERROR_STOP=1 \
      -c "DROP DATABASE IF EXISTS \"${DRILL_DB}\" WITH (FORCE)" \
      && log "Dropped throwaway database ${DRILL_DB}" \
      || log "WARNING: could not drop ${DRILL_DB} — drop it by hand (see BACKUPS.md)"
  fi
  rm -rf "$WORK" "${GNUPGHOME:-}"
}
trap cleanup EXIT

# Names sort by their UTC timestamp, so the last one is the newest.
OBJECT="${DRILL_OBJECT:-$(rclone lsf --files-only "r2:${R2_BUCKET}/${SOURCE}/" | grep '\.dump\.gpg$' | sort | tail -n 1)}"
[ -n "$OBJECT" ] || die "no backups found under ${SOURCE}/ in ${R2_BUCKET}"
NAME="${OBJECT%.dump.gpg}"
log "Drill starting: ${SOURCE}/${OBJECT} -> ${DRILL_DB}"

rclone copyto "r2:${R2_BUCKET}/${SOURCE}/${NAME}.dump.gpg"   "$WORK/db.dump.gpg"
rclone copyto "r2:${R2_BUCKET}/${SOURCE}/${NAME}.counts.gpg" "$WORK/counts.gpg"
decrypt_file "$WORK/db.dump.gpg" "$WORK/db.dump" || die "could not decrypt — wrong BACKUP_PASSPHRASE, or the file is damaged"
decrypt_file "$WORK/counts.gpg"  "$WORK/counts"

psql "$ADMIN_URL" -X -q -v ON_ERROR_STOP=1 -c "CREATE DATABASE \"${DRILL_DB}\""
created=1
pg_restore --no-owner --no-acl --exit-on-error --dbname="$DRILL_URL" "$WORK/db.dump"
log "Restore finished; comparing row counts"

table_counts "$DRILL_URL" > "$WORK/restored"

# counts: "table before after"; restored: "table n". A table missing from
# either side shows up as a failure rather than being skipped.
fail=0
while read -r table before after restored; do
  if [ -z "$restored" ] || [ "$restored" = "MISSING" ] || [ "$before" = "MISSING" ]; then
    log "  FAIL  ${table}: backup=${before}/${after} restored=${restored:-MISSING}"; fail=1
  elif [ "$before" = "$after" ] && [ "$restored" = "$before" ]; then
    log "  ok    ${table}: ${restored}"
  elif [ "$before" != "$after" ] && [ "$restored" -ge "$(( before < after ? before : after ))" ] \
       && [ "$restored" -le "$(( before > after ? before : after ))" ]; then
    log "  ok    ${table}: ${restored} (changed during backup: ${before}->${after})"
  else
    log "  FAIL  ${table}: expected ${before}, restored ${restored}"; fail=1
  fi
done < <(join -a1 -a2 -e MISSING -o 0,1.2,1.3,2.2 "$WORK/counts" "$WORK/restored")

[ "$fail" = 0 ] || die "restore drill found mismatched tables (above)"
log "RESTORE DRILL PASSED: ${SOURCE}/${OBJECT} restored and every table's row count matches"
