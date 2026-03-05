# Baseline — Product Backlog

Last updated: March 5, 2026 | 4 active users

**Priority:** P0 = fix now, P1 = next sprint, P2 = soon, P3 = later
**Size:** S = small (<2hrs), M = medium (half day), L = large (1+ days)

---

## P0 — Fix Now

Privacy/legal foundation items and active bugs. Complete before adding users beyond trusted circle.

| Area | Item | Source | Size | Status |
|---|---|---|---|---|
| Privacy & Legal | Write and publish privacy policy (in app + linked from Settings) | Legal | M | Draft complete — needs publishing in app |
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
| Episode Logging | Allow multiple rescue options per episode | Mackenzie | M | Current model allows only one rescue per episode |
| Protocol Tracking | Manual compliance logging without AI check-in | Mackenzie | S | For users who opted out of AI |
| Help & Onboarding | Full Help page with Dashboard explanation | Internal | M | Include check-in tutorial per Mackenzie |
| Dashboard | Chart time range selector (days / weeks / months) | Mackenzie/Missy | M | Users should control the time window |
| Reporting | Neurologist insurance report — auto-generated PDF matching standard migraine calendar form. Day, category (M/H/P), pain score 0-10, medication codes, monthly totals. Required for insurance approval of triptans/gepants. Baseline already captures all needed data. | Missy | L | Reference form photographed 3/4/26 |
| Analytics | Internal event logging to PostgreSQL (privacy-safe instrumentation) | Internal | M | No third-party tools until privacy policy live + MHMD review |
| Portfolio | Prepare repo for public GitHub launch — clean dev routes, write README | Internal | M | In progress — dev routes cleaned up 3/5/26 |

---

## P2 — Soon

Important but not urgent. Build once P0 and P1 are clear.

| Area | Item | Source | Size | Notes |
|---|---|---|---|---|
| Dashboard | Include current partial week in trend charts | Lizz | S | Under investigation — may be intentional |
| AI Features | Trigger analysis: AI surfaces patterns from episode notes on dashboard | Mackenzie | L | Episode notes already capture trigger text |
| Episode Logging | Photo logging — low-friction capture when too unwell to type. Snap photo of possible trigger as placeholder to review later. Future: AI analysis to suggest trigger. | Mackenzie | L | Must be minimum taps. Fastest possible interaction. |
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
| Architecture | React frontend rebuild (API-first, positions for React Native) | Internal | L | Planned for post-vacation when full time. Deliberate learning project. |
| Platform | React Native iOS + Android apps | Internal | L | Follows React rebuild — not a parallel track |
| Platform | Apple Health / HealthKit integration | Mackenzie | L | Requires native iOS app |
| Monetization | Freemium tier definition and paywall | Internal | L | HIPAA review not needed but FTC/MHMD compliance required before paid tier |
| Monetization | Sponsored protocol library with clear labeling | Internal | L | Secondary revenue stream |
| Community | Anonymized aggregate experiment outcomes | Internal | L | |
| Protocol Library | Curated protocol templates by condition area (GF, Keto, FODMAP etc.) | Internal | L | |

---

## Needs Investigation

| Area | Item | Source | Notes |
|---|---|---|---|
| Dashboard | Current week chart behavior — intentional or bug? | Lizz | If intentional, label partial week clearly |
| Episode Logging | Future episode dates — remove or keep with documented reason? | Lizz | Currently blocked |
| Privacy/Legal | GDPR obligations if non-US users join | Legal | Not immediate — all current users are US-based |

---

## Decision Log

### Architecture
**March 2026:** Chose monolith-first (Flask + Jinja2) for MVP speed. PWA added for home screen install — solves "I only use apps" adoption objection without native app cost. React frontend rebuild planned post-vacation (April/May 2026) once product stabilizes at 20-30 users. React Native follows React rebuild naturally. All backend work is reusable regardless of frontend choice.

### Analytics
**March 2026:** Deferred third-party analytics (Mixpanel, PostHog cloud) until privacy policy is live and Washington MHMD compliance is understood. Collecting health data means behavioral data sent to third parties requires explicit consent. Interim: internal PostgreSQL event logging. PostHog self-hosted is preferred path when ready.

### Symptom Limit
**March 2026:** 3-symptom limit is an onboarding guardrail only — not enforced app-wide. Protects new users from overwhelm without restricting power users.

### Data Deletion
**March 2026:** Account deletion implemented (/settings/delete-account). Satisfies Washington MHMD right to delete. Requires typing "DELETE" to confirm. Deletes all data in FK-safe order.

---

## Sources
- **Lizz** — engineer user, security and data integrity feedback
- **Mackenzie** — stepdaughter, chronic illness user, UX and feature feedback
- **Missy** — product owner
- **Internal** — product/engineering decisions
- **Legal** — compliance requirements
