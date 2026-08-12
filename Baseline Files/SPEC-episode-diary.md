# Spec — Episode Diary (physician / insurance export)

Status: **draft, schema-identification only** · Started 8/12/26 · Owner: Missy

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

### G1 — Reason a protocol was stopped  ← highest value, likely **no migration**
Step therapy documentation explicitly requires *why* each preventive was discontinued
("ineffective", "side effects", "cost"). Baseline captures nothing today: a protocol
stopped outside an experiment records only that it stopped.

**`ProtocolEvent.detail` (Text) already exists** and is currently written only for
`dose_changed`. A stop/pause reason fits it exactly — the same finding as the effective
date (the column existed; nothing wrote to it). Verify before assuming, but the likely
answer is **capture-only, no migration**: a reason field on the status-change form,
written to `detail`.

Open question: free text, or a short controlled list (ineffective / side effects /
cost / doctor's advice / other + note)? A controlled list is far more useful for a
document an insurer reads, and mirrors the existing `input_type`-style CHECK constraint
precedent. **Recommend controlled list + optional note.**

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
- **Boundary rule is a user SETTING, not an assumption** (owner decision 8/12/26). An
  episode from 11pm–2am touches two calendar days. "Any overlap counts the day" is what
  someone filling in a paper diary would do, but it inflates at the margins — the
  opposite risk to undercounting. Rather than pick for the user, expose it in diary
  mode (G3): **"An episode that runs past midnight counts as — [both days / only the day
  it started]."** Default: both days. The user knows how their clinician and insurer
  count; the system shouldn't guess, and the same attestation logic applies as for
  blank days. Whichever is chosen, **state it on the export** so the reader knows the
  rule behind the number.
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

**Before the rebuild** (data capture — unrecoverable if skipped):
1. G2 `Protocol.med_class` and G3's `User` diary-mode flag — the two migrations.
2. G1 stop reason — verify `ProtocolEvent.detail` suffices, then capture.
3. G4 rules recorded in CONVENTIONS.

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
