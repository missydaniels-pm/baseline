"""Every trial stays visible, whatever the outcome (10/19/26).

Runs against an ISOLATED temp SQLite DB (set before importing app, which
bootstraps create_all + run_migrations at import) so the local dev database is
never touched.

Owner report 10/4/26: a medication made migraines worse, and abandoning the
experiment looked like deletion — it recorded only a status (no reason, no end
date, no rating), rendered faded with no result, and its data couldn't be
opened. Covered here:

  1. Abandon asks why (controlled list incl. 'made_worse'), when, and optionally
     a rating — and rejects a missing/forged reason, a future or pre-start end
     date, an off-scale rating and an over-long note, writing nothing.
  2. Abandon and assess are one-shot: an ended experiment can't be re-ended or
     assessed.
  3. The DB CHECK keeps a stop reason off a non-abandoned row.
  4. An ended trial's results page renders, and its "during" window stops on its
     end date (an episode after the end is not counted as "during").
  5. Assess records end_date and rejects (not clamps) a forged rating.
  6. edit_experiment can't blank a running trial's protocol or repoint an ended
     one (review F9).

Run:  python3 test_experiment_outcomes.py
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmpdir}/test_experiment_outcomes.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError

from app import app, user_today
from database import db, User, Protocol, Experiment, Episode

failures = []


def check(label, condition):
    if condition:
        print(f'PASS: {label}')
    else:
        print(f'FAIL: {label}')
        failures.append(label)


def make_user(email):
    u = User(name='T', email=email, is_active=True,
             verified_at=datetime.utcnow(), onboarding_complete=True)
    u.password_hash = 'x'
    db.session.add(u)
    db.session.commit()
    return u


def make_trial(user, start, name='Qulipta trial'):
    p = Protocol(user_id=user.id, name='Qulipta', type='preventative',
                 start_date=start, status='active')
    db.session.add(p)
    db.session.flush()
    e = Experiment(user_id=user.id, protocol_id=p.id, name=name,
                   start_date=start, status='active')
    db.session.add(e)
    db.session.commit()
    return p, e


def client_for(user):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = user.id
    return c


def main():
    with app.test_request_context():
        today = user_today()  # no cookie → server day, same as the routes below
    start = today - timedelta(days=20)

    with app.app_context():
        u = make_user('outcomes@test.com')
        p, e = make_trial(u, start)
        eid, pid = e.id, p.id
        c = client_for(u)

        # ── 1. Abandon form + validation ─────────────────────────────────
        r = c.get(f'/experiments/{eid}/abandon')
        check('abandon page renders', r.status_code == 200 and b'made my symptoms worse' in r.data)

        def abandon(**form):
            data = {'stop_reason': 'made_worse', 'end_date': str(today - timedelta(days=5)),
                    'stop_reason_note': '', 'outcome_rating': ''}
            data.update(form)
            return c.post(f'/experiments/{eid}/abandon', data=data)

        def still_active():
            db.session.expire_all()
            x = db.session.get(Experiment, eid)
            return x.status == 'active' and x.stop_reason is None and x.end_date is None

        abandon(stop_reason='')
        check('missing reason is rejected, nothing written', still_active())
        abandon(stop_reason='bored')
        check('off-list reason is rejected, nothing written', still_active())
        abandon(end_date=str(today + timedelta(days=1)))
        check('future end date is rejected', still_active())
        abandon(end_date=str(start - timedelta(days=1)))
        check('end date before the start is rejected', still_active())
        abandon(end_date='31/12/2026')
        check('malformed end date is rejected', still_active())
        abandon(outcome_rating='11')
        check('off-scale rating is rejected (not clamped)', still_active())
        abandon(stop_reason_note='x' * 501)
        check('note over 500 chars is rejected', still_active())

        r = abandon(stop_reason_note='Migraines got worse in week 2.', outcome_rating='2')
        db.session.expire_all()
        x = db.session.get(Experiment, eid)
        check('abandon records status, reason, end date, note and rating',
              r.status_code == 302 and x.status == 'abandoned' and x.stop_reason == 'made_worse'
              and x.end_date == today - timedelta(days=5)
              and x.stop_reason_note == 'Migraines got worse in week 2.' and x.outcome_rating == 2)
        check('abandoning does not touch the protocol',
              db.session.get(Protocol, pid).status == 'active')

        # ── 2. One-shot ──────────────────────────────────────────────────
        abandon(stop_reason='cost', end_date=str(today))
        db.session.expire_all()
        x = db.session.get(Experiment, eid)
        check('re-abandoning is refused, outcome unchanged',
              x.stop_reason == 'made_worse' and x.end_date == today - timedelta(days=5))
        r = c.get(f'/experiments/{eid}/abandon')
        check('abandon page redirects for an ended experiment', r.status_code == 302)
        c.post(f'/experiments/{eid}/assess', data={'outcome_rating': '9', 'decision': 'continue'})
        db.session.expire_all()
        check('an abandoned experiment cannot be assessed',
              db.session.get(Experiment, eid).status == 'abandoned')

        # Blank rating is allowed (optional) and stays NULL.
        _, e2 = make_trial(u, start, name='Second')
        c.post(f'/experiments/{e2.id}/abandon',
               data={'stop_reason': 'ineffective', 'end_date': '', 'outcome_rating': ''})
        db.session.expire_all()
        x2 = db.session.get(Experiment, e2.id)
        check('blank rating stays NULL and blank end date means today',
              x2.status == 'abandoned' and x2.outcome_rating is None and x2.end_date == today)

        # ── 3. DB CHECK ──────────────────────────────────────────────────
        bad = Experiment(user_id=u.id, name='bad', start_date=start, status='active',
                         stop_reason='cost')
        db.session.add(bad)
        try:
            db.session.commit()
            check('CHECK rejects a stop reason on a non-abandoned row', False)
        except IntegrityError:
            db.session.rollback()
            check('CHECK rejects a stop reason on a non-abandoned row', True)

        # ── 4. Results page + window ─────────────────────────────────────
        # One episode inside the trial, one after it ended. Only the first is "during".
        for d in (today - timedelta(days=10), today - timedelta(days=2)):
            db.session.add(Episode(user_id=u.id, onset=datetime.combine(d, datetime.min.time()).replace(hour=9)))
        db.session.commit()
        r = c.get('/experiments')
        check('experiments list shows the ended trial with its reason and a results link',
              b'Ended early' in r.data and b'It made my symptoms worse' in r.data
              and f'/experiments/{eid}/assess'.encode() in r.data
              and b'opacity:0.55' not in r.data)
        r = c.get(f'/experiments/{eid}/assess')
        check('results page renders read-only for an ended trial',
              r.status_code == 200 and b'Experiment Results' in r.data
              and b'Complete Assessment' not in r.data)
        # 15 days during → 2.1 weeks; 1 episode → 0.5/week. Counting the post-end
        # episode too would read 1.0.
        check('"during" stops at the end date', b'>0.5<' in r.data.replace(b' ', b'').replace(b'\n', b''))

        # ── 5. Assess ────────────────────────────────────────────────────
        _, e3 = make_trial(u, today - timedelta(days=30), name='Third')
        c.post(f'/experiments/{e3.id}/assess', data={'outcome_rating': '99', 'decision': 'continue'})
        db.session.expire_all()
        check('assess rejects an off-scale rating instead of clamping',
              db.session.get(Experiment, e3.id).status == 'active')
        c.post(f'/experiments/{e3.id}/assess', data={'outcome_rating': '7', 'decision': 'continue',
                                                     'outcome_notes': 'helped'})
        db.session.expire_all()
        x3 = db.session.get(Experiment, e3.id)
        check('assess records completed + end_date = today',
              x3.status == 'completed' and x3.end_date == today and x3.outcome_rating == 7)

        # ── 6. edit_experiment protocol rules (review F9) ────────────────
        p4, e4 = make_trial(u, start, name='Fourth')
        other = Protocol(user_id=u.id, name='Magnesium', type='preventative', status='active')
        db.session.add(other)
        db.session.commit()
        base = {'name': 'Fourth', 'start_date': str(start), 'stabilization_weeks': '3'}
        c.post(f'/experiments/{e4.id}/edit', data=dict(base, protocol_id=''))
        db.session.expire_all()
        check('blanking a running trial\'s protocol is refused',
              db.session.get(Experiment, e4.id).protocol_id == p4.id)
        c.post(f'/experiments/{e4.id}/edit', data=dict(base, protocol_id='999999'))
        db.session.expire_all()
        check('an unknown protocol id is refused, not silently detached',
              db.session.get(Experiment, e4.id).protocol_id == p4.id)
        c.post(f'/experiments/{e4.id}/edit', data=dict(base, protocol_id=str(other.id)))
        db.session.expire_all()
        check('a running trial can switch to another own protocol',
              db.session.get(Experiment, e4.id).protocol_id == other.id)

        c.post(f'/experiments/{eid}/edit',
               data={'name': 'Renamed', 'start_date': str(start), 'stabilization_weeks': '3',
                     'protocol_id': str(other.id)})
        db.session.expire_all()
        x = db.session.get(Experiment, eid)
        check('an ended trial keeps its protocol (rename still works)',
              x.protocol_id == pid and x.name == 'Renamed')

        c.post(f'/experiments/{eid}/edit',
               data={'name': 'Renamed', 'start_date': str(today), 'stabilization_weeks': '3'})
        db.session.expire_all()
        check('an ended trial\'s start date cannot move past its end date',
              db.session.get(Experiment, eid).start_date == start)

        # Another user's experiment is not reachable.
        u2 = make_user('other@test.com')
        c2 = client_for(u2)
        r = c2.get(f'/experiments/{e4.id}/abandon')
        check('abandon is user-scoped (404 for another user)', r.status_code == 404)

    print()
    if failures:
        print(f'{len(failures)} EXPERIMENT-OUTCOME TEST(S) FAILED')
        for f in failures:
            print('  -', f)
        raise SystemExit(1)
    print('ALL EXPERIMENT-OUTCOME TESTS PASSED')


if __name__ == '__main__':
    main()
