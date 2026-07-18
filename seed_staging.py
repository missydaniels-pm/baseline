#!/usr/bin/env python3
"""Seed the STAGING database with 12 weeks of synthetic health data.

Run against the Railway *staging* environment only:

    railway run --environment staging python seed_staging.py

(or with the staging DATABASE_URL exported locally). It reuses the exact seed
logic behind /dev/seed (``app.seed_test_data``) so staging data matches what
local dev generates — including triggers, a binary symptom, and Protocol.why,
which the migrations we're about to test all touch.

Safety guards (staging is a public Railway URL with its own throwaway DB, but
this script writes rows and must never touch production):
  * Refuses to run unless ``CONFIRM_SEED=yes`` — guards against pointing it at
    the wrong database by reflex.
  * Hard-refuses if ``APP_URL`` looks like production.
  * Only seeds a user with < 20 episodes (mirrors the /dev/seed guard), so a
    second run is a no-op rather than a duplicate.
"""
import os
import sys

# Domains that mean "this is production, do not seed."
PROD_MARKERS = ('mybaselineapp.com', 'baseline-health.up.railway.app')


def main():
    if os.environ.get('CONFIRM_SEED') != 'yes':
        sys.exit(
            'Refusing to seed: set CONFIRM_SEED=yes to confirm this is a '
            'staging/dev database (never run this against production).'
        )

    app_url = os.environ.get('APP_URL', '')
    if any(marker in app_url for marker in PROD_MARKERS):
        sys.exit(
            f'Refusing to seed: APP_URL ({app_url!r}) looks like production. '
            'Aborting to protect real user data.'
        )

    # Import only after the guards pass — importing app runs migrations against
    # whatever DATABASE_URL is set, so we confirm the target first.
    from datetime import datetime
    from app import app, db, bcrypt, seed_test_data
    from database import User, Episode

    email = os.environ.get('STAGING_SEED_EMAIL', 'staging@baseline.test')
    password = os.environ.get('STAGING_SEED_PASSWORD', 'Staging2026!')

    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(
                name='Staging Tester',
                email=email,
                password_hash=bcrypt.generate_password_hash(password).decode('utf-8'),
                is_active=True,
                onboarding_complete=True,
                verified_at=datetime.utcnow(),
            )
            db.session.add(user)
            db.session.commit()
            print(f'Created staging user: {email}')
        else:
            print(f'Using existing staging user: {email}')

        existing = Episode.query.filter_by(user_id=user.id).count()
        if existing >= 20:
            print(
                f'User already has {existing} episodes (>= 20) — skipping seed '
                'so this stays idempotent. Nothing written.'
            )
            return

        seed_test_data(user)
        total = Episode.query.filter_by(user_id=user.id).count()
        print(f'Seeded 12 weeks of data. User now has {total} episodes.')
        print(f'Log in at your staging URL with:  {email}  /  {password}')


if __name__ == '__main__':
    main()
