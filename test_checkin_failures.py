"""AI check-in failure paths — what the user sees when the Anthropic call
can't complete (exit-gate F1 follow-through, 9/4/26).

Since the Dockerfile runs gunicorn with --threads 4 (gthread), gunicorn's
--timeout no longer caps a request, so parse_checkin() owns its own bound
(timeout=60, max_retries=1) and its own failure handling. Guards: a refused
connection, a 503, a 429 and a 400 all return 200 with an assistant reply
(transient vs rejected wording), write no Episode, and persist the reply as
the assistant CheckIn row — same shape as the pre-existing auth-failure branch.

Isolated temp DB (set before importing app). The Anthropic endpoint is pointed
at a local stub via ANTHROPIC_BASE_URL. Run: python test_checkin_failures.py
"""
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-test-dummy'

from datetime import datetime

from app import app, CHECKIN_TRANSIENT_MSG, CHECKIN_REJECTED_MSG
from database import db, User, Episode, CheckIn

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


STATUS = {'code': 503}


class Stub(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(STATUS['code'])
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"type":"error","error":{"type":"stub_error","message":"stub"}}')

    def log_message(self, *a):
        pass


def main():
    srv = HTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    stub_url = f'http://127.0.0.1:{srv.server_address[1]}'

    with app.app_context():
        u = User(email='ai@test.com', is_active=True, verified_at=datetime.utcnow(),
                 onboarding_complete=True, ai_logging_enabled=True)
        u.password_hash = 'x'
        db.session.add(u)
        db.session.commit()
        uid = u.id
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = uid

        cases = [
            ('refused connection', 'http://127.0.0.1:9', None, CHECKIN_TRANSIENT_MSG),
            ('503 upstream', stub_url, 503, CHECKIN_TRANSIENT_MSG),
            ('429 rate limit', stub_url, 429, CHECKIN_TRANSIENT_MSG),
            ('400 bad request', stub_url, 400, CHECKIN_REJECTED_MSG),
        ]
        for label, base, code, expected in cases:
            os.environ['ANTHROPIC_BASE_URL'] = base
            if code:
                STATUS['code'] = code
            before = CheckIn.query.filter_by(user_id=uid, role='assistant').count()
            r = c.post('/checkin', data={'message': 'had a migraine this morning'},
                       follow_redirects=True)
            check(r.status_code == 200, f'[{label}] returns 200 (got {r.status_code})')
            last = (CheckIn.query.filter_by(user_id=uid, role='assistant')
                    .order_by(CheckIn.id.desc()).first())
            check(last is not None and last.content == expected,
                  f'[{label}] assistant reply is the expected wording')
            check(CheckIn.query.filter_by(user_id=uid, role='assistant').count() == before + 1,
                  f'[{label}] exactly one assistant row persisted')
            check(Episode.query.filter_by(user_id=uid).count() == 0,
                  f'[{label}] no Episode written')

        check(CHECKIN_TRANSIENT_MSG != CHECKIN_REJECTED_MSG,
              'transient and rejected messages are distinct')

    srv.shutdown()
    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL CHECK-IN FAILURE TESTS PASSED')


if __name__ == '__main__':
    main()
