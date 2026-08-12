# Baseline — Pre-Rebuild Exit-Gate Review

**Run:** August 12, 2026 · Fable 5 (`claude-fable-5`) · fresh session, per `FABLE-EXIT-GATE-PROMPT.md`
**Nature:** read-only assessment — no code changed; live-DB access was schema/aggregate introspection only (no health content read); Railway CLI left on staging.

**Scope actually covered:** all canonical docs; `app.py` (4,737 lines, read in full), `database.py` (in full), templates/static greps for the class-level checks, all 8 test suites executed (all green), and read-only introspection of **both** live Postgres environments.

> Line numbers reference the tree at commit `90c509b` (the commit this review ran against). They will drift.

---

## 1. Executive summary

**Q1 — App Store readiness of the foundation: READY-WITH-CONDITIONS.**
The data layer is genuinely an asset: the deployed schema was verified on **both** staging and production against the models — all 20 FK `ON DELETE` directives correct, both CHECK constraints present with the exact declared predicates, all 5 unique/partial indexes present, episode-diary Layer A landed on both (all 5 columns, 0 rows written, as designed), zero orphans, zero compliance duplicates, `symptoms.name` widened to 200. Staging and production agree with each other and with the docs. The rebuild can build on this schema with confidence. The conditions are the known ones plus two it must not underestimate: the auth model needs a token layer for native clients (the current session model is an extendable base, not a dead end), and `Episode.onset` naive-local + nullable `duration_hours` must be migrated **early** in the rebuild because the diary's day-counting (the ≥4/≥8/≥15 insurance thresholds) is computed from exactly those two fields.

**Q2 — Systems & processes to build for market: NOT-READY.**
Deploy discipline (staging gate, artifact verification, migration pre-flights) is unusually good, and the standing production checks were re-verified during this review (debugger probe 302, `/dev/bootstrap` 403, both envs). But three pillars of "keep shipping safely" are absent, and none is documented as a known deviation: **(a) there is no observability at all** — no error tracking, no alerting, no uptime monitoring; a production 500 today is invisible unless a user reports it; **(b) there is no verified backup/restore story for the production Postgres** — 19 real users' health data on one Railway volume, and no doc anywhere says whether a backup exists or has ever been restored; **(c) no CI** — 8 test suites that only run when someone remembers. Each fix is small; their absence is what makes this verdict Not-ready rather than conditions.

**Q3 — Human senior-engineer onboarding: READY-WITH-CONDITIONS.**
This is the best-documented small codebase I've reviewed — `CONVENTIONS.md` alone would save a new hire a week, and the Decision Log records *why*, including its own past errors. ~20 doc claims were spot-checked against code: **~80% held exactly**, and the misses are enumerated below (§3). The conditions: fix the specific false claims (two are behavioral, not cosmetic), and accept that `app.py`'s two megaroutes (`checkin()` ~230 lines, `index()` ~310 lines) are where a newcomer needs a map. A senior could safely ship a change on day one — the tests, staging gate, and delete-route guards would catch them before production did.

---

## 2. Findings (most severe first)

### F1 — The app serves exactly one request at a time, with up to 120s single-request stalls — Q1+Q2, **should-fix now (pre-rebuild)**
`Dockerfile` CMD: gunicorn **1 worker, default sync worker class, no `--threads`**, `--timeout 120`. Concurrency = 1. The AI check-in (`app.py:2235`, in-request Anthropic call) and transactional email (`app.py:1497`, in-request Resend call) run inside that one slot.
**Failing scenario:** user A submits a check-in on a slow upstream; for up to 120 seconds, every other user's every request — dashboard, login, episode save — queues behind it. At 19 active users (not the 5 the agent docs claim) this is a live failure mode today, not a latent one.
The docs document "email + AI in-request" as Rule 2 deviations but never connect them with the single sync worker — that combination is an **undocumented deviation of the documented class**. Cheapest mitigation: `--threads 4` (Rule 1-clean; threads share no cross-request state — verify `log_activity`'s standalone connections under threads, they're fine). Note the deliberate 1-*worker* choice was about migration races; threads don't re-run migrations.

### F2 — No verified backup/restore for production health data — Q2, **blocker-for-market**
No file in the repo or `Baseline Files/` mentions Postgres backups. Railway volumes are not automatically a backup. If the volume or the service is lost, 19 users' health histories may be unrecoverable, and nobody has tested a restore. **Owner action:** confirm Railway's backup state in the dashboard, schedule automated dumps (even a nightly `pg_dump` to object storage), and restore one into staging once to prove the path. This is an afternoon of work protecting the only thing in the system that can't be rebuilt.

