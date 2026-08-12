"""Protocol event dating — effective date + the experiment-assessment path.

Runs against an ISOLATED temp SQLite DB (set before importing app, which
bootstraps create_all + run_migrations at import) so the local dev database is
never touched.

Two things are covered:

  1. `_resolve_effective_date()` — ProtocolEvent already separated `date` (when
     the change happened) from `created_at` (when it was recorded); the routes
     just always wrote today into `date`. A stop recorded a week late therefore
     correlated against the wrong days, and that data is unrecoverable after the
     fact. Future dates are refused for the same reason episode onsets are
     (closed 7/14/26): this is a log, not a schedule.

  2. Assessing an experiment with decision pause/stop must WRITE a ProtocolEvent.
     Until 8/12/26 it changed `protocol.status` and recorded nothing, so the most
     decision-driven stop in the app was invisible in the protocol's own history.

Run:  python3 test_protocol_events.py
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmpdir}/test_protocol_events.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from datetime import date, datetime, timedelta

from app import app, _resolve_effective_date
from database import db, User, Protocol, Experiment, ProtocolEvent

failures = []


def check(label, condition):
    if condition:
        print(f'PASS: {label}')
    else:
        print(f'FAIL: {label}')
        failures.append(label)


def main():
    today = date(2026, 8, 12)
    started = date(2026, 7, 1)

    # ── 1. The resolver ──────────────────────────────────────────────────
    d, err = _resolve_effective_date('2026-08-06', today, started)
    check('back-dated within range is accepted', d == date(2026, 8, 6) and err is None)

    d, err = _resolve_effective_date(str(today), today, started)
    check('today is accepted', d == today and err is None)

    # No field submitted at all — the no-JS path. Must fall back, not fail:
    # refusing here would make the form unusable without JavaScript.
    d, err = _resolve_effective_date(None, today, started)
    check('missing value falls back to today (no-JS path)', d == today and err is None)
    d, err = _resolve_effective_date('', today, started)
    check('empty string falls back to today', d == today and err is None)

    d, err = _resolve_effective_date(str(today + timedelta(days=1)), today, started)
    check('future date is refused', d is None and 'future' in (err or ''))

    d, err = _resolve_effective_date('2026-06-01', today, started)
    check('date before the protocol started is refused', d is None and err is not None)

    d, err = _resolve_effective_date('not-a-date', today, started)
    check('unparseable date is refused', d is None and err is not None)

    d, err = _resolve_effective_date('2026-08-06', today, None)
    check('no start_date to compare against still works', d == date(2026, 8, 6) and err is None)

    # Boundary: exactly the start date is legitimate (stopped the day you started).
    d, err = _resolve_effective_date(str(started), today, started)
    check('effective date == start_date is allowed', d == started and err is None)

    # ── 2. The experiment-assessment path writes an event ────────────────
    with app.app_context():
        # verified_at + onboarding_complete matter: require_auth redirects an
        # unverified or mid-onboarding user away before the route ever runs.
        u = User(name='Ev Test', email='ev@test.com', is_active=True,
                 verified_at=datetime.utcnow(), onboarding_complete=True)
        u.password_hash = 'x'
        db.session.add(u)
        db.session.flush()
        p = Protocol(user_id=u.id, name='Magnesium', type='preventative',
                     start_date=started, status='active')
        db.session.add(p)
        db.session.flush()
        e = Experiment(user_id=u.id, protocol_id=p.id, name='Mg trial',
                       hypothesis='h', start_date=started, status='active')
        db.session.add(e)
        db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess['user_id'] = u.id

        pid = p.id
        before = ProtocolEvent.query.filter_by(protocol_id=pid).count()
        client.post(f'/experiments/{e.id}/assess',
                    data={'outcome_rating': '3', 'outcome_notes': 'no help',
                          'decision': 'stop'})

        events = ProtocolEvent.query.filter_by(protocol_id=pid).all()
        check('assessing with "stop" records a ProtocolEvent',
              len(events) == before + 1)
        check('the recorded event is a stop, attributed to the experiment',
              bool(events) and events[-1].event_type == 'stopped'
              and 'Mg trial' in (events[-1].detail or ''))
        check('the protocol really is stopped',
              db.session.get(Protocol, pid).status == 'stopped')

    # ── 3. Route-level: the status floor (reviewers' repros) ─────────────
    with app.app_context():
        u2 = User(name='Floor Test', email='floor@test.com', is_active=True,
                  verified_at=datetime.utcnow(), onboarding_complete=True)
        u2.password_hash = 'x'
        db.session.add(u2)
        db.session.flush()
        p2 = Protocol(user_id=u2.id, name='Riboflavin', type='preventative',
                      start_date=date(2026, 7, 1), status='active',
                      dose_frequency='400mg')
        db.session.add(p2)
        db.session.commit()
        pid2 = p2.id

        c2 = app.test_client()
        with c2.session_transaction() as sess:
            sess['user_id'] = u2.id

        base = {'name': 'Riboflavin', 'start_date': '2026-07-01',
                'dose_frequency': '400mg'}

        def edit(**over):
            return c2.post(f'/protocols/{pid2}/edit',
                           data={**base, **over}, follow_redirects=True)

        def status_log():
            return [(e.event_type, e.date) for e in
                    ProtocolEvent.query.filter_by(protocol_id=pid2)
                    .order_by(ProtocolEvent.date).all()]

        edit(status='paused', effective_date='2026-08-10')
        check('back-dated pause is recorded on its effective date',
              ('paused', date(2026, 8, 10)) in status_log())

        # The reviewers' repro: reactivating BEFORE the pause already on record
        # would make the timeline read "reactivated, then paused" while the live
        # status says active.
        r = edit(status='active', effective_date='2026-08-05')
        check('a status change before the last status event is refused',
              'Check the date' in r.get_data(as_text=True)
              and ('reactivated', date(2026, 8, 5)) not in status_log())

        edit(status='active', effective_date='2026-08-11')
        check('a status change after the last status event is allowed',
              ('reactivated', date(2026, 8, 11)) in status_log())

        # Dose changes don't participate in a status replay, so they stay free.
        edit(status='active', dose_frequency='800mg', effective_date='2026-07-15')
        check('a dose change may still be back-dated freely',
              ('dose_changed', date(2026, 7, 15)) in status_log())

        # A rejected submit must leave the record completely untouched.
        before = db.session.get(Protocol, pid2).status
        edit(status='stopped', effective_date='2026-01-01')
        check('a rejected date leaves the protocol unchanged',
              db.session.get(Protocol, pid2).status == before)

    # ── 4. assess_experiment guards ──────────────────────────────────────
    with app.app_context():
        u3 = User(name='Assess Guard', email='guard@test.com', is_active=True,
                  verified_at=datetime.utcnow(), onboarding_complete=True)
        u3.password_hash = 'x'
        db.session.add(u3)
        db.session.flush()
        # Already paused BEFORE the assessment, and the decision is 'pause'.
        p3 = Protocol(user_id=u3.id, name='Already paused', type='preventative',
                      start_date=date(2026, 7, 1), status='paused')
        db.session.add(p3)
        db.session.flush()
        e3 = Experiment(user_id=u3.id, protocol_id=p3.id, name='Noop trial',
                        hypothesis='h', start_date=date(2026, 7, 1), status='active')
        db.session.add(e3)
        db.session.commit()
        pid3, eid3 = p3.id, e3.id

        c3 = app.test_client()
        with c3.session_transaction() as sess:
            sess['user_id'] = u3.id

        c3.post(f'/experiments/{eid3}/assess',
                data={'outcome_rating': '4', 'outcome_notes': 'n', 'decision': 'pause'})
        # Deliberate: protocol_events logs what happened TO THE PROTOCOL. It was
        # already paused, so nothing happened to it — a second 'paused' with no
        # intervening 'reactivated' would make a replay read as two pauses. The
        # decision itself is recorded on the Experiment.
        check('no duplicate event when the protocol is already in that state',
              ProtocolEvent.query.filter_by(protocol_id=pid3).count() == 0)
        check('the decision is still recorded on the experiment',
              db.session.get(Experiment, eid3).decision == 'pause')

        # Re-assessing a completed experiment must not overwrite the outcome.
        r = c3.post(f'/experiments/{eid3}/assess',
                    data={'outcome_rating': '9', 'outcome_notes': 'changed my mind',
                          'decision': 'stop'})
        exp = db.session.get(Experiment, eid3)
        check('re-assessing a completed experiment is refused',
              exp.decision == 'pause' and exp.outcome_rating == 4
              and db.session.get(Protocol, pid3).status == 'paused')

    print()
    if failures:
        print(f'{len(failures)} PROTOCOL-EVENT TEST(S) FAILED')
        for f in failures:
            print('  -', f)
        raise SystemExit(1)
    print('ALL PROTOCOL-EVENT TESTS PASSED')


if __name__ == '__main__':
    main()
