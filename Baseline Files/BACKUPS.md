# Baseline — Database Backups Runbook

Last updated: September 25, 2026 · Decision record: BACKLOG Decision Log "Backups — plan" (exit-gate F2)

This page explains how production data is backed up, how to tell that backups are working, and how
to get data back. If you are here because something went wrong, jump to **Emergency restore**.

---

## What happens every night

Two Railway services are built from the same code (`backups/` at the repo root). Railway service
names are unique across the whole project, so each is named after its job:

- **`backups`** (production) runs once a night and makes the backup.
- **`backup-drill`** (staging) restores the newest production backup whenever it's redeployed, to
  prove it works. See **Restore drill**.

Each night, `backups`:

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
| Schedule | `0 10 * * *` UTC = 3am Pacific in summer (PDT), 2am in winter (PST) | Railway dashboard → production `backups` → Settings → **Cron Schedule** |
| Environment | production only; staging runs the **restore drill** instead | Staging's `backup-drill` has **no** Cron Schedule, so it runs only when redeployed |
| Retention | 30 days, via the bucket's **Object Lifecycle Rule** `expire-after-30-days` | Cloudflare dashboard → R2 → `baseline-backups` → Settings |
| Encryption | gpg symmetric, AES-256, passphrase in `BACKUP_PASSPHRASE` | Railway variable **and** the owner's Google Password Manager (entry `baseline-backups.local`) |
| File names | `production/baseline-<UTC time>.dump.gpg` and `.counts.gpg` | R2 |

**Who can read the backups:** anyone with the passphrase *and* R2 access, and the staging `backup-drill`
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

