# Baseline — Product Backlog

Last updated: April 24, 2026 | open self-serve registration

**Priority:** P0 = fix now, P1 = next sprint, P2 = soon, P3 = later
**Size:** S = small (<2hrs), M = medium (half day), L = large (1+ days)

---

## P0 — Fix Now

Privacy/legal foundation items and active bugs. Complete before adding users beyond trusted circle.

| Area | Item | Source | Size | Status |
|---|---|---|---|---|
| Privacy & Legal | Write and publish privacy policy (in app + linked from Settings) | Legal | M | ✅ Complete — /privacy route live, linked from login/register/settings 3/18/26 |
| Privacy & Legal | Build account + data deletion feature in Settings | Legal/MHMD | M | ✅ Complete — /settings/delete-account |
| Auth & Security | Frontend password match validation on registration | Lizz | S | ✅ Complete |
| Auth & Security | Password strength indicator and requirements (register + change password) | Lizz | S | ✅ Complete |
| Data Integrity | Symptom name field: 200 char limit + frontend validation | Lizz | S | ✅ Complete |
| Data Integrity | Description/notes fields: 500 char limit with counter | Lizz | S | ✅ Complete |
| Data Integrity | Enforce unique symptom names per user (case-insensitive) | Lizz | S | ✅ Complete |
| Data Integrity | Prevent future episode dates (frontend + backend) | Lizz | S | ✅ Complete |
| UX | Grey out sidebar nav during onboarding with tooltip | Lizz | S | ✅ Complete |
| Experiments | Lower default experiment duration to 3 weeks | Mackenzie | S | ✅ Complete |
| Experiments | Fix protocol/experiment workflow — inline protocol creation from experiment form, auto-suggest experiment name, active experiment warning modal before submit | Missy | S | ✅ Complete 4/6/26 |
| Data Integrity | Protocol delete 500s on production (IntegrityError — FK children never removed) | Missy smoke test | S | ✅ Complete 7/12/26 — pre-existing bug surfaced by Today's Protocols card writing compliance rows for every active protocol. `delete_protocol` now removes EpisodeIntervention/ProtocolCompliance/ProtocolEvent rows and detaches Experiments (history preserved) before deleting, with IntegrityError rollback. Same hole patched in `/dev/reset` (missing ProtocolEvent). SQLite doesn't enforce FKs, which is why local testing never caught it. |

---

## P1 — Next Sprint

High-value improvements targeting user satisfaction, retention, and portfolio readiness.

