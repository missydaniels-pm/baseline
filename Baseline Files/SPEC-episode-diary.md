# Spec — Episode Diary (physician / insurance export)

Status: **schema identified; both open decisions closed 8/13/26 — ready to build** ·
Started 8/12/26 · Owner: Missy

**Decisions closed 8/13/26:** (1) midnight boundary defaults to **both days**, setting retained
(G4); (2) stop reason is a **controlled list + optional note** (G1). Verification of G1's
"no migration needed" assumption showed it was wrong, so the pre-rebuild migration count went
**2 → 3**. Rendering remains post-rebuild.

**Purpose of this document.** Identify the **schema changes that must land before the
React rebuild** so a physician-facing episode diary is possible later. It is not a
build plan for the export itself — rendering is deliberately post-rebuild (a Jinja
print view would be rewritten in React). What cannot wait is *data capture*: any month
that passes without capturing a field is a month that field is missing from the record,
and unlike layout, that is not recoverable later.

---

## What this is for

Chronic-illness users are frequently *required* to keep a diary — by a physician, or by
an insurer as a condition of treatment authorisation. Baseline already is that diary.
The gap is getting it to the doctor in a form they accept.

The concrete driver (Missy, 8/12/26): a **Migraine / Headache Calendar** form her
neurologist submits to insurance. But the design target is deliberately **generic** —
"pick one of the things you track, get its episodes with the rescue protocols used,
counted per month." Filtering by tracked item, rather than by a hardcoded condition,
keeps the app condition-agnostic (CLAUDE.md, Product Philosophy) and needs no
migraine-specific concept.

### What insurers ask for, and where Baseline stands

| Requirement | Baseline today | Gap |
|---|---|---|
| **Attack frequency** — N headache days/month (thresholds ≥4, ≥8, ≥15 gate CGRP/Botox) | Episodes with onset dates | Derivable. But see **G3 — coverage** |
| **3–6 months of *consistent* diary data** | Episodes over time | **G3** — a day with no episode is ambiguous: no attack, or no logging? |
| **Step therapy** — 2–3 failed preventive classes, each with name, dose, duration, **reason for stopping** | `Protocol.name`, `dose_frequency`, `start_date`; stop date now accurate (8/12/26) | **G1** reason · **G2** drug class |
| **Disability / impact** — MIDAS or HIT-6 | `Episode.functional_impairment` (4 levels) | **G5** — not the validated instruments |
| **Work impairment** — missed workdays, reduced hours, FMLA | `functional_impairment` covers "working reduced" / "cannot work" | Close enough to describe; not day-count of *missed work* |
| **Associated symptoms** — cognitive slowing, photophobia, nausea | User-defined symptoms + scores | ✅ none |
| **Medication used per day** | `EpisodeIntervention` → rescue `Protocol.name` | **G2** — class letter, not name |
| **Format** — exported log or handwritten calendar | — | No schema implication |

---

## Schema changes needed before the rebuild

### G1 — Reason a protocol was stopped  ← highest value; **needs a migration after all**
Step therapy documentation explicitly requires *why* each preventive was discontinued
("ineffective", "side effects", "cost"). Baseline captures nothing today: a protocol
stopped outside an experiment records only that it stopped.

**DECIDED 8/13/26 — controlled list + optional note** (owner). A controlled vocabulary is
far more useful in a document an insurer reads, and it is the only version that can be
*counted* — "prove 2–3 different classes were tried and failed" is a query, not a
paragraph. Free text would leave the central step-therapy claim to be assembled by hand
every time. Precedent verified: `symptoms_input_type_check` in `database.py:154` is a
real CHECK constraint on a short vocabulary, so this matches an established pattern.

Proposed vocabulary: `ineffective` · `side_effects` · `cost` · `doctors_advice` ·
`other`, plus a free-text note (the `other` escape hatch, and useful detail on
`side_effects` regardless).

