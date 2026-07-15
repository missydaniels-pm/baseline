"""Manage-triggers (Settings surface) verification.

Isolated temp SQLite DB. Exercises the trigger-management CRUD via the Flask
test client: list scoping, rename (success + collision rejections + case-only +
history preservation + user scoping), and deactivate/reactivate soft-toggle.

Run:  python test_trigger_manage.py
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmpdir}/test_trigger_manage.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'  # test client posts without tokens
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from datetime import datetime

from app import app
from database import db, User, Episode, Trigger, EpisodeTrigger

FAILS = []


def check(cond, msg):
    print(('PASS' if cond else 'FAIL') + f': {msg}')
    if not cond:
        FAILS.append(msg)


def make_user(email):
    u = User(email=email, is_active=True, verified_at=datetime.utcnow(), onboarding_complete=True)
    u.password_hash = 'x'
    db.session.add(u); db.session.commit()
    return u


def client_for(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = uid
    return c


def main():
    with app.app_context():
        user = make_user('m@test.com')
        other = make_user('o@test.com')
        uid, oid = user.id, other.id

        g_stress = Trigger.query.filter(Trigger.user_id.is_(None), Trigger.name == 'Stress').first()

        # user's customs
        c_active = Trigger(user_id=uid, name='Bright screens', is_active=True)
        c_paused = Trigger(user_id=uid, name='Old thing', is_active=False)
        c_other = Trigger(user_id=uid, name='Loud neighbors', is_active=True)
        foreign = Trigger(user_id=oid, name='Their custom', is_active=True)
        db.session.add_all([c_active, c_paused, c_other, foreign]); db.session.commit()

        c = client_for(uid)

        # 1. List shows own active + paused customs + a global, not the other user's.
        body = c.get('/triggers').get_data(as_text=True)
        check('Bright screens' in body and 'Old thing' in body, 'manage page lists own active + paused customs')
        check('Stress' in body, 'manage page shows shared globals (read-only)')
        check('Their custom' not in body, "manage page does NOT show another user's custom")

        # 2. Rename success preserves EpisodeTrigger history.
        ep = Episode(user_id=uid, onset=datetime(2020, 1, 1, 9, 0)); db.session.add(ep); db.session.flush()
        db.session.add(EpisodeTrigger(episode_id=ep.id, trigger_id=c_active.id, source='user')); db.session.commit()
        c.post(f'/triggers/{c_active.id}/rename', data={'name': 'Bright office screens'})
        db.session.refresh(c_active)
        check(c_active.name == 'Bright office screens', 'rename updates the name')
        links = EpisodeTrigger.query.filter_by(episode_id=ep.id).all()
        check(len(links) == 1 and links[0].trigger_id == c_active.id, 'rename preserves EpisodeTrigger history (same row)')

        # 3. Rename rejected when it collides with an active global.
        c.post(f'/triggers/{c_active.id}/rename', data={'name': 'stress'})
        db.session.refresh(c_active)
        check(c_active.name == 'Bright office screens', 'rename into a global name is rejected')

        # 4. Rename rejected when it collides with the user's own other custom.
        c.post(f'/triggers/{c_active.id}/rename', data={'name': 'loud neighbors'})
        db.session.refresh(c_active)
        check(c_active.name == 'Bright office screens', 'rename into own other custom is rejected')

        # 5. Case-only rename of the SAME trigger is allowed.
        c.post(f'/triggers/{c_active.id}/rename', data={'name': 'bright office screens'})
        db.session.refresh(c_active)
        check(c_active.name == 'bright office screens', 'case-only self-rename allowed')

        # 6. Empty + too-long names rejected.
        c.post(f'/triggers/{c_active.id}/rename', data={'name': '   '})
        db.session.refresh(c_active)
        check(c_active.name == 'bright office screens', 'empty rename rejected')
        c.post(f'/triggers/{c_active.id}/rename', data={'name': 'x' * 150})
        db.session.refresh(c_active)
        check(c_active.name == 'bright office screens', 'over-100-char rename rejected')

        # 7. User scoping: cannot rename/deactivate a global or another user's custom.
        r1 = c.post(f'/triggers/{g_stress.id}/rename', data={'name': 'Hijacked'})
        r2 = c.post(f'/triggers/{foreign.id}/deactivate', data={})
        db.session.refresh(g_stress); db.session.refresh(foreign)
        check(r1.status_code == 404 and g_stress.name == 'Stress', 'cannot rename a global (404)')
        check(r2.status_code == 404 and foreign.is_active is True, "cannot deactivate another user's custom (404)")

        # 8. Deactivate + reactivate toggle is_active.
        c.post(f'/triggers/{c_other.id}/deactivate', data={})
        db.session.refresh(c_other)
        check(c_other.is_active is False, 'deactivate soft-pauses the custom')
        c.post(f'/triggers/{c_paused.id}/reactivate', data={})
        db.session.refresh(c_paused)
        check(c_paused.is_active is True, 'reactivate resumes the custom')

    if FAILS:
        print(f'\n{len(FAILS)} FAILURE(S)')
        raise SystemExit(1)
    print('\nALL MANAGE-TRIGGER TESTS PASSED')


if __name__ == '__main__':
    main()