| Area | Item | Source | Size | Notes |
|---|---|---|---|---|
| UX | App-wide naming overhaul — rename "Symptoms" to "What I Track", rename "Rescue Medications" to "Interventions", update Protocols description from medication-centric copy to "Ongoing practices, medications, supplements, and routines that support your health". Impacts: nav labels, page headers, onboarding flow, help page, welcome modal, empty states, dashboard headers, CLAUDE.md, README, backlog. | Kiersten/Missy | M | ✅ Complete 4/12/26 |
| Episode Logging | Allow multiple rescue options per episode | Mackenzie | M | ✅ Complete 4/12/26 — EpisodeIntervention junction table, multi-intervention forms, AI prompt updated |
| Tracked Items | Binary (Yes/No) tracked-item type — Spec 01 of 4 | Mackenzie | M | ✅ Complete 6/12/26 — `Symptom.input_type` ('scale'|'binary') with DB CHECK constraint (`symptoms_input_type_check`), `SymptomScore.value_bool` separate column from `score` (filtering `IS NOT NULL` keeps the two types from contaminating each other), type selector on new/edit symptom forms + onboarding step 1, locked once a SymptomScore exists, episode logging renders Yes/No tap targets (tap-again-to-clear, `aria-pressed`, focus-visible outline), baseline-score step skipped for binary in onboarding, AI check-in prompt teaches Claude per-symptom type and accepts boolean values, dashboard binary stat cards (yes/N) + per-episode dot strip, experiment assessment compares yes-rate before/during, episode history shows "Yes" / "No" / "—". |
| Protocol Tracking | Manual compliance logging without AI check-in | Mackenzie | S | ✅ Complete 7/12/26 — dashboard "Today's Protocols" card is the manual path (see row below); per-protocol detail form retained. |
| Protocol Tracking | Rework daily compliance tracking UX (pre-React-rebuild) | Missy | M | ✅ Complete 7/12/26 — dashboard "Today's Protocols" card: assumed-taken pills (dashed style until confirmed), one-tap Confirm writes the whole day via JSON `POST /protocols/log-day`, quiet "Missed all today", optional per-miss notes, calm collapsed state, 7-day tappable dot strip (green/amber/hollow, purple ring today) with inline backfill editor (neutral-default pills, partial saves, guilt-free exit). Supportive messaging personalized from new `Protocol.why` → experiment hypothesis → generic pool. AI check-in reached full parity (records misses + notes, upserts). Designed and iterated in the Claude Design project first. See Decision Log. |
| Protocol Tracking | User timezone support | Missy | M | Railway runs UTC — server `date.today()` rolls over at ~4-5pm Pacific. Interim fix shipped 7/12/26: compliance writes use the browser-local date (±1 day server validation). Full fix: stored/auto-detected user timezone applied to episode timestamps, chart bucketing, and check-in "today". |
| Episode Logging | Structured trigger tracking | Missy | M | Pre-rebuild. Add a first-class trigger field to episode logging (vs. free-text notes) so triggers become a queryable dimension. Unlocks dashboard breakdowns and feeds the P2 AI trigger-analysis item. Decide: user-defined trigger list (like Symptoms) vs. curated global list vs. hybrid — raise trade-offs before building. |
| Help & Onboarding | YouTube feature update videos | Missy | S | Pre-rebuild. Record short feature walkthrough/update videos and surface them in-app (Help page) and in update emails. Lightweight — no app changes required to start; embed once videos exist. |
| Help & Onboarding | Email opt-in for product updates | Missy | S | ✅ Complete 4/24/26 — see "Email & Admin" row below and the Email Opt-In decision-log entry. Settings toggle + unsubscribe link + Resend audience sync + privacy policy update shipped together. |
| Dashboard | Chart time range selector (days / weeks / months) | Mackenzie/Missy | M | Build before React rebuild — understand the requirement fully before rebuilding frontend. |
| Help & Onboarding | Full Help page with Dashboard explanation + check-in tutorial | Internal | M | ✅ Complete 3/21/26 |
| Help & Onboarding | Welcome email for new users | Internal | S | ✅ Complete 3/21/26 — uses Gmail SMTP, requires MAIL_USERNAME and MAIL_PASSWORD env vars in Railway |
| Help & Onboarding | Welcome tour modal | Internal | S | ✅ Complete 3/21/26 — has_seen_tour boolean on User model, 5-step slideshow on first login |
| UX | Dashboard check-in shortcut | Internal | S | ✅ Complete 3/21/26 — "Start Check-in →" button in dashboard header next to Log Episode |
| UX | Update contact email to baselinehealthapp@gmail.com | Internal | S | ✅ Complete 3/21/26 — updated in help page and welcome email |
| UX | Remove invite code reference from help page | Internal | S | ✅ Complete 3/21/26 — registration flow may change |
| Auth & Security | Self-serve registration with email verification | Missy | M | ✅ Complete 4/14/26 — itsdangerous signed tokens (24h TTL), SHA-256 replay protection via `used_verify_tokens`, Flask-Limiter rate limits, disposable-email blocklist, privacy-policy acknowledgment checkbox, stale-account cleanup at 48h. Replaces invite-only registration. |
| UX | Dashboard empty states for new users | Internal | M | ✅ Complete 3/18/26 — per-section empty states with SVG placeholders and action links |
| UX | Experiments page empty state with assessment preview | Internal | S | ✅ Complete 3/18/26 — full two-column assessment preview using real assess-*/decision-* classes at 50% opacity |
| Analytics | Internal event logging to PostgreSQL (privacy-safe instrumentation) | Internal | M | ✅ Complete 4/22/26 — UserActivity model (signup/login/page_view events), admin-only /admin/analytics dashboard with DAU/WAU/feature usage/retention, is_admin flag on User, privacy policy updated. |
| Email & Admin | Email opt-in preference + unsubscribe + Resend contacts sync + admin user view | Missy | M | ✅ Complete 4/24/26 — `email_updates_enabled` on User (default True), Settings → Email Preferences auto-save toggle, public `/unsubscribe/<token>` (HMAC-SHA256, no expiry, enumeration-safe, rate-limited 30/hour), welcome email footer with unsubscribe link, Resend audience sync on verify/email-change/preference-toggle/unsubscribe/account-delete (gated on `RESEND_AUDIENCE_ID`, log-and-swallow), `/admin/users` read-only directory (email/verified/joined/last login/email pref/episode count) guarded by `is_admin`. Privacy policy updated to disclose admin access + Resend as email processor. AI Logging toggle also auto-saves; `ANTHROPIC_API_KEY` dev diagnostic removed from user-facing settings. |
| Portfolio | Prepare repo for public GitHub launch — clean dev routes, write README | Internal | M | ✅ Complete — dev routes cleaned 3/5/26, README written, repo public 3/4/26 |
| Reporting | Neurologist insurance report — auto-generated PDF matching standard migraine calendar form. Day, category (M/H/P), pain score 0-10, medication codes, monthly totals. Required for insurance approval of triptans/gepants. Baseline already captures all needed data. | Missy | L | DEFER TO POST-REACT REBUILD — backend logic transfers cleanly, but PDF generation layer will be cleaner in API-first architecture. Spec the backend now, build properly during React rebuild. |

