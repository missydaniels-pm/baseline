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
os.environ['WTF_CSRF_ENABLED'] = 'false'  # test client posts without tokens
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

        # --- AI check-in trigger parity -------------------------------------
        from app import _match_trigger, _apply_ai_triggers

        # 10. _match_trigger matches EXISTING active triggers only, never creates.
        g_stress2 = next(t for t in globals_ if t.name == 'Stress')
        m = _match_trigger(uid, '  stress ')
        check(m is not None and m.id == g_stress2.id, '_match_trigger maps case-insensitively to an active global')
        check(_match_trigger(uid, 'totally novel thing') is None, '_match_trigger returns None for an unknown name (no create)')
        # A custom only the OTHER user owns must not match for uid.
        db.session.add(Trigger(user_id=oid, name='Oid Only Trigger', is_active=True))
        db.session.commit()
        check(_match_trigger(uid, 'Oid Only Trigger') is None, "_match_trigger ignores another user's custom")
        own_active = Trigger(user_id=uid, name='Bright office lights', is_active=True)
        db.session.add(own_active); db.session.commit()
        check(_match_trigger(uid, 'bright office lights') is not None, '_match_trigger maps to own active custom')

        # 11. _apply_ai_triggers links matched (source='ai', dedup) and returns
        #     unmatched names as suggestions WITHOUT creating them.
        ep_ai = Episode(user_id=uid, onset=datetime(2020, 1, 2, 9, 0))
        db.session.add(ep_ai); db.session.flush()
        suggestions = _apply_ai_triggers(ep_ai.id, uid,
            ['Stress', 'stress', 'Bright office lights', 'Skywriting fumes', 'skywriting fumes'])
        db.session.commit()
        ai_links = links_for(ep_ai.id)
        check(bool(ai_links) and all(t.source == 'ai' for t in ai_links), 'AI-linked triggers stamped source=ai')
        check(sorted(t.trigger.name for t in ai_links) == ['Bright office lights', 'Stress'],
              'matched names linked + deduped')
        check(suggestions == ['Skywriting fumes'], f'unmatched name returned as a single deduped suggestion (got {suggestions})')
        check(Trigger.query.filter(db.func.lower(Trigger.name) == 'skywriting fumes').count() == 0,
              'AI does NOT auto-create the suggested trigger')
        # non-list guard: a bare string from the model must not char-iterate.
        ep_guard = Episode(user_id=uid, onset=datetime(2020, 1, 4, 9, 0))
        db.session.add(ep_guard); db.session.flush()
        check(_apply_ai_triggers(ep_guard.id, uid, 'Stress') == [],
              '_apply_ai_triggers ignores a non-list triggers value (no char iteration)')
        db.session.rollback()

        # 12. Edit form pre-loads the session suggestion as a pre-checked chip;
        #     it PEEKS (survives reloads) and is only cleared on a successful save.
        ep_conf = Episode(user_id=uid, onset=datetime(2020, 1, 3, 9, 0))
        db.session.add(ep_conf); db.session.commit()
        with c.session_transaction() as s:
            s['pending_ai_triggers'] = {'episode_id': ep_conf.id, 'names': ['Skywriting fumes']}
        body = c.get(f'/episodes/{ep_conf.id}/edit').get_data(as_text=True)
        check('Suggested from your check-in' in body and 'Skywriting fumes' in body,
              'edit form renders the AI suggestion chip + hint')
        check('name="new_trigger_names" value="Skywriting fumes" checked' in body,
              'suggestion rendered as a pre-checked new_trigger_names chip')
        # PEEK: a second GET still shows it (not consumed on render).
        body2 = c.get(f'/episodes/{ep_conf.id}/edit').get_data(as_text=True)
        check('Skywriting fumes' in body2, 'suggestion survives a reload (peek, not consume-on-GET)')
        with c.session_transaction() as s:
            check(s.get('pending_ai_triggers', {}).get('episode_id') == ep_conf.id,
                  'session suggestion still present until saved')
        # A validation-error re-render must KEEP the suggestion (not drop it).
        err_body = c.post(f'/episodes/{ep_conf.id}/edit',
                          data={'onset': '2099-01-01T09:00'}, follow_redirects=False).get_data(as_text=True)
        check('Skywriting fumes' in err_body, 'suggestion preserved on a validation-error re-render')
        # Save confirms + clears it.
        c.post(f'/episodes/{ep_conf.id}/edit',
               data={'onset': '2020-01-03T09:00', 'new_trigger_names': ['Skywriting fumes']},
               follow_redirects=False)
        conf = Trigger.query.filter(Trigger.user_id == uid, db.func.lower(Trigger.name) == 'skywriting fumes').first()
        check(conf is not None, 'saving the episode confirms/creates the suggested trigger')
        link_conf = links_for(ep_conf.id)
        check(len(link_conf) == 1 and link_conf[0].source == 'user' and link_conf[0].trigger_id == conf.id,
              'confirmed suggestion linked with source=user')
        with c.session_transaction() as s:
            check('pending_ai_triggers' not in s, 'session suggestion cleared after a successful save')

        # 13. Editing an episode preserves each re-submitted link's provenance:
        #     an AI-linked trigger stays source='ai', a newly-picked one is 'user'.
        ep_prov = Episode(user_id=uid, onset=datetime(2020, 1, 5, 9, 0))
        db.session.add(ep_prov); db.session.flush()
        db.session.add(EpisodeTrigger(episode_id=ep_prov.id, trigger_id=g_stress2.id, source='ai'))
        db.session.commit()
        c.post(f'/episodes/{ep_prov.id}/edit',
               data={'onset': '2020-01-05T09:00',
                     'trigger_ids': [str(g_stress2.id), str(g_alcohol.id)]},
               follow_redirects=False)
        prov = {t.trigger_id: t.source for t in links_for(ep_prov.id)}
        check(prov.get(g_stress2.id) == 'ai', "re-submitted AI link keeps source='ai' on edit")
        check(prov.get(g_alcohol.id) == 'user', "newly-picked link on the same edit is source='user'")

        # 14. Deleting an episode clears its pending AI suggestion from the session.
        ep_del = Episode(user_id=uid, onset=datetime(2020, 1, 6, 9, 0))
        db.session.add(ep_del); db.session.commit()
        with c.session_transaction() as s:
            s['pending_ai_triggers'] = {'episode_id': ep_del.id, 'names': ['Ghost trigger']}
        c.post(f'/episodes/{ep_del.id}/delete', follow_redirects=False)
        with c.session_transaction() as s:
            check('pending_ai_triggers' not in s, 'delete_episode clears the pending suggestion for that episode')

    if FAILS:
        print(f'\n{len(FAILS)} FAILURE(S)')
        raise SystemExit(1)
    print('\nALL TRIGGER WRITE-PATH TESTS PASSED')


if __name__ == '__main__':
    main()
