# Baseline

**A health tracking app for people managing chronic conditions through structured experiment tracking.**

Live at: [baseline-health.up.railway.app](https://baseline-health.up.railway.app) — invite only during early access

---

## What it does

Most health tracking apps are logging tools. Baseline is a hypothesis-testing tool.

The core idea: chronic illness is complex and poorly understood, even by doctors. The people who make the most progress are the ones who treat themselves as a system — establishing a baseline, introducing one change at a time, and measuring outcomes with real data.

Baseline gives users the structure to do that:

- **Symptom tracking** — define what you're measuring
- **Protocol management** — track preventative medications, supplements, and lifestyle changes
- **Episode logging** — record what happens and when, via natural language check-in or manual entry
- **Experiment framework** — form a hypothesis, run a protocol for a defined period, assess the outcome with your own data
- **Dashboard** — visualize patterns, protocol impact markers, rescue effectiveness, and symptom trends over time

Currently in use by 5 people managing chronic migraine, mold toxicity, and MS.

---

## Why I built it

I've had chronic migraine for 20 years. In that time I've kept paper diaries, used tracking apps, and filled out the same insurance forms for neurologists by hand every quarter. I've tried elimination diets, preventatives, rescue medications, and a dozen protocols — usually with no real way to know if anything was working, or why.

The frustrating part isn't the migraine. It's the data problem. Every potential trigger, every medication trial, every pattern — held in my head or scattered across notes with no structure to evaluate it against. Doctors have 15 minutes and no longitudinal picture. Insurance requires documented frequency to approve the medications that actually help. And most tracking apps just make the logging problem slightly more digital without solving the analysis problem at all.

I built Baseline because I needed it. The experiment framework, the protocol compliance model, the 3-week default — these aren't product decisions from user research. They're from two decades of being the patient.

When my stepdaughter was diagnosed with mold toxicity and a close friend with MS, the tool I'd built for myself became something I could give them. That's when it became a real product — not just a personal project.

I'm a product leader by background, not an engineer. I built this entirely using **Claude Code** as my primary development tool, starting from my first terminal session. The build took roughly 6 weeks of evenings and weekends, going from zero to a live production app with real users.

The experience shaped how I think about AI-assisted development — not as autocomplete for engineers, but as a genuine capability multiplier for people who think in systems and problems, not syntax.

---

## Tech stack

- **Backend:** Python 3.10, Flask
- **Database:** PostgreSQL (production), SQLite (local dev) via SQLAlchemy
- **Frontend:** Jinja2 templates, vanilla JavaScript, Chart.js
- **AI:** Anthropic API (Claude) for natural language check-in parsing
- **Auth:** Flask sessions, bcrypt, invite-code registration
- **Hosting:** Railway (auto-deploys from this repo)
- **PWA:** Installable as a home screen app on iOS and Android

---

## Product decisions worth noting

A few places where I made deliberate product calls rather than just technical ones:

**Condition-agnostic by design.** No condition field. Users define their own symptoms and protocols. The same experiment framework works for migraine, autoimmune conditions, or anything else — the structure is the value, not the taxonomy.

**Assumed compliance, exception capture.** The app doesn't ask users to log every pill every day. It assumes they're following their protocols and captures exceptions through the daily check-in. Reduces friction for sick people who have limited energy.

**Experiment default of 3 weeks, not 8.** Early version defaulted to 8 weeks. A user with chronic migraine told me that was too long — by week 3 she already knew something wasn't working. Changed to 3 weeks with user-adjustable duration.

**Invite-only registration.** Health data requires trust. Growth is manual and intentional during early access.

**Privacy-first from day one.** Washington State's My Health MY Data Act applies. Privacy policy written before meaningful user growth. Account deletion built before it was requested.

---

## Architecture notes

The current app is a Flask monolith — server-rendered HTML via Jinja2. This was the right call for getting to a live, tested product quickly.

The planned next chapter is a React frontend rebuild with an API-first Flask backend. That creates the foundation for React Native mobile apps and Apple HealthKit integration, which users are already asking for. The backend is production-ready and won't change; only the frontend layer moves.

---

## What's next

Current focus: retention features for the existing user base — multiple rescues per episode, chart time range controls, neurologist report export (auto-generated PDF for insurance documentation).

Longer term: React rebuild, native mobile, HealthKit integration.

---

## Running locally

```bash
git clone https://github.com/missydaniels-pm/baseline.git
cd baseline

# Create .env with:
# ANTHROPIC_API_KEY=your-key
# SECRET_KEY=any-random-string
# DEBUG=true

./run.sh
# Open http://localhost:5001
# Use /dev/bootstrap to create the first admin account
```

---

## A note on this codebase

This project was built with Claude Code as the primary development tool. I'm a product manager, not a software engineer — my background is in product strategy, API-first architecture, and leading technical teams, not writing production code day to day.

What you'll find here is what happens when someone who thinks deeply about product problems gets access to a capable AI development partner: opinionated decisions about what to build and why, real users giving real feedback, and a system that's evolved through iteration rather than upfront design.

The code quality reflects Claude Code's output. The product decisions reflect mine.

---

*Built by [Missy Daniels](https://linkedin.com/in/missydaniels) — VP of Digital Products @ Teaching Strategies. Previously Amazon. Currently figuring out what AI-assisted product development looks like when it's done seriously.*
