# Baseline — End-of-Life Register

Last updated: October 4, 2026 · Decision record: BACKLOG Decision Log "End-of-life register"

**Why this exists.** Python 3.10 reached end-of-life on 2026-10-01 while production
was still running it, and nothing warned: Dependabot and vulnerability alerts track
*library* releases, not *runtime* support windows. This register lists every
component Baseline depends on, when it stops getting security fixes, and how we'll
hear about it.

**How it's checked.**
- **Automated (weekly):** `.github/workflows/eol-check.yml` runs `scripts/eol_check.py`,
  which asks [endoflife.date](https://endoflife.date) and **fails within 180 days** of
  any listed end-of-life (a failed run emails the owner). Python and PostgreSQL
  versions are read from the Dockerfiles, so the check follows what actually ships.
- **Caveats:** scheduled workflows run only from `main`, and GitHub pauses them after
  60 days with no repo activity — re-enable from the Actions tab after a quiet stretch.
  A failed run emails whoever last edited the workflow's schedule line.
- **Dated (no API can check these):** the same dates as the model-retirement checks in
  BACKLOG — **2027-01-04 · 2027-04-05 · 2027-07-05 · 2027-10-04** — walk the
  "Manual" table below.

## Automated

| Component | Version | End of life | Version comes from |
|---|---|---|---|
| Python | 3.14 | 2030-10-31 | `Dockerfile` `FROM python:3.14-slim` |
| PostgreSQL | 17 | 2029-11-08 | `backups/Dockerfile` `FROM postgres:17` — the Railway database server is also 17 (17.11, checked 10/4/26); they must stay the same major |
| Debian | 13 "trixie" | 2030-06-30 (LTS end; Debian's own security team stops 2028-08-09) | **Detected weekly** by running the `python:3.14-slim` image the Dockerfile names. Docker keeps rebuilding `-slim` images while Debian LTS ships fixes, so the LTS date is the one that matters |
| Ubuntu (CI runner) | 24.04 | 2029-05-31 (standard support) | **Detected weekly** from the runner's `/etc/os-release`, so GitHub's label move (below) updates it automatically |

## Manual (check on the dated reviews)

| Item | Date / status | What to do |
|---|---|---|
| GitHub `ubuntu-latest` → Ubuntu 26.04 | Migration begins **2026-10-19** (announced in CI warnings) | The EOL check follows it automatically; just confirm the `tests` workflow is still green afterwards. |
| Railway `railway.json` (Config as Code) | **Stops being read 2026-12-01** | Nothing should depend on it (service settings are dashboard-only — BACKUPS.md). Confirm before 12/1; evaluate Railway's replacement (BACKLOG row). |
| Claude model in the check-in (`claude-sonnet-4-6` in `parse_checkin()`) | Pinned; retired on Anthropic's schedule | Already a dated check in BACKLOG (model-retirement row). |
| Domain `mybaselineapp.com` | Registered at **Squarespace**, auto-renew on, **renews 2028-03-05** (owner, 10/4/26) | Auto-renew only works if the card on file is valid when it charges: **by 2028-02-05, confirm the Squarespace payment method** and that auto-renew is still on, then record the next expiry here. If it lapses, the whole app is down and no monitor here would explain why. |
| TLS certificate | Automatic (Cloudflare / Railway) | Nothing, unless the uptime check fails. |
| Python libraries (Flask, SQLAlchemy, gunicorn, …) | Pinned in `requirements.txt` | Dependabot weekly PRs + vulnerability alerts; major versions reviewed one at a time. |
| GitHub Actions (`checkout@v4`, `setup-python@v5`) | Pinned majors | Dependabot monthly PRs. |
| App Store / Play Store policies (rebuild) | Not yet applicable | Add when the native apps are planned (minimum OS versions, SDK target deadlines). |

## When something comes due
Treat it like the Python move: decide the target version with a short trade-off
(BACKLOG Decision Log), change the Dockerfile and CI together, regenerate the
lockfile, run every suite on the new version, and ship through staging.
