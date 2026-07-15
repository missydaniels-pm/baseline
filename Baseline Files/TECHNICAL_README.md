# Baseline — Technical README

Last updated: April 24, 2026

---

## Overview

Baseline is a web application for tracking health protocols and experiments for people managing chronic conditions. Built with Python/Flask, deployed on Railway, using PostgreSQL in production and the Anthropic API for AI-powered daily check-ins.

Live at: **https://baseline-health.up.railway.app**

---

## Tech Stack

- **Backend:** Python 3.10, Flask
- **Database:** SQLAlchemy ORM — PostgreSQL (production), SQLite (local dev). Local SQLite enforces foreign keys (`PRAGMA foreign_keys=ON` on every connection, set in `database.py`) so FK-unsafe deletes fail in dev the same way they would on production PostgreSQL (added 7/12/26 after the protocol-delete incident).
- **Frontend:** Jinja2 templates, vanilla JavaScript, Chart.js
- **AI:** Anthropic API (claude-sonnet-4-6) for check-in parsing
- **Auth:** Flask sessions, bcrypt password hashing, self-serve registration with email verification (itsdangerous signed tokens, 24h TTL), Flask-Limiter rate limiting
- **Hosting:** Railway (auto-deploys from GitHub main branch)
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
Procfile                      — gunicorn for production
run.sh                        — local startup script
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
| User | email, password_hash, invite_code_used (legacy), is_active, verified_at, onboarding_complete, baseline data, ai_logging_enabled, has_seen_tour, is_admin, email_updates_enabled |
| InviteCode | code, created_at, used_at, used_by_user_id (legacy — admin use only) |
| UsedVerifyToken | token_hash (SHA-256), used_at — prevents email-verification token replay |
| Symptom | user-defined trackable items (name, description, is_active, input_type). Displayed as "What I Track" in UI. No hard post-onboarding limit. `input_type` is `'scale'` (1-10 slider) or `'binary'` (Yes/No), enforced by a DB CHECK constraint (`symptoms_input_type_check`). Type is locked once any SymptomScore for that symptom exists. |
| Episode | onset timestamp, duration, functional_impairment, notes |
| SymptomScore | One row per (episode, symptom). For scale symptoms `score` (1-10) is set and `value_bool` is null. For binary symptoms `value_bool` is set and `score` is null. The `Symptom.input_type` discriminator decides which column to read. Aggregations should filter `score IS NOT NULL` (scale) or `value_bool IS NOT NULL` (binary) so the two types never mix. |
| EpisodeIntervention | junction table: episode_id, protocol_id (rescue), effectiveness (1-10), time_to_relief_hours. Supports multiple interventions per episode. |
| Trigger | episode trigger dimension, hybrid: `user_id` NULL = curated **global** seed (shared, admin-curated, ≤12 via `SEED_TRIGGERS`); non-null = user **custom**. `name` (100), `is_active`, `created_at`. Two **partial** unique indexes enforce name uniqueness (`ux_trigger_global_name`: unique `lower(name)` WHERE `user_id IS NULL`; `ux_trigger_user_name`: unique `(user_id, lower(name))` WHERE `user_id IS NOT NULL`) — a plain composite index wouldn't constrain globals (NULLs compare distinct). Seeded idempotently in `run_migrations()`. Custom deletion is a soft-deactivate (`is_active=False`), never hard-delete. **Data-model pass only — no read/write path wired yet** (episode picker + inline add, Settings management, AI parity are the next increment). |
| EpisodeTrigger | junction table: episode_id, trigger_id; unique `(episode_id, trigger_id)`. Episode-side `cascade='all, delete-orphan'`, so `db.session.delete(episode)` clears links. |
| Protocol | name, start_date, dose, frequency, status, `why` (nullable text — "Why I'm doing this", surfaced in compliance messaging) (preventative) |
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
| `SECRET_KEY` | Yes | Flask session secret key |
| `DEBUG` | No | `true` locally only, `false` in production |
| `DATABASE_URL` | Production only | Set automatically by Railway PostgreSQL reference |
| `RESEND_API_KEY` | No | Resend API key for transactional email. From address is hardcoded to `Baseline <hello@mybaselineapp.com>` (domain must be verified in Resend). If unset, email sends fail silently — used in local dev. Replaces the prior Gmail SMTP flow (Railway blocks outbound SMTP — errno 101). |
| `RESEND_AUDIENCE_ID` | No | Resend audience UUID for contact-list sync. When set (alongside `RESEND_API_KEY`), verify/unsubscribe/email-change/account-delete events upsert or remove the user's contact in this audience with the current `email_updates_enabled` state. Unset locally → all sync calls are no-ops. |
| `BACKFILL_RESEND_CONTACTS` | No | Set to `1` for one deploy to upsert every active verified user into the Resend audience at startup, then unset. Idempotent (re-running just patches the `unsubscribed` flag). Logs a warning and skips if `RESEND_AUDIENCE_ID`/`RESEND_API_KEY` not configured. |
| `APP_URL` | No | Base URL for email links (defaults to `https://baseline-health.up.railway.app`) |
| `ADMIN_EMAIL` | No | Email address to grant admin access on startup (defaults to `daniels.missy@gmail.com`) |

