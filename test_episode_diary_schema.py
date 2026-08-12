"""Episode-diary Layer A — schema-only regression tests (SPEC-episode-diary.md).

Layer A pre-stages DATA STRUCTURES for the React rebuild: five nullable columns
across three tables, with a CHECK on protocol_events.stop_reason. NOTHING in the
monolith writes to them — these tests assert the schema and the DB-level invariant,
not any capture path (there is none yet, by design).

Covers both creation paths, because they differ by engine and by fresh-vs-existing:
  1. fresh create_all()  — columns + CHECK come from the model DDL (__table_args__)
  2. run_migrations()    — columns + CHECK added to an existing (pre-diary) DB

Plain-script style (no pytest), matching the other test_*.py in this repo. Run:
    WTF_CSRF_ENABLED=false python3 test_episode_diary_schema.py
"""
import os
import tempfile

# Isolated scratch DB — never touch the real dev DB.
_FD, _PATH = tempfile.mkstemp(suffix='.db', prefix='diary_schema_test_')
os.close(_FD)
os.environ['DATABASE_URL'] = 'sqlite:///' + _PATH
os.environ.setdefault('WTF_CSRF_ENABLED', 'false')

from sqlalchemy import inspect, text  # noqa: E402
from app import app, db, run_migrations  # noqa: E402
from database import STOP_REASON_VALUES, STOP_REASON_EVENT_TYPES  # noqa: E402

DIARY_COLUMNS = {
    'protocols': {'med_class'},
    'protocol_events': {'stop_reason', 'stop_reason_note'},
    'users': {'diary_mode_enabled', 'diary_span_counts_both_days'},
}

failures = []


def check(cond, label):
    print(('  ok  ' if cond else '  FAIL ') + label)
    if not cond:
        failures.append(label)


def _cols(table):
    return {c['name'] for c in inspect(db.engine).get_columns(table)}


def _insert_user(conn, email, diary=False, span_both=True):
    conn.execute(text(
        "INSERT INTO users (name,email,is_active,onboarding_complete,ai_logging_enabled,"
        "has_seen_tour,is_admin,email_updates_enabled,diary_mode_enabled,diary_span_counts_both_days) "
        "VALUES ('t',:e,1,0,0,0,0,1,:d,:s)"
    ), {'e': email, 'd': 1 if diary else 0, 's': 1 if span_both else 0})
    return conn.execute(text("SELECT id FROM users WHERE email=:e"), {'e': email}).scalar()


def _insert_protocol(conn, uid):
    conn.execute(text(
        "INSERT INTO protocols (user_id,name,type,status,available) "
        "VALUES (:u,'P','preventative','active',1)"
    ), {'u': uid})
    return conn.execute(text("SELECT id FROM protocols WHERE user_id=:u ORDER BY id DESC LIMIT 1"),
                        {'u': uid}).scalar()


def _pe_insert_ok(pid, uid, event_type, stop_reason):
    """Raw INSERT into protocol_events, bypassing the app layer. True = accepted."""
    sr = 'NULL' if stop_reason is None else "'%s'" % stop_reason
    try:
        with db.engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO protocol_events (protocol_id,user_id,event_type,date,stop_reason) "
                "VALUES (%d,%d,'%s','2026-08-13',%s)" % (pid, uid, event_type, sr)))
        return True
    except Exception:
        return False


