"""Rate-limit key + session cookie flags — exit-gate F5 + F10 (10/3/26).

F5: the limiter keyed on request.remote_addr, which behind Railway is its
internal proxy for every request — so /register's 5/hour was one bucket for the
whole world. _client_ip_key() now keys on Railway's X-Real-IP (the real client,
incl. behind Cloudflare), groups IPv6 by /64, and falls back to remote_addr
(the old shared bucket — never something spoofable) when X-Real-IP is missing
or not a public address. F10: the session cookie is Secure + SameSite=Lax
unless DEBUG=true (local http dev).

Route-level: real POSTs to /register, which is limited to 5/hour. This suite
deliberately does NOT set DEBUG, so the cookie flags are the production ones.
Run: python test_rate_limit_key.py
"""
import os
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('DEBUG', None)

from app import app, limiter

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def register(c, real_ip=None):
    headers = {'X-Real-IP': real_ip} if real_ip else {}
    # Invalid on purpose: the limiter counts the request; no account is created.
    return c.post('/register', data={'email': 'bad'}, headers=headers).status_code


def main():
    c = app.test_client()

    # ── F5: one bucket per real client ──
    limiter.reset()
    codes = [register(c, '8.8.8.8') for _ in range(6)]
    check(codes[:5] == [200] * 5 and codes[5] == 429,
          f'5/hour enforced per client (got {codes})')
    check(register(c, '1.1.1.1') == 200,
          'a different real client is NOT blocked by the first one\'s usage (the F5 bug)')

    # IPv6: same /64 shares a bucket; a different /64 doesn't.
    limiter.reset()
    # Real global prefixes — 2001:db8::/32 is the documentation range, which the
    # key function (correctly) treats as non-public and sends to the fallback.
    codes = [register(c, f'2a00:1450:4001:81a::{i}') for i in range(1, 7)]
    check(codes[5] == 429, 'rotating addresses inside one IPv6 /64 shares one bucket')
    check(register(c, '2a00:1450:4001:81b::1') == 200, 'a different IPv6 /64 has its own bucket')
    check(register(c, '1.0.0.1') == 200, 'IPv6 usage did not land in the shared fallback bucket')

    # IPv4-mapped IPv6 is the IPv4 it wraps: its own bucket, not a shared ::/64.
    limiter.reset()
    for _ in range(5):
        register(c, '::ffff:8.8.4.4')
    check(register(c, '8.8.4.4') == 429, '::ffff:8.8.4.4 shares the bucket of 8.8.4.4')
    check(register(c, '::ffff:8.8.8.8') == 200, 'a different mapped IPv4 is not lumped into ::/64')

    # Fallback: missing / private / garbage X-Real-IP -> remote_addr (one shared bucket).
    limiter.reset()
    for v in (None, '10.0.0.7', '100.64.1.2', 'not-an-ip'):  # + mapped CGNAT below
        register(c, v)
    register(c, '::ffff:100.64.9.9')  # CGNAT in mapped form: is_global is wrongly True on 3.10
    check(register(c, None) == 429,
          'missing/private/CGNAT/garbage X-Real-IP all fall back to remote_addr (shared, unspoofable)')
    check(register(c, '9.9.9.9') == 200, 'a public X-Real-IP is unaffected by the fallback bucket')

    # Malformed shapes fall back too (never a 500, never a fresh bucket).
    limiter.reset()
    # (Surrounding whitespace is trimmed and is NOT malformed — '8.8.8.8 ' is 8.8.8.8.)
    for v in ('8.8.8.8:1234', '[2a00:1450::1]', '8.8.8.8, 1.1.1.1'):
        register(c, v)
    first, second, third = register(c, None), register(c, None), register(c, None)
    check((first, second, third) == (200, 200, 429),
          'port / brackets / joined duplicate header fall back to the shared bucket')
    check(register(c, '2a00:1450:4001:81c::1%eth0') == 200, 'a zone-ID IPv6 neither 500s nor lands in the fallback')

    # The fallback is logged (reason only, no address) so the error sweep sees it.
    import logging

    class Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.msgs = []

        def emit(self, record):
            self.msgs.append(record.getMessage())

    cap = Cap()
    app.logger.addHandler(cap)
    limiter.reset()
    register(c, '10.9.8.7')
    register(c, '8.8.8.8')
    app.logger.removeHandler(cap)
    fb = [m for m in cap.msgs if 'rate-limit key fallback' in m]
    check(len(fb) == 1 and 'not a public address' in fb[0], 'fallback logs one warning with its reason')
    check(not any('10.9.8.7' in m or '8.8.8.8' in m for m in cap.msgs), 'the warning contains no address')

    # ── F10: cookie flags ──
    # A failed login writes the flash message to the session, so a cookie is set.
    r = c.post('/login', data={'email': 'nobody@example.com', 'password': 'x'})
    cookie = ' '.join(v for k, v in r.headers.items() if k.lower() == 'set-cookie')
    check('Secure' in cookie, 'session cookie is Secure without DEBUG')
    check('SameSite=Lax' in cookie, 'session cookie is SameSite=Lax')
    check('HttpOnly' in cookie, 'session cookie is HttpOnly')

    limiter.reset()
    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL RATE-LIMIT-KEY TESTS PASSED')


if __name__ == '__main__':
    main()
