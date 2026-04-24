import os
import json
import re
import random
import secrets
import hashlib
import hmac
import base64
import resend
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_bcrypt import Bcrypt
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy import text
from database import db, User, Episode, Protocol, Symptom, SymptomScore, EpisodeIntervention, Experiment, CheckIn, ProtocolCompliance, ProtocolEvent, InviteCode, UsedVerifyToken, UserActivity
from collections import defaultdict
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

app = Flask(__name__)

database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Railway provides postgres:// but SQLAlchemy requires postgresql://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {}
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///migraine_tracker.db'
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {'check_same_thread': False, 'timeout': 20}
    }
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or 'dev-only-' + secrets.token_hex(16)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)

db.init_app(app)
bcrypt = Bcrypt(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri='memory://',
)

VERIFY_TOKEN_MAX_AGE = 60 * 60 * 24  # 24 hours
VERIFY_SALT = 'baseline-email-verify-v1'


def _compute_asset_version(relpath):
    """Short content hash for cache-busting the query string on static assets.
    Falls back to a fixed string if the file is missing (shouldn't happen)."""
    path = os.path.join(app.static_folder, relpath)
    try:
        with open(path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()[:8]
    except OSError:
        return '0'


CSS_VERSION = _compute_asset_version('css/style.css')


@app.context_processor
def inject_asset_versions():
    return {'CSS_VERSION': CSS_VERSION}

DISPOSABLE_EMAIL_DOMAINS = {
    'mailinator.com', 'tempmail.com', 'temp-mail.org', 'guerrillamail.com',
    'guerrillamail.net', 'guerrillamail.org', 'guerrillamailblock.com',
    'sharklasers.com', 'grr.la', 'throwawaymail.com', 'yopmail.com',
    '10minutemail.com', '10minutemail.net', 'trashmail.com', 'trashmail.net',
    'getnada.com', 'getairmail.com', 'dispostable.com', 'fakeinbox.com',
    'mytemp.email', 'mohmal.com', 'emailondeck.com', 'maildrop.cc',
    'moakt.com', 'mintemail.com', 'mailnesia.com',
}


def is_disposable_email(email):
    try:
        domain = email.rsplit('@', 1)[1].lower()
    except IndexError:
        return False
    return domain in DISPOSABLE_EMAIL_DOMAINS


def _verify_serializer():
    return URLSafeTimedSerializer(app.config['SECRET_KEY'])


def generate_verify_token(email):
    return _verify_serializer().dumps(email, salt=VERIFY_SALT)


def load_verify_token(token, max_age=VERIFY_TOKEN_MAX_AGE):
    """Return (email, error) — error is 'expired', 'invalid', or None."""
    try:
        email = _verify_serializer().loads(token, salt=VERIFY_SALT, max_age=max_age)
        return email, None
    except SignatureExpired:
        return None, 'expired'
    except BadSignature:
        return None, 'invalid'


UNSUBSCRIBE_SALT = b'baseline-email-unsubscribe-v1'


def _unsubscribe_mac(email):
    key = app.config['SECRET_KEY']
    key_bytes = key.encode('utf-8') if isinstance(key, str) else key
    msg = UNSUBSCRIBE_SALT + b':' + email.lower().encode('utf-8')
    return hmac.new(key_bytes, msg, hashlib.sha256).digest()


def generate_unsubscribe_token(email):
    """Stateless HMAC-SHA256 token that lets any email recipient unsubscribe.
    No expiry: an old welcome email years later should still work. The email
    payload is base64-encoded for URL safety, not encrypted — the security
    property is authenticity (the HMAC), not confidentiality."""
    digest = _unsubscribe_mac(email)
    sig = base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')
    payload = base64.urlsafe_b64encode(email.lower().encode('utf-8')).rstrip(b'=').decode('ascii')
    return f'{payload}.{sig}'


def load_unsubscribe_token(token):
    """Return email on valid token, None otherwise. Constant-time compare."""
    try:
        # Base64url alphabet excludes '.', so the separator is unambiguous.
        payload_b64, sig_b64 = token.split('.', 1)
        pad_p = '=' * (-len(payload_b64) % 4)
        pad_s = '=' * (-len(sig_b64) % 4)
        email = base64.urlsafe_b64decode(payload_b64 + pad_p).decode('utf-8')
        sig = base64.urlsafe_b64decode(sig_b64 + pad_s)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        return None
    expected = _unsubscribe_mac(email)
    if not hmac.compare_digest(sig, expected):
        return None
    return email


# ---------------------------------------------------------------------------
# Resend audience (contacts) sync — opt-in preference mirroring
# ---------------------------------------------------------------------------
# All operations are best-effort: fail silently when RESEND_AUDIENCE_ID is
# unset (local dev) and log-and-swallow any API error so user-facing flows
# (verify, unsubscribe, delete-account) are never blocked by a Resend issue.

def _resend_audience_configured():
    if not os.environ.get('RESEND_API_KEY') or not os.environ.get('RESEND_AUDIENCE_ID'):
        return None
    resend.api_key = os.environ['RESEND_API_KEY']
    return os.environ['RESEND_AUDIENCE_ID']


def resend_contact_upsert(email, unsubscribed=False):
    """Add or update a contact in the Resend audience."""
    audience_id = _resend_audience_configured()
    if not audience_id:
        return
    try:
        resend.Contacts.create({
            'audience_id': audience_id,
            'email': email,
            'unsubscribed': bool(unsubscribed),
        })
    except Exception as e:
        # The SDK raises generically on any non-2xx response (including the 422
        # Resend returns for duplicate email), so we can't cheaply distinguish
        # "already exists" from other failures. Fall through to update — if the
        # underlying error is transient/auth, update will also fail and get
        # swallowed. update() accepts email as the contact identifier in the
        # SDK, so this is a valid upsert path.
        try:
            resend.Contacts.update({
                'audience_id': audience_id,
                'email': email,
                'unsubscribed': bool(unsubscribed),
            })
        except Exception as e2:
            if os.environ.get('DATABASE_URL'):
                app.logger.warning(
                    'resend_contact_upsert failed: %s: %s (email=%s)',
                    type(e2).__name__, e2, email,
                )


def resend_contact_delete(email):
    """Remove a contact from the Resend audience."""
    audience_id = _resend_audience_configured()
    if not audience_id:
        return
    try:
        resend.Contacts.remove(audience_id=audience_id, email=email)
    except Exception as e:
        if os.environ.get('DATABASE_URL'):
            app.logger.warning(
                'resend_contact_delete failed: %s: %s (email=%s)',
                type(e).__name__, e, email,
            )


MAIL_FROM = 'Baseline <hello@mybaselineapp.com>'


def _send_email(to_email, subject, html, text):
    """Send via Resend. Returns True on success, False otherwise.
    Fails silently if RESEND_API_KEY is not set (local dev)."""
    api_key = os.environ.get('RESEND_API_KEY')
    if not api_key:
        return False
    resend.api_key = api_key
    try:
        resend.Emails.send({
            'from': MAIL_FROM,
            'to': [to_email],
            'subject': subject,
            'html': html,
            'text': text,
        })
        return True
    except Exception as e:
        # Log in prod only (DATABASE_URL is Railway-only). Local dev stays silent.
        if os.environ.get('DATABASE_URL'):
            app.logger.error(
                '_send_email failed: %s: %s (to=%s, subject=%s)',
                type(e).__name__, e, to_email, subject,
            )
        return False


def send_verification_email(user_email, verify_url):
    """Send the verify-your-email link. Returns True if sent, False otherwise."""
    plain = f"""Welcome to Baseline!

Baseline helps you track what's working — and what's not — so your health decisions are based on evidence, not guesswork.

Verify your email to activate your account:

{verify_url}

This link expires in 24 hours. If you didn't create a Baseline account, you can ignore this message.

— The Baseline Team
"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#f8f8f8; font-family:'Inter','Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f8f8; padding:32px 16px;">
<tr><td align="center">
<table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff; border:1px solid #ececee; border-radius:10px; padding:36px 32px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
<tr><td>
  <div style="font-size:20px; font-weight:700; color:#1a1a2e; margin-bottom:24px;">
    <span style="color:#7c3aed;">B</span>aseline
  </div>
  <h1 style="font-size:22px; font-weight:700; color:#1a1a2e; margin:0 0 12px;">Verify your email</h1>
  <p style="font-size:14px; color:#666666; line-height:1.7; margin:0 0 20px;">Baseline helps you track what's working — and what's not — so your health decisions are based on evidence, not guesswork.</p>
  <div style="text-align:center; margin:24px 0;">
    <a href="{verify_url}" style="display:inline-block; background:#7c3aed; color:#ffffff; text-decoration:none; padding:12px 28px; border-radius:8px; font-size:14px; font-weight:600;">Verify email</a>
  </div>
  <p style="font-size:13px; color:#666666; line-height:1.7; margin:0 0 12px;">Or paste this link into your browser:</p>
  <p style="font-size:12px; color:#7c3aed; word-break:break-all; margin:0 0 20px;">{verify_url}</p>
  <p style="font-size:13px; color:#666666; line-height:1.7; margin:0;">This link expires in 24 hours. If you didn't create a Baseline account, you can ignore this email.</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    return _send_email(user_email, 'Verify your Baseline account', html, plain)


def send_welcome_email(user_email, user_name):
    """Send a welcome/orientation email to a newly registered user.
    Fails silently — never blocks registration. user_name is accepted for
    API stability but no longer rendered (greeting is now brand-focused)."""
    app_url = os.environ.get('APP_URL', 'https://baseline-health.up.railway.app')
    unsubscribe_url = f"{app_url.rstrip('/')}/unsubscribe/{generate_unsubscribe_token(user_email)}"

    plain = f"""Welcome to Baseline

You now have a structured way to track your health, run experiments, and find out what actually moves the needle.

WHAT TO EXPECT
You just completed onboarding — you've set up what you're tracking and your baseline scores. Now you're ready to start tracking.

TWO WAYS TO LOG
1. Daily Check-in (if you enabled AI logging): Describe your day in plain language and Baseline logs the details automatically.
2. Manual logging: Use the Episodes and Protocols pages to log structured data directly.

Both create the same records — use whichever feels easier.

YOUR FIRST STEPS
- Log your first episode or try the Daily Check-in
- Add your preventatives — medications, supplements, or routines you do consistently
- When you're ready, start your first experiment to test a protocol change

NEED HELP?
Visit the Help page for a full guide: {app_url}/help

Questions or feedback? Reply to this email or reach out at baselinehealthapp@gmail.com.

— The Baseline Team

---
You're receiving this because you have a Baseline account.
Unsubscribe from app updates: {unsubscribe_url}
{app_url}
"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#f8f8f8; font-family:'Inter','Helvetica Neue',Helvetica,Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f8f8; padding:32px 16px;">
<tr><td align="center">
<table width="480" cellpadding="0" cellspacing="0" style="background:#ffffff; border:1px solid #ececee; border-radius:10px; padding:36px 32px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
<tr><td>
  <div style="font-size:20px; font-weight:700; color:#1a1a2e; margin-bottom:24px;">
    <span style="color:#7c3aed;">B</span>aseline
  </div>
  <h1 style="font-size:22px; font-weight:700; color:#1a1a2e; margin:0 0 12px;">Welcome to Baseline</h1>
  <p style="font-size:14px; color:#666666; line-height:1.7; margin:0 0 20px;">You now have a structured way to track your health, run experiments, and find out what actually moves the needle.</p>

  <h2 style="font-size:15px; font-weight:600; color:#1a1a2e; margin:0 0 8px;">What to expect</h2>
  <p style="font-size:14px; color:#666666; line-height:1.7; margin:0 0 20px;">You've set up what you're tracking and your baseline scores during onboarding. Now you're ready to start tracking and building your evidence base.</p>

  <h2 style="font-size:15px; font-weight:600; color:#1a1a2e; margin:0 0 8px;">Two ways to log</h2>
  <p style="font-size:14px; color:#666666; line-height:1.7; margin:0 0 6px;"><strong style="color:#1a1a2e;">Daily Check-in</strong> — if you enabled AI logging, just describe your day in plain language and Baseline logs the details automatically.</p>
  <p style="font-size:14px; color:#666666; line-height:1.7; margin:0 0 20px;"><strong style="color:#1a1a2e;">Manual logging</strong> — use the Episodes and Protocols pages to enter structured data directly.</p>

  <h2 style="font-size:15px; font-weight:600; color:#1a1a2e; margin:0 0 8px;">Your first steps</h2>
  <ul style="font-size:14px; color:#666666; line-height:1.7; margin:0 0 24px; padding-left:20px;">
    <li style="margin-bottom:4px;">Log your first episode or try the Daily Check-in</li>
    <li style="margin-bottom:4px;">Add your preventatives — medications, supplements, or routines you do consistently</li>
    <li>When you're ready, start your first experiment to test a protocol change</li>
  </ul>

  <div style="text-align:center; margin:24px 0;">
    <a href="{app_url}" style="display:inline-block; background:#7c3aed; color:#ffffff; text-decoration:none; padding:12px 28px; border-radius:8px; font-size:14px; font-weight:600;">Open Baseline</a>
  </div>

  <h2 style="font-size:15px; font-weight:600; color:#1a1a2e; margin:0 0 8px;">Need help?</h2>
  <p style="font-size:14px; color:#666666; line-height:1.7; margin:0 0 20px;">Visit the <a href="{app_url}/help" style="color:#7c3aed; text-decoration:none;">Help page</a> for a full guide including a check-in tutorial. Questions or feedback? Reply to this email or reach out at <a href="mailto:baselinehealthapp@gmail.com" style="color:#7c3aed; text-decoration:none;">baselinehealthapp@gmail.com</a>.</p>

  <div style="border-top:1px solid #ececee; padding-top:16px; margin-top:8px; font-size:12px; color:#666666; line-height:1.7;">
    You're receiving this because you have a Baseline account.<br>
    <a href="{unsubscribe_url}" style="color:#7c3aed; text-decoration:none;">Unsubscribe</a>
    &nbsp;|&nbsp;
    <a href="{app_url}" style="color:#7c3aed; text-decoration:none;">mybaselineapp.com</a>
    &nbsp;|&nbsp;
    <a href="{app_url}/privacy" style="color:#7c3aed; text-decoration:none;">Privacy Policy</a>
  </div>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""

    _send_email(user_email, 'Welcome to Baseline', html, plain)


def run_migrations():
    """Add columns that may not exist in older DB files."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(db.engine)

    migrations = [
        ('protocols',             'available',                 'ALTER TABLE protocols ADD COLUMN available BOOLEAN NOT NULL DEFAULT TRUE'),
        ('users',                 'onboarding_complete',       'ALTER TABLE users ADD COLUMN onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE'),
        ('users',                 'baseline_episodes_per_month','ALTER TABLE users ADD COLUMN baseline_episodes_per_month INTEGER'),
        ('users',                 'ai_logging_enabled',        'ALTER TABLE users ADD COLUMN ai_logging_enabled BOOLEAN NOT NULL DEFAULT FALSE'),
        ('protocol_compliance',   'took',                      'ALTER TABLE protocol_compliance ADD COLUMN took BOOLEAN NOT NULL DEFAULT TRUE'),
        ('protocol_compliance',   'notes',                     'ALTER TABLE protocol_compliance ADD COLUMN notes TEXT'),
        ('users',                 'email',                     'ALTER TABLE users ADD COLUMN email VARCHAR(255)'),
        ('users',                 'password_hash',             'ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)'),
        ('users',                 'invite_code_used',          'ALTER TABLE users ADD COLUMN invite_code_used VARCHAR(100)'),
        ('users',                 'is_active',                 'ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT TRUE'),
        ('users',                 'has_seen_tour',             'ALTER TABLE users ADD COLUMN has_seen_tour BOOLEAN NOT NULL DEFAULT FALSE'),
        ('users',                 'verified_at',               'ALTER TABLE users ADD COLUMN verified_at TIMESTAMP'),
        ('users',                 'is_admin',                  'ALTER TABLE users ADD COLUMN is_admin BOOLEAN NOT NULL DEFAULT FALSE'),
        ('users',                 'email_updates_enabled',     'ALTER TABLE users ADD COLUMN email_updates_enabled BOOLEAN NOT NULL DEFAULT TRUE'),
    ]

    # Widen symptoms.name from varchar(100) to varchar(200) on PostgreSQL
    is_sqlite = str(db.engine.url).startswith('sqlite')
    if not is_sqlite:
        sym_cols = inspector.get_columns('symptoms')
        name_col = next((c for c in sym_cols if c['name'] == 'name'), None)
        if name_col and hasattr(name_col['type'], 'length') and name_col['type'].length and name_col['type'].length < 200:
            with db.engine.connect() as conn2:
                conn2.execute(text('ALTER TABLE symptoms ALTER COLUMN name TYPE VARCHAR(200)'))
                conn2.commit()

    with db.engine.connect() as conn:
        for table, column, ddl in migrations:
            existing = [c['name'] for c in inspector.get_columns(table)]
            if column not in existing:
                conn.execute(text(ddl))
                conn.commit()

        # Make episodes.peak_severity nullable if it isn't already.
        peak_col = next(
            (c for c in inspector.get_columns('episodes') if c['name'] == 'peak_severity'),
            None,
        )
        if peak_col and peak_col.get('nullable') is False:
            print("Migrating episodes.peak_severity to nullable...")
            is_sqlite = str(db.engine.url).startswith('sqlite')
            if is_sqlite:
                conn.execute(text('PRAGMA foreign_keys=OFF'))
                conn.execute(text('''
                    CREATE TABLE episodes_new (
                        id INTEGER NOT NULL PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        onset DATETIME NOT NULL,
                        peak_severity INTEGER,
                        duration_hours FLOAT,
                        functional_impairment VARCHAR(50),
                        rescue_protocol TEXT,
                        rescue_effectiveness INTEGER,
                        time_to_relief_hours FLOAT,
                        notes TEXT,
                        created_at DATETIME,
                        FOREIGN KEY(user_id) REFERENCES users(id)
                    )
                '''))
                conn.execute(text('INSERT INTO episodes_new SELECT * FROM episodes'))
                conn.execute(text('DROP TABLE episodes'))
                conn.execute(text('ALTER TABLE episodes_new RENAME TO episodes'))
                conn.execute(text('PRAGMA foreign_keys=ON'))
            else:
                conn.execute(text('ALTER TABLE episodes ALTER COLUMN peak_severity DROP NOT NULL'))
            conn.commit()
            print("Migration complete.")


def cleanup_stale_unverified_users():
    """Delete unverified accounts older than 48h so the email can be re-registered.
    Runs at startup; safe at our scale (5 users) without cron."""
    try:
        cutoff = datetime.utcnow() - timedelta(hours=48)
        stale = User.query.filter(
            User.verified_at.is_(None),
            User.is_active == False,
            User.created_at < cutoff,
        ).all()
        if not stale:
            return
        for u in stale:
            # Unverified accounts never completed onboarding, so no child data to clean.
            db.session.delete(u)
        db.session.commit()
    except Exception:
        db.session.rollback()


def backfill_verified_existing_users():
    """Mark pre-existing active users as verified so they aren't locked out."""
    users = User.query.filter(User.is_active == True, User.verified_at.is_(None)).all()
    if not users:
        return
    for u in users:
        u.verified_at = u.created_at or datetime.utcnow()
    db.session.commit()


def backfill_resend_contacts():
    """One-shot: upsert every active verified user into the Resend audience.
    Gated by BACKFILL_RESEND_CONTACTS=1 — set the env var on the next deploy,
    then unset it so subsequent restarts don't re-hit the Resend API.
    Idempotent: a re-run just patches each contact's unsubscribed state."""
    if os.environ.get('BACKFILL_RESEND_CONTACTS') != '1':
        return
    if not _resend_audience_configured():
        app.logger.warning(
            'BACKFILL_RESEND_CONTACTS=1 but RESEND_API_KEY/RESEND_AUDIENCE_ID not set — skipping'
        )
        return
    users = User.query.filter(
        User.is_active == True,
        User.verified_at.isnot(None),
        User.email.isnot(None),
    ).all()
    app.logger.info('Resend backfill: syncing %d users to audience', len(users))
    for u in users:
        resend_contact_upsert(u.email, unsubscribed=not u.email_updates_enabled)
    app.logger.info('Resend backfill: complete')


def ensure_admin_user():
    """Grant admin to the app owner on startup."""
    try:
        admin_email = os.environ.get('ADMIN_EMAIL', 'daniels.missy@gmail.com')
        owner = User.query.filter_by(email=admin_email).first()
        if owner and not owner.is_admin:
            owner.is_admin = True
            db.session.commit()
    except Exception:
        db.session.rollback()


def log_activity(event_type, detail=None, user_id=None):
    """Write an activity row using a standalone connection so it never
    interferes with the request session's transaction state."""
    try:
        with db.engine.connect() as conn:
            conn.execute(
                text(
                    'INSERT INTO user_activity (user_id, event_type, detail, created_at)'
                    ' VALUES (:uid, :etype, :detail, :ts)'
                ),
                {'uid': user_id, 'etype': event_type, 'detail': detail, 'ts': datetime.utcnow()},
            )
            conn.commit()
    except Exception as e:
        app.logger.warning('log_activity failed: %s: %s', type(e).__name__, e)


def run_data_migrations():
    """Migrate existing episode peak_severity values to SymptomScore records."""
    for user in User.query.all():
        episodes_needing_migration = [
            ep for ep in Episode.query.filter_by(user_id=user.id).all()
            if ep.peak_severity is not None and not ep.symptom_scores
        ]

        if not episodes_needing_migration:
            continue

        print(f"Migrating {len(episodes_needing_migration)} episode(s) to symptom scores for user {user.id}...")

        primary = Symptom.query.filter_by(user_id=user.id, name='Primary Symptom').first()
        if not primary:
            primary = Symptom(user_id=user.id, name='Primary Symptom', is_active=True)
            db.session.add(primary)
            db.session.flush()

        for ep in episodes_needing_migration:
            db.session.add(SymptomScore(episode_id=ep.id, symptom_id=primary.id, score=ep.peak_severity))

        # Existing users with episodes skip the onboarding wizard
        user.onboarding_complete = True
        db.session.commit()
        print("Migration complete.")


def migrate_episode_interventions():
    """Migrate flat rescue_protocol columns to EpisodeIntervention records."""
    episodes_needing_migration = (
        Episode.query
        .filter(Episode.rescue_protocol.isnot(None), Episode.rescue_protocol != '')
        .all()
    )
    # Only process episodes that don't already have EpisodeIntervention records
    episodes_needing_migration = [
        ep for ep in episodes_needing_migration if not ep.interventions
    ]
    if not episodes_needing_migration:
        return

    print(f"Migrating {len(episodes_needing_migration)} episode(s) to EpisodeIntervention records...")

    for ep in episodes_needing_migration:
        names = [n.strip() for n in ep.rescue_protocol.split(',') if n.strip()]
        for idx, name in enumerate(names):
            # Match to existing Protocol (type='rescue') for same user, case-insensitive
            protocol = Protocol.query.filter(
                Protocol.user_id == ep.user_id,
                Protocol.type == 'rescue',
                db.func.lower(Protocol.name) == name.lower(),
            ).first()
            if not protocol:
                protocol = Protocol(
                    user_id=ep.user_id,
                    name=name,
                    type='rescue',
                    available=True,
                )
                db.session.add(protocol)
                db.session.flush()

            ei = EpisodeIntervention(
                episode_id=ep.id,
                protocol_id=protocol.id,
                # Apply effectiveness/relief to first intervention only
                effectiveness=ep.rescue_effectiveness if idx == 0 else None,
                time_to_relief_hours=ep.time_to_relief_hours if idx == 0 else None,
            )
            db.session.add(ei)

    db.session.commit()
    print("EpisodeIntervention migration complete.")


def _safe_float(val, default=None, min_val=None):
    """Safely convert a form value to float, returning default on failure."""
    if not val:
        return default
    try:
        result = float(val)
        if min_val is not None and result < min_val:
            result = min_val
        return result
    except (ValueError, TypeError):
        return default


def get_user():
    """Return the logged-in user from the session, or None."""
    user_id = session.get('user_id')
    if user_id:
        return User.query.get(user_id)
    return None


def get_active_experiment(user_id):
    """Return the most recent active experiment for the user, or None."""
    return Experiment.query.filter_by(user_id=user_id, status='active').order_by(Experiment.start_date.desc()).first()


# ---------------------------------------------------------------------------
# Authentication gate + onboarding gate
# ---------------------------------------------------------------------------

PUBLIC_ENDPOINTS = {'login', 'register', 'static', 'dev_bootstrap', 'serve_sw', 'offline', 'privacy', 'verify_email', 'resend_verification', 'verify_sent', 'unsubscribe'}

@app.context_processor
def inject_onboarding_state():
    user = get_user()
    return {
        'onboarding_in_progress': user is not None and not user.onboarding_complete,
        'current_user': user,
    }


SKIP_TRACKING = {'static', 'serve_sw', 'admin_analytics', 'admin_users', 'offline'}


@app.before_request
def require_auth():
    if request.endpoint in (None,) or request.endpoint in PUBLIC_ENDPOINTS:
        return
    user = get_user()
    if not user:
        return redirect(url_for('login'))
    if not user.is_active:
        session.clear()
        flash('Your account has been deactivated.', 'error')
        return redirect(url_for('login'))
    # Onboarding gate — let auth and onboarding endpoints through
    if request.endpoint.startswith('onboarding_'):
        return
    if not user.onboarding_complete:
        return redirect(url_for('onboarding_step1'))
    # Track page views for analytics (GET only, lightweight, never blocks)
    if request.method == 'GET' and request.endpoint not in SKIP_TRACKING:
        log_activity('page_view', detail=request.endpoint, user_id=user.id)


# ---------------------------------------------------------------------------
# PWA routes
# ---------------------------------------------------------------------------

@app.route('/sw.js')
def serve_sw():
    return app.send_static_file('sw.js'), 200, {'Content-Type': 'application/javascript', 'Service-Worker-Allowed': '/'}

@app.route('/offline')
def offline():
    return render_template('offline.html')


# ---------------------------------------------------------------------------
# Authentication routes
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit('20 per hour', methods=['POST'])
def login():
    if get_user():
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = 'remember' in request.form

        user = User.query.filter_by(email=email).first()

        # Surface unverified state before doing a password check, so we don't
        # leak password validity via the unverified-vs-generic-error response.
        if user and user.verified_at is None and not user.is_active:
            return render_template('login.html', email=email, unverified=True)

        if user and user.password_hash and bcrypt.check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('Your account has been deactivated.', 'error')
                return render_template('login.html')
            session.clear()
            session['user_id'] = user.id
            log_activity('login', user_id=user.id)
            if remember:
                session.permanent = True
            else:
                session.permanent = False
            if not user.onboarding_complete:
                return redirect(url_for('onboarding_step1'))
            return redirect(url_for('index'))
        else:
            flash('Invalid email or password.', 'error')
            return render_template('login.html', email=email)

    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
def register():
    if get_user():
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        privacy_ack = 'privacy_ack' in request.form

        errors = []
        if not email or '@' not in email or '.' not in email.split('@')[-1]:
            errors.append('A valid email is required.')
        elif is_disposable_email(email):
            errors.append('Please use a non-disposable email address.')
        elif User.query.filter_by(email=email).first():
            errors.append('An account with that email already exists.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        elif not re.search(r'[A-Z]', password):
            errors.append('Password must contain at least one uppercase letter.')
        elif not re.search(r'[0-9]', password):
            errors.append('Password must contain at least one number.')
        if password != confirm:
            errors.append('Passwords do not match.')
        if not privacy_ack:
            errors.append('You must acknowledge the Privacy Policy to create an account.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register.html', email=email, privacy_ack=privacy_ack)

        pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(
            name='Friend',
            email=email,
            password_hash=pw_hash,
            is_active=False,
            verified_at=None,
        )
        try:
            db.session.add(user)
            db.session.commit()
            log_activity('signup', user_id=user.id)
        except Exception:
            db.session.rollback()
            flash('An account with that email already exists.', 'error')
            return render_template('register.html', email=email, privacy_ack=privacy_ack)

        token = generate_verify_token(email)
        app_url = os.environ.get('APP_URL', 'https://baseline-health.up.railway.app')
        verify_url = f"{app_url.rstrip('/')}/verify/{token}"
        sent = send_verification_email(email, verify_url)
        if not os.environ.get('DATABASE_URL'):  # local SQLite only — never logs in prod
            print(f'[DEV] Verification URL for {email}: {verify_url}')

        session['pending_verify_email'] = email
        if not sent and os.environ.get('MAIL_USERNAME'):
            flash('We had trouble sending your verification email. Try the resend link, or contact baselinehealthapp@gmail.com.', 'warning')
        return redirect(url_for('verify_sent'))

    return render_template('register.html')


@app.route('/verify/sent')
def verify_sent():
    email = session.pop('pending_verify_email', '')
    return render_template('verify_sent.html', email=email)


@app.route('/verify/<token>')
def verify_email(token):
    email, err = load_verify_token(token)
    if err == 'expired':
        return render_template('verify_result.html', status='expired', email=None), 400
    if err or not email:
        return render_template('verify_result.html', status='invalid', email=None), 400

    token_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
    if UsedVerifyToken.query.filter_by(token_hash=token_hash).first():
        # Token already consumed — treat as invalid so attackers can't distinguish replay.
        return render_template('verify_result.html', status='invalid', email=None), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return render_template('verify_result.html', status='invalid', email=None), 400

    if user.verified_at is not None:
        flash('This account is already verified. Please log in.', 'success')
        return redirect(url_for('login'))

    try:
        user.verified_at = datetime.utcnow()
        user.is_active = True
        db.session.add(UsedVerifyToken(token_hash=token_hash))
        db.session.commit()
    except Exception:
        db.session.rollback()
        return render_template('verify_result.html', status='error', email=None), 500

    send_welcome_email(user.email, user.name)
    resend_contact_upsert(user.email, unsubscribed=not user.email_updates_enabled)

    session.clear()
    session['user_id'] = user.id
    if not user.onboarding_complete:
        return redirect(url_for('onboarding_step1'))
    return redirect(url_for('index'))


@app.route('/resend-verification', methods=['GET', 'POST'])
@limiter.limit('5 per hour', methods=['POST'])
def resend_verification():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        # Enumeration-safe: always redirect to the same confirmation page
        if email and '@' in email:
            user = User.query.filter_by(email=email).first()
            if user and user.verified_at is None:
                token = generate_verify_token(email)
                app_url = os.environ.get('APP_URL', 'https://baseline-health.up.railway.app')
                verify_url = f"{app_url.rstrip('/')}/verify/{token}"
                send_verification_email(email, verify_url)
                if not os.environ.get('DATABASE_URL'):
                    print(f'[DEV] Verification URL for {email}: {verify_url}')
        session['pending_verify_email'] = email
        return redirect(url_for('verify_sent'))

    return render_template('resend_verification.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


@app.route('/unsubscribe/<token>')
@limiter.limit('30 per hour')
def unsubscribe(token):
    """One-click unsubscribe from non-transactional email. Publicly accessible."""
    email = load_unsubscribe_token(token)
    if not email:
        return render_template('unsubscribe.html', status='invalid'), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        # Generic error — don't reveal whether the address is known.
        return render_template('unsubscribe.html', status='invalid'), 400

    if user.email_updates_enabled:
        try:
            user.email_updates_enabled = False
            db.session.commit()
        except Exception:
            db.session.rollback()
            return render_template('unsubscribe.html', status='error'), 500

    resend_contact_upsert(user.email, unsubscribed=True)
    return render_template('unsubscribe.html', status='success')


# ---------------------------------------------------------------------------
# Onboarding wizard
# ---------------------------------------------------------------------------

@app.route('/onboarding/step1', methods=['GET', 'POST'])
def onboarding_step1():
    user = get_user()

    if request.method == 'POST':
        # Collect submitted names first
        new_names = []
        for i in range(1, 4):
            name = request.form.get(f'name_{i}', '').strip()[:200]
            if name:
                desc = request.form.get(f'description_{i}', '').strip()[:500] or None
                new_names.append((name, desc))

        if not new_names:
            flash('Add at least one thing to track.', 'error')
            symptoms = Symptom.query.filter_by(user_id=user.id).all()
            return render_template('onboarding_step1.html', symptoms=symptoms)

        # Remove stale onboarding symptoms not yet tied to any episode
        for sym in Symptom.query.filter_by(user_id=user.id).all():
            if not SymptomScore.query.filter_by(symptom_id=sym.id).first():
                db.session.delete(sym)
        db.session.flush()

        for name, desc in new_names:
            db.session.add(Symptom(user_id=user.id, name=name, description=desc))

        db.session.commit()
        return redirect(url_for('onboarding_step2'))

    symptoms = Symptom.query.filter_by(user_id=user.id).all()
    return render_template('onboarding_step1.html', symptoms=symptoms)


@app.route('/onboarding/step2', methods=['GET', 'POST'])
def onboarding_step2():
    user = get_user()
    symptoms = Symptom.query.filter_by(user_id=user.id).all()

    if not symptoms:
        return redirect(url_for('onboarding_step1'))

    if request.method == 'POST':
        baseline_str = request.form.get('baseline_episodes_per_month', '').strip()
        try:
            user.baseline_episodes_per_month = int(baseline_str) if baseline_str else None
        except ValueError:
            user.baseline_episodes_per_month = None

        for symptom in symptoms:
            score_str = request.form.get(f'score_{symptom.id}', '').strip()
            if score_str:
                try:
                    symptom.baseline_score = max(1, min(10, int(score_str)))
                except ValueError:
                    pass

        db.session.commit()
        return redirect(url_for('onboarding_step3'))

    return render_template('onboarding_step2.html', symptoms=symptoms)


@app.route('/onboarding/step3', methods=['GET', 'POST'])
def onboarding_step3():
    user = get_user()

    if request.method == 'POST':
        choice = request.form.get('choice')
        user.ai_logging_enabled = (choice == 'enable')
        user.onboarding_complete = True
        db.session.commit()
        flash('Welcome to Baseline!', 'success')
        return redirect(url_for('index'))

    return render_template('onboarding_step3.html')


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route('/help')
def help_page():
    return render_template('help.html')


@app.route('/tour/complete', methods=['POST'])
def tour_complete():
    user = get_user()
    if user:
        user.has_seen_tour = True
        db.session.commit()
    return {'ok': True}


@app.route('/tour/restart')
def tour_restart():
    user = get_user()
    if user:
        user.has_seen_tour = False
        db.session.commit()
    return redirect(url_for('index'))


@app.route('/privacy')
def privacy():
    return render_template('privacy.html')


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user = get_user()

    if request.method == 'POST':
        user.ai_logging_enabled = ('ai_logging' in request.form)
        db.session.commit()
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    return render_template('settings.html', user=user)


@app.route('/settings/email-preferences', methods=['POST'])
def email_preferences():
    user = get_user()
    if not user:
        return redirect(url_for('login'))
    enabled = ('email_updates' in request.form)
    try:
        user.email_updates_enabled = enabled
        db.session.commit()
    except Exception:
        db.session.rollback()
        flash("Couldn't save your preferences. Please try again.", 'error')
        return redirect(url_for('settings'))
    # Mirror preference to Resend audience so campaigns honor it.
    if user.email:
        resend_contact_upsert(user.email, unsubscribed=not enabled)
    flash('Email preferences saved.', 'success')
    return redirect(url_for('settings'))


@app.route('/settings/email', methods=['POST'])
def change_email():
    user = get_user()
    new_email = request.form.get('new_email', '').strip().lower()
    password = request.form.get('current_password_email', '')

    if not new_email or '@' not in new_email:
        flash('Please enter a valid email address.', 'error')
    elif not bcrypt.check_password_hash(user.password_hash, password):
        flash('Current password is incorrect.', 'error')
    elif User.query.filter(User.email == new_email, User.id != user.id).first():
        flash('That email is already in use.', 'error')
    else:
        old_email = user.email
        user.email = new_email
        db.session.commit()
        # Keep Resend audience in sync: remove the old contact, add the new
        # one carrying the user's current email-updates preference.
        if old_email:
            resend_contact_delete(old_email)
        resend_contact_upsert(new_email, unsubscribed=not user.email_updates_enabled)
        flash('Email updated.', 'success')

    return redirect(url_for('settings'))


@app.route('/settings/password', methods=['POST'])
def change_password():
    user = get_user()
    current = request.form.get('current_password', '')
    new_pw = request.form.get('new_password', '')
    confirm = request.form.get('confirm_new_password', '')

    if not bcrypt.check_password_hash(user.password_hash, current):
        flash('Current password is incorrect.', 'error')
    elif len(new_pw) < 8:
        flash('New password must be at least 8 characters.', 'error')
    elif not re.search(r'[A-Z]', new_pw):
        flash('New password must contain at least one uppercase letter.', 'error')
    elif not re.search(r'[0-9]', new_pw):
        flash('New password must contain at least one number.', 'error')
    elif new_pw != confirm:
        flash('New passwords do not match.', 'error')
    else:
        new_hash = bcrypt.generate_password_hash(new_pw).decode('utf-8')
        User.query.filter_by(id=user.id).update({'password_hash': new_hash})
        db.session.commit()
        flash('Password updated.', 'success')

    return redirect(url_for('settings'))


@app.route('/settings/delete-account', methods=['POST'])
def delete_account():
    user = get_user()
    if not user:
        return redirect(url_for('login'))

    confirmation = request.form.get('confirmation', '').strip()
    if confirmation != 'DELETE':
        flash('Account deletion cancelled — confirmation text did not match.', 'error')
        return redirect(url_for('settings'))

    user_id = user.id
    user_email = user.email

    # Remove from Resend audience before DB delete so we still have the email.
    if user_email:
        resend_contact_delete(user_email)

    # Delete in FK-safe order
    episode_ids = db.session.query(Episode.id).filter_by(user_id=user_id)
    EpisodeIntervention.query.filter(
        EpisodeIntervention.episode_id.in_(episode_ids)
    ).delete(synchronize_session=False)
    SymptomScore.query.filter(
        SymptomScore.episode_id.in_(episode_ids)
    ).delete(synchronize_session=False)
    CheckIn.query.filter_by(user_id=user_id).delete()
    Episode.query.filter_by(user_id=user_id).delete()
    ProtocolCompliance.query.filter_by(user_id=user_id).delete()
    ProtocolEvent.query.filter_by(user_id=user_id).delete()
    Experiment.query.filter_by(user_id=user_id).delete()
    Protocol.query.filter_by(user_id=user_id).delete()
    Symptom.query.filter_by(user_id=user_id).delete()
    UserActivity.query.filter_by(user_id=user_id).delete()
    InviteCode.query.filter_by(used_by_user_id=user_id).update({'used_by_user_id': None, 'used_at': None})
    User.query.filter_by(id=user_id).delete()
    db.session.commit()

    session.clear()
    flash('Your account has been deleted.', 'success')
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Admin Analytics
# ---------------------------------------------------------------------------

FEATURE_LABELS = {
    'index': 'Dashboard', 'checkin': 'Check-in', 'episodes': 'Episodes',
    'episode_new': 'New Episode', 'episode_edit': 'Edit Episode',
    'protocols': 'Protocols', 'protocol_new': 'New Protocol',
    'protocol_detail': 'Protocol Detail', 'edit_protocol': 'Edit Protocol',
    'experiments': 'Experiments', 'experiment_new': 'New Experiment',
    'edit_experiment': 'Edit Experiment', 'assess_experiment': 'Assess Experiment',
    'symptoms': 'What I Track', 'symptom_new': 'New Symptom', 'edit_symptom': 'Edit Symptom',
    'settings': 'Settings', 'help_page': 'Help', 'delete_account': 'Delete Account',
    'new_rescue_option': 'New Intervention', 'edit_rescue_option': 'Edit Intervention',
    'experiment_offer': 'Experiment Offer',
}


@app.route('/admin/analytics')
def admin_analytics():
    from sqlalchemy import func, cast, Date

    user = get_user()
    if not user or not user.is_admin:
        return redirect(url_for('index'))

    is_sqlite = str(db.engine.url).startswith('sqlite')

    def date_group(column):
        if is_sqlite:
            return func.strftime('%Y-%m-%d', column)
        return cast(column, Date)

    def week_group(column):
        if is_sqlite:
            return func.strftime('%Y-W%W', column)
        return func.to_char(column, 'IYYY-"W"IW')

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    twelve_weeks_ago = datetime.utcnow() - timedelta(weeks=12)

    day_expr = date_group(UserActivity.created_at)
    week_expr = week_group(UserActivity.created_at)

    # Total users
    total_users = User.query.filter(User.is_active == True, User.verified_at.isnot(None)).count()

    # Signups (last 30 days)
    signups = db.session.query(
        day_expr.label('day'),
        func.count().label('count')
    ).filter(
        UserActivity.event_type == 'signup',
        UserActivity.created_at >= thirty_days_ago
    ).group_by(day_expr).order_by(day_expr).all()

    # Logins (last 30 days)
    logins = db.session.query(
        day_expr.label('day'),
        func.count().label('count')
    ).filter(
        UserActivity.event_type == 'login',
        UserActivity.created_at >= thirty_days_ago
    ).group_by(day_expr).order_by(day_expr).all()

    # DAU (last 30 days)
    dau = db.session.query(
        day_expr.label('day'),
        func.count(func.distinct(UserActivity.user_id)).label('count')
    ).filter(
        UserActivity.event_type == 'page_view',
        UserActivity.created_at >= thirty_days_ago
    ).group_by(day_expr).order_by(day_expr).all()

    # WAU (last 12 weeks)
    wau = db.session.query(
        week_expr.label('week'),
        func.count(func.distinct(UserActivity.user_id)).label('count')
    ).filter(
        UserActivity.event_type == 'page_view',
        UserActivity.created_at >= twelve_weeks_ago
    ).group_by(week_expr).order_by(week_expr).all()

    # Feature usage (last 30 days)
    feature_rows = db.session.query(
        UserActivity.detail.label('feature'),
        func.count().label('count')
    ).filter(
        UserActivity.event_type == 'page_view',
        UserActivity.created_at >= thirty_days_ago,
        UserActivity.detail.isnot(None)
    ).group_by(UserActivity.detail).order_by(func.count().desc()).all()

    total_views = sum(r.count for r in feature_rows) or 1
    feature_usage = [
        {
            'name': FEATURE_LABELS.get(r.feature, r.feature),
            'endpoint': r.feature,
            'count': r.count,
            'pct': round(r.count / total_views * 100, 1),
        }
        for r in feature_rows
    ]

    # Retention — per-user activity summary
    retention = db.session.query(
        User.name,
        User.email,
        User.created_at.label('joined'),
        func.min(UserActivity.created_at).label('first_seen'),
        func.max(UserActivity.created_at).label('last_seen'),
        func.count(func.distinct(date_group(UserActivity.created_at))).label('active_days')
    ).join(UserActivity, User.id == UserActivity.user_id).filter(
        UserActivity.event_type == 'page_view'
    ).group_by(User.id, User.name, User.email, User.created_at).all()

    # Summary cards
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    signups_this_week = UserActivity.query.filter(
        UserActivity.event_type == 'signup',
        UserActivity.created_at >= week_start
    ).count()
    logins_today = UserActivity.query.filter(
        UserActivity.event_type == 'login',
        UserActivity.created_at >= today_start
    ).count()
    dau_today = db.session.query(
        func.count(func.distinct(UserActivity.user_id))
    ).filter(
        UserActivity.event_type == 'page_view',
        UserActivity.created_at >= today_start
    ).scalar() or 0

    return render_template('admin_analytics.html',
        total_users=total_users,
        signups_this_week=signups_this_week,
        logins_today=logins_today,
        dau_today=dau_today,
        signups=signups,
        logins=logins,
        dau=dau,
        wau=wau,
        feature_usage=feature_usage,
        retention=retention,
        now=datetime.utcnow(),
    )


@app.route('/admin/users')
def admin_users():
    """Read-only admin table of all users. Guarded by is_admin."""
    from sqlalchemy import func

    user = get_user()
    if not user or not user.is_admin:
        app.logger.warning(
            'Unauthorized /admin/users access by user_id=%s',
            user.id if user else None,
        )
        return redirect(url_for('index'))

    # Last-login per user: latest UserActivity row of type 'login'
    last_login_rows = (
        db.session.query(
            UserActivity.user_id,
            func.max(UserActivity.created_at).label('last_login'),
        )
        .filter(UserActivity.event_type == 'login')
        .group_by(UserActivity.user_id)
        .all()
    )
    last_login_by_user = {uid: ts for uid, ts in last_login_rows}

    # Episode count per user
    episode_count_rows = (
        db.session.query(Episode.user_id, func.count(Episode.id))
        .group_by(Episode.user_id)
        .all()
    )
    episode_count_by_user = {uid: n for uid, n in episode_count_rows}

    users = User.query.order_by(User.created_at.desc()).all()
    rows = [{
        'email': u.email or '—',
        'verified': u.verified_at is not None,
        'joined': u.created_at,
        'last_login': last_login_by_user.get(u.id),
        'email_updates_enabled': u.email_updates_enabled,
        'episode_count': episode_count_by_user.get(u.id, 0),
    } for u in users]

    return render_template('admin_users.html', rows=rows, now=datetime.utcnow())


# ---------------------------------------------------------------------------
# AI Check-in
# ---------------------------------------------------------------------------

def build_system_prompt(user, client_time=None):
    local_dt = None
    if client_time:
        try:
            local_dt = datetime.strptime(client_time, '%Y-%m-%dT%H:%M')
        except ValueError:
            pass
    if local_dt is None:
        local_dt = datetime.utcnow()
    today = local_dt.strftime('%Y-%m-%d')
    current_time = local_dt.strftime('%H:%M')
    symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative', status='active').all()
    rescues = Protocol.query.filter(Protocol.user_id == user.id, Protocol.type == 'rescue', Protocol.status != 'removed').all()
    active_exp = get_active_experiment(user.id)

    symptom_list = '\n'.join(
        f'  - id={s.id}, name="{s.name}"' + (f', description="{s.description}"' if s.description else '')
        for s in symptoms
    ) or '  (none)'

    preventative_list = '\n'.join(
        f'  - id={p.id}, name="{p.name}", dose="{p.dose_frequency or "not specified"}"'
        for p in preventatives
    ) or '  (none)'

    rescue_list = '\n'.join(
        f'  - id={r.id}, name="{r.name}"'
        for r in rescues
    ) or '  (none)'

    exp_text = ''
    if active_exp:
        exp_text = f'\nActive experiment: "{active_exp.name}" (started {active_exp.start_date}).'

    return f"""You are a warm, empathetic health companion helping someone track their migraines and health.
Today is {today}, current local time is approximately {current_time}.{exp_text}

The user tracks these symptoms (use their exact IDs in your JSON):
{symptom_list}

Active preventative protocols:
{preventative_list}

Rescue options (interventions):
{rescue_list}

The user will describe how they are feeling or what happened today. Parse their message and respond with ONLY valid JSON (no markdown, no code fences).

For the episode onset field: if the user mentions a specific time (e.g. "around 2pm", "this morning at 8"), infer the full datetime. Otherwise use the current date and time ({today}T{current_time}) as the onset. Never return null for onset when had_episode is true.

Always match intervention names to the user's existing interventions listed above. Use your knowledge of brand/generic medication names, common misspellings, and colloquial terms to map to the correct one (e.g., "muscle relaxer" → the user's muscle relaxant if they have one, "ondansetren" → Ondansetron). If the user mentions multiple interventions, create a separate entry for each. If an intervention is genuinely new (not in the list), use the correct pharmacological or common name.

Use this exact schema:

{{
  "had_episode": true or false,
  "episode_data": {{
    "onset": "YYYY-MM-DDTHH:MM or null",
    "symptom_scores": {{"<symptom_id_as_string>": <1-10 integer>}},
    "functional_impairment": "working_normally or working_reduced or cannot_work or completely_incapacitated or null",
    "interventions": [
      {{
        "name": "<exact intervention name from the list above>",
        "effectiveness": <1-10 integer or null>,
        "time_to_relief_hours": <float or null>
      }}
    ],
    "notes": "<string or null>"
  }},
  "protocol_compliance": [<list of preventative protocol IDs taken today>],
  "general_notes": "<string or null>",
  "suggested_response": "<warm 1-3 sentence reply to the user>"
}}

If no episode occurred, set had_episode to false and episode_data fields to null/empty.
If the user describes experiencing a tracked symptom but does not give a severity score, still set had_episode to true and omit the score — but in suggested_response warmly ask them to rate it on a scale of 1–10 so it can be logged accurately.
If no interventions were used, set interventions to an empty array [].
Always populate suggested_response with a warm, brief reply."""


def parse_checkin(user, message_text, client_time=None):
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return None, 'ANTHROPIC_API_KEY is not set.'

    from anthropic import Anthropic, AuthenticationError
    client = Anthropic(api_key=api_key)

    cutoff = datetime.utcnow() - timedelta(days=7)
    history = CheckIn.query.filter(
        CheckIn.user_id == user.id,
        CheckIn.created_at >= cutoff
    ).order_by(CheckIn.created_at.asc()).all()

    messages = [{'role': ci.role, 'content': ci.content} for ci in history]
    messages.append({'role': 'user', 'content': message_text})

    try:
        response = client.messages.create(
            model='claude-sonnet-4-6',
            max_tokens=1024,
            system=build_system_prompt(user, client_time=client_time),
            messages=messages,
        )
    except AuthenticationError:
        return None, 'API authentication failed. Check your ANTHROPIC_API_KEY in .env and restart the server.'

    raw = response.content[0].text

    match = re.search(r'\{[\s\S]*\}', raw)
    if not match:
        return None, raw

    try:
        parsed = json.loads(match.group(0))
        return parsed, raw
    except json.JSONDecodeError:
        return None, raw


@app.route('/checkin', methods=['GET', 'POST'])
def checkin():
    user = get_user()

    if request.method == 'POST':
        if not user.ai_logging_enabled:
            flash('AI logging is disabled. Enable it in Settings.', 'error')
            return redirect(url_for('checkin'))

        message_text = request.form.get('message', '').strip()
        if not message_text:
            return redirect(url_for('checkin'))

        client_time = request.form.get('client_time', '').strip() or None

        # Call the API before opening any write transaction to avoid holding a SQLite lock
        parsed, raw = parse_checkin(user, message_text, client_time=client_time)

        episode_id = None

        if parsed is None:
            # Parse failure — save raw as assistant message
            assistant_content = raw if raw else 'Sorry, I had trouble understanding that. Could you rephrase?'
        else:
            if parsed.get('had_episode'):
                ep_data = parsed.get('episode_data', {})

                onset_str = ep_data.get('onset')
                onset = None
                if onset_str:
                    try:
                        onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M')
                    except ValueError:
                        pass
                if onset is None and client_time:
                    try:
                        onset = datetime.strptime(client_time, '%Y-%m-%dT%H:%M')
                    except ValueError:
                        pass
                if onset is None:
                    onset = datetime.utcnow()

                episode = Episode(
                    user_id=user.id,
                    onset=onset,
                    peak_severity=None,
                    functional_impairment=ep_data.get('functional_impairment') if ep_data.get('functional_impairment') in ('working_normally', 'working_reduced', 'cannot_work', 'completely_incapacitated') else None,
                    notes=ep_data.get('notes') or None,
                )
                db.session.add(episode)
                db.session.flush()
                episode_id = episode.id

                user_symptom_ids = {s.id for s in Symptom.query.filter_by(user_id=user.id).all()}
                for sym_id_str, score in (ep_data.get('symptom_scores') or {}).items():
                    try:
                        sid = int(sym_id_str)
                        sc = int(score)
                    except (ValueError, TypeError):
                        continue
                    if sid not in user_symptom_ids or not (1 <= sc <= 10):
                        continue
                    db.session.add(SymptomScore(
                        episode_id=episode.id,
                        symptom_id=sid,
                        score=sc,
                    ))

                # Handle interventions — new array format
                interventions_list = ep_data.get('interventions') or []
                # Backward compat: old single-string format
                if not interventions_list and ep_data.get('rescue_option_used'):
                    interventions_list = [{
                        'name': ep_data['rescue_option_used'],
                        'effectiveness': ep_data.get('rescue_effectiveness'),
                        'time_to_relief_hours': ep_data.get('time_to_relief_hours'),
                    }]

                new_intervention_names = []
                for intervention in interventions_list:
                    iname = (intervention.get('name') or '').strip()
                    if not iname:
                        continue
                    # Match to existing Protocol (type='rescue') for user
                    protocol = Protocol.query.filter(
                        Protocol.user_id == user.id,
                        Protocol.type == 'rescue',
                        db.func.lower(Protocol.name) == iname.lower(),
                    ).first()
                    if not protocol:
                        protocol = Protocol(
                            user_id=user.id,
                            name=iname,
                            type='rescue',
                            available=True,
                        )
                        db.session.add(protocol)
                        db.session.flush()
                        new_intervention_names.append(iname)

                    eff = intervention.get('effectiveness')
                    relief = intervention.get('time_to_relief_hours')
                    try:
                        eff_val = int(eff) if eff is not None else None
                        relief_val = float(relief) if relief is not None else None
                    except (ValueError, TypeError):
                        eff_val = None
                        relief_val = None
                    if eff_val is not None and not (1 <= eff_val <= 10):
                        eff_val = None
                    if relief_val is not None and relief_val < 0:
                        relief_val = None
                    db.session.add(EpisodeIntervention(
                        episode_id=episode.id,
                        protocol_id=protocol.id,
                        effectiveness=eff_val,
                        time_to_relief_hours=relief_val,
                    ))

                if new_intervention_names:
                    suggestion = parsed.get('suggested_response', '')
                    for n in new_intervention_names:
                        suggestion += f" I've added {n} as a new intervention."
                    parsed['suggested_response'] = suggestion

            # Log protocol compliance (deduplicate)
            today_date = date.today()
            user_protocol_ids = {p.id for p in Protocol.query.filter_by(user_id=user.id).all()}
            for proto_id in (parsed.get('protocol_compliance') or []):
                try:
                    pid = int(proto_id)
                except (ValueError, TypeError):
                    continue
                if pid not in user_protocol_ids:
                    continue
                exists = ProtocolCompliance.query.filter_by(
                    user_id=user.id,
                    protocol_id=pid,
                    date=today_date,
                ).first()
                if not exists:
                    db.session.add(ProtocolCompliance(
                        user_id=user.id,
                        protocol_id=pid,
                        date=today_date,
                    ))

            assistant_content = parsed.get('suggested_response') or 'Got it, thanks for the update!'

        # Write user message and assistant reply in a single transaction
        db.session.add(CheckIn(user_id=user.id, role='user', content=message_text))
        db.session.add(CheckIn(
            user_id=user.id,
            role='assistant',
            content=assistant_content,
            episode_id=episode_id,
        ))
        db.session.commit()
        return redirect(url_for('checkin'))

    # GET
    cutoff = datetime.utcnow() - timedelta(days=7)
    history = CheckIn.query.filter(
        CheckIn.user_id == user.id,
        CheckIn.created_at >= cutoff,
    ).order_by(CheckIn.created_at.asc()).all()
    api_key_set = bool(os.environ.get('ANTHROPIC_API_KEY'))
    return render_template('checkin.html', user=user, history=history, api_key_set=api_key_set)


# ---------------------------------------------------------------------------
# Symptoms management
# ---------------------------------------------------------------------------

@app.route('/symptoms')
def symptoms():
    user = get_user()
    active = Symptom.query.filter_by(user_id=user.id, is_active=True).order_by(Symptom.created_at).all()
    inactive = Symptom.query.filter_by(user_id=user.id, is_active=False).order_by(Symptom.created_at).all()
    return render_template('symptoms.html', active=active, inactive=inactive)


@app.route('/symptoms/new', methods=['GET', 'POST'])
def new_symptom():
    user = get_user()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip() or None
        if not name:
            return redirect(url_for('symptoms'))
        if len(name) > 200:
            flash('Name must be 200 characters or fewer.', 'error')
            return render_template('new_symptom.html')
        if description and len(description) > 500:
            flash('Description must be 500 characters or fewer.', 'error')
            return render_template('new_symptom.html')
        # Case-insensitive duplicate check
        existing = Symptom.query.filter(Symptom.user_id == user.id,
                                        db.func.lower(Symptom.name) == name.lower()).first()
        if existing:
            flash(f'"{existing.name}" already exists.', 'error')
            return render_template('new_symptom.html')
        symptom = Symptom(user_id=user.id, name=name, description=description)
        db.session.add(symptom)
        db.session.commit()
        flash(f'"{name}" added.', 'success')
        return redirect(url_for('symptoms'))

    return render_template('new_symptom.html')


@app.route('/symptoms/<int:symptom_id>/edit', methods=['GET', 'POST'])
def edit_symptom(symptom_id):
    user = get_user()
    symptom = Symptom.query.filter_by(id=symptom_id, user_id=user.id).first_or_404()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip() or None
        if not name:
            return redirect(url_for('symptoms'))
        if len(name) > 200:
            flash('Name must be 200 characters or fewer.', 'error')
            return render_template('edit_symptom.html', symptom=symptom)
        if description and len(description) > 500:
            flash('Description must be 500 characters or fewer.', 'error')
            return render_template('edit_symptom.html', symptom=symptom)
        # Case-insensitive duplicate check (exclude this symptom)
        existing = Symptom.query.filter(Symptom.user_id == user.id,
                                        Symptom.id != symptom.id,
                                        db.func.lower(Symptom.name) == name.lower()).first()
        if existing:
            flash(f'"{existing.name}" already exists.', 'error')
            return render_template('edit_symptom.html', symptom=symptom)
        symptom.name = name
        symptom.description = description
        db.session.commit()
        flash('Updated.', 'success')
        return redirect(url_for('symptoms'))

    return render_template('edit_symptom.html', symptom=symptom)


@app.route('/symptoms/<int:symptom_id>/deactivate', methods=['POST'])
def deactivate_symptom(symptom_id):
    user = get_user()
    symptom = Symptom.query.filter_by(id=symptom_id, user_id=user.id).first_or_404()
    symptom.is_active = False
    db.session.commit()
    flash(f'Tracking paused for "{symptom.name}". Historical data preserved.', 'success')
    return redirect(url_for('symptoms'))


@app.route('/symptoms/<int:symptom_id>/reactivate', methods=['POST'])
def reactivate_symptom(symptom_id):
    user = get_user()
    symptom = Symptom.query.filter_by(id=symptom_id, user_id=user.id).first_or_404()
    symptom.is_active = True
    db.session.commit()
    flash(f'"{symptom.name}" resumed.', 'success')
    return redirect(url_for('symptoms'))


# ---------------------------------------------------------------------------
# Experiments
# ---------------------------------------------------------------------------

@app.route('/experiments')
def experiments():
    user = get_user()
    active = Experiment.query.filter_by(user_id=user.id, status='active').order_by(Experiment.start_date.desc()).all()
    completed = Experiment.query.filter_by(user_id=user.id, status='completed').order_by(Experiment.start_date.desc()).all()
    abandoned = Experiment.query.filter_by(user_id=user.id, status='abandoned').order_by(Experiment.start_date.desc()).all()
    return render_template('experiments.html', active=active, completed=completed, abandoned=abandoned)


@app.route('/experiments/new', methods=['GET', 'POST'])
def new_experiment():
    user = get_user()
    preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative', status='active').all()
    prefill_protocol_id = request.args.get('protocol_id', type=int)
    active_experiment = get_active_experiment(user.id)

    if request.method == 'POST':
        exp_name = request.form.get('name', '').strip()
        hypothesis_val = request.form.get('hypothesis', '').strip() or None
        if exp_name and len(exp_name) > 200:
            flash('Experiment name must be 200 characters or fewer.', 'error')
            return render_template('new_experiment.html', preventatives=preventatives,
                                   prefill_protocol_id=prefill_protocol_id, today=date.today(),
                                   active_experiment=active_experiment)
        if hypothesis_val and len(hypothesis_val) > 500:
            flash('Hypothesis must be 500 characters or fewer.', 'error')
            return render_template('new_experiment.html', preventatives=preventatives,
                                   prefill_protocol_id=prefill_protocol_id, today=date.today(),
                                   active_experiment=active_experiment)
        start_str = request.form.get('start_date', '').strip()
        try:
            start = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else date.today()
        except ValueError:
            start = date.today()
        try:
            weeks = max(1, int(request.form.get('stabilization_weeks') or 3))
        except (ValueError, TypeError):
            weeks = 3

        # Handle inline protocol creation
        raw_protocol_id = request.form.get('protocol_id', '')
        if raw_protocol_id == '__new__':
            new_proto_name = request.form.get('new_protocol_name', '').strip()
            if not new_proto_name:
                flash('Protocol name is required.', 'error')
                return render_template('new_experiment.html', preventatives=preventatives,
                                       prefill_protocol_id=prefill_protocol_id, today=date.today(),
                                       active_experiment=active_experiment)
            if len(new_proto_name) > 200:
                flash('Protocol name must be 200 characters or fewer.', 'error')
                return render_template('new_experiment.html', preventatives=preventatives,
                                       prefill_protocol_id=prefill_protocol_id, today=date.today(),
                                       active_experiment=active_experiment)
            protocol = Protocol(
                user_id=user.id,
                name=new_proto_name,
                type='preventative',
                start_date=start,
                dose_frequency=request.form.get('new_protocol_dose') or None,
                status='active',
            )
            db.session.add(protocol)
            db.session.flush()
            db.session.add(ProtocolEvent(
                protocol_id=protocol.id,
                user_id=user.id,
                event_type='started',
                date=start,
            ))
            protocol_id = protocol.id
        else:
            try:
                protocol_id = int(raw_protocol_id) if raw_protocol_id else None
            except (ValueError, TypeError):
                protocol_id = None
            if protocol_id:
                owned = Protocol.query.filter_by(id=protocol_id, user_id=user.id, type='preventative').first()
                if not owned:
                    flash('Invalid protocol.', 'error')
                    return redirect(url_for('new_experiment'))

        exp = Experiment(
            user_id=user.id,
            name=exp_name,
            hypothesis=hypothesis_val,
            protocol_id=protocol_id,
            start_date=start,
            stabilization_weeks=weeks,
            baseline_episodes_per_month=user.baseline_episodes_per_month,
            status='active',
        )
        db.session.add(exp)
        db.session.commit()
        assess_date = exp.assessment_date.strftime('%b %-d, %Y')
        flash(f'Experiment started. Assessment due {assess_date}.', 'success')
        return redirect(url_for('experiments'))

    return render_template('new_experiment.html', preventatives=preventatives,
                           prefill_protocol_id=prefill_protocol_id, today=date.today(),
                           active_experiment=active_experiment)


@app.route('/experiments/offer/<int:protocol_id>')
def experiment_offer(protocol_id):
    user = get_user()
    protocol = Protocol.query.filter_by(id=protocol_id, user_id=user.id, type='preventative').first_or_404()
    return render_template('experiment_offer.html', protocol=protocol)


@app.route('/experiments/<int:exp_id>/assess', methods=['GET', 'POST'])
def assess_experiment(exp_id):
    user = get_user()
    experiment = Experiment.query.filter_by(id=exp_id, user_id=user.id).first_or_404()

    if request.method == 'POST':
        try:
            experiment.outcome_rating = max(1, min(10, int(request.form.get('outcome_rating', 5))))
        except (ValueError, TypeError):
            experiment.outcome_rating = 5
        experiment.outcome_notes = request.form.get('outcome_notes', '').strip() or None
        experiment.decision = request.form.get('decision')
        experiment.status = 'completed'

        if experiment.protocol and experiment.decision in ('pause', 'stop'):
            experiment.protocol.status = {'pause': 'paused', 'stop': 'stopped'}[experiment.decision]

        db.session.commit()
        flash(f'"{experiment.name}" assessed and completed.', 'success')
        return redirect(url_for('experiments'))

    # ── Compute assessment context data ──
    today = date.today()
    before_start = experiment.start_date - timedelta(weeks=8)
    exp_start = experiment.start_date

    episodes_before = (
        Episode.query.filter(Episode.user_id == user.id,
                             Episode.onset >= datetime.combine(before_start, datetime.min.time()),
                             Episode.onset < datetime.combine(exp_start, datetime.min.time()))
        .order_by(Episode.onset)
        .all()
    )
    episodes_during = (
        Episode.query.filter(Episode.user_id == user.id,
                             Episode.onset >= datetime.combine(exp_start, datetime.min.time()))
        .order_by(Episode.onset)
        .all()
    )

    weeks_before = max((exp_start - before_start).days / 7.0, 1)
    weeks_during = max((today - exp_start).days / 7.0, 1)
    freq_before = round(len(episodes_before) / weeks_before, 1)
    freq_during = round(len(episodes_during) / weeks_during, 1)

    # Per-symptom avg before vs during
    active_symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    symptom_comparison = []
    for symptom in active_symptoms:
        before_scores = [
            ss.score for ep in episodes_before
            for ss in ep.symptom_scores if ss.symptom_id == symptom.id
        ]
        during_scores = [
            ss.score for ep in episodes_during
            for ss in ep.symptom_scores if ss.symptom_id == symptom.id
        ]
        symptom_comparison.append({
            'name': symptom.name,
            'avg_before': round(sum(before_scores) / len(before_scores), 1) if before_scores else None,
            'avg_during': round(sum(during_scores) / len(during_scores), 1) if during_scores else None,
        })

    # Weekly symptom trend data across full window (before + during)
    window_start = before_start - timedelta(days=before_start.weekday())  # Monday
    total_weeks = max(int(((today - window_start).days + 6) / 7), 1)
    chart_labels = []
    for i in range(total_weeks):
        ws = window_start + timedelta(weeks=i)
        chart_labels.append(ws.strftime('%b %-d'))

    all_window_episodes = episodes_before + episodes_during
    symptom_colors = ['#a07de0', '#4caf78', '#e8a838', '#e05252', '#5c9dbf', '#bf5ca0']
    assess_chart_datasets = []
    for idx, symptom in enumerate(active_symptoms):
        weekly_data = []
        for i in range(total_weeks):
            ws = window_start + timedelta(weeks=i)
            we = ws + timedelta(days=7)
            week_scores = [
                ss.score for ep in all_window_episodes
                for ss in ep.symptom_scores
                if ss.symptom_id == symptom.id and ws <= ep.onset.date() < we
            ]
            weekly_data.append(round(sum(week_scores) / len(week_scores), 1) if week_scores else None)
        assess_chart_datasets.append({
            'label': symptom.name,
            'data': weekly_data,
            'color': symptom_colors[idx % len(symptom_colors)]
        })

    exp_start_week_index = (exp_start - window_start).days / 7.0
    has_before_data = len(episodes_before) >= 2

    assess_data = {
        'freq_before': freq_before,
        'freq_during': freq_during,
        'weeks_during': int(weeks_during),
        'symptom_comparison': symptom_comparison,
        'chart_labels': chart_labels,
        'chart_datasets': assess_chart_datasets,
        'exp_start_week_index': round(exp_start_week_index, 1),
        'has_before_data': has_before_data,
        'has_chart_data': len(all_window_episodes) >= 2,
    }

    return render_template('assess_experiment.html', experiment=experiment, assess_data=assess_data)


@app.route('/experiments/<int:exp_id>/edit', methods=['GET', 'POST'])
def edit_experiment(exp_id):
    user = get_user()
    experiment = Experiment.query.filter_by(id=exp_id, user_id=user.id).first_or_404()
    preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative').all()

    if request.method == 'POST':
        exp_name = request.form.get('name', '').strip()
        hypothesis_val = request.form.get('hypothesis', '').strip() or None
        if exp_name and len(exp_name) > 200:
            flash('Experiment name must be 200 characters or fewer.', 'error')
            return render_template('edit_experiment.html', experiment=experiment, preventatives=preventatives)
        if hypothesis_val and len(hypothesis_val) > 500:
            flash('Hypothesis must be 500 characters or fewer.', 'error')
            return render_template('edit_experiment.html', experiment=experiment, preventatives=preventatives)
        experiment.name = exp_name
        experiment.hypothesis = hypothesis_val
        try:
            experiment.stabilization_weeks = max(1, int(request.form.get('stabilization_weeks') or 3))
        except (ValueError, TypeError):
            experiment.stabilization_weeks = 3
        start_str = request.form.get('start_date', '').strip()
        if start_str:
            try:
                experiment.start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
            except ValueError:
                pass
        protocol_id = request.form.get('protocol_id')
        if protocol_id:
            try:
                owned = Protocol.query.filter_by(id=int(protocol_id), user_id=user.id, type='preventative').first()
            except (ValueError, TypeError):
                owned = None
            experiment.protocol_id = owned.id if owned else None
        else:
            experiment.protocol_id = None
        db.session.commit()
        flash(f'"{experiment.name}" updated.', 'success')
        return redirect(url_for('experiments'))

    return render_template('edit_experiment.html', experiment=experiment,
                           preventatives=preventatives)


@app.route('/experiments/<int:exp_id>/abandon', methods=['POST'])
def abandon_experiment(exp_id):
    user = get_user()
    experiment = Experiment.query.filter_by(id=exp_id, user_id=user.id).first_or_404()
    experiment.status = 'abandoned'
    db.session.commit()
    flash(f'"{experiment.name}" abandoned.', 'success')
    return redirect(url_for('experiments'))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    user = get_user()
    today = date.today()
    active_preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative', status='active').all()

    # ── Symptom cards: avg this month + pct change from baseline ──
    month_start = today.replace(day=1)
    active_symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    symptom_stats = []
    for symptom in active_symptoms:
        scores = (
            db.session.query(SymptomScore.score)
            .join(Episode, SymptomScore.episode_id == Episode.id)
            .filter(Episode.user_id == user.id, SymptomScore.symptom_id == symptom.id,
                    Episode.onset >= datetime.combine(month_start, datetime.min.time()))
            .all()
        )
        avg = round(sum(s[0] for s in scores) / len(scores), 1) if scores else None
        baseline = symptom.baseline_score
        trend = None
        pct_change = None
        if avg is not None and baseline is not None and baseline > 0:
            pct_change = round((avg - baseline) / baseline * 100)
            if avg > baseline + 0.5:
                trend = 'up'
            elif avg < baseline - 0.5:
                trend = 'down'
            else:
                trend = 'neutral'
        symptom_stats.append({
            'symptom': symptom, 'avg_score': avg, 'baseline_score': baseline,
            'trend': trend, 'pct_change': pct_change
        })

    # ── Episode frequency: weekly counts for last 12 weeks + current partial week ──
    twelve_weeks_ago = today - timedelta(weeks=12)
    all_episodes_12w = (
        Episode.query.filter(Episode.user_id == user.id,
                             Episode.onset >= datetime.combine(twelve_weeks_ago, datetime.min.time()))
        .order_by(Episode.onset)
        .all()
    )

    # Build week buckets (Monday-anchored), including current partial week
    week_start = twelve_weeks_ago - timedelta(days=twelve_weeks_ago.weekday())  # Monday
    current_week_monday = today - timedelta(days=today.weekday())
    num_weeks = max(int((current_week_monday - week_start).days / 7) + 1, 12)
    week_labels = []
    episode_counts = []
    for i in range(num_weeks):
        ws = week_start + timedelta(weeks=i)
        we = ws + timedelta(days=7)
        is_current = ws <= today < we
        label = ws.strftime('%b %-d')
        if is_current:
            label += ' *'
        week_labels.append(label)
        count = sum(1 for ep in all_episodes_12w if ws <= ep.onset.date() < we)
        episode_counts.append(count)

    # ── Symptom trend datasets: weekly avg per symptom ──
    symptom_colors = ['#a07de0', '#4caf78', '#e8a838', '#e05252', '#5c9dbf', '#bf5ca0']
    symptom_trend_datasets = []
    for idx, symptom in enumerate(active_symptoms):
        scores_12w = (
            db.session.query(SymptomScore.score, Episode.onset)
            .join(Episode, SymptomScore.episode_id == Episode.id)
            .filter(Episode.user_id == user.id, SymptomScore.symptom_id == symptom.id,
                    Episode.onset >= datetime.combine(twelve_weeks_ago, datetime.min.time()))
            .all()
        )
        weekly_data = []
        for i in range(num_weeks):
            ws = week_start + timedelta(weeks=i)
            we = ws + timedelta(days=7)
            week_scores = [s[0] for s in scores_12w if ws <= s[1].date() < we]
            weekly_data.append(round(sum(week_scores) / len(week_scores), 1) if week_scores else None)
        symptom_trend_datasets.append({
            'label': symptom.name,
            'data': weekly_data,
            'color': symptom_colors[idx % len(symptom_colors)]
        })

    # ── Protocol annotations: vertical lines at start dates ──
    protocol_annotations = []
    for p in active_preventatives:
        if p.start_date and p.start_date >= twelve_weeks_ago:
            days_from_start = (p.start_date - week_start).days
            week_index = days_from_start / 7.0
            if 0 <= week_index < num_weeks:
                protocol_annotations.append({'name': p.name, 'week_index': round(week_index, 1)})

    # ── Rescue effectiveness stats (from EpisodeIntervention) ──
    intervention_records = (
        db.session.query(EpisodeIntervention, Protocol.name)
        .join(Protocol, EpisodeIntervention.protocol_id == Protocol.id)
        .join(Episode, EpisodeIntervention.episode_id == Episode.id)
        .filter(Episode.user_id == user.id)
        .all()
    )
    rescue_grouped = defaultdict(list)
    for ei, pname in intervention_records:
        rescue_grouped[pname].append(ei)
    rescue_stats = []
    for name, eis in rescue_grouped.items():
        eff_scores = [ei.effectiveness for ei in eis if ei.effectiveness is not None]
        relief_hours = [ei.time_to_relief_hours for ei in eis if ei.time_to_relief_hours is not None]
        rescue_stats.append({
            'name': name,
            'times_used': len(eis),
            'avg_effectiveness': round(sum(eff_scores) / len(eff_scores), 1) if eff_scores else None,
            'avg_relief_hours': round(sum(relief_hours) / len(relief_hours), 1) if relief_hours else None,
        })
    rescue_stats.sort(key=lambda r: r['times_used'], reverse=True)

    # ── Chart data availability ──
    total_episode_count = Episode.query.filter_by(user_id=user.id).count()
    has_symptom_data = any(
        any(d is not None for d in ds['data'])
        for ds in symptom_trend_datasets
    )
    if all_episodes_12w:
        earliest = all_episodes_12w[0].onset.date()
        has_enough_span = (today - earliest).days >= 14
    else:
        has_enough_span = False
    # Load Chart.js if either chart will render
    has_chart_data = has_enough_span and (total_episode_count >= 3 or has_symptom_data)

    experiments_ready = [
        e for e in Experiment.query.filter_by(user_id=user.id, status='active').all()
        if e.ready_to_assess
    ]

    show_tour = not user.has_seen_tour

    return render_template('index.html',
                           protocols=active_preventatives,
                           symptom_stats=symptom_stats,
                           experiments_ready=experiments_ready,
                           week_labels=week_labels,
                           episode_counts=episode_counts,
                           symptom_trend_datasets=symptom_trend_datasets,
                           protocol_annotations=protocol_annotations,
                           rescue_stats=rescue_stats,
                           has_chart_data=has_chart_data,
                           total_episode_count=total_episode_count,
                           has_symptom_data=has_symptom_data,
                           show_tour=show_tour)


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------

@app.route('/episodes')
def episodes():
    user = get_user()
    all_episodes = Episode.query.filter_by(user_id=user.id).order_by(Episode.onset.desc()).all()
    return render_template('episodes.html', episodes=all_episodes)


@app.route('/episodes/new', methods=['GET', 'POST'])
def new_episode():
    user = get_user()
    symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    rescue_options = Protocol.query.filter(Protocol.user_id == user.id, Protocol.type == 'rescue', Protocol.status != 'removed').order_by(Protocol.available.desc(), Protocol.name).all()

    if request.method == 'POST':
        onset_str = request.form.get('onset')
        onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M') if onset_str else datetime.utcnow()

        if onset > datetime.now():
            flash('Onset date cannot be in the future.', 'error')
            return render_template('new_episode.html', rescue_options=rescue_options, symptoms=symptoms)

        notes_val = request.form.get('notes', '').strip()
        if notes_val and len(notes_val) > 500:
            flash('Notes must be 500 characters or fewer.', 'error')
            return render_template('new_episode.html', rescue_options=rescue_options, symptoms=symptoms)

        episode = Episode(
            user_id=user.id,
            onset=onset,
            peak_severity=None,
            duration_hours=_safe_float(request.form.get('duration_hours'), min_val=0),
            functional_impairment=request.form.get('functional_impairment'),
            notes=request.form.get('notes') or None,
        )
        db.session.add(episode)
        db.session.flush()

        for symptom in symptoms:
            if request.form.get(f'score_{symptom.id}_rated') != '1':
                continue
            score_str = request.form.get(f'score_{symptom.id}', '').strip()
            if score_str:
                try:
                    db.session.add(SymptomScore(episode_id=episode.id, symptom_id=symptom.id, score=max(1, min(10, int(score_str)))))
                except ValueError:
                    pass

        # Save interventions from repeatable form fields
        for i in range(5):
            proto_id = request.form.get(f'intervention_protocol_{i}', '').strip()
            if not proto_id:
                continue
            try:
                proto_id_int = int(proto_id)
            except ValueError:
                continue
            # Validate protocol belongs to current user
            protocol = Protocol.query.filter_by(id=proto_id_int, user_id=user.id, type='rescue').first()
            if not protocol:
                continue
            eff_str = request.form.get(f'intervention_effectiveness_{i}', '').strip()
            relief_str = request.form.get(f'intervention_relief_{i}', '').strip()
            try:
                eff_val = max(1, min(10, int(eff_str))) if eff_str else None
            except ValueError:
                eff_val = None
            try:
                relief_val = max(0, float(relief_str)) if relief_str else None
            except ValueError:
                relief_val = None
            db.session.add(EpisodeIntervention(
                episode_id=episode.id,
                protocol_id=protocol.id,
                effectiveness=eff_val,
                time_to_relief_hours=relief_val,
            ))

        db.session.commit()
        flash('Episode logged.', 'success')
        return redirect(url_for('episodes'))

    return render_template('new_episode.html', rescue_options=rescue_options, symptoms=symptoms)


@app.route('/episodes/<int:episode_id>/edit', methods=['GET', 'POST'])
def edit_episode(episode_id):
    user = get_user()
    episode = Episode.query.filter_by(id=episode_id, user_id=user.id).first_or_404()
    symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    rescue_options = Protocol.query.filter(Protocol.user_id == user.id, Protocol.type == 'rescue', Protocol.status != 'removed').order_by(Protocol.available.desc(), Protocol.name).all()

    if request.method == 'POST':
        onset_str = request.form.get('onset')
        new_onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M') if onset_str else episode.onset

        if new_onset > datetime.now():
            flash('Onset date cannot be in the future.', 'error')
            existing_scores = {ss.symptom_id: ss.score for ss in episode.symptom_scores}
            return render_template('edit_episode.html', episode=episode, rescue_options=rescue_options,
                                   symptoms=symptoms, existing_scores=existing_scores)

        notes_val = request.form.get('notes', '').strip()
        if notes_val and len(notes_val) > 500:
            flash('Notes must be 500 characters or fewer.', 'error')
            existing_scores = {ss.symptom_id: ss.score for ss in episode.symptom_scores}
            return render_template('edit_episode.html', episode=episode, rescue_options=rescue_options,
                                   symptoms=symptoms, existing_scores=existing_scores)

        episode.onset = new_onset
        episode.duration_hours = _safe_float(request.form.get('duration_hours'), min_val=0)
        episode.functional_impairment = request.form.get('functional_impairment')
        episode.notes = notes_val or None

        # Replace symptom scores
        for ss in list(episode.symptom_scores):
            db.session.delete(ss)
        db.session.flush()

        for symptom in symptoms:
            if request.form.get(f'score_{symptom.id}_rated') != '1':
                continue
            score_str = request.form.get(f'score_{symptom.id}', '').strip()
            if score_str:
                try:
                    db.session.add(SymptomScore(episode_id=episode.id, symptom_id=symptom.id, score=max(1, min(10, int(score_str)))))
                except ValueError:
                    pass

        # Replace interventions
        for ei in list(episode.interventions):
            db.session.delete(ei)
        db.session.flush()

        for i in range(5):
            proto_id = request.form.get(f'intervention_protocol_{i}', '').strip()
            if not proto_id:
                continue
            try:
                proto_id_int = int(proto_id)
            except ValueError:
                continue
            # Validate protocol belongs to current user
            protocol = Protocol.query.filter_by(id=proto_id_int, user_id=user.id, type='rescue').first()
            if not protocol:
                continue
            eff_str = request.form.get(f'intervention_effectiveness_{i}', '').strip()
            relief_str = request.form.get(f'intervention_relief_{i}', '').strip()
            try:
                eff_val = max(1, min(10, int(eff_str))) if eff_str else None
            except ValueError:
                eff_val = None
            try:
                relief_val = max(0, float(relief_str)) if relief_str else None
            except ValueError:
                relief_val = None
            db.session.add(EpisodeIntervention(
                episode_id=episode.id,
                protocol_id=protocol.id,
                effectiveness=eff_val,
                time_to_relief_hours=relief_val,
            ))

        db.session.commit()
        flash('Episode updated.', 'success')
        return redirect(url_for('episodes'))

    existing_scores = {ss.symptom_id: ss.score for ss in episode.symptom_scores}
    return render_template('edit_episode.html', episode=episode, rescue_options=rescue_options,
                           symptoms=symptoms, existing_scores=existing_scores)


@app.route('/episodes/<int:episode_id>/delete', methods=['POST'])
def delete_episode(episode_id):
    user = get_user()
    episode = Episode.query.filter_by(id=episode_id, user_id=user.id).first_or_404()
    db.session.delete(episode)
    db.session.commit()
    flash('Episode deleted.', 'success')
    return redirect(url_for('episodes'))


# ---------------------------------------------------------------------------
# Preventative protocols
# ---------------------------------------------------------------------------

@app.route('/protocols')
def protocols():
    user = get_user()
    preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative').order_by(Protocol.start_date.desc()).all()
    rescue_options = Protocol.query.filter(Protocol.user_id == user.id, Protocol.type == 'rescue', Protocol.status != 'removed').order_by(Protocol.available.desc(), Protocol.name).all()
    removed_interventions = Protocol.query.filter_by(user_id=user.id, type='rescue', status='removed').order_by(Protocol.name).all()
    return render_template('protocols.html', preventatives=preventatives, rescue_options=rescue_options, removed_interventions=removed_interventions)


@app.route('/protocols/new', methods=['GET', 'POST'])
def new_protocol():
    user = get_user()
    active_experiment = get_active_experiment(user.id)

    if request.method == 'POST':
        name_val = request.form.get('name', '').strip()
        notes_val = request.form.get('notes', '').strip()
        if name_val and len(name_val) > 200:
            flash('Name must be 200 characters or fewer.', 'error')
            return render_template('new_protocol.html', active_experiment=active_experiment)
        if notes_val and len(notes_val) > 500:
            flash('Notes must be 500 characters or fewer.', 'error')
            return render_template('new_protocol.html', active_experiment=active_experiment)
        start_date_str = request.form.get('start_date')
        protocol = Protocol(
            user_id=user.id,
            name=name_val,
            type='preventative',
            start_date=datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None,
            dose_frequency=request.form.get('dose_frequency') or None,
            status=request.form.get('status', 'active'),
            notes=notes_val or None,
        )
        db.session.add(protocol)
        db.session.flush()
        event_type = {'active': 'started', 'paused': 'paused', 'stopped': 'stopped'}.get(protocol.status, 'started')
        db.session.add(ProtocolEvent(
            protocol_id=protocol.id,
            user_id=user.id,
            event_type=event_type,
            date=protocol.start_date or date.today(),
        ))
        db.session.commit()
        flash('Preventative added.', 'success')
        # Offer to start an experiment for active protocols
        if protocol.status == 'active':
            return redirect(url_for('experiment_offer', protocol_id=protocol.id))
        return redirect(url_for('protocols'))

    return render_template('new_protocol.html', active_experiment=active_experiment)


@app.route('/protocols/<int:protocol_id>/edit', methods=['GET', 'POST'])
def edit_protocol(protocol_id):
    user = get_user()
    protocol = Protocol.query.filter_by(id=protocol_id, user_id=user.id, type='preventative').first_or_404()
    active_experiment = get_active_experiment(user.id)

    if request.method == 'POST':
        name_val = request.form.get('name', '').strip()
        notes_val = request.form.get('notes', '').strip()
        if name_val and len(name_val) > 200:
            flash('Name must be 200 characters or fewer.', 'error')
            return render_template('edit_protocol.html', protocol=protocol, active_experiment=active_experiment)
        if notes_val and len(notes_val) > 500:
            flash('Notes must be 500 characters or fewer.', 'error')
            return render_template('edit_protocol.html', protocol=protocol, active_experiment=active_experiment)
        old_status = protocol.status
        old_dose = protocol.dose_frequency

        start_date_str = request.form.get('start_date')
        protocol.name = name_val
        protocol.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        protocol.dose_frequency = request.form.get('dose_frequency') or None
        protocol.status = request.form.get('status', protocol.status)
        protocol.notes = notes_val or None

        if old_status != protocol.status:
            event_type = {'active': 'reactivated', 'paused': 'paused', 'stopped': 'stopped'}.get(protocol.status, protocol.status)
            db.session.add(ProtocolEvent(
                protocol_id=protocol.id, user_id=user.id,
                event_type=event_type, date=date.today(),
            ))
        if old_dose != protocol.dose_frequency:
            detail = f'Changed from "{old_dose or "not set"}" to "{protocol.dose_frequency or "not set"}"'
            db.session.add(ProtocolEvent(
                protocol_id=protocol.id, user_id=user.id,
                event_type='dose_changed', detail=detail, date=date.today(),
            ))

        db.session.commit()
        flash('Preventative updated.', 'success')
        return redirect(url_for('protocols'))

    return render_template('edit_protocol.html', protocol=protocol, active_experiment=active_experiment)


@app.route('/protocols/<int:protocol_id>')
def protocol_detail(protocol_id):
    user = get_user()
    protocol = Protocol.query.filter_by(id=protocol_id, user_id=user.id, type='preventative').first_or_404()

    compliance = ProtocolCompliance.query.filter_by(protocol_id=protocol_id, user_id=user.id).all()
    events = ProtocolEvent.query.filter_by(protocol_id=protocol_id, user_id=user.id).all()

    timeline = []
    for c in compliance:
        timeline.append({
            'date': c.date,
            'type': 'taken' if c.took else 'missed',
            'notes': c.notes,
            'detail': None,
            'created_at': c.created_at,
        })
    for e in events:
        timeline.append({
            'date': e.date,
            'type': e.event_type,
            'notes': None,
            'detail': e.detail,
            'created_at': e.created_at,
        })
    timeline.sort(key=lambda x: (x['date'], x['created_at']), reverse=True)

    today_log = ProtocolCompliance.query.filter_by(
        protocol_id=protocol_id, user_id=user.id, date=date.today()
    ).first()

    return render_template('protocol_detail.html',
                           protocol=protocol, timeline=timeline, today_log=today_log, today=date.today())


@app.route('/protocols/<int:protocol_id>/log', methods=['POST'])
def log_protocol_today(protocol_id):
    user = get_user()
    protocol = Protocol.query.filter_by(id=protocol_id, user_id=user.id, type='preventative').first_or_404()

    took = request.form.get('took') == 'yes'
    notes = request.form.get('notes', '').strip() or None

    existing = ProtocolCompliance.query.filter_by(
        protocol_id=protocol_id, user_id=user.id, date=date.today()
    ).first()
    if existing:
        existing.took = took
        existing.notes = notes
    else:
        db.session.add(ProtocolCompliance(
            user_id=user.id, protocol_id=protocol_id,
            date=date.today(), took=took, notes=notes,
        ))
    db.session.commit()
    flash('Logged.', 'success')
    return redirect(url_for('protocol_detail', protocol_id=protocol_id))


# ---------------------------------------------------------------------------
# Rescue options
# ---------------------------------------------------------------------------

@app.route('/rescue-options/new', methods=['GET', 'POST'])
def new_rescue_option():
    user = get_user()

    if request.method == 'POST':
        name_val = request.form.get('name', '').strip()
        notes_val = request.form.get('notes', '').strip()
        if name_val and len(name_val) > 200:
            flash('Name must be 200 characters or fewer.', 'error')
            return render_template('new_rescue_option.html')
        if notes_val and len(notes_val) > 500:
            flash('Notes must be 500 characters or fewer.', 'error')
            return render_template('new_rescue_option.html')
        option = Protocol(
            user_id=user.id,
            name=name_val,
            type='rescue',
            available=bool(request.form.get('available')),
            notes=notes_val or None,
        )
        db.session.add(option)
        db.session.commit()
        flash('Intervention added.', 'success')
        return redirect(url_for('protocols'))

    return render_template('new_rescue_option.html')


@app.route('/rescue-options/<int:option_id>/edit', methods=['GET', 'POST'])
def edit_rescue_option(option_id):
    user = get_user()
    option = Protocol.query.filter_by(id=option_id, user_id=user.id, type='rescue').first_or_404()
    if option.status == 'removed':
        flash('Restore this intervention before editing.', 'error')
        return redirect(url_for('protocols'))

    if request.method == 'POST':
        name_val = request.form.get('name', '').strip()
        notes_val = request.form.get('notes', '').strip()
        if name_val and len(name_val) > 200:
            flash('Name must be 200 characters or fewer.', 'error')
            return render_template('edit_rescue_option.html', option=option)
        if notes_val and len(notes_val) > 500:
            flash('Notes must be 500 characters or fewer.', 'error')
            return render_template('edit_rescue_option.html', option=option)
        option.name = name_val
        option.available = bool(request.form.get('available'))
        option.notes = notes_val or None
        db.session.commit()
        flash('Intervention updated.', 'success')
        return redirect(url_for('protocols'))

    return render_template('edit_rescue_option.html', option=option)


@app.route('/rescue-options/<int:option_id>/restore', methods=['POST'])
def restore_rescue_option(option_id):
    user = get_user()
    option = Protocol.query.filter_by(id=option_id, user_id=user.id, type='rescue', status='removed').first_or_404()
    option.status = 'active'
    option.available = True
    db.session.commit()
    flash(f'{option.name} has been restored.', 'success')
    return redirect(url_for('protocols'))


@app.route('/protocols/<int:protocol_id>/delete', methods=['POST'])
def delete_protocol(protocol_id):
    user = get_user()
    protocol = Protocol.query.filter_by(id=protocol_id, user_id=user.id).first_or_404()

    # For rescue protocols (interventions) with historical usage, soft-delete instead of hard delete
    if protocol.type == 'rescue':
        has_usage = EpisodeIntervention.query.filter_by(protocol_id=protocol.id).first()
        if has_usage:
            protocol.status = 'removed'
            protocol.available = False
            db.session.commit()
            flash(f'{protocol.name} has been removed. Historical episode data is preserved.', 'success')
            return redirect(url_for('protocols'))

    # No historical usage (or preventative) — safe to hard delete
    EpisodeIntervention.query.filter_by(protocol_id=protocol.id).delete()
    db.session.delete(protocol)
    db.session.commit()
    flash('Deleted.', 'success')
    return redirect(url_for('protocols'))


# ─────────────────────────────────────────────────────────────────────────────
# DEV / DEBUG ROUTES
# These routes are only accessible when DEBUG=True (local development).
# They are automatically blocked in production (Railway, DEBUG=False).
# They exist to support local testing and database bootstrapping only.
# ─────────────────────────────────────────────────────────────────────────────

# /dev/reset — clears all user data, resets onboarding for testing
@app.route('/dev/reset', methods=['GET', 'POST'])
def dev_reset():
    if not app.debug:
        return 'Not available in production.', 403

    if request.method == 'POST':
        user = get_user()
        CheckIn.query.filter_by(user_id=user.id).delete()
        ProtocolCompliance.query.filter_by(user_id=user.id).delete()
        Experiment.query.filter_by(user_id=user.id).delete()
        episode_ids = db.session.query(Episode.id).filter_by(user_id=user.id)
        EpisodeIntervention.query.filter(
            EpisodeIntervention.episode_id.in_(episode_ids)
        ).delete(synchronize_session=False)
        SymptomScore.query.filter(
            SymptomScore.episode_id.in_(episode_ids)
        ).delete(synchronize_session=False)
        Episode.query.filter_by(user_id=user.id).delete()
        Protocol.query.filter_by(user_id=user.id).delete()
        Symptom.query.filter_by(user_id=user.id).delete()
        user.onboarding_complete = False
        user.ai_logging_enabled = False
        user.baseline_episodes_per_month = None
        user.has_seen_tour = False
        db.session.commit()
        flash('Dev reset complete. Onboarding restarted.', 'success')
        return redirect(url_for('onboarding_step1'))

    return '''<!doctype html><html><body style="font-family:sans-serif;max-width:400px;margin:60px auto;padding:20px;">
        <h2>Dev Reset</h2>
        <p>This will delete all episodes, symptoms, experiments, and protocols, and restart onboarding.</p>
        <form method="POST">
          <button type="submit" style="background:#e05252;color:#fff;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;font-size:15px;">
            Reset everything
          </button>
          <a href="/" style="margin-left:12px;">Cancel</a>
        </form></body></html>'''


# /dev/seed — populates 12 weeks of realistic test data
@app.route('/dev/seed', methods=['GET', 'POST'])
def dev_seed():
    if not app.debug:
        return 'Not available in production.', 403

    user = get_user()
    existing = Episode.query.filter_by(user_id=user.id).count()
    if existing >= 20:
        return (
            '<!doctype html><html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px;">'
            f'<h2>Seed aborted</h2><p>You already have {existing} episodes. '
            'Seeder only runs with fewer than 20 to avoid overwriting real data.</p>'
            '<a href="/">← Back</a></body></html>'
        ), 400

    if request.method == 'POST':
        today = date.today()
        base_date = today - timedelta(weeks=12)

        # ── Symptoms ──────────────────────────────────────────────────────
        symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
        if not symptoms:
            s1 = Symptom(user_id=user.id, name='Headache',
                         description='Throbbing head pain, typically one-sided',
                         is_active=True, baseline_score=7)
            s2 = Symptom(user_id=user.id, name='Nausea',
                         description='Stomach upset and queasiness',
                         is_active=True, baseline_score=5)
            db.session.add_all([s1, s2])
            db.session.flush()
            symptoms = [s1, s2]

        # ── Protocols ─────────────────────────────────────────────────────
        prev1 = Protocol(
            user_id=user.id, name='Magnesium Glycinate 400mg',
            type='preventative', start_date=base_date,
            dose_frequency='400mg daily at bedtime', status='active',
        )
        prev2_start = base_date + timedelta(weeks=4)
        prev2 = Protocol(
            user_id=user.id, name='Riboflavin 400mg',
            type='preventative', start_date=prev2_start,
            dose_frequency='400mg daily with breakfast', status='active',
        )
        rescue = Protocol(
            user_id=user.id, name='Sumatriptan 50mg',
            type='rescue', available=True,
        )
        db.session.add_all([prev1, prev2, rescue])
        db.session.flush()

        db.session.add(ProtocolEvent(protocol_id=prev1.id, user_id=user.id,
                                     event_type='started', date=prev1.start_date))
        db.session.add(ProtocolEvent(protocol_id=prev2.id, user_id=user.id,
                                     event_type='started', date=prev2.start_date))
        db.session.flush()

        # ── Episodes ──────────────────────────────────────────────────────
        impairments_early = ['working_reduced', 'cannot_work', 'cannot_work', 'completely_incapacitated']
        impairments_late  = ['working_normally', 'working_reduced', 'working_reduced', 'cannot_work']

        for week in range(12):
            week_start = base_date + timedelta(weeks=week)
            n_episodes = random.randint(3, 4)
            day_offsets = sorted(random.sample(range(7), n_episodes))
            rescue_day = random.choice(day_offsets) if random.random() < 0.85 else None

            for day_offset in day_offsets:
                ep_date = week_start + timedelta(days=day_offset)
                if ep_date > today:
                    continue
                onset = datetime(ep_date.year, ep_date.month, ep_date.day,
                                 random.randint(5, 22), random.choice([0, 15, 30, 45]))

                # Scores trend downward after week 6
                base_score = random.randint(6, 9) if week < 6 else random.randint(4, 7)
                impairment = random.choice(impairments_early if week < 6 else impairments_late)
                used_rescue = (day_offset == rescue_day)

                episode = Episode(
                    user_id=user.id,
                    onset=onset,
                    peak_severity=None,
                    duration_hours=round(random.uniform(4, 24), 1),
                    functional_impairment=impairment,
                )
                db.session.add(episode)
                db.session.flush()

                if used_rescue:
                    db.session.add(EpisodeIntervention(
                        episode_id=episode.id,
                        protocol_id=rescue.id,
                        effectiveness=random.randint(4, 9),
                        time_to_relief_hours=round(random.uniform(0.5, 4.0), 1),
                    ))

                for symptom in symptoms:
                    score = max(1, min(10, base_score + random.randint(-1, 1)))
                    db.session.add(SymptomScore(
                        episode_id=episode.id, symptom_id=symptom.id, score=score))

        # ── Protocol compliance ───────────────────────────────────────────
        missed_notes = ['Forgot', 'Upset stomach, skipped', 'Away from home', 'Ran out briefly']

        def seed_compliance(protocol, start):
            current = start
            while current <= today:
                took = random.random() < 0.85
                notes = random.choice(missed_notes) if not took and random.random() < 0.35 else None
                db.session.add(ProtocolCompliance(
                    user_id=user.id, protocol_id=protocol.id,
                    date=current, took=took, notes=notes,
                ))
                current += timedelta(days=1)

        seed_compliance(prev1, prev1.start_date)
        seed_compliance(prev2, prev2.start_date)

        db.session.commit()
        flash('12 weeks of test data seeded successfully.', 'success')
        return redirect(url_for('index'))

    return '''<!doctype html><html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px;">
        <h2>Seed Test Data</h2>
        <p>Generates <strong>12 weeks</strong> of realistic test data:</p>
        <ul style="line-height:1.8;">
          <li>3–4 episodes per week (36–48 total) with symptom scores trending lower after week 6</li>
          <li>2 preventative protocols starting at weeks 1 and 5</li>
          <li>1 intervention (Sumatriptan 50mg) used ~once per week</li>
          <li>Daily protocol compliance entries</li>
        </ul>
        <p style="color:#888; font-size:13px;">Only runs if you have fewer than 20 existing episodes.</p>
        <form method="POST">
          <button type="submit" style="background:#5a6fd6;color:#fff;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;font-size:15px;">
            Seed test data
          </button>
          <a href="/" style="margin-left:12px;">Cancel</a>
        </form></body></html>'''


# /dev/bootstrap — creates admin account on fresh empty database
@app.route('/dev/bootstrap')
def dev_bootstrap():
    if not app.debug:
        return 'Not available in production.', 403

    if User.query.count() > 0:
        return (
            '<!doctype html><html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px;">'
            '<h2>Bootstrap not needed</h2>'
            '<p>Users already exist in the database.</p>'
            '<a href="/login">← Go to login</a></body></html>'
        )

    admin = User(
        name='Admin',
        email='admin@baseline.app',
        password_hash=bcrypt.generate_password_hash('Baseline2026!').decode('utf-8'),
        onboarding_complete=True,
        is_active=True,
    )
    db.session.add(admin)
    db.session.flush()

    code = secrets.token_urlsafe(12)
    db.session.add(InviteCode(code=code))
    db.session.commit()

    return (
        '<!doctype html><html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px;">'
        '<h2>Bootstrap Complete</h2>'
        '<p>Admin account created: <strong>admin@baseline.app</strong> / <strong>Baseline2026!</strong></p>'
        '<p style="margin-top:16px;">Invite code for next user:</p>'
        f'<p style="font-size:20px;font-weight:bold;background:#222;color:#7c5cbf;padding:16px;border-radius:8px;'
        f'text-align:center;letter-spacing:1px;font-family:monospace;">{code}</p>'
        '<a href="/login">← Go to login</a></body></html>'
    )


# /dev/create-invite — generates a new invite code
@app.route('/dev/create-invite')
def dev_create_invite():
    if not app.debug:
        return 'Not available in production.', 403

    code = secrets.token_urlsafe(12)
    db.session.add(InviteCode(code=code))
    db.session.commit()
    return (
        '<!doctype html><html><body style="font-family:sans-serif;max-width:500px;margin:60px auto;padding:20px;">'
        f'<h2>Invite Code Created</h2>'
        f'<p style="font-size:20px;font-weight:bold;background:#222;color:#7c5cbf;padding:16px;border-radius:8px;'
        f'text-align:center;letter-spacing:1px;font-family:monospace;">{code}</p>'
        f'<p style="color:#888;">Share this code with a new user. It can only be used once.</p>'
        f'<a href="/">← Back to dashboard</a></body></html>'
    )


def migrate_existing_user():
    """Give the existing legacy user an email/password so they can log in."""
    user = User.query.first()
    if user and not user.email:
        user.email = 'admin@baseline.app'
        user.password_hash = bcrypt.generate_password_hash('Baseline2026!').decode('utf-8')
        db.session.commit()
        print('=' * 60)
        print('  EXISTING USER MIGRATED')
        print(f'  Email:    admin@baseline.app')
        print(f'  Password: Baseline2026!')
        print('  ** Change this password immediately! **')
        print('=' * 60)


with app.app_context():
    db.create_all()
    run_migrations()
    migrate_existing_user()
    backfill_verified_existing_users()
    cleanup_stale_unverified_users()
    run_data_migrations()
    migrate_episode_interventions()
    ensure_admin_user()
    backfill_resend_contacts()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
