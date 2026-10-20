"""Every page renders, plus the remaining CRUD contracts (review F11, 10/19/26).

Before this, nothing rendered `/` (the dashboard) or the admin pages in a test,
and `/symptoms/*`, `/rescue-options/*`, onboarding and unsubscribe had no
route-level coverage. Covered:

  1. Smoke: with 12 weeks of seeded data, every parameterless GET page a user
     can reach (walked from app.url_map, so a new page is covered automatically)
     returns 200 or a redirect — never a 500. Detail pages render for real ids.
     Admin pages render for an admin and redirect for everyone else.
  2. Onboarding: step 1 → 2 → 3 completes onboarding, saves baselines and the
     AI choice; the dashboard is gated until then.
  3. What I Track: create (incl. case-insensitive duplicate rejection),
     edit, deactivate, reactivate; type locked once scores exist.
  4. Interventions: create, edit, a removed one can't be edited until restored.
  5. Unsubscribe: a valid token turns email updates off; a tampered one is a
     generic 400.
  6. User scoping: another user's ids 404 on detail/edit pages.

Isolated temp DB. Run: python test_pages_and_crud.py
"""
import os
import tempfile

# TEST_DATABASE_URL (CI's Postgres job) wins; otherwise an isolated temp SQLite DB.
os.environ['DATABASE_URL'] = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{tempfile.mkdtemp()}/t.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)

from datetime import datetime

from app import app, seed_test_data, generate_unsubscribe_token
from database import (db, User, Episode, Protocol, Symptom, SymptomScore, Experiment, Trigger)

FAILS = []

# Routes that aren't pages a signed-in user browses to with GET.
SKIP = {'static', 'logout', 'serve_sw', 'tour_restart', 'dev_reset', 'dev_seed',
        'dev_bootstrap', 'dev_create_invite', 'verify_sent'}


# GET endpoints that legitimately redirect a signed-in, onboarded user.
EXPECTED_REDIRECTS = {'login', 'register', 'onboarding_step2', 'admin_analytics', 'admin_users'}


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def client_for(uid):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess['user_id'] = uid
    return c


def make_user(email, **kw):
    u = User(name='T', email=email, is_active=True, verified_at=datetime.utcnow(),
             onboarding_complete=True, has_seen_tour=True, **kw)
    u.password_hash = 'x'
    db.session.add(u)
    db.session.commit()
    return u


