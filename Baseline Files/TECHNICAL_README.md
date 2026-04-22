# Baseline — Technical README

Last updated: April 22, 2026

---

## Overview

Baseline is a web application for tracking health protocols and experiments for people managing chronic conditions. Built with Python/Flask, deployed on Railway, using PostgreSQL in production and the Anthropic API for AI-powered daily check-ins.

Live at: **https://baseline-health.up.railway.app**

---

## Tech Stack

- **Backend:** Python 3.10, Flask
- **Database:** SQLAlchemy ORM — PostgreSQL (production), SQLite (local dev)
- **Frontend:** Jinja2 templates, vanilla JavaScript, Chart.js
- **AI:** Anthropic API (claude-sonnet-4-6) for check-in parsing
- **Auth:** Flask sessions, bcrypt password hashing, self-serve registration with email verification (itsdangerous signed tokens, 24h TTL), Flask-Limiter rate limiting
- **Hosting:** Railway (auto-deploys from GitHub main branch)
- **PWA:** manifest.json, service worker, home screen icons (Pillow-generated)

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
| User | email, password_hash, invite_code_used (legacy), is_active, verified_at, onboarding_complete, baseline data, ai_logging_enabled, has_seen_tour, is_admin |
| InviteCode | code, created_at, used_at, used_by_user_id (legacy — admin use only) |
| UsedVerifyToken | token_hash (SHA-256), used_at — prevents email-verification token replay |
| Symptom | user-defined trackable items (name, description, is_active). Displayed as "What I Track" in UI. No hard post-onboarding limit. |
| Episode | onset timestamp, duration, functional_impairment, notes |
| SymptomScore | severity score (1-10) per symptom per episode |
| EpisodeIntervention | junction table: episode_id, protocol_id (rescue), effectiveness (1-10), time_to_relief_hours. Supports multiple interventions per episode. |
| Protocol | name, start_date, dose, frequency, status (preventative) |
| ProtocolCompliance | daily compliance log per protocol |
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
| `/settings/change-email` | POST | Change email |
| `/settings/delete-account` | POST | Delete account and all data (MHMD compliance) |
| `/help` | GET | Help and documentation |
| `/admin/analytics` | GET | Admin-only usage analytics dashboard (signups, logins, DAU/WAU, feature usage, retention) |
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
- Account deletion removes all data in FK-safe order: EpisodeInterventions → SymptomScores → CheckIns → Episodes → ProtocolCompliance → ProtocolEvents → Experiments → Protocols → Symptoms → UserActivity → InviteCode reference → User
- Data deletion satisfies Washington State My Health MY Data Act (MHMD) requirements
- **Email verification (self-serve registration):** New accounts created with `is_active=False` and `verified_at=None`. Signed itsdangerous token (HMAC over SECRET_KEY, salt `baseline-email-verify-v1`, 24h max_age) emailed as a verification link. On verify, token SHA-256 hash stored in `used_verify_tokens` to prevent replay; user flipped to `is_active=True` with `verified_at=now()`. Welcome email sent post-verification.
- **Rate limiting:** Flask-Limiter (in-memory backend). `/register` and `/resend-verification` 5/hour/IP; `/login` 20/hour/IP.
- **Disposable-email blocklist:** common throwaway domains rejected at `/register`.
- **Privacy acknowledgment:** `/register` requires a checkbox acknowledging the Privacy Policy (server-enforced).
- **Stale unverified cleanup:** accounts with `verified_at IS NULL` and `created_at` older than 48 hours are deleted at app startup, freeing the email for re-registration.
- Welcome email sent on successful email verification via Resend API (`resend` package). From address `Baseline <hello@mybaselineapp.com>` (domain verified in Resend). Fails silently if `RESEND_API_KEY` not configured. HTML + plain text. Previously used Gmail SMTP — retired because Railway blocks outbound SMTP (errno 101 network unreachable).
- Welcome tour modal shown on first dashboard visit after onboarding (`has_seen_tour` flag on User model). Replayable from Help page via `/tour/restart`.

---

## Dashboard

The dashboard header contains two primary action buttons: "Start Check-in →" (links to `/checkin`) and "+ Log Episode" (links to `/episodes/new`), giving users immediate access to the two main logging paths without navigating the sidebar.

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
