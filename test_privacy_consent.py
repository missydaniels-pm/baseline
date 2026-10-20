"""Privacy-policy consent: recorded, and re-asked on a policy change (10/19/26).

Owner decisions 10/19/26 (WA MHMD): consent is recorded per user
(privacy_consent_at + privacy_policy_version), and an existing user who hasn't
consented to the current policy version gets a one-time consent page before
anything else. Covered:

  1. Signup records consent (timestamp + current version).
  2. An existing account with no consent (or an older version) is sent to
     /privacy/consent from every page — dashboard, onboarding, JSON — and the
     nav is disabled there.
  3. Logout and account deletion stay reachable while consent is owed.
  4. Posting without the toggle records nothing (400); with it, consent is
     recorded and the user lands on the dashboard; the page then redirects away.
  5. A user who consents mid-onboarding continues to onboarding.
  6. The policy's "Effective" date matches PRIVACY_POLICY_VERSION.

Isolated temp DB. Run: python test_privacy_consent.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime

from app import app, PRIVACY_POLICY_VERSION
from database import db, User

FAILS = []
GOOD = {'email': 'new.user@example.com', 'password': 'Passw0rdX', 'confirm_password': 'Passw0rdX',
        'privacy_ack': 'on'}


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def make_user(email, version=None, onboarded=True):
    u = User(name='T', email=email, is_active=True, verified_at=datetime.utcnow(),
             onboarding_complete=onboarded, has_seen_tour=True,
             privacy_policy_version=version,
             privacy_consent_at=datetime.utcnow() if version else None)
    u.password_hash = 'x'
    db.session.add(u)
    db.session.commit()
    return u.id


def client_for(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = uid
    return c


def main():
    with app.app_context():
        # ── 1. Signup records consent ──
        c = app.test_client()
        r = c.post('/register', data=GOOD, headers={'X-Real-IP': '8.8.4.4'})
        u = User.query.filter_by(email=GOOD['email']).first()
        check(r.status_code == 302 and u is not None, '[signup] account created')
        check(u.privacy_policy_version == PRIVACY_POLICY_VERSION and u.privacy_consent_at is not None,
              '[signup] consent recorded with the current policy version')

        # ── 2. Existing account without consent is gated everywhere ──
        old = make_user('old@test.com')
        older = make_user('older@test.com', version='2026-04-24')
        for uid, label in ((old, 'no consent'), (older, 'older version')):
            c = client_for(uid)
            for path in ('/', '/episodes', '/settings', '/onboarding/step1'):
                r = c.get(path)
                check(r.status_code == 302 and r.headers['Location'].endswith('/privacy/consent'),
                      f'[gate:{label}] GET {path} → /privacy/consent (got {r.status_code} {r.headers.get("Location")})')
            r = c.post('/protocols/log-day', json={'date': '2026-10-19', 'protocols': []})
            check(r.status_code == 302 and r.headers['Location'].endswith('/privacy/consent'),
                  f'[gate:{label}] JSON POST is gated too')
        c = client_for(old)
        r = c.get('/privacy/consent')
        body = r.get_data(as_text=True)
        check(r.status_code == 200 and "We've updated our Privacy Policy" in body, '[page] consent page renders')
        check('nav-disabled' in body, '[page] nav is disabled while consent is owed')
        check(c.get('/privacy').status_code == 200, '[page] the full policy stays readable')

        # ── 3. The ways out stay reachable ──
        r = client_for(old).get('/logout')
        check(r.status_code == 302 and r.headers['Location'].endswith('/login'), '[exit] logout works while gated')
        gone = make_user('leaving@test.com')
        r = client_for(gone).post('/settings/delete-account', data={'confirmation': 'DELETE'})
        check(db.session.get(User, gone) is None, f'[exit] delete account works while gated (got {r.status_code})')

        # A failed delete while gated (wrong confirmation) comes back to the consent
        # page with its message — no loop, account intact.
        c = client_for(old)
        r = c.post('/settings/delete-account', data={'confirmation': 'nope'}, follow_redirects=True)
        check(r.status_code == 200 and b"We've updated our Privacy Policy" in r.data
              and b'Account deletion cancelled' in r.data and db.session.get(User, old) is not None,
              '[exit] a cancelled delete returns to the consent page with its message')

        # ── 4. Consent POST ──
        c = client_for(old)
        r = c.post('/privacy/consent', data={})
        db.session.expire_all()
        check(r.status_code == 400 and db.session.get(User, old).privacy_policy_version is None,
              '[post] without the toggle → 400, nothing recorded')
        r = c.post('/privacy/consent', data={'privacy_ack': 'on'})
        db.session.expire_all()
        u = db.session.get(User, old)
        check(r.status_code == 302 and r.headers['Location'].endswith('/'), '[post] consent → dashboard')
        check(u.privacy_policy_version == PRIVACY_POLICY_VERSION and u.privacy_consent_at is not None,
              '[post] consent recorded with the current version')
        check(c.get('/').status_code == 200, '[post] the dashboard opens afterwards')
        r = c.get('/privacy/consent')
        check(r.status_code == 302, '[post] the consent page redirects once consent is current')
        first = db.session.get(User, old).privacy_consent_at
        r = c.post('/privacy/consent', data={'privacy_ack': 'on'})
        db.session.expire_all()
        check(r.status_code == 302 and db.session.get(User, old).privacy_consent_at == first,
              '[post] a repeat POST once current changes nothing')

        # ── 5. Mid-onboarding user: consent first, then onboarding ──
        mid = make_user('mid@test.com', onboarded=False)
        c = client_for(mid)
        c.post('/privacy/consent', data={'privacy_ack': 'on'})
        r = c.get('/')
        check(r.status_code == 302 and '/onboarding/step1' in r.headers['Location'],
              '[onboarding] after consent, the onboarding gate takes over')

        # The two gates don't loop: a mid-onboarding user owing consent reaches
        # the consent page, and can delete their account from it.
        mid2 = make_user('mid2@test.com', onboarded=False)
        c = client_for(mid2)
        check(c.get('/privacy/consent').status_code == 200,
              '[onboarding] the consent page renders for a mid-onboarding user (no gate loop)')
        c.post('/settings/delete-account', data={'confirmation': 'DELETE'})
        check(db.session.get(User, mid2) is None, '[onboarding] and they can delete their account from it')

        # ── 6. Effective date matches the version ──
        effective = datetime.strptime(PRIVACY_POLICY_VERSION, '%Y-%m-%d')
        label = f'Effective {effective:%B} {effective.day}, {effective.year}'
        body = app.test_client().get('/privacy').get_data(as_text=True)
        check(label in body, f'[policy] privacy.html says "{label}"')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL PRIVACY CONSENT TESTS PASSED')


if __name__ == '__main__':
    main()
