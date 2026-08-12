# Baseline — Technical README

Last updated: July 18, 2026

---

## Overview

Baseline is a web application for tracking health protocols and experiments for people managing chronic conditions. Built with Python/Flask, deployed on Railway, using PostgreSQL in production and the Anthropic API for AI-powered daily check-ins.

Live at: **https://mybaselineapp.com** (custom domain; Railway default: baseline-health.up.railway.app)

---

## Tech Stack

- **Backend:** Python 3.10, Flask
- **Database:** SQLAlchemy ORM — PostgreSQL (production), SQLite (local dev). Local SQLite enforces foreign keys (`PRAGMA foreign_keys=ON` on every connection, set in `database.py`) so FK-unsafe deletes fail in dev the same way they would on production PostgreSQL (added 7/12/26 after the protocol-delete incident).
- **Frontend:** Jinja2 templates, vanilla JavaScript, Chart.js
- **AI:** Anthropic API (claude-sonnet-4-6) for check-in parsing. Onset is **classify-and-resolve** (7/17/26): the model returns a time phrase + type (never a computed date); `resolve_onset()` resolves it deterministically against the message anchor using **`dateparser`** + a colloquial-normalization layer, never logging a future onset.
- **Auth:** Flask sessions, bcrypt password hashing, self-serve registration with email verification (itsdangerous signed tokens, 24h TTL), Flask-Limiter rate limiting, CSRF protection (Flask-WTF)
- **Hosting:** Railway. Built from the repo's **`Dockerfile`** (`python:3.10-slim`), not Railway's railpack/`mise` builder — the base image is what pins the runtime. The Dockerfile's `CMD` (gunicorn, 1 worker, `--timeout 120`) is the **only in-repo definition of how the app starts**; there is deliberately no Procfile. **Caveat:** Railway's dashboard **Custom Start Command** field overrides the image `CMD` at the deploy layer and does not appear in `railway logs --build`, so "the Dockerfile decides" only holds while that field is blank — confirm it (STAGING_SETUP.md verification list). Two environments: `staging` branch → staging, `main` branch → production.
- **PWA:** manifest.json, service worker, home screen icons (Pillow-generated)

---

## Architecture Rules (Non-Negotiable)

Two distinct failure modes — full text in CLAUDE.md. Enforced in code review + QA (section 0 of both agent checklists).

### Rule 1 — The Backend Stays Stateless (scalability)

Any server instance must handle any request; **no server holds state in its own memory or local disk that another wouldn't have.** Sessions → signed client cookie / shared store (compliant today); uploaded files & artifacts → object storage, never local disk; no in-memory caches, cross-request temp files, or single-instance schedulers. **Test:** would a change make one server remember something another doesn't? → violation, flag it. *Known deviation:* Flask-Limiter in-memory backend (P2 → Redis).

### Rule 2 — Slow Work Belongs Off the Request Path (responsiveness)

Slow/blocking work must not run synchronously in-request when the caller doesn't need the result inline — it ties up a request worker and causes latency/timeouts under load. Fire-and-forget (email) → background job queue; batch (AI trigger analysis) → worker; interactive-but-slow (AI check-in) → async job pattern (submit → poll/stream). **This leaves no per-server state — it is *not* a Rule 1 issue.** The deferral mechanism must itself be shared (Redis queue), not an in-process thread — else it fixes Rule 2 but breaks Rule 1. *Known deviations:* transactional email in-request (P2 → Redis + worker), AI check-in in-request (rebuild-era async job).

---

## Project Structure

```
app.py                        — all routes and business logic
database.py                   — SQLAlchemy models
requirements.txt              — Python dependencies
Dockerfile                    — THE production build + start command (gunicorn); Railway builds from this
.dockerignore                 — keeps .env, instance/*.db and Baseline Files/ out of the image
run.sh                        — local startup script (exports DEBUG=true, runs the Flask dev server)
seed_staging.py               — seed the staging DB (railway run; reuses app.seed_test_data)
CLAUDE.md                     — Claude Code persistent context document
generate_icons.py             — PWA icon generation (Pillow)
.env                          — environment variables (not committed)

static/
  css/style.css               — all styles, dark theme
  icons/                      — PWA app icons (192px, 512px, apple-touch)
  manifest.json               — PWA manifest
  sw.js                       — service worker with offline support

templates/
  base.html                   — base template, nav, PWA meta tags
  index.html                  — dashboard with Chart.js visualizations
  login.html                  — login page (surfaces resend prompt for unverified accounts)
  register.html               — self-serve registration with privacy acknowledgment + password validation
  verify_sent.html            — "check your email" confirmation page
  verify_result.html          — verification link expired/invalid/error landing
  resend_verification.html    — request a new verification link
  offline.html                — PWA offline fallback page
  settings.html               — user settings, change password/email, delete account
  symptoms.html               — symptom management
  episodes.html               — episode log
  protocols.html              — protocol management
  experiments.html            — experiment tracking
  assess_experiment.html      — data-informed experiment assessment
  help.html                   — user help and documentation

Baseline Files/
  TECHNICAL_README.md         — this file
  BACKLOG.md                  — product backlog
  baseline-vision-roadmap.docx — product vision

templates/privacy.html          — in-app privacy policy (single source of truth, edited directly)
```

