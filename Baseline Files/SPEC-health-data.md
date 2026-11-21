# Spec — Storing Apple Health data (HRV, blood glucose)

Status: **Owner-approved 11/21/26 (H1–H6, Q1–Q4, §5a). Storage + upload endpoint built 11/21/26 (§4 steps 1–4).** Display summaries and precursor windows are React / step 7. Step 2 of the Apple Health plan
(BACKLOG "Apple Health sync — invite-only iOS app"; Decision Log 11/21/26). · Owner: Missy

**Goal.** Decide how readings synced from Apple Health are stored, so that step 4 (the minimal
iOS app) has an endpoint to send them to, and step 7 (HRV/glucose correlation with episodes) has
the data it needs. Built on `SPEC-tracked-values.md` (Number-with-unit items, daily entries).
**Repo is public:** examples are generic; no user's readings, device names or item names go in
code, seeds, tests or fixtures.

**What already exists (step 1, live 11/21/26):** `/api/v1` with phone sign-in tokens,
`users.health_sync_enabled` (admin-set), `health_sync_required` (flag + current privacy consent),
and `GET /api/v1/health/sync-status`. Carry-overs from the step-1 code review: the upload needs a
request-size cap (`MAX_CONTENT_LENGTH` is not set today) and its own rate limit; day boundaries
come from `user_today()` / `user_tz_name()` (they work in API routes).

---

## 1. What Apple Health gives us (facts the design rests on)

- **Each reading is a sample** with a unique id (UUID), a start and end time, a value in a unit
  the app asks for, and the app or device that wrote it. Samples are never edited in place: a
  change is a delete plus a new sample.
- **HRV** (`heartRateVariabilitySDNN`, milliseconds). Apple Watch takes readings at irregular
  times, more of them overnight. A few to a few dozen a day.
- **Blood glucose** (`bloodGlucose`, mg/dL or mmol/L — the app asks for mg/dL and Health converts).
  A finger-stick meter adds a handful a day; a continuous glucose monitor (CGM) adds a reading
  every ~5 minutes, **~288 a day**. *To confirm when building the app:* some CGM apps write to
  Health on a delay (reportedly up to a few hours), so recent readings can arrive late.
- **The phone can ask "what changed since last time"** (an anchored query): new samples **and the
  ids of deleted ones**. The phone keeps that bookmark (anchor) itself; the server needs none.
- **Background sync** wakes the app when new data arrives, but iOS decides how often. The server
  must accept late, repeated and out-of-order uploads.

**Volume:** five CGM users ≈ 525,000 glucose readings a year — small for PostgreSQL (tens of MB
with indexes), and it rides the existing nightly backup and restore drill unchanged.

---

## 2. Decisions (options · short-term · long-term · recommendation)

### H1 — Where synced readings live, and when they join "What I Track"
- **(a) Build the tracked-values `entries` tables now; each reading (or a daily value) is an entry
  of a system-managed Number item.** *Short:* the Health path uses the final shape from day one.
  *Long:* pre-stages part of `SPEC-tracked-values.md` in the monolith, which its D8 decided
  against; the entries shape gets locked before the React screens that use it are designed; and a
  CGM's ~288 rows a day flood a table built for things people log by hand.
- **(b) A raw table for synced readings now (`health_samples`); daily values become entries of a
  system-managed Number item when the React build creates the entries tables.** *Short:* one
  small, self-contained table; nothing in the monolith reads it (until step 6 nothing displays
  Health data anyway — the iOS app only shows sync status). *Long:* raw readings stay the source
  of truth, so a daily value can always be recomputed (a deleted reading, a changed summary rule),
  and step 7 can look at readings in the hours before an episode's onset — which daily values
  alone can't do.
- **(c) Daily values only, no raw readings.** *Short:* the smallest table. *Long:* loses "glucose
  in the 6 hours before onset" — the point of correlating with episodes — and a deleted reading
  can't be taken back out of a day.
- **Recommendation: (b).** It honours tracked-values D8 (no pre-staging), keeps the monolith
  untouched, and keeps the raw data correlation needs.

### H2 — One row per reading, identified by Apple's id (safe re-sync)
- Unique `(user_id, hk_uuid)`. An upload **upserts**: a reading sent twice (retry, reinstall,
  overlapping batches) is stored once. This also gives the upload endpoint the idempotency the
  DB-backed-idempotency-keys row asks for, with no keys table.
- **Deleted in Health → deleted here.** The upload carries the ids the phone reports as deleted;
  the server **hard-deletes** them (the user removed them; privacy-first). No tombstones needed:
  the phone's anchor already knows what it has sent.

### H3 — Units and plausible values
- **One canonical unit per kind:** HRV in ms, glucose in mg/dL. The phone asks Health for those
  units; the server rejects anything else rather than converting. No `unit` column (the kind
  implies it).