| Variable | Production `backups` | Staging `backup-drill` | Notes |
|---|---|---|---|
| `BACKUP_MODE` | `backup` | `restore-drill` | Chooses which job runs |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | — (deliberately absent, so it can't back up by mistake) | Reference variable, private network |
| `DRILL_DATABASE_URL` | — | `${{Postgres.DATABASE_URL}}` | Staging's Postgres; the drill restores into a *new throwaway database* on that server |
| `DRILL_SOURCE_PREFIX` | — | `production` | Which environment's backups to test |
| `DRILL_OBJECT` | — | optional | A specific `….dump.gpg` name instead of the newest |
| `R2_ENDPOINT` | ✔ | ✔ | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | ✔ | ✔ | From the R2 API token |
| `R2_SECRET_ACCESS_KEY` | ✔ | ✔ | From the R2 API token (shown once, kept in the password manager) |
| `R2_BUCKET` | `baseline-backups` | `baseline-backups` | |
| `BACKUP_PASSPHRASE` | ✔ | ✔ | Same value in both. The drill must be able to decrypt production's files |

**Two Railway dashboard gotchas (both hit on 9/25/26):**
- **Railway autofills variables from the code, and gets references wrong.** It filled
  `DRILL_DATABASE_URL` as `${{Postgres.DRILL_DATABASE_URL}}`, which doesn't exist, so the value came out
  **empty**. Every `${{…}}` value here must be exactly `${{Postgres.DATABASE_URL}}`. A correct
  reference shows a link icon. If a run fails with `missing Railway variable(s): X`, this is the
  first thing to check.
- **Service names are project-wide.** A staging service called `backups` blocks the name in
  production, hence `backup-drill`.
- **Renaming a service** isn't in its Settings tab. Right-click the service card → **Config** →
  **Name**.

### Service settings live in the Railway dashboard, not the repo

Railway retired `railway.json` ("Config as Code"). Services that never used it can't opt in after
2026-08-28, and it stops being read everywhere on 2026-12-01. So these settings exist **only in the
dashboard**. They don't appear in build logs and nothing in git records them. That is the same kind
of out-of-repo setting as the app's Custom Start Command, so **this table is the record**, and the
checks below confirm it. (Railway's replacement, "Infrastructure as Code", is a project-wide
TypeScript file; evaluating it is a BACKLOG item.)

| Setting (Settings tab) | Production `backups` | Staging `backup-drill` | Why |
|---|---|---|---|
| Source → **Root Directory** | `/backups` | `/backups` | Builds `backups/Dockerfile`, not the app |
| Source → **Branch** | `main` | `staging` | Same branch gate as the app |
| Build → **Watch Paths** | `/backups/**` | `/backups/**` | App pushes don't rebuild this service |
| Deploy → **Cron Schedule** | `0 10 * * *` | *(none)* | Nightly in production; staging runs only on redeploy |
| Deploy → **Restart Policy** | **Never** | **Never** | Railway's default "On Failure × 10" re-ran a failed drill 6 times in 3 seconds (9/25) — a failure must stay visibly failed |
| Networking → public domain | none | none | It serves nothing |

**Check after any Railway change** (and at each drill): open both services' Settings and compare
them with this table.

---

## Is it working? (weekly, ~1 minute)

Railway sends no alert if the job **never runs** (schedule removed, service paused), so check weekly
until real alerting exists (exit-gate F3):

1. Cloudflare dashboard → **R2 object storage** → **`baseline-backups`** → open the `production/`
   folder.
2. The newest `…dump.gpg` should be dated **today or yesterday**. There should be about 30 of them.
3. Optional: Railway → production → `backups` → **Deployments / Cron runs**. The last run's log should
   end with `Backup complete: … verified in R2`.

A failed run ends its log with a `FAILED: …` line that says why. **Railway emails the owner when a run fails.** Tested 9/25/26 by forcing a staging drill to fail (`DRILL_SOURCE_PREFIX=nothing-here`): one failed run (Restart Policy Never held), and the email arrived. That is the failure alert. The weekly check above covers the case it can't: a job that never runs.

---

## Restore drill (proving a backup can be restored)

Run it after setup, after any change to `backups/`, after a Postgres major-version upgrade, and every
few months otherwise. **A backup is only trusted once a drill has passed.**

1. Railway → **staging** → `backup-drill` → **Deployments** → latest → **⋮ → Redeploy**. It has no
   cron schedule, so each deploy runs the drill once and exits.
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
| 2026-09-25 | `staging/baseline-2026-09-25T205712Z` (staging's seeded data, real R2) | **passed**, 15 tables | Claude + owner |
| 2026-09-25 | **`production/baseline-2026-09-25T212913Z`** — first production backup (real user data: 22 users, 127 episodes, 402 symptom scores, 178 check-ins) | **PASSED**, all 15 tables match; drill database dropped, none left over | Claude + owner |

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
- **Rotating the R2 token:** update both services' (`backups`, `backup-drill`) `R2_ACCESS_KEY_ID` /
  `R2_SECRET_ACCESS_KEY`, then redeploy staging to confirm with a drill.
- **Deleting the lifecycle rule:** backups would pile up without limit. The free tier is 10 GB. The
  database was 9 MB on 9/25/26, so this would take years, but it is not intended.
- **Rule 1:** this is a separate service, not part of the app. The temporary files it writes live in
  its own container for one run and are deleted on exit. Nothing is held between runs.

## Setup checklist (one-time, 9/25/26)

- [x] R2 bucket `baseline-backups`, lifecycle rule 30 days, token `baseline-backup-writer` (owner)
- [x] Scripts built and tested locally, including failure cases (Claude)
- [x] Passphrase created and saved in the password manager (owner)
- [x] Staging test backup in the real container + real R2 (9/25). The first run exposed Debian's rclone 1.60 getting 501s from R2; now pinned to 1.75.1, and the rerun was clean
- [x] A `backups/`-only push rebuilt the service (9/25)
- [x] Staging drill against staging's own backup: **passed**, 15 tables, temporary database dropped, none left over (9/25)
- [x] Merged to `main` (9/25); production `backups` service created with the variables above (app unchanged, standard production checks passed after the restart)
- [x] First production backup run by hand (9/25, 21:29 UTC): 15 tables, verified in R2
- [x] **Drill against the production backup PASSED** (9/25) ← the deliverable
- [ ] Production `backups` Cron Schedule set to `0 10 * * *`, then the first *scheduled* run confirmed the next morning
- [ ] Both services' Settings match the dashboard-settings table above (Restart Policy **Never**, Watch Paths `/backups/**`, production cron `0 10 * * *`, no cron on staging)
- [ ] Watch paths: an app-only push does **not** rebuild `backups`
- [x] Forced-failure test (9/25): one failed run with Restart Policy **Never**, and Railway **emailed the owner**
