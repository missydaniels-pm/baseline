# Baseline — Product Backlog

Last updated: April 23, 2026 | 5 active users

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

---

## P1 — Next Sprint

High-value improvements targeting user satisfaction, retention, and portfolio readiness.

| Area | Item | Source | Size | Notes |
|---|---|---|---|---|
| UX | App-wide naming overhaul — rename "Symptoms" to "What I Track", rename "Rescue Medications" to "Interventions", update Protocols description from medication-centric copy to "Ongoing practices, medications, supplements, and routines that support your health". Impacts: nav labels, page headers, onboarding flow, help page, welcome modal, empty states, dashboard headers, CLAUDE.md, README, backlog. | Kiersten/Missy | M | ✅ Complete 4/12/26 |
| Episode Logging | Allow multiple rescue options per episode | Mackenzie | M | ✅ Complete 4/12/26 — EpisodeIntervention junction table, multi-intervention forms, AI prompt updated |
| Protocol Tracking | Manual compliance logging without AI check-in | Mackenzie | S | For users who opted out of AI |
| Protocol Tracking | Rework daily compliance tracking UX (pre-React-rebuild) | Missy | M | Current daily tracking is too subtle — unclear whether the app is assuming compliance or waiting for input, and it isn't driving daily usage. Make the assumed-compliance model legible (e.g. visible "assumed taken" state with one-tap exception logging), surface today's protocols on the dashboard, and tie into the dashboard check-in shortcut. Build in monolith before React rebuild so the interaction model is settled before frontend rewrite. Related to "Manual compliance logging without AI check-in" above — consider scoping together. |
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
| Auth & Security | Add CSRF protection (Flask-WTF) to all auth forms | Code Review | S | Pre-existing gap surfaced during self-serve reg review. Rate limiting currently mitigates. |
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
