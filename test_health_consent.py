"""Separate Apple Health consent + privacy-policy update (Apple Health step 3, 11/21/26).

Design: SPEC-health-data.md §8 (owner-approved). Covered, through real requests:
  1. GET /api/v1/health/consent: text + version from one place; behind the
     token, the Health sync flag and current privacy consent.
  2. POST: only the version the app showed is accepted (409 otherwise); records
     health_consent_at/_version; /me and sync-status report it.
  3. Uploads need the current Health consent (403 health_consent_required),
     and the upload re-checks it under a lock (a withdrawal mid-upload wins).
  4. Withdraw from the app (DELETE): deletes every reading, clears consent,
     works even with sync off or the privacy policy awaiting re-acceptance,
     idempotent, user-scoped.
  5. Withdraw from the website (Settings): section shown only to accounts with
     sync on or a consent on record; reading count; withdraw deletes; CSRF
     form; another user's readings untouched; logs carry counts only.
  6. A changed consent version: uploads refused, readings kept, web delete works.
  7. Privacy policy: new sections present, version bumped (re-acceptance
     page shows the new summary); Help has the Apple Health section.

Isolated temp DB. Run: python test_health_consent.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

import io  # noqa: E402
import logging  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime  # noqa: E402

import app as app_module  # noqa: E402
from app import (app, bcrypt, limiter, _pw, PRIVACY_POLICY_VERSION,  # noqa: E402
                 HEALTH_CONSENT_VERSION, HEALTH_CONSENT_TEXT, purge_unconsented_health_samples)
from app import init_db  # noqa: E402
init_db()  # schema + startup tasks; importing app no longer runs them (11/21/26)
from database import db, User, HealthSample  # noqa: E402

FAILS = []
PW = 'Passw0rd-consent'
CONSENT = '/api/v1/health/consent'
SAMPLES = '/api/v1/health/samples'


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def make_user(email, **kw):
    fields = dict(name='T', email=email, is_active=True, verified_at=datetime.utcnow(),
                  onboarding_complete=True, has_seen_tour=True, health_sync_enabled=True,
                  privacy_policy_version=PRIVACY_POLICY_VERSION, timezone='America/Los_Angeles')
    fields.update(kw)
    u = User(**fields)
    u.password_hash = bcrypt.generate_password_hash(_pw(PW)).decode('utf-8')
    db.session.add(u)
    db.session.commit()
    return u.id


def token_for(c, email):
    return {'Authorization': 'Bearer ' + c.post('/api/v1/auth/token',
                                               json={'email': email, 'password': PW}).get_json()['token']}


def web_for(uid):
    w = app.test_client()
    with app.app_context():
        sv = db.session.get(User, uid).session_version
    with w.session_transaction() as s:
        s['user_id'], s['sv'] = uid, sv
    return w


def readings(n=3):
    return [{'id': str(uuid.uuid4()), 'start': f'2026-11-20T0{i}:00:00Z', 'end': f'2026-11-20T0{i}:01:00Z',
             'value': 40 + i} for i in range(n)]


def count(uid):
    with app.app_context():
        return HealthSample.query.filter_by(user_id=uid).count()


def user(uid):
    with app.app_context():
        u = db.session.get(User, uid)
        db.session.expunge(u)
        return u


def set_user(uid, **kw):
    with app.app_context():
        u = db.session.get(User, uid)
        for k, v in kw.items():
            setattr(u, k, v)
        db.session.commit()


def main():
    limiter.enabled = False
    c = app.test_client()
    with app.app_context():
        a = make_user('a@example.test')
        b = make_user('b@example.test')
        plain = make_user('plain@example.test', health_sync_enabled=False)
    A, B = token_for(c, 'a@example.test'), token_for(c, 'b@example.test')

    # ── 1. The consent text ──
    r = c.get(CONSENT, headers=A)
    j = r.get_json()
    check(r.status_code == 200 and j['version'] == HEALTH_CONSENT_VERSION and j['title'] == HEALTH_CONSENT_TEXT['title']
          and j['points'] == HEALTH_CONSENT_TEXT['points'] and j['current'] is False and j['given_at'] is None,
          '[text] GET returns the one source of the wording, its version, not yet given')
    check('never sent to AI services' in ' '.join(j['points']) and 'deleted immediately' in ' '.join(j['points']),
          '[text] approved wording: no AI services; withdrawal deletes')
    check(c.get(CONSENT).status_code == 401, '[text] no token -> 401')
    P = token_for(c, 'plain@example.test')
    r = c.get(CONSENT, headers=P)
    check(r.status_code == 403 and r.get_json()['code'] == 'health_sync_not_enabled', '[text] sync off -> 403')

    # ── 3a. Uploads need the consent ──
    r = c.post(SAMPLES, json={'kind': 'hrv_sdnn', 'samples': readings()}, headers=A)
    check(r.status_code == 403 and r.get_json()['code'] == 'health_consent_required' and count(a) == 0,
          '[gate] no Health consent -> 403 health_consent_required, nothing stored')

    # ── 2. Giving consent ──
    for label, body in [('no body', None), ('old version', {'version': '2000-01-01'}), ('list', [HEALTH_CONSENT_VERSION])]:
        r = c.post(CONSENT, json=body, headers=A) if body is not None else c.post(CONSENT, headers=A)
        check(r.status_code == 409 and r.get_json()['code'] == 'consent_version_changed' and user(a).health_consent_at is None,
              f'[give] {label} -> 409, nothing recorded')
    r = c.post(CONSENT, json={'version': HEALTH_CONSENT_VERSION}, headers=A)
    u = user(a)
    check(r.status_code == 200 and u.health_consent_version == HEALTH_CONSENT_VERSION and u.health_consent_at is not None,
          '[give] the shown version -> recorded')
    check(c.get(CONSENT, headers=A).get_json()['current'] is True, '[give] GET now says current')
    check(c.get('/api/v1/me', headers=A).get_json()['user']['health_consent_current'] is True, '[give] /me reports it')
    check(c.get('/api/v1/health/sync-status', headers=A).get_json()['health_consent_current'] is True,
          '[give] sync-status reports it')
    r = c.post(SAMPLES, json={'kind': 'hrv_sdnn', 'samples': readings(3)}, headers=A)
    check(r.status_code == 200 and count(a) == 3, '[gate] with consent, readings store')
    c.post(CONSENT, json={'version': HEALTH_CONSENT_VERSION}, headers=B)
    c.post(SAMPLES, json={'kind': 'blood_glucose', 'samples': [dict(x, value=100) for x in readings(2)]}, headers=B)
    check(count(b) == 2, '[setup] user B has 2 readings')

    # ── 3b. Withdrawal wins over an upload in flight (re-check under the lock) ──
    set_user(a, health_consent_version='old')
    real = app_module.health_consent_current
    app_module.health_consent_current = lambda u: True       # the gate saw consent...
    try:
        r = c.post(SAMPLES, json={'kind': 'hrv_sdnn', 'samples': readings(2)}, headers=A)
    finally:
        app_module.health_consent_current = real
    check(r.status_code == 403 and r.get_json()['code'] == 'health_consent_required' and count(a) == 3,
          '[race] consent gone by the time the upload writes -> 403, nothing stored')
    set_user(a, health_consent_version=HEALTH_CONSENT_VERSION)

    # ── 4. Withdraw from the app ──
    set_user(a, health_sync_enabled=False, privacy_policy_version='old')   # withdrawing must still work
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    app.logger.addHandler(h)
    try:
        r = c.delete(CONSENT, headers=A)
    finally:
        app.logger.removeHandler(h)
    u = user(a)
    check(r.status_code == 200 and r.get_json() == {'ok': True, 'deleted': 3} and count(a) == 0,
          '[withdraw app] works with sync off and policy pending; deletes all 3 readings')
    check(u.health_consent_at is None and u.health_consent_version is None, '[withdraw app] consent cleared')
    check(count(b) == 2, "[withdraw app] user B's readings untouched")
    check(app.logger.getEffectiveLevel() <= logging.INFO,
          '[logs] info lines are emitted outside debug mode (audit lines reach production logs)')
    check('3 readings deleted' in buf.getvalue() and 'a@example.test' not in buf.getvalue(),
          '[withdraw app] log has the count, no email')
    r = c.delete(CONSENT, headers=A)
    check(r.status_code == 200 and r.get_json()['deleted'] == 0, '[withdraw app] withdrawing again is harmless')
    check(c.delete(CONSENT).status_code == 401, '[withdraw app] no token -> 401')
    set_user(a, health_sync_enabled=True, privacy_policy_version=PRIVACY_POLICY_VERSION)
    r = c.post(SAMPLES, json={'kind': 'hrv_sdnn', 'samples': readings()}, headers=A)
    check(r.status_code == 403, '[withdraw app] after withdrawal, uploads are refused')

    # ── 5. Withdraw from the website ──
    page = web_for(plain).get('/settings').get_data(as_text=True)
    check('id="apple-health"' not in page, '[web] no Apple Health section for an account without sync or consent')
    page = web_for(a).get('/settings').get_data(as_text=True)
    check('id="apple-health"' in page and 'Connect it from Baseline' in page and 'Withdraw and delete' not in page,
          '[web] sync on, no consent: explains to connect from the app, no withdraw button')
    page = web_for(b).get('/settings').get_data(as_text=True)
    check('Connected through the Baseline Sync iPhone app since' in page and '2 stored readings' in page
          and 'Withdraw and delete my Health data' in page and 'health-consent/withdraw' in page,
          '[web] consented: date, reading count, withdraw behind a reveal')
    r = web_for(b).post('/settings/health-consent/withdraw', follow_redirects=True)
    check(r.status_code == 200 and '2 stored readings deleted' in r.get_data(as_text=True) and count(b) == 0
          and user(b).health_consent_version is None, '[web] withdraw deletes readings, clears consent, says how many')
    # Withdrawing never requires accepting a new privacy policy first.
    c.post(CONSENT, json={'version': HEALTH_CONSENT_VERSION}, headers=B)
    c.post(SAMPLES, json={'kind': 'blood_glucose', 'samples': [dict(x, value=105) for x in readings(2)]}, headers=B)
    set_user(b, privacy_policy_version='2026-10-19')
    wb = web_for(b)
    page = wb.get('/privacy/consent').get_data(as_text=True)
    check('Withdraw Health consent and delete my Health data' in page,
          '[web] the re-acceptance page offers Health withdrawal to someone with Health data')
    r = wb.post('/settings/health-consent/withdraw')
    check(r.status_code == 302 and '/privacy/consent' in r.headers['Location'] and count(b) == 0
          and user(b).health_consent_version is None and user(b).privacy_policy_version == '2026-10-19',
          '[web] withdraw works while the new policy is unaccepted, and does not accept it')
    page = web_for(plain).get('/privacy/consent') if False else None
    set_user(b, privacy_policy_version=PRIVACY_POLICY_VERSION)
    set_user(plain, privacy_policy_version='2026-10-19')
    page = web_for(plain).get('/privacy/consent').get_data(as_text=True)
    check('Withdraw Health consent' not in page, '[web] no Health withdrawal offered to someone with no Health data')
    set_user(plain, privacy_policy_version=PRIVACY_POLICY_VERSION)
    r = app.test_client().post('/settings/health-consent/withdraw')
    check(r.status_code == 302 and '/login' in r.headers['Location'], '[web] signed out -> login, nothing happens')

    # ── 6. A changed consent version ──
    c.post(CONSENT, json={'version': HEALTH_CONSENT_VERSION}, headers=B)
    c.post(SAMPLES, json={'kind': 'blood_glucose', 'samples': [dict(x, value=110) for x in readings(2)]}, headers=B)
    set_user(b, health_consent_version='2000-01-01')
    r = c.post(SAMPLES, json={'kind': 'blood_glucose', 'samples': [dict(x, value=110) for x in readings(1)]}, headers=B)
    check(r.status_code == 403 and count(b) == 2, '[version] an old consent: uploads refused, readings kept')
    check(c.get(CONSENT, headers=B).get_json()['current'] is False, '[version] the app sees it must ask again')
    page = web_for(b).get('/settings').get_data(as_text=True)
    check('still has 2 readings' in page and 'Delete my stored Health data' in page,
          '[version] Settings explains kept readings and offers to delete them')
    web_for(b).post('/settings/health-consent/withdraw')
    check(count(b) == 0, '[version] ...and deleting them works')

    # ── Startup sweep: readings without any Health consent are deleted ──
    with app.app_context():
        nc = make_user('noconsent@example.test')
        st = make_user('stale@example.test', health_consent_at=datetime.utcnow(), health_consent_version='2000-01-01')
        ok = make_user('ok@example.test', health_consent_at=datetime.utcnow(), health_consent_version=HEALTH_CONSENT_VERSION)
        for uid in (nc, st, ok):
            db.session.add(HealthSample(user_id=uid, kind='hrv_sdnn', hk_uuid=str(uuid.uuid4()).upper(), value=50,
                                        start_at=datetime(2026, 11, 20), end_at=datetime(2026, 11, 20, 0, 1),
                                        local_date=datetime(2026, 11, 19).date()))
        db.session.commit()
        purge_unconsented_health_samples()
    check(count(nc) == 0 and count(st) == 1 and count(ok) == 1,
          '[sweep] no consent -> deleted; stale-version and current consent -> kept')

    # ── 7. Policy text, version bump, Help ──
    policy = app.test_client().get('/privacy').get_data(as_text=True)
    for needle in ['Apple Health data (iPhone app, by invitation only)', 'Phone sign-ins',
                   'never sent to Anthropic or any other AI service', 'Withdraw Health consent',
                   'other than the service providers that host Baseline (see Section 4)',
                   'Apple Health readings are kept while your Health', 'turning Health sync on or off is the only',
                   'Effective November 21, 2026']:
        check(needle in policy, f'[policy] contains: {needle}')
    check('Administrators cannot edit your account from' not in policy,
          '[policy] the old "admins cannot edit" sentence (no longer true) is gone')
    check(PRIVACY_POLICY_VERSION == '2026-11-21', '[policy] version bumped')
    old = make_user_ctx('old@example.test', privacy_policy_version='2026-10-19')
    w = web_for(old)
    r = w.get('/')
    check(r.status_code == 302 and '/privacy/consent' in r.headers['Location'],
          '[policy] a user who accepted the October policy is asked again')
    page = w.get('/privacy/consent').get_data(as_text=True)
    check('November 21, 2026' in page and 'Apple Health (iPhone app)' in page and 'iPhone app sign-ins' in page,
          '[policy] the re-acceptance page summarises this change')
    help_page = web_for(a).get('/help').get_data(as_text=True)
    check('id="apple-health"' in help_page and 'invitation' in help_page, '[help] Apple Health section present')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL HEALTH CONSENT TESTS PASSED')


def make_user_ctx(email, **kw):
    with app.app_context():
        return make_user(email, **kw)


if __name__ == '__main__':
    main()
