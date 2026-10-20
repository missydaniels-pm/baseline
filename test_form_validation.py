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

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime, date, timedelta

from app import app
from app import PRIVACY_POLICY_VERSION  # fixtures are consented users (consent gate)
from database import db, User, Episode, Protocol, ProtocolEvent, Experiment, Symptom, SymptomScore, ProtocolCompliance, EpisodeIntervention, Trigger

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def main():
    with app.app_context():
        u = User(privacy_policy_version=PRIVACY_POLICY_VERSION, email='v@test.com', is_active=True, verified_at=datetime.utcnow(),
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

    # ── Write-path caps converge: reject on forms, cap-and-continue on AI (review F8, 10/19/26) ──
    with app.app_context():
        sym = Symptom(user_id=uid, name='Pain', input_type='scale')
        yn = Symptom(user_id=uid, name='Aura', input_type='binary')
        rescue = Protocol(user_id=uid, name='Triptan', type='rescue')
        prev = Protocol(user_id=uid, name='Mag', type='preventative', status='active')
        db.session.add_all([sym, yn, rescue, prev])
        db.session.commit()
        base = {'onset': yesterday, 'functional_impairment': ''}

        def episodes():
            return Episode.query.filter_by(user_id=uid).count()

        for label, extra, msg in [
            ('negative duration', {'duration_hours': '-3'}, b'Duration must be'),
            ('non-numeric duration', {'duration_hours': 'abc'}, b'Duration must be'),
            ('score 11', {f'score_{sym.id}_rated': '1', f'score_{sym.id}': '11'}, b'score from 1 to 10'),
            ('binary garbage', {f'score_{yn.id}_rated': '1', f'binary_{yn.id}': 'maybe'}, b'Yes or No'),
            ('effectiveness 0', {'intervention_protocol_0': str(rescue.id),
                                 'intervention_effectiveness_0': '0'}, b'Effectiveness must be'),
            ('negative relief', {'intervention_protocol_0': str(rescue.id),
                                 'intervention_relief_0': '-1'}, b'Time to relief must be'),
            ('trigger name over 100', {'new_trigger_names': 'x' * 101}, b'100 characters'),
            ('superscript score', {f'score_{sym.id}_rated': '1', f'score_{sym.id}': '\u00b2'}, b'score from 1 to 10'),
            ('superscript effectiveness', {'intervention_protocol_0': str(rescue.id),
                                           'intervention_effectiveness_0': '\u00b2'}, b'Effectiveness must be'),
            ('notes over 500', {'notes': 'n' * 501}, b'500 characters'),
        ]:
            before = episodes()
            r = c.post('/episodes/new', data=dict(base, **extra))
            check(episodes() == before and msg in r.data,
                  f'[new episode, {label}] rejected with a message, nothing written')

        before = episodes()
        c.post('/episodes/new', data=dict(base, duration_hours='2.5', **{
            f'score_{sym.id}_rated': '1', f'score_{sym.id}': '7',
            'intervention_protocol_0': str(rescue.id), 'intervention_effectiveness_0': '8',
            'intervention_relief_0': '1.5', 'new_trigger_names': 'Bright light'}))
        ep = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        ei = EpisodeIntervention.query.filter_by(episode_id=ep.id).first()
        check(episodes() == before + 1 and ep.duration_hours == 2.5
              and ep.symptom_scores[0].score == 7 and ei.effectiveness == 8 and ei.time_to_relief_hours == 1.5,
              '[new episode] valid numbers still save exactly')

        # Route-level: a future onset is refused (the guard is exact in the user's zone).
        before = episodes()
        future = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')
        r = c.post('/episodes/new', data=dict(base, onset=future))
        check(episodes() == before and b'cannot be in the future' in r.data,
              '[new episode] future onset refused on the real route')

        # Route-level: scale and binary items land in their own columns, never mixed.
        c.post('/episodes/new', data=dict(base, **{
            f'score_{sym.id}_rated': '1', f'score_{sym.id}': '6',
            f'score_{yn.id}_rated': '1', f'binary_{yn.id}': 'yes'}))
        ep2 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        got = {ss.symptom_id: (ss.score, ss.value_bool) for ss in ep2.symptom_scores}
        check(got == {sym.id: (6, None), yn.id: (None, True)},
              '[new episode] scale → score only, binary → value_bool only')

        r = c.post(f'/episodes/{ep.id}/edit', data=dict(base, duration_hours='-1'))
        db.session.expire_all()
        check(db.session.get(Episode, ep.id).duration_hours == 2.5,
              '[edit episode] negative duration rejected, value unchanged')

        # Protocol detail "log today": missing choice and over-long note rejected.
        def logs():
            return ProtocolCompliance.query.filter_by(protocol_id=prev.id).count()
        c.post(f'/protocols/{prev.id}/log', data={'notes': 'x'})
        check(logs() == 0, '[log today] missing took is rejected, not logged as missed')
        c.post(f'/protocols/{prev.id}/log', data={'took': 'yes', 'notes': 'n' * 501})
        check(logs() == 0, '[log today] note over 500 rejected, not truncated')
        c.post(f'/protocols/{prev.id}/log', data={'took': 'no', 'notes': 'late'})
        check(logs() == 1 and ProtocolCompliance.query.filter_by(protocol_id=prev.id).first().took is False,
              '[log today] valid entry saves')

        # Onboarding step 1: reject instead of truncating; typed input survives.
        u2 = User(privacy_policy_version=PRIVACY_POLICY_VERSION, email='onb@test.com', is_active=True, verified_at=datetime.utcnow(),
                  onboarding_complete=False)
        u2.password_hash = 'x'
        db.session.add(u2)
        db.session.commit()
        c2 = app.test_client()
        with c2.session_transaction() as sess:
            sess['user_id'] = u2.id
        r = c2.post('/onboarding/step1', data={'name_1': 'y' * 201, 'input_type_1': 'scale'})
        check(Symptom.query.filter_by(user_id=u2.id).count() == 0 and b'200 characters' in r.data,
              '[onboarding] name over 200 rejected (was silently truncated)')
        r = c2.post('/onboarding/step1', data={'name_1': 'Pain', 'input_type_1': 'scale',
                                                'name_2': 'Fog', 'input_type_2': 'sliders'})
        check(Symptom.query.filter_by(user_id=u2.id).count() == 0 and b'value="Fog"' in r.data,
              '[onboarding] off-list type rejected and typed names kept on re-render')
        r = c2.post('/onboarding/step1', data={'name_1': 'Pain', 'input_type_1': 'scale',
                                                'name_2': 'pain', 'input_type_2': 'scale'})
        check(Symptom.query.filter_by(user_id=u2.id).count() == 0 and b'listed twice' in r.data,
              '[onboarding] duplicate names rejected')

    # Onboarding duplicate against a kept (scored) symptom; resubmitting the
    # pre-filled kept row must not duplicate it.
    with app.app_context():
        u3 = User(privacy_policy_version=PRIVACY_POLICY_VERSION, email='onb3@test.com', is_active=True, verified_at=datetime.utcnow(),
                  onboarding_complete=False)
        u3.password_hash = 'x'
        db.session.add(u3)
        db.session.flush()
        kept = Symptom(user_id=u3.id, name='Pain', input_type='scale')
        db.session.add(kept)
        db.session.flush()
        e3 = Episode(user_id=u3.id, onset=datetime.now() - timedelta(days=1))
        db.session.add(e3)
        db.session.flush()
        db.session.add(SymptomScore(episode_id=e3.id, symptom_id=kept.id, score=5))
        db.session.commit()
        c4 = app.test_client()
        with c4.session_transaction() as sess:
            sess['user_id'] = u3.id
        r = c4.post('/onboarding/step1', data={'name_1': 'pain', 'input_type_1': 'scale'})
        check(Symptom.query.filter_by(user_id=u3.id).count() == 1 and b'already exists' in r.data,
              '[onboarding] case-variant of a kept symptom rejected')
        r = c4.post('/onboarding/step1', data={'name_1': 'Pain', 'input_type_1': 'scale',
                                                'name_2': 'Fog', 'input_type_2': 'scale'})
        names = sorted(x.name for x in Symptom.query.filter_by(user_id=u3.id).all())
        check(r.status_code == 302 and names == ['Fog', 'Pain'],
              '[onboarding] resubmitting the kept row does not duplicate it')

    with app.app_context():
        c3 = app.test_client()
        with c3.session_transaction() as sess:
            sess['user_id'] = uid
        before = Symptom.query.filter_by(user_id=uid).count()
        r = c3.post('/symptoms/new', data={'name': 'Brain fog', 'input_type': 'sliders'})
        check(Symptom.query.filter_by(user_id=uid).count() == before and b'Scale or Yes' in r.data,
              '[new symptom] off-list type rejected (was silently made a scale)')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL FORM-VALIDATION TESTS PASSED')


if __name__ == '__main__':
    main()
