# Baseline — Claude Code Context Document

Last updated: April 24, 2026

This file gives Claude Code persistent context about the Baseline project. Read it at the start of every session before making any changes.

---

## What is Baseline?

Baseline is a personal health tracking web application for people managing chronic conditions. The core differentiator is structured experiment tracking — users establish a baseline, introduce one protocol change at a time, and assess outcomes with real data. The philosophy: give chronic illness sufferers the tools to be scientists of their own health.

Live at: https://mybaselineapp.com (custom domain; Railway default: baseline-health.up.railway.app)

---

## Current Status

- Open self-serve registration. Core feedback circle: Missy (admin/developer), Mackenzie (stepdaughter, chronic illness), Lizz (software engineer), Kiersten (cousin), Katherine (Kiersten's daughter). Broader user base referenced as `current_user` in code and visible via `/admin/users`.
- MVP is live and deployed on Railway
- PostgreSQL database in production, SQLite locally
- PWA installed — works as home screen app on iOS (Safari) and Android (Chrome)

---

## Tech Stack

- **Backend:** Python 3.10, Flask
- **Database:** SQLAlchemy ORM — PostgreSQL in production, SQLite locally
- **Frontend:** Jinja2 templates, vanilla JavaScript, Chart.js
- **AI:** Anthropic API (claude-sonnet-4-6) for check-in parsing
- **Hosting:** Railway (auto-deploys from GitHub main branch), custom domain via Cloudflare DNS
- **Auth:** Flask sessions, bcrypt password hashing, self-serve registration with email verification (itsdangerous signed tokens, 24h TTL, SHA-256 replay protection), Flask-Limiter rate limiting
- **PWA:** manifest.json, service worker, home screen icons

---

## Project Structure

```
app.py                  — all routes and business logic
database.py             — SQLAlchemy models
requirements.txt        — Python dependencies
Procfile                — gunicorn for production (web: gunicorn app:app)
run.sh                  — local startup script
.env                    — environment variables (not committed)
generate_icons.py       — PWA icon generation script
static/
  css/style.css         — all styles, dark theme
  icons/                — PWA app icons (192, 512, apple-touch)
  manifest.json         — PWA manifest
  sw.js                 — service worker
templates/
  base.html             — base template with nav, PWA meta tags
  index.html            — dashboard with charts
  login.html            — login page
  register.html         — registration with invite code
  offline.html          — PWA offline fallback
  [other templates]     — episodes, protocols, experiments, symptoms, settings, help
Baseline Files/
  baseline-technical-readme.docx
  baseline-vision-roadmap.docx
  baseline-backlog.docx
```

---

## Data Models

- **User** — email, password_hash, invite_code_used (legacy), is_active, verified_at, onboarding_complete, baseline data, ai_logging_enabled, has_seen_tour, is_admin, email_updates_enabled
- **InviteCode** — code, created_at, used_at, used_by_user_id (legacy — admin use only via /dev/create-invite)
- **UsedVerifyToken** — token_hash (SHA-256), used_at. Prevents email-verification token replay.
- **Symptom** — user-defined trackable items (name, description, is_active, input_type). Displayed as "What I Track" in UI. No hard limit on count post-onboarding. `input_type` is `'scale'` (1–10 slider) or `'binary'` (Yes/No). Type is locked once any SymptomScore for the symptom exists.
- **Episode** — health episodes with onset timestamp, duration, functional_impairment, notes
- **SymptomScore** — per-episode entry. For scale symptoms `score` (1–10) is set and `value_bool` is null; for binary symptoms `value_bool` is set and `score` is null. Aggregations must filter `score IS NOT NULL` (scale) or `value_bool IS NOT NULL` (binary) so the two types never mix.
- **EpisodeIntervention** — junction table linking episodes to interventions (Protocol type='rescue') with per-intervention effectiveness (1-10) and time_to_relief_hours
- **Trigger** — episode trigger dimension (hybrid model). `user_id` nullable: **NULL = curated global seed** (shared, admin-curated, capped at 12 via `SEED_TRIGGERS`; editing that list adds new globals on next deploy but never renames/removes existing ones — globals are soft-retired via `is_active`); non-null = a user's custom trigger. `name` (100), `is_active`, `created_at`. Uniqueness enforced by two DB **partial** unique indexes (`ux_trigger_global_name` = unique `lower(name)` where `user_id IS NULL`; `ux_trigger_user_name` = unique `(user_id, lower(name))` where `user_id IS NOT NULL`) — a plain composite index wouldn't constrain globals since NULLs compare distinct. Custom "deletion" will be a soft-deactivate (`is_active=False`), never hard-delete, so linked history survives. **As of the data-model pass, no code reads/writes triggers yet** — the write helper (match-and-link), episode-form picker + inline "+add", Settings "Manage triggers", and AI check-in parity are the next increment.
- **EpisodeTrigger** — junction linking an episode to a Trigger (global or custom); unique `(episode_id, trigger_id)`. Episode-side `cascade='all, delete-orphan'` (same pattern as EpisodeIntervention), so `db.session.delete(episode)` cleans up links.
- **Protocol** — preventative protocols with name, start_date, dose, frequency, status, and optional `why` ("Why I'm doing this" — powers personalized compliance messaging)
- **ProtocolCompliance** — daily compliance log per protocol. One row per (user, protocol, day), enforced by unique index `ux_protocol_compliance_day`. All writers (dashboard "Today's Protocols" batch confirm via `POST /protocols/log-day`, protocol-detail form, AI check-in) share `_upsert_compliance()` — most recent explicit statement wins. The dashboard card treats each protocol as tri-state (Complete / Not Today / **blank**): `POST /protocols/log-day` accepts `took` = `true`/`false`/`null`, where `null` un-logs the day (routes through `_delete_compliance()` — deletes the row so the protocol reads unmarked again). The confirm button is **"Confirm all as complete"** when nothing is explicitly marked (one-tap assumed-compliance: every blank is written taken) and **"Save"** once anything is marked or cleared (writes only what's set; blanks are sent as `null` to leave/return them unmarked). "Assumed compliance" is now expressed by that all-blank button, not by a written row — nothing is saved until the user acts. Dashboard card supports 7-day backfill (its Save also sends tri-state, so clearing a past entry removes it). All day-boundary date logic resolves the user's local day through the single-source-of-truth `user_today()` helper, which `unquote()`s the `baseline_tz` cookie before `zoneinfo` — base.html writes it URL-encoded and Flask returns cookie values un-decoded, so before 7/14/26 `ZoneInfo` always raised and the render silently fell back to UTC (evening entries showed a day off). Falls back to server UTC only when the cookie is absent/unparseable; ±1 day server window on writes.
- **RescueOption** — interventions (displayed as "Interventions" in UI; stored as Protocol with type='rescue')
- **Experiment** — hypothesis, protocol_id, start_date, stabilization_weeks (default 3), status, outcome
- **CheckIn** — AI chat history
- **UserActivity** — first-party usage analytics: user_id (nullable FK), event_type (signup/login/page_view), detail (endpoint name), created_at. Admin-only dashboard at /admin/analytics.

---

## Environment Variables

Required in .env locally and in Railway variables in production:
- `ANTHROPIC_API_KEY` — Anthropic API key for AI check-in
- `SECRET_KEY` — Flask session secret key
- `DEBUG` — set to `true` locally only, `false` in production
- `DATABASE_URL` — set automatically by Railway from PostgreSQL service reference
- `APP_URL` — base URL for email links (production: `https://mybaselineapp.com`)
- `RESEND_API_KEY` — Resend API key for transactional email (verification + welcome). From address is `Baseline <hello@mybaselineapp.com>`. Unset locally → email sends fail silently.
- `RESEND_AUDIENCE_ID` — Resend audience UUID for contact-list sync. Verify/unsubscribe/email-change/account-delete events upsert or remove the user's contact carrying their current `email_updates_enabled` state. Unset locally → all sync calls are no-ops.
- `BACKFILL_RESEND_CONTACTS` — set to `1` for a single deploy to upsert every active verified user into the Resend audience at startup, then unset. Idempotent.
- `ADMIN_EMAIL` — email address to grant admin access on startup (defaults to `daniels.missy@gmail.com`)

---

## Architecture Rules (Non-Negotiable)

Two distinct failure modes — keep them separate. **Rule 1 is about *where state lives*** (scalability); **Rule 2 is about *when slow work runs*** (responsiveness). They are not the same thing, and a naive fix for one can break the other (see the interaction note under Rule 2).

### Rule 1 — The Backend Stays Stateless

**Rule (non-negotiable):** The backend must remain stateless. Any server instance must be able to handle any request. No server may hold information in its own memory or local disk that another server wouldn't have access to. This is what lets us add servers later without a rewrite — do not break it.

**This means, including but not limited to:**
- User sessions live in the database or a shared session store (or a signed client-side cookie), never in server memory.
- Uploaded files (e.g. episode/trigger photos) go to dedicated object/file storage, never the server's local disk.
- No other per-server state: in-memory caches or counters held across requests, local temp files held across requests, background schedulers pinned to one instance.

**The test for any new code:** If this change would make one server remember something another server doesn't know about, it violates the rule. Stop and flag it instead of writing it. These examples are not exhaustive — when in doubt, treat new per-server state as a violation and surface it for review.

**Known deviation** (latent at single-instance today): Flask-Limiter uses an in-memory backend (per-worker, resets on deploy) — the P2 "switch Flask-Limiter to Redis" item is the fix. Sessions are already compliant (Flask signed client-side cookies, no server memory).

### Rule 2 — Slow Work Belongs Off the Request Path

**Rule (non-negotiable):** Slow or blocking work must not run synchronously inside the HTTP request when the caller doesn't need the result in that same response. A blocking call ties up a request worker for its whole duration, so under load this causes latency, request timeouts, and worker exhaustion. This is a *throughput/responsiveness* failure mode — **distinct from Rule 1, and it leaves no per-server state.**

**This means, including but not limited to:**
- Fire-and-forget work (email sends, notifications) → a background job queue; the request returns immediately.
- Batch work with nobody waiting (e.g. AI trigger-pattern analysis) → a background worker.
- Interactive slow work where the user *is* waiting for the result (e.g. AI check-in parsing) → an async job pattern (submit → return a job id → client polls or streams → render when ready), not a synchronous in-request block.

**The test for any new code:** If a change adds a slow or external/blocking call (email, PDF, AI/LLM, image processing, third-party HTTP) inside a request handler, ask whether the caller needs the result in that response. If not, defer it. If yes but it's slow, use the async-job pattern.

**Interaction with Rule 1 (important):** the mechanism you defer work *to* must itself be stateless — use a shared queue/worker (e.g. Redis + RQ/Celery), not an in-process thread or a scheduler pinned to one instance. An in-process deferral would satisfy Rule 2 but *violate Rule 1*.

**Known deviations** (latent at single-instance/low load): (1) transactional email (verification/welcome) runs in-request — P2 "provision Redis + move email to a background job" is the fix; (2) AI check-in runs the Anthropic call in-request — durable fix is the async-job pattern, a natural fit for the React rebuild. Both are throughput items, not per-server state.

### Rule 3 — Converge, Don't Diverge

**Rule:** When you add or change a feature or CRUD action, match the pattern of the **nearest existing sibling** — shared helpers, date/timezone handling, validation, delete-safety, user-scoping, error handling, and response shape. A new one-off pattern for something the codebase already does is a defect in waiting; introduce a divergent approach only with a stated justification, raised as a decision. The canonical patterns live in **`Baseline Files/CONVENTIONS.md`** — read it before building, and update it when a new pattern is genuinely established.

**The test for any new code:** "Does the codebase already do this somewhere, and does my version match it?" If the same kind of thing is done three different ways (three ways to resolve "today," three delete patterns, three validation styles), *that* is the bug — converge. This rule exists because approach-drift across areas is invisible to a non-coding owner and only surfaces as production inconsistency (the 7/14 timezone bug was exactly this: "today" resolved three different ways).

---

## Key Architectural Decisions

**Monolith-first:** Flask serves HTML via Jinja2 templates. This was the right call for MVP speed. A React frontend rebuild is planned post-vacation (April/May 2026) once the product stabilizes at 20-30 users. The React rebuild is a deliberate learning project, not just a refactor.

**Database:** SQLite locally (no setup required), PostgreSQL in production. The DATABASE_URL environment variable controls which is used. The postgres:// → postgresql:// rewrite is handled in app.py.

**Auth:** Self-serve registration with email verification. Flow: `/register` creates inactive user + sends signed itsdangerous token (24h TTL) → user clicks `/verify/<token>` → `verified_at` set, `is_active=True`, welcome email sent, logged in. Used tokens hashed into `used_verify_tokens` to block replay. Unverified accounts deleted after 48h by `cleanup_stale_unverified_users()` at startup. Flask-Limiter: 5/hour register + resend, 20/hour login. Disposable-email blocklist. Privacy policy acknowledgment required (server-enforced). `InviteCode` + `/dev/create-invite` retained for admin manual onboarding.

**AI check-in:** Opt-in. Uses load_dotenv(override=True) to ensure .env always wins over shell environment. Returns structured JSON parsed into episode/compliance records.

**Email opt-in / unsubscribe:** `email_updates_enabled` defaults to True. Settings → Email Preferences toggle auto-saves on change (form submit on toggle change, flash banner confirms). Welcome email carries an unsubscribe link with a stateless HMAC-SHA256 token over `SECRET_KEY` (no expiry — old emails should still work). Verification email is purely transactional and has no unsubscribe link. `/unsubscribe/<token>` is in `PUBLIC_ENDPOINTS`, rate-limited 30/hour, enumeration-safe (invalid token, tampered token, and unknown email all return the same generic error). Resend audience kept in sync on verify, settings toggle, email change, unsubscribe, and account delete — log-and-swallow on failure, never blocks user flows. Rotating `SECRET_KEY` invalidates all in-flight unsubscribe tokens.

**Admin views:** `/admin/analytics` (existing) and `/admin/users` (read-only user directory: email, verified, joined, last login, email_updates_enabled, episode count). Both guarded by `user.is_admin`. Unauthorized access on `/admin/users` logs a warning. Both excluded from `SKIP_TRACKING` so admin browsing doesn't pollute analytics. The `is_admin` flag is set on startup from `ADMIN_EMAIL` env var.

**PWA:** Fully implemented. Icons are generated by generate_icons.py using Pillow. Manifest and service worker are in static/.

---

## Dev Routes (debug mode only)

- `/dev/reset` — clears all user data, resets onboarding
- `/dev/seed` — populates 12 weeks of test data (only if <20 episodes exist)
- `/dev/create-invite` — generates a new invite code
- `/dev/bootstrap` — creates admin account on empty database (safe: checks for zero users first)

---

## Deployment Workflow

1. Make changes locally
2. Test at http://localhost:5001
3. `git add . && git commit -m "description" && git push`
4. Railway auto-deploys from GitHub main branch
5. Watch Railway for green deployment

**Important:** Every push to main deploys to production immediately. Real users are on the app. Always test locally before pushing.

---

## Known Issues / Active Investigation

- None currently open. (Partial-week chart display — resolved; the current week now shows with an asterisk label. Future-episode-dates question — reviewed 7/14/26: keeping them blocked, allowing future-dated episodes isn't needed, no change required.)

---

## Product Philosophy

- **Keep it simple** — Mackenzie's first feedback was "loving it, keep it simple." Resist feature bloat.
- **Soft warnings not hard blocks** — users have agency. Surface information, let them decide.
- **Assumed compliance** — the app assumes users follow their protocols and captures exceptions via check-in.
- **Condition-agnostic** — no condition field. Symptoms and protocols are the meaningful units.
- **Privacy-first** — health data. No third-party analytics until privacy policy is live and Washington MHMD compliance is understood. No instrumentation that sends health content to third parties without explicit user consent.
- **Learning project** — Baseline is intentionally a learning project. All architecture and design decisions must be raised to Missy with a short-term vs long-term trade-off analysis before proceeding. When a decision point arises (e.g., "should we add an index?", "should this be a separate table?"), present: (1) what the options are, (2) short-term trade-off (speed, simplicity, what works now), (3) long-term trade-off (scalability, maintainability, what matters at 50+ users or during React rebuild), and (4) a recommendation.

---

## Documentation Files

Two markdown files live in `Baseline Files/` and must be updated directly as part of every commit that affects them. Do not wait for a separate documentation step — update them in the same session and include them in the same commit as the code changes.

### Files you update directly (markdown):

**`Baseline Files/TECHNICAL_README.md`** — update when:
- New routes added or removed
- Data models change
- Dependencies change
- Environment variables change
- Deployment process changes
- Security notes need updating

**`Baseline Files/BACKLOG.md`** — update when:
- Items are completed (mark ✅ Complete with date)
- New bugs or features are identified during a build session
- Priorities change
- New decisions are made (add to Decision Log)

**`Baseline Files/CONVENTIONS.md`** — the canonical-patterns reference (Rule 3). Read it before building; update it when a genuinely new canonical pattern is established (e.g. a new shared helper, a standard way to do a class of CRUD action).

### In-app help page:

**`templates/help.html`** — the single source of truth for user-facing documentation. Update when user-facing features, workflows, or terminology change. The user guide `.docx` was retired — all user docs live in the help page now.

**`templates/privacy.html`** — the single source of truth for the privacy policy. Update directly when registration/email/data-handling changes. Changes with legal or MHMD implications must be raised to Missy for approval before committing.

### Files you do NOT edit directly (.docx):

**`Baseline Files/baseline-vision-roadmap.docx`** — Note significant product direction changes for Missy to update.

### Session end checklist:
1. Update TECHNICAL_README.md and BACKLOG.md directly
2. Update help.html if user-facing features or workflows changed
3. Include all in the commit with the code changes
4. Note what needs updating in the .docx files for Missy
5. Confirm git push completed

---

## Post-Implementation Workflow (Mandatory)

After every code change, Claude Code must follow this 4-phase workflow automatically. Do not skip phases. Do not wait for Missy to ask.

### Phase 1 — Implementation

1. Build the requested feature or fix
2. Verify the app loads locally without errors (`python app.py` or check for import/syntax issues)
3. Confirm the change works as intended (happy path)
4. **Adversarial verification** — users rarely follow happy paths. Drive the real UI in the browser and actively try to break the change: do steps out of order (edit after confirm, open B while A is mid-edit), interrupt mid-flow (navigate away, back button, reload with unsaved state), repeat actions (double-tap, re-open after close), revisit completed states, and interleave every entry point that writes the same data (form vs. AI check-in vs. dashboard). For anything that writes new rows referencing existing tables, exercise the *delete paths of the parent records* — local SQLite now enforces FKs (`PRAGMA foreign_keys=ON` in database.py) so these failures reproduce in dev.

### Phase 2 — Parallel Review

Launch review agents in parallel using the Task tool with `subagent_type: "general-purpose"` and `model: "sonnet"`:

**Always launch (2 agents):**

1. **QA Tester** — Prompt: "You are the QA Tester for Baseline. Read `.claude/agents/qa-tester.md` for your full instructions. Review the following changes: [describe what changed and which files]. Run your checklist and return your report."

2. **Senior Code Reviewer** — Prompt: "You are the Senior Code Reviewer for Baseline. Read `.claude/agents/code-reviewer.md` for your full instructions. Review the following changes: [describe what changed and which files]. Run your checklist and return your report."

**Conditionally launch (1 agent, only when templates or CSS changed):**

3. **UX Reviewer** — Prompt: "You are the UX Reviewer for Baseline. Read `.claude/agents/ux-reviewer.md` for your full instructions. Review the following template/CSS changes: [describe what changed and which files]. Run your checklist and return your report."

All agents run in parallel. Wait for all to complete before proceeding.

**Prompt framing:** every review prompt must (a) describe what changed and what it is *supposed* to do, and (b) explicitly instruct the agent to **attempt to falsify** that description — hunt for inputs, sequences, and states where it breaks — and to widen from the diff to its blast radius (unchanged code whose behavior shifts because of new data or state the change writes).

### Phase 3 — Fix & Document

1. **Present findings** — Show Missy a summary of all agent reports (blockers, warnings, architecture decisions)
2. **Fix blockers** — Address any BLOCKER items from QA and Code Review. Re-run affected agents if fixes are significant.
   - **Triage rule for "pre-existing" findings:** a finding marked pre-existing or out-of-scope that touches a surface *this change makes more prominent or more frequently used* is promoted to an explicit fix-now-vs-accept decision in the Deploy Gate summary — never a footnote. (Lesson from 7/12/26: a QA note about stacked editing surfaces was parked as "pre-existing"; the owner hit it in production hours later.)
3. **Address architecture decisions** — If the Code Reviewer flagged architecture decisions, present them to Missy with trade-off analysis before proceeding.
4. **Update documentation** — Reference `.claude/agents/doc-updater.md` checklist and apply updates directly:
   - Update `CLAUDE.md` if project context changed
   - Update `Baseline Files/TECHNICAL_README.md` if technical details changed
   - Update `Baseline Files/BACKLOG.md` if items were completed or discovered
   - Flag any `.docx` files that need Missy's attention

### Phase 4 — Deploy Gate

Present a deployment summary and **ask Missy for approval** before committing and pushing:

```
## Deployment Summary

### Changes
- [list of what was implemented]

### Review Results
- QA: X blockers, X warnings (all blockers resolved)
- Code Review: X blockers, X warnings (all blockers resolved)
- UX Review: X blockers, X warnings (or "not triggered — no template/CSS changes")

### Documentation Updated
- [list of docs updated]
- [list of .docx files flagged for Missy]

### Ready to deploy?
This will commit and push to main, triggering auto-deploy to production.
```

**Do not commit or push without Missy's explicit approval.**

---

## Contact / Owner

Missy Daniels — daniels.missy@gmail.com
GitHub: github.com/missydaniels-pm/baseline