---

## Data Models

| Model | Description |
|---|---|
| User | email, password_hash, invite_code_used (legacy), is_active, verified_at, onboarding_complete, baseline data, ai_logging_enabled, has_seen_tour, is_admin, email_updates_enabled, `timezone` (nullable IANA string; auto-detected from the `baseline_tz` cookie and persisted by `sync_user_timezone()` in `require_auth`. Resolved **cookie-first → stored fallback → server UTC** by `user_tz_name()`, which backs `user_today()`. Both cookie and stored values are `ZoneInfo`-validated), plus **episode-diary Layer A** `diary_mode_enabled` (bool, default False) + `diary_span_counts_both_days` (bool, default True). Both unwritten in the monolith — schema pre-staged for React. |
| InviteCode | code, created_at, used_at, used_by_user_id (legacy — admin use only) |
| UsedVerifyToken | token_hash (SHA-256), used_at — prevents email-verification token replay |
| Symptom | user-defined trackable items (name, description, is_active, input_type). Displayed as "What I Track" in UI. No hard post-onboarding limit. `input_type` is `'scale'` (1-10 slider) or `'binary'` (Yes/No), enforced by a DB CHECK constraint (`symptoms_input_type_check`). Type is locked once any SymptomScore for that symptom exists. |
| Episode | onset timestamp, duration, functional_impairment, notes |
| SymptomScore | One row per (episode, symptom). For scale symptoms `score` (1-10) is set and `value_bool` is null. For binary symptoms `value_bool` is set and `score` is null. The `Symptom.input_type` discriminator decides which column to read. Aggregations should filter `score IS NOT NULL` (scale) or `value_bool IS NOT NULL` (binary) so the two types never mix. |
| EpisodeIntervention | junction table: episode_id, protocol_id (rescue), effectiveness (1-10), time_to_relief_hours. Supports multiple interventions per episode. |
| Trigger | episode trigger dimension, hybrid: `user_id` NULL = curated **global** seed (shared, admin-curated, ≤12 via `SEED_TRIGGERS`); non-null = user **custom**. `name` (100), `is_active`, `created_at`. Two **partial** unique indexes enforce name uniqueness (`ux_trigger_global_name`: unique `lower(name)` WHERE `user_id IS NULL`; `ux_trigger_user_name`: unique `(user_id, lower(name))` WHERE `user_id IS NOT NULL`) — a plain composite index wouldn't constrain globals (NULLs compare distinct). Seeded idempotently in `run_migrations()`. Custom deletion is a soft-deactivate (`is_active=False`), never hard-delete. **Wired 7/15/26:** episode picker + inline add (new/edit), AI check-in parity (link matched via `_match_trigger`, suggest-and-confirm for new), and Settings management (Manage Triggers, `/triggers` — rename + pause/resume own customs). Dashboard reads/breakdowns deferred to the React rebuild. |
| EpisodeTrigger | junction table: episode_id, trigger_id, `source` ('user'\|'ai', default 'user'); unique `(episode_id, trigger_id)`. Episode-side `cascade='all, delete-orphan'`, so `db.session.delete(episode)` clears links. Written via `_save_episode_triggers_from_form()`. |
| Protocol | name, start_date, dose, frequency, status, `why` (nullable text — "Why I'm doing this", surfaced in compliance messaging) (preventative). `med_class` (nullable VARCHAR(30), **episode-diary Layer A** — drug class for step-therapy docs; **no CHECK**, type-dependent vocabulary ratified with the React capture UI; unwritten in the monolith). |
| ProtocolEvent | status/dose-change log: `protocol_id`, `user_id`, `event_type`, `detail`, back-datable `date`, `created_at`. **Episode-diary Layer A** added `stop_reason` (nullable VARCHAR(30)) + `stop_reason_note` (nullable TEXT). `stop_reason` carries CHECK `protocol_events_stop_reason_check` = `stop_reason IS NULL OR (event_type IN ('stopped','paused') AND stop_reason IN ('ineffective','side_effects','cost','doctors_advice','other'))` — predicate defined once as `STOP_REASON_CHECK_SQL` in `database.py`, imported by `run_migrations()` so model DDL and migration can't drift. `detail` deliberately NOT reused (`assess_experiment` already writes it on a status event). Both columns unwritten in the monolith (capture = React). |
| ProtocolCompliance | daily compliance log per protocol: (user_id, protocol_id, date, took bool, notes). One row per (user, protocol, day) enforced by unique index `ux_protocol_compliance_day` (dedup pass + `CREATE UNIQUE INDEX IF NOT EXISTS` in `run_migrations()`, plus a model-level UniqueConstraint for fresh DBs). All writers (dashboard batch confirm, protocol-detail form, AI check-in) go through the shared `_upsert_compliance()` helper — the most recent explicit statement wins. Days with no row = nothing recorded; the dashboard card's "assumed taken" state is UI-only and never written until the user confirms. |
| RescueOption | interventions (stored as Protocol with type='rescue') |
| Experiment | hypothesis, protocol_id, start_date, stabilization_weeks (default 3), status, outcome |
| CheckIn | AI chat history |
| UserActivity | First-party usage analytics: user_id (nullable FK), event_type (signup/login/page_view), detail (endpoint name), created_at. Indexed on (event_type, created_at). |

