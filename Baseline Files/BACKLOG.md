# Baseline — Product Backlog

Last updated: March 21, 2026 | 5 active users

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

---

## P1 — Next Sprint

High-value improvements targeting user satisfaction, retention, and portfolio readiness.

| Area | Item | Source | Size | Notes |
|---|---|---|---|---|
| UX | App-wide naming overhaul — rename "Symptoms" to "What I Track", rename "Rescue Medications" to "Interventions", update Protocols description from medication-centric copy to "Ongoing practices, medications, supplements, and routines that support your health". Impacts: nav labels, page headers, onboarding flow, help page, welcome modal, empty states, dashboard headers, CLAUDE.md, README, backlog. | Kiersten/Missy | M | BUILD FIRST — all subsequent features should use correct naming. Building on old naming creates throwaway work. |
| Episode Logging | Allow multiple rescue options per episode | Mackenzie | M | Build before React rebuild — touches data model, better to get right in monolith. |
| Protocol Tracking | Manual compliance logging without AI check-in | Mackenzie | S | For users who opted out of AI |
| Dashboard | Chart time range selector (days / weeks / months) | Mackenzie/Missy | M | Build before React rebuild — understand the requirement fully before rebuilding frontend. |
| Help & Onboarding | Full Help page with Dashboard explanation + check-in tutorial | Internal | M | ✅ Complete 3/21/26 |
| Help & Onboarding | Welcome email for new users | Internal | S | ✅ Complete 3/21/26 — uses Gmail SMTP, requires MAIL_USERNAME and MAIL_PASSWORD env vars in Railway |
| Help & Onboarding | Welcome tour modal | Internal | S | ✅ Complete 3/21/26 — has_seen_tour boolean on User model, 5-step slideshow on first login |
| UX | Dashboard check-in shortcut | Internal | S | ✅ Complete 3/21/26 — "Start Check-in →" button in dashboard header next to Log Episode |
| UX | Update contact email to baselinehealthapp@gmail.com | Internal | S | ✅ Complete 3/21/26 — updated in help page and welcome email |
| UX | Remove invite code reference from help page | Internal | S | ✅ Complete 3/21/26 — registration flow may change |
| UX | Dashboard empty states for new users | Internal | M | ✅ Complete 3/18/26 — per-section empty states with SVG placeholders and action links |
| UX | Experiments page empty state with assessment preview | Internal | S | ✅ Complete 3/18/26 — full two-column assessment preview using real assess-*/decision-* classes at 50% opacity |
| Analytics | Internal event logging to PostgreSQL (privacy-safe instrumentation) | Internal | M | Consider building into React architecture from the start rather than retrofitting monolith. |
| Portfolio | Prepare repo for public GitHub launch — clean dev routes, write README | Internal | M | ✅ Complete — dev routes cleaned 3/5/26, README written, repo public 3/4/26 |
| Protocol Tracking | Manual compliance editing — ability to go back and adjust protocol compliance for past days. Currently assumed compliant with no way to correct. Wrong compliance data corrupts experiment assessments. UI: weekly calendar view per protocol, tap a day to toggle compliance. | Internal | M | Required before weekly summary email CTA and weekly review are useful. |
| Dashboard | Time-based insight unlock — replace any episode-count-based unlock triggers with time-based confirmation. After 2 weeks of tracking, prompt user to confirm their symptom setup is correct, then unlock baseline charts from existing data. Reframe: insights unlock by confirming accuracy, not by suffering more episodes. | Internal | S | Critical for chronic illness UX — never make users wait for more bad days to see their data. |
| Engagement | Weekly in-app review — a dedicated weekly review screen or modal that surfaces on a regular cadence (Sunday or Monday). Shows the week's assumed data: episodes logged, protocol compliance, symptom averages. User can confirm accuracy or tap to correct individual items inline. Brief summary of how the week compares to baseline. Prompted via dashboard banner ("Your weekly review is ready") not a forced modal. Dismissable. | Internal | M | Makes data review a ritual rather than a task. Manual compliance editing is a dependency. Email CTA should link to this screen. |
| Engagement | Weekly summary email — Sunday digest showing last week's episode count, symptom averages, assumed protocol compliance summary with "Your weekly review is ready" CTA linking to the in-app weekly review screen. Builds on existing email infrastructure. | Internal | M | Manual compliance editing and weekly review screen are dependencies. |
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
| UX | Quick log PWA shortcut — home screen shortcut that opens directly to the check-in (not manual episode form). One tap capture for reactive logging moments. Natural language is lower friction than a form and captures richer data. | Missy | S | PWA supports home screen shortcuts via manifest shortcuts array. |
| Dashboard | Include current partial week in trend charts | Lizz | S | ✅ Resolved — current week now shows with asterisk label |
| AI Features | Trigger analysis: AI surfaces patterns from episode notes on dashboard | Mackenzie | L | Episode notes already capture trigger text |
| Analytics | PostHog self-hosted session recording | Internal | M | After privacy policy live and MHMD review complete |
| Infrastructure | Staging environment on Railway | Internal | M | Separate branch, deploy before main |
| Infrastructure | GitHub Actions: automated doc updates on deploy | Internal | M | Phase 1 of automated documentation pipeline |
| Infrastructure | GitHub Actions: basic automated testing | Internal | M | |
| Privacy | Consult health tech lawyer — Washington MHMD obligations | Legal | S | Before paid tier or significant user growth |

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
| UX | Self-serve access request form | Deferred — meaningful new user growth requires welcome email and in-app tutorial first. Invite-only maintained until those are in place. |

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
**March 2026:** Account deletion implemented (/settings/delete-account). Satisfies Washington MHMD right to delete. Requires typing "DELETE" to confirm. Deletes all data in FK-safe order: SymptomScores → CheckIns → Episodes → ProtocolCompliance → ProtocolEvents → Experiments → Protocols → Symptoms → InviteCode reference → User.

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

### Engagement Model
**March 2026:** Baseline's core retention risk is invisibility on good days — users naturally open the app during episodes, not between them. Decided against streaks — too punishing for chronic illness users who have multi-day episodes. Core engagement strategy: (1) Assumed compliance with easy correction via manual compliance editing and weekly in-app review. (2) Time-based insight unlocks — insights unlock after 2 weeks by confirming data accuracy, never by waiting for more episodes. (3) Weekly in-app review as primary engagement ritual — Sunday/Monday prompt to confirm weekly data, correct compliance, and see how the week compared to baseline. (4) Weekly summary email as secondary re-engagement mechanism linking back to the in-app review. (5) Quick log PWA shortcut routes to check-in not manual form — natural language is lower friction. Weekly review and email both depend on manual compliance editing being built first.

### Pre-LinkedIn Launch Requirements
**March 2026:** Identified minimum viable requirements before posting on LinkedIn to 1000+ connections: privacy policy in app (✅ complete), in-app help/tutorial, welcome email, Baseline email address (✅ baselinehealthapp@gmail.com). Invite-only registration maintained during this phase. Self-serve access request deferred until support infrastructure is ready.

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
