# Baseline — Database Backups Runbook

Last updated: September 25, 2026 · Decision record: BACKLOG Decision Log "Backups — plan" (exit-gate F2)

This page explains how production data is backed up, how to tell that backups are working, and how
to get data back. If you are here because something went wrong, jump to **Emergency restore**.

---

## What happens every night

A small Railway service called **`backups`** (code in `backups/` at the repo root) runs once a night
in the **production** environment:

1. **Dump:** `pg_dump` copies the whole production database into one file.
2. **Check:** the dump is only kept if `pg_restore --list` can read it back in full. A cut-off dump
   stops the run and nothing is uploaded.
3. **Encrypt:** the dump is locked with **gpg (AES-256)** using the **backup passphrase**.
4. **Upload:** the encrypted file goes to the Cloudflare R2 bucket **`baseline-backups`**, under
   `production/`. The script then compares the file size in R2 with the local file.
5. **Row counts:** a second small encrypted file (`.counts.gpg`) records each table's row count at
   backup time. The restore drill uses it to check the restore.

| Setting | Value | Where it lives |
|---|---|---|
| Schedule | `0 10 * * *` UTC = 3am Pacific in summer (PDT), 2am in winter (PST) | `backups/railway.json` |
| Environment | production only; staging runs the **restore drill** instead | `backups/railway.json` (`environments.staging` nulls the cron) |
| Retention | 30 days, via the bucket's **Object Lifecycle Rule** `expire-after-30-days` | Cloudflare dashboard → R2 → `baseline-backups` → Settings |
| Encryption | gpg symmetric, AES-256, passphrase in `BACKUP_PASSPHRASE` | Railway variable **and** the owner's Google Password Manager (entry `baseline-backups.local`) |
| File names | `production/baseline-<UTC time>.dump.gpg` and `.counts.gpg` | R2 |

**Who can read the backups:** anyone with the passphrase *and* R2 access, and the staging `backups`
service holds both, because the drill has to decrypt production backups. So access to the **staging**
environment's variables is as sensitive as access to production data. Keep the Railway project
single-owner, and never paste staging variables anywhere.

The script **never deletes anything**. Old copies expire through the lifecycle rule. The R2 API token
(`baseline-backup-writer`) is scoped to **Object Read & Write on this one bucket**, so it cannot
touch any other bucket or change account settings.

**The passphrase is the one thing that must not be lost.** Without it every backup is unreadable,
including to us. It lives in two places on purpose: Railway, so the job can run, and the password
manager, so the backups can still be opened if Railway is gone.

---

## Railway variables

| Variable | Production `backups` | Staging `backups` | Notes |
|---|---|---|---|
| `BACKUP_MODE` | `backup` | `restore-drill` | Chooses which job runs |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | (only for a staging test backup) | Reference variable, private network |
| `DRILL_DATABASE_URL` | — | `${{Postgres.DATABASE_URL}}` | Staging's Postgres; the drill restores into a *new throwaway database* on that server |
| `DRILL_SOURCE_PREFIX` | — | `production` | Which environment's backups to test |
| `DRILL_OBJECT` | — | optional | A specific `….dump.gpg` name instead of the newest |
| `R2_ENDPOINT` | ✔ | ✔ | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | ✔ | ✔ | From the R2 API token |
| `R2_SECRET_ACCESS_KEY` | ✔ | ✔ | From the R2 API token (shown once, kept in the password manager) |
| `R2_BUCKET` | `baseline-backups` | `baseline-backups` | |
| `BACKUP_PASSPHRASE` | ✔ | ✔ | Same value in both. The drill must be able to decrypt production's files |

Service settings (dashboard, both environments): **Root Directory** `/backups` and **Railway Config
File** `/backups/railway.json`. Railway's config-file path does not follow the Root Directory, so it
must be the full path. The config file sets the Dockerfile builder, the watch path `/backups/**` (app
pushes don't rebuild this service), the cron schedule and restart policy `NEVER` (a failed run stays
visibly failed instead of looping).

---

## Is it working? (weekly, ~1 minute)

Railway sends no alert if the job **never runs** (schedule removed, service paused), so check weekly
until real alerting exists (exit-gate F3):

1. Cloudflare dashboard → **R2 object storage** → **`baseline-backups`** → open the `production/`
   folder.
2. The newest `…dump.gpg` should be dated **today or yesterday**. There should be about 30 of them.
3. Optional: Railway → production → `backups` → **Deployments / Cron runs**. The last run's log should
   end with `Backup complete: … verified in R2`.

A failed run ends its log with a `FAILED: …` line that says why. Whether Railway **emails** you about
a failed cron run is checked by the forced-failure test in the setup checklist below. Record the
result here: **_not yet tested_**.

---

## Restore drill (proving a backup can be restored)

Run it after setup, after any change to `backups/`, after a Postgres major-version upgrade, and every
few months otherwise. **A backup is only trusted once a drill has passed.**

