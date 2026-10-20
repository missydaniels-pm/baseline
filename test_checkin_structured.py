"""AI check-in structured outputs — one valid reply, never raw model text (bug 10/3/26).

The model sometimes "corrected itself" mid-reply: a JSON object, then "Wait — I
mis-assigned…", then a second object. parse_checkin()'s greedy match spanned
both, json.loads failed, and the raw text was shown as the reply with nothing
logged. Fix: the request carries output_config.format = CHECKIN_SCHEMA, so the
API returns exactly one schema-valid object; anything unusable (cut off,
refused, unparseable) becomes CHECKIN_UNREADABLE_MSG and is logged server-side.

Guards: the schema is sent; a structured reply's symptom list (scale + binary)
is folded back and written correctly; the old double-JSON text, a max_tokens
cut-off and a refusal each write nothing and never show the raw text.

Isolated temp DB (set before importing app). The Anthropic endpoint is pointed
at a local stub via ANTHROPIC_BASE_URL. Run: python test_checkin_structured.py
"""
import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['ANTHROPIC_API_KEY'] = 'sk-ant-test-dummy'

from datetime import datetime

from app import app, CHECKIN_SCHEMA, CHECKIN_UNREADABLE_MSG
from app import PRIVACY_POLICY_VERSION  # fixtures are consented users (consent gate)
from database import db, User, Episode, CheckIn, Symptom, SymptomScore, EpisodeIntervention, Protocol

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


# What the stub answers next: (text, stop_reason). Set per case.
NEXT = {'text': '', 'stop_reason': 'end_turn'}
CAPTURED = {}


class Stub(BaseHTTPRequestHandler):
    """Minimal Messages API: records the request, answers with NEXT."""
    def do_POST(self):
        CAPTURED['body'] = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        body = json.dumps({
            'id': 'msg_stub', 'type': 'message', 'role': 'assistant',
            'model': 'claude-sonnet-4-6', 'stop_reason': NEXT['stop_reason'], 'stop_sequence': None,
            'content': [{'type': 'text', 'text': NEXT['text']}],
            'usage': {'input_tokens': 1, 'output_tokens': 1},
        }).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def reply(had_episode, scores=None, response='Logged.'):
    return json.dumps({
        'had_episode': had_episode,
        'episode_data': {
            'onset_time_expr': None, 'onset_time_type': 'now',
            'symptom_scores': scores or [], 'functional_impairment': None,
            'interventions': [], 'triggers': [], 'notes': None,
        },
        'protocol_compliance': [],
        'suggested_response': response,
    })


