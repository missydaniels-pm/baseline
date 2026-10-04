"""Long passwords + the Postgres URL — two library changes found by the Python 3.14
upgrade (10/4/26) that the other suites can't see.

1. bcrypt 5.0 raises ValueError on passwords over 72 bytes (older bcrypt
   truncated silently): /register, /login and /settings/password 500'd, and a
   user whose stored hash came from a long password under the old bcrypt was
   locked out for good. _pw() feeds bcrypt the first 72 UTF-8 bytes — exactly
   what the old version did — so old hashes still verify. Route-level, real
   requests, incl. a multi-byte password (the limit is bytes, not characters).
2. SQLAlchemy 2.1 maps a bare postgresql:// to psycopg 3, which isn't installed —
   production would crash at boot. normalize_database_url() names psycopg2.
   (CI runs SQLite, so the URL mapping is asserted directly.)

Run: python test_password_and_db_url.py
"""
import os
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime

import bcrypt as raw_bcrypt

from app import app, limiter
from database import db, User, normalize_database_url

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


LONG = 'Aa1' + 'x' * 97                 # 100 ASCII bytes
MULTI = 'Aa1' + 'é' * 40                # 83 bytes, 43 characters


def login(c, email, password):
    return c.post('/login', data={'email': email, 'password': password})


def main():
    limiter.enabled = False   # many logins below; rate limits have their own suite

    # ── 2. URL normalisation ──
    check(normalize_database_url('postgres://u:p@h:5432/d') == 'postgresql+psycopg2://u:p@h:5432/d',
          'postgres:// -> postgresql+psycopg2://')
    check(normalize_database_url('postgresql://u:p@h/d') == 'postgresql+psycopg2://u:p@h/d',
          'postgresql:// -> postgresql+psycopg2:// (SQLAlchemy 2.1 would pick psycopg 3)')
    check(normalize_database_url('postgresql+psycopg2://h/d') == 'postgresql+psycopg2://h/d',
          'an explicit driver is left alone')
    check(normalize_database_url('sqlite:///x.db') == 'sqlite:///x.db', 'sqlite URLs untouched')
    check(normalize_database_url('postgresql://u@h/d?sslmode=require') == 'postgresql+psycopg2://u@h/d?sslmode=require',
          'query string (e.g. sslmode) preserved')
    check(normalize_database_url('POSTGRES://u@h/d') == 'postgresql+psycopg2://u@h/d', 'scheme match is case-insensitive')

    with app.app_context():
        c = app.test_client()

        # ── 1a. Registration with a long password, then log in with it ──
        r = c.post('/register', data={'email': 'long@example.com', 'password': LONG,
                                      'confirm_password': LONG, 'privacy_ack': 'on'})
        check(r.status_code in (200, 302), f'register with a 100-byte password does not 500 (got {r.status_code})')
        u = User.query.filter_by(email='long@example.com').first()
        check(u is not None, 'the account was created')
        u.is_active, u.verified_at, u.onboarding_complete = True, datetime.utcnow(), True
        db.session.commit()
        r = login(c, 'long@example.com', LONG)
        check(r.status_code == 302 and '/login' not in r.headers.get('Location', ''),
              'login with the same 100-byte password succeeds')
        c.get('/logout')

        # ── 1b. Legacy hash: made by old bcrypt from a long password (= first 72 bytes) ──
        legacy = raw_bcrypt.hashpw(MULTI.encode('utf-8')[:72], raw_bcrypt.gensalt()).decode()
        old = User(email='legacy@example.com', is_active=True, verified_at=datetime.utcnow(),
                   onboarding_complete=True, password_hash=legacy)
        db.session.add(old)
        db.session.commit()
        r = login(c, 'legacy@example.com', MULTI)
        check(r.status_code == 302 and '/login' not in r.headers.get('Location', ''),
              'a pre-bcrypt-5 hash of a long multi-byte password still logs in (no lockout)')
        c.get('/logout')
        r = login(c, 'legacy@example.com', 'Aa1wrong-password')
        check(r.status_code == 200, 'a wrong password is still rejected')

        # ── 1c. Change password, long current + long new ──
        c.post('/login', data={'email': 'legacy@example.com', 'password': MULTI})
        NEW = 'Bb2' + 'y' * 120
        r = c.post('/settings/password', data={'current_password': MULTI, 'new_password': NEW,
                                               'confirm_new_password': NEW})
        check(r.status_code == 302, f'change password with long values does not 500 (got {r.status_code})')
        c.get('/logout')
        r = login(c, 'legacy@example.com', NEW)
        check(r.status_code == 302 and '/login' not in r.headers.get('Location', ''),
              'the new long password works')

    limiter.enabled = True
    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL PASSWORD / DB-URL TESTS PASSED')


if __name__ == '__main__':
    main()