Local `.env` file uses `load_dotenv(override=True)` to ensure `.env` always wins over shell environment.

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
| `/checkin` | GET, POST | AI daily check-in |
| `/episodes` | GET, POST | Episode log |
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
| `/protocols/log-day` | POST | JSON batch compliance save for the dashboard Today's Protocols card (confirm + 7-day backfill). Body `{date, entries: [{protocol_id, took, note}]}`. `took` is tri-state: `true`/`false` upsert the day's row, `null` un-logs it (deletes any existing row). Validates date within `[server_today−7, server_today+1]` (the +1 absorbs browser-local vs UTC skew), ownership of every protocol id, `took ∈ {true,false,null}` (other types → 400), note ≤500 chars — all before touching the session, so an invalid entry can't partial-write. Single commit, returns `{ok, date, day_status}` where day_status ∈ hollow/amber/green. |
| `/episodes/<id>/delete` | POST | FK-safe (fixed 7/12/26): detaches AI check-in messages referencing the episode (`checkins.episode_id=NULL` — chat history survives) before deleting; SymptomScores/EpisodeInterventions removed by ORM cascade; IntegrityError → rollback + friendly flash. Previously 500'd in production for episodes logged via AI check-in. |
| `/protocols/<id>/delete` | POST | FK-safe hard delete (fixed 7/12/26 after production IntegrityError): removes the protocol's EpisodeIntervention, ProtocolCompliance, and ProtocolEvent rows first, detaches Experiments (`protocol_id=NULL` — hypothesis/outcome history survives; templates guard with `{% if exp.protocol %}`), then deletes the protocol, all in one transaction with IntegrityError rollback. Rescue protocols with historical episode usage are soft-deleted (`status='removed'`) instead — unchanged. Confirm dialog warns that compliance history is permanently deleted. NOTE: local SQLite now enforces FKs (`PRAGMA foreign_keys=ON`, database.py), so delete-path omissions reproduce in dev instead of only failing on production PostgreSQL. |
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

### Workflow
1. Make and test changes locally at http://localhost:5001
2. `git add . && git commit -m "description" && git push`
3. Railway auto-deploys from GitHub main branch
4. Watch Railway dashboard for green deployment

**Important:** Every push to main deploys to production immediately. Real users are on the app. Always test locally before pushing.