### F3 — Zero observability: production errors are invisible — Q2, **should-fix before rebuild**
Nothing beyond `app.logger` into Railway's log stream. No Sentry/error tracker, no alerting, no uptime check. Several code paths deliberately log-and-swallow (email sends `app.py:260-267`, Resend sync, FK warnings) — correct behavior, but "logged" currently means "written where nobody looks." The 8/12 debugger incident was found by *accident* while diagnosing a build. A free-tier Sentry + one uptime ping is ~an hour and would have caught that class months earlier.

### F4 — Publicly-known credentials in a public repo, set by an ungated startup path — Q2, **should-fix**
`app.py:4697-4709` `migrate_existing_user()` runs at **every startup, in production, with no debug gate** (`app.py:4715`): if the first `User` row ever has a NULL email, it assigns `admin@baseline.app` / `Baseline2026!` — a password committed to the public GitHub repo (also in `dev_bootstrap`, `app.py:4656`). Inert today (all prod users have emails), but it's a land mine armed by a data anomaly. `run_data_migrations()` and `migrate_episode_interventions()` are the same vintage: completed one-shot migrations from the single-user era that still scan every user/episode on every boot. **Fix: delete all three from the startup path** (dev_bootstrap can keep a randomized password).

### F5 — Rate limiter is very likely keying every client to one IP — Q2, **should-fix, currently under-weighted**
`get_remote_address` (`app.py:76`) reads `request.remote_addr` with no `ProxyFix`; behind Railway's edge, that is plausibly the proxy address for all clients. If so, `/register`'s 5/hour limit is **global**: five signups anywhere in an hour block the sixth real person, and one abuser exhausts login attempts for everyone. This is tracked in the backlog only as a footnote to the CSRF item ("do near Redis"). With open registration it deserves its own verification: log `remote_addr` + `X-Forwarded-For` for one request in production; if they confirm the shared bucket, `ProxyFix` is a two-line fix that shouldn't wait for Redis.

### F6 — Email change requires no verification of the new address — Q1+Q2, **should-fix pre-market**
`app.py:1764-1787` `change_email` swaps the login identity to any unverified address after a password check. The registration flow's entire verification apparatus (tokens, replay protection, disposable-domain blocklist) is bypassed; the disposable-email check isn't even applied. Consequences: account lockout via typo (recovery email never proven owned), Resend contact created for an address the user may not control, and it undercuts the verified-email invariant everything else assumes. Standard fix (verify-new-address-before-switch) can reuse the existing token machinery; reasonable to schedule with the rebuild's auth work, but it is a real gap now.

### F7 — Class: enum-ish fields validated on the AI path but not the form paths — Q3 (Rule 3), **should-fix, small**
The check-in whitelists `functional_impairment` against the four allowed values (`app.py:2298`), but the episode forms store the raw string (`app.py:3614`, `3736`); `new_protocol`/`edit_protocol` accept any `status` (`app.py:3901`, `4045`) — a junk status also flows into `ProtocolEvent.event_type` via the `.get(status, status)` fallback (`app.py:4050`), polluting the status-replay history the 8/12 work specifically protects; `assess_experiment` stores any `decision` string (`app.py:2845`) — on PostgreSQL a >20-char forged value raises an uncaught `DataError` → 500. UI `<select>`s mask all of this, but the codebase's own convention ("required fields are rejected server-side, always — `required` in the HTML is a convenience") applies with equal force to enums. One sibling sweep closes the class.

### F8 — Class: experiment routes silently substitute invalid dates; protocol routes reject them — Q3 (Rule 3), **should-fix, small**
`new_experiment` replaces a malformed `start_date` with today, silently (`app.py:2740-2743`); `edit_experiment` silently ignores it (`app.py:3020-3025`); invalid `stabilization_weeks` silently becomes 3. The protocol siblings flash and re-render (`app.py:3890-3894`, `4010-4018`) — the post-8/12 convention. An experiment silently started "today" instead of its intended back-date corrupts exactly the before/during windows the assessment page computes. Same sweep as F7.

### F9 — Verification-email failure is silent to the user — Q2, **should-fix, small**
`app.py:1502`: the "we had trouble sending" warning is gated on `os.environ.get('MAIL_USERNAME')` — the **retired Gmail-era variable that is never set anywhere**. Under Resend, a failed send (`sent=False`) shows the user "check your email" and nothing ever arrives. The resend link is the only recovery. Change the gate to `if not sent:` (`_send_email` returns the bool precisely for this).

