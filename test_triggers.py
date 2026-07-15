"""Data-model pass verification for structured triggers.

Runs against an ISOLATED temp SQLite DB (set before importing app, which
bootstraps create_all + run_migrations at import) so the local dev database is
never touched. Exercises seed correctness/idempotency, the two partial unique
indexes, the (episode, trigger) uniqueness, and FK-safe deletion under
PRAGMA foreign_keys=ON (the same enforcement production PostgreSQL has).

Run:  python test_triggers.py
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmpdir}/test_triggers.db'
os.environ['DEBUG'] = 'true'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from datetime import datetime
from sqlalchemy.exc import IntegrityError

from app import app, run_migrations, SEED_TRIGGERS
from database import db, User, Episode, Trigger, EpisodeTrigger


def main():
    with app.app_context():
        # 1. Curated globals present (user_id NULL), within the product cap of 12,
        #    and what was seeded matches what was declared.
        globals_ = Trigger.query.filter(Trigger.user_id.is_(None)).all()
        assert len(SEED_TRIGGERS) <= 12, f"seed list exceeds the 12-item cap: {len(SEED_TRIGGERS)}"
        assert len(globals_) == len(SEED_TRIGGERS), f"seeded {len(globals_)}, declared {len(SEED_TRIGGERS)}"
        assert {t.name for t in globals_} == set(SEED_TRIGGERS)
        assert all(t.is_active for t in globals_)
        print("PASS: 12 curated globals seeded (Strong smells present:",
              'Strong smells' in {t.name for t in globals_}, ")")

        # 2. Idempotent: re-running migrations does not duplicate.
        run_migrations()
        assert Trigger.query.filter(Trigger.user_id.is_(None)).count() == 12
        print("PASS: seed idempotent across a second run_migrations()")

        # 3. Global uniqueness index rejects a duplicate global (case-insensitive).
        db.session.add(Trigger(user_id=None, name='stress'))
        try:
            db.session.commit()
            raise SystemExit("FAIL: duplicate global 'stress' was allowed")
        except IntegrityError:
            db.session.rollback()
            print("PASS: duplicate global rejected (case-insensitive)")

        # Two users for the custom-trigger cases.
        u1 = User(name='u1', email='u1@example.com')
        u2 = User(name='u2', email='u2@example.com')
        db.session.add_all([u1, u2])
        db.session.commit()

        # 4. Same custom name is allowed across DIFFERENT users.
        db.session.add(Trigger(user_id=u1.id, name='Screens'))
        db.session.add(Trigger(user_id=u2.id, name='Screens'))
        db.session.commit()
        print("PASS: same custom name allowed across different users")

        # 5. Duplicate custom per-user rejected (case-insensitive).
        db.session.add(Trigger(user_id=u1.id, name='screens'))
        try:
            db.session.commit()
            raise SystemExit("FAIL: duplicate custom-per-user was allowed")
        except IntegrityError:
            db.session.rollback()
            print("PASS: duplicate custom-per-user rejected (case-insensitive)")

        # 6. Episode linked to one global + one custom trigger.
        ep = Episode(user_id=u1.id, onset=datetime.utcnow())
        db.session.add(ep)
        db.session.commit()
        g = Trigger.query.filter(Trigger.user_id.is_(None)).first()
        c = Trigger.query.filter_by(user_id=u1.id, name='Screens').first()
        db.session.add(EpisodeTrigger(episode_id=ep.id, trigger_id=g.id))
        db.session.add(EpisodeTrigger(episode_id=ep.id, trigger_id=c.id))
        db.session.commit()

        # 6a. (episode, trigger) uniqueness.
        db.session.add(EpisodeTrigger(episode_id=ep.id, trigger_id=g.id))
        try:
            db.session.commit()
            raise SystemExit("FAIL: duplicate (episode, trigger) was allowed")
        except IntegrityError:
            db.session.rollback()
            print("PASS: duplicate (episode, trigger) rejected")

        # 6b. FK is enforced: deleting the parent Episode while links exist fails.
        #     (Proves the manual delete ordering in delete_account/dev_reset is
        #     load-bearing, not decorative.)
        try:
            Episode.query.filter_by(user_id=u1.id).delete(synchronize_session=False)
            db.session.commit()
            raise SystemExit("FAIL: episode delete with live EpisodeTrigger links did not raise")
        except IntegrityError:
            db.session.rollback()
            print("PASS: FK enforced — episode delete blocked while links exist")

        # 6c. Correct ordering (mirrors delete_account / dev_reset): links, then
        #     episodes, then this user's custom triggers. Globals + the OTHER
        #     user's custom must survive.
        eids = db.session.query(Episode.id).filter_by(user_id=u1.id)
        EpisodeTrigger.query.filter(EpisodeTrigger.episode_id.in_(eids)).delete(synchronize_session=False)
        Episode.query.filter_by(user_id=u1.id).delete(synchronize_session=False)
        Trigger.query.filter_by(user_id=u1.id).delete(synchronize_session=False)
        db.session.commit()

        assert Trigger.query.filter(Trigger.user_id.is_(None)).count() == 12, "globals must survive"
        assert Trigger.query.filter_by(user_id=u1.id).count() == 0, "u1 customs gone"
        assert Trigger.query.filter_by(user_id=u2.id).count() == 1, "u2 custom must survive"
        assert EpisodeTrigger.query.count() == 0, "no dangling links"
        print("PASS: FK-safe delete ordering (globals + other user's customs survive)")

        # 7. ORM-cascade path (the /episodes/<id>/delete route): db.session.delete(episode)
        #    must remove the EpisodeTrigger children via cascade='all, delete-orphan'
        #    — the same mechanism the route already relies on for SymptomScore /
        #    EpisodeIntervention. Proves delete_episode won't 500 once links exist.
        db.session.expunge_all()  # clear stale identities (SQLite reuses PKs after deletes)
        ep2 = Episode(user_id=u2.id, onset=datetime.utcnow())
        db.session.add(ep2)
        db.session.commit()
        g2 = Trigger.query.filter(Trigger.user_id.is_(None)).first()
        c2 = Trigger.query.filter_by(user_id=u2.id).first()
        db.session.add(EpisodeTrigger(episode_id=ep2.id, trigger_id=g2.id))
        db.session.add(EpisodeTrigger(episode_id=ep2.id, trigger_id=c2.id))
        db.session.commit()
        assert EpisodeTrigger.query.filter_by(episode_id=ep2.id).count() == 2

        ep2_fresh = Episode.query.filter_by(id=ep2.id).first()  # fresh fetch, children NOT eager-loaded
        db.session.delete(ep2_fresh)
        db.session.commit()  # would raise IntegrityError if cascade didn't fire
        assert EpisodeTrigger.query.filter_by(episode_id=ep2.id).count() == 0, "cascade must clear links"
        assert Trigger.query.filter(Trigger.user_id.is_(None)).count() == len(SEED_TRIGGERS), "global survives"
        assert Trigger.query.filter_by(user_id=u2.id).count() == 1, "custom trigger survives episode delete"
        print("PASS: ORM cascade clears EpisodeTrigger on db.session.delete(episode) [settles B1]")

        print("\nALL TRIGGER TESTS PASSED")


if __name__ == '__main__':
    main()