---

## Deferred to React Rebuild

Items that would create throwaway frontend work if built in the monolith. Build during or after React/React Native rebuild.

| Area | Item | Source | Size | Notes |
|---|---|---|---|---|
| UX | Configurable check-in reminders | Internal | M | Defer — push notification infrastructure is cleaner in React Native with proper notification APIs |
| UX | Light mode | Internal | M | Defer — easier to implement properly in React with CSS variables than retrofit Jinja2 templates |
| Episode Logging | Photo logging | Mackenzie | L | Defer — React Native territory. Do not build in monolith. |

---

## P2 — Soon

Important but not urgent. Build once P0 and P1 are clear.

| Area | Item | Source | Size | Notes |
|---|---|---|---|---|
| Dashboard | Include current partial week in trend charts | Lizz | S | ✅ Resolved — current week now shows with asterisk label |
| AI Features | Trigger analysis: AI surfaces patterns from episode notes on dashboard | Mackenzie | L | Episode notes already capture trigger text |
| Analytics | PostHog self-hosted session recording | Internal | M | After privacy policy live and MHMD review complete |
| Infrastructure | Staging environment on Railway | Internal | M | Separate branch, deploy before main |
| Infrastructure | GitHub Actions: automated doc updates on deploy | Internal | M | Phase 1 of automated documentation pipeline |
| Infrastructure | GitHub Actions: basic automated testing | Internal | M | |
| Privacy | Consult health tech lawyer — Washington MHMD obligations | Legal | S | Before paid tier or significant user growth |
| Privacy & Legal | Update privacy policy to describe transactional email (verification + welcome) and temporary storage of unverified accounts (48h TTL) | Internal | S | ✅ Complete 4/14/26 — `templates/privacy.html` updated (in-app page is single source of truth). |
| Auth & Security | Add CSRF protection (Flask-WTF) to all auth forms | Code Review | S | Pre-existing gap surfaced during self-serve reg review. Rate limiting currently mitigates. With open registration now live, also covers `/settings/email-preferences`, `/settings/email`, `/settings/password`, `/settings/delete-account`, and the JSON `POST /protocols/log-day` compliance endpoint (7/12/26). |
| AI Features | Trend-based supportive messages on the compliance card | Missy | M | Fast-follow to the 7/12/26 compliance card. Surface real data trends ("episodes down N% since starting {protocol}", "logging rate up this month") in the same calm message slot. Rules agreed in design: only when statistically noticeable, tied to a concrete action, no confetti, never a push notification. Start with the two cheap ones (logging-rate delta, longest validated stretch — worded without "streak"). |
| Protocol Tracking | Exact historical protocol status for backfill | Internal | S | Backfill editor currently approximates "active on that day" as `start_date <= day` for currently-active protocols. Exact answer requires replaying ProtocolEvent (paused/stopped/reactivated) — fine to defer at a 7-day window. |
| UX | Unify Taken/Missed pill styling between dashboard card and protocol detail | UX Review | S | Two visual languages for the same action shipped 7/12/26: `.tp-pill` (dashboard, filled color + dashed assumed state) vs `.proto-log-label` radio pills (protocol detail). Restyle the detail-page log form to reuse `.tp-pill`, or document why it stays lightweight. |
| Protocol Tracking | Richer delete protection for preventatives with compliance history — informed confirmation with history count ("47 days of history will be deleted") or soft-delete/archive pattern like rescue interventions | Code Review | S | 7/12/26 — the FK-safe delete fix made silent history loss newly *possible* (previously the delete just crashed). Interim mitigation shipped: confirm dialog states history is permanently deleted. Decision pending with Missy: informed count vs archive. |
| Protocol Tracking | Edit existing compliance entries older than 7 days from the History timeline | Missy | S | 7/12/26 — currently entries older than the dashboard's 7-day backfill window are frozen (History timeline is read-only; detail form is today-only). Add an edit affordance on taken/missed timeline entries opening the Taken/Missed + note form, posting through `_upsert_compliance` with a relaxed date window for rows that already exist. Deliberate boundary kept: correcting an existing record is allowed at any age, but *creating* entries for blank days stays capped at 7 days (memory-based guessing corrupts experiment data — a blank day is honest data). |
| Auth & Security | Switch Flask-Limiter to Redis backend once Railway Redis is provisioned | Code Review | S | In-memory backend resets on each deploy and is per-worker — acceptable at current scale but documented gap. |

