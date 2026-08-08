# Baseline — Conventions / Canonical Patterns

**Purpose:** the single definition of "the way we do things," so features and CRUD
actions stay consistent across areas as the app grows. Before adding or changing
code, find the nearest existing sibling and **match these patterns**. A new or
divergent pattern is a review finding unless it's justified. Enforced by the
CLAUDE.md rule *"Rule 3 — Converge, Don't Diverge"* and checked by the Code
Reviewer agent's Consistency section.

Keep this doc alive: when a genuinely new canonical pattern is established, add it
here in the same commit.

---

## Dates & time
- **User-facing "day" (which calendar date):** always `user_today()` — never a raw
  `date.today()` or `datetime.utcnow().date()`. Covers compliance dates,
  protocol-event dates, the dashboard "today," and anything a user perceives as
  today/yesterday. Railway runs UTC; `user_today()` resolves the zone via
  `user_tz_name()`.
- **Which timezone is the user in:** `user_tz_name()` — the single source of truth.
  Resolution is **cookie-first**: the current request's `baseline_tz` cookie
  (this device's own zone) → then the stored `User.timezone` (durable fallback for
  cookie-less contexts) → then None (server UTC). Cookie-first so a device always
  computes "today" in its OWN zone; the stored column is a fallback, not an override
  (owner decision 7/17/26 — stored-first caused cross-device thrash). Both cookie
  and stored value are `ZoneInfo`-validated before use so a bad value self-heals.
- **`User.timezone` is persisted** by `sync_user_timezone()` in `require_auth` (the
  one place a `db.session.commit()` runs inside a `before_request` — it's before any
  route touches the session, writes only on change, logs on failure, never blocks).
  Don't add other before-request commits without the same care.
- **"Now" in the user's zone (a moment they're logging):** `user_now()` — the
  time-of-day sibling of `user_today()`. Returns the user's local wall-clock as a
  **naive** datetime, matching `Episode.onset`'s browser-local-naive storage. Use
  it for the future-onset guard and any no-JS onset fallback so both sides of a
  comparison are in the same frame. Never `datetime.now()` (server-local) for
  user-facing "now."
- **Absolute timestamps / ordering / TTLs:** `datetime.utcnow()` — `created_at`,
  `verified_at`, cutoffs, rate windows. These are moments in time, not calendar
  days; UTC is correct and needs no timezone handling.
- **Rule of thumb:** if you're deciding "is this *today* for the user," use
  `user_today()`. If you're stamping "when did this happen," use `utcnow()`.
- Client-sent dates (dashboard card body, check-in `client_time`) are browser-local
  and preferred where available; the server validates them within a small window
  anchored on `user_today()`.
- **Models stay request-context-free.** A model method that depends on "today"
  (e.g. `Experiment.ready_to_assess(today)`, `weeks_elapsed(today)`) takes `today`
  as a parameter — the route resolves `user_today()` and passes it in (annotating
  the result onto the instance for templates: `exp.is_ready = exp.ready_to_assess(today)`).
  Never import `flask.g`/`request`/`user_tz_name` into `database.py` — the model
  layer must stay reusable behind the future non-Flask API (owner decision 7/17/26).

## Compliance writes
- Go through the shared helpers: `_commit_compliance()` → `_upsert_compliance()` /
  `_delete_compliance()`. Never write `ProtocolCompliance` rows directly.
- Tri-state `took`: `True`/`False` set the day's row, `None` un-logs it (deletes).
  Most-recent-explicit-statement wins. Validate every entry before touching the
  session — no partial writes.

## Selection controls (single- vs multi-select)
- **Single-select → a `<select>` dropdown** (functional impairment, the symptom
  add-back picker, the per-row intervention protocol). One choice, compact.
