"""Auth flows and settings routes — the account contract (review F11, 10/19/26).

Nothing tested register / verify / login / logout / resend or the settings
writes, and these are the routes the rebuild re-exposes. This pins TODAY's
behaviour so a refactor can't change it silently:

  Register  validation rejects (bad email, weak or mismatched password, no
            privacy acknowledgement, duplicate email) and creates nothing; a
            good signup creates an INACTIVE, unverified user with a bcrypt hash.
  Login     unverified → the unverified notice, no session; wrong password → one
            generic message; right password → session; logout clears it.
  Verify    a signed token activates + verifies + signs in; the same token again
            (replay), a tampered token and an expired token are refused.
  Resend    enumeration-safe — the same redirect for unknown, verified and
            unverified emails.
  Settings  password change (wrong current / weak / mismatch rejected; success
            swaps the hash); email change (wrong password / taken / invalid
            rejected); email-preferences + AI-logging toggles; delete-account
            refuses a wrong confirmation and, on "DELETE", removes the user and
            their data and signs out.

Rate-limited routes are hit from distinct X-Real-IP addresses (the limiter's
key) so the 5/hour limits don't interfere. Isolated temp DB; RESEND_API_KEY
unset (sends no-op). Run: python test_auth_settings.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)
os.environ.pop('RESEND_AUDIENCE_ID', None)

from datetime import datetime, timedelta
from itertools import count

from app import app, bcrypt, _pw, generate_verify_token, load_verify_token
from database import db, User, Episode, UsedVerifyToken

FAILS = []
_ip = count(1)


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def fresh_ip():
    return {'X-Real-IP': f'45.33.{next(_ip)}.7'}  # must be a public address or the key falls back


def signed_in(client):
    with client.session_transaction() as sess:
        return sess.get('user_id')


GOOD = {'email': 'new.user@example.com', 'password': 'Passw0rdX', 'confirm_password': 'Passw0rdX',
        'privacy_ack': 'on'}


def main():
    with app.app_context():
        c = app.test_client()

        # ── Register: every rejection creates nothing ──
        for label, data in [
            ('invalid email', dict(GOOD, email='not-an-email')),
            ('short password', dict(GOOD, password='Pa1', confirm_password='Pa1')),
            ('no uppercase', dict(GOOD, password='passw0rdx', confirm_password='passw0rdx')),
            ('no number', dict(GOOD, password='Passwordx', confirm_password='Passwordx')),
            ('mismatch', dict(GOOD, confirm_password='Passw0rdY')),
            ('no privacy acknowledgement', {k: v for k, v in GOOD.items() if k != 'privacy_ack'}),
        ]:
            r = c.post('/register', data=data, headers=fresh_ip())
            check(r.status_code == 200 and User.query.count() == 0,
                  f'[register] {label} → form re-rendered, no user created')

        r = c.post('/register', data=GOOD, headers=fresh_ip())
        u = User.query.filter_by(email=GOOD['email']).first()
        check(r.status_code == 302 and '/verify/sent' in r.headers['Location'], '[register] good signup → verify-sent')
        check(u is not None and u.is_active is False and u.verified_at is None,
              '[register] new user is inactive and unverified')
        check(u is not None and u.password_hash != GOOD['password']
              and bcrypt.check_password_hash(u.password_hash, _pw(GOOD['password'])),
              '[register] password stored as a bcrypt hash')
        check(signed_in(c) is None, '[register] signup does not sign in')

        r = c.post('/register', data=dict(GOOD, email='NEW.User@Example.com'), headers=fresh_ip())
        check(User.query.count() == 1, '[register] duplicate email (any case) creates nothing')
        r = c.post('/register', data=dict(GOOD, email='someone@mailinator.com'), headers=fresh_ip())
        check(User.query.count() == 1 and b'non-disposable' in r.data,
              '[register] disposable-email domain rejected')

        # ── Login before verifying ──
        r = c.post('/login', data={'email': GOOD['email'], 'password': GOOD['password']}, headers=fresh_ip())
        check(r.status_code == 200 and signed_in(c) is None and b'Please verify your email' in r.data,
              '[login] unverified account → the verify-your-email notice, not signed in')

        # ── Verify ──
        token = generate_verify_token(GOOD['email'])
        tampered = token[:-2] + ('AA' if not token.endswith('AA') else 'BB')
        r = c.get(f'/verify/{tampered}')
        check(r.status_code == 400 and not db.session.get(User, u.id).is_active,
              '[verify] tampered token refused')
        email, err = load_verify_token(token, max_age=-1)
        check(err == 'expired', '[verify] token past its max age reads as expired')

        r = c.get(f'/verify/{token}')
        db.session.expire_all()
        u = db.session.get(User, u.id)
        check(r.status_code == 302 and u.is_active and u.verified_at is not None,
              '[verify] good token activates and verifies')
        check(signed_in(c) == u.id, '[verify] and signs the user in')
        check(UsedVerifyToken.query.count() == 1, '[verify] the token is recorded as used')

        c2 = app.test_client()
        r = c2.get(f'/verify/{token}')
        check(r.status_code == 400 and signed_in(c2) is None, '[verify] replaying the same token is refused')

        # ── Login / logout ──
        # Still mid-onboarding here: logout must work anyway (it was gated by the
        # onboarding redirect until 10/19/26).
        c.get('/logout')
        check(signed_in(c) is None, '[logout] clears the session, even mid-onboarding')
        u.onboarding_complete = True
        db.session.commit()
        r = c.post('/login', data={'email': GOOD['email'], 'password': 'Wrong-pass1'}, headers=fresh_ip())
        check(r.status_code == 200 and signed_in(c) is None and b'Invalid email or password' in r.data,
              '[login] wrong password → generic message, no session')
        r = c.post('/login', data={'email': 'nobody@example.com', 'password': 'Wrong-pass1'}, headers=fresh_ip())
        check(b'Invalid email or password' in r.data, '[login] unknown email → the same generic message')
        r = c.post('/login', data={'email': GOOD['email'].upper(), 'password': GOOD['password']},
                   headers=fresh_ip())
        check(r.status_code == 302 and signed_in(c) == u.id, '[login] right password (email any case) signs in')

        # A deactivated (but verified) account can't sign in.
        gone = User(email='deactivated@example.com', is_active=False, verified_at=datetime.utcnow(),
                    onboarding_complete=True,
                    password_hash=bcrypt.generate_password_hash(_pw('Passw0rdX')).decode())
        db.session.add(gone)
        db.session.commit()
        cd = app.test_client()
        r = cd.post('/login', data={'email': 'deactivated@example.com', 'password': 'Passw0rdX'},
                    headers=fresh_ip())
        check(signed_in(cd) is None and b'deactivated' in r.data,
              '[login] deactivated account → refused with the deactivated message')

        # ── Resend verification: enumeration-safe ──
        anon = app.test_client()
        locs = set()
        for email in ('nobody@example.com', GOOD['email'], ''):
            r = anon.post('/resend-verification', data={'email': email}, headers=fresh_ip())
            locs.add((r.status_code, r.headers.get('Location')))
        check(len(locs) == 1, '[resend] same response for unknown, verified and blank emails')

        # ── Settings: password ──
        def pw_ok(pw):
            db.session.expire_all()
            return bcrypt.check_password_hash(db.session.get(User, u.id).password_hash, _pw(pw))

        for label, data in [
            ('wrong current', {'current_password': 'Nope-1234', 'new_password': 'Newpass99', 'confirm_new_password': 'Newpass99'}),
            ('too short', {'current_password': GOOD['password'], 'new_password': 'Np1', 'confirm_new_password': 'Np1'}),
            ('mismatch', {'current_password': GOOD['password'], 'new_password': 'Newpass99', 'confirm_new_password': 'Newpass98'}),
        ]:
            c.post('/settings/password', data=data)
            check(pw_ok(GOOD['password']), f'[password] {label} → unchanged')
        c.post('/settings/password', data={'current_password': GOOD['password'],
                                           'new_password': 'Newpass99', 'confirm_new_password': 'Newpass99'})
        check(pw_ok('Newpass99') and not pw_ok(GOOD['password']), '[password] success swaps the hash')

        # ── Settings: email ──
        taken = User(email='taken@example.com', is_active=True, verified_at=datetime.utcnow(),
                     onboarding_complete=True, password_hash='x')
        db.session.add(taken)
        db.session.commit()

        def email_now():
            db.session.expire_all()
            return db.session.get(User, u.id).email

        for label, data in [
            ('wrong password', {'new_email': 'moved@example.com', 'current_password_email': 'Nope-1234'}),
            ('taken', {'new_email': 'taken@example.com', 'current_password_email': 'Newpass99'}),
            ('invalid', {'new_email': 'nope', 'current_password_email': 'Newpass99'}),
        ]:
            c.post('/settings/email', data=data)
            check(email_now() == GOOD['email'], f'[email] {label} → unchanged')
        c.post('/settings/email', data={'new_email': 'Moved@Example.com', 'current_password_email': 'Newpass99'})
        check(email_now() == 'moved@example.com', '[email] success stores the new address, lower-cased')

        # ── Settings: toggles ──
        c.post('/settings/email-preferences', data={})
        db.session.expire_all()
        check(db.session.get(User, u.id).email_updates_enabled is False, '[prefs] unticked → email updates off')
        c.post('/settings/email-preferences', data={'email_updates': 'on'})
        db.session.expire_all()
        check(db.session.get(User, u.id).email_updates_enabled is True, '[prefs] ticked → back on')
        c.post('/settings', data={'ai_logging': 'on'})
        db.session.expire_all()
        check(db.session.get(User, u.id).ai_logging_enabled is True, '[settings] AI logging toggle saves')

        # ── Delete account ──
        db.session.add(Episode(user_id=u.id, onset=datetime.now() - timedelta(days=1)))
        db.session.commit()
        c.post('/settings/delete-account', data={'confirmation': 'delete'})
        check(db.session.get(User, u.id) is not None, '[delete] wrong confirmation → account kept')
        r = c.post('/settings/delete-account', data={'confirmation': 'DELETE'})
        db.session.expire_all()
        check(r.status_code == 302 and db.session.get(User, u.id) is None
              and Episode.query.filter_by(user_id=u.id).count() == 0,
              '[delete] "DELETE" removes the user and their data')
        check(signed_in(c) is None, '[delete] and signs out')
        check(db.session.get(User, taken.id) is not None, '[delete] other users untouched')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL AUTH/SETTINGS TESTS PASSED')


if __name__ == '__main__':
    main()
