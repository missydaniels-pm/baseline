#!/usr/bin/env python3
"""Pre-flight orphan check for FK cleanup — run BEFORE deploying the migration.

`ALTER TABLE ... ADD CONSTRAINT ... FOREIGN KEY` validates every existing row.
If any child row points at a parent that no longer exists, the ALTER fails and
that FK silently stays on its old `NO ACTION` behaviour (the migration logs a
warning and continues, by design, so one bad FK can't abort a deploy). The
result would be a database that disagrees with the declared matrix — exactly
what Increment 2 must not be built on.

Orphans shouldn't exist: these columns have always had *a* FK constraint, just
`NO ACTION` rather than CASCADE. But a direct DB edit, a partial earlier
migration, or a bulk operation run with constraints disabled could have left
some, and nothing in the app would have noticed. This checks instead of hoping.

READ-ONLY. It issues SELECTs only and never modifies anything.

Run against staging first, then production:

    DATABASE_URL='postgresql://...' python3 check_fk_orphans.py

Use the Railway *public* DB URL, not the internal `postgres.railway.internal`
host, which only resolves inside Railway (see STAGING_SETUP.md).

Exit code 0 = clean, safe to migrate. 1 = orphans found, investigate first.
"""
import os
import sys

if not os.environ.get('DATABASE_URL'):
    print('Set DATABASE_URL to the database you want to check (public URL).')
    print("Local SQLite: DATABASE_URL='sqlite:///instance/migraine_tracker.db'")
    sys.exit(2)

from sqlalchemy import create_engine, text

from database import EXPECTED_FK_ONDELETE

# Which parent table each FK column points at. Kept explicit rather than
# introspected so this script still works on a database whose constraints are
# in an unexpected state — the thing it exists to detect.
PARENT_OF = {
    'user_id': 'users',
    'used_by_user_id': 'users',
    'episode_id': 'episodes',
    'symptom_id': 'symptoms',
    'protocol_id': 'protocols',
    'trigger_id': 'triggers',
}


def main():
    url = os.environ['DATABASE_URL']
    if url.startswith('postgres://'):           # Railway legacy scheme
        url = url.replace('postgres://', 'postgresql://', 1)

    safe = url.split('@')[-1] if '@' in url else url
    print(f'Checking {len(EXPECTED_FK_ONDELETE)} foreign keys on {safe}\n')

    engine = create_engine(url)
    total_orphans = 0
    problems = []

    with engine.connect() as conn:
        existing = set()
        for row in conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema NOT IN "
            "('pg_catalog','information_schema')" if url.startswith('postgresql')
            else "SELECT name AS table_name FROM sqlite_master WHERE type='table'"
        )):
            existing.add(row[0])

        for (table, column), expected in sorted(EXPECTED_FK_ONDELETE.items()):
            parent = PARENT_OF[column]
            if table not in existing or parent not in existing:
                print(f'  SKIP {table}.{column} — table not present')
                continue
            # A NULL FK is not an orphan; it's a legitimately detached row.
            count = conn.execute(text(
                f'SELECT COUNT(*) FROM {table} c '
                f'LEFT JOIN {parent} p ON c.{column} = p.id '
                f'WHERE c.{column} IS NOT NULL AND p.id IS NULL'
            )).scalar()
            flag = 'OK  ' if count == 0 else 'ORPHANS'
            print(f'  {flag} {table}.{column} -> {parent}.id '
                  f'({expected}){"" if count == 0 else f" — {count} orphaned row(s)"}')
            if count:
                total_orphans += count
                problems.append((table, column, parent, count))

    print()
    if not problems:
        print('CLEAN — no orphaned rows. ADD CONSTRAINT will validate successfully.')
        return 0

    print(f'FOUND {total_orphans} orphaned row(s) across {len(problems)} foreign key(s).')
    print('ADD CONSTRAINT WILL FAIL for these, leaving them on the old NO ACTION')
    print('behaviour while the rest migrate. Investigate before deploying:\n')
    for table, column, parent, count in problems:
        print(f'  SELECT * FROM {table} c LEFT JOIN {parent} p ON c.{column} = p.id')
        print(f'  WHERE c.{column} IS NOT NULL AND p.id IS NULL;   -- {count} row(s)\n')
    return 1


if __name__ == '__main__':
    sys.exit(main())
