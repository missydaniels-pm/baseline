"""AI check-in scope — the model is told what check-in can and can't do (bug 9/25/26).

Missy asked the check-in to create an experiment; the reply said it would and
nothing was created. Check-in has no experiment-writing path (it only logs a new
episode + preventative compliance), so the fix is in the system prompt: name the
two things check-in can do, list what it can't, and point to the right page.

The model's wording can't be asserted in CI, so this guards the two things code
owns: (1) the scope instructions actually reach the API, for a user with an
active experiment and for a brand-new user with nothing set up; (2) an
"add an experiment" message writes no Experiment / Protocol / Episode, and the
reply is persisted as the assistant turn.

Isolated temp DB (set before importing app). The Anthropic endpoint is pointed
at a local stub via ANTHROPIC_BASE_URL. Run: python test_checkin_scope.py
"""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-test-dummy'

from datetime import datetime, date

from app import app
from database import db, User, Episode, CheckIn, Experiment, Protocol

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


REPLY = ("Check-in can't start experiments — use the Experiments page, "
         "\"+ Start Experiment\".")
CAPTURED = {}


class Stub(BaseHTTPRequestHandler):
    """Minimal Messages API: records the request, answers with a no-episode parse."""
    def do_POST(self):
        CAPTURED['body'] = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        parsed = {'had_episode': False, 'episode_data': None, 'protocol_compliance': [],
                  'suggested_response': REPLY}
        body = json.dumps({
            'id': 'msg_stub', 'type': 'message', 'role': 'assistant',
            'model': 'claude-sonnet-4-6', 'stop_reason': 'end_turn', 'stop_sequence': None,
            'content': [{'type': 'text', 'text': json.dumps(parsed)}],
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


# Phrases that must reach the model — the boundary, the specific action from
# the bug, the follow-up-episode case, and where to send the user instead.
REQUIRED = [
    'WHAT THIS CHECK-IN CAN DO',
    'It CANNOT do anything else',
    'start, change, end or assess an experiment',
    'only updating an episode they already told you about',
    "don't log it again",
    'new triggers are only suggested',
    'never say or imply you did it',
    '"+ Start Experiment"',
    'Settings → Manage Triggers',
    'never an action from the CANNOT list above',
]


def make_user(email):
    u = User(email=email, is_active=True, verified_at=datetime.utcnow(),
             onboarding_complete=True, ai_logging_enabled=True)
    u.password_hash = 'x'
    db.session.add(u)
    db.session.commit()
    return u.id


def counts(uid):
    return (Experiment.query.filter_by(user_id=uid).count(),
            Protocol.query.filter_by(user_id=uid).count(),
            Episode.query.filter_by(user_id=uid).count())


def main():
    srv = HTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ['ANTHROPIC_BASE_URL'] = f'http://127.0.0.1:{srv.server_address[1]}'

    with app.app_context():
        # A user mid-experiment (the prompt shows the experiment for context —
        # the case most likely to invite "I'll set up another one").
        busy = make_user('busy@test.com')
        p = Protocol(user_id=busy, name='Magnesium', type='preventative',
                     status='active', start_date=date(2026, 9, 1))
        db.session.add(p)
        db.session.flush()
        db.session.add(Experiment(user_id=busy, name='Magnesium trial', protocol_id=p.id,
                                  start_date=date(2026, 9, 1), status='active'))
        db.session.commit()
        # And a brand-new user with nothing set up.
        fresh = make_user('fresh@test.com')

        for label, uid in (('active experiment', busy), ('new user', fresh)):
            c = app.test_client()
            with c.session_transaction() as s:
                s['user_id'] = uid
            before = counts(uid)
            CAPTURED.clear()
            r = c.post('/checkin', data={'message': 'Can you add a new experiment for riboflavin?'},
                       follow_redirects=True)
            check(r.status_code == 200, f'[{label}] returns 200 (got {r.status_code})')

            # Whitespace-collapsed, so re-wrapping the prompt prose doesn't fail CI.
            system = ' '.join(((CAPTURED.get('body') or {}).get('system') or '').split())
            check(bool(system), f'[{label}] system prompt was sent to the API')
            for phrase in REQUIRED:
                check(phrase in system, f'[{label}] system prompt contains {phrase!r}')

            check(counts(uid) == before,
                  f'[{label}] no Experiment/Protocol/Episode written (before {before}, after {counts(uid)})')
            last = (CheckIn.query.filter_by(user_id=uid, role='assistant')
                    .order_by(CheckIn.id.desc()).first())
            check(last is not None and last.content == REPLY,
                  f'[{label}] model reply persisted as the assistant turn')

    srv.shutdown()
    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL CHECK-IN SCOPE TESTS PASSED')


if __name__ == '__main__':
    main()