---

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for AI check-in |
| `SECRET_KEY` | Yes | Flask session secret key (also backs CSRF tokens) |
| `DEBUG` | No | `true` locally only, `false` in production |
| `WTF_CSRF_ENABLED` | No | CSRF on by default (unset = on, incl. local dev + prod). Set `false` **only** in test harnesses that POST via the test client without tokens. Never set in production. |
| `DATABASE_URL` | Production only | Set automatically by Railway PostgreSQL reference |
| `RESEND_API_KEY` | No | Resend API key for transactional email. From address is hardcoded to `Baseline <hello@mybaselineapp.com>` (domain must be verified in Resend). If unset, email sends fail silently — used in local dev. Replaces the prior Gmail SMTP flow (Railway blocks outbound SMTP — errno 101). |
| `RESEND_AUDIENCE_ID` | No | Resend audience UUID for contact-list sync. When set (alongside `RESEND_API_KEY`), verify/unsubscribe/email-change/account-delete events upsert or remove the user's contact in this audience with the current `email_updates_enabled` state. Unset locally → all sync calls are no-ops. |
| `BACKFILL_RESEND_CONTACTS` | No | Set to `1` for one deploy to upsert every active verified user into the Resend audience at startup, then unset. Idempotent (re-running just patches the `unsubscribed` flag). Logs a warning and skips if `RESEND_AUDIENCE_ID`/`RESEND_API_KEY` not configured. |
| `APP_URL` | No | Base URL for email links (defaults to `https://baseline-health.up.railway.app`) |
| `ADMIN_EMAIL` | No | Email address to grant admin access on startup (defaults to `daniels.missy@gmail.com`) |

Local `.env` file uses `load_dotenv(override=True)` to ensure `.env` always wins over shell environment.

---

## Foreign Keys / ON DELETE

All 20 foreign keys carry DB-level `ON DELETE` directives, declared once in `database.EXPECTED_FK_ONDELETE` (16 `CASCADE`, 3 `SET NULL`). That dict is authoritative for the model `ondelete=` kwargs, the PostgreSQL migration (`migrate_fk_ondelete`), and `verify_fk_ondelete()`.

- **`SET NULL` is only** `checkins.episode_id`, `experiments.protocol_id`, `invite_codes.used_by_user_id` — the children that deliberately outlive their parent. Changing one to `CASCADE` destroys user history.
- **Both increments shipped 8/8/26.** Inc 1 added the constraints (behaviour-preserving); Inc 2 removed the hand-ordered cleanup and added `passive_deletes=True` to 11 relationships, so a hard delete is now a single `db.session.delete(parent)` and the DB cascades. `dev_reset` is the one exception — it keeps the `User` row, so it still bulk-deletes each top-level owned table (order matters: `CheckIn` before `Episode`, or `SET NULL` detaches chat history instead of removing it).
- **The constraints are load-bearing now** — there is no app-level fallback. Every hard-delete route keeps an `except IntegrityError` guard, because `run_migrations()` and `check_fk_ondelete()` are both deliberately non-fatal, so a mis-migrated environment surfaces there.
- **`check_fk_ondelete()`** runs at startup, warn-only, and reports any live-schema mismatch. On local SQLite the fix is to delete `instance/migraine_tracker.db` and restart — SQLite cannot alter constraints in place.
- **`check_fk_orphans.py`** is a read-only pre-flight to run against staging and production *before* the migration: `ADD CONSTRAINT` validates existing rows, and one orphan leaves that FK on its old behaviour while the rest migrate.

```bash
DATABASE_URL='postgresql://...' python3 check_fk_orphans.py   # use the PUBLIC Railway URL
```

---

## Local Development

### Prerequisites
- Python 3.10+
- Node.js v24+ (for Claude Code)
- An Anthropic API key

### First Time Setup
```bash
git clone https://github.com/missydaniels-pm/baseline.git migraine-tracker
cd migraine-tracker
# Create .env file with ANTHROPIC_API_KEY and SECRET_KEY
./run.sh
```

Open http://localhost:5001

### Subsequent Starts
```bash
cd ~/migraine-tracker && ./run.sh
```

### Claude Code
```bash
cd ~/migraine-tracker && claude        # new session
claude --resume                         # resume previous session
```

### Gotcha — editing CSS alone requires a server restart

`static/css/style.css` is cache-busted by `CSS_VERSION`, an md5 of the file computed **once at import time** (`app.py`, `_compute_asset_version`). The Flask dev reloader only watches `.py` files, so **editing CSS alone does not restart the app** — the stale hash keeps getting served, the browser cache-hits the old URL, and your CSS changes appear to do nothing. Restart the server after a CSS-only edit.