---

## P3 — Later

Longer-term vision. Architecture decision point: React rebuild is the gateway to native apps.

| Area | Item | Source | Size | Notes |
|---|---|---|---|---|
| Architecture | React frontend rebuild (API-first, positions for React Native) | Internal | L | Planned post-vacation (April/May 2026) when full time. Deliberate learning project, not just a refactor. |
| Architecture | MCP server layer — expose Baseline as agent-accessible backend | Internal | L | Build during or after React rebuild. API-first architecture makes this natural. See Decision Log. |
| Platform | React Native iOS + Android apps | Internal | L | Follows React rebuild — not a parallel track |
| Platform | Apple Health / HealthKit integration | Mackenzie | L | Requires native iOS app |
| Platform | HRV correlation with episodes | Missy | L | Post-rebuild. Pull HRV from Apple Health and correlate against episode onset/severity — surface as a dashboard signal. Depends on HealthKit integration above. |
| Platform | Blood glucose correlation with episodes | Missy | L | Post-rebuild. Pull blood glucose from Apple Health (incl. CGM sources like Dexcom/Libre that write to Health) and correlate against episode onset/severity. Depends on HealthKit integration above. |
| Monetization | Freemium tier definition and paywall | Internal | L | FTC/MHMD compliance required before paid tier |
| Monetization | Sponsored protocol library with clear labeling | Internal | L | Secondary revenue stream |
| Community | Anonymized aggregate experiment outcomes | Internal | L | |
| Protocol Library | Curated protocol templates by condition area (GF, Keto, FODMAP, Vegan etc.) | Internal | L | |

---

## Deferred — Not Building Now

| Area | Item | Rationale |
|---|---|---|
| UX | Self-serve access request form | Superseded 4/14/26 — replaced by self-serve registration with email verification (no access-request step needed). |

---

## Needs Investigation

| Area | Item | Source | Notes |
|---|---|---|---|
| Episode Logging | Future episode dates — remove or keep with documented reason? | Lizz | Currently blocked — evaluate use case |
| Privacy/Legal | GDPR obligations if non-US users join | Legal | Not immediate — all current users are US-based |

---

## Decision Log

### Protocol Delete — Detach Experiments, FK-Safe Children Cleanup
**July 12, 2026:** Production smoke test after the Today's Protocols deploy surfaced an IntegrityError on protocol delete — `delete_protocol` never removed ProtocolCompliance/ProtocolEvent children or detached Experiments, and PostgreSQL enforces those FKs (SQLite locally does not, so it always passed local testing). Pre-existing bug; the new card made it near-certain to fire because every Confirm now writes compliance rows for all active protocols. Decisions: (1) Experiments referencing a deleted protocol are **detached** (`protocol_id=NULL`), not deleted — hypothesis/outcome/decision history survives; all templates already guard `{% if exp.protocol %}`. Considered blocking the delete (contradicts soft-warnings philosophy) and cascading (destroys the user's science). (2) Compliance/event rows delete with the protocol — they're protocol-scoped logs. The crash had been accidentally protecting this history, so the confirm dialog now explicitly warns it's permanent; a richer guard (history count or archive pattern) is a P2 decision. (3) IntegrityError → rollback + friendly flash for defense in depth. Testing note recorded: delete paths must be reasoned against PostgreSQL FK semantics, not just local SQLite.