### Railway Services
- **baseline** — Flask app, gunicorn, Python 3.13
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
- Account deletion removes all data in FK-safe order: EpisodeInterventions → SymptomScores → EpisodeTriggers → CheckIns → Episodes → ProtocolCompliance → ProtocolEvents → Experiments → Protocols → Symptoms → Triggers (custom only) → UserActivity → InviteCode reference → User
- Data deletion satisfies Washington State My Health MY Data Act (MHMD) requirements
- **Email verification (self-serve registration):** New accounts created with `is_active=False` and `verified_at=None`. Signed itsdangerous token (HMAC over SECRET_KEY, salt `baseline-email-verify-v1`, 24h max_age) emailed as a verification link. On verify, token SHA-256 hash stored in `used_verify_tokens` to prevent replay; user flipped to `is_active=True` with `verified_at=now()`. Welcome email sent post-verification.
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
  - **Known edge (accepted, per owner spec 7/14/26):** clearing *every* protocol flips the button back to "Confirm all as complete", so the main button can't clear a whole day to hollow in one press. Un-logging works when at least one protocol stays marked (the realistic partial case). Fully clearing a day to hollow is done by clearing each protocol individually and Saving while one remains marked, or by un-logging via the protocol-detail form.
- **Completion state:** a fully-resolved save (every protocol marked) collapses the card to "All set for today" / "Logged for today (N not completed)" with a "change something" link that re-expands. A **partial** save (some protocols left blank) keeps the card expanded, sets the chip to "Partly logged", and shows "Saved. The rest are still open for today." — so partial logging is a first-class state, not a dead end.
- **7-day dot strip:** one dot per day, today rightmost with a purple ring. Green = every active protocol that day is logged **and** Complete; amber = logged but not a clean all-complete day (some Not Today **or** some still blank/partial); hollow dashed = nothing logged. Computed by the single `_compute_day_status(user_id, day)` helper, scoped to the protocols active for that day — used by both the server render and the `log_protocol_day` AJAX response so the dot color a save returns can never diverge from what a reload shows. (Changed 7/14/26 from the prior "partial-but-all-taken counts as green" rule, which became misleading once partial logging turned into a routine today state — see Decision Log.) 26px visual inside a ~44px hit area. Tapping a past day opens an inline backfill editor (amber "editing" ring): pills default **neutral** with tap-again-to-clear, partial saves allowed, "Not sure — leave blank" exits without writing. The backfill Save also sends tri-state, so clearing a past entry and saving un-logs it; it has no note field, so it carries each protocol's existing note through unchanged (`bfNote`) rather than blanking a note set via the detail form or AI check-in. Only protocols whose `start_date` was on/before that day are listed (approximation; exact ProtocolEvent replay is a backlog item). Server independently enforces the 7-day window. The dot strip is the day selector: **one editing surface at a time** — opening a backfill day hides the today section and vice versa; tapping today's dot closes any open backfill and opens today's editor directly (one-tap equivalent of "change something"). Implicitly closing an editor with unsaved pill changes shows a quiet "{weekday} wasn't saved" notice.
- **Supportive messaging:** server-side catalog (`SUPPORT_MESSAGES` + `pick_support_message()` in app.py) with deterministic daily rotation; per-protocol missed messages resolve `protocol.why` → active experiment hypothesis → generic copy, pre-interpolated in `index()` and embedded via `tojson` so JS only looks up by scenario. Copy rules: no shame, no streak language; misses framed as honest data. Placeholder filling uses `str.replace` (user text may contain braces).
- **Timezone (interim):** compliance writes use the browser's local date (dashboard sends it in the fetch body; check-in derives it from the existing `client_time` hidden field), validated within ±1 day of server UTC. The card's server-side render also uses the user's local day: base.html stores the browser's IANA zone in a `baseline_tz` cookie (1-year, lax) and `index()` computes the card's "today" via `zoneinfo` — falling back to server UTC on first-ever request or invalid zone. The rest of the dashboard (charts) still uses server date; full per-user timezone support is a backlog item.
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

- Current partial week may not show in dashboard trend charts (under investigation)
- Future episode dates: currently blocked — evaluating whether any use case exists

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
