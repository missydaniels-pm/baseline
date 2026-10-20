"""Logging hygiene — email and Resend failures log a user id, never an address
(review F5, 10/19/26).

The error-path log lines used to carry the raw address (`to=…`, `email=…`), and
the SDK's exception text can echo it too. Railway logs are a third place that
personal data would live. Each failure path is forced here with a stubbed SDK
that raises an error containing the address; the captured log must name the
user id and contain no address.

Isolated temp DB (set before importing app). Run: python test_logging_hygiene.py
"""
import logging
import os
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['RESEND_API_KEY'] = 're_test_dummy'
os.environ['RESEND_AUDIENCE_ID'] = 'aud_test_dummy'

from datetime import datetime

import resend

import app as app_module
from app import app
from database import db, User

FAILS = []
ADDR = 'Person.Private@example.com'


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def boom(*_a, **_k):
    raise RuntimeError(f'422 invalid recipient {ADDR}')


def main():
    cap = Capture()
    app.logger.addHandler(cap)
    resend.Emails.send = boom
    resend.Contacts.create = boom
    resend.Contacts.update = boom
    resend.Contacts.remove = boom

    with app.app_context():
        u = User(email=ADDR, is_active=True, verified_at=datetime.utcnow(), onboarding_complete=True)
        u.password_hash = 'x'
        db.session.add(u)
        db.session.commit()
        uid = u.id

        for label, call in [
            ('_send_email', lambda: app_module._send_email(ADDR, 'Hi', '<p>x</p>', 'x')),
            ('resend_contact_upsert', lambda: app_module.resend_contact_upsert(ADDR)),
            ('resend_contact_delete', lambda: app_module.resend_contact_delete(ADDR)),
        ]:
            cap.lines.clear()
            call()
            joined = '\n'.join(cap.lines)
            check(bool(cap.lines), f'[{label}] the failure is still logged')
            check(ADDR.lower() not in joined.lower(), f'[{label}] no email address in the log')
            check(f'user {uid}' in joined, f'[{label}] the log names the user id')

        cap.lines.clear()
        app_module._send_email('nobody@example.com', 'Hi', '<p>x</p>', 'x')
        joined = '\n'.join(cap.lines)
        check('@example.com' not in joined and 'unknown user' in joined,
              '[_send_email] an address with no account logs "unknown user", not the address')

    # F15: /admin/analytics now logs an unauthorized attempt like /admin/users.
    with app.app_context():
        c = app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = uid
        cap.lines.clear()
        r = c.get('/admin/analytics')
        check(r.status_code == 302 and any('Unauthorized /admin/analytics' in ln and f'user_id={uid}' in ln
                                           for ln in cap.lines),
              '[admin] non-admin /admin/analytics attempt is logged with the user id')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL LOGGING-HYGIENE TESTS PASSED')


if __name__ == '__main__':
    main()
