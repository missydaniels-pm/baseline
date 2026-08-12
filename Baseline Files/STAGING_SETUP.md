# Staging Environment — Setup Runbook

Last updated: July 17, 2026

Staging is a **separate deploy of Baseline that mirrors production** but runs from the `staging`
git branch against its **own throwaway PostgreSQL database**. It exists to de-risk the schema-heavy
migrations coming next (user-timezone, FK cleanup) — prove them on staging before they ever touch a
real user's data.

**Topology decided (7/17/26):** same Railway project, a new **environment** called `staging`
(prod + staging side by side, each with its own Postgres + variables). **Email is off on staging**
(`RESEND_API_KEY` unset) so testing registration flows can never email a real person.

> The app is already staging-ready in code — all config is env-var driven (`DATABASE_URL`,
> `APP_URL`, `SECRET_KEY`). Nothing here requires a code change beyond the `seed_staging.py` script
> already in the repo. The steps below are Railway-dashboard + CLI work.

---

## Prerequisites

- [ ] A Railway plan that supports **multiple environments** (Pro/Team). If you're on Hobby, this
      runbook's "new environment" step isn't available — tell Claude and we'll switch to a separate
      Railway *project* instead (same end state, more manual setup).
- [ ] Railway CLI installed and logged in (`railway login`) — needed for seeding.

---

## Part A — Create the staging environment (Railway dashboard)

1. [ ] Open the **Baseline** project in Railway.
2. [ ] Environment switcher (top of the project) → **New Environment** → **fork from
       `production`**. Name it **`staging`**. Forking copies the service config + variables so you
       start from a working baseline.
3. [ ] **Confirm the staging Postgres is its own isolated database**, not a pointer to prod. After
       the fork, open the Postgres service *in the staging environment* and check its connection
       info differs from production's. **This is the most important check in the whole runbook** —
       staging must never read or write the production database. If the fork shared the DB, add a
       fresh PostgreSQL service to the staging environment and repoint the web service's
       `DATABASE_URL` at it (Variables → reference the staging Postgres).
4. [ ] Open the **web service** in the staging environment → **Settings → Deploy** (or Source) →
       set the deployment branch to **`staging`** (production stays on `main`). Enable automatic
       deploys. Now: push `staging` → staging deploys; push `main` → prod deploys.

## Part B — Set staging variables (Railway dashboard → staging env → web service → Variables)

Set these **on the staging environment only**. Leave production untouched.