**Production is unaffected** — every Railway deploy restarts the process, so the hash is always recomputed against the deployed file. This is a local-dev-loop wrinkle only, not a bug to fix. (Noted 8/8/26 after it cost real debugging time.)

Killing the dev server: `pkill -f "python3 app.py"` does **not** match it — the process shows as the framework interpreter's absolute path. Use `lsof -nP -iTCP:5001 -sTCP:LISTEN` to find the pid and `kill` it, or the restart silently fails with "Address already in use" while the old process keeps serving.

---

## Routes

### Public
| Route | Method | Description |
|---|---|---|
| `/login` | GET, POST | Login (rate-limited 20/hour POST). Surfaces resend prompt for unverified accounts. |
| `/register` | GET, POST | Self-serve registration (rate-limited 5/hour POST). Requires privacy policy acknowledgment. Creates inactive user and sends verification email. |
| `/verify/<token>` | GET | Email verification callback. Signed itsdangerous token, 24h TTL, SHA-256 replay protection via `used_verify_tokens`. |
| `/verify/sent` | GET | "Check your email" confirmation page. |
| `/resend-verification` | GET, POST | Resend verification email (rate-limited 5/hour POST). Enumeration-safe. |
| `/unsubscribe/<token>` | GET | One-click unsubscribe from app-update emails. Stateless HMAC-SHA256 token over SECRET_KEY (no expiry). Rate-limited 30/hour/IP. Sets `email_updates_enabled=False` and syncs Resend contact. Enumeration-safe — invalid token, tampered token, and unknown-email all return the same generic error. |
| `/logout` | GET | Logout |
| `/sw.js` | GET | Service worker (must be served from root scope) |
| `/offline` | GET | PWA offline fallback |

