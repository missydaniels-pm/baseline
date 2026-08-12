# Fable Exit-Gate Review — prompt

**How to run:** open a **fresh Claude Code session in the repo root** with the **Fable 5**
model selected (`claude-fable-5`). Paste everything below the line into that session. It is a
**read-only assessment** — the reviewer should produce a written verdict and punch-list, not
change code. Budget it generously; this is the capstone review before the React rebuild.

---

You are a staff-level engineer doing a **pre-rebuild exit-gate review** of "Baseline," a Flask
health-tracking web app (server-rendered Jinja + PWA) that is about to be rebuilt on a React /
React Native stack aimed at the App Store. This is the last review before that rebuild starts,
and it exists to catch what the per-increment reviews structurally could not: whole-codebase
drift, and whether the foundation is actually sound. You did not write any of this code. Treat
that as an advantage.

## The three questions this review must answer

Everything you do should serve a clear, evidence-backed verdict on each:

1. **App Store readiness of the foundation.** Could we start building a mobile app that could be
   submitted to the App Store *on top of this codebase* — or does the foundation have to change
   first? Be concrete about what "the rebuild" is actually carrying vs. papering over.
2. **Systems & processes to build for market.** Do we have the right engineering *system* —
   testing, deploy/release, migrations, observability, security, backups, documentation — to
   keep shipping a real product safely, not just to ship once?
3. **Human senior-engineer onboarding.** If the owner hired a senior engineer tomorrow, would
   this codebase and its docs make sense to them, and could they safely make a real change on
   **day one**? Where would they get stuck, misled, or bitten?

Give each a plain verdict — **Ready / Ready-with-conditions / Not-ready** — with the specific
conditions, and a short "what I'd fix before the rebuild" list ranked by leverage.

## Posture — falsify, don't confirm

- **Trust nothing you're told; verify against the code.** The docs and code comments are
  extensive and mostly earnest, but this project's recurring failure mode is a claim that was
  written down, believed, and never checked (a "gunicorn in production" line was wrong for
  months; a "no staging" claim sat stale for weeks; a SIGTERM "fix" was asserted before being
  tested and turned out not to reproduce). When a comment or doc asserts a behavior, confirm it
  in the code or by running something. Flag every claim you find that the code contradicts.
- **Hunt the class, not the instance.** The single most expensive pattern here has been "fixed
  the case in front of me, not its siblings" (validation added to create routes but not edit
  routes; a parse guard on one form but not the next). When you find a defect, immediately look
  for its siblings across the whole codebase and report the *class*.
- **Widen past anything I name.** The dimensions below are a floor, not a ceiling. If the real
  risks are somewhere I didn't point you, that omission is itself a finding.
- **Known deviations are already documented** (see CLAUDE.md "Architecture Rules" and BACKLOG):
  Flask-Limiter in-memory (Rule 1), email + AI check-in run in-request (Rule 2), no object
  storage yet. Don't just re-report their existence — assess whether they're *adequately
  tracked and safe to carry into the rebuild*, and whether there are **undocumented** deviations
  of the same class that nobody has caught.

## Read first (context), then the code

Start with the canonical context so you review against *this project's* standards, not a generic
bar: `CLAUDE.md`, `Baseline Files/CONVENTIONS.md`, `Baseline Files/TECHNICAL_README.md`,
`Baseline Files/BACKLOG.md` (esp. the Decision Log), the `Baseline Files/SPEC-*.md`, the
`.claude/agents/*.md` review checklists, and the root `README.md`. Then the code: `app.py`
(large — all routes + business logic), `database.py` (models + migrations), `templates/`,
`static/`, and the `test_*.py` suites. Run the test suites (they are plain scripts, not pytest:
`WTF_CSRF_ENABLED=false python3 test_X.py`).

## Environment access — staging + production Postgres (READ-ONLY)

Local dev is **SQLite**; staging and production are **PostgreSQL**. The local test suites can pass
while the *deployed* schema has drifted — SQLite can't alter constraints in place, so the real
schema of record lives in Postgres. **A local-only review misses exactly the class of problem
this project has an FK-verifier and a startup schema-check to guard against.** So you have, and
should use, read access to both live environments via the Railway CLI (already installed and
linked).

**Hard guardrails — non-negotiable:**
- **Read-only. No writes, no schema changes, no data mutation, in either environment.** Not even
  a rolled-back write-probe against **production**. To test whether a CHECK/constraint enforces,
  read its definition from `pg_constraint` / `information_schema` — a present constraint *is*
  enforcement in Postgres; you never need to insert a bad row to prove it.
- **Never SELECT real health content.** This is real users' medical data. Use schema/aggregate
  introspection only — column lists, constraint definitions, `COUNT(*)`, `NULL`-vs-not counts.
  Do not read episode notes, symptom values, check-in text, emails, or any row-level PII.
