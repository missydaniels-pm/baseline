"""`/protocols/log-day` — the dashboard card's JSON contract (review F11, 10/19/26).

The Today's Protocols card (confirm + 7-day backfill) talks to this one endpoint,
and the React rebuild re-exposes it as API. Nothing tested it. Covered:

  1. `took` is tri-state: true/false upsert the day's row, null un-logs it.
  2. Most recent explicit statement wins (upsert, never a second row).
  3. Rejections (400 + {ok: false}) write NOTHING — including when an earlier
     entry in the same batch was valid: bad date, date outside the editable
     window, empty entries, a non-dict entry, a foreign/unknown protocol, a
     non-bool `took`, a non-string note, a note over 500.
  4. `day_status` matches the shared rule: hollow (nothing logged), amber
     (partial or any Not Today), green (every active protocol logged complete).
  5. User-scoped: another user's protocol id is refused.

Isolated temp DB (set before importing app). Run: python test_log_day.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')

from datetime import datetime, timedelta

from app import app, user_today
from app import PRIVACY_POLICY_VERSION  # fixtures are consented users (consent gate)
from database import db, User, Protocol, ProtocolCompliance

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def main():
    with app.test_request_context():
        today = user_today()

    with app.app_context():
        u = User(privacy_policy_version=PRIVACY_POLICY_VERSION, email='ld@test.com', is_active=True, verified_at=datetime.utcnow(), onboarding_complete=True)
        other = User(privacy_policy_version=PRIVACY_POLICY_VERSION, email='ld2@test.com', is_active=True, verified_at=datetime.utcnow(), onboarding_complete=True)
        u.password_hash = other.password_hash = 'x'
        db.session.add_all([u, other])
        db.session.flush()
        a = Protocol(user_id=u.id, name='Magnesium', type='preventative', status='active')
        b = Protocol(user_id=u.id, name='Riboflavin', type='preventative', status='active')
        foreign = Protocol(user_id=other.id, name='Theirs', type='preventative', status='active')
        db.session.add_all([a, b, foreign])
        db.session.commit()
        aid, bid, fid, uid = a.id, b.id, foreign.id, u.id

        c = app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = uid

        def post(day, entries):
            return c.post('/protocols/log-day', json={'date': str(day), 'entries': entries})

        def rows(day=today):
            return {r.protocol_id: r for r in ProtocolCompliance.query.filter_by(user_id=uid, date=day).all()}

        # 1/4. Tri-state + day_status
        r = post(today, [{'protocol_id': aid, 'took': True, 'note': ''}])
        j = r.get_json()
        check(r.status_code == 200 and j['ok'] and j['day_status'] == 'amber',
              'one of two logged complete → ok, amber (partial)')
        r = post(today, [{'protocol_id': bid, 'took': True, 'note': 'with food'}])
        check(r.get_json()['day_status'] == 'green', 'both complete → green')
        check(rows()[bid].notes == 'with food', 'note saved')
        r = post(today, [{'protocol_id': bid, 'took': False, 'note': None}])
        check(r.get_json()['day_status'] == 'amber' and rows()[bid].took is False,
              'a Not Today → amber, and the row flips (upsert)')
        check(ProtocolCompliance.query.filter_by(user_id=uid, date=today, protocol_id=bid).count() == 1,
              'most recent statement wins — still one row for the day')
        r = post(today, [{'protocol_id': aid, 'took': None}, {'protocol_id': bid, 'took': None}])
        check(r.get_json()['day_status'] == 'hollow' and rows() == {},
              'null un-logs (rows deleted) → hollow')

        # Backfill inside the window, and the window edges.
        r = post(today - timedelta(days=7), [{'protocol_id': aid, 'took': True}])
        check(r.status_code == 200, 'today − 7 is inside the editable window')
        r = post(today + timedelta(days=1), [{'protocol_id': aid, 'took': True}])
        check(r.status_code == 200, 'today + 1 is allowed (browser ahead of the server day)')
        post(today + timedelta(days=1), [{'protocol_id': aid, 'took': None}])

        # 3. Rejections write nothing
        def rejected(label, resp, day=today):
            body = resp.get_json() or {}
            check(resp.status_code == 400 and body.get('ok') is False and rows(day) == {},
                  f'{label} → 400 {{ok: false}}, nothing written')

        rejected('malformed date', c.post('/protocols/log-day', json={'date': 'yesterday', 'entries': [
            {'protocol_id': aid, 'took': True}]}))
        rejected('date older than 7 days', post(today - timedelta(days=8), [{'protocol_id': aid, 'took': True}]),
                 today - timedelta(days=8))
        rejected('date 2 days ahead', post(today + timedelta(days=2), [{'protocol_id': aid, 'took': True}]),
                 today + timedelta(days=2))
        rejected('empty entries', post(today, []))
        rejected('entries not a list', c.post('/protocols/log-day', json={'date': str(today), 'entries': 'x'}))
        rejected('no JSON body', c.post('/protocols/log-day', data='not json'))
        rejected('non-dict entry', post(today, ['oops']))
        rejected('unknown protocol', post(today, [{'protocol_id': 999999, 'took': True}]))
        rejected('another user\'s protocol', post(today, [{'protocol_id': fid, 'took': True}]))
        rejected('took as a string', post(today, [{'protocol_id': aid, 'took': 'false'}]))
        rejected('took as 0', post(today, [{'protocol_id': aid, 'took': 0}]))
        rejected('note not a string', post(today, [{'protocol_id': aid, 'took': True, 'note': 5}]))
        rejected('note over 500', post(today, [{'protocol_id': aid, 'took': True, 'note': 'n' * 501}]))
        rejected('a valid entry followed by an invalid one',
                 post(today, [{'protocol_id': aid, 'took': True}, {'protocol_id': bid, 'took': 'yes'}]))

        # A stopped protocol isn't on the card, but its past days can still be
        # corrected through this endpoint (ownership is the rule, not status).
        db.session.get(Protocol, bid).status = 'stopped'
        db.session.commit()
        r = post(today, [{'protocol_id': aid, 'took': True}])
        check(r.get_json()['day_status'] == 'green',
              'day_status scopes to active protocols: a stopped one no longer makes it partial')

        # A protocol starting after the day being logged isn't due that day, so it
        # doesn't make the day partial (the `start_date <= day` scope).
        later = Protocol(user_id=uid, name='Starts tomorrow', type='preventative', status='active',
                         start_date=today + timedelta(days=1))
        db.session.add(later)
        db.session.commit()
        r = post(today, [{'protocol_id': aid, 'took': True}])
        check(r.get_json()['day_status'] == 'green',
              'a protocol starting after the day is not counted for that day')
        r = post(today + timedelta(days=1), [{'protocol_id': aid, 'took': True}])
        check(r.get_json()['day_status'] == 'amber', '…but is counted from its start day')
        post(today + timedelta(days=1), [{'protocol_id': aid, 'took': None}])

        anon = app.test_client()
        r = anon.post('/protocols/log-day', json={'date': str(today), 'entries': [{'protocol_id': aid, 'took': True}]})
        check(r.status_code == 302, 'signed out → redirected to login, nothing written')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL LOG-DAY TESTS PASSED')


if __name__ == '__main__':
    main()
