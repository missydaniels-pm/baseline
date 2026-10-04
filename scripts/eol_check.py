#!/usr/bin/env python3
"""End-of-life check for Baseline's runtime components (10/4/26).

Python 3.10 reached end-of-life on 2026-10-01 while production was still on it,
and nothing warned: Dependabot tracks library releases, not runtime support.
This asks endoflife.date (a free public API — it receives only product names
and versions, nothing about users) when each component we run stops getting
security fixes, and fails when one is within WARN_DAYS.

Versions are read from the files that decide them wherever possible, so the
check can't drift from what actually ships:
  - Python      Dockerfile          FROM python:<cycle>-slim
  - PostgreSQL  backups/Dockerfile  FROM postgres:<major>   (the Railway DB
                server must be the same major — see Baseline Files/EOL.md)
Others can't be read from the repo and are declared in MANUAL below; keep them
in step with Baseline Files/EOL.md.

Run: python3 scripts/eol_check.py     (weekly in .github/workflows/eol-check.yml)
Exit 1 if anything is past or within WARN_DAYS of end-of-life, or the API can't
be reached (a check that silently can't check is worse than a red run).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WARN_DAYS = 180     # fail: half a year is enough to plan and ship a move
NOTICE_DAYS = 365   # print a heads-up, don't fail

# Components the repo can't tell us the version of. The weekly workflow
# DETECTS both (env vars below) so they can't go stale; the declared values are
# the local fallback only — keep them in step with EOL.md.
MANUAL = [
    # python:3.14-slim is built on Debian 13 "trixie" (same digest as
    # python:3.14-slim-trixie, checked 10/4/26). CI reads it from the image.
    ('debian', 'DETECTED_DEBIAN', '13', 'OS inside the python:3.14-slim base image'),
    # `runs-on: ubuntu-latest` — GitHub moves this label between LTS releases.
    ('ubuntu', 'DETECTED_UBUNTU', '24.04', 'GitHub Actions runner (ubuntu-latest)'),
]


def from_dockerfile(path, image, pattern):
    text = (ROOT / path).read_text()
    # Tolerates `--platform=…` flags and a registry prefix (docker.io/library/).
    m = re.search(rf'^FROM\s+(?:--\S+\s+)*(?:\S*/)?{image}:{pattern}', text, re.M | re.I)
    if not m:
        raise SystemExit(f'EOL CHECK FAILED — could not read the {image} version from {path}')
    return m.group(1)


def fetch(url, tries=3):
    """GET JSON, retrying network blips — but not a 404 (an unknown cycle is a
    real signal, e.g. the version moved to something the API doesn't know)."""
    req = urllib.request.Request(url, headers={'User-Agent': 'baseline-eol-check'})
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == tries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries:
                raise
        time.sleep(10)


def eol_of(product, cycle):
    """End-of-life date for one release. Strict about the API's shape: if the
    `result` wrapper or the `eolFrom` key disappears (an API change), that's a
    failure — a check that quietly reads "no date" forever is the 3.10 problem
    again. Only an explicit `eolFrom: null` on a release that isn't EOL counts
    as "no date published"."""
    data = fetch(f'https://endoflife.date/api/v1/products/{product}/releases/{cycle}')
    if 'result' not in data or 'eolFrom' not in data['result']:
        raise ValueError(f'unexpected API response shape (keys: {sorted(data)})')
    release = data['result']
    eol = date.fromisoformat(release['eolFrom']) if release['eolFrom'] else None
    if release.get('isEol') and (eol is None or eol > date.today()):
        raise ValueError('API says isEol=true but gives no past end-of-life date')
    return eol


def main():
    components = [
        ('python', from_dockerfile('Dockerfile', 'python', r'(\d+\.\d+)'), 'App runtime (Dockerfile)'),
        ('postgresql', from_dockerfile('backups/Dockerfile', 'postgres', r'(\d+)'),
         'Database server + backup tools (backups/Dockerfile)'),
    ] + [
        # Detected in CI (eol-check.yml); declared value is the local fallback.
        (product, os.environ.get(env) or declared,
         role + (' — detected' if os.environ.get(env) else ' — declared'))
        for product, env, declared, role in MANUAL
    ]

    today = date.today()
    failing, rows = [], []
    for product, cycle, role in components:
        try:
            eol = eol_of(product, cycle)
        except Exception as e:  # network, 404 for an unknown cycle, bad JSON
            print(f'EOL CHECK FAILED — could not look up {product} {cycle}: {e}')
            return 1
        if eol is None:
            rows.append((product, cycle, 'no date published', role, 'ok'))
            continue
        days = (eol - today).days
        status = ('PAST END-OF-LIFE' if days < 0 else
                  f'FAIL: {days} days left' if days <= WARN_DAYS else
                  f'notice: {days} days left' if days <= NOTICE_DAYS else 'ok')
        rows.append((product, cycle, eol.isoformat(), role, status))
        if days <= WARN_DAYS:
            failing.append(f'{product} {cycle} ({role}) — end-of-life {eol.isoformat()}')

    print(f'End-of-life check, {today.isoformat()} (fails within {WARN_DAYS} days):\n')
    for product, cycle, eol, role, status in rows:
        print(f'  {product:<11} {cycle:<6} EOL {eol:<12} {status:<22} {role}')
    if failing:
        print('\nPlan a move — see Baseline Files/EOL.md:')
        for f in failing:
            print(f'  - {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
