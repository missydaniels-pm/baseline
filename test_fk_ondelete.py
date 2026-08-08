"""FK cleanup Increment 1 — DB-level ON DELETE directives.

Runs against an ISOLATED temp SQLite DB (set before importing app, which
bootstraps create_all + run_migrations at import) so the local dev database is
never touched.

Two things are verified, and the distinction matters:

  1. The live schema matches database.EXPECTED_FK_ONDELETE exactly. This is the
     same check that runs at startup on PostgreSQL, so local and production are
     held to one declared matrix — the guard against a hand-rolled migration
     silently drifting from the models.

  2. The database ACTUALLY ENFORCES those directives — deleting a parent really
     does cascade, and the three deliberate SET NULL cases really do detach
     rather than delete. These are exercised with raw SQL deletes, bypassing the
     ORM and the hand-ordered cleanup in the routes, because Increment 2 removes
     that cleanup and leaves the DB solely responsible.

The SET NULL cases are the ones worth being paranoid about: they encode product
decisions (deleting a protocol keeps its experiment; deleting an episode keeps
the chat history). A CASCADE there would silently destroy user history.

Run:  python3 test_fk_ondelete.py
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp()
os.environ['DATABASE_URL'] = f'sqlite:///{_tmpdir}/test_fk_ondelete.db'
os.environ['DEBUG'] = 'true'
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')

from datetime import datetime, date

from sqlalchemy import text

from app import app
from database import (db, User, Episode, Protocol, Symptom, SymptomScore,
                      EpisodeIntervention, Experiment, CheckIn, ProtocolCompliance,
                      ProtocolEvent, UserActivity, Trigger, EpisodeTrigger,
                      EXPECTED_FK_ONDELETE, verify_fk_ondelete)

failures = []


def check(label, condition):
    if condition:
        print(f'PASS: {label}')
    else:
        print(f'FAIL: {label}')
        failures.append(label)


def _fixture():
    """A user with one of everything, wired together."""
    u = User(name='FK Test', email=f'fk{datetime.utcnow().timestamp()}@test.com')
    db.session.add(u)
    db.session.flush()

    sym = Symptom(user_id=u.id, name='Headache')
    proto = Protocol(user_id=u.id, name='Magnesium', type='preventative',
                     start_date=date.today(), status='active')
    trig = Trigger(user_id=u.id, name=f'Custom {u.id}')
    db.session.add_all([sym, proto, trig])
    db.session.flush()

    ep = Episode(user_id=u.id, onset=datetime.utcnow())
    db.session.add(ep)
    db.session.flush()

    db.session.add_all([
        SymptomScore(episode_id=ep.id, symptom_id=sym.id, score=7),
        EpisodeIntervention(episode_id=ep.id, protocol_id=proto.id, effectiveness=5),
        EpisodeTrigger(episode_id=ep.id, trigger_id=trig.id, source='user'),
        CheckIn(user_id=u.id, episode_id=ep.id, role='user', content='m'),
        Experiment(user_id=u.id, protocol_id=proto.id, name='Exp',
                   hypothesis='h', start_date=date.today(), status='active'),
        ProtocolCompliance(user_id=u.id, protocol_id=proto.id, date=date.today(), took=True),
        ProtocolEvent(user_id=u.id, protocol_id=proto.id, event_type='started',
                      date=date.today()),
        UserActivity(user_id=u.id, event_type='login', detail='x'),
    ])
    db.session.commit()
    return u, ep, proto, sym, trig


def raw_delete(table, row_id):
    """Delete straight through the DB, bypassing every ORM cascade and the
    hand-ordered cleanup in the routes — so only the FK directives are in play."""
    db.session.execute(text(f'DELETE FROM {table} WHERE id = :i'), {'i': row_id})
    db.session.commit()
    # The DB changed rows behind the ORM's back, so anything already in the
    # identity map is stale. Without this, a SET NULL assertion can read a
    # cached object still holding the old FK and pass for the wrong reason.
    db.session.expire_all()


def main():
    with app.app_context():
        # 0. Declared matrix vs the MODELS themselves.
        #    verify_fk_ondelete() only walks the dict's own keys, so it compares
        #    dict<->live-DB and is blind to a model FK that was never added to the
        #    dict at all. That blind spot would make "single source of truth"
        #    aspirational: add a new FK column, forget the dict entry, and nothing
        #    anywhere complains. This closes it by walking SQLAlchemy metadata —
        #    no DB connection needed — so a forgotten entry fails here first.
        declared, actual_models = set(EXPECTED_FK_ONDELETE), set()
        model_mismatches = []
        for table in db.metadata.tables.values():
            for col in table.columns:
                for fk in col.foreign_keys:
                    key = (table.name, col.name)
                    actual_models.add(key)
                    want = EXPECTED_FK_ONDELETE.get(key)
                    if want is None:
                        continue  # reported below as a missing dict entry
                    if (fk.ondelete or '').upper() != want:
                        model_mismatches.append(
                            f'{table.name}.{col.name}: model says {fk.ondelete!r}, dict says {want!r}')
        check(f'every model FK is declared in the matrix ({len(actual_models)} found)',
              actual_models == declared)
        for k in sorted(actual_models - declared):
            print(f'        model FK missing from EXPECTED_FK_ONDELETE: {k[0]}.{k[1]}')
        for k in sorted(declared - actual_models):
            print(f'        dict entry with no matching model FK: {k[0]}.{k[1]}')
        check('every model ondelete= agrees with the matrix', model_mismatches == [])
        for m in model_mismatches:
            print('       ', m)

        # 1. Declared matrix vs live schema.
        problems = verify_fk_ondelete(db.engine)
        check(f'schema matches all {len(EXPECTED_FK_ONDELETE)} declared FK directives',
              problems == [])
        for p in problems:
            print('       ', p)

        # 2. Deleting an episode cascades its children...
        u, ep, proto, sym, trig = _fixture()
        ep_id, u_id, proto_id = ep.id, u.id, proto.id
        raw_delete('episodes', ep_id)
        check('episode delete cascades SymptomScore',
              SymptomScore.query.filter_by(episode_id=ep_id).count() == 0)
        check('episode delete cascades EpisodeIntervention',
              EpisodeIntervention.query.filter_by(episode_id=ep_id).count() == 0)
        check('episode delete cascades EpisodeTrigger',
              EpisodeTrigger.query.filter_by(episode_id=ep_id).count() == 0)

        # ...but the check-in survives, detached. Chat history is not the
        # episode's to destroy.
        ci = CheckIn.query.filter_by(user_id=u_id).first()
        check('episode delete DETACHES CheckIn (SET NULL), does not delete it',
              ci is not None and ci.episode_id is None)

        # 3. Deleting a protocol cascades its own children...
        raw_delete('protocols', proto_id)
        check('protocol delete cascades ProtocolCompliance',
              ProtocolCompliance.query.filter_by(protocol_id=proto_id).count() == 0)
        check('protocol delete cascades ProtocolEvent',
              ProtocolEvent.query.filter_by(protocol_id=proto_id).count() == 0)

        # ...but the experiment survives, detached — the hypothesis and outcome
        # are the user's record, and the delete dialog promises they're kept.
        exp = Experiment.query.filter_by(user_id=u_id).first()
        check('protocol delete DETACHES Experiment (SET NULL), does not delete it',
              exp is not None and exp.protocol_id is None)

        # 4. Deleting the user takes everything owned with it.
        raw_delete('users', u_id)
        remaining = {
            'Episode': Episode.query.filter_by(user_id=u_id).count(),
            'Symptom': Symptom.query.filter_by(user_id=u_id).count(),
            'Protocol': Protocol.query.filter_by(user_id=u_id).count(),
            'Experiment': Experiment.query.filter_by(user_id=u_id).count(),
            'CheckIn': CheckIn.query.filter_by(user_id=u_id).count(),
            'ProtocolCompliance': ProtocolCompliance.query.filter_by(user_id=u_id).count(),
            'ProtocolEvent': ProtocolEvent.query.filter_by(user_id=u_id).count(),
            'UserActivity': UserActivity.query.filter_by(user_id=u_id).count(),
            'Trigger(custom)': Trigger.query.filter_by(user_id=u_id).count(),
        }
        check(f'user delete cascades every owned child ({remaining})',
              all(v == 0 for v in remaining.values()))

        # 5. Global triggers are shared and must survive a user deletion.
        globals_left = Trigger.query.filter_by(user_id=None).count()
        check(f'global triggers survive user deletion ({globals_left} intact)',
              globals_left > 0)

        # 6. Deleting a symptom cascades its scores but leaves other users alone.
        u2, ep2, proto2, sym2, trig2 = _fixture()
        other_u, other_ep, _, other_sym, _ = _fixture()
        sym2_id, other_sym_id = sym2.id, other_sym.id
        raw_delete('symptoms', sym2_id)
        check('symptom delete cascades only its own SymptomScores',
              SymptomScore.query.filter_by(symptom_id=sym2_id).count() == 0
              and SymptomScore.query.filter_by(symptom_id=other_sym_id).count() == 1)

        # 7. The PostgreSQL migration path. This branch NEVER runs locally
        #    (SQLite short-circuits it), so without this it would first execute
        #    on staging completely unproven — a NameError or a malformed ALTER
        #    would only surface there. Drive it with a fake connection and an
        #    inspector reporting the pre-migration state.
        from app import migrate_fk_ondelete

        class FakeInspector:
            """Reports every FK as NO ACTION, i.e. a real pre-migration DB."""
            def get_table_names(self):
                return sorted({t for t, _ in EXPECTED_FK_ONDELETE})

            def get_foreign_keys(self, table):
                out = []
                for (t, col) in EXPECTED_FK_ONDELETE:
                    if t != table:
                        continue
                    referred = {'user_id': 'users', 'used_by_user_id': 'users',
                                'episode_id': 'episodes', 'symptom_id': 'symptoms',
                                'protocol_id': 'protocols', 'trigger_id': 'triggers'}[col]
                    out.append({'name': f'{t}_{col}_fkey', 'constrained_columns': [col],
                                'referred_table': referred, 'referred_columns': ['id'],
                                'options': {}})
                return out

        class FakeConn:
            def __init__(self):
                self.statements = []
            def execute(self, stmt):
                self.statements.append(str(stmt))
            def commit(self):
                pass
            def rollback(self):
                raise AssertionError('migration rolled back — a statement failed')

        conn = FakeConn()
        sql = migrate_fk_ondelete(conn, FakeInspector(), is_sqlite=False)
        check(f'postgres path emits DROP+ADD for all {len(EXPECTED_FK_ONDELETE)} FKs',
              len(sql) == 2 * len(EXPECTED_FK_ONDELETE))
        check('every ADD CONSTRAINT carries the declared ON DELETE',
              all(any(s.startswith(f'ALTER TABLE {t} ADD CONSTRAINT')
                      and f'({c})' in s and s.endswith(f'ON DELETE {exp}')
                      for s in sql)
                  for (t, c), exp in EXPECTED_FK_ONDELETE.items()))
        check('SET NULL is emitted for exactly the three detach cases',
              sorted(s.split(' ')[2] + '.' for s in sql if s.endswith('ON DELETE SET NULL'))
              == ['checkins.', 'experiments.', 'invite_codes.'])
        # Idempotency: a DB already migrated must produce no statements at all.
        class MigratedInspector(FakeInspector):
            def get_foreign_keys(self, table):
                fks = super().get_foreign_keys(table)
                for fk in fks:
                    fk['options'] = {'ondelete': EXPECTED_FK_ONDELETE[(table, fk['constrained_columns'][0])]}
                return fks
        check('re-running on an already-migrated DB is a no-op',
              migrate_fk_ondelete(FakeConn(), MigratedInspector(), is_sqlite=False) == [])
        check('sqlite short-circuits the postgres path',
              migrate_fk_ondelete(FakeConn(), FakeInspector(), is_sqlite=True) == [])

    print()
    if failures:
        print(f'{len(failures)} FK ONDELETE TEST(S) FAILED')
        for f in failures:
            print('  -', f)
        raise SystemExit(1)
    print('ALL FK ONDELETE TESTS PASSED')


if __name__ == '__main__':
    main()
