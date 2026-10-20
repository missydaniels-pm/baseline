"""Doctor summary printout (interim, pre-rebuild) — 10/19/26.

Runs against an ISOLATED temp SQLite DB (set before importing app, which
bootstraps create_all + run_migrations at import) so the local dev database is
never touched.

Covers `build_doctor_summary()` (the request-context-free helper the React API
will reuse) and the `/reports/doctor-summary` route:

  1. The window is inclusive of both local days, and nothing outside it counts.
  2. Strictly user-scoped — another user's episodes, scores and trials never appear.
  3. Scale items average, binary items report % yes; never mixed.
  4. Every trial that overlaps the range appears, including ended-early ones
     with their reason; trials wholly outside the range do not.
  5. Preventatives stopped before the range are left out; compliance counts are
     limited to the range.
  6. Notes are off by default and only appear when asked for.
  7. A malformed, inverted or future range is rejected with a message and the
     default range is shown instead (reject, don't substitute silently).

Run:  python3 test_doctor_summary.py
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmpdir}/test_doctor_summary.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from datetime import date, datetime, timedelta

from app import app, build_doctor_summary, user_today
from database import (db, User, Episode, Symptom, SymptomScore, Protocol, ProtocolEvent,
                      ProtocolCompliance, Experiment, EpisodeIntervention, Trigger, EpisodeTrigger)

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


def ep(user, day, hour=9, **kw):
    e = Episode(user_id=user.id, onset=datetime.combine(day, datetime.min.time()).replace(hour=hour), **kw)
    db.session.add(e)
    db.session.flush()
    return e


def main():
    start, end = date(2026, 9, 1), date(2026, 9, 30)

    with app.app_context():
        u = make_user('doc@test.com')
        other = make_user('other@test.com')

        pain = Symptom(user_id=u.id, name='Headache', input_type='scale')
        aura = Symptom(user_id=u.id, name='Aura', input_type='binary')
        db.session.add_all([pain, aura])
        db.session.flush()

        # In range: first minute of the first day and last minute of the last day.
        e1 = ep(u, start, hour=0, duration_hours=4, functional_impairment='cannot_work', notes='bad one')
        e2 = ep(u, end, hour=23, functional_impairment='working_reduced')
        e3 = ep(u, date(2026, 9, 15))
        e3b = ep(u, date(2026, 9, 15), hour=18)  # same day as e3
        # Out of range on both sides.
        ep(u, start - timedelta(days=1), hour=23)
        ep(u, end + timedelta(days=1), hour=0)
        db.session.add_all([
            SymptomScore(episode_id=e1.id, symptom_id=pain.id, score=8),
            SymptomScore(episode_id=e2.id, symptom_id=pain.id, score=4),
            SymptomScore(episode_id=e1.id, symptom_id=aura.id, value_bool=True),
            SymptomScore(episode_id=e2.id, symptom_id=aura.id, value_bool=False),
            SymptomScore(episode_id=e3.id, symptom_id=aura.id, value_bool=False),
            SymptomScore(episode_id=e3b.id, symptom_id=aura.id, value_bool=True),
        ])

        rescue = Protocol(user_id=u.id, name='Sumatriptan', type='rescue')
        current = Protocol(user_id=u.id, name='Magnesium', type='preventative',
                           start_date=date(2026, 6, 1), status='active', dose_frequency='400mg')
        old = Protocol(user_id=u.id, name='Topiramate', type='preventative',
                       start_date=date(2026, 3, 1), status='stopped')
        future = Protocol(user_id=u.id, name='Later', type='preventative',
                          start_date=date(2026, 11, 1), status='active')
        db.session.add_all([rescue, current, old, future])
        db.session.flush()
        db.session.add(ProtocolEvent(protocol_id=old.id, user_id=u.id, event_type='stopped',
                                     date=date(2026, 5, 1)))
        db.session.add_all([
            EpisodeIntervention(episode_id=e1.id, protocol_id=rescue.id, effectiveness=6),
            EpisodeIntervention(episode_id=e2.id, protocol_id=rescue.id),
        ])
        for d, took in ((date(2026, 9, 2), True), (date(2026, 9, 3), False), (date(2026, 8, 30), True)):
            db.session.add(ProtocolCompliance(user_id=u.id, protocol_id=current.id, date=d, took=took))

        trig = Trigger(user_id=u.id, name='Poor sleep')
        db.session.add(trig)
        db.session.flush()
        db.session.add_all([EpisodeTrigger(episode_id=e1.id, trigger_id=trig.id),
                            EpisodeTrigger(episode_id=e2.id, trigger_id=trig.id)])

        db.session.add_all([
            Experiment(user_id=u.id, name='Qulipta', start_date=date(2026, 8, 20), status='abandoned',
                       end_date=date(2026, 9, 5), stop_reason='made_worse', stop_reason_note='worse'),
            Experiment(user_id=u.id, name='Ancient', start_date=date(2026, 1, 1), status='abandoned',
                       end_date=date(2026, 2, 1), stop_reason='cost'),
            Experiment(user_id=u.id, name='Running', start_date=date(2026, 9, 10), status='active'),
        ])

        # Another user's data on the same days — must never leak in.
        oe = ep(other, date(2026, 9, 15), notes='OTHER USER NOTE')
        osym = Symptom(user_id=other.id, name='OtherSym', input_type='scale')
        db.session.add(osym)
        db.session.flush()
        db.session.add(SymptomScore(episode_id=oe.id, symptom_id=osym.id, score=10))
        db.session.add(Experiment(user_id=other.id, name='OtherTrial', start_date=date(2026, 9, 1),
                                  status='active'))
        db.session.commit()

        s = build_doctor_summary(u.id, start, end)

        # 1. Window
        check('both boundary days are included, outside days are not', s['episode_count'] == 4)
        check('days with an episode counts distinct days', s['episode_days'] == 3)
        check('one month row for a single-month range, not partial',
              len(s['months']) == 1 and s['months'][0]['episodes'] == 4 and not s['months'][0]['partial'])
        check('duration honesty: 1 with, 3 without',
              s['duration'] == {'n_with': 1, 'n_without': 3, 'avg_hours': 4.0})
        check('impairment tallies + unset count',
              {i['value']: i['count'] for i in s['impairment']} == {'cannot_work': 1, 'working_reduced': 1}
              and s['impairment_unset'] == 2)

        # 2/3. Tracked items
        by_name = {i['name']: i for i in s['tracked']}
        check('user-scoped: another user\'s symptom never appears', 'OtherSym' not in by_name)
        check('scale item averages its scores', by_name['Headache'] == {
            'name': 'Headache', 'kind': 'scale', 'n': 2, 'avg': 6.0, 'max': 8})
        check('binary item reports % yes', by_name['Aura']['kind'] == 'binary'
              and by_name['Aura']['yes_pct'] == 50 and by_name['Aura']['n'] == 4)

        # Interventions + triggers
        check('interventions: uses counted, average over rated only',
              s['interventions'] == [{'name': 'Sumatriptan', 'times_used': 2,
                                      'avg_effectiveness': 6.0, 'n_rated': 1}])
        check('triggers counted per episode', s['triggers'] == [{'name': 'Poor sleep', 'episodes': 2}])

        # 4. Experiments
        names = [e['name'] for e in s['experiments']]
        check('overlapping trials appear, ended-early included', 'Qulipta' in names and 'Running' in names)
        check('trials wholly outside the range do not', 'Ancient' not in names)
        check('another user\'s trial never appears', 'OtherTrial' not in names)
        q = next(e for e in s['experiments'] if e['name'] == 'Qulipta')
        check('ended-early trial carries its reason; its free-text note follows the notes toggle',
              q['stop_reason'] == 'made_worse' and q['note'] is None)

        # 5. Preventatives
        pnames = [p['name'] for p in s['preventatives']]
        check('a preventative stopped before the range is left out', 'Topiramate' not in pnames)
        check('a preventative starting after the range is left out', 'Later' not in pnames)
        mg = next(p for p in s['preventatives'] if p['name'] == 'Magnesium')
        check('compliance counts are limited to the range', mg['days_taken'] == 1 and mg['days_missed'] == 1)

        # 6. Notes
        check('notes are off by default', s['notes'] == [] and not s['include_notes'])
        s2 = build_doctor_summary(u.id, start, end, include_notes=True)
        check('notes appear when asked for, own only',
              [n['text'] for n in s2['notes']] == ['bad one'])
        check('trial note appears when notes are asked for',
              next(e for e in s2['experiments'] if e['name'] == 'Qulipta')['note'] == 'worse')

        # Status history: a stopped protocol with no event row is listed with its
        # missing date stated (not silently undated); pause/resume inside the range
        # are listed as changes.
        legacy = Protocol(user_id=u.id, name='Legacy', type='preventative',
                          start_date=date(2025, 1, 1), status='stopped')
        bumpy = Protocol(user_id=u.id, name='Bumpy', type='preventative',
                         start_date=date(2026, 7, 1), status='active')
        db.session.add_all([legacy, bumpy])
        db.session.flush()
        db.session.add_all([
            ProtocolEvent(protocol_id=bumpy.id, user_id=u.id, event_type='paused', date=date(2026, 9, 5)),
            ProtocolEvent(protocol_id=bumpy.id, user_id=u.id, event_type='reactivated', date=date(2026, 9, 12)),
        ])
        db.session.commit()
        s3 = build_doctor_summary(u.id, start, end)
        lg = next(p for p in s3['preventatives'] if p['name'] == 'Legacy')
        check('stopped protocol with no event: listed, stop date flagged missing',
              lg['stop_date_missing'] and lg['stopped_on'] is None)
        bp = next(p for p in s3['preventatives'] if p['name'] == 'Bumpy')
        check('pause and resume inside the range are listed',
              [(c['label'], c['date']) for c in bp['changes']] ==
              [('Paused', date(2026, 9, 5)), ('Resumed', date(2026, 9, 12))])

        # Month rows across a year boundary, partial at both ends.
        s4 = build_doctor_summary(u.id, date(2025, 11, 20), date(2026, 2, 10))
        check('month rows across a year boundary',
              [(m['label'], m['partial'], m['days_covered']) for m in s4['months']] ==
              [('Nov 2025', True, 11), ('Dec 2025', False, 31), ('Jan 2026', False, 31), ('Feb 2026', True, 10)])
        check('an empty range renders as zeros', s4['episode_count'] == 0 and s4['tracked'] == [])

        # Route
        c = app.test_client()
        with c.session_transaction() as sess:
            sess['user_id'] = u.id
        r = c.get('/reports/doctor-summary?start=2026-09-01&end=2026-09-30')
        body = r.get_data(as_text=True)
        check('route renders the chosen range', r.status_code == 200 and 'Sep 1, 2026 to Sep 30, 2026' in body
              and 'It made my symptoms worse' in body)
        check('the page is never cached (browser or offline cache)',
              'no-store' in r.headers.get('Cache-Control', ''))
        check('trial note hidden by default on the page', '>worse<' not in body.replace(' ', ''))
        check('route hides notes by default', 'bad one' not in body)
        r = c.get('/reports/doctor-summary?start=2026-09-01&end=2026-09-30&notes=1')
        check('route shows notes when asked', 'bad one' in r.get_data(as_text=True))
        check('route never shows another user\'s note', 'OTHER USER NOTE' not in r.get_data(as_text=True))

        with app.test_request_context():
            today = user_today()
        for qs, msg in (('start=2026-09-30&end=2026-09-01', 'on or before'),
                        ('start=nope', 'look right'),
                        (f'end={(today + timedelta(days=1)).isoformat()}', 'future'),
                        ('start=2020-01-01', 'three years')):
            r = c.get(f'/reports/doctor-summary?{qs}')
            body = r.get_data(as_text=True)
            check(f'bad range ({qs}) is rejected with a message and no report',
                  r.status_code == 400 and msg in body and 'Health summary' not in body
                  and 'no-store' in r.headers.get('Cache-Control', ''))

        r = c.get('/settings')
        check('settings links to the doctor summary', '/reports/doctor-summary' in r.get_data(as_text=True))

    print()
    if failures:
        print(f'{len(failures)} DOCTOR-SUMMARY TEST(S) FAILED')
        for f in failures:
            print('  -', f)
        raise SystemExit(1)
    print('ALL DOCTOR-SUMMARY TESTS PASSED')


if __name__ == '__main__':
    main()