### Today's Protocols — Daily Compliance Card, Backfill, "Why" Field & AI Parity
**July 12, 2026:** Shipped the compliance pair (manual logging + UX rework) as one feature, designed first in the Claude Design project ("Baseline Design System" → Compliance groups) and iterated with Missy there before any code. Decisions, each raised with short-term vs long-term trade-offs:
1. **Unique index now, not later** (Missy's call, against the defer recommendation): dedup pass + `CREATE UNIQUE INDEX IF NOT EXISTS ux_protocol_compliance_day` on `protocol_compliance(user_id, protocol_id, date)` in `run_migrations()`, log-and-continue so a failure can't block startup; model-level UniqueConstraint added for fresh DBs. Both statements valid on SQLite and PostgreSQL.
2. **JSON fetch endpoint** (`POST /protocols/log-day`) over form-post: no reload/scroll loss for confirm + backfill, survives the API-first React rebuild, natural shape for the future MCP layer. One endpoint serves both today-confirm and backfill (same operation, different date).
3. **Server-side message catalog, pre-interpolated at render:** single Python source of truth for supportive copy (`SUPPORT_MESSAGES`); the pre-request pill-flip moment reads from JSON embedded in the page. Deterministic daily rotation. `str.replace` interpolation (user text may contain braces). Future trend messages slot into the same catalog.
4. **Folded the old "Active Protocols" dashboard list** into the card ("keep it simple"); "Manage →" link preserves the path.
5. **AI check-in full parity:** schema changed from "list of IDs taken" to `{id, took, note}` objects (legacy bare ints still parsed); writes now upsert via the shared `_upsert_compliance()` helper, so the most recent explicit statement wins in both directions. The AI preserves an existing user note when it has nothing to say about it.
6. **Assumed-taken with distinct styling:** pills default to Taken but in a dashed/soft "our suggestion" style to counter default bias — the user's Confirm converts them to solid recorded fact. Nothing writes until Confirm.
7. **Deliberate asymmetry:** today defaults assumed-taken; backfill days default neutral with tap-to-clear ("a blank day is better data than a guess"). Backfill window is 7 days; dot strip never shows red (fully-missed-but-logged = amber; only the Missed pill itself is red).
8. **Timezone interim fix** (raised by Missy): browser-local date sent with all compliance writes (dashboard fetch body; check-in's existing `client_time` field), server validates ±1 day of UTC today. After QA review flagged the render/write mismatch, the card's server-side render was also localized via a `baseline_tz` cookie (IANA zone set by base.html, read with `zoneinfo` in `index()`, UTC fallback). Charts still use server date. Full user-timezone support added to backlog as P1.
9. **Review-driven hardening (same session):** `_commit_compliance()` retries once on IntegrityError so concurrent confirms can't silently lose a write (QA-reproduced race); AI check-in commit degrades to a "please resend" flash instead of a 500; AI compliance allow-list scoped to `type='preventative'`; malformed JSON entries return 400; note input fixed for iOS auto-zoom (16px) and given an aria-label; today's dot demoted from button to status indicator; quiet text buttons padded to real touch targets; in-flight "Saving…" button states.

### Binary Tracked-Item Storage — Separate Column vs Reused `score`
**June 2026:** For the Yes/No tracked-item type (Spec 01), chose to add a new nullable `value_bool` column on `SymptomScore` rather than overloading the existing `score` column with 0/1. Trade-off considered: reusing `score` would have saved a column add but creates a real footgun — any aggregate that sums or averages `score` without joining `Symptom` to check `input_type` would treat a binary `1` as a scale `1`, silently corrupting trend lines and correlations. With separate columns, every existing scale aggregate keeps working unchanged because `score IS NOT NULL` naturally excludes binary rows, and the same shape extends cleanly to the future categorical/count types the spec calls out. Cost: one nullable column on the entries table and slightly more code on the write path. Worth it. Decision is per-spec, not per-symptom — `Symptom.input_type` remains the single source of truth for which column to read.

### Architecture — Monolith First
**March 2026:** Chose monolith-first (Flask + Jinja2) for MVP speed. PWA added for home screen install — solves "I only use apps" adoption objection without native app cost. React frontend rebuild planned post-vacation (April/May 2026) once product stabilizes at 20-30 users. React Native follows React rebuild naturally. All backend work is reusable regardless of frontend choice.

### Architecture — MCP Server / Agent-Accessible Backend
**March 2026:** The emerging paradigm of "software built for agents not humans" — AI assistants (Claude, ChatGPT) access app functionality on behalf of users rather than users opening apps directly. Decision: build both paths. Keep PWA for current users (non-technical chronic illness patients). Add MCP server layer during/after React rebuild exposing core functionality as agent-callable tools: log episode, add protocol, start experiment, query dashboard data. Rationale: apps without agent access will be invisible to power users within 12-18 months. Mainstream UI replacement is 3-5 years out. React rebuild's API-first architecture positions Baseline for MCP with minimal additional work.

### Analytics
**March 2026:** Deferred third-party analytics (Mixpanel, PostHog cloud) until privacy policy is live and Washington MHMD compliance is understood. Health data + behavioral data sent to third parties requires explicit consent. Interim: internal PostgreSQL event logging. PostHog self-hosted is preferred path when ready.

### Symptom Limit
**March 2026:** 3-symptom limit is an onboarding guardrail only — not enforced app-wide. Protects new users from overwhelm without restricting power users.

### Data Deletion
**March 2026:** Account deletion implemented (/settings/delete-account). Satisfies Washington MHMD right to delete. Requires typing "DELETE" to confirm. Deletes all data in FK-safe order: EpisodeInterventions → SymptomScores → CheckIns → Episodes → ProtocolCompliance → ProtocolEvents → Experiments → Protocols → Symptoms → InviteCode reference → User.

### Boolean Migration Defaults — PostgreSQL Compatibility
**March 2026:** PostgreSQL rejects `DEFAULT 0` / `DEFAULT 1` for BOOLEAN columns (requires `DEFAULT FALSE` / `DEFAULT TRUE`). SQLite accepts both. Fixed all ALTER TABLE migrations in `run_migrations()` to use `TRUE`/`FALSE`. Original bug surfaced when `has_seen_tour` migration ran on production PostgreSQL for the first time.

### Welcome Email — smtplib over Flask-Mail
**March 2026:** Chose Python stdlib `smtplib` over Flask-Mail for welcome emails. No new dependency. Gmail App Password for auth, `smtp.gmail.com:587` with TLS, 10-second timeout. Fails silently if `MAIL_USERNAME`/`MAIL_PASSWORD` not set (local dev). HTML email with plain text fallback, dark theme matching app. Sent on registration after `db.session.commit()`.

### Welcome Modal — Guided Tour
**March 2026:** Added 5-step guided walkthrough modal on first dashboard visit after onboarding. `has_seen_tour` boolean on User model. Auto-marks as seen via JS fetch to `/tour/complete`. Replayable from Help page via `/tour/restart`. No external dependencies — pure CSS/JS modal.

### Build Sequencing — Monolith vs React Rebuild
**March 2026:** Reordered P1 build sequence based on throwaway work risk and data model stability. Key decisions: (1) Naming overhaul must happen first — every feature built on old naming creates rework. (2) Data model changes (multiple rescues, compliance logging) should happen in monolith before React rebuild to avoid mid-migration complexity. (3) Neurologist PDF deferred to post-React rebuild — PDF generation layer will be significantly cleaner in API-first architecture. (4) Reminders, light mode, and photo logging deferred to React/React Native — building these in the monolith creates throwaway frontend work.

### Naming & Market Positioning
**March 2026:** Deliberately positioning Baseline beyond medication management to include health optimization protocols (morning routines, cold plunge, sleep hygiene, dietary approaches). Influenced by Huberman Protocol cultural momentum and user feedback from Kiersten (perimenopausal tracking doesn't fit "symptoms" or "preventative medication" framing). Final naming decisions: Symptoms → "What I Track", Rescue Medications → "Interventions", Protocols description updated to be lifestyle-inclusive. "Episodes" retained — works for both communities.

### Experiment/Protocol Workflow Redesign
**April 2026:** Resolved circular dependency in protocol/experiment creation. Previously, starting an experiment required an existing protocol, but creating a protocol immediately redirected to experiment setup — confusing flow. Fix: (1) Added "+ Add new protocol" option directly in the experiment form's protocol dropdown, with inline fields for protocol name and dose/frequency. (2) Experiment name auto-suggests from protocol name (e.g. "Magnesium 400mg trial") but remains editable. (3) Added active experiment warning modal on the experiment form — fires before submit, matching the existing pattern on the protocol form. Help page updated to lead with experiment-first flow.

### Pre-LinkedIn Launch Requirements
**March 2026:** Identified minimum viable requirements before posting on LinkedIn to 1000+ connections: privacy policy in app (✅ complete), in-app help/tutorial, welcome email, Baseline email address (✅ baselinehealthapp@gmail.com). Invite-only registration maintained during this phase. Self-serve access request deferred until support infrastructure is ready.

### Email Sending — Resend API over Gmail SMTP
**April 14, 2026:** Migrated transactional email from Gmail SMTP (smtplib) to Resend API after verification emails failed silently in production. Root cause: Railway blocks outbound SMTP (errno 101 network unreachable). Resend uses HTTPS, works from Railway. From address hardcoded to `Baseline <hello@mybaselineapp.com>` — requires domain verification in Resend. New env var `RESEND_API_KEY` replaces `MAIL_USERNAME`/`MAIL_PASSWORD`. Local dev still fails silently when key unset. HTML + plain text email content unchanged.

### Email Opt-In, Unsubscribe & Admin User View
**April 24, 2026:** Added `email_updates_enabled` boolean (default True) to User and shipped:
(1) Settings → Email Preferences toggle (auto-saves on change — no Save button needed; mirrors AI Logging toggle behavior).
(2) Welcome email carries an unsubscribe link in its footer; verification email stays purely transactional.
(3) `GET /unsubscribe/<token>` is publicly accessible — token is a stateless HMAC-SHA256 over `SECRET_KEY` (salt `baseline-email-unsubscribe-v1`, no expiry by design — old emails should still work years later). Trade-off: rotating `SECRET_KEY` invalidates all in-flight tokens; affected users must use Settings.
(4) Resend audience contacts kept in sync on verify, settings toggle, email change, unsubscribe, and account delete — gated on `RESEND_AUDIENCE_ID`, log-and-swallow on failure, contacts keyed by email (not stored ID).
(5) Read-only `/admin/users` directory (email, verified, joined, last login, email-updates state, episode count) guarded by `user.is_admin`. Unauthorized access logs a warning. Excluded from analytics tracking.
(6) Admin link surfaced in Settings (above Account, only when `is_admin`) — replaces the previous "only via sidebar" pattern.

Decisions:
- Reused existing `is_admin` flag instead of hardcoded email check on `/admin/users` for consistency with `/admin/analytics`.
- Chose HMAC-SHA256 (per spec) over itsdangerous unsigned-serializer; same security, simpler.
- Kept episode count in admin view despite open registration — single aggregate integer (engagement signal) rather than health content. Privacy policy explicitly discloses admin can see this.
- Removed the `ANTHROPIC_API_KEY` env-var diagnostic from user-facing Settings (was leaking dev info to non-developer users).

Known follow-ups: CSRF on `/settings/email-preferences` rolls into the existing site-wide CSRF backlog item.

### Self-Serve Registration with Email Verification
**April 14, 2026:** Replaced invite-only registration with self-serve registration gated by email verification. Chose email-verification-only (vs. fully open or invite+verify) to support LinkedIn launch without manual invite issuance. Implementation: itsdangerous signed tokens (HMAC over SECRET_KEY, 24h TTL) sent via existing Gmail SMTP pipeline; SHA-256 hash of consumed tokens stored in `used_verify_tokens` to prevent replay (chosen over "verified_at check only" for defense in depth); Flask-Limiter (in-memory) applied to `/register` 5/hour, `/resend-verification` 5/hour, `/login` 20/hour; small hardcoded disposable-email blocklist; required privacy-policy acknowledgment checkbox on the form (server-enforced); stale unverified accounts deleted at startup after 48h so the email can be re-registered. Login leaks no password-validity oracle for unverified accounts (unverified banner shown before bcrypt check). Welcome email moved from registration to post-verification. `InviteCode` model retained for admin use via `/dev/create-invite`. Known follow-ups in backlog: CSRF protection (pre-existing gap), Redis backend for rate limiter. In-app privacy policy (`templates/privacy.html`) updated in the same commit to describe transactional email (verification + welcome), 48-hour unverified-account retention, and the shift away from invite-only.

---

## Sources
- **Lizz** — engineer user, security and data integrity feedback
- **Mackenzie** — stepdaughter, chronic illness user, UX and feature feedback
- **Kiersten** — cousin, user
- **Katherine** — Kiersten's daughter, user
- **Dave** — partner, Android PWA testing
- **Missy** — product owner
- **Internal** — product/engineering decisions
- **Legal** — compliance requirements
