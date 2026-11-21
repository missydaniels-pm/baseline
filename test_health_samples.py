"""Apple Health upload: POST /api/v1/health/samples (SPEC-health-data.md, 11/21/26).

Covered, through real requests into a real database:
  1. Store a batch: rows, canonical uppercase ids, UTC times, local_date in the
     account's stored zone, source name.
  2. Re-sending is harmless: stored once, counted as already_had (also within
     one batch); readings are never edited in place.
  3. Deletes: hard-deleted, scoped to the user and the kind; added-and-deleted
     in one batch ends deleted; unknown ids are a no-op.
  4. Validation: kind, ids, times (offset required, end >= start, not in the
     future), value type/finite/range per kind, source; bad readings are
     rejected and reported while good ones in the same batch are stored.
  5. Limits: 500 per batch, 2 MB body (JSON 413), 600 uploads an hour per token.
  6. Gates: no token 401; Health sync off / privacy consent outdated 403.
  7. User A's token never reads, writes or deletes user B's readings.
  8. Account delete removes readings (DB cascade).
  9. sync-status reports last_synced_at.

Isolated temp DB. Run: python test_health_samples.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

import json  # noqa: E402
import uuid  # noqa: E402
from datetime import date, datetime, timedelta  # noqa: E402

from app import app, bcrypt, limiter, _pw, PRIVACY_POLICY_VERSION, HEALTH_CONSENT_VERSION  # noqa: E402
from app import init_db  # noqa: E402
init_db()  # schema + startup tasks; importing app no longer runs them (11/21/26)
from database import db, User, HealthSample  # noqa: E402

FAILS = []
PW = 'Passw0rd-health'
URL = '/api/v1/health/samples'


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def make_user(email, **kw):
    fields = dict(name='T', email=email, is_active=True, verified_at=datetime.utcnow(),
                  onboarding_complete=True, has_seen_tour=True, health_sync_enabled=True,
                  privacy_policy_version=PRIVACY_POLICY_VERSION, timezone='America/Los_Angeles',
                  health_consent_at=datetime.utcnow(), health_consent_version=HEALTH_CONSENT_VERSION)
    fields.update(kw)
    u = User(**fields)
    u.password_hash = bcrypt.generate_password_hash(_pw(PW)).decode('utf-8')
    db.session.add(u)
    db.session.commit()
    return u.id


def token_for(c, email):
    return {'Authorization': 'Bearer ' + c.post('/api/v1/auth/token',
                                               json={'email': email, 'password': PW}).get_json()['token']}


def sample(value, start='2026-11-20T07:30:00Z', minutes=1, sid=None, source='Test Watch'):
    s = datetime.fromisoformat(start)
    return {'id': sid or str(uuid.uuid4()), 'start': start,
            'end': (s + timedelta(minutes=minutes)).isoformat().replace('+00:00', 'Z'),
            'value': value, 'source': source}


def count(uid, **kw):
    with app.app_context():
        return HealthSample.query.filter_by(user_id=uid, **kw).count()


def main():
    limiter.enabled = False
    c = app.test_client()
    with app.app_context():
        a_id = make_user('a@example.test')
        b_id = make_user('b@example.test')
        off_id = make_user('off@example.test', health_sync_enabled=False)
        make_user('c@example.test')
    A, B, OFF = token_for(c, 'a@example.test'), token_for(c, 'b@example.test'), token_for(c, 'off@example.test')
    C = token_for(c, 'c@example.test')

    r = c.get('/api/v1/health/sync-status', headers=A)
    check(r.get_json()['last_synced_at'] is None, '[status] nothing synced yet -> last_synced_at null')

    # ── 1. Store ──
    lower = str(uuid.uuid4())
    batch = [sample(42.5, sid=lower),
             sample(55, start='2026-11-21T06:30:00Z'),    # 22:30 on 11/20 in Los Angeles
             sample(61.0, start='2026-11-20T23:15:00-08:00')]
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': batch}, headers=A)
    j = r.get_json()
    check(r.status_code == 200 and j == {'ok': True, 'stored': 3, 'already_had': 0, 'deleted': 0, 'rejected': []},
          f'[store] 3 readings stored ({j})')
    with app.app_context():
        row = HealthSample.query.filter_by(user_id=a_id, hk_uuid=lower.upper()).one()
        check(row.kind == 'hrv_sdnn' and row.value == 42.5 and row.source_name == 'Test Watch',
              '[store] kind, value and source saved; id stored upper-case')
        check(row.start_at == datetime(2026, 11, 20, 7, 30) and row.end_at == datetime(2026, 11, 20, 7, 31),
              '[store] times stored as naive UTC')
        late = HealthSample.query.filter_by(user_id=a_id, value=55.0).one()
        check(late.local_date == date(2026, 11, 20),
              f'[store] 06:30Z is 22:30 the day before in the account zone ({late.local_date})')
        offset = HealthSample.query.filter_by(user_id=a_id, value=61.0).one()
        check(offset.start_at == datetime(2026, 11, 21, 7, 15) and offset.local_date == date(2026, 11, 20),
              '[store] a -08:00 offset converts to UTC; day from the account zone')

    # ── 2. Re-send ──
    resend = [dict(batch[0], id=lower.upper(), value=99.0)] + batch[1:]
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': resend}, headers=A)
    j = r.get_json()
    check(j['stored'] == 0 and j['already_had'] == 3, f'[resend] re-sent readings stored once ({j})')
    with app.app_context():
        check(HealthSample.query.filter_by(user_id=a_id, hk_uuid=lower.upper()).one().value == 42.5,
              '[resend] a reading is never edited in place (Health samples are immutable)')
    dup = str(uuid.uuid4())
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(40, sid=dup), sample(41, sid=dup)]}, headers=A)
    j = r.get_json()
    check(j['stored'] == 1 and j['already_had'] == 1, '[resend] the same id twice in one batch is stored once')
    check(count(a_id) == 4, '[resend] 4 readings for user A')

    # ── 3. Deletes ──
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'deleted': [dup.lower(), str(uuid.uuid4())]}, headers=A)
    check(r.get_json()['deleted'] == 1 and count(a_id) == 3,
          '[delete] deleted id removed (any case); an unknown id is a no-op')
    both = str(uuid.uuid4())
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(45, sid=both)], 'deleted': [both]}, headers=A)
    j = r.get_json()
    check(j['stored'] == 1 and j['deleted'] == 1 and count(a_id, hk_uuid=both.upper()) == 0,
          '[delete] added and deleted in one batch -> deleted')
    r = c.post(URL, json={'kind': 'blood_glucose', 'deleted': [lower]}, headers=A)
    check(r.get_json()['deleted'] == 0 and count(a_id, hk_uuid=lower.upper()) == 1,
          '[delete] deletes are scoped to the kind')

    # ── 4. Validation ──
    now = datetime.utcnow()
    future = (now + timedelta(hours=2)).replace(microsecond=0).isoformat() + 'Z'
    bad = [
        ('bad_id', {**sample(50), 'id': 'not-a-uuid'}),
        ('bad_time', {**sample(50), 'start': '2026-11-20T07:30:00'}),   # no offset
        ('bad_time', {**sample(50), 'end': 'yesterday'}),
        ('end_before_start', {**sample(50), 'end': '2026-11-20T07:00:00Z'}),
        ('in_future', sample(50, start=future)),
        ('bad_value', {**sample(50), 'value': '50'}),
        ('bad_value', {**sample(50), 'value': True}),
        ('out_of_range', sample(0.5)),
        ('out_of_range', sample(301)),
        ('too_old', sample(50, start='0001-01-01T00:00:00Z')),   # was a 500 (overflow)
        ('bad_time', {**sample(50), 'start': '9999-12-31T23:59:59-05:00', 'end': '9999-12-31T23:59:59-05:00'}),
        ('too_old', sample(50, start='1999-12-31T23:00:00Z')),
        ('bad_source', {**sample(50), 'source': 5}),
        ('bad_source', {**sample(50), 'source': 'watch\ud800'}),   # lone surrogate: was a 500
        ('not_an_object', 'oops'),
    ]
    good = sample(50)
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [x for _, x in bad] + [good]}, headers=A)
    j = r.get_json()
    reasons = [x['reason'] for x in j['rejected']]
    check(r.status_code == 200 and j['stored'] == 1 and reasons == [b for b, _ in bad],
          f'[validate] each bad reading rejected with its reason; the good one stored ({reasons})')
    r = c.post(URL, data='{"kind": "hrv_sdnn", "samples": [{"id": "%s", "start": "2026-11-20T07:30:00Z", '
                         '"end": "2026-11-20T07:31:00Z", "value": NaN}]}' % uuid.uuid4(),
               content_type='application/json', headers=A)
    with app.app_context():
        nan_rows = HealthSample.query.filter(HealthSample.value != HealthSample.value).count()  # NaN != NaN
    check(r.status_code in (200, 400) and nan_rows == 0, '[validate] NaN is never stored')
    huge = '{"kind": "hrv_sdnn", "samples": [{"id": "%s", "start": "2026-11-20T07:30:00Z", ' \
           '"end": "2026-11-20T07:31:00Z", "value": 1%s}, %s]}' % (uuid.uuid4(), '0' * 400, json.dumps(sample(51)))
    r = c.post(URL, data=huge, content_type='application/json', headers=A)
    j = r.get_json()
    check(r.status_code == 200 and j['stored'] == 1 and j['rejected'][0]['reason'] == 'out_of_range',
          f'[validate] a 400-digit value is rejected, not a 500; the rest of the batch stores ({r.status_code})')
    r = c.post(URL, json={'kind': 'blood_glucose', 'samples': [sample(95), sample(5), sample(1001)]}, headers=A)
    j = r.get_json()
    check(j['stored'] == 1 and [x['reason'] for x in j['rejected']] == ['out_of_range'] * 2,
          '[validate] glucose uses its own range (10-1000 mg/dL)')
    long_src = sample(48, source='S' * 300)
    c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [long_src]}, headers=A)
    with app.app_context():
        check(len(HealthSample.query.filter_by(hk_uuid=long_src['id'].upper()).one().source_name) == 100,
              '[validate] a long source name is cut to 100 characters')
    for label, body, code in [
        ('unknown kind', {'kind': 'steps', 'samples': []}, 'bad_kind'),
        ('kind not text', {'kind': ['hrv_sdnn'], 'samples': []}, 'bad_kind'),
        ('samples not a list', {'kind': 'hrv_sdnn', 'samples': {}}, 'bad_request'),
        ('not an object', ['hrv_sdnn'], 'bad_request'),
    ]:
        r = c.post(URL, json=body, headers=A)
        check(r.status_code == 400 and r.get_json()['code'] == code, f'[validate] {label} -> 400 {code}')
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'deleted': ['nope', 7]}, headers=A)
    check([x['reason'] for x in r.get_json()['rejected']] == ['bad_deleted_id'] * 2, '[validate] bad deleted ids reported')

    # ── 5. Limits ──
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(50) for _ in range(501)]}, headers=A)
    check(r.status_code == 400 and r.get_json()['code'] == 'batch_too_large', '[limits] 501 readings -> 400')
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(50) for _ in range(500)]}, headers=A)
    check(r.status_code == 200 and r.get_json()['stored'] == 500, '[limits] 500 readings accepted')
    big = '{"kind": "hrv_sdnn", "samples": [], "pad": "' + 'x' * (2 * 1024 * 1024) + '"}'
    r = c.post(URL, data=big, content_type='application/json', headers=A)
    check(r.status_code == 413 and r.get_json()['code'] == 'too_large', '[limits] body over 2 MB -> JSON 413')

    # ── 6. Gates ──
    check(c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(50)]}).status_code == 401, '[gate] no token -> 401')
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(50)]}, headers=OFF)
    check(r.status_code == 403 and r.get_json()['code'] == 'health_sync_not_enabled' and count(off_id) == 0,
          '[gate] Health sync off -> 403, nothing stored')
    with app.app_context():
        db.session.get(User, a_id).privacy_policy_version = 'old'
        db.session.commit()
    before = count(a_id)
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(50)]}, headers=A)
    check(r.status_code == 403 and r.get_json()['code'] == 'consent_required' and count(a_id) == before,
          '[gate] privacy consent outdated -> 403, nothing stored')
    with app.app_context():
        db.session.get(User, a_id).privacy_policy_version = PRIVACY_POLICY_VERSION
        db.session.commit()

    # ── Day comes from the stored zone; cookies are ignored by the API ──
    c.set_cookie('baseline_tz', 'Pacific%2FKiritimati')
    noon = sample(52, start='2026-11-21T12:00:00Z')
    c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [noon]}, headers=A)
    c.delete_cookie('baseline_tz')
    with app.app_context():
        d = HealthSample.query.filter_by(hk_uuid=noon['id'].upper()).one().local_date
    check(d == date(2026, 11, 21), f'[zone] a baseline_tz cookie on an API request is ignored ({d})')
    with app.app_context():
        db.session.get(User, a_id).timezone = None
        db.session.commit()
    edge = sample(53, start='2026-11-21T07:59:59Z')
    c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [edge]}, headers=A)
    with app.app_context():
        d = HealthSample.query.filter_by(hk_uuid=edge['id'].upper()).one().local_date
        db.session.get(User, a_id).timezone = 'America/Los_Angeles'
        db.session.commit()
    check(d == date(2026, 11, 21), '[zone] no stored zone -> UTC days (documented fallback)')

    # ── Race retry: a failed commit is retried once, then 409 ──
    import app as app_module
    from sqlalchemy.exc import IntegrityError
    real_commit = app_module.db.session.commit
    fails = {'n': 1}
    def flaky_commit():
        if fails['n'] > 0:
            fails['n'] -= 1
            raise IntegrityError('INSERT', {}, Exception('simulated race'))
        return real_commit()
    app_module.db.session.commit = flaky_commit
    try:
        race = sample(54)
        r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [race]}, headers=A)
        check(r.status_code == 200 and r.get_json()['stored'] == 1 and count(a_id, hk_uuid=race['id'].upper()) == 1,
              '[race] one failed commit is retried and the reading stored once')
        fails['n'] = 2
        import io, logging
        buf = io.StringIO()
        h = logging.StreamHandler(buf)
        app.logger.addHandler(h)
        try:
            r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(55.5)]}, headers=A)
        finally:
            app.logger.removeHandler(h)
        check(r.status_code == 409 and r.get_json()['code'] == 'conflict', '[race] failing twice -> 409 conflict')
        check('55.5' not in buf.getvalue() and 'INSERT' not in buf.getvalue() and 'IntegrityError' in buf.getvalue(),
              '[race] the log names the error type only, never the readings or SQL')
    finally:
        app_module.db.session.commit = real_commit

    # ── 7. User isolation ──
    shared = str(uuid.uuid4())
    c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(70, sid=shared)]}, headers=B)
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': [sample(33, sid=shared)]}, headers=A)
    check(r.get_json()['stored'] == 1, "[isolation] the same Apple id for two users is two separate readings")
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'deleted': [shared]}, headers=A)
    with app.app_context():
        b_row = HealthSample.query.filter_by(user_id=b_id, hk_uuid=shared.upper()).one_or_none()
    check(r.get_json()['deleted'] == 1 and b_row is not None and b_row.value == 70.0,
          "[isolation] A deleting that id removes only A's reading; B's is untouched")
    r = c.get('/api/v1/health/sync-status', headers=B)
    check(r.get_json()['last_synced_at'] is not None, '[status] last_synced_at set after a sync')
    r = c.get('/api/v1/health/sync-status', headers=C)
    check(r.get_json()['last_synced_at'] is None, "[status] another user's syncs don't show as yours")

    # ── 8. Account delete cascades ──
    with app.app_context():
        db.session.delete(db.session.get(User, b_id))
        db.session.commit()
    check(count(b_id) == 0, '[delete account] readings removed with the account')

    # ── Rate limit: 600 uploads an hour per token ──
    limiter.enabled = True
    limiter.reset()
    codes = [c.post(URL, json={'kind': 'hrv_sdnn', 'samples': []}, headers=A).status_code for _ in range(601)]
    check(codes[:600] == [200] * 600 and codes[600] == 429, f'[rate limit] 601st upload in an hour -> 429 ({codes[-2:]})')
    variant = {'Authorization': 'bearer   ' + A['Authorization'].split(' ', 1)[1]}
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': []}, headers=variant)
    check(r.status_code == 429, f'[rate limit] header spacing/case variants share the bucket ({r.status_code})')
    r = c.post(URL, json={'kind': 'hrv_sdnn', 'samples': []}, headers=C)
    check(r.status_code == 200, f'[rate limit] another phone from the same address has its own bucket ({r.status_code})')
    limiter.reset()

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL HEALTH SAMPLE TESTS PASSED')


if __name__ == '__main__':
    main()
