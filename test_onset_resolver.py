"""Unit tests for resolve_onset() — the deterministic AI-check-in onset resolver
(timezone Increment 3). The conversational model classifies + quotes the time
phrase; this code owns the date math against the message anchor. Guards: correct
resolution of common phrases, never a future onset, and low-confidence flagging
for vague/unresolvable phrases.

Isolated temp DB (set before importing app). Run: python test_onset_resolver.py
"""
import os
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')

from datetime import datetime, timedelta

from app import resolve_onset

ANCHOR = datetime(2026, 7, 17, 14, 30)  # Fri 2:30pm — fixed so tests are deterministic


def R(expr, ttype):
    return resolve_onset(expr, ttype, ANCHOR)


def main():
    # ── The message time is the anchor for now / unknown / no-cue ────────────
    assert R('right now', 'now') == (ANCHOR, False)
    assert R(None, 'unknown') == (ANCHOR, True)          # unknown -> now, flagged
    assert R('', 'past_relative') == (ANCHOR, True)      # no phrase + non-now type -> flagged
    print("PASS: now / unknown / no-phrase resolve to the message anchor")

    # ── Future is never logged as an occurred onset (clamped to now) ─────────
    onset, low = R('tomorrow', 'future')
    assert onset == ANCHOR and low is False
    # a phrase that parses into the future is also clamped
    onset, low = R('in 2 hours', 'past_relative')
    assert onset <= ANCHOR, "a future-resolving phrase must clamp to the anchor"
    print("PASS: future / future-resolving phrases clamp to now (never future)")

    # ── Explicit + numeric-relative resolve exactly, and are TRUSTED (not flagged)
    assert R('2 hours ago', 'past_relative') == (datetime(2026, 7, 17, 12, 30), False)
    assert R('at 3pm yesterday', 'past_specific') == (datetime(2026, 7, 16, 15, 0), False)
    assert R('at 8am', 'past_specific') == (datetime(2026, 7, 17, 8, 0), False)
    print("PASS: explicit and numeric-relative phrases resolve exactly (trusted)")

    # ── Colloquial day-parts resolve, but are FLAGGED (inferred → confirm nudge) ─
    assert R('this morning', 'past_relative') == (datetime(2026, 7, 17, 9, 0), True)
    assert R('this afternoon', 'past_relative') == (datetime(2026, 7, 17, 14, 0), True)
    assert R('last night', 'past_relative') == (datetime(2026, 7, 16, 21, 0), True)
    assert R('yesterday evening', 'past_relative') == (datetime(2026, 7, 16, 20, 0), True)
    # Day-part PLUS an explicit hour keeps the stated hour ("this morning around 8"
    # is a verified live model output) — still flagged as inferred.
    assert R('this morning around 8', 'past_specific') == (datetime(2026, 7, 17, 8, 0), True)
    assert R('last night at 10', 'past_relative') == (datetime(2026, 7, 16, 22, 0), True)
    assert R('a few days ago', 'past_vague') == (datetime(2026, 7, 14, 14, 30), True)
    print("PASS: colloquial day-part phrases resolve, flagged as inferred")

    # ── A nearby non-time number ("8 out of 10") must NOT be grabbed as the hour ─
    # (Code B1): falls back to the part-of-day default (9am), flagged.
    assert R('this morning my pain was an 8 out of 10', 'past_relative') == (datetime(2026, 7, 17, 9, 0), True)
    print("PASS: a severity number is not mistaken for the onset hour")

    # ── Ongoing interval resolves to its START (Finding A / owner use case #1) ───
    assert R('the last seven days', 'ongoing') == (datetime(2026, 7, 10, 14, 30), True)
    assert R('since Monday', 'ongoing') == (datetime(2026, 7, 13, 0, 0), True)
    print("PASS: ongoing phrases resolve to the interval start (e.g. 7 days ago)")

    # ── Vague / unresolvable -> best guess = now, flagged for confirmation ────
    assert R('earlier', 'past_vague') == (ANCHOR, True)
    assert R('gibberish zzz', 'past_relative') == (ANCHOR, True)
    # Schema drift: a non-'now' type with a MISSING phrase is flagged (Code W3).
    assert R(None, 'past_specific') == (ANCHOR, True)
    assert R(None, 'now') == (ANCHOR, False)
    print("PASS: vague / unresolvable / missing-phrase fall back to now + flag")

    # ── Invariant: resolve_onset NEVER returns a future datetime ─────────────
    for expr, t in [('right now', 'now'), ('tonight', 'past_relative'),
                    ('in 3 days', 'future'), ('next week', 'past_specific'),
                    ('a few days ago', 'past_vague'), (None, 'unknown')]:
        onset, _ = R(expr, t)
        assert onset <= ANCHOR, f"{expr!r} produced a future onset {onset}"
    print("PASS: invariant — resolve_onset never returns a future onset")

    print("\nALL ONSET-RESOLVER TESTS PASSED")


if __name__ == '__main__':
    main()
