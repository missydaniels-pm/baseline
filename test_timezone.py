"""Regression tests for user_today() — the single source of truth for the
user's local calendar day. Guards the encode/decode bug that made the
dashboard fall back to UTC (evening entries landed a day off).

Isolated temp DB (set before importing app). Run: python test_timezone.py
"""
import os
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'  # test client posts without tokens
os.environ.setdefault('SECRET_KEY', 'test-secret')

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import g

from app import app, user_today, user_now, user_tz_name, sync_user_timezone, db
from database import User

LA = 'America/Los_Angeles'
NY = 'America/New_York'
DEN_COOKIE = 'baseline_tz=America%2FDenver'


def main():
    fallback = date(2000, 1, 1)  # obviously-wrong sentinel: proves we didn't fall back

    # 1. The reported bug: URL-encoded cookie ("America%2FLos_Angeles") must
    #    resolve, NOT silently fall back to UTC.
    with app.test_request_context('/', headers={'Cookie': 'baseline_tz=America%2FLos_Angeles'}):
        got = user_today(fallback)
        assert got == datetime.now(ZoneInfo(LA)).date(), f"encoded cookie should resolve to LA date, got {got}"
        assert got != fallback, "must NOT fall back when the cookie is valid"
    print("PASS: URL-encoded baseline_tz cookie resolves (the reported bug)")

    # 1b. A valid cookie must WIN over an explicit server_today argument — this
    #     is the index() call pattern user_today(today), where `today` is UTC.
    with app.test_request_context('/', headers={'Cookie': 'baseline_tz=America%2FLos_Angeles'}):
        assert user_today(date(1999, 9, 9)) == datetime.now(ZoneInfo(LA)).date()
    print("PASS: valid cookie wins over the server_today argument")

    # 2. Raw (un-encoded) cookie also resolves — unquote is idempotent.
    with app.test_request_context('/', headers={'Cookie': f'baseline_tz={LA}'}):
        assert user_today(fallback) == datetime.now(ZoneInfo(LA)).date()
    print("PASS: raw (un-encoded) cookie also resolves")

    # 3. Absent cookie -> explicit server fallback.
    with app.test_request_context('/'):
        assert user_today(fallback) == fallback
    print("PASS: absent cookie -> server fallback")

    # 4. Unparseable cookie -> server fallback, no crash.
    with app.test_request_context('/', headers={'Cookie': 'baseline_tz=Not%2FAZone'}):
        assert user_today(fallback) == fallback
    print("PASS: unparseable cookie -> server fallback (no crash)")

    # 5. Default arg falls back to date.today().
    with app.test_request_context('/'):
        assert user_today() == date.today()
    print("PASS: default fallback is server date.today()")

    # ── Increment 1: stored User.timezone + cookie-first precedence ──────────
    with app.app_context():
        db.create_all()
        u = User(name='t', email='tz1@t.local', is_active=True,
                 onboarding_complete=True, timezone=NY)
        db.session.add(u)
        db.session.commit()
        uid = u.id

    # 6. Cookie-first (the B1 fix): a present valid cookie WINS over a different
    #    stored zone, so a device always computes "today" in its own zone.
    with app.test_request_context('/', headers={'Cookie': 'baseline_tz=America%2FLos_Angeles'}):
        g.user = User.query.get(uid)  # stored = NY
        assert user_tz_name() == LA, "cookie must win over a different stored zone"
        assert user_today(fallback) == datetime.now(ZoneInfo(LA)).date()
    print("PASS: cookie wins over a different stored timezone (cookie-first)")

    # 7. Stored zone is the fallback when NO cookie is present (durable).
    with app.test_request_context('/'):
        g.user = User.query.get(uid)  # stored = NY
        assert user_tz_name() == NY, "stored zone should be the fallback when no cookie"
        assert user_today(fallback) == datetime.now(ZoneInfo(NY)).date()
    print("PASS: stored zone is the fallback when no cookie present")

    # 8. Invalid stored zone self-heals: cookie present -> cookie; else -> None.
    with app.app_context():
        bad = User.query.get(uid)
        bad.timezone = 'Bogus/Nonexistent_Zone'
        db.session.commit()
    with app.test_request_context('/', headers={'Cookie': 'baseline_tz=America%2FLos_Angeles'}):
        g.user = User.query.get(uid)
        assert user_tz_name() == LA, "invalid stored zone must not block a valid cookie"
    with app.test_request_context('/'):
        g.user = User.query.get(uid)
        assert user_tz_name() is None, "invalid stored zone + no cookie -> None (server fallback)"
        assert user_today(fallback) == fallback
    print("PASS: invalid stored zone self-heals (cookie wins; else server fallback)")

    # 9. sync_user_timezone write path: persists a valid cookie zone, ignores
    #    junk, and is a no-op when unchanged.
    with app.app_context():
        u2 = User(name='t2', email='tz2@t.local', is_active=True, onboarding_complete=True)
        db.session.add(u2)
        db.session.commit()
        uid2 = u2.id
    with app.test_request_context('/', headers={'Cookie': DEN_COOKIE}):
        u2 = User.query.get(uid2)
        sync_user_timezone(u2)
        assert u2.timezone == 'America/Denver', f"sync should persist cookie zone, got {u2.timezone}"
    with app.test_request_context('/', headers={'Cookie': 'baseline_tz=Junk%2FZone'}):
        u2 = User.query.get(uid2)
        sync_user_timezone(u2)
        assert u2.timezone == 'America/Denver', "junk cookie must not overwrite a good stored zone"
    print("PASS: sync_user_timezone persists valid cookie, ignores junk")

    # 10. Full request dispatch through require_auth persists the zone end-to-end.
    with app.app_context():
        u3 = User(name='t3', email='tz3@t.local', is_active=True, onboarding_complete=True)
        db.session.add(u3)
        db.session.commit()
        uid3 = u3.id
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid3
    # Add baseline_tz to the client's cookie jar (alongside the session cookie)
    # so BOTH are sent — API differs across Werkzeug versions.
    try:
        client.set_cookie('baseline_tz', 'America%2FDenver')          # Werkzeug >= 2.3
    except TypeError:
        client.set_cookie('localhost', 'baseline_tz', 'America%2FDenver')  # older
    client.get('/settings')
    with app.app_context():
        assert User.query.get(uid3).timezone == 'America/Denver', \
            "require_auth should persist the browser zone end-to-end"
    print("PASS: full request dispatch through require_auth persists the timezone")

    # ── Increment 2: user_now() — the future-guard / onset frame ─────────────
    # 11. user_now() is the user's local wall-clock, NAIVE (same frame as the
    #     browser-local-naive Episode.onset), so the future-onset guard is exact.
    with app.test_request_context('/', headers={'Cookie': 'baseline_tz=America%2FLos_Angeles'}):
        un = user_now()
        assert un.tzinfo is None, "user_now must be naive (matches Episode.onset)"
        la_now = datetime.now(ZoneInfo(LA)).replace(tzinfo=None)
        assert abs((un - la_now).total_seconds()) < 5, f"user_now should be LA wall-clock, got {un}"
        # Guard semantics: one minute ahead is rejected, one minute ago is allowed.
        assert (un + timedelta(minutes=1)) > user_now(), "a future onset must trip the guard"
        assert not ((un - timedelta(minutes=1)) > user_now()), "a past onset must pass the guard"
    # No cookie / no zone -> falls back to server UTC wall-clock, still naive.
    with app.test_request_context('/'):
        assert user_now().tzinfo is None
    print("PASS: user_now() is the user's naive local wall-clock (exact future-guard frame)")

    # 12. Experiment today-dependent methods take an injected `today` (Option A —
    #     models stay request-context-free; caller passes user_today()).
    from database import Experiment
    with app.app_context():
        e = Experiment(name='E', start_date=date(2026, 1, 1), stabilization_weeks=3, status='active')
        # start 2026-01-01, 3-week stabilization -> assessment_date 2026-01-22.
        assert e.assessment_date == date(2026, 1, 22)
        assert e.ready_to_assess(date(2026, 1, 22)) is True   # exactly on the day
        assert e.ready_to_assess(date(2026, 1, 21)) is False  # day before
        assert e.weeks_elapsed(date(2026, 1, 8)) == 1.0
        assert e.weeks_elapsed(date(2027, 1, 1)) == 3.0       # capped at stabilization_weeks
        assert e.progress_pct(date(2026, 1, 8)) == 33         # 1 of 3 weeks
        assert e.progress_pct(date(2026, 1, 22)) == 100       # capped
        # Two different `today` values give two different answers — proving the
        # day is injected, not resolved from a server clock inside the model.
        assert e.ready_to_assess(date(2026, 1, 30)) != e.ready_to_assess(date(2026, 1, 1))
    print("PASS: Experiment methods take injected today (models stay Flask-free)")

    print("\nALL TIMEZONE TESTS PASSED")


if __name__ == '__main__':
    main()