### F10 — Session cookie hardening not set — Q2, **should-fix, bundle with ProxyFix**
`SESSION_COOKIE_SECURE` is never configured (`app.py` config block, lines 27-51), so the session cookie lacks the `Secure` attribute; no HSTS/security headers either. Low practical risk behind Railway's HTTPS redirect, but it's two config lines and belongs in the same pass as F5.

### F11 — Check-in endpoint is uncapped — Q2, **nice-to-have**
`/checkin` has no rate limit and no message-length cap (`app.py:2266`); any authenticated user can pump arbitrarily large messages (plus 7 days of history) through the Anthropic key in a loop. At 19 trusted users, low; before market, cap message length and rate-limit the route.

### F12 — Adequately-tracked deviations: assessment of what's safe to carry
- **Flask-Limiter in-memory (Rule 1):** genuinely latent while 1 worker; the *keying* problem (F5) is the live part. Tracked ✓.
- **Email + AI in-request (Rule 2):** tracked ✓, but see F1 — the docs understate today's severity because they never mention the concurrency of 1.
- **`Episode.onset` naive-local:** tracked ✓ ("deferred to rebuild"), but under-sequenced: the moment a native client in a different timezone writes episodes, naive-local becomes ambiguous per-row. It must be increment-#1-adjacent in the rebuild, ahead of any diary work, because day-counting reads it. Nullable `duration_hours` is the same story (SPEC G4 already says so — carry that emphasis into the rebuild plan).
- **Two-tab/retry duplicates → idempotency keys:** correctly specced, correct to defer ✓.
- **Multi-worker migration race:** documented and real. The durable fix that also unlocks F1's *worker*-based option: move `run_migrations()` out of import-time into a guarded step (Postgres advisory lock, or a release-phase command). Punch-listed below.

### F13 — `app.py` monolith coupling — Q1, informational for the rebuild plan
Business logic is moderately Flask-coupled: the worst offenders are `checkin()` (parse→resolve→write→session-stash in one 230-line request handler) and `index()` (~310 lines of aggregate assembly). But the good pattern is already established — `_upsert_compliance`, `_resolve_trigger`, `_apply_ai_triggers`, `resolve_onset`, `_compute_day_status` are extractable pure-ish helpers, and `database.py` is deliberately request-context-free (the injected-`today` convention). Lifting an API out of this is a re-plumbing job, not a rewrite. No pagination anywhere (`/episodes` loads all rows with per-episode lazy loads) — fine at current scale; design pagination into the API from day one rather than retrofitting.

---

## 3. Doc-accuracy audit

Claims checked against code/live systems. **Held (sample):** staging-gate checks (re-verified live: debugger 302, `/dev/bootstrap` 403 on both envs); Dockerfile-is-the-start-definition; all FK/CHECK/index claims (verified on both live DBs); cookie-first tz resolution in `user_tz_name()`; tri-state log-day validation window; CSRF coverage (36 forms counted, every one carries a token; zero `|safe` in templates); suggest-and-confirm AI trigger flow; rate-limit numbers; `STOP_REASON_CHECK_SQL` single-source claim.

**Failed:**

| # | Claim | Reality |
|---|---|---|
| 1 | TECHNICAL_README: day-dot status "computed by the single `_compute_day_status()` helper — used by both the server render and the AJAX response" | **False.** `index()` computes it inline (`app.py:3308-3318`); only `log_protocol_day` uses the helper (`app.py:4263`). Logic is currently equivalent — this is precisely the "two todays" drift shape Rule 3 exists for. **Corrected 8/12/26** (same commit as this report). |
| 2 | TECHNICAL_README: "Fully clearing a day to hollow is done … or by un-logging via the protocol-detail form" | **False.** The detail form is yes/no radios only; `log_protocol_today` (`app.py:4195`) can never delete a row. There is no un-log path outside the dashboard card. **Corrected 8/12/26** (same commit as this report). *(The report as first drafted attributed both this and item 1 partly to CLAUDE.md — both claims live only in TECHNICAL_README; fixed here before committing.)* |
| 3 | `user_today()` docstring (`app.py:1112`): resolves "stored User.timezone → cookie → server UTC" | **Backwards.** Resolution is cookie-first (`user_tz_name`, `app.py:1059`), per the owner's 7/17 decision. A newcomer reading the docstring learns the rejected design. |
| 4 | CLAUDE.md + TECHNICAL_README: "20 FKs (16 CASCADE, 3 SET NULL)" | 16+3=19. The dict has **17 CASCADE** + 3 SET NULL = 20. Arithmetic slip, in both files. |
| 5 | `.claude/agents/*`: "5 active users" / "5 real users"; qa-tester.md: "Auth: … **invite-code registration**" | Production has **19 active verified users**; registration has been self-serve since April. The 8/13 sweep fixed these files' staging line but missed the user-count and auth lines — the drift class survived its own cleanup. |
| 6 | TECHNICAL_README routes table: `/settings/change-password`, `/settings/change-email`, `/assess_experiment/<id>` | Actual routes: `/settings/password`, `/settings/email` (`app.py:1790`, `1764`), `/experiments/<id>/assess` (`app.py:2826`). |
| 7 | Root README: "The backend is production-ready and won't change; only the frontend layer moves." | Overclaim, contradicted by the project's own backlog: auth token layer, async check-in, Redis, onset migration, API layer are all backend changes the rebuild requires. On the portfolio-facing page, this is the one claim an outside senior engineer would read and then distrust the rest. |
| 8 | SPEC/spec-era line refs (`database.py:154`, `app.py:557`) | Drifted (now ~163, ~571). Cosmetic; expected without tooling. |