- **Range check** on the server: HRV 1–300 ms; glucose 10–1,000 mg/dL. Out of range → that
  reading is rejected and counted in the response, never silently clamped.
- Showing mmol/L to a user is a display choice for later (React), not a storage one.

### H4 — Which day a reading belongs to
- Stored at write time as `local_date`, from the account's zone (`user_tz_name()`, which in an API
  request uses the stored zone) — the same rule as tracked-values D4 and the protocol log
  (Rule 3). A later zone change never moves old readings.
- Overnight HRV that starts before midnight counts for the day it started. Stated, not hidden.
  (Whether "last night's HRV" should belong to the next morning is a summary question — Q2.)

### H5 — The upload endpoint
- `POST /api/v1/health/samples`, behind `health_sync_required` (and, from step 3, the Health
  consent).
  ```
  { "kind": "hrv_sdnn" | "blood_glucose",
    "samples": [ { "id": "<uuid>", "start": "<ISO 8601>", "end": "<ISO 8601>",
                   "value": 42.0, "source": "<app/device name, optional>" } ],
    "deleted": [ "<uuid>", ... ] }
  → { "ok": true, "stored": n, "already_had": n, "deleted": n, "rejected": [ {id, reason} ] }
  ```
- **Limits:** at most 500 samples + 500 deleted ids per request; an app-wide
  `MAX_CONTENT_LENGTH` of 2 MB (far above any web form; a JSON 413 under `/api/`); a per-token
  rate limit (e.g. 600/hour — a first backfill of a year of CGM data is ~210 requests).
- **Rule 2:** a 500-row upsert is a fast database write with no external call, so it runs in the
  request. If summaries are later computed on write, that moves to the worker once Redis exists.
- **Response** lets the app show "last synced" and lets `GET /api/v1/health/sync-status` report
  `last_synced_at` (the newest `created_at` for that user — no extra table).

### H6 — Which kinds in v1
- `kind` ∈ `hrv_sdnn | blood_glucose`, a DB CHECK like `stop_reason`. Adding a kind later (resting
  heart rate, sleep) is one vocabulary change plus the app asking for it — no new table.
- **Source** (`source_name`, optional, ≤100 chars): which app or device wrote the reading. Lets a
  summary tell a CGM apart from a finger-stick entered by hand (Q2) and lets a doctor summary say
  where numbers came from. It can name a device brand — covered by the step-3 privacy wording.

### H7 — How far back the first sync goes, and what happens when sync stops
Owner questions — see §5 (Q1, Q3).

---

## 3. Proposed data model (for review — names provisional)

```
health_samples   id, user_id → users CASCADE,
                 kind        CHECK IN ('hrv_sdnn','blood_glucose'),
                 hk_uuid     VARCHAR(36)  -- Apple's sample id
                 value       FLOAT        -- ms (hrv_sdnn) or mg/dL (blood_glucose)
                 start_at, end_at  (UTC, naive — matches the codebase)
                 local_date  DATE         -- day in the account's zone at write (H4)
                 source_name VARCHAR(100) NULL
                 created_at  (UTC)
                 UNIQUE (user_id, hk_uuid)
                 INDEX  (user_id, kind, start_at)
```

- New FK goes into `EXPECTED_FK_ONDELETE` (`CASCADE`), with a `passive_deletes=True`
  relationship on `User` — account deletion removes every reading with no extra code.
- New table → created by `create_all` in `init-db`; no migration of existing data. The `kind`
  CHECK follows CONVENTIONS "a new column that carries a CHECK" (predicate defined once in
  `database.py`).
- **Later, in React (tracked-values build):** each kind becomes a system-managed Number item per
  user (unit locked, not editable, not written by the check-in), with one daily entry per day,
  `source='apple_health'`, computed from `health_samples` by the summary rules in Q2. Raw rows stay.

## 4. Build order and tests (when approved)

1. Model + FK matrix entry + `MAX_CONTENT_LENGTH` + JSON 413 under `/api/`.
2. `POST /api/v1/health/samples` (validation, upsert, deletes, limits) and `last_synced_at` in
   `sync-status`.
3. Tests (`test_health_samples.py`, route-level, Postgres in CI): store; re-send is a no-op;
   deletes; wrong kind/unit/range rejected and reported; batch and size limits (413); rate limit;
   flag off / consent outdated → 403; **user A's token never touches user B's rows** (QA carry-over);
   account delete cascades; `local_date` follows the stored zone.
4. Docs: TECHNICAL_README, CONVENTIONS (API routes), CLAUDE.md data models, BACKUPS.md (new table
   is in the dump and drill automatically — confirm on the first drill after deploy).
5. Ships to staging/production like step 1 — but **no phone sends data until step 4's app exists
   and step 3's consent is live**, so nothing real is stored before the privacy wording is approved.

## 5. Owner questions

