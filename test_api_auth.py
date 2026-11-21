"""API layer + native-app token sign-in + Health sync flag (Apple Health plan step 1, 11/21/26).

Covered, through real requests into a real database:
  1. POST /api/v1/auth/token: success (token shown once, only its hash stored,
     90-day expiry, device name), and every refusal — wrong password, unknown
     email, unverified, deactivated, not JSON, missing/ill-typed fields.
  2. Bearer auth on /api/v1/me: no header, wrong scheme, unknown token,
     incomplete onboarding (API skips the page gates).
  3. The browser session cookie never authenticates an API route.
  4. CSRF: API routes are exempt (a native client has no CSRF token), web
     forms are still protected.
  5. Sign-out (DELETE) revokes only that phone's token.
  6. A password change signs the phone out (session_version).
  7. Expiry and the sliding window (extended on use, written at most hourly).
  8. A deactivated account's token stops working.
  9. The Health gate: flag off / outdated consent / on.
 10. The admin toggle on /admin/users: admin only, on, off, missing account.
 11. Deleting an account deletes its tokens (DB cascade).
 12. Dead tokens are pruned by init-db's prune task; live ones are kept.
 13. API errors are JSON (404, 405, 429); web 404 is unchanged.
 14. Web login still works after moving to the shared check_credentials().

Isolated temp DB. Run: python test_api_auth.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime, timedelta  # noqa: E402
import hashlib  # noqa: E402

from app import (app, bcrypt, limiter, _pw, set_password, prune_api_tokens,  # noqa: E402
                 API_TOKEN_PREFIX, PRIVACY_POLICY_VERSION)
from app import init_db  # noqa: E402
init_db()  # schema + startup tasks; importing app no longer runs them (11/21/26)
from database import db, User, ApiToken  # noqa: E402

FAILS = []
PW = 'Passw0rd-api'


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def make_user(email, **kw):
    fields = dict(name='T', email=email, is_active=True, verified_at=datetime.utcnow(),
                  onboarding_complete=True, has_seen_tour=True,
                  privacy_policy_version=PRIVACY_POLICY_VERSION)
    fields.update(kw)
    u = User(**fields)
    u.password_hash = bcrypt.generate_password_hash(_pw(PW)).decode('utf-8')
    db.session.add(u)
    db.session.commit()
    return u


def get_token(c, email, password=PW, device_name=None):
    body = {'email': email, 'password': password}
    if device_name is not None:
        body['device_name'] = device_name
    return c.post('/api/v1/auth/token', json=body)


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


def main():
    limiter.enabled = False   # many sign-ins below; the rate limit has its own check at the end
    c = app.test_client()
    with app.app_context():
        alice = make_user('alice@example.test')
        make_user('pending@example.test', is_active=False, verified_at=None)
        make_user('gone@example.test', is_active=False)
        make_user('newbie@example.test', onboarding_complete=False)
        admin = make_user('admin@example.test', is_admin=True)
        alice_id, admin_id = alice.id, admin.id

    # ── 1. Issuing a token ──
    r = get_token(c, ' Alice@Example.test ', device_name="Alice's iPhone")
    j = r.get_json()
    check(r.status_code == 200 and j['ok'] and j['token'].startswith(API_TOKEN_PREFIX),
          f'[issue] correct email (any case/spaces) + password gets a token ({r.status_code})')
    token = j['token']
    check(j['user'] == {'id': alice_id, 'email': 'alice@example.test', 'name': 'T',
                        'health_sync_enabled': False, 'consent_current': True,
                        'health_consent_current': False},
          f'[issue] response carries the user ({j.get("user")})')
    check(r.headers.get('Cache-Control') == 'no-store', '[issue] token response is no-store')
    with app.app_context():
        row = ApiToken.query.filter_by(user_id=alice_id).one()
        check(row.token_hash == hashlib.sha256(token.encode()).hexdigest()
              and token not in (row.token_hash or ''),
              '[issue] only the SHA-256 of the token is stored')
        check(row.device_name == "Alice's iPhone", '[issue] device name stored')
        days = (row.expires_at - row.created_at).days
        check(days == 90, f'[issue] expires in 90 days ({days})')
        check(j['expires_at'].endswith('Z'), '[issue] expires_at is ISO UTC')

    for label, body, status, code in [
        ('wrong password', {'email': 'alice@example.test', 'password': 'nope'}, 401, 'invalid_credentials'),
        ('unknown email', {'email': 'who@example.test', 'password': PW}, 401, 'invalid_credentials'),
        ('unverified', {'email': 'pending@example.test', 'password': PW}, 403, 'unverified'),
        ('deactivated', {'email': 'gone@example.test', 'password': PW}, 403, 'deactivated'),
        ('missing password', {'email': 'alice@example.test'}, 400, 'bad_request'),
        ('blank email', {'email': '  ', 'password': PW}, 400, 'bad_request'),
        ('email not text', {'email': ['alice@example.test'], 'password': PW}, 400, 'bad_request'),
        ('device_name not text', {'email': 'alice@example.test', 'password': PW, 'device_name': 5}, 400, 'bad_request'),
        ('NUL in device_name', {'email': 'alice@example.test', 'password': PW, 'device_name': 'a\x00b'}, 400, 'bad_request'),
        ('NUL in email', {'email': 'alice@example.test\x00', 'password': PW}, 400, 'bad_request'),
    ]:
        r = c.post('/api/v1/auth/token', json=body)
        j = r.get_json() or {}
        check(r.status_code == status and j.get('ok') is False and j.get('code') == code,
              f'[issue] {label} -> {status} {code} (got {r.status_code} {j.get("code")})')
    r = c.post('/api/v1/auth/token', data='email=alice@example.test&password=x',
               content_type='application/x-www-form-urlencoded')
    check(r.status_code == 400 and r.get_json()['code'] == 'bad_request', '[issue] form-encoded body -> 400')
    r = c.post('/api/v1/auth/token', data='[1,2]', content_type='application/json')
    check(r.status_code == 400, '[issue] a JSON list, not an object -> 400')
    with app.app_context():
        n = ApiToken.query.count()
    check(n == 1, f'[issue] refusals created no tokens ({n} total)')

    # Unknown email costs the same bcrypt work as a wrong password (no timing oracle).
    import time
    def timed(email):
        t = time.perf_counter()
        c.post('/api/v1/auth/token', json={'email': email, 'password': 'wrong-password'})
        return time.perf_counter() - t
    timed('nobody@example.test')  # first call builds the dummy hash
    known = min(timed('alice@example.test') for _ in range(3))
    unknown = min(timed('nobody@example.test') for _ in range(3))
    check(unknown > known * 0.5, f'[issue] unknown email takes as long as a wrong password ({unknown*1000:.0f}ms vs {known*1000:.0f}ms)')

    # ── 2. Bearer auth on /me ──
    r = c.get('/api/v1/me', headers=bearer(token))
    check(r.status_code == 200 and r.get_json()['user']['id'] == alice_id, '[me] valid token -> the user')
    for label, headers in [('no header', {}),
                           ('Basic scheme', {'Authorization': f'Basic {token}'}),
                           ('unknown token', bearer(API_TOKEN_PREFIX + 'x' * 43)),
                           ('no prefix', bearer('x' * 43))]:
        r = c.get('/api/v1/me', headers=headers)
        j = r.get_json() or {}
        check(r.status_code == 401 and j.get('code') == 'unauthorized' and 'Location' not in r.headers,
              f'[me] {label} -> JSON 401, no redirect (got {r.status_code})')
    nb = get_token(c, 'newbie@example.test').get_json()['token']
    r = c.get('/api/v1/me', headers=bearer(nb))
    check(r.status_code == 200, '[me] mid-onboarding account is not redirected to onboarding')

    # ── 3. The session cookie never authenticates the API ──
    web = app.test_client()
    with web.session_transaction() as sess:
        sess['user_id'] = alice_id
        sess['sv'] = 0
    check(web.get('/').status_code == 200, '[cookie] the cookie signs in to the website')
    r = web.get('/api/v1/me')
    check(r.status_code == 401, f'[cookie] ...but not the API ({r.status_code})')
    r = web.delete('/api/v1/auth/token')
    check(r.status_code == 401, '[cookie] ...and cannot sign a phone out')

    # ── 4. CSRF: API exempt, web forms still protected ──
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        r = get_token(c, 'alice@example.test')
        check(r.status_code == 200, f'[csrf] token sign-in works with CSRF on, no CSRF token ({r.status_code})')
        extra = r.get_json()['token']
        r = c.delete('/api/v1/auth/token', headers=bearer(extra))
        check(r.status_code == 200, '[csrf] sign-out works with CSRF on')
        with app.test_client() as wc:
            wc.post('/login', data={'email': 'alice@example.test', 'password': PW})
            with wc.session_transaction() as sess:
                signed_in = sess.get('user_id') is not None
        check(not signed_in, '[csrf] a web login form without a CSRF token is still refused')
    finally:
        app.config['WTF_CSRF_ENABLED'] = False

    # ── 5. Sign-out revokes only that phone ──
    second = get_token(c, 'alice@example.test', device_name='iPad').get_json()['token']
    r = c.delete('/api/v1/auth/token', headers=bearer(second))
    check(r.status_code == 200 and r.get_json() == {'ok': True}, '[revoke] DELETE signs the phone out')
    check(c.get('/api/v1/me', headers=bearer(second)).status_code == 401, '[revoke] revoked token stops working')
    check(c.get('/api/v1/me', headers=bearer(token)).status_code == 200, '[revoke] the other phone stays signed in')
    check(c.delete('/api/v1/auth/token', headers=bearer(second)).status_code == 401,
          '[revoke] revoking twice -> 401, not an error')

    # ── 6. Password change signs the phone out ──
    with app.app_context():
        u = db.session.get(User, alice_id)
        set_password(u, PW)  # same password, still a change event
        db.session.commit()
    check(c.get('/api/v1/me', headers=bearer(token)).status_code == 401,
          '[password] a password change signs the phone out')
    token = get_token(c, 'alice@example.test').get_json()['token']
    check(c.get('/api/v1/me', headers=bearer(token)).status_code == 200, '[password] signing in again works')

    # ── 7. Expiry and the sliding window ──
    h = hashlib.sha256(token.encode()).hexdigest()
    with app.app_context():
        row = ApiToken.query.filter_by(token_hash=h).one()
        row.last_used_at = datetime.utcnow() - timedelta(minutes=10)
        row.expires_at = datetime.utcnow() + timedelta(days=5)
        db.session.commit()
    c.get('/api/v1/me', headers=bearer(token))
    with app.app_context():
        row = ApiToken.query.filter_by(token_hash=h).one()
        check((row.expires_at - datetime.utcnow()).days < 6, '[slide] used 10 min ago: no write')
        row.last_used_at = datetime.utcnow() - timedelta(hours=2)
        db.session.commit()
    c.get('/api/v1/me', headers=bearer(token))
    with app.app_context():
        row = ApiToken.query.filter_by(token_hash=h).one()
        check((row.expires_at - datetime.utcnow()).days >= 89, '[slide] used 2h ago: expiry pushed out to 90 days')
        check(datetime.utcnow() - row.last_used_at < timedelta(minutes=1), '[slide] last_used_at updated')
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()
    check(c.get('/api/v1/me', headers=bearer(token)).status_code == 401, '[expiry] an expired token stops working')
    token = get_token(c, 'alice@example.test').get_json()['token']

    # ── 8. Deactivated account ──
    with app.app_context():
        db.session.get(User, alice_id).is_active = False
        db.session.commit()
    check(c.get('/api/v1/me', headers=bearer(token)).status_code == 401, '[deactivated] token stops working')
    with app.app_context():
        db.session.get(User, alice_id).is_active = True
        db.session.commit()

    # ── 9. Health gate ──
    r = c.get('/api/v1/health/sync-status', headers=bearer(token))
    check(r.status_code == 403 and r.get_json()['code'] == 'health_sync_not_enabled',
          '[health] flag off -> 403 health_sync_not_enabled')
    check(c.get('/api/v1/health/sync-status').status_code == 401, '[health] no token -> 401 before the gate')

    # ── 10. Admin toggle ──
    alice_web = app.test_client()
    with app.app_context():
        alice_sv = db.session.get(User, alice_id).session_version
    with alice_web.session_transaction() as sess:
        sess['user_id'] = alice_id
        sess['sv'] = alice_sv
    r = alice_web.post(f'/admin/users/{alice_id}/health-sync', data={'enabled': '1'})
    with app.app_context():
        on = db.session.get(User, alice_id).health_sync_enabled
    check(r.status_code == 302 and not on, '[admin] a non-admin cannot turn Health sync on (own account)')

    admin_web = app.test_client()
    with admin_web.session_transaction() as sess:
        sess['user_id'] = admin_id
        sess['sv'] = 0
    page = admin_web.get('/admin/users')
    check(page.status_code == 200 and b'Health sync' in page.data
          and f'/admin/users/{alice_id}/health-sync'.encode() in page.data,
          '[admin] /admin/users shows the Health sync column with a toggle per account')
    r = admin_web.post(f'/admin/users/{alice_id}/health-sync', data={'enabled': '1'})
    with app.app_context():
        on = db.session.get(User, alice_id).health_sync_enabled
    check(r.status_code == 302 and on, '[admin] admin turns Health sync on')
    r = c.get('/api/v1/health/sync-status', headers=bearer(token))
    check(r.status_code == 200 and r.get_json() == {'ok': True, 'enabled': True, 'health_consent_current': False, 'last_synced_at': None},
          '[health] flag on -> 200')
    check(c.get('/api/v1/me', headers=bearer(token)).get_json()['user']['health_sync_enabled'] is True,
          '[me] reports health_sync_enabled')
    with app.app_context():
        db.session.get(User, alice_id).privacy_policy_version = 'old'
        db.session.commit()
    r = c.get('/api/v1/health/sync-status', headers=bearer(token))
    check(r.status_code == 403 and r.get_json()['code'] == 'consent_required',
          '[health] outdated privacy consent -> 403 consent_required')
    check(c.get('/api/v1/me', headers=bearer(token)).get_json()['user']['consent_current'] is False,
          '[me] still answers (so the app can explain), consent_current false')
    with app.app_context():
        db.session.get(User, alice_id).privacy_policy_version = PRIVACY_POLICY_VERSION
        db.session.commit()
    r = admin_web.post(f'/admin/users/{alice_id}/health-sync', data={'enabled': '0'})
    with app.app_context():
        on = db.session.get(User, alice_id).health_sync_enabled
    check(r.status_code == 302 and not on, '[admin] admin turns Health sync off')
    check(c.get('/api/v1/health/sync-status', headers=bearer(token)).status_code == 403,
          '[health] turning it off takes effect on the next request')
    r = admin_web.post('/admin/users/999999/health-sync', data={'enabled': '1'})
    check(r.status_code == 302, '[admin] a missing account -> redirect, no error')

    # ── 11. Account delete removes its tokens ──
    with app.app_context():
        bob = make_user('bob@example.test')
        bob_id = bob.id
    bob_token = get_token(c, 'bob@example.test').get_json()['token']
    with app.app_context():
        db.session.delete(db.session.get(User, bob_id))
        db.session.commit()
        left = ApiToken.query.filter_by(user_id=bob_id).count()
    check(left == 0, '[delete] deleting an account deletes its tokens (DB cascade)')
    check(c.get('/api/v1/me', headers=bearer(bob_token)).status_code == 401, '[delete] and the token stops working')

    # ── 12. Prune ──
    with app.app_context():
        old = datetime.utcnow() - timedelta(days=40)
        db.session.add_all([
            ApiToken(user_id=alice_id, token_hash='a' * 64, session_version=0,
                     created_at=old, expires_at=old, revoked_at=None),
            ApiToken(user_id=alice_id, token_hash='b' * 64, session_version=0,
                     created_at=old, expires_at=datetime.utcnow() + timedelta(days=50), revoked_at=old),
        ])
        db.session.commit()
        prune_api_tokens()
        gone = ApiToken.query.filter(ApiToken.token_hash.in_(['a' * 64, 'b' * 64])).count()
    check(gone == 0, '[prune] long-expired and long-revoked tokens deleted')
    check(c.get('/api/v1/me', headers=bearer(token)).status_code == 200, '[prune] a live token survives')

    # ── 13. JSON errors under /api, web unchanged ──
    r = c.get('/api/v1/nope')
    check(r.status_code == 404 and r.get_json() == {'ok': False, 'code': 'not_found', 'error': 'Not found.'},
          '[errors] unknown API path -> JSON 404')
    r = c.put('/api/v1/me', headers=bearer(token))
    check(r.status_code == 405 and r.get_json()['code'] == 'method_not_allowed', '[errors] wrong method -> JSON 405')
    r = admin_web.get('/definitely-not-a-page')
    check(r.status_code == 404 and r.get_json(silent=True) is None, '[errors] a web 404 is still a page, not JSON')
    import app as app_module
    real = app_module._api_user_json
    app_module._api_user_json = lambda u: 1 / 0
    try:
        r = c.get('/api/v1/me', headers=bearer(token))
    finally:
        app_module._api_user_json = real
    j = r.get_json(silent=True) or {}
    check(r.status_code == 500 and j.get('code') == 'server_error' and 'division' not in r.get_data(as_text=True),
          '[errors] a crash under /api/ is a JSON 500 that reveals nothing')

    # ── CSRF exemption covers API views only (Flask-WTF exempts by "module.name") ──
    from app import csrf, API_ENDPOINTS
    exempt = {v.rsplit('.', 1)[-1] for v in csrf._exempt_views if v.startswith('app.')}
    check(exempt == API_ENDPOINTS, f'[csrf] exactly the API views are exempt ({sorted(exempt)})')
    web_views = {ep for ep in app.view_functions if ep not in API_ENDPOINTS}
    check(not any(app.view_functions[ep].__name__ in exempt for ep in web_views),
          '[csrf] no web view shares a name with an exempt API view')

    # ── 14. Web login still works (shared check_credentials) ──
    wl = app.test_client()
    r = wl.post('/login', data={'email': 'alice@example.test', 'password': PW})
    check(r.status_code == 302 and r.headers['Location'].endswith('/'), '[web login] correct password signs in')
    r = app.test_client().post('/login', data={'email': 'alice@example.test', 'password': 'nope'})
    check(r.status_code == 200 and b'Invalid email or password' in r.data, '[web login] wrong password refused')
    r = app.test_client().post('/login', data={'email': 'pending@example.test', 'password': 'whatever'})
    check(r.status_code == 200 and b'verif' in r.data.lower(), '[web login] unverified account told to verify')
    r = app.test_client().post('/login', data={'email': 'gone@example.test', 'password': PW}, follow_redirects=True)
    check(b'deactivated' in r.data, '[web login] deactivated account told so')

    # ── Rate limit: 20 token requests an hour per address, JSON 429 ──
    limiter.enabled = True
    limiter.reset()
    codes = [c.post('/api/v1/auth/token', json={'email': 'x@example.test', 'password': 'y'}).status_code
             for _ in range(21)]
    r = c.post('/api/v1/auth/token', json={'email': 'x@example.test', 'password': 'y'})
    check(codes[:20] == [401] * 20 and codes[20] == 429, f'[rate limit] 21st attempt in an hour -> 429 ({codes[-3:]})')
    check(r.get_json() == {'ok': False, 'code': 'rate_limited', 'error': 'Too many attempts. Try again later.'},
          '[rate limit] 429 is JSON')
    limiter.reset()

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL API AUTH TESTS PASSED')


if __name__ == '__main__':
    main()
