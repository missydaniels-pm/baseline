"""TEMPORARY — goes with _log_proxy_shape() (exit-gate F5, 10/3/26); delete both
in the ProxyFix commit.

The diagnostic must never write an IP address or raw header text to the logs,
even when every header is attacker-controlled (QA 10/3/26 found X-Forwarded-Proto
and Host echoed verbatim). Sends hostile headers and asserts the logged line has
no IPv4/IPv6 address and none of the injected text, and that plain /login logs
nothing. Run: python test_proxy_diag.py
"""
import logging
import os
import re
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')

from app import app

FAILS = []


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


def main():
    cap = Capture()
    app.logger.addHandler(cap)
    c = app.test_client()

    c.get('/login')
    check(not cap.lines, 'plain GET /login logs nothing')

    hostile = {
        'X-Forwarded-For': '8.8.8.8, 2001:4860:4860::8888, not-an-ip, 104.16.0.1',
        'X-Real-IP': '104.16.0.1',
        'CF-Connecting-IP': '8.8.8.8',
        'X-Envoy-External-Address': '9.9.9.9',
        'X-Forwarded-Proto': 'https\x01evil 9.9.9.9, http',
        'Forwarded': 'for=1.2.3.4',
    }
    r = c.get('/login?proxy_diag=1', headers=hostile, base_url='http://1.2.3.4')
    check(r.status_code == 200, f'diagnostic request still renders login (got {r.status_code})')
    line = ' '.join(l for l in cap.lines if 'proxy-diag' in l)
    check(bool(line), 'diagnostic line was logged')
    check(not re.search(r'\b\d{1,3}(\.\d{1,3}){3}\b', line), 'no IPv4 address in the log line')
    check(not re.search(r'[0-9a-f]{1,4}(:[0-9a-f]{0,4}){2,}', line, re.I), 'no IPv6 address in the log line')
    check('evil' not in line and 'not-an-ip' not in line, 'no raw header text in the log line')
    check('host=other' in line, 'attacker-controlled Host reduced to a fixed word')
    check('xf_proto=other,http' in line, 'X-Forwarded-Proto reduced to a fixed vocabulary')
    check('-4:public4=cf' in line and '-1:public4=real' in line, 'positions labelled from the right with tags')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL PROXY-DIAG TESTS PASSED')


if __name__ == '__main__':
    main()
