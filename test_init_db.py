"""Importing app writes nothing; `flask init-db` sets the database up — review F7 (11/21/26).

Until 11/21/26 importing app.py ran create_all, the migrations, the
unverified-user purge, the admin flag, the Resend backfill and the FK check
against whatever DATABASE_URL was set — so a test or script run in a shell with
a production URL exported wrote to production. Now that work runs only through
init_db() / `flask --app app init-db` (the Dockerfile CMD runs it before
gunicorn).

Covered:
  1. Importing app creates no tables (and, on SQLite, no database file).
  2. `init-db` (through Flask's CLI runner, as the Dockerfile calls it) exits 0
     and builds the schema; a second run is a no-op that still exits 0.
  3. The startup tasks run through it (ADMIN_EMAIL's account becomes admin).
  4. The delete safety gate works in a process that never ran init-db — the
     serving process under gunicorn. It checks the FK schema itself on first
     use: a mismatch refuses a real delete request and keeps the row; a correct
     schema lets the delete through.

Isolated temp DB. Run: python test_init_db.py
"""
import os
import tempfile
from datetime import datetime

# TEST_DATABASE_URL (CI's Postgres job, a fresh database per suite) wins;
# otherwise an isolated temp SQLite DB that does not exist yet.
_tmpdir = tempfile.mkdtemp()
_sqlite_path = f'{_tmpdir}/t.db'
DB_URL = os.environ.get('TEST_DATABASE_URL') or f'sqlite:///{_sqlite_path}'
os.environ['DATABASE_URL'] = DB_URL
os.environ['WTF_CSRF_ENABLED'] = 'false'
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.pop('RESEND_API_KEY', None)
os.environ.pop('RESEND_AUDIENCE_ID', None)
ADMIN = 'owner@example.test'
os.environ['ADMIN_EMAIL'] = ADMIN

from sqlalchemy import create_engine, inspect  # noqa: E402

from database import normalize_database_url  # noqa: E402

FAILS = []


def check(cond, msg):
    print(('PASS: ' if cond else 'FAIL: ') + msg)
    if not cond:
        FAILS.append(msg)


def table_names():
    eng = create_engine(normalize_database_url(DB_URL))
    try:
        return set(inspect(eng).get_table_names())
    finally:
        eng.dispose()


def main():
    sqlite = DB_URL.startswith('sqlite')
    # (Inspecting a SQLite URL creates the file, so on SQLite the "nothing
    # there yet" check is the file's absence, taken after the import below.)
    if not sqlite:
        check(table_names() == set(), '[before import] the test database starts empty')

    # ── 1. Importing app touches no database ──
    import app as app_module
    from app import app, PRIVACY_POLICY_VERSION
    check(app.config['SQLALCHEMY_DATABASE_URI'] == normalize_database_url(DB_URL),
          '[import] app is pointed at the test database')
    if sqlite:
        check(not os.path.exists(_sqlite_path), '[import] no SQLite file was created')
    check(table_names() == set(), '[import] importing app created no tables')
    check(app_module.FK_SCHEMA_PROBLEMS is None, '[import] the FK check did not run at import')

    # ── 2. init-db builds the schema; running it again is harmless ──
    runner = app.test_cli_runner()
    r = runner.invoke(args=['init-db'])
    check(r.exit_code == 0 and 'init-db: done' in r.output,
          f'[init-db] exits 0 and reports done (exit {r.exit_code}, output {r.output!r})')
    tables = table_names()
    check({'users', 'episodes', 'symptom_scores', 'triggers'} <= tables,
          f'[init-db] schema created ({len(tables)} tables)')

    # ── 3. Startup tasks run through it ──
    from database import db, User, Episode
    with app.app_context():
        owner = User(email=ADMIN, name='Owner', password_hash='x', is_active=True,
                     verified_at=datetime.utcnow(), onboarding_complete=True,
                     has_seen_tour=True, privacy_policy_version=PRIVACY_POLICY_VERSION)
        db.session.add(owner)
        db.session.commit()
        check(not owner.is_admin, '[startup tasks] owner starts without admin')

    r = runner.invoke(args=['init-db'])
    check(r.exit_code == 0, f'[init-db] second run exits 0 (exit {r.exit_code})')
    with app.app_context():
        owner = User.query.filter_by(email=ADMIN).one()
        check(owner.is_admin, '[startup tasks] ADMIN_EMAIL account made admin by init-db')
        uid = owner.id
        ep1 = Episode(user_id=uid, onset=datetime(2026, 11, 1, 9, 0))
        ep2 = Episode(user_id=uid, onset=datetime(2026, 11, 2, 9, 0))
        db.session.add_all([ep1, ep2])
        db.session.commit()
        ep1_id, ep2_id = ep1.id, ep2.id

    client = app.test_client()
    with client.session_transaction() as sess:
        sess['user_id'] = uid

    # ── 4. Delete gate in a process that never ran init-db ──
    # Simulate the gunicorn process: the module-level result is unknown (None).
    real_verify = app_module.verify_fk_ondelete
    try:
        app_module.FK_SCHEMA_PROBLEMS = None
        app_module.verify_fk_ondelete = lambda engine: ['episodes.user_id: expected CASCADE, got NO ACTION']
        r = client.post(f'/episodes/{ep1_id}/delete')
        with app.app_context():
            kept = db.session.get(Episode, ep1_id) is not None
        check(r.status_code == 302 and kept,
              '[delete gate] mismatch found on first use refuses the delete; the episode is kept')
        check(app_module.FK_SCHEMA_PROBLEMS == ['episodes.user_id: expected CASCADE, got NO ACTION'],
              '[delete gate] the check ran once in this process and its result is kept')
    finally:
        app_module.verify_fk_ondelete = real_verify

    # Reflection failing: no crash, delete allowed (diagnostic, not a gate on
    # everything — the pre-11/21 behaviour), and it retries next time.
    def boom(engine):
        raise RuntimeError('reflection unavailable')
    try:
        app_module.FK_SCHEMA_PROBLEMS = None
        app_module.verify_fk_ondelete = boom
        with app.test_request_context():
            result = app_module._fk_schema_problems()
        check(result == [] and app_module.FK_SCHEMA_PROBLEMS is None,
              '[delete gate] a failed check allows deletes and is retried next time')
    finally:
        app_module.verify_fk_ondelete = real_verify

    app_module.FK_SCHEMA_PROBLEMS = None
    r = client.post(f'/episodes/{ep2_id}/delete')
    with app.app_context():
        gone = db.session.get(Episode, ep2_id) is None
    check(r.status_code == 302 and gone,
          '[delete gate] correct schema found on first use: the delete goes through')
    check(app_module.FK_SCHEMA_PROBLEMS == [], '[delete gate] correct schema recorded as no problems')

    # ── 5. Production actually runs it: the Dockerfile CMD calls init-db first ──
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Dockerfile')) as f:
        cmd = [l for l in f if l.startswith('CMD ')]
    check(len(cmd) == 1 and 'flask --app app init-db && exec gunicorn app:app' in cmd[0],
          '[Dockerfile] CMD runs init-db, then execs gunicorn')

    if FAILS:
        print(f'\n{len(FAILS)} FAILED')
        raise SystemExit(1)
    print('\nALL INIT-DB TESTS PASSED')


if __name__ == '__main__':
    main()
