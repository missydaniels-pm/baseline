#!/usr/bin/env python3
"""Interim production error sweep — run at the start of each Claude session.

Error tracking (Sentry or similar) is deferred to the React rebuild plan (owner
decision 10/3/26). Until then this pulls production's error-level log lines
and HTTP 5xx responses since the last sweep, so nothing that went wrong
between sessions stays invisible. It is only as frequent as our sessions — it
cannot alert anyone. See BACKLOG Decision Log "Error tracking deferred".

PRIVACY: this output goes into a Claude conversation, so log text is redacted
before printing — SQL parameter/statement lines and Postgres DETAIL lines are
dropped (they carry the values being written: notes, names, "why" text),
exception messages are reduced to their type, and email addresses are masked.
The type, location and endpoint are enough to know something broke; read the
full text deliberately in Railway if needed.

Railway filters server-side, so a clean window prints a few lines. Reads
production through explicit -e/-s flags: it never switches the CLI's linked
environment (that switch silently drops the linked service).

Usage:  python3 scripts/error_sweep.py            # since the last sweep (max 7 days)
        python3 scripts/error_sweep.py --since 2d # explicit window; doesn't move the marker
Needs the Railway CLI, logged in. Exit 1 if the sweep itself couldn't run.
"""
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENV, SERVICE = 'production', 'baseline'
STATE = Path(__file__).resolve().parent.parent / '.claude' / 'error_sweep_last'
MAX_WINDOW = timedelta(days=7)
SHOW = 25   # unique lines shown per category; the rest are counted

# Gunicorn writes its INFO lines to stderr, which Railway labels error-level.
# Anchored to gunicorn's own format so an app error quoting "[INFO]" survives.
NOISE = re.compile(r'^\[\d{4}-\d\d-\d\d [\d:]+ [+-]\d{4}\] \[\d+\] \[INFO\]')
# Lines that carry the data being written — never printed (see PRIVACY above).
SENSITIVE = re.compile(r'\[parameters:|\[SQL:|^\s*DETAIL:')
EXCEPTION = re.compile(r'^\s*([\w.]+(?:Error|Exception|Warning|Exit|Interrupt))\b(:.*)?$')
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')
# Shown first so the cap can never hide them.
IMPORTANT = re.compile(r'^(EXC |\S+ in \w+:|.*\[(ERROR|CRITICAL)\])')
ERR_CAP, HTTP_CAP = 1000, 500
# Leading timestamps / pids, stripped so repeats of one error group together.
PREFIX = re.compile(r'^(\[[^\]]*\]\s*)+|^\S+Z\s+')


def railway(*args):
    r = subprocess.run(['railway', *args, '-e', ENV, '-s', SERVICE],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"railway {' '.join(args[:2])}: {r.stderr.strip()[:200]}")
    return r.stdout


def redact(line):
    """Return the printable form of a log line, or None if it must not be shown."""
    if SENSITIVE.search(line):
        return None
    m = EXCEPTION.match(line)
    if m:
        return f'EXC {m.group(1)}' + (': <message redacted>' if m.group(2) else '')
    return EMAIL.sub('<email>', line)


def parse_since(arg):
    m = re.fullmatch(r'(\d+)([hd])', arg)
    if not m:
        sys.exit('--since takes e.g. 12h or 2d')
    n, unit = int(m.group(1)), m.group(2)
    return datetime.now(timezone.utc) - (timedelta(hours=n) if unit == 'h' else timedelta(days=n))


def main():
    now = datetime.now(timezone.utc)
    explicit = '--since' in sys.argv
    if explicit:
        i = sys.argv.index('--since')
        since = parse_since(sys.argv[i + 1] if i + 1 < len(sys.argv) else '')
    else:
        try:
            since = datetime.fromisoformat(STATE.read_text().strip())
        except (OSError, ValueError):
            since = now - MAX_WINDOW
    since = max(since, now - MAX_WINDOW)
    since_iso = since.strftime('%Y-%m-%dT%H:%M:%SZ')

    try:
        deploys = json.loads(railway('deployment', 'list', '--json', '--limit', '50'))
        # Every deploy created in the window, plus the one already running when it opened.
        def created(d):
            return datetime.fromisoformat(d['createdAt'].replace('Z', '+00:00'))
        in_window = [d for d in deploys if created(d) >= since]
        before = [d for d in deploys if created(d) < since and d.get('status') in ('SUCCESS', 'REMOVED')]
        targets = in_window + before[:1]

        errors, server_errors = Counter(), Counter()
        redacted, warnings = 0, []
        for d in targets:
            raw = railway('logs', d['id'], '--since', since_iso, '--filter', '@level:error',
                          '--lines', str(ERR_CAP)).splitlines()
            if len(raw) >= ERR_CAP:
                warnings.append(f'deploy {d["id"][:8]}: error lines TRUNCATED at {ERR_CAP} — rerun with a shorter --since')
            for line in raw:
                if not line.strip() or NOISE.search(line):
                    continue
                shown = redact(PREFIX.sub('', line).strip())
                if shown is None:
                    redacted += 1
                else:
                    errors[shown[:200]] += 1
            http = railway('logs', d['id'], '--http', '--since', since_iso,
                           '--filter', '@httpStatus:>=500', '--lines', str(HTTP_CAP)).splitlines()
            if len(http) >= HTTP_CAP:
                warnings.append(f'deploy {d["id"][:8]}: 5xx lines TRUNCATED at {HTTP_CAP}')
            # An empty result can also mean the logs expired — say so if there's nothing at all.
            if not raw and not http and not railway('logs', d['id'], '--lines', '1').strip():
                warnings.append(f'deploy {d["id"][:8]}: NO logs at all (expired?) — "clean" is unverified for it')
            for line in http:
                if line.strip():
                    # "<ts> METHOD /path STATUS ms reqid" -> "METHOD /path STATUS"
                    parts = PREFIX.sub('', line).split()
                    server_errors[' '.join(parts[:3])] += 1
    except FileNotFoundError:
        print('SWEEP FAILED — the Railway CLI is not installed or not on PATH')
        return 1
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError) as e:
        print(f'SWEEP FAILED — nothing checked: {e}')
        return 1

    hours = (now - since).total_seconds() / 3600
    print(f'Production error sweep: last {hours:.0f}h (since {since_iso}), {len(targets)} deploy(s)')
    for title, counter in (('Error-level log lines', errors), ('HTTP 5xx responses', server_errors)):
        total = sum(counter.values())
        print(f'\n{title}: {total}' + ('' if total else ' — clean'))
        ranked = sorted(counter.items(), key=lambda kv: (not IMPORTANT.match(kv[0]), -kv[1]))
        for text, n in ranked[:SHOW]:
            print(f'  {n:>4}x  {text}')
        if len(counter) > SHOW:
            print(f'  … and {len(counter) - SHOW} more distinct lines')

    if redacted:
        print(f'\n({redacted} line(s) withheld: SQL parameters/statements or DB detail — read in Railway if needed)')
    for w in warnings:
        print(f'WARNING: {w}')

    if not explicit:
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(now.isoformat())
    return 0


if __name__ == '__main__':
    sys.exit(main())