### Authenticated
| Route | Method | Description |
|---|---|---|
| `/` | GET | Dashboard with charts |
| `/onboarding` | GET, POST | First-run onboarding wizard |
| `/checkin` | GET, POST | AI daily check-in. Parses episodes/interventions/compliance and now triggers: matched triggers link (`source='ai'`) via `_apply_ai_triggers()`; unmatched names become session-held suggestions surfaced for confirmation on the episode form. |
| `/episodes` | GET, POST | Episode log. New/edit episode forms include a trigger chip multi-select + inline "+ add" (match-and-link via `_resolve_trigger()` / `_save_episode_triggers_from_form()`). |
| `/triggers` | GET | Manage Triggers (linked from Settings, not in nav): rename + pause/resume the user's own custom triggers; globals read-only. |
| `/triggers/<id>/rename` | POST | Rename an own custom (rejects collision with a global or another own custom; 100-char cap). |
| `/triggers/<id>/deactivate`, `/triggers/<id>/reactivate` | POST | Soft pause/resume an own custom (history preserved). |
| `/symptoms` | GET, POST | Symptom management |
| `/protocols` | GET, POST | Protocol management |
| `/experiments` | GET, POST | Experiment tracking (supports inline protocol creation) |
| `/assess_experiment/<id>` | GET, POST | Experiment assessment |
| `/settings` | GET, POST | User settings |
| `/settings/change-password` | POST | Change password |
| `/settings/change-email` | POST | Change email. Also updates Resend contact (delete old, upsert new with current `email_updates_enabled`). |
| `/settings/email-preferences` | POST | Toggle `email_updates_enabled`. Mirrors the new state to Resend audience. |
| `/settings/delete-account` | POST | Delete account and all data (MHMD compliance). Removes Resend contact before DB delete. |
| `/help` | GET | Help and documentation |
| `/admin/analytics` | GET | Admin-only usage analytics dashboard (signups, logins, DAU/WAU, feature usage, retention) |
| `/admin/users` | GET | Admin-only read-only user directory: email, verified, joined, last login, email_updates_enabled, episode count. Unauthorized access logs a warning. Excluded from `SKIP_TRACKING` so admin browsing doesn't pollute analytics. |
| `/protocols/log-day` | POST | JSON batch compliance save for the dashboard Today's Protocols card (confirm + 7-day backfill). Body `{date, entries: [{protocol_id, took, note}]}`. `took` is tri-state: `true`/`false` upsert the day's row, `null` un-logs it (deletes any existing row). Validates date within `[user_today−7, user_today+1]` (anchored on the user's local day via `user_today()` so it lines up with the browser-local date the card sends), ownership of every protocol id, `took ∈ {true,false,null}` (other types → 400), note ≤500 chars — all before touching the session, so an invalid entry can't partial-write. Single commit, returns `{ok, date, day_status}` where day_status ∈ hollow/amber/green. |
| `/episodes/<id>/delete` | POST | FK-safe (fixed 7/12/26): detaches AI check-in messages referencing the episode (`checkins.episode_id=NULL` — chat history survives) before deleting; SymptomScores/EpisodeInterventions removed by ORM cascade; IntegrityError → rollback + friendly flash. Previously 500'd in production for episodes logged via AI check-in. |
| `/protocols/<id>/delete` | POST | Hard delete is **one statement** as of FK cleanup Increment 2 (8/8/26): `db.session.delete(protocol)` and the DB cascades EpisodeIntervention / ProtocolCompliance / ProtocolEvent, and detaches Experiments via `ON DELETE SET NULL` (hypothesis/outcome history survives; templates guard with `{% if exp.protocol %}`). **Do not reintroduce hand-ordered child cleanup.** An active experiment testing the protocol is marked abandoned first (8/8/26) — that's product logic no FK directive can express. Rescue protocols with historical episode usage soft-delete (`status='removed'`) instead. Guarded by `deletion_blocked_response()` + `except IntegrityError`. |
| `/tour/complete` | POST | Mark welcome tour as seen (JSON response) |
| `/tour/restart` | GET | Reset tour flag and redirect to dashboard |

### Dev Only (DEBUG=true)

All dev routes are grouped in a clearly marked section at the bottom of `app.py`. Every route has an explicit `if not app.debug: return 403` guard so they are automatically blocked in production (Railway, DEBUG=false).

| Route | Description |
|---|---|
| `/dev/reset` | Clear all user data, reset onboarding for testing |
| `/dev/seed` | Populate 12 weeks of realistic test data (only if <20 episodes) |
| `/dev/create-invite` | Generate a new invite code |
| `/dev/bootstrap` | Create admin account on fresh empty database |

---

## Deployment

### Workflow — staging is a **gate**, not a waypoint
1. Make and test changes locally at http://localhost:5001
2. Commit and push **`staging` only** — never `staging` and `main` in one command
3. Wait for the staging build to finish, then **verify against the staging URL**
4. Only then merge `staging → main` and push; production deploys
5. Verify production the same way

**Verify the artifact, not a proxy signal.** A green Railway badge, an `ACTIVE` status or an HTTP 200
means a container booted — not that your code is serving. Check a marker only the new build can
produce. Two standing checks (must hold on staging *and* production):

```bash
# expect NOT 200 — a 200 with ~10KB of JS means the Werkzeug debugger is exposed
curl -s -o /dev/null -w "%{http_code}\n" "$URL/?__debugger__=yes&cmd=resource&f=debugger.js"
# expect 403 "Not available in production." — only reachable when app.debug is False
curl -s -w " %{http_code}\n" "$URL/dev/bootstrap"
```

Confirm the *build* too when the build config changed — `railway logs --build` should show
`load build definition from Dockerfile` and `python:3.10-slim`, never `mise` or railpack.

**Important:** Every push to main deploys to production immediately. Real users are on the app.
Both times this bit us (8/12/26) the deploy looked healthy: once because staging was pushed
alongside main instead of ahead of it, and once because the deployment was ACTIVE while Railway's
public-domain **target port** still pointed at the previous deployment's port, so every request 502'd.

### Staging (live since 7/17/26)
- Separate Railway **environment** (`staging`) in the same project, its own Postgres, deploying from the **`staging` branch**. `main` → production.
- Email off on staging (`RESEND_API_KEY` unset). Full setup/runbook: `Baseline Files/STAGING_SETUP.md`.
- Seed with `CONFIRM_SEED=yes railway run python seed_staging.py` (reuses `app.seed_test_data`; guards against running against production). Dev routes (`/dev/seed`, `/dev/reset`) stay `app.debug`-gated → inert on staging, hence the script.
- Workflow: feature work → push `staging` → verify on staging URL → merge `staging → main` → prod deploys.

### Railway Services
- **baseline** — Flask app on **Python 3.10** (from the `python:3.10-slim` base image), served by
  **gunicorn** via the Dockerfile `CMD`: one worker, `--timeout 120`, bound to `$PORT` (8080).
  One worker matches the historical single-process behaviour — more workers would run the startup
  migrations concurrently. `--timeout 120` because the AI check-in's Anthropic call is in-request
  and would exceed gunicorn's 30s default. `CMD` uses `sh -c "exec gunicorn …"`; the explicit `exec`
  is **hardening, not a fix** — the familiar "shell form leaves `sh` as PID 1 and swallows SIGTERM"
  story was tested 8/13/26 and did not reproduce (`sh -c '<single command>'` implicit-execs), so it
  just makes the guarantee explicit and fails safe if a second command is ever appended. PID 1 in the
  real container is unverified.
  *(Corrected 8/13/26: this line read "gunicorn, Python 3.13" — gunicorn was not actually running
  at all before 8/12/26, and the version became 3.10 when the build moved to the Dockerfile.)*
- **Postgres** — PostgreSQL database with persistent volume

### Database Handling
- Production: `DATABASE_URL` environment variable (Railway reference)
- Local: SQLite at default path
- `postgres://` URLs are rewritten to `postgresql://` for SQLAlchemy compatibility
- `db.create_all()` runs outside `__name__ == '__main__'` block so gunicorn triggers table creation on first deploy
- Boolean column migrations must use `DEFAULT TRUE` / `DEFAULT FALSE` (not `0`/`1`) for PostgreSQL compatibility

---

## PWA

Baseline is a Progressive Web App. Users can install it to their home screen:
- **iOS:** Safari → Share → Add to Home Screen
- **Android:** Chrome → Add to Home Screen

Icons are generated by `generate_icons.py` using Pillow. Purple background (#7c3aed), white EKG pulse graphic. Regenerate with:
```bash
python generate_icons.py
```

---

## Security Notes

- Passwords hashed with bcrypt, never stored in plain text
- Sessions encrypted with SECRET_KEY
- All production traffic over HTTPS (Railway provides SSL)
- Dev routes grouped in a dedicated section with explicit `if not app.debug` guards — blocked in production (DEBUG=false)
- Account deletion is a single `db.session.delete(user)`; the database cascades every owned child (FK cleanup Increment 2, 8/8/26). Invite codes are detached (`SET NULL`) and their `used_at` cleared explicitly, since SET NULL only clears the FK column. `cleanup_stale_unverified_users()` deliberately still anonymises `UserActivity` by hand — it needs the opposite of the declared CASCADE.
- Data deletion satisfies Washington State My Health MY Data Act (MHMD) requirements
- **Email verification (self-serve registration):** New accounts created with `is_active=False` and `verified_at=None`. Signed itsdangerous token (HMAC over SECRET_KEY, salt `baseline-email-verify-v1`, 24h max_age) emailed as a verification link. On verify, token SHA-256 hash stored in `used_verify_tokens` to prevent replay; user flipped to `is_active=True` with `verified_at=now()`. Welcome email sent post-verification.
- **CSRF protection:** Flask-WTF `CSRFProtect(app)` — global/opt-out, covers every POST. Forms carry a hidden `{{ csrf_token() }}`; JSON fetch POSTs (`/protocols/log-day`, `/tour/complete`) send it as an `X-CSRFToken` header read from a `<meta name="csrf-token">` (`csrfToken()` helper in base.html). `CSRFError` handler → friendly flash+redirect for forms, `{ok:false}` 400 for JSON. Secret rides SECRET_KEY. On by default incl. local dev; tests set `WTF_CSRF_ENABLED=false`.
- **Rate limiting:** Flask-Limiter (in-memory backend). `/register` and `/resend-verification` 5/hour/IP; `/login` 20/hour/IP.
- **Disposable-email blocklist:** common throwaway domains rejected at `/register`.
- **Privacy acknowledgment:** `/register` requires a checkbox acknowledging the Privacy Policy (server-enforced).
- **Stale unverified cleanup:** accounts with `verified_at IS NULL` and `created_at` older than 48 hours are deleted at app startup, freeing the email for re-registration. Fixed 7/12/26: this job had silently failed on every startup since analytics shipped (4/22) — the signup `UserActivity` row blocked the User delete on its FK and a bare `except` hid it, so unverified accounts were retained indefinitely despite the documented policy. Now anonymizes activity rows (`user_id=NULL` — signup counts stay accurate), commits per-user, and logs failures. Any stale accounts accumulated in production are purged on the first startup after this deploy.
- Welcome email sent on successful email verification via Resend API (`resend` package). From address `Baseline <hello@mybaselineapp.com>` (domain verified in Resend). Fails silently if `RESEND_API_KEY` not configured. HTML + plain text. Previously used Gmail SMTP — retired because Railway blocks outbound SMTP (errno 101 network unreachable).
- **Email opt-in / unsubscribe:** `email_updates_enabled` boolean on User (default True). Welcome email footer carries an unsubscribe link with a stateless HMAC-SHA256 token over `SECRET_KEY` (salt `baseline-email-unsubscribe-v1`, no expiry — old emails should still work years later). The verification email stays purely transactional and has no unsubscribe footer. Trade-off: rotating `SECRET_KEY` invalidates all in-flight unsubscribe tokens; users must use Settings instead. Resend audience contacts are kept in sync on verify, settings toggle, email change, unsubscribe, and account delete; sync gated on `RESEND_AUDIENCE_ID` and log-and-swallow on failure (never blocks user flows). The Resend SDK accepts email as the contact identifier on update, so the upsert pattern (try create → fall back to update on any 4xx/5xx) is valid.
- Welcome tour modal shown on first dashboard visit after onboarding (`has_seen_tour` flag on User model). Replayable from Help page via `/tour/restart`.

---

## Dashboard

The dashboard header contains two primary action buttons: "Start Check-in →" (links to `/checkin`) and "+ Log Episode" (links to `/episodes/new`), giving users immediate access to the two main logging paths without navigating the sidebar.

### Today's Protocols Card

Top-of-dashboard daily compliance card (renders only when the user has active preventative protocols; replaced the old "Active Protocols" list — a "Manage →" link in the card header preserves the path to `/protocols`).

**Historical data note:** before this card shipped (7/12/26), "assumed compliance" was a product philosophy, not a database write — no row was ever created for unlogged days. Protocol history for pre-existing users therefore only contains days they explicitly logged (protocol-detail form or AI check-in). Sparse pre-7/12 history is expected, not data loss, and those blank days are intentionally left blank (backfill is capped at 7 days).

- **Tri-state model (Complete / Not Today / blank):** each protocol row renders **Complete** and **Not Today** pills (relabeled from Taken/Missed 7/14/26 to drop medication bias for non-med protocols). Pills are tri-state with **tap-again-to-clear** — tapping a selected pill returns it to a genuine blank/unmarked state (no pill pressed; the old dashed "assumed-taken" pre-selection was removed). The assumption "blank = complete" now lives only in the button label, not in any pre-selected styling. Nothing is written to the DB until the user acts:
  - **Button label is state-driven:** **"Confirm all as complete"** when *nothing* is explicitly marked (one tap writes every protocol taken — the assumed-compliance fast path), switching to **"Save"** as soon as any protocol is marked or cleared.
  - **Save semantics:** `POST /protocols/log-day` accepts `took` = `true`/`false`/`null` per entry. Explicitly-marked protocols write `true`/`false`; blanks are sent as `null`, which **un-logs** the day (deletes any existing row via `_delete_compliance()`) so a protocol can return to unmarked — including after it was already confirmed. Marking Not Today reveals an optional note input and a supportive message. "Mark all Not Today" (renamed from "Missed all today") is a quiet text button that sets everything to Not Today but still requires Save.
  - **Known edge (accepted, per owner spec 7/14/26):** clearing *every* protocol flips the button back to "Confirm all as complete", so the main button can't clear a whole day to hollow in one press. Un-logging works when at least one protocol stays marked (the realistic partial case). Fully clearing a day to hollow is done by clearing each protocol individually and Saving while one remains marked. *(Corrected 8/12/26, exit-gate review: this note used to add "or by un-logging via the protocol-detail form" — false. `log_protocol_today` only ever writes Complete/Not Today; the detail form has no clear/un-log path, so the dashboard card is the only un-log surface.)*
- **Completion state:** a fully-resolved save (every protocol marked) collapses the card to "All set for today" / "Logged for today (N not completed)" with a "change something" link that re-expands. A **partial** save (some protocols left blank) keeps the card expanded, sets the chip to "Partly logged", and shows "Saved. The rest are still open for today." — so partial logging is a first-class state, not a dead end.
- **7-day dot strip:** one dot per day, today rightmost with a purple ring. Green = every active protocol that day is logged **and** Complete; amber = logged but not a clean all-complete day (some Not Today **or** some still blank/partial); hollow dashed = nothing logged. Computed by `_compute_day_status(user_id, day)`, scoped to the protocols active for that day, in the `log_protocol_day` AJAX response; the server render (`index()`) computes the same rule **inline** rather than calling the helper. *(Corrected 8/12/26, exit-gate review: this line claimed the helper was "used by both the server render and the AJAX response" — it never was. The two implementations are currently equivalent, but this is duplicated logic, not a shared helper — exactly the Rule-3 drift shape; converge them when this surface is next touched, or in the rebuild's API.)* (Changed 7/14/26 from the prior "partial-but-all-taken counts as green" rule, which became misleading once partial logging turned into a routine today state — see Decision Log.) 26px visual inside a ~44px hit area. Tapping a past day opens an inline backfill editor (amber "editing" ring): pills default **neutral** with tap-again-to-clear, partial saves allowed, "Not sure — leave blank" exits without writing. The backfill Save also sends tri-state, so clearing a past entry and saving un-logs it; it has no note field, so it carries each protocol's existing note through unchanged (`bfNote`) rather than blanking a note set via the detail form or AI check-in. Only protocols whose `start_date` was on/before that day are listed (approximation; exact ProtocolEvent replay is a backlog item). Server independently enforces the 7-day window. The dot strip is the day selector: **one editing surface at a time** — opening a backfill day hides the today section and vice versa; tapping today's dot closes any open backfill and opens today's editor directly (one-tap equivalent of "change something"). Implicitly closing an editor with unsaved pill changes shows a quiet "{weekday} wasn't saved" notice.
- **Supportive messaging:** server-side catalog (`SUPPORT_MESSAGES` + `pick_support_message()` in app.py) with deterministic daily rotation; per-protocol missed messages resolve `protocol.why` → active experiment hypothesis → generic copy, pre-interpolated in `index()` and embedded via `tojson` so JS only looks up by scenario. Copy rules: no shame, no streak language; misses framed as honest data. Placeholder filling uses `str.replace` (user text may contain braces).
- **Timezone:** the user's local calendar day is resolved through one helper, **`user_today()`** — the single source of truth for every functional day-boundary write/read (dashboard render, detail-form compliance write + read, protocol-event dates, AI check-in clamp). It reads the `baseline_tz` cookie (browser IANA zone set by base.html, 1-year/lax) and **`unquote()`s it** before `zoneinfo`. base.html writes the cookie URL-encoded (`encodeURIComponent`) and Flask returns cookie values un-decoded, so before 7/14/26 `ZoneInfo('America%2FLos_Angeles')` raised and a bare `except` silently fell back to UTC — landing evening (post-~5pm-Pacific) entries a day off / appearing to revert cross-device. `unquote` is idempotent (handles raw cookies too); falls back to server UTC only when the cookie is absent/unparseable. The dashboard card additionally sends the browser's own local date in the fetch body; check-in derives it from `client_time`. **Update — per-user-tz project shipped & live (Increments 1–3, 7/17/26):** a stored `User.timezone` (cookie-first → stored fallback) now backs the day logic; chart month/week bucketing and the future-episode guard resolve on `user_today()`/`user_now()` (exact, not fuzzy-by-offset); check-in onset is classify-and-resolve via `resolve_onset()`. Still deferred to the React rebuild: the naive-local `Episode.onset` UTC migration and unifying the card's browser-sent write date with the cookie (dual-source A1 — negligible at current scale).
- **Concurrency:** batch saves go through `_commit_compliance()`, which retries once on `IntegrityError` — two near-simultaneous confirms (two tabs, PWA + browser) both pass the upsert's check-then-insert, the unique index rejects the loser, and the retry re-applies it as an update so no write is lost. Tri-state entries with `took=null` route to `_delete_compliance()` (a filtered `.delete()`); deletes are idempotent, so replaying them on the retry is safe. The AI check-in commit catches the same error and asks the user to resend rather than 500ing.

### AI Check-in Compliance Parity

The check-in JSON schema's `protocol_compliance` is a list of `{id, took, note}` objects (the parser still accepts legacy bare protocol ids as `took=true`). The AI can record misses with the user's stated reason as the note, and its writes go through `_upsert_compliance()` — so "correction: I actually took everything" updates a day already confirmed on the dashboard, and vice versa. When the AI has no note, an existing user note on that row is preserved (`preserve_note_if_none`).

### Empty States

The dashboard renders informative empty states for new users who have no data yet, rather than hiding sections entirely. Each section checks for actual data before deciding which state to render:

| Section | Empty Condition | Action Link |
|---|---|---|
| Tracking Baseline Cards | No active items | Set up what you track → |
| Episode Frequency Chart | Fewer than 3 episodes or <14 days of data | Log your first episode → |
| Trends Chart | No episodes with severity scores or <14 days of data | Log an episode → |
| Protocol Impact Markers | No active preventative protocols | Add a protocol → |
| Intervention Effectiveness | No episodes with intervention data | Log an episode → |

Empty states include greyed-out SVG chart placeholders, encouraging copy, and action links to the relevant section. Template variables `total_episode_count` and `has_symptom_data` are passed from the `index()` route to support per-section conditional rendering.

### Experiment Creation Flow

Two entry points for creating experiments:

1. **Experiment-first** (`/experiments/new`): User starts from Experiments page. Protocol dropdown includes all active preventatives plus "+ Add new protocol" which reveals inline fields (name, dose/frequency). Selecting or creating a protocol auto-suggests the experiment name as `<protocol name> trial` (editable). If an active experiment exists, a warning modal fires on submit showing the current experiment name and weeks elapsed — user can go back or continue.

2. **Protocol-first** (`/protocols/new` → `/experiments/offer/<id>`): After adding an active protocol, user is redirected to an offer page asking if they want to track it as an experiment. Clicking "Yes" redirects to `/experiments/new?protocol_id=<id>` with the protocol pre-selected.

### Experiments Page Empty State

The experiments page (`experiments.html`) shows a muted preview of the full assessment screen when the user has no experiments. The preview reuses the actual `assess-layout` two-column grid, `assess-*` data panel classes, and `decision-*` option classes from `assess_experiment.html` — wrapped in an `.experiment-preview` container at 50% opacity with `pointer-events: none`. This ensures the preview is a pixel-accurate muted replica of the real assessment experience, showing mock before/during episode frequency comparison (2.1→0.8), symptom score improvements (Headache 6.2→3.1, Nausea 4.8→2.4), and the Continue/Pause/Stop decision cards. Condition: `not active and not completed and not abandoned`.

---

## Known Issues / Active Investigation

- Resolved: blank names accepted on create + edit routes (8/12/26) — nameless records on create, and a blanked name on an existing record on edit. Guard now runs before the length check and before any model assignment on all six routes.
- Resolved: duplicate records from double-tapping Save (8/8/26 — reproduced, then fixed by the global client submit guard in `base.html`; see CONVENTIONS.md "Double-submit protection"). **Residual by design:** two browser tabs and retried requests are *not* covered — DB-backed idempotency keys are specced for the rebuild.
- Resolved: "Add" symptom did nothing (7/18/26; reproduced and fixed 8/8/26 — the episode form's "+ Add" was a silent no-op on the placeholder selection, now disabled-until-valid); partial-week charts (now shown with an asterisk label); future episode dates (decided keep-blocked 7/14/26, guard made exact in tz Increment 2).

---

## Dependencies

Key packages (see requirements.txt for full list):
- `flask` — web framework
- `flask-sqlalchemy` — ORM
- `flask-bcrypt` — password hashing
- `psycopg2-binary` — PostgreSQL adapter
- `gunicorn` — production WSGI server
- `anthropic` — Anthropic API client
- `python-dotenv` — environment variable management
- `Pillow` — PWA icon generation
- `Flask-Limiter` — rate limiting for auth endpoints
- `itsdangerous` — signed email verification tokens (pinned; also a Flask transitive)
