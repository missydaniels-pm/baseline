"""Regression tests for user_today() — the single source of truth for the
user's local calendar day. Guards the encode/decode bug that made the
dashboard fall back to UTC (evening entries landed a day off).

Isolated temp DB (set before importing app). Run: python test_timezone.py
"""
import os
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ.setdefault('SECRET_KEY', 'test-secret')

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app import app, user_today

LA = 'America/Los_Angeles'


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

    print("\nALL TIMEZONE TESTS PASSED")


if __name__ == '__main__':
    main()
