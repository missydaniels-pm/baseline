# Privacy policy update — APPLIED 10/19/26 (owner-approved; kept as the rationale record)

> **Status:** approved by Missy 10/19/26 and applied to `templates/privacy.html` (items 1–4, 6) and `templates/register.html` (item 5, consent wording as recommended). §6 "Your Rights" also got a pointer to the 30-day backup note so it agrees with §7. The text below is the original proposal.

Drafted 10/19/26 for BACKLOG "Privacy posture" part (a). **Nothing here is published** —
`templates/privacy.html` is unchanged until you approve. Legal/WA MHMD implications: this
is a plain-language accuracy pass, not legal advice. If you want a lawyer's eye on anything,
items 1 and 5 are the ones.

Each change: **what's wrong today** → **proposed wording** (drop-in for the matching section).

---

## 1. Backups — Section 7 "Data Retention" is now inaccurate  *(most important)*

**Today:** "When you delete your account, all data … is permanently removed from our database."
Since 9/25/26 a nightly **encrypted backup** of the production database goes to Cloudflare R2
and each copy is kept **30 days**. A deleted account is gone from the database at once but
survives in backups for up to 30 days.

**Proposed (replace the Section 7 paragraph):**
> Your data is retained for as long as your account exists. When you delete your account, all
> data — episodes, protocols, experiments, compliance logs, check-in history, usage activity
> logs, and tracking records — is immediately and permanently removed from our live database.
> For disaster recovery we keep encrypted nightly backups of the database for 30 days; a
> deleted account's data remains only in those encrypted backups until they expire, at most
> 30 days after deletion, and is never restored into the live service except to recover from
> a failure of the whole system.

## 2. Processors — Section 4 "Third-Party Services" is missing Cloudflare

**Today:** names Anthropic, Resend, Railway. Missing: **Cloudflare** — (a) all traffic to
mybaselineapp.com passes through Cloudflare's network (DNS/proxy), and (b) the encrypted
backups are stored in Cloudflare R2.

**Proposed (add a bullet):**
> **Cloudflare (network and backup storage):** requests to mybaselineapp.com pass through
> Cloudflare's network, which processes standard connection data (IP addresses, request
> metadata) to deliver and protect the site. Our encrypted database backups are stored in
> Cloudflare R2; they are encrypted before they leave our servers, and Cloudflare does not hold
> the key. See Cloudflare's privacy policy.

**Section 5 "Data Storage and Security" — add:**
> Database backups are encrypted (AES-256) before storage and kept for 30 days.

## 3. What we collect — Section 1 is missing newer data

**Today:** "episodes (severity scores, duration, notes), protocols, experiments,
interventions, and daily compliance logs." Missing: **triggers** (since July), **how an
experiment ended** (reason, end date — since 10/19/26), and the items you choose to track.

**Proposed:**
> **Health data you enter:** the symptoms and other items you choose to track; episodes (scores,
> duration, how much they affected your day, notes, and possible triggers); protocols and
> interventions; experiments and their outcomes, including why an experiment was ended; and
> daily compliance logs.

## 4. Doctor summary — worth one sentence (new feature)

**Proposed (Section 2 "How We Use Your Data", new bullet):**
> To produce a doctor summary when you ask for one. It is generated on request from your own
> data and shown only to you; Baseline does not send it anywhere — you decide whether to print,
> save or share it. It is not stored by your browser or the app's offline cache.

## 5. Consent wording — a question for you, not a rewrite

Registration has one checkbox: "I acknowledge the Privacy Policy." WA MHMD asks for **consent to
collect** consumer health data (and separate consent to *share* it). Two things to decide:

- Is "acknowledge" enough, or should the checkbox read as consent — e.g. *"I consent to Baseline
  collecting and storing the health information I enter, as described in the Privacy Policy"*?
- AI logging already has a separate opt-in (the one place health text is shared — with
  Anthropic). That looks like the right shape for "separate consent to share"; worth confirming.

My recommendation: change the checkbox to consent wording (one-line template change + a
server-side check that already exists), and add a dated "Effective" line. I haven't changed it.

## 6. Housekeeping

- "Effective April 24, 2026" → the approval date.
- Section 5: logs on Railway may include your **user id** with an error (never your email —
  changed 10/19/26). The existing Railway bullet already covers "standard server logs"; no
  change strictly needed.

---

**To approve:** say which items to apply (e.g. "apply 1–4 and 6; checkbox wording as you
proposed"), and I'll make the template change and sweep the help page and welcome email in the
same commit.