> **⚠️ The earlier "likely no migration" plan does not survive verification.** This spec
> said `ProtocolEvent.detail` "is currently written only for `dose_changed`" and told the
> reader to verify before assuming. Verified 8/13/26 — **it is false.** `assess_experiment`
> (`app.py:2813`) already writes `detail=f'From assessing "{experiment.name}"'` **on a
> status event**, which is precisely the stop path a stop-reason targets. Reusing `detail`
> would therefore either overwrite that provenance or concatenate two unrelated meanings
> into one free-text column — and a column with two meanings cannot be queried for either.
> Combined with the controlled-list decision (which wants a constrained column anyway),
> **G1 needs its own migration.** That takes the pre-rebuild count from two to three.

**Proposed shape** (implementation detail, confirm at build time): two nullable columns on
**`ProtocolEvent`**, not on `Protocol` — the reason belongs to the *stop event*, so a
protocol paused, reactivated and later stopped records a distinct reason each time, which
is what a step-therapy history actually looks like.
- `stop_reason` — `String(30)`, nullable, CHECK-constrained to the vocabulary above.
- `stop_reason_note` — `Text`, nullable.

`detail` keeps its existing single meaning (dose-change description, assessment
provenance) and is deliberately **not** overloaded. Nullable throughout: existing rows
cannot be backfilled, and users not producing a clinical document should never be asked.

> **Build-time decision (raised by Code review 8/13/26) — scope the CHECK to the event
> type, don't just constrain the vocabulary.** A plain `CHECK (stop_reason IN (...))`
> polices the *value* but not *which rows* may carry it: nothing would stop a
> `stop_reason` landing on a `'dose_changed'` or `'started'` row, which is meaningless in
> a clinical export. That is the *same* unpoliced-column-meaning hazard that made the
> `detail` collision above possible — so closing it here rather than repeating it is the
> consistent move. **Recommend Option B**, a compound constraint
> `CHECK (stop_reason IS NULL OR event_type IN ('stopped','paused'))`, matching the
> load-bearing self-enforcement rationale behind `symptoms_input_type_check` (`app.py:557`).
> App-layer-only (Option A) is acceptable and matches how `event_type` itself is validated,
> but the DB-level version is cheap now and expensive to discover missing later (a bad row
> silently entering a physician document).
>
> **Cross-engine (SQLite dev / PostgreSQL staging+prod) — verified where possible, and it
> works on both, but each path must be written explicitly. Follow the
> `symptoms_input_type_check` precedent (`app.py:557–608`), which is already engine-split:**
> - **Model DDL** — put a *named* constraint in `ProtocolEvent.__table_args__`:
>   `db.CheckConstraint("stop_reason IS NULL OR event_type IN ('stopped','paused')",
>   name='protocol_events_stop_reason_check')`. This is what `create_all()` applies on a
>   **fresh** DB, both engines — the common local path (CLAUDE.md: a stale local SQLite DB
>   is fixed by deleting `instance/migraine_tracker.db`, not migrating in place).
> - **PostgreSQL (existing staging/prod DB)** — the additive migration adds the nullable
>   column, then adds the constraint as a **named table constraint**, guarded by the same
>   `information_schema.table_constraints` existence check the `symptoms` branch uses:
>   `ALTER TABLE protocol_events ADD CONSTRAINT protocol_events_stop_reason_check
>   CHECK (stop_reason IS NULL OR event_type IN ('stopped','paused'))`. A named *table*
>   constraint (not an inline column CHECK) avoids Postgres's column-vs-table-constraint
>   ambiguity for a cross-column predicate, and being named makes it idempotency-checkable.
> - **SQLite** — no retrofit rebuild needed: `stop_reason` is a *new* column, so unlike
>   `input_type` (which retrofitted a CHECK onto an existing column and thus needed the
>   full table rebuild), the CHECK rides the `ADD COLUMN`. **Verified 8/13/26** against a
>   scratch SQLite DB: `ALTER TABLE … ADD COLUMN stop_reason … CHECK (stop_reason IS NULL
>   OR event_type IN ('stopped','paused'))` is accepted *and* enforced — a `stop_reason` on
>   a `dose_changed`/`started` row is rejected, `NULL` and stop/pause rows pass.
> - **The `stop_reason IS NULL OR …` prefix is load-bearing twice:** it is the semantic
>   rule, *and* it is what makes the Postgres `ADD CONSTRAINT` valid against the populated
>   prod table — every existing row has `stop_reason = NULL` and passes the NULL branch.
>   Without it, the migration would fail on every existing `protocol_events` row.
> - **Not verifiable off-Railway:** the PostgreSQL execution itself (no local Postgres).
>   That is precisely the staging gate's job — the migration runs against staging Postgres
>   and is confirmed there before `main`. Don't merge on the SQLite result alone.

