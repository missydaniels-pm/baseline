# Baseline database backups (exit-gate F2) — a separate Railway service, NOT
# part of the app image. See Baseline Files/BACKUPS.md for the runbook.
#
# Built from the official Postgres image so pg_dump/pg_restore match the
# server's major version. pg_dump refuses to dump a server NEWER than itself, so
# if Railway's Postgres is ever upgraded past 17, bump this tag first
# (production was 17.11 on 9/25/26).
FROM postgres:17

# gnupg  — encrypts the dump before it leaves the container (AES-256, passphrase)
# rclone — uploads to / downloads from Cloudflare R2 (S3-compatible)
RUN apt-get update \
 && apt-get install -y --no-install-recommends gnupg rclone ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /backups
COPY lib.sh backup.sh restore-drill.sh entrypoint.sh ./
RUN chmod +x *.sh

# The Postgres image's own entrypoint starts a database server — we don't want
# one. This container runs a single job and exits (Railway cron requires that).
ENTRYPOINT ["/backups/entrypoint.sh"]
