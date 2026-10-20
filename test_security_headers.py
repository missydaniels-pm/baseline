"""Security + cache headers, logout cache clear — review F3 + F10 (10/19/26).

F3: every dynamic response is `Cache-Control: no-store` (a route's
own Cache-Control wins), and logout sends `Clear-Site-Data: "cache"`.
F10: nosniff, X-Frame-Options DENY + CSP frame-ancestors 'none',
Referrer-Policy same-origin, and HSTS when not in debug.

Covered: signed-in pages, JSON, public pages, redirects, 404s, a 405 and the
CSRF-error redirect get no-store + the headers; static files, /sw.js and /offline stay cacheable; logout clears
the cache; HSTS is absent under DEBUG (local http); every page registers the
service worker at its versioned URL (/sw.js?v=<hash>), never bare /sw.js —
Cloudflare edge-caches .js, and one page registering a stale bare URL would
swap the old worker back in. Like test_rate_limit_key,
this suite does NOT set DEBUG, so the headers are the production ones.

Isolated temp DB. Run: python test_security_headers.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('DEBUG', None)
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime

from app import app, SW_VERSION, HSTS_VALUE
from database import db, User

FAILS = []

HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Content-Security-Policy': "frame-ancestors 'none'",
    'Referrer-Policy': 'same-origin',
    'Strict-Transport-Security': HSTS_VALUE,
}


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def check_headers(r, label, cacheable=False):
    for k, v in HEADERS.items():
        check(r.headers.get(k) == v, f'[{label}] {k}: {v} (got {r.headers.get(k)!r})')
    cc = r.headers.get('Cache-Control') or ''
    if cacheable:
        check('no-store' not in cc, f'[{label}] stays cacheable (Cache-Control {cc!r})')
    else:
        check(cc == 'no-store', f'[{label}] Cache-Control: no-store (got {cc!r})')


def main():
    with app.app_context():
        u = User(name='T', email='headers@test.com', is_active=True, verified_at=datetime.utcnow(),
                 onboarding_complete=True, has_seen_tour=True)
        u.password_hash = 'x'
        db.session.add(u)
        db.session.commit()
        uid = u.id

    # https base URL: without DEBUG the session cookie is Secure.
    c = app.test_client()
    c.base_url = 'https://localhost'
    with c.session_transaction() as sess:
        sess['user_id'] = uid

    # ── Signed-in pages and JSON: no-store + headers ──
    for path in ('/', '/episodes', '/checkin', '/settings'):
        r = c.get(path, base_url='https://localhost')
        check(r.status_code == 200, f'[{path}] renders signed in (got {r.status_code})')
        check_headers(r, path)
    r = c.post('/protocols/log-day', json={'date': 'not-a-date', 'protocols': []},
               base_url='https://localhost')
    check(r.is_json, f'[log-day] JSON response (got {r.status_code})')
    check_headers(r, 'log-day JSON')

    # ── Doctor summary keeps its own explicit no-store (route header wins) ──
    r = c.get('/reports/doctor-summary', base_url='https://localhost')
    check(r.status_code == 200 and r.headers.get('Cache-Control') == 'no-store',
          f'[doctor summary] no-store (got {r.status_code}, {r.headers.get("Cache-Control")!r})')

    # ── Public pages, redirects, 404s ──
    anon = app.test_client()
    for path, code in (('/login', 200), ('/privacy', 200), ('/', 302), ('/no-such-page', 404)):
        r = anon.get(path, base_url='https://localhost')
        check(r.status_code == code, f'[anon {path}] status {code} (got {r.status_code})')
        check_headers(r, f'anon {path}')

    # ── PWA shell + static stay cacheable (sw.js pre-caches /offline) ──
    for path in ('/sw.js', '/offline', '/static/css/style.css'):
        r = anon.get(path, base_url='https://localhost')
        check(r.status_code == 200, f'[{path}] 200 (got {r.status_code})')
        check_headers(r, path, cacheable=True)
        r.close()
    r = anon.get('/sw.js', base_url='https://localhost')
    check(r.headers.get('Content-Type', '').startswith('application/javascript'),
          '[sw.js] still served as JavaScript (nosniff would block a wrong type)')
    r.close()

    # ── 405 and the CSRF-error redirect (the one request.referrer reader) ──
    r = anon.post('/privacy', base_url='https://localhost')
    check(r.status_code == 405, f'[405] POST /privacy is 405 (got {r.status_code})')
    check_headers(r, '405')
    app.config['WTF_CSRF_ENABLED'] = True
    try:
        r = anon.post('/login', data={'email': 'x@y.z', 'password': 'p'},
                      headers={'Referer': 'https://localhost/login'}, base_url='https://localhost')
        check(r.status_code == 302 and r.headers.get('Location', '').endswith('/login'),
              f'[csrf error] same-origin redirect back still works (got {r.status_code} '
              f'{r.headers.get("Location")!r})')
        check_headers(r, 'csrf error')
    finally:
        app.config['WTF_CSRF_ENABLED'] = False

    # ── Every page registers the versioned worker URL ──
    for client, path in ((c, '/'), (anon, '/login'), (anon, '/register')):
        body = client.get(path, base_url='https://localhost').get_data(as_text=True)
        check(f"register('/sw.js?v={SW_VERSION}')" in body and "register('/sw.js')" not in body,
              f'[{path}] registers /sw.js?v={SW_VERSION}')
    r = anon.get(f'/sw.js?v={SW_VERSION}', base_url='https://localhost')
    check(r.status_code == 200 and b'CACHE_NAME' in r.data, '[sw.js?v=] the versioned URL serves the worker')
    r.close()

    # ── Logout clears the browser cache and signs out ──
    r = c.get('/logout', base_url='https://localhost')
    check(r.status_code == 302 and r.headers.get('Clear-Site-Data') == '"cache"',
          f'[logout] Clear-Site-Data: "cache" (got {r.headers.get("Clear-Site-Data")!r})')
    check_headers(r, 'logout')
    r = c.get('/', base_url='https://localhost')
    check(r.status_code == 302, '[logout] the session is gone afterwards')

    # ── DEBUG (local http dev): no HSTS, the rest unchanged ──
    app.debug = True
    try:
        r = anon.get('/login')
        check('Strict-Transport-Security' not in r.headers, '[debug] no HSTS over local http')
        check(r.headers.get('X-Frame-Options') == 'DENY', '[debug] other headers still set')
    finally:
        app.debug = False

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL SECURITY HEADER TESTS PASSED')


if __name__ == '__main__':
    main()