1. Railway → **staging** → `backups` service → **Deployments** → latest → **⋮ → Redeploy**. The
   staging copy has no cron schedule, so each deploy runs the drill once and exits.
2. Read its log. It:
   - downloads the newest `production/` backup and decrypts it,
   - creates a throwaway database `restore_drill_<time>` on **staging's** Postgres server,
   - restores into it with `pg_restore --exit-on-error`,
   - compares every table's row count with the counts recorded at backup time,
   - **drops the throwaway database, pass or fail**, so real health data does not stay on staging.
3. Success ends with `RESTORE DRILL PASSED`. Any `FAIL` line names the table and the two counts.

Only counts are logged, never row content. If a table was being written while the backup ran, its
count is checked against the range it moved through (logged as `changed during backup`).

If a drill log ever shows `could not drop restore_drill_…`, remove it by hand from staging's Postgres
(Railway → staging → Postgres → Data, or `DROP DATABASE "restore_drill_…" WITH (FORCE);`).

**Drill history:**

| Date | Backup tested | Result | Run by |
|---|---|---|---|
| 2026-09-25 | local test (seeded schema, fake bucket) | passed, plus failure cases | Claude |

---

## Emergency restore (production data lost or damaged)

Stop and think before writing into production: restoring **replaces** what is there. Where possible,
restore into a **new** Postgres service first, check it, then point the app at it.

**A. Railway still works:**
1. Railway → production → **+ New → Database → PostgreSQL**. This creates a fresh, empty database.
2. Get the backup onto a machine with `pg_restore` 17+ and `gpg`. On a Mac:
   `brew install postgresql@17 gnupg`.
3. Download the backup: Cloudflare → R2 → `baseline-backups` → `production/` → pick the file →
   **Download**.
4. Decrypt it. gpg asks for the passphrase from the password manager:
   ```bash
   gpg --output baseline.dump --decrypt baseline-<time>.dump.gpg
   ```
5. Restore into the **new** database. Use its `DATABASE_PUBLIC_URL`, from its Variables tab:
   ```bash
   pg_restore --no-owner --no-acl --exit-on-error --dbname "<new DATABASE_PUBLIC_URL>" baseline.dump
   ```
6. Point the app at it: production `baseline` service → Variables → `DATABASE_URL` =
   `${{<new service name>.DATABASE_URL}}` → deploy → run the standard production checks (CLAUDE.md
   "Deployment Workflow").
7. **Delete the decrypted `baseline.dump`** from the laptop. It is every user's health data in the
   clear.

**B. Railway account lost:** steps 2–5 work against any PostgreSQL 17+ server (a new Railway account,
or any managed Postgres). The backups and the passphrase are both outside Railway for exactly this
reason.

---

## Things that will break this (read before changing)

- **Postgres upgraded past 17:** `pg_dump` refuses to dump a server newer than itself. Bump
  `FROM postgres:17` in `backups/Dockerfile` **first**. Production was 17.11 on 9/25/26.
- **Changing the passphrase:** old backups still need the **old** passphrase. Keep it in the password
  manager, labelled with the date it was retired, for 30 days (until those files expire). The drill
  uses the current one, so run a drill after the next nightly backup.
- **Rotating the R2 token:** update both `backups` services' `R2_ACCESS_KEY_ID` /
  `R2_SECRET_ACCESS_KEY`, then redeploy staging to confirm with a drill.
- **Deleting the lifecycle rule:** backups would pile up without limit. The free tier is 10 GB. The
  database was 9 MB on 9/25/26, so this would take years, but it is not intended.
- **Rule 1:** this is a separate service, not part of the app. The temporary files it writes live in
  its own container for one run and are deleted on exit. Nothing is held between runs.

## Setup checklist (one-time, 9/25/26)

- [x] R2 bucket `baseline-backups`, lifecycle rule 30 days, token `baseline-backup-writer` (owner)
- [x] Scripts built and tested locally, including failure cases (Claude)
- [ ] Passphrase created and saved in the password manager (owner)
- [ ] Staging `backups` service: test backup of staging's seeded data → drill against it
- [ ] Merge to `main`; production `backups` service created with the variables above
- [ ] First production backup run by hand → file visible in R2
- [ ] **Drill against the production backup passes** ← the deliverable
- [ ] Both `backups` services show Root Directory `/backups` and Config File `/backups/railway.json`, and production's settings show the cron `0 10 * * *` (proves the config file is actually being read — like the app's Custom Start Command, these settings don't appear in build logs)
- [ ] Staging `backups` service shows **no cron schedule** in its settings (proves the `null` override in `railway.json` worked)
- [ ] Watch paths: an app-only push does **not** rebuild `backups`, and a `backups/`-only push does (patterns in `railway.json` are written from the repo root)
- [ ] Forced-failure test: does Railway email the owner? Record the answer above
