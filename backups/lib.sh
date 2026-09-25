#!/usr/bin/env bash
# Shared helpers for backup.sh and restore-drill.sh. Sourced, not executed.
#
# Never echo a secret or a connection URL from here — Railway keeps these logs.

# sort and join must agree on ordering, whatever locale the image sets.
export LC_ALL=C

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
die() { log "FAILED: $*" >&2; exit 1; }

# Any other failing command (set -e) still ends with a clear FAILED line in the log.
# set -E makes the trap fire inside functions too (table_counts, encrypt_file...).
set -E
trap 'log "FAILED: $(basename "$0") stopped on an error — see the message above" >&2' ERR

require_env() {
  local missing=()
  for name in "$@"; do
    [ -n "${!name:-}" ] || missing+=("$name")
  done
  [ ${#missing[@]} -eq 0 ] || die "missing Railway variable(s): ${missing[*]}"
}

# rclone reads its remote definition from RCLONE_CONFIG_<REMOTE>_* variables,
# so there is no config file on disk. no_check_bucket: the R2 token is scoped
# to Object Read & Write on one bucket, so it may not create or inspect buckets.
setup_r2() {
  require_env R2_ENDPOINT R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET
  export RCLONE_CONFIG_R2_TYPE=s3
  export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
  export RCLONE_CONFIG_R2_ENDPOINT="$R2_ENDPOINT"
  export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
  export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
  export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true
  export RCLONE_LOG_LEVEL=ERROR   # hide the harmless "no config file" notice
}

# A throwaway gpg home so nothing depends on (or is left in) a keyring.
setup_gpg() {
  require_env BACKUP_PASSPHRASE
  GNUPGHOME="$(mktemp -d)"
  export GNUPGHOME
  chmod 700 "$GNUPGHOME"
}

# The passphrase goes in on stdin, never on the command line (where it would
# show up in the process list).
encrypt_file() {  # <in> <out>
  printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
    --pinentry-mode loopback --passphrase-fd 0 \
    --symmetric --cipher-algo AES256 --output "$2" "$1"
}

decrypt_file() {  # <in> <out>
  printf '%s' "$BACKUP_PASSPHRASE" | gpg --batch --yes --quiet \
    --pinentry-mode loopback --passphrase-fd 0 \
    --decrypt --output "$2" "$1"
}

# Exact row count of every table in the public schema, one "table count" per
# line, sorted. Built from the catalog so a new table is covered automatically.
# Numbers only — no row content is ever read.
table_counts() {  # <database url>
  psql "$1" -X -q -At -F ' ' -v ON_ERROR_STOP=1 <<'SQL' | sort
SELECT format('SELECT %L, count(*) FROM %I.%I', table_name, table_schema, table_name)
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
\gexec
SQL
}