| Variable | Value | Why |
|---|---|---|
| `SECRET_KEY` | a **new** random secret — `python -c "import secrets; print(secrets.token_hex(32))"` | Don't share prod's session/CSRF secret with staging. |
| `APP_URL` | the staging URL (e.g. `https://baseline-staging.up.railway.app`) | Email/link building. Grab it from the service's public domain after the first deploy. |
| `ADMIN_EMAIL` | `staging@baseline.test` | Makes the seeded staging user an admin (so `/admin/*` is reachable on staging). |
| `ANTHROPIC_API_KEY` | reuse prod's key, or a separate key | AI check-in won't work without it. Separate key = cleaner cost attribution; reuse is fine. |
| `RESEND_API_KEY` | **leave UNSET** | Email off on staging — verification/welcome sends fail silently (the code's built-in behavior when unset). Zero risk of emailing a real person. |
| `RESEND_AUDIENCE_ID` | **leave UNSET** | No Resend contact-list sync from staging. |
| `BACKFILL_RESEND_CONTACTS` | **never set** | One-shot prod-only backfill; must never run on staging. |
| `WTF_CSRF_ENABLED` | **leave UNSET** (= on) | Match prod so the CSRF token flow is exercised on staging too. |
| `DATABASE_URL` | auto (staging Postgres reference) | Set by Railway; just confirm it points at the **staging** Postgres (see A3). |

> **Note on dev routes:** `/dev/seed` and `/dev/reset` are gated on `app.debug`, which is now driven
> **only by the `DEBUG` env var** — leave it unset on staging and production and they stay blocked.
> Corrected 8/12/26: this note used to say "which is False under gunicorn", but there was no Procfile
> and no start command, so gunicorn was never running — the builder ran `python app.py`, which
> hardcoded `debug=True`. The dev routes were live and the Werkzeug debugger was exposed. Fixed by
> the Dockerfile + Procfile + env-gated entrypoint; see BACKLOG Decision Log.

## Part C — Push the staging branch

The `staging` branch already exists locally with the seed tooling on it. Push it so Railway deploys:

```bash
git push -u origin staging
```

- [ ] Confirm Railway's **staging** environment deploys green from the `staging` branch.
- [ ] Visit the staging URL — you should see the login page.

> This pushes the `staging` branch to GitHub and deploys to **staging only**. Production (`main`) is
> untouched. The local `main`-branch backlog commit rides along on `staging` and reaches prod only
> when you later merge `staging → main` — that's the intended "batch it with the next deploy."

## Part D — Seed staging with test data

Reuses the exact `/dev/seed` logic (`app.seed_test_data`), so staging data matches local dev —
including triggers, a binary symptom, and `Protocol.why`, which the upcoming migrations touch.

From the repo root, with the Railway CLI linked to the project + **staging** environment
(`railway link`, then select staging).

> **Important — use the public DB URL, not `railway run` alone.** The app's `DATABASE_URL` points at
> Railway's **private** host (`postgres.railway.internal`), which only resolves *inside* Railway's
> network — a plain `railway run python seed_staging.py` from your Mac fails with
> `could not translate host name "postgres.railway.internal"`. Seed by overriding `DATABASE_URL`
> with the Postgres service's **public** proxy URL (`DATABASE_PUBLIC_URL`), while still passing the
> real `APP_URL` so the safety guard is meaningful:

```bash
# 1) (safe, read-only) confirm the DB is the fresh staging one — expect 0 users, never your prod count
PUBURL=$(railway variables -s Postgres --json | python3 -c "import sys,json;print(json.load(sys.stdin)['DATABASE_PUBLIC_URL'])")
DATABASE_URL="$PUBURL" python3 -c "from app import app; from database import User; app.app_context().push(); print('users:', User.query.count())"

# 2) seed (only after the count above is 0 / clearly not production)
APPURL=$(railway variables -s baseline --json | python3 -c "import sys,json;print(json.load(sys.stdin)['APP_URL'])")
DATABASE_URL="$PUBURL" APP_URL="$APPURL" CONFIRM_SEED=yes python3 seed_staging.py
```

The script:
- refuses to run without `CONFIRM_SEED=yes`,
- **hard-refuses if `APP_URL` looks like production** (belt-and-suspenders against a mis-linked env —
  so keep passing the real `APP_URL`, don't hardcode a fake staging one),
- creates user `staging@baseline.test` / `Staging2026!` (override via `STAGING_SEED_EMAIL` /
  `STAGING_SEED_PASSWORD`),
- writes 12 weeks of data, and is **idempotent** (skips if the user already has ≥20 episodes).

> Alternative (no public URL): run the seed *inside* the container via `railway ssh` (or the service
> **Console** tab), where `postgres.railway.internal` resolves — requires the container to be on the
> `staging` branch so `seed_staging.py` is present.

- [ ] Log in to staging as `staging@baseline.test` / `Staging2026!` and confirm the dashboard shows
      episodes, charts, protocols, and trigger data.

## Part E — Ongoing workflow

```
feature work  →  push to `staging`  →  verify on the staging URL  →  merge `staging → main`  →  prod deploys
```

- Do risky migrations on `staging` first; watch them run against the staging Postgres before `main`.
- Keep `staging` and `main` from drifting: after a prod deploy, fast-forward `staging` to `main`
  (or rebase) so staging keeps mirroring prod.

---

## Division of labor

- **Claude did (in code):** created the `staging` branch, extracted `seed_test_data()` (shared by
  `/dev/seed` + the script), wrote and verified `seed_staging.py` (guards + trigger/binary/why
  coverage), wrote this runbook.
- **You do (needs your Railway account):** Parts A–D above — create the environment, verify the
  isolated Postgres, set variables, push `staging`, run the seed. Ping Claude if the Postgres isn't
  isolated or the plan doesn't support environments.