def main():
    with app.app_context():
        u = make_user('pages@test.com')
        seed_test_data(u)
        uid = u.id
        c = client_for(uid)

        # ── 1. Smoke: every parameterless GET page ──
        pages = sorted({r.endpoint for r in app.url_map.iter_rules()
                        if 'GET' in r.methods and not r.arguments and r.endpoint not in SKIP})
        for ep in pages:
            with app.test_request_context():
                from flask import url_for
                url = url_for(ep)
            r = c.get(url)
            # Onboarded user: only these may redirect; everything else must render.
            ok = (200, 302) if ep in EXPECTED_REDIRECTS else (200,)
            check(r.status_code in ok, f'[smoke] GET {url} → {r.status_code}')

        # Detail/edit pages for real ids.
        ep_ = Episode.query.filter_by(user_id=uid).first()
        prev = Protocol.query.filter_by(user_id=uid, type='preventative').first()
        resc = Protocol.query.filter_by(user_id=uid, type='rescue').first()
        sym = Symptom.query.filter_by(user_id=uid).first()
        exp = Experiment(user_id=uid, name='Trial', start_date=prev.start_date or datetime.now().date(),
                         protocol_id=prev.id, status='active')
        db.session.add(exp)
        db.session.commit()
        for url in (f'/episodes/{ep_.id}/edit', f'/protocols/{prev.id}', f'/protocols/{prev.id}/edit',
                    f'/rescue-options/{resc.id}/edit', f'/symptoms/{sym.id}/edit',
                    f'/experiments/{exp.id}/edit', f'/experiments/{exp.id}/assess',
                    f'/experiments/{exp.id}/abandon', f'/experiments/offer/{prev.id}',
                    '/reports/doctor-summary?notes=1'):
            r = c.get(url)
            check(r.status_code == 200, f'[smoke] GET {url} → {r.status_code}')
        r = c.get('/')
        a_symptom = Symptom.query.filter_by(user_id=uid, is_active=True).first().name.encode()
        a_protocol = Protocol.query.filter_by(user_id=uid, type='preventative', status='active').first().name.encode()
        check(r.status_code == 200 and a_symptom in r.data and a_protocol in r.data,
              '[smoke] dashboard renders the seeded data (a tracked item and a preventative)')

        # Admin pages: admin renders, others redirect.
        for url in ('/admin/analytics', '/admin/users'):
            check(c.get(url).status_code == 302, f'[admin] {url} redirects a non-admin')
        admin = make_user('admin@test.com', is_admin=True)
        ca = client_for(admin.id)
        for url in ('/admin/analytics', '/admin/users'):
            r = ca.get(url)
            check(r.status_code == 200, f'[admin] {url} renders for an admin ({r.status_code})')

        # ── 6. Scoping ──
        other = make_user('other@test.com')
        co = client_for(other.id)
        for url in (f'/episodes/{ep_.id}/edit', f'/protocols/{prev.id}', f'/symptoms/{sym.id}/edit',
                    f'/rescue-options/{resc.id}/edit', f'/experiments/{exp.id}/assess'):
            check(co.get(url).status_code == 404, f'[scope] another user gets 404 on {url}')

        # ── 2. Onboarding ──
        new = User(name='N', email='onb@test.com', is_active=True, verified_at=datetime.utcnow(),
                   onboarding_complete=False)
        new.password_hash = 'x'
        db.session.add(new)
        db.session.commit()
        cn = client_for(new.id)
        r = cn.get('/')
        check(r.status_code == 302 and 'onboarding' in r.headers['Location'], '[onboarding] dashboard gated')
        cn.post('/onboarding/step1', data={'name_1': 'Pain', 'input_type_1': 'scale',
                                           'name_2': 'Aura', 'input_type_2': 'binary'})
        syms = {s.name: s for s in Symptom.query.filter_by(user_id=new.id).all()}
        check(set(syms) == {'Pain', 'Aura'} and syms['Aura'].input_type == 'binary',
              '[onboarding] step 1 creates the tracked items with their types')
        cn.post('/onboarding/step2', data={'baseline_episodes_per_month': '6',
                                           f'score_{syms["Pain"].id}': '7'})
        db.session.expire_all()
        check(db.session.get(User, new.id).baseline_episodes_per_month == 6
              and db.session.get(Symptom, syms['Pain'].id).baseline_score == 7,
              '[onboarding] step 2 saves the baselines')
        r = cn.post('/onboarding/step3', data={'choice': 'enable'})
        db.session.expire_all()
        nu = db.session.get(User, new.id)
        check(r.status_code == 302 and nu.onboarding_complete and nu.ai_logging_enabled,
              '[onboarding] step 3 completes onboarding with the AI choice')
        check(cn.get('/').status_code == 200, '[onboarding] dashboard reachable afterwards')

        # ── 3. What I Track ──
        cn.post('/symptoms/new', data={'name': 'Fatigue', 'input_type': 'scale'})
        f = Symptom.query.filter_by(user_id=new.id, name='Fatigue').first()
        check(f is not None, '[symptoms] create')
        cn.post('/symptoms/new', data={'name': 'fatigue', 'input_type': 'scale'})
        check(Symptom.query.filter_by(user_id=new.id).filter(db.func.lower(Symptom.name) == 'fatigue').count() == 1,
              '[symptoms] case-insensitive duplicate rejected')
        cn.post(f'/symptoms/{f.id}/edit', data={'name': 'Tiredness', 'input_type': 'binary'})
        db.session.expire_all()
        f = db.session.get(Symptom, f.id)
        check(f.name == 'Tiredness' and f.input_type == 'binary', '[symptoms] edit name + type (no scores yet)')
        cn.post(f'/symptoms/{f.id}/deactivate')
        db.session.expire_all()
        check(db.session.get(Symptom, f.id).is_active is False, '[symptoms] deactivate')
        cn.post(f'/symptoms/{f.id}/reactivate')
        db.session.expire_all()
        check(db.session.get(Symptom, f.id).is_active is True, '[symptoms] reactivate')
        pain = syms['Pain']
        e = Episode(user_id=new.id, onset=datetime.now())
        db.session.add(e)
        db.session.flush()
        db.session.add(SymptomScore(episode_id=e.id, symptom_id=pain.id, score=4))
        db.session.commit()
        cn.post(f'/symptoms/{pain.id}/edit', data={'name': 'Pain', 'input_type': 'binary'})
        db.session.expire_all()
        check(db.session.get(Symptom, pain.id).input_type == 'scale', '[symptoms] type locked once scores exist')

        # ── 4. Interventions ──
        cn.post('/rescue-options/new', data={'name': 'Ice pack', 'available': 'on'})
        ice = Protocol.query.filter_by(user_id=new.id, type='rescue', name='Ice pack').first()
        check(ice is not None and ice.available, '[interventions] create')
        cn.post('/rescue-options/new', data={'name': '   '})
        check(Protocol.query.filter_by(user_id=new.id, type='rescue').count() == 1,
              '[interventions] blank name rejected')
        cn.post(f'/rescue-options/{ice.id}/edit', data={'name': 'Cold pack'})
        db.session.expire_all()
        ice = db.session.get(Protocol, ice.id)
        check(ice.name == 'Cold pack' and ice.available is False, '[interventions] edit (unticked → unavailable)')
        ice.status = 'removed'
        db.session.commit()
        cn.post(f'/rescue-options/{ice.id}/edit', data={'name': 'Changed'})
        db.session.expire_all()
        check(db.session.get(Protocol, ice.id).name == 'Cold pack', '[interventions] a removed one cannot be edited')
        cn.post(f'/rescue-options/{ice.id}/restore')
        db.session.expire_all()
        ice = db.session.get(Protocol, ice.id)
        check(ice.status == 'active' and ice.available, '[interventions] restore')

        # ── 5. Unsubscribe ──
        anon = app.test_client()
        tok = generate_unsubscribe_token('onb@test.com')
        r = anon.get(f'/unsubscribe/{tok}')
        db.session.expire_all()
        check(r.status_code == 200 and db.session.get(User, new.id).email_updates_enabled is False,
              '[unsubscribe] valid token turns email updates off')
        r = anon.get(f'/unsubscribe/{tok[:-3]}xyz')
        check(r.status_code == 400, '[unsubscribe] tampered token → generic 400')
        r = anon.get(f'/unsubscribe/{generate_unsubscribe_token("ghost@test.com")}')
        check(r.status_code == 400, '[unsubscribe] unknown address → the same generic 400')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL PAGE/CRUD TESTS PASSED')


if __name__ == '__main__':
    main()
