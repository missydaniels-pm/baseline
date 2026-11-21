# Baseline — Start Here (engineers)

Last updated: October 19, 2026 · One page. The deep references are linked at the bottom.

Baseline is a Flask monolith for people with chronic conditions to log episodes, track
treatments and run one-change-at-a-time experiments. It holds **real health data**. Read this
before you run anything against a real database.

## What runs where

| Thing | Where | From |
|---|---|---|
| Web app (production) | Railway, `mybaselineapp.com` (Cloudflare DNS) | `main` branch → repo `Dockerfile` (`gunicorn`, 1 worker × 4 threads) |
| Web app (staging) | Railway environment `staging` | `staging` branch, same Dockerfile, its own Postgres, email off |
| Database | Railway PostgreSQL (one per environment); SQLite locally | `DATABASE_URL` |
| Nightly backup | Railway service `backups` (production) → encrypted `pg_dump` to Cloudflare R2 | `backups/` — runbook `BACKUPS.md` |
| Restore drill | Railway service `backup-drill` (staging) | `backups/` |
| Email | Resend (verification, welcome, audience sync) | `RESEND_API_KEY` |
| AI check-in | Anthropic API, pinned model in `parse_checkin()` | `ANTHROPIC_API_KEY` |
| CI | GitHub Actions: every `test_*.py` on push/PR; weekly EOL check; uptime ping every 30 min | `.github/workflows/` |

Railway dashboard settings that are **not** in the repo: the service's Custom Start Command (must
be blank), the backup services' cron/restart/watch settings (recorded in `BACKUPS.md`).

## Run it locally (safely)

```bash
./run.sh            # builds .venv on Python 3.14 if needed, exports DEBUG=true, serves :5001
```
Needs a `.env` with `SECRET_KEY` (and `ANTHROPIC_API_KEY` for check-in). Local DB is SQLite in
`instance/`. `/dev/bootstrap` creates a first account (not an admin) and `/dev/seed` adds 12 weeks of
data — both only work with `DEBUG=true`.

Tests: `WTF_CSRF_ENABLED=false .venv/bin/python test_<name>.py` (each suite builds its own temp
DB). A pass on any Python other than 3.14 says nothing about production.

## Setting up the database is an explicit step (`init_db()` / `flask init-db`)

Until 11/21/26, `import app` ran `create_all`, the migrations, the stale-unverified-user purge, the
admin flag, the optional Resend backfill and the FK check against whatever `DATABASE_URL` was set.
**Importing the app no longer touches a database.** That work is `init_db()` in `app.py`, run by:

- **production/staging:** `flask --app app init-db` in the Dockerfile `CMD`, before gunicorn starts
  (a failed init-db stops the container rather than serving on an unmigrated schema);
- **local dev:** `python app.py` / `run.sh` (the `__main__` block calls it). Starting it any other
  way (`flask run`, `gunicorn` by hand) on a fresh checkout gives 500s with missing tables until
  you run `flask --app app init-db` once;
- **tests and scripts:** an explicit `init_db()` after `DATABASE_URL` is set — copy the line from any
  `test_*.py` when you add a suite.

Still: `init_db()` and `flask init-db` write to whatever `DATABASE_URL` is set, so never
`export DATABASE_URL=<production>` in a working shell. If a runbook points a script at production
(e.g. `check_fk_orphans.py`), set the variable for **that one command only**.

## How a change ships

1. Branch from `staging`; build; tests ship in the same commit (CLAUDE.md "Tests ship with the change").
2. Push `staging` **only**. Wait for the build. Verify on the staging URL with something only the new
   build can produce — never a green badge.
3. Then merge `staging → main`. Production deploys on push. Verify production the same way.
4. Standing checks on both, every deploy:
   ```bash
   curl -s -o /dev/null -w "%{http_code}\n" "$URL/?__debugger__=yes&cmd=resource&f=debugger.js"  # NOT 200
   curl -s -w " %{http_code}\n" "$URL/dev/bootstrap"                                         # 403
   ```

## Rules that bite

- **Stateless backend** (Rule 1), **slow work off the request** (Rule 2), **converge, don't diverge**
  (Rule 3) — CLAUDE.md. Patterns live in `CONVENTIONS.md`; read it before building.
- Every query is scoped to the session's user. Auth is the `require_auth` before-request hook
  (`PUBLIC_ENDPOINTS` lists the exceptions) — there is no `@login_required`.
- "Today" for a user is `user_today()`, never the server date.
- Foreign keys cascade in the database (`EXPECTED_FK_ONDELETE`) — never hand-order child deletes.
- Schema changes are additive, run by `flask init-db` (container start) from `run_migrations()`; redeploying an old image does
  not undo them.
- Public repo: security findings go to the private repo; BACKLOG carries only neutral "review Fn" rows.

## Where to read next

`CLAUDE.md` (rules, workflow) · `TECHNICAL_README.md` (routes, models, deploy detail) ·
`CONVENTIONS.md` (canonical patterns) · `STAGING_SETUP.md` · `BACKUPS.md` · `EOL.md` ·
`BACKLOG.md` (what's next, and the Decision Log for *why* things are the way they are).