def main():
    srv = HTTPServer(('127.0.0.1', 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ['ANTHROPIC_BASE_URL'] = f'http://127.0.0.1:{srv.server_address[1]}'

    with app.app_context():
        u = User(privacy_policy_version=PRIVACY_POLICY_VERSION, email='s@test.com', is_active=True, verified_at=datetime.utcnow(),
                 onboarding_complete=True, ai_logging_enabled=True)
        u.password_hash = 'x'
        db.session.add(u)
        db.session.commit()
        uid = u.id
        pain = Symptom(user_id=uid, name='Headache pain', input_type='scale', is_active=True)
        nausea = Symptom(user_id=uid, name='Nausea', input_type='binary', is_active=True)
        db.session.add_all([pain, nausea])
        db.session.commit()
        pain_id, nausea_id = pain.id, nausea.id

        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = uid

        def post():
            return c.post('/checkin', data={'message': 'migraine, a 7, nauseous'},
                          follow_redirects=True)

        def last_reply():
            row = (CheckIn.query.filter_by(user_id=uid, role='assistant')
                   .order_by(CheckIn.id.desc()).first())
            return row.content if row else None

        # 1. The schema rides on the request.
        NEXT.update(text=reply(False), stop_reason='end_turn')
        post()
        fmt = ((CAPTURED.get('body') or {}).get('output_config') or {}).get('format') or {}
        check(fmt.get('type') == 'json_schema', 'request carries output_config.format json_schema')
        check(fmt.get('schema') == CHECKIN_SCHEMA, 'request schema is CHECKIN_SCHEMA')

        # 2. Structured reply: symptom list folds back; scale and binary land in their own columns.
        NEXT.update(text=reply(True, [{'symptom_id': pain_id, 'value': 7},
                                      {'symptom_id': nausea_id, 'value': True},
                                      {'symptom_id': 99999, 'value': 5}],
                               response='Logged — 7/10, nausea.'),
                    stop_reason='end_turn')
        r = post()
        check(r.status_code == 200, f'[structured] returns 200 (got {r.status_code})')
        ep = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        check(ep is not None, '[structured] episode written')
        rows = {s.symptom_id: s for s in SymptomScore.query.filter_by(episode_id=ep.id).all()} if ep else {}
        check(pain_id in rows and rows[pain_id].score == 7 and rows[pain_id].value_bool is None,
              '[structured] scale symptom -> score=7, value_bool NULL')
        check(nausea_id in rows and rows[nausea_id].value_bool is True and rows[nausea_id].score is None,
              '[structured] binary symptom -> value_bool=True, score NULL')
        check(len(rows) == 2, "[structured] another user's / unknown symptom id is ignored")
        check(last_reply() == 'Logged — 7/10, nausea.', '[structured] model reply persisted')

        # A boolean on a scale symptom is dropped, not saved as score 1.
        NEXT.update(text=reply(True, [{'symptom_id': pain_id, 'value': True}]), stop_reason='end_turn')
        post()
        ep2 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        check(ep2.id != ep.id and SymptomScore.query.filter_by(episode_id=ep2.id).count() == 0,
              '[structured] boolean on a scale symptom is not saved as a score')

        # 3-5. Unusable replies: nothing written, raw text never shown.
        double = (reply(True, [{'symptom_id': pain_id, 'value': 6}]) +
                  "\n\nWait — I mis-assigned that. Let me correct it:\n\n" +
                  reply(True, [{'symptom_id': pain_id, 'value': 6}]))
        cases = [
            ('double JSON + aside', double, 'end_turn'),
            ('cut off at max_tokens', reply(True)[:60], 'max_tokens'),
            ('refusal', '', 'refusal'),
            ('not an object', '[1, 2]', 'end_turn'),
        ]
        for label, text, stop in cases:
            NEXT.update(text=text, stop_reason=stop)
            before = Episode.query.filter_by(user_id=uid).count()
            r = post()
            check(r.status_code == 200, f'[{label}] returns 200 (got {r.status_code})')
            check(Episode.query.filter_by(user_id=uid).count() == before, f'[{label}] no Episode written')
            check(last_reply() == CHECKIN_UNREADABLE_MSG, f'[{label}] reply is the fixed unreadable wording')

    # Review F8 (10/19/26): the AI path caps and continues. An over-long AI
    # intervention name used to raise DataError on PostgreSQL (VARCHAR(200)) → 500,
    # losing the message; AI notes were uncapped.
    with app.app_context():
        long_reply = json.loads(reply(True))
        long_reply['episode_data']['interventions'] = [{'name': 'Z' * 250, 'effectiveness': 7,
                                                        'time_to_relief_hours': 1}]
        long_reply['episode_data']['notes'] = 'n' * 800
        NEXT.update(text=json.dumps(long_reply), stop_reason='end_turn')
        c = app.test_client()
        u = User.query.filter_by(email='s@test.com').first()
        with c.session_transaction() as sess:
            sess['user_id'] = u.id
        r = c.post('/checkin', data={'message': 'migraine, took a long-named thing'}, follow_redirects=True)
        ep = Episode.query.filter_by(user_id=u.id).order_by(Episode.id.desc()).first()
        ei = EpisodeIntervention.query.filter_by(episode_id=ep.id).first() if ep else None
        check(r.status_code == 200 and ep is not None and len(ep.notes or '') == 500,
              '[F8] AI notes capped at 500, episode still logged')
        check(ei is not None and len(db.session.get(Protocol, ei.protocol_id).name) == 200,
              '[F8] AI intervention name capped at 200, intervention still linked')

    srv.shutdown()
    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL CHECK-IN STRUCTURED-OUTPUT TESTS PASSED')


if __name__ == '__main__':
    main()
