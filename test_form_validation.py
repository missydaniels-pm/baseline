"""Form validation sweep — exit-gate F7 + F8 + F9 (10/3/26).

F7: enum-ish fields were whitelisted on the AI path but stored raw from the
forms — functional_impairment (episode new/edit), protocol status (new/edit;
an off-list status also became a ProtocolEvent.event_type), experiment
assessment decision. F8: experiment routes silently replaced a malformed start
date / stabilization weeks instead of rejecting like the protocol siblings.
Same class, found in the sweep: a malformed episode onset 500'd. F9: a failed
verification-email send was silent (gated on a retired MAIL_USERNAME).

Every bad value: 200 with the form re-rendered (or a redirect back), a flash,
and NO write. Good values still save; blank impairment is stored as NULL.

Isolated temp DB (set before importing app). RESEND_API_KEY is unset, so the
verification send fails — which is exactly the F9 case.
Run: python test_form_validation.py
"""
import os
import tempfile

os.environ['DATABASE_URL'] = f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime, date, timedelta

from app import app
from database import db, User, Episode, Protocol, ProtocolEvent, Experiment

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def main():
    with app.app_context():
        u = User(email='v@test.com', is_active=True, verified_at=datetime.utcnow(),
                 onboarding_complete=True)
        u.password_hash = 'x'
        db.session.add(u)
        db.session.commit()
        uid = u.id
        c = app.test_client()
        with c.session_transaction() as s:
            s['user_id'] = uid

        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')

        # ── Episodes: functional_impairment (F7) + onset parse (same class) ──
        def episodes():
            return Episode.query.filter_by(user_id=uid).count()

        for label, data in [
            ('off-list impairment', {'onset': yesterday, 'functional_impairment': 'fine_ish'}),
            ('malformed onset', {'onset': 'not-a-date', 'functional_impairment': ''}),
        ]:
            before = episodes()
            r = c.post('/episodes/new', data=data)
            check(r.status_code == 200, f'[new episode, {label}] re-renders 200 (got {r.status_code})')
            check(episodes() == before, f'[new episode, {label}] nothing written')
            # Jinja escapes the apostrophe ("isn&#39;t"), so match around it.
            check(b'one of the options' in r.data or b'look right' in r.data,
                  f'[new episode, {label}] shows the validation message')

        with_seconds = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%S')
        before = episodes()
        c.post('/episodes/new', data={'onset': with_seconds, 'functional_impairment': ''})
        check(episodes() == before + 1, '[new episode] onset with seconds (some browsers) accepted')

        c.post('/episodes/new', data={'onset': yesterday, 'functional_impairment': ''})
        ep = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        check(ep is not None and ep.functional_impairment is None,
              '[new episode] blank impairment stored as NULL, not ""')
        c.post('/episodes/new', data={'onset': yesterday, 'functional_impairment': 'cannot_work'})
        ep = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        check(ep.functional_impairment == 'cannot_work', '[new episode] listed impairment saved')

        ep_id, ep_onset = ep.id, ep.onset
        for label, data in [
            ('off-list impairment', {'onset': ep_onset.strftime('%Y-%m-%dT%H:%M'),
                                     'functional_impairment': 'x' * 40, 'notes': 'changed'}),
            ('malformed onset', {'onset': '2026-13-45T99:99', 'functional_impairment': 'working_normally',
                                 'notes': 'changed'}),
        ]:
            r = c.post(f'/episodes/{ep_id}/edit', data=data)
            db.session.expire_all()
            e = db.session.get(Episode, ep_id)
            check(r.status_code == 200, f'[edit episode, {label}] re-renders 200 (got {r.status_code})')
            check(e.functional_impairment == 'cannot_work' and e.onset == ep_onset and e.notes != 'changed',
                  f'[edit episode, {label}] nothing changed')

        # ── Protocols: status (F7) ──
        before = Protocol.query.filter_by(user_id=uid).count()
        r = c.post('/protocols/new', data={'name': 'Magnesium', 'status': 'retired_forever'})
        check(r.status_code == 200 and Protocol.query.filter_by(user_id=uid).count() == before,
              '[new protocol] off-list status rejected, nothing written')
        c.post('/protocols/new', data={'name': 'Magnesium', 'status': 'active'})
        p = Protocol.query.filter_by(user_id=uid, name='Magnesium').first()
        check(p is not None and p.status == 'active', '[new protocol] listed status saved')

        events_before = ProtocolEvent.query.filter_by(protocol_id=p.id).count()
        r = c.post(f'/protocols/{p.id}/edit', data={'name': 'Magnesium', 'status': 'bogus'})
        db.session.expire_all()
        p = db.session.get(Protocol, p.id)
        check(r.status_code == 200 and p.status == 'active', '[edit protocol] off-list status rejected')
        check(ProtocolEvent.query.filter_by(protocol_id=p.id).count() == events_before,
              '[edit protocol] no ProtocolEvent written for the off-list status')
        c.post(f'/protocols/{p.id}/edit', data={'name': 'Magnesium', 'status': 'paused'})
        db.session.expire_all()
        check(db.session.get(Protocol, p.id).status == 'paused', '[edit protocol] listed status saved')

        # ── Experiments: dates + weeks (F8) ──
        def experiments():
            return Experiment.query.filter_by(user_id=uid).count()

        for label, data in [
            ('malformed start date', {'name': 'Trial', 'start_date': '2026-02-30'}),
            ('weeks = 0', {'name': 'Trial', 'stabilization_weeks': '0'}),
            ('weeks = 53', {'name': 'Trial', 'stabilization_weeks': '53'}),
            ('weeks non-numeric', {'name': 'Trial', 'stabilization_weeks': 'three'}),
        ]:
            before = experiments()
            r = c.post('/experiments/new', data=data)
            check(r.status_code == 200 and experiments() == before,
                  f'[new experiment, {label}] rejected, nothing written')

        # A rejected form keeps what the user typed (CONVENTIONS "Forms: a
        # validation error must not discard the user's input").
        r = c.post('/experiments/new', data={'name': 'Kept name', 'hypothesis': 'Kept hypothesis',
                                             'start_date': '2026-09-01', 'stabilization_weeks': '60'})
        check(b'value="Kept name"' in r.data and b'Kept hypothesis' in r.data
              and b'value="2026-09-01"' in r.data and b'value="60"' in r.data,
              '[new experiment] rejected form re-renders with the submitted values')

        # "+ Add new protocol" chosen, then rejected: the choice and the typed
        # protocol fields come back, so a resubmit can't silently drop the protocol.
        r = c.post('/experiments/new', data={'name': 'N', 'protocol_id': '__new__',
                                             'new_protocol_name': 'Riboflavin 400mg',
                                             'new_protocol_dose': '400mg daily',
                                             'stabilization_weeks': '0'})
        check(b'value="__new__" selected' in r.data and b'value="Riboflavin 400mg"' in r.data
              and b'value="400mg daily"' in r.data,
              '[new experiment] rejected "+ Add new protocol" submit keeps the choice and its fields')

        start = date.today() - timedelta(days=30)
        c.post('/experiments/new', data={'name': 'Trial', 'start_date': start.isoformat(),
                                         'stabilization_weeks': ''})
        exp = Experiment.query.filter_by(user_id=uid, name='Trial').first()
        check(exp is not None and exp.start_date == start and exp.stabilization_weeks == 3,
              '[new experiment] back-dated start kept; blank weeks -> default 3')

        r = c.post(f'/experiments/{exp.id}/edit', data={'name': 'Typed rename', 'stabilization_weeks': '0'})
        check(b'value="Typed rename"' in r.data and b'value="0"' in r.data,
              '[edit experiment] rejected form re-renders with the submitted values')
        for label, data in [
            ('malformed start date', {'name': 'Renamed', 'start_date': 'yesterday'}),
            ('weeks out of range', {'name': 'Renamed', 'stabilization_weeks': '99'}),
        ]:
            r = c.post(f'/experiments/{exp.id}/edit', data=data)
            db.session.expire_all()
            e = db.session.get(Experiment, exp.id)
            check(r.status_code == 200 and e.name == 'Trial' and e.start_date == start
                  and e.stabilization_weeks == 3,
                  f'[edit experiment, {label}] rejected before any field changed')
        c.post(f'/experiments/{exp.id}/edit', data={'name': 'Trial', 'start_date': start.isoformat(),
                                                    'stabilization_weeks': '4'})
        db.session.expire_all()
        check(db.session.get(Experiment, exp.id).stabilization_weeks == 4, '[edit experiment] valid weeks saved')

        # ── Assessment decision (F7) ──
        r = c.post(f'/experiments/{exp.id}/assess', data={'decision': 'x' * 40, 'outcome_rating': '7'})
        db.session.expire_all()
        e = db.session.get(Experiment, exp.id)
        check(r.status_code == 302 and e.status == 'active' and e.decision is None and e.outcome_rating is None,
              '[assess] off-list decision rejected before any assignment')
        r = c.post(f'/experiments/{exp.id}/assess', data={'outcome_rating': '7'})
        db.session.expire_all()
        check(db.session.get(Experiment, exp.id).status == 'active', '[assess] missing decision rejected')
        c.post(f'/experiments/{exp.id}/assess', data={'decision': 'continue', 'outcome_rating': '7'})
        db.session.expire_all()
        e = db.session.get(Experiment, exp.id)
        check(e.status == 'completed' and e.decision == 'continue', '[assess] listed decision saved')

        # ── F9: a failed verification send is not silent ──
        anon = app.test_client()
        r = anon.post('/register', data={'email': 'new@example.com', 'password': 'Passw0rdX',
                                         'confirm_password': 'Passw0rdX', 'privacy_ack': 'on'},
                      follow_redirects=True)
        check(r.status_code == 200 and b'trouble sending your verification email' in r.data,
              '[register] failed verification send shows the warning')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL FORM-VALIDATION TESTS PASSED')


if __name__ == '__main__':
    main()