**Can a newcomer trust the docs?** Mostly yes — an unusually strong yes on *why* (Decision Log) and *how we do things* (CONVENTIONS). The failure pattern is specific: **behavioral claims about code that later changed shape** (items 1-3) and **counts that were true once** (4-5). A newcomer should trust decisions and conventions, and verify any sentence that describes what a specific route/helper does — which, to the project's credit, is what CLAUDE.md itself now instructs.

---

## 4. Pre-rebuild punch-list (ranked by leverage)

**Do before the rebuild starts (roughly a week of part-time work, total):**
1. **Backups (F2):** confirm/enable Railway Postgres backups + one tested restore into staging. Nothing else on this list matters if this fails.
2. **Concurrency (F1):** add `--threads 4` to the gunicorn CMD; verify on staging with a slow check-in + a second browser. One line; removes the worst live failure mode.
3. **Observability (F3):** Sentry (or equivalent) + one uptime monitor on `mybaselineapp.com`. ~1 hour.
4. **Dead startup paths (F4):** delete `migrate_existing_user`, `run_data_migrations`, `migrate_episode_interventions` from startup; randomize `dev_bootstrap`'s password.
5. **CI (already backlogged as "early-rebuild" — pull it forward):** a GitHub Action running the 8 suites on every push to `staging`. The rebuild period is exactly when a regression net pays off most; don't start the rebuild without it.
6. **Proxy verification (F5):** one log line to confirm the shared-IP-bucket hypothesis; if confirmed, `ProxyFix` + `SESSION_COOKIE_SECURE` (F10) in one small deploy.
7. **Small correctness sweep (F7+F8+F9):** enum whitelists on the form paths, reject-don't-substitute dates on experiment routes, fix the `MAIL_USERNAME` gate. One session, mostly mechanical, all covered by existing test patterns.
8. **Doc fixes (§3, items 1-7):** one commit. Items 1-3 are behavioral and will actively mislead the rebuild's authors.

**Fold into the rebuild plan (don't do now):**
- Migration runner out of import-time (advisory lock / release phase) — prerequisite for >1 worker and for any real API deploy cadence.
- Token auth + email-change re-verification (F6) as part of the auth layer; idempotency keys as specced.
- `Episode.onset` → UTC + duration capture **early** (diary depends on both); Redis + async check-in as planned.
- API pagination from day one (F13).

**Verified safe to carry forward as-is:**
- **The schema and its enforcement machinery** — verified live on both environments; the FK matrix, CHECKs, partial indexes, and the verifier/orphan-check tooling are better than most teams' Alembic hygiene.
- **CSRF posture** (complete coverage, verified), template escaping (no `|safe` anywhere), user-scoping (spot-checked every query family — all scoped or deliberately global).
- **The deploy discipline and its written runbooks** — the staging gate, artifact-verification checks, and migration pre-flights are real and currently true.
- **The test suites** (all 8 green under framework `python3`) and the conventions/decision-log documentation system, staleness spots aside.

**Bottom line:** the *foundation* (data model, security posture, deploy discipline) is ready to build on. The *operational shell around it* (backups, observability, CI, single-request concurrency) is not yet what a market product needs — but every gap is days, not months, and all of it should land before the first rebuild commit so the rebuild starts on a floor that can't silently fail.