- **Multi-select → tap-to-toggle pills/chips**, not a native multi-`<select>`
  (miserable on mobile). Canonical example: the episode-form **trigger picker**
  (`.trigger-chip`). Each chip is a real `<label>` wrapping a checkbox; selection
  is a JS-toggled `.selected` class (same approach as the binary Yes/No toggle,
  so we don't depend on `:has()`). A "+ add" text input beside the chips creates
  a new selected chip and **flushes any un-added text on form submit** so a typed
  value is never silently lost. Chips must `overflow-wrap: anywhere` + tap target
  ≥44px. This pills=multi / dropdown=single split is deliberate (decided 7/15/26);
  don't reach for a dropdown when the field is genuinely multi-select.

## Action buttons: never a live-looking silent no-op
- **An enabled button must always do something visible.** If a button's action
  requires an input that isn't there yet, **disable it until it is** — don't let
  the click fall through a `if (!value) return;` guard with no feedback. A dead
  tap is indistinguishable from a broken app, especially for a user in a flare.
- Canonical pair, both on the episode form: `#addSymptomBtn` (disabled until the
  `<select>` has a real value — toggled inside `refreshAddSection()`, which is the
  single place that computes both "is the section visible" and "is the button
  enabled") and `#addTriggerBtn` (disabled until `normalize(input.value)` is
  non-empty, toggled on an `input` listener). Both also carry `disabled` in the
  **markup**, so a JS failure fails safe (inert) rather than unsafe (clickable
  and dead).
- Use plain `disabled`, not `aria-disabled` — it matches every other guarded
  button in the app (`#pw-submit-btn`, the char-limit guards in `base.html`).
- Styling comes from the global **`.btn:disabled`** rule (opacity `0.55`, default
  cursor, with `:hover`/`:active` neutralised so a disabled button can't brighten
  on hover). Don't add a scoped copy — that rule was `.tp-card`-scoped until
  8/8/26 and left disabled buttons undimmed everywhere else.
- If disabling a control can make it (or its whole section) vanish while focused,
  **move focus somewhere stable first** — `addSymptomBack()` focuses the restored
  symptom row rather than letting focus drop to `<body>`.
- Established 8/8/26 fixing the "'Add' symptom did nothing" bug, where the button
  looked live and silently no-op'd on the placeholder selection.

## Double-submit protection (every POST form, automatic)
- `base.html` carries a **global submit guard**. New forms get it for free —
  there is nothing to opt into. It listens for `submit` on `document` in the
  **bubble** phase, so every form-level listener has already run.
- Attribute protocol:
  - **`data-submitting="1"`** — set on the form once a real submit is in flight.
    A second submit is `preventDefault()`ed. *This flag is the actual
    protection.*
  - **`data-guard-disabled="1"`** — set on each of that form's submit buttons.
    Purely feedback: it drives the dimming (`.btn:disabled`) and the trailing
    ellipsis (`.btn[data-guard-disabled="1"]::after`), so "Save Episode" reads
    "Save Episode…" while in flight. Never make correctness depend on it.
  - **`data-no-submit-guard="1"`** — opt out. Needs a stated reason.
- **Bubble phase is required, not incidental.** `e.defaultPrevented` is how the
  guard knows a submit isn't really happening — a cancelled
  `onsubmit="return confirm(...)"`, a failed validity check, the
  active-experiment modal's own `preventDefault()`. A capture-phase listener
  would latch forms shut on submits that never happen.
- **Any code that recomputes a submit button's `disabled` on input must respect
  the latch** — early-return (or `|| data-guard-disabled`) when it is set.
  Precedents: the char-limit guard in `base.html`, the password-strength
  validators in `settings.html` + `register.html`, the delete-account confirm.
  Miss this and typing during a slow save hands the button back.
- **PITFALL — `form.submit()` bypasses all of this.** `HTMLFormElement.submit()`
  dispatches **no** `submit` event (per spec), so no listener anywhere runs: not
  the guard, not `onsubmit`, and *not native constraint validation*. Always use
  **`form.requestSubmit()`** for a JS-driven resubmit. This silently exempted
  the "active experiment → continue anyway" path on `/protocols/new`,
  `/protocols/<id>/edit` and `/experiments/new` until 8/8/26 — and, because
  validation was skipped too, let a **blank-name protocol** be created.
- **What this does NOT cover** — a retried/replayed request, or two browser tabs
  (the flag lives in one tab's DOM; two tabs still duplicate). The durable fix
  is DB-backed idempotency keys, specced for the rebuild's API layer (BACKLOG).
  Don't describe this guard as making duplicates impossible.
- Established 8/8/26 after two rapid taps on `/episodes/new` were proven to
  create two identical episodes.

## Match-and-link writes (shared dimensions: triggers)
- A user-extensible dimension backed by curated globals + per-user customs
  (currently **triggers**) resolves a typed name through **`_resolve_trigger()`**,
  never a raw insert: match an **active** global (case-insensitive) → else the
  user's own custom (reactivating a soft-deactivated one) → else create the
  custom. This prevents duplicate/split rows and reuses the `Symptom`
  case-insensitive-unique precedent.
- Guard the custom-create with a **`db.session.begin_nested()` savepoint** so a
  concurrent same-name create raises `IntegrityError` on the savepoint only, and
  we re-select the winner without losing the outer transaction (the row-level
  form of the "retry once on a possible race" rule — see also `_commit_compliance`).
- Link rows carry a **`source`** ('user' | 'ai') for provenance. Existing-id
  validation is scoped `or_(user_id IS NULL, user_id == me)` and gated on
  `is_active` (with an explicit `preserve_ids` allowance on edit so a
  linked-but-inactive custom the user left checked isn't silently dropped).
- Edit = **replace-on-save** (delete existing link rows, re-create from the form)
  — the same shape as interventions and symptom scores.
- **AI check-in parity is deliberately NOT symmetric with the form.** The AI path
  uses match-only **`_match_trigger()`** (never `_resolve_trigger`), so it links
  only triggers the user already has (`source='ai'`); a name it doesn't recognize
  is returned as a *suggestion*, stashed in the session, and offered on the
  episode form for the user to confirm (Save → `source='user'`). Rule: **AI links,
  the user creates.** (Contrast interventions, where the AI auto-creates — triggers
  are more speculative, so they get a confirm step. Owner decision 7/15/26.)

## Deletes (FK-safe)
- Delete children before parents. Local SQLite enforces FKs
  (`PRAGMA foreign_keys=ON`), so FK-unsafe deletes fail in dev too, not just prod.
- **Update BOTH manual delete paths** when you add an owned table: `delete_account`
  AND `dev_reset` (bulk `.delete()` bypasses ORM cascade).
- Prefer **soft-deactivate** (`is_active=False`) over hard delete for records with
  history (triggers, rescue options). Never hard-delete a row others reference.
- New child table → trace every parent's delete path (blast radius).

## CSRF (every state-changing POST)
- Flask-WTF `CSRFProtect` is global/opt-out — a new POST route is protected
  automatically. **Every `<form method="POST">` must include**
  `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">` right
  after the opening tag. A button with `form="..."` outside its form is fine as
  long as the token is inside that form.
- **JSON `fetch`/XHR POSTs** send the token as an `X-CSRFToken` header:
  `headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() }`.
  `csrfToken()` (global, in base.html) reads the `<meta name="csrf-token">`.
- Don't hand-roll CSRF exemptions. If a genuinely token-less POST is ever needed
  (e.g. an external webhook), `@csrf.exempt` it explicitly and say why.

## User scoping (privacy)
- Every query is scoped by `user_id` — directly, or by joining through an owned
  parent (e.g. Episode). This is health data; a missing scope is a cross-user leak.
- Never trust a client-supplied `user_id`; derive it from the session (`get_user()`).

## Validation
- Text caps: name ≤ 200, description/notes ≤ 500 (with a UI counter). Enforce on
  **every** write path for the same field — form, AI check-in, and dashboard/JSON —
  not just one.
- Validate everything before the DB write. JSON endpoints reject invalid input with
  `{ok: false, error}` + a 4xx **before** any partial write.

## Error handling
- DB writes in `try/except` with `db.session.rollback()`; retry once on
  `IntegrityError` where a concurrent race is possible (see `_commit_compliance`).
- User-facing: friendly `flash()`; log the real detail. Never surface health data
  or stack traces to the user.
- **Swallow narrowly.** Catch the specific exception and **log** it. A bare
  `except: pass` around timezone parsing hid a production bug for months — don't
  repeat it.

## Migrations
- Additive columns via `run_migrations()` ALTER, gated by a column-exists check.
  PostgreSQL-safe booleans: `DEFAULT TRUE/FALSE`, never `1/0`.
- Seed/index idempotently: `CREATE ... IF NOT EXISTS`, `INSERT ... WHERE NOT EXISTS`.
  Wrap in try/except log-and-continue so a migration can't block startup.
- New tables are auto-created by `db.create_all()`; only seed/index needs a migration.

## Architecture rules (full text in CLAUDE.md)
- **Rule 1** — backend stays stateless. **Rule 2** — slow work off the request path.
  **Rule 3** — converge, don't diverge (this doc).
- Raise design decisions (new table? index? new pattern?) with short-term vs
  long-term trade-offs before proceeding — this is a learning project.

## Adding a feature or CRUD action — checklist
1. **Find the nearest sibling** — the closest existing CRUD action or feature.
2. **Match it:** shared helpers, date/tz handling, validation, delete-safety,
   user-scoping, error handling, and JSON/response shape.
3. **If you must diverge, say why** — in the commit and raised to the owner. An
   accidental new pattern is a bug in waiting.
4. **Update this doc** when a new canonical pattern is genuinely established.
