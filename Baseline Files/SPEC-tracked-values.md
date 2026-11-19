# Spec — Flexible "What I Track" (value types, daily entries, definitions)

Status: **Owner-approved 11/19/26 (D1–D8 and Q1–Q5, see §9). Nothing built.** Built in the React
rebuild (owner decision 11/19/26). Monolith work: none planned. · Owner: Missy

Absorbs the BACKLOG row "Daily wellbeing check — energy + mood (non-episodic)" (9/29/26). That
row's open decisions (a)–(d) are answered here.

**Goal.** Any user can track a new kind of measure without code changes. Nothing in this spec is
specific to one condition or one user's items. **Repo is public:** examples here are generic, and
no user's real item names, level definitions or example phrases go in code, seeds, tests or
fixtures.

---

## 1. What exists today (investigated 11/19/26)

**Storage**
- `Symptom` (`symptoms`): `name`, `description` (≤500 chars, free text), `is_active`,
  `baseline_score` (1–10, scale only), `input_type` ∈ `scale | binary` (DB CHECK
  `symptoms_input_type_check`). The type is locked once any entry exists.
- `SymptomScore` (`symptom_scores`): `episode_id` **NOT NULL**, `symptom_id`, and exactly one of
  `score` (1–10) or `value_bool`.
- **Every entry belongs to an episode.** There is no daily or standalone entry, so "how many
  entries a day" has no answer today. An item gets **at most one value per episode** (the form has
  one input per item; the check-in sends one `symptom_scores` object per item).
- **Every 1–10 scale reads as "higher is worse"** (severity): the dashboard's "vs baseline"
  trend, "Severity by Week", and help.html all assume it.

**Readers** (`app.py` line numbers as of 11/19/26)

| Reader | Where | What it does with entries today |
|---|---|---|
| Item CRUD + type lock | `/symptoms/new`, `/symptoms/<id>/edit` (~3245–3320) | create, edit, lock type once scored |
| Onboarding | `onboarding_step1` / `step2` (~2140–2230) | creates items; step 2 sets `baseline_score` for scale items |
| Episode form | `_validate_episode_form` (~1294), `_save_symptom_entries_from_form` (~4220), `new_episode` / `edit_episode` | slider for scale, Yes/No for binary |
| AI check-in | `parse_checkin` prompt (~2645), `CHECKIN_SCHEMA.symptom_scores` (~2851), write path (~3035) | lists items with type + description; the model returns `{symptom_id, value}` inside `episode_data`, so values are only logged when an episode is logged |
| Dashboard cards | `index` (~3910) | scale: month average vs baseline, with a trend arrow. Binary: yes/no count this month |
| Dashboard charts | `index` (~3990–4045) | weekly average line per scale item; dot strip of the last 20 episodes per binary item |
| Experiment assessment | `assess_experiment` (~3665–3740) | per item: average before vs during (scale), % yes (binary); weekly chart (scale only) |
| Doctor summary | `build_doctor_summary` (~5160) | scale: average; binary: % yes, over episodes in range |
| Seed data | `seed_demo_data` (~5530, 5640) | 2 scale + 1 binary item, a score on every episode |
| Help surfaces | help.html, welcome tour, onboarding copy, welcome email | describe Scale and Yes/No |

There is **no separate daily view or weekly summary page** today. "Weekly" exists only as chart
buckets on the dashboard and the experiment page. The rebuild defines those views (§6).

---

## 2. Requirements (owner, 11/19/26)

1. Keep **Scale (1–10)** and **Yes/No** exactly as they are, including the type lock.
2. Add three types: **Named levels** (an ordered list of labels, each with a definition),
   **Number with a unit**, and **Set** (named values logged together, e.g. start, end, minutes,
   optional note).
3. Each item says **how often it can be logged**: once a day, or any number of times a day.
   Existing items keep their current behavior.