def assert_columns_and_check(label_prefix):
    for table, cols in DIARY_COLUMNS.items():
        present = _cols(table)
        check(cols <= present, '%s: %s has %s' % (label_prefix, table, ', '.join(sorted(cols))))

    with db.engine.begin() as conn:
        uid = _insert_user(conn, '%s@t.co' % label_prefix.replace(' ', '_'))
        pid = _insert_protocol(conn, uid)

    # CHECK: value scoped to stop/pause events AND to the controlled vocabulary.
    check(_pe_insert_ok(pid, uid, 'stopped', 'ineffective'), label_prefix + ': stopped + valid value accepted')
    check(_pe_insert_ok(pid, uid, 'paused', 'side_effects'), label_prefix + ': paused + valid value accepted')
    # A stop/pause with NO reason is legitimate — the reason is optional (SPEC G1).
    check(_pe_insert_ok(pid, uid, 'stopped', None), label_prefix + ': stopped + NULL (no reason) accepted')
    check(_pe_insert_ok(pid, uid, 'dose_changed', None), label_prefix + ': dose_changed + NULL accepted')
    check(not _pe_insert_ok(pid, uid, 'dose_changed', 'ineffective'),
          label_prefix + ': dose_changed + reason REJECTED (event-type scope)')
    check(not _pe_insert_ok(pid, uid, 'started', 'cost'),
          label_prefix + ': started + reason REJECTED (event-type scope)')
    check(not _pe_insert_ok(pid, uid, 'stopped', 'made_up'),
          label_prefix + ': stopped + off-vocabulary value REJECTED')


def main():
    print("Vocabulary:", STOP_REASON_VALUES, "on events", STOP_REASON_EVENT_TYPES)

    # ---- Path 1: fresh create_all() (model DDL) ----
    print("\n[fresh create_all path]")
    with app.app_context():
        db.create_all()
        # Boolean defaults land as declared.
        with db.engine.begin() as conn:
            uid = _insert_user(conn, 'defaults@t.co')
        with db.engine.begin() as conn:
            row = conn.execute(text(
                "SELECT diary_mode_enabled, diary_span_counts_both_days FROM users WHERE id=:i"),
                {'i': uid}).first()
        check(bool(row[0]) is False, 'fresh: diary_mode_enabled defaults False')
        check(bool(row[1]) is True, 'fresh: diary_span_counts_both_days defaults True')
        # med_class is intentionally unconstrained (vocabulary deferred to React capture).
        with db.engine.begin() as conn:
            pid = _insert_protocol(conn, uid)
            conn.execute(text("UPDATE protocols SET med_class='anything-goes-here' WHERE id=:i"), {'i': pid})
        check(True, 'fresh: med_class accepts free text (no CHECK, by design)')
        assert_columns_and_check('fresh')

    # ---- Path 2: run_migrations() onto a pre-diary DB ----
    print("\n[run_migrations path]")
    with app.app_context():
        # Rebuild protocol_events without the diary columns/CHECK (SQLite can't DROP a
        # CHECK'd column), and plain-drop the others, to mimic a pre-diary database.
        with db.engine.begin() as conn:
            conn.execute(text('PRAGMA foreign_keys=OFF'))
            conn.execute(text('''CREATE TABLE protocol_events_old (
                id INTEGER PRIMARY KEY, protocol_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                event_type VARCHAR(30) NOT NULL, detail TEXT, date DATE NOT NULL, created_at DATETIME)'''))
            conn.execute(text('DROP TABLE protocol_events'))
            conn.execute(text('ALTER TABLE protocol_events_old RENAME TO protocol_events'))
            conn.execute(text('ALTER TABLE protocols DROP COLUMN med_class'))
            conn.execute(text('ALTER TABLE users DROP COLUMN diary_mode_enabled'))
            conn.execute(text('ALTER TABLE users DROP COLUMN diary_span_counts_both_days'))
            conn.execute(text('PRAGMA foreign_keys=ON'))
        pre = _cols('protocol_events')
        check('stop_reason' not in pre, 'migration: pre-state has no stop_reason column')

        run_migrations()
        assert_columns_and_check('migration')

        # Idempotent: a second run must not error or duplicate.
        try:
            run_migrations()
            check(True, 'migration: second run is idempotent (no error)')
        except Exception as e:  # noqa: BLE001
            check(False, 'migration: second run raised %r' % e)

    print()
    if failures:
        print('FAILURES (%d):' % len(failures))
        for f in failures:
            print('  -', f)
        raise SystemExit(1)
    print('ALL EPISODE-DIARY SCHEMA TESTS PASSED')


if __name__ == '__main__':
    try:
        main()
    finally:
        try:
            os.remove(_PATH)
        except OSError:
            pass