### G2 — Medication class on `Protocol`  ← needs a migration
Needed twice, for different vocabularies:
- **Preventive**: the class is what proves "2–3 *different* classes were tried" — beta
  blocker, anti-seizure, antidepressant, CGRP, Botox, other. A list of drug *names*
  does not demonstrate this; an insurer needs the classes.
- **Rescue**: the calendar form wants a single letter — T=Triptan, O=Opioid, N=NSAID,
  A=Acetaminophen, G=Gepant, B=Barbiturate, D=Device, X=Other.

One nullable `med_class` column on `Protocol`, vocabulary selected by `type`. Nullable
because it is optional for users who aren't producing a clinical document, and because
existing rows can't be backfilled reliably. Set once per protocol at creation.

**This is the only change here that certainly requires a migration.**

### G3 — Diary mode is opt-in; coverage is *attested*, not inferred
**Owner decision 8/12/26.** Only some users ever need this. Do **not** make fields
mandatory app-wide to serve them — that puts the cost on everyone who doesn't.

Instead: a Settings toggle, *"Use Baseline as an episode diary for my doctor."* It is
the switch that (a) makes the diary-only fields required **for those users only**, and
(b) unlocks a short pre-print confirmation step.

Schema: one boolean on `User`, following the `ai_logging_enabled` /
`email_updates_enabled` precedent. Trivial migration.

**This also resolves the coverage ambiguity better than deriving it.** An insurer wants
3–6 months of *consistent* data, and a day with no episode is ambiguous — good day, or
no logging? Rather than have the system infer, **ask the user at print time**:

- "You have no episodes recorded on 14 days in this range. Were those episode-free?"
- "Import period data from Apple Health?" (when HealthKit lands — see G5)
- "An episode running past midnight counts as both days / only its start day?" (G4)

The user attests; the system doesn't guess. For a document going to a physician under
the user's name that is the correct division of responsibility, and it costs nothing on
any ordinary day. Supersedes the earlier derive-vs-explicit-marker options.

### G4 — Rules that need deciding, not schema
No migration, but they must be written down before anything renders, because they change
the numbers a clinician reads:
- **Count DAYS, not episodes — the single most important rule here.** Naively counting
  episode rows is wrong in both directions, and they are the same bug: two episodes on
  one day counts 2 and should be 1; one 76-hour episode counts 1 and should be 4
  (Missy, 8/12/26 — a real logged episode). Both fall out of one operation: for each
  episode derive the set of calendar days it touches from `onset` + `duration_hours`,
  **union** across episodes, count the union. `{Tue}` → 1. `{Mon,Tue,Wed,Thu}` → 4.
  Undercounting is the dangerous direction: authorisation thresholds are ≥4 / ≥8 / ≥15
  days per month.
- **`duration_hours` is therefore load-bearing, not decorative.** It already exists and
  is nullable, so no migration — but a blank duration silently undercounts a multi-day
  episode to one day. This is the strongest reason for diary mode (G3) to make duration
  required *there and only there*.