- **Q1 — First-sync history.** How far back the first sync reaches: **90 days** (enough for a
  first comparison; ~26k CGM readings), **1 year** (seasonal patterns, a full baseline; ~105k),
  or **everything Health has**. Recommendation: **1 year** — correlation needs a baseline, and the
  volume is small.
- **Q2 — Daily summaries (needed in React, recorded now).** HRV: **average of the day's readings**
  (simple, explainable) vs **overnight readings only** (steadier, but needs sleep data or a fixed
  window). Glucose: **daily average + % time in range 70–180 mg/dL** (the standard CGM measure)
  vs average only. Recommendation: HRV daily average; glucose average + time in range; revisit
  after seeing real data.
- **Q3 — When sync stops.** If an admin turns Health sync off, or (from step 3) the user withdraws
  Health consent: **keep the readings** (sync just stops) or **delete them**. Recommendation: flag
  off → keep (it's an access switch); consent withdrawn → delete the readings, and say so in the
  consent text — that is what withdrawing consent for health data should mean (WA MHMD).
- **Q4 — Glucose units on screen.** mg/dL only for now (US circle), mmol/L as a per-user display
  setting later. Recommendation: mg/dL only now.

## 5a. Precursor analysis — why daily averages aren't enough (owner question, 11/21/26)

**Owner:** "If we're looking to see if HRV or CGM drops or spikes might be a precursor to a
migraine, will averages be enough data?" **No.** A daily average blends the hours before an
episode with the rest of the day, so a dip at 3 a.m. or a spike after lunch disappears. Two
different things are needed, and only the first is a "summary":

1. **Display summaries** (React dashboard, doctor summary): one number per day — HRV daily
   average; glucose daily average + % time in range 70–180 mg/dL. Good for trends and a doctor,
   not for finding precursors.
2. **Precursor windows** (step 7): for every episode, look at the raw readings in a window
   *before its onset* and compare them with the same person's normal:
   - **Windows:** e.g. the 6, 12 and 24 hours before onset; for HRV, the night before.
   - **Compared against:** the same person's readings at the same time of day on days with no
     episode (control windows), or a rolling 30-day personal baseline. HRV varies a lot between
     people, so only a change *from your own normal* means anything.
   - **Glucose features:** lowest and highest value, any reading below 70 or above 180 (or the
     user's own thresholds), the biggest rise or fall per hour, variability (standard deviation /
     coefficient of variation).
   - **HRV features:** the window's average vs personal baseline; the drop in % from baseline.
   - **Honest statistics:** a pattern needs enough episodes before it's shown, and it's shown as
     a *hypothesis to test*, never a conclusion — the stats-first pattern-detection rule
     (BACKLOG "Pattern detection — stats-first, then narrate"). The natural next step is an
     experiment.
3. **What this needs from storage:** raw readings with times (H1 (b) keeps them — this is why
   daily-values-only (c) was rejected), and **accurate episode onsets** (vague check-in times
   already get an "adjust the time" nudge). It also depends on **episodes counted by kind**
   (BACKLOG React row): a migraine and a hot flash may have different precursors, so step 7
   needs to know which kind each episode is.

Nothing in H1–H6 changes. Recommendation for Q2: display summaries as above, **plus** precursor
windows in step 7 as described here.

## 6. Owner decisions (11/21/26)
- **H1 — storage:** (b) approved — raw `health_samples` table now; daily values join "What I
  Track" as system-managed Number items in the React build. H2–H6 as recommended.
- **Q1 — first sync:** 1 year of history.
- **Q3 — when sync stops:** admin flag off keeps readings (sync just stops); withdrawing Health
  consent (step 3) deletes them, and the consent text says so.
- **Q4 — units:** mg/dL only for now.
- **Q2 — summaries:** owner asked whether averages are enough to find precursors; answered in §5a
  (display summaries + precursor windows) — **approved 11/21/26**.

## 7. As built (11/21/26) — differences from the draft
- **Lower time bound:** readings starting before 2000-01-01 are rejected (`too_old`); times that
  overflow on conversion are `bad_time` (code review: they were a 500 that lost the whole batch).
- **Day boundary:** the account's **stored** zone only — API routes ignore cookies, including
  `baseline_tz` (QA: a cookie could shift the day). No stored zone → UTC days. Users register on
  the web, which stores the zone; **step 4 (iOS app) should send the phone's zone at sign-in** so a
  phone-first account never falls back to UTC before step 7 relies on `local_date`.
- **Safety net:** any unexpected error on one reading rejects that reading (`invalid`), never the
  batch; logs carry the error type only, never readings or SQL (review F5).
- **Races:** rows inserted sorted by id (no PostgreSQL deadlock between concurrent batches); a failed
  commit retries once, then 409 `conflict`.
- **Rate limit** keyed on the token itself (header spacing/case variants share one bucket); it runs
  after the token and Health-sync checks, so refused requests don't spend it.
- **Adding a kind** needs a migration that drops and re-adds `health_samples_kind_check`
  (create_all never alters an existing constraint).
