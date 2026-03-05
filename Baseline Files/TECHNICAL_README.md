# Baseline — Technical README

Last updated: March 4, 2026

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
- **Auth:** Flask sessions, bcrypt password hashing, invite-code registration
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
  login.html                  — login page
  register.html               — registration with invite code + password validation
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
  baseline-user-guide.docx    — end user guide
  baseline-privacy-policy.docx — privacy policy
  baseline-vision-roadmap.docx — product vision
```

---

## Data Models

| Model | Description |
|---|---|
| User | email, password_hash, invite_code_used, is_active, onboarding_complete, baseline data, ai_logging_enabled |
| InviteCode | code, created_at, used_at, used_by_user_id |
| Symptom | user-defined trackable symptoms (name, description, is_active). No hard post-onboarding limit. |
| Episode | onset timestamp, duration, functional_impairment, notes |
| SymptomScore | severity score (1-10) per symptom per episode |
| Protocol | name, start_date, dose, frequency, status (preventative) |
| ProtocolCompliance | daily compliance log per protocol |
| RescueOption | rescue medications/interventions |
| Experiment | hypothesis, protocol_id, start_date, stabilization_weeks (default 3), status, outcome |
| CheckIn | AI chat history |

---

## Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key for AI check-in |
| `SECRET_KEY` | Yes | Flask session secret key |
| `DEBUG` | No | `true` locally only, `false` in production |
| `DATABASE_URL` | Production only | Set automatically by Railway PostgreSQL reference |

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
| `/login` | GET, POST | Login |
| `/register` | GET, POST | Register with invite code |
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
| `/experiments` | GET, POST | Experiment tracking |
| `/assess_experiment/<id>` | GET, POST | Experiment assessment |
| `/settings` | GET, POST | User settings |
| `/settings/change-password` | POST | Change password |
| `/settings/change-email` | POST | Change email |
| `/settings/delete-account` | POST | Delete account and all data (MHMD compliance) |
| `/help` | GET | Help and documentation |

### Dev Only (DEBUG=true)
| Route | Description |
|---|---|
| `/dev/reset` | Clear all user data, reset onboarding |
| `/dev/seed` | Populate 12 weeks of test data (only if <20 episodes) |
| `/dev/create-invite` | Generate a new invite code |
| `/dev/bootstrap` | Create admin account on empty database (zero-user check) |

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
- Dev routes blocked in production (DEBUG=false)
- Account deletion removes all data in FK-safe order: SymptomScores → CheckIns → Episodes → ProtocolCompliance → ProtocolEvents → Experiments → Protocols → Symptoms → InviteCode reference → User
- Data deletion satisfies Washington State My Health MY Data Act (MHMD) requirements

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