4. Definitions the check-in can use: build on Description; add a definition per level and one or
   two optional example phrases per item. When the check-in isn't sure, it doesn't guess.
5. Every reader is type-aware: the logging form, daily/weekly views, experiments and before/after
   comparisons. For a Set, the user picks what to compare, including a difference between two
   values.
6. The architecture rules hold: stateless (Rule 1), slow work off the request (Rule 2), converge
   on existing patterns (Rule 3).

---

## 3. Decisions (options · short-term · long-term · recommendation)

### D1 — Where entries live
- **(a) Keep `symptom_scores` on episodes; add a second table for standalone entries.**
  *Short:* nothing existing moves. *Long:* every reader queries two tables, and "what is the value
  of item X" gets two answers. That is the Rule 3 drift this project has already paid for once
  (the 7/14 timezone bug).
- **(b) One entries table for every tracked value; `episode_id` optional.** Existing scores are
  copied in once. *Short:* a one-time migration. *Long:* one read path, and an entry can be
  standalone or tied to an episode.
- **(c) Make `symptom_scores.episode_id` nullable and add date columns in place.** *Short:* the
  smallest diff. *Long:* the per-type value columns (score, value_bool, …) keep widening with
  every new type, and Set values don't fit in one row.
- **Recommendation: (b).** The rebuild rewrites every reader anyway, so the cost of one migration
  is paid once, while the cost of (a) would be paid forever.

### D2 — How values are stored
- **(a) A JSON column per entry.** Flexible, but averages, comparisons and the Set "end − start"
  measure become JSON queries that differ between SQLite and PostgreSQL. The DB also can't check
  the values.
- **(b) One value row per entry and field** (`entry_values`), with typed columns (`num_value`,
  `bool_value`, `level_id`, `text_value`) and a CHECK that exactly one is set. Plain SQL
  aggregates work the same on both engines. Same "exactly one column, never mixed" rule as
  `symptom_scores` today.
- **Recommendation: (b).** Scale, Yes/No, Named levels and Number store one value row; a Set
  stores one row per field.

### D3 — Item definitions
New child tables of the item:
- `item_levels`: `position` (order), `label`, `definition`, `color` (from a fixed palette that
  works on the dark theme; the label is always shown too, so meaning never depends on color),
  `is_active`.
- `item_fields` (Set fields, and the single field of a Number item): `position`, `name`, `kind`
  (`number | text`), `unit` (free text, e.g. °F, minutes), `required`, `is_active`.
- **Examples:** one `examples` text column on the item, up to two lines, validated in the app.
  A separate table would be overkill.
- **Direction** (from the 9/29 row): an item-level `higher_is` ∈ `worse | better | neutral` for
  Scale and Number. Existing items migrate as `worse`, so every chart reads exactly as today.
  Named levels get direction from their order (first level = best or worst, chosen once).

### D4 — How often an item can be logged
- `frequency` ∈ `with_episode | once_per_day | any`. **Existing items migrate as
  `with_episode`**, so nothing changes for current users.
- "Once a day" is enforced in the DB the way the daily protocol log is
  (`ux_protocol_compliance_day`): a `day_key` column holds the local date for once-a-day items
  and NULL otherwise, with a unique index on `(item_id, day_key)`. NULLs never collide, so
  "any number" items are unaffected. A second entry on the same day **replaces** the first (the
  latest statement wins, same as `_upsert_compliance()`).
- **Each entry stores its local date when it is written**, resolved through `user_today()` /
  `user_tz_name()`. A later time-zone change then never moves past entries to another day.

### D5 — What locks once an item has entries
- Type: locked (unchanged rule).
- Level labels and definitions: can be renamed freely. Levels **can't be removed or reordered**;
  pause one instead (`is_active=False`, like triggers), so old entries still show their label.
- Set fields and a Number's unit: the name can change; the unit and kind lock. New optional fields
  can be added; existing fields can be paused, not deleted.
- Frequency: can change from `any` → `once_per_day` only if no day already has two entries
  (otherwise the app says which days to resolve first).