- **Boundary rule is a user SETTING, not an assumption** (owner decision 8/12/26), and the
  **default is "both days" — DECIDED 8/13/26** (owner). An episode from 11pm–2am touches
  two calendar days. "Any overlap counts the day" is what someone filling in a paper diary
  would do, and three things point the same way: it is what the union-of-calendar-days rule
  two bullets above already computes (so the default needs no special case — "only the day
  it started" is the deviation, implemented as a *narrowing* of the union), it errs away
  from undercounting, and undercounting is the direction that costs a user their
  authorisation at the ≥4/≥8/≥15 thresholds. Inflation at the margins is the milder failure:
  it is visible to a clinician reading the diary, whereas a missing day is not.
  The setting stays, because a user whose insurer counts start-day-only must be able to say
  so — the system should not guess on their behalf (same principle as G3's attestation).
  Exposed in diary mode (G3) as: **"An episode that runs past midnight counts as — [both
  days / only the day it started]."** Whichever is chosen, **state it on the export** so the
  reader knows the rule behind the number.
- **Severity when a day has several episodes → max.** The calendar form's own wording
  ("the worst pain you have experienced") supports this.
- **Scale mismatch.** The form is 0–10 where 0 = no pain; Baseline scale symptoms are
  1–10. Blank ≠ 0.
- **Which symptom is "the" score** when the user filters by a tracked item — the
  filtered symptom's score, by definition. This is why filtering by tracked item is the
  right primitive.
- **Period (P column)** — "Hormonal / menstrual cycle" is a seeded *trigger*, but a
  trigger attaches to an *episode*, and a period spans days with or without episodes.
  Baseline cannot fill this column from its own data. Leave it blank rather than infer
  — but it is a natural **Apple Health import** once HealthKit lands (already on the
  roadmap), offered as one of the diary-mode pre-print questions (G3).

### G5 — Out of scope, named so it isn't rediscovered
- **MIDAS / HIT-6** are validated instruments (scored questionnaires, MIDAS over a
  3-month recall). They are normally administered *by the clinician*, not derived from a
  diary. Capturing them would need their own table. Not now.
- **Sending to / sharing with a physician** is long-term (Missy 8/12/26). If it ever
  becomes a shared link, the token pattern already exists (`UsedVerifyToken`), and it is
  a privacy decision (health data leaving the app) before it is a technical one.

---

## Sequencing

**Before the rebuild** (data capture — unrecoverable if skipped). **Three migrations, not
two** — G1 moved into this list when verification showed `ProtocolEvent.detail` was already
occupied (see G1):
1. G2 — `Protocol.med_class` (nullable, vocabulary selected by `type`).
2. G3 — the `User` diary-mode boolean (`ai_logging_enabled` / `email_updates_enabled`
   precedent) plus the midnight-boundary setting, defaulting to "both days".
3. G1 — `ProtocolEvent.stop_reason` + `stop_reason_note`, captured on the status-change
   form. Highest clinical value of the three.
4. G4 rules recorded in CONVENTIONS.

All three are additive nullable columns, so they follow the existing `run_migrations()`
ALTER pattern (CONVENTIONS → Migrations) and can land in one increment.

**During / after the rebuild** (rendering and querying — throwaway if built now):
4. The diary query itself (filter by tracked item, date range, monthly counts).
5. The output. Probably a **print-styled HTML page**, not a PDF library — the target is
   a fixed grid, and browsers already print. Revisit at build time.

## Notes

- **"Is episode length useful?" — answered: yes.** An earlier draft of this spec said
  duration wasn't needed because insurers ask for *days*, not hours. That was wrong:
  **you cannot compute days without it.** A 76-hour episode is four headache days, and
  counting it as one undercounts by three against a threshold that gates treatment.
  This closes the open Needs-Investigation question and **unblocks the "AI check-in —
  episode duration capture" follow-up**, which was gated on it. Note the answer arrived
  from insurance requirements, not from clinical usefulness — the original framing
  ("does an end-time change any decision?") was looking in the wrong place.
- Everything clinician-facing is **descriptive only, never causal** (owner decision
  8/12/26). Report counts, trends and timelines; never "X reduced your migraines." The
  calendar form has nowhere to put a causal claim, which makes this easy to honour.