- When done, leave the Railway CLI context on **staging** (not production).

**How (grounded in `Baseline Files/STAGING_SETUP.md`):** select an environment and read its
Postgres public URL, then introspect with SQLAlchemy/psycopg2 read-only:
```bash
railway environment staging   # or: production
railway service Postgres
railway variables -s Postgres --json   # parse DATABASE_PUBLIC_URL (the private *.railway.internal host won't resolve off-Railway)
```
The repo's own read-only tools work against a live DB and are the right first move: run
`check_fk_orphans.py` and `verify_fk_ondelete()` against **both** environments.

**What to actually check against live Postgres (bears on Q1 + Q2):**
- Does the **deployed schema match the models** — every column, every CHECK
  (`symptoms_input_type_check`, `protocol_events_stop_reason_check`), every FK `ON DELETE`
  directive in `EXPECTED_FK_ONDELETE`? Report any drift between SQLite-dev, staging, and prod.
- Did the episode-diary Layer A migration land correctly on **both** (5 columns + the constraint)?
- Any orphaned rows, missing indexes (`ux_protocol_compliance_day`, the partial trigger indexes),
  or constraints that silently failed to apply (`ADD CONSTRAINT` stays on old behaviour if a row
  violated it at migration time)?
- Do staging and production agree with each other, and with what the docs claim is deployed?

## Dimensions (map each finding back to Q1/Q2/Q3)

**Architecture & rebuild-readiness (Q1).**
- How coupled is business logic to Flask request/session/Jinja? `app.py` holds all routes *and*
  business logic — assess how cleanly the domain logic could be lifted behind an API for a
  mobile client, and where the coupling is worst. Is the data model an asset the rebuild builds
  on, or does it carry mistakes forward?
- Rule 1 (stateless backend) and Rule 2 (slow work off the request path): verify compliance and
  find undocumented violations. These are load-bearing for any real mobile backend.
- Auth for a native client (today it's Flask session cookies) — what has to change, and is the
  current model a dead end or an extendable base?
- Health-data specifics that gate an App Store submission: data handling, privacy-label
  implications, MHMD/consent posture, any HealthKit assumptions. Flag what will bite at review
  time, not just what's technically wrong.

**Systems & process maturity (Q2).**
- Test strategy: coverage, what's *untested*, and the known absence of any JS/browser test
  harness — is that survivable at market scale? What breaks silently today?
- Release/deploy: the staging-gate discipline, the Dockerfile, single-worker gunicorn,
  in-request startup migrations (assess the multi-worker migration-race risk), rollback story.
- Observability: is there any error tracking, structured logging, alerting, uptime/DB
  monitoring, backup/restore for Postgres? What would the team be blind to in an incident?
- Security posture beyond CSRF (which is done): the pending items (ProxyFix / `X-Forwarded-Proto`,
  `/logout` GET→POST, rate-limiter backend) and anything unlisted.
- Migration discipline: is the additive-ALTER + engine-split pattern robust, idempotent, and
  safe on real Postgres, or are there latent footguns?

**Comprehensibility & onboarding (Q3).**
- Could a new senior engineer navigate `app.py`'s size and structure and locate where to make a
  change? Is the module boundary between `app.py` and `database.py` sensible?
- Are `CLAUDE.md` / `CONVENTIONS.md` / `TECHNICAL_README.md` accurate and sufficient as
  onboarding material — or would they actively mislead (stale claims, aspirational statements
  presented as fact)? Verify a sample of their claims against the code and report the hit rate.
- Convention consistency (Rule 3): is the same kind of thing done the same way everywhere, or is
  there drift a newcomer would trip on? Name the worst offenders.
- Do the heavy explanatory comments and Decision Log help a newcomer, or is there noise/staleness
  that misleads? Is the test suite a usable safety net for a first change?

## Output

1. **Executive summary** — the three verdicts (Ready / Ready-with-conditions / Not-ready), each
   in a few sentences with the deciding evidence.
2. **Findings**, most severe first. Each: a one-line claim, the file:line evidence, a concrete
   failing scenario or the contradiction, which of Q1/Q2/Q3 it bears on, and severity
   (blocker-for-rebuild / should-fix / nice-to-have). Group siblings as one class-level finding.
3. **Doc-accuracy audit** — a short list of doc/comment claims you checked and whether each held,
   with the overall "can a newcomer trust the docs?" read.
4. **Pre-rebuild punch-list** — the ranked set of things worth fixing *before* the rebuild
   starts, and explicitly what is safe to carry forward as-is (with why).

Be direct. A false "you're ready" is far more expensive here than an unwelcome "not yet."