### D6 — Check-in
- The schema gets a top-level **`entries` array, separate from `episode_data`**. "Low-energy day,
  did a session from 10 to 14 in 20 min" logs two entries and **no episode**. Today such a message
  could only log values by creating an episode.
- The prompt lists each active item: type, description, frequency, levels with definitions,
  fields with units, and examples.
- **When it isn't sure → suggest and confirm** (the trigger precedent, owner 7/15/26). The model
  returns uncertain values as suggestions; the reply names them and links to a pre-filled entry
  form, and saving confirms them. Certain matches are logged with `source='ai'`.
  *Alternative:* a clarifying question back in the chat. That is a new multi-turn behavior and
  belongs in the async check-in pipeline, so it isn't part of v1.
- The CAN / CANNOT section and `eval_checkin.py` are updated, with **generic** eval cases.
- Rule 2: the rebuild's async check-in pipeline carries this. No new in-request slow work.
- **Privacy:** level definitions and examples are sent to Anthropic, the same as descriptions are
  today. Before shipping, check that the privacy policy's wording covers them (it may name
  "descriptions"; MHMD review if the wording changes).

### D7 — Comparisons and experiments
A comparison needs one number per entry, so each type has a defined **measure**:

| Type | Measure per entry | Shown as |
|---|---|---|
| Scale | the score | average before vs during (unchanged) |
| Yes/No | yes = 1 | % yes (unchanged) |
| Number | the value | average, in its unit |
| Named levels | — | **% of days at each level**, before vs during (not an average of positions, which treats unequal steps as equal) |
| Set | one field, **or the difference of two fields** (e.g. end − start) | sessions per week + average of the chosen measure |

- An experiment stores its chosen measure for a Set item: `(item_id, field_a_id, field_b_id
  nullable)`. B empty = field A alone; B set = A − B. Only a difference, no general formulas, in
  v1.
- `with_episode` items keep comparing over episodes. Daily items compare over **days** in the
  window. Days with no entry are "not logged", never "zero" (same open question as compliance:
  unknown vs assumed).

### D8 — Pre-stage the schema in the monolith?
The episode diary did this (Layer A). **Recommendation: no.** Layer A only added empty columns.
This change moves existing rows, and every monolith reader would have to switch to the new table
at the same moment, which is the reader work you decided to leave for React. Build schema,
migration and readers together in the rebuild.

---

## 4. Proposed data model (for review — names provisional)

```
tracked_items   (today's `symptoms`, extended in place — keeps ids, FKs, the type lock)
  + input_type   CHECK IN ('scale','binary','levels','number','set')
  + frequency    CHECK IN ('with_episode','once_per_day','any')   default 'with_episode'
  + higher_is    CHECK IN ('worse','better','neutral')             default 'worse'
  + examples     TEXT NULL

item_levels     id, item_id → CASCADE, position, label, definition, color, is_active
item_fields     id, item_id → CASCADE, position, name, kind, unit, required, is_active

entries         id, user_id → CASCADE, item_id → CASCADE, episode_id → CASCADE NULL,
                logged_at (UTC), local_date, day_key NULL, source ('user'|'ai'), note NULL,
                created_at
                UNIQUE (item_id, day_key)
entry_values    id, entry_id → CASCADE, field_id → item_fields NULL,
                num_value, bool_value, level_id → item_levels, text_value
                CHECK exactly one value column set
```

New foreign keys go into `EXPECTED_FK_ONDELETE` with `passive_deletes=True` (CONVENTIONS), so
account and episode deletion keep working with no hand-written cleanup. Whether
`level_id` / `field_id` use CASCADE or RESTRICT follows from D5 (they are paused, never deleted).

## 5. Moving existing items and entries

1. **Back up first**: take a manual backup and confirm the latest nightly in R2 (BACKUPS.md).
2. Add the new columns with defaults. Existing items become `frequency='with_episode'`,
   `higher_is='worse'`, `input_type` unchanged.
