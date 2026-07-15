"""Write-path verification for structured triggers (episode form increment).

Isolated temp SQLite DB (set before importing app). Exercises the episode-form
match-and-link write path via the Flask test client: picking globals, inline
custom add (create / match-global / reactivate), foreign-id rejection, dedup,
source stamping, and edit replace-on-save (including not dropping a linked
inactive custom the user leaves checked).

Run:  python test_trigger_writes.py
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmpdir}/test_trigger_writes.db'
os.environ['DEBUG'] = 'true'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from datetime import datetime

from app import app
from database import db, User, Episode, Trigger, EpisodeTrigger

FAILS = []


def check(cond, msg):
    if cond:
        print(f'PASS: {msg}')
    else:
        print(f'FAIL: {msg}')
        FAILS.append(msg)


def make_user(email):
    u = User(email=email, is_active=True, verified_at=datetime.utcnow(),
             onboarding_complete=True)
    u.password_hash = 'x'
    db.session.add(u)
    db.session.commit()
    return u


def client_for(user_id):
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = user_id
    return c


def links_for(episode_id):
    return EpisodeTrigger.query.filter_by(episode_id=episode_id).all()


def main():
    with app.app_context():
        user = make_user('writer@test.com')
        other = make_user('other@test.com')
        uid, oid = user.id, other.id

        globals_ = Trigger.query.filter(Trigger.user_id.is_(None)).order_by(Trigger.id).all()
        g_stress = next(t for t in globals_ if t.name == 'Stress')
        g_alcohol = next(t for t in globals_ if t.name == 'Alcohol')

        # A custom owned by the OTHER user — must never be linkable here.
        foreign = Trigger(user_id=oid, name='Their Secret', is_active=True)
        db.session.add(foreign)
        db.session.commit()
        foreign_id = foreign.id

        c = client_for(uid)

        # 1. Pick two globals + type a brand-new custom.
        r = c.post('/episodes/new', data={
            'onset': '2020-01-01T09:00',
            'trigger_ids': [str(g_stress.id), str(g_alcohol.id)],
            'new_trigger_names': ['Red wine'],
        }, follow_redirects=False)
        check(r.status_code in (302, 303), 'new_episode POST redirects on success')
        ep1 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        l1 = links_for(ep1.id)
        names1 = sorted(t.trigger.name for t in l1)
        check(names1 == ['Alcohol', 'Red wine', 'Stress'], f'linked globals + new custom (got {names1})')
        check(all(t.source == 'user' for t in l1), 'links stamped source=user')
        custom = Trigger.query.filter_by(user_id=uid, name='Red wine').first()
        check(custom is not None and custom.is_active, 'new custom created + active')

        # 2. Typing a name that matches a GLOBAL (case-insensitive) links the
        #    global, never a duplicate custom.
        r = c.post('/episodes/new', data={
            'onset': '2020-01-01T10:00',
            'new_trigger_names': ['  stress  '],
        }, follow_redirects=False)
        ep2 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        l2 = links_for(ep2.id)
        check([t.trigger_id for t in l2] == [g_stress.id], 'typed "stress" links the global')
        dup = Trigger.query.filter(Trigger.user_id == uid, db.func.lower(Trigger.name) == 'stress').count()
        check(dup == 0, 'no duplicate custom created for a global-name collision')

        # 3. Foreign custom id is ignored; typing its exact name creates the
        #    user's OWN custom (does not link the other user's row).
        r = c.post('/episodes/new', data={
            'onset': '2020-01-01T11:00',
            'trigger_ids': [str(foreign_id)],
            'new_trigger_names': ['Their Secret'],
        }, follow_redirects=False)
        ep3 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        l3 = links_for(ep3.id)
        check(foreign_id not in [t.trigger_id for t in l3], "another user's trigger id is not linked")
        own = Trigger.query.filter_by(user_id=uid, name='Their Secret').first()
        check(own is not None and [t.trigger_id for t in l3] == [own.id],
              'typing a foreign name makes your own custom')

        # 4. Dedup: same global id twice + its name typed → exactly one link.
        r = c.post('/episodes/new', data={
            'onset': '2020-01-01T12:00',
            'trigger_ids': [str(g_alcohol.id), str(g_alcohol.id)],
            'new_trigger_names': ['alcohol'],
        }, follow_redirects=False)
        ep4 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        l4 = links_for(ep4.id)
        check(len(l4) == 1 and l4[0].trigger_id == g_alcohol.id, 'duplicate picks dedup to one link')

        # 5. Reactivate a soft-deactivated custom by typing its name.
        custom.is_active = False
        db.session.commit()
        r = c.post('/episodes/new', data={
            'onset': '2020-01-01T13:00',
            'new_trigger_names': ['red wine'],
        }, follow_redirects=False)
        db.session.refresh(custom)
        check(custom.is_active, 'typing a deactivated custom name reactivates it')
        ep5 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        check([t.trigger_id for t in links_for(ep5.id)] == [custom.id], 'reactivated custom linked')

        # 6. Edit replace-on-save: swap Stress+Alcohol+Red wine (ep1) for just
        #    Alcohol. Unchecked links are dropped; kept one survives.
        r = c.post(f'/episodes/{ep1.id}/edit', data={
            'onset': '2020-01-01T09:00',
            'trigger_ids': [str(g_alcohol.id)],
        }, follow_redirects=False)
        l1b = links_for(ep1.id)
        check([t.trigger_id for t in l1b] == [g_alcohol.id], 'edit replaces links (only kept one remains)')

        # 7. Edit does not silently drop a linked INACTIVE custom the user leaves
        #    checked. Link ep2 to the now-inactive custom, deactivate it, then
        #    edit re-submitting that id → survives.
        custom.is_active = False
        db.session.commit()
        db.session.add(EpisodeTrigger(episode_id=ep2.id, trigger_id=custom.id, source='user'))
        db.session.commit()
        r = c.post(f'/episodes/{ep2.id}/edit', data={
            'onset': '2020-01-01T10:00',
            'trigger_ids': [str(g_stress.id), str(custom.id)],
        }, follow_redirects=False)
        ids2 = sorted(t.trigger_id for t in links_for(ep2.id))
        check(ids2 == sorted([g_stress.id, custom.id]),
              'edit keeps a checked inactive-custom link (no silent drop)')

        # 8. Delete episode cascades its trigger links (no orphan rows).
        r = c.post(f'/episodes/{ep4.id}/delete', follow_redirects=False)
        check(links_for(ep4.id) == [], 'delete_episode cascades EpisodeTrigger rows')

        # 9. Typing a name that matches a soft-RETIRED global does NOT freshly
        #    link the retired global (it's out of circulation); it becomes the
        #    user's own custom, preserving agency without a silent drop.
        g_caffeine = next(t for t in globals_ if t.name == 'Caffeine')
        g_caffeine.is_active = False
        db.session.commit()
        caffeine_gid = g_caffeine.id
        r = c.post('/episodes/new', data={
            'onset': '2020-01-01T14:00',
            'new_trigger_names': ['Caffeine'],
        }, follow_redirects=False)
        ep9 = Episode.query.filter_by(user_id=uid).order_by(Episode.id.desc()).first()
        l9 = links_for(ep9.id)
        own_caf = Trigger.query.filter_by(user_id=uid, name='Caffeine').first()
        check(caffeine_gid not in [t.trigger_id for t in l9],
              'retired global is not freshly linked by a typed name')
        check(own_caf is not None and [t.trigger_id for t in l9] == [own_caf.id],
              "typed retired-global name becomes the user's own custom")

    if FAILS:
        print(f'\n{len(FAILS)} FAILURE(S)')
        raise SystemExit(1)
    print('\nALL TRIGGER WRITE-PATH TESTS PASSED')


if __name__ == '__main__':
    main()