3. **Copy, don't move:** each `symptom_scores` row → one `entries` row (`episode_id` kept,
   `logged_at` = episode onset, `local_date` = onset in the user's zone, `day_key` NULL) + one
   `entry_values` row (`num_value` = score, or `bool_value`). `symptom_scores` stays untouched.
4. **Verify:** row counts per user and per item match; each item's average and % yes match the old
   ones; `verify_fk_ondelete()` and `check_fk_orphans.py` come back clean.
5. Readers switch to `entries`. `symptom_scores` is dropped in a **later** release, after
   production has run on the new table for a while.
6. Order: local → staging (seeded test account) → production, per the deploy gate.

The rebuild may bring a migration tool (e.g. Alembic) instead of `app.py`'s startup migrations.
That is an infrastructure decision for the rebuild, not this spec.

## 6. What each reader becomes (React)

- **Item setup screen:** the five types; a level editor (label, definition, color, order); a field
  editor (name, unit, number/text, required); frequency; direction; examples. The type lock and
  D5 rules are shown in the UI, not just enforced.
- **Logging:** a slider (Scale), one tap (Yes/No), a picker showing each level's definition
  (Levels), a number field with its unit (Number), a small grouped form (Set). `with_episode`
  items appear on the episode form; daily items appear on a "Today" card beside Today's
  Protocols, with the 7-day backfill precedent.
- **Daily view:** each item's entries for the day; Levels as a colored label; Sets as a list of
  sessions.
- **Weekly summary:** Scale and Number as averages and trends; Yes/No as days hit; Levels as a
  colored label per day; Sets as a session count plus the average of each field.
- **Dashboard cards and charts:** the same rules. "vs baseline" respects `higher_is`. Baselines
  for Number and Named-level items are calculated from the first entries, not asked at setup
  (Q3); the exact window (e.g. first 14 logged days) is set at build time and shown to the user.
- **Experiments:** D7. **Doctor summary:** add the new types with the same measures; Sets show
  session count and averages.
- **Onboarding:** offers only Scale and Yes/No, with a short note that more ways to log are
  available from the "What I Track" menu (Q4, owner 11/19/26). The note's wording is drafted with
  the other help-surface copy.
- **Seed data and tests:** one generic item of every type and frequency.
- **Help surfaces:** help.html, the tour, onboarding copy and the welcome email all get the new
  types.

## 7. Tests (ship with each increment)
- Every type, including the existing Scale and Yes/No: create, log, edit, the lock rules, and the
  once-a-day replace.
- Migration: a fixture DB with scale + binary scores; after migrating, counts and averages match,
  and running it twice changes nothing.
- Check-in: write-path tests without the API (as `_apply_ai_triggers` does): certain → entry,
  unsure → suggestion, never created. `eval_checkin.py` cases for each type, generic wording.
- Deletes: deleting an item, an episode or an account cascades through entries and values.
- Postgres in CI (already in place).

## 8. Before production (owner rule)
On **staging**, with a test account using generic items: show the migration output and
verification counts, a sample parsed check-in, and the daily and weekly views. Then deploy. The
owner sets up their own items through the app screens afterwards, never in code.

## 9. Owner decisions (11/19/26)
**D1–D8 approved as recommended.** Open questions resolved:
- **Q1 — check-in uncertainty:** suggest and confirm (trigger precedent). Clarifying questions in
  the chat may come later with the async check-in pipeline.
- **Q2 — named levels in comparisons:** % of days at each level only; no "mean level."
- **Q3 — baselines for Number and Named-level items:** calculated from the first entries, not asked
  at setup.
- **Q4 — onboarding:** shows only Scale and Yes/No to keep it simple, plus a note that more ways to
  log are available from the "What I Track" menu.
- **Q5 — mixed items:** each item picks one frequency in v1; no item is both "with an episode" and
  daily.
