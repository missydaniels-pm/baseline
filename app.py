import os
import json
import re
import random
import secrets
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_bcrypt import Bcrypt
from sqlalchemy import text
from database import db, User, Episode, Protocol, Symptom, SymptomScore, Experiment, CheckIn, ProtocolCompliance, ProtocolEvent, InviteCode
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


def run_migrations():
    """Add columns that may not exist in older DB files."""
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(db.engine)

    migrations = [
        ('protocols',             'available',                 'ALTER TABLE protocols ADD COLUMN available BOOLEAN NOT NULL DEFAULT 1'),
        ('users',                 'onboarding_complete',       'ALTER TABLE users ADD COLUMN onboarding_complete BOOLEAN NOT NULL DEFAULT 0'),
        ('users',                 'baseline_episodes_per_month','ALTER TABLE users ADD COLUMN baseline_episodes_per_month INTEGER'),
        ('users',                 'ai_logging_enabled',        'ALTER TABLE users ADD COLUMN ai_logging_enabled BOOLEAN NOT NULL DEFAULT 0'),
        ('protocol_compliance',   'took',                      'ALTER TABLE protocol_compliance ADD COLUMN took BOOLEAN NOT NULL DEFAULT 1'),
        ('protocol_compliance',   'notes',                     'ALTER TABLE protocol_compliance ADD COLUMN notes TEXT'),
        ('users',                 'email',                     'ALTER TABLE users ADD COLUMN email VARCHAR(255)'),
        ('users',                 'password_hash',             'ALTER TABLE users ADD COLUMN password_hash VARCHAR(255)'),
        ('users',                 'invite_code_used',          'ALTER TABLE users ADD COLUMN invite_code_used VARCHAR(100)'),
        ('users',                 'is_active',                 'ALTER TABLE users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1'),
    ]

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

PUBLIC_ENDPOINTS = {'login', 'register', 'static', 'dev_bootstrap'}

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


# ---------------------------------------------------------------------------
# Authentication routes
# ---------------------------------------------------------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if get_user():
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        remember = 'remember' in request.form

        user = User.query.filter_by(email=email).first()
        if user and user.password_hash and bcrypt.check_password_hash(user.password_hash, password):
            if not user.is_active:
                flash('Your account has been deactivated.', 'error')
                return render_template('login.html')
            session.clear()
            session['user_id'] = user.id
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
def register():
    if get_user():
        return redirect(url_for('index'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        code_str = request.form.get('invite_code', '').strip()

        errors = []
        if not email or '@' not in email:
            errors.append('A valid email is required.')
        elif User.query.filter_by(email=email).first():
            errors.append('An account with that email already exists.')
        if len(password) < 8:
            errors.append('Password must be at least 8 characters.')
        if password != confirm:
            errors.append('Passwords do not match.')

        invite = InviteCode.query.filter_by(code=code_str).first() if code_str else None
        if not invite:
            errors.append('Invalid invite code.')
        elif invite.used_at is not None:
            errors.append('That invite code has already been used.')

        if errors:
            for e in errors:
                flash(e, 'error')
            return render_template('register.html', email=email, invite_code=code_str)

        pw_hash = bcrypt.generate_password_hash(password).decode('utf-8')
        user = User(name=email.split('@')[0], email=email, password_hash=pw_hash,
                    invite_code_used=code_str)
        db.session.add(user)
        db.session.flush()

        invite.used_at = datetime.utcnow()
        invite.used_by_user_id = user.id
        db.session.commit()

        session.clear()
        session['user_id'] = user.id
        return redirect(url_for('onboarding_step1'))

    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


# ---------------------------------------------------------------------------
# Onboarding wizard
# ---------------------------------------------------------------------------

@app.route('/onboarding/step1', methods=['GET', 'POST'])
def onboarding_step1():
    user = get_user()

    if request.method == 'POST':
        # Remove stale onboarding symptoms not yet tied to any episode
        for sym in Symptom.query.filter_by(user_id=user.id).all():
            if not SymptomScore.query.filter_by(symptom_id=sym.id).first():
                db.session.delete(sym)
        db.session.flush()

        for i in range(1, 4):
            name = request.form.get(f'name_{i}', '').strip()
            if name:
                desc = request.form.get(f'description_{i}', '').strip() or None
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
        user.baseline_episodes_per_month = int(baseline_str) if baseline_str else None

        for symptom in symptoms:
            score_str = request.form.get(f'score_{symptom.id}', '').strip()
            if score_str:
                symptom.baseline_score = int(score_str)

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

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    user = get_user()

    if request.method == 'POST':
        user.ai_logging_enabled = ('ai_logging' in request.form)
        db.session.commit()
        flash('Settings saved.', 'success')
        return redirect(url_for('settings'))

    api_key_set = bool(os.environ.get('ANTHROPIC_API_KEY'))
    return render_template('settings.html', user=user, api_key_set=api_key_set)


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
        user.email = new_email
        db.session.commit()
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
    elif new_pw != confirm:
        flash('New passwords do not match.', 'error')
    else:
        user.password_hash = bcrypt.generate_password_hash(new_pw).decode('utf-8')
        db.session.commit()
        flash('Password updated.', 'success')

    return redirect(url_for('settings'))


# ---------------------------------------------------------------------------
# AI Check-in
# ---------------------------------------------------------------------------

def build_system_prompt(user, client_time=None):
    if client_time:
        try:
            local_dt = datetime.strptime(client_time, '%Y-%m-%dT%H:%M')
            today = local_dt.strftime('%Y-%m-%d')
            current_time = local_dt.strftime('%H:%M')
        except ValueError:
            now = datetime.utcnow()
            today = now.strftime('%Y-%m-%d')
            current_time = now.strftime('%H:%M')
    else:
        now = datetime.utcnow()
        today = now.strftime('%Y-%m-%d')
        current_time = now.strftime('%H:%M')
    symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative', status='active').all()
    rescues = Protocol.query.filter_by(user_id=user.id, type='rescue').all()
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

Rescue options:
{rescue_list}

The user will describe how they are feeling or what happened today. Parse their message and respond with ONLY valid JSON (no markdown, no code fences).

For the episode onset field: if the user mentions a specific time (e.g. "around 2pm", "this morning at 8"), infer the full datetime. Otherwise use the current date and time ({today}T{current_time}) as the onset. Never return null for onset when had_episode is true.

Use this exact schema:

{{
  "had_episode": true or false,
  "episode_data": {{
    "onset": "YYYY-MM-DDTHH:MM or null",
    "symptom_scores": {{"<symptom_id_as_string>": <1-10 integer>}},
    "functional_impairment": "working_normally or working_reduced or cannot_work or completely_incapacitated or null",
    "rescue_option_used": "<rescue name> or null",
    "rescue_effectiveness": <1-10 integer or null>,
    "time_to_relief_hours": <float or null>,
    "notes": "<string or null>"
  }},
  "protocol_compliance": [<list of preventative protocol IDs taken today>],
  "general_notes": "<string or null>",
  "suggested_response": "<warm 1-3 sentence reply to the user>"
}}

If no episode occurred, set had_episode to false and episode_data fields to null/empty.
If the user describes experiencing a tracked symptom but does not give a severity score, still set had_episode to true and omit the score — but in suggested_response warmly ask them to rate it on a scale of 1–10 so it can be logged accurately.
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
                try:
                    if onset_str:
                        onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M')
                    elif client_time:
                        onset = datetime.strptime(client_time, '%Y-%m-%dT%H:%M')
                    else:
                        onset = datetime.utcnow()
                except ValueError:
                    onset = datetime.utcnow()

                episode = Episode(
                    user_id=user.id,
                    onset=onset,
                    peak_severity=None,
                    functional_impairment=ep_data.get('functional_impairment') or None,
                    rescue_protocol=ep_data.get('rescue_option_used') or None,
                    rescue_effectiveness=ep_data.get('rescue_effectiveness') or None,
                    time_to_relief_hours=ep_data.get('time_to_relief_hours') or None,
                    notes=ep_data.get('notes') or None,
                )
                db.session.add(episode)
                db.session.flush()
                episode_id = episode.id

                for sym_id_str, score in (ep_data.get('symptom_scores') or {}).items():
                    db.session.add(SymptomScore(
                        episode_id=episode.id,
                        symptom_id=int(sym_id_str),
                        score=int(score),
                    ))

            # Log protocol compliance (deduplicate)
            today_date = date.today()
            for proto_id in (parsed.get('protocol_compliance') or []):
                exists = ProtocolCompliance.query.filter_by(
                    user_id=user.id,
                    protocol_id=int(proto_id),
                    date=today_date,
                ).first()
                if not exists:
                    db.session.add(ProtocolCompliance(
                        user_id=user.id,
                        protocol_id=int(proto_id),
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
        if name:
            symptom = Symptom(
                user_id=user.id,
                name=name,
                description=request.form.get('description', '').strip() or None,
            )
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
        if name:
            symptom.name = name
            symptom.description = request.form.get('description', '').strip() or None
            db.session.commit()
            flash('Symptom updated.', 'success')
        return redirect(url_for('symptoms'))

    return render_template('edit_symptom.html', symptom=symptom)


@app.route('/symptoms/<int:symptom_id>/deactivate', methods=['POST'])
def deactivate_symptom(symptom_id):
    user = get_user()
    symptom = Symptom.query.filter_by(id=symptom_id, user_id=user.id).first_or_404()
    symptom.is_active = False
    db.session.commit()
    flash(f'"{symptom.name}" deactivated. Historical data preserved.', 'success')
    return redirect(url_for('symptoms'))


@app.route('/symptoms/<int:symptom_id>/reactivate', methods=['POST'])
def reactivate_symptom(symptom_id):
    user = get_user()
    symptom = Symptom.query.filter_by(id=symptom_id, user_id=user.id).first_or_404()
    symptom.is_active = True
    db.session.commit()
    flash(f'"{symptom.name}" reactivated.', 'success')
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

    if request.method == 'POST':
        start_str = request.form.get('start_date', '').strip()
        start = datetime.strptime(start_str, '%Y-%m-%d').date() if start_str else date.today()
        weeks = int(request.form.get('stabilization_weeks') or 8)
        protocol_id = int(p) if (p := request.form.get('protocol_id')) else None

        exp = Experiment(
            user_id=user.id,
            name=request.form.get('name', '').strip(),
            hypothesis=request.form.get('hypothesis', '').strip() or None,
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
                           prefill_protocol_id=prefill_protocol_id, today=date.today())


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
        experiment.outcome_rating = int(request.form.get('outcome_rating', 5))
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
        experiment.name = request.form.get('name', '').strip()
        experiment.hypothesis = request.form.get('hypothesis', '').strip() or None
        experiment.stabilization_weeks = int(request.form.get('stabilization_weeks') or 8)
        start_str = request.form.get('start_date', '').strip()
        if start_str:
            experiment.start_date = datetime.strptime(start_str, '%Y-%m-%d').date()
        protocol_id = request.form.get('protocol_id')
        experiment.protocol_id = int(protocol_id) if protocol_id else None
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

    # ── Episode frequency: weekly counts for last 12 weeks ──
    twelve_weeks_ago = today - timedelta(weeks=12)
    all_episodes_12w = (
        Episode.query.filter(Episode.user_id == user.id,
                             Episode.onset >= datetime.combine(twelve_weeks_ago, datetime.min.time()))
        .order_by(Episode.onset)
        .all()
    )

    # Build week buckets (Monday-anchored)
    week_start = twelve_weeks_ago - timedelta(days=twelve_weeks_ago.weekday())  # Monday
    week_labels = []
    episode_counts = []
    for i in range(12):
        ws = week_start + timedelta(weeks=i)
        we = ws + timedelta(days=7)
        week_labels.append(ws.strftime('%b %-d'))
        count = sum(1 for ep in all_episodes_12w if ws <= ep.onset.date() < we)
        episode_counts.append(count)

    # ── Symptom trend datasets: weekly avg per symptom for 12 weeks ──
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
        for i in range(12):
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
            if 0 <= week_index < 12:
                protocol_annotations.append({'name': p.name, 'week_index': round(week_index, 1)})

    # ── Rescue effectiveness stats ──
    rescue_episodes = (
        Episode.query.filter(Episode.user_id == user.id,
                             Episode.rescue_protocol.isnot(None),
                             Episode.rescue_protocol != '')
        .all()
    )
    rescue_grouped = defaultdict(list)
    for ep in rescue_episodes:
        rescue_grouped[ep.rescue_protocol].append(ep)
    rescue_stats = []
    for name, eps in rescue_grouped.items():
        eff_scores = [ep.rescue_effectiveness for ep in eps if ep.rescue_effectiveness is not None]
        relief_hours = [ep.time_to_relief_hours for ep in eps if ep.time_to_relief_hours is not None]
        rescue_stats.append({
            'name': name,
            'times_used': len(eps),
            'avg_effectiveness': round(sum(eff_scores) / len(eff_scores), 1) if eff_scores else None,
            'avg_relief_hours': round(sum(relief_hours) / len(relief_hours), 1) if relief_hours else None,
        })
    rescue_stats.sort(key=lambda r: r['times_used'], reverse=True)

    # ── Chart data availability ──
    if all_episodes_12w:
        earliest = all_episodes_12w[0].onset.date()
        has_chart_data = (today - earliest).days >= 14
    else:
        has_chart_data = False

    experiments_ready = [
        e for e in Experiment.query.filter_by(user_id=user.id, status='active').all()
        if e.ready_to_assess
    ]

    return render_template('index.html',
                           protocols=active_preventatives,
                           symptom_stats=symptom_stats,
                           experiments_ready=experiments_ready,
                           week_labels=week_labels,
                           episode_counts=episode_counts,
                           symptom_trend_datasets=symptom_trend_datasets,
                           protocol_annotations=protocol_annotations,
                           rescue_stats=rescue_stats,
                           has_chart_data=has_chart_data)


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
    rescue_options = Protocol.query.filter_by(user_id=user.id, type='rescue').order_by(Protocol.available.desc(), Protocol.name).all()

    if request.method == 'POST':
        onset_str = request.form.get('onset')
        onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M') if onset_str else datetime.utcnow()

        episode = Episode(
            user_id=user.id,
            onset=onset,
            peak_severity=None,
            duration_hours=float(request.form.get('duration_hours') or 0) or None,
            functional_impairment=request.form.get('functional_impairment'),
            rescue_protocol=request.form.get('rescue_protocol') or None,
            rescue_effectiveness=int(request.form.get('rescue_effectiveness', 5)) if request.form.get('rescue_protocol') else None,
            time_to_relief_hours=float(v) if (v := request.form.get('time_to_relief_hours')) else None,
            notes=request.form.get('notes') or None,
        )
        db.session.add(episode)
        db.session.flush()

        for symptom in symptoms:
            score_str = request.form.get(f'score_{symptom.id}', '').strip()
            if score_str:
                db.session.add(SymptomScore(episode_id=episode.id, symptom_id=symptom.id, score=int(score_str)))

        db.session.commit()
        flash('Episode logged.', 'success')
        return redirect(url_for('episodes'))

    return render_template('new_episode.html', rescue_options=rescue_options, symptoms=symptoms)


@app.route('/episodes/<int:episode_id>/edit', methods=['GET', 'POST'])
def edit_episode(episode_id):
    user = get_user()
    episode = Episode.query.filter_by(id=episode_id, user_id=user.id).first_or_404()
    symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    rescue_options = Protocol.query.filter_by(user_id=user.id, type='rescue').order_by(Protocol.available.desc(), Protocol.name).all()

    if request.method == 'POST':
        onset_str = request.form.get('onset')
        episode.onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M') if onset_str else episode.onset
        episode.duration_hours = float(v) if (v := request.form.get('duration_hours')) else None
        episode.functional_impairment = request.form.get('functional_impairment')
        episode.rescue_protocol = request.form.get('rescue_protocol') or None
        episode.rescue_effectiveness = int(request.form.get('rescue_effectiveness', 5)) if episode.rescue_protocol else None
        episode.time_to_relief_hours = float(v) if (v := request.form.get('time_to_relief_hours')) else None
        episode.notes = request.form.get('notes') or None

        # Replace symptom scores
        for ss in list(episode.symptom_scores):
            db.session.delete(ss)
        db.session.flush()

        for symptom in symptoms:
            score_str = request.form.get(f'score_{symptom.id}', '').strip()
            if score_str:
                db.session.add(SymptomScore(episode_id=episode.id, symptom_id=symptom.id, score=int(score_str)))

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
    rescue_options = Protocol.query.filter_by(user_id=user.id, type='rescue').order_by(Protocol.available.desc(), Protocol.name).all()
    return render_template('protocols.html', preventatives=preventatives, rescue_options=rescue_options)


@app.route('/protocols/new', methods=['GET', 'POST'])
def new_protocol():
    user = get_user()
    active_experiment = get_active_experiment(user.id)

    if request.method == 'POST':
        start_date_str = request.form.get('start_date')
        protocol = Protocol(
            user_id=user.id,
            name=request.form.get('name'),
            type='preventative',
            start_date=datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None,
            dose_frequency=request.form.get('dose_frequency') or None,
            status=request.form.get('status', 'active'),
            notes=request.form.get('notes') or None,
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
        old_status = protocol.status
        old_dose = protocol.dose_frequency

        start_date_str = request.form.get('start_date')
        protocol.name = request.form.get('name')
        protocol.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        protocol.dose_frequency = request.form.get('dose_frequency') or None
        protocol.status = request.form.get('status', protocol.status)
        protocol.notes = request.form.get('notes') or None

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
        option = Protocol(
            user_id=user.id,
            name=request.form.get('name'),
            type='rescue',
            available=bool(request.form.get('available')),
            notes=request.form.get('notes') or None,
        )
        db.session.add(option)
        db.session.commit()
        flash('Rescue option added.', 'success')
        return redirect(url_for('protocols'))

    return render_template('new_rescue_option.html')


@app.route('/rescue-options/<int:option_id>/edit', methods=['GET', 'POST'])
def edit_rescue_option(option_id):
    user = get_user()
    option = Protocol.query.filter_by(id=option_id, user_id=user.id, type='rescue').first_or_404()

    if request.method == 'POST':
        option.name = request.form.get('name')
        option.available = bool(request.form.get('available'))
        option.notes = request.form.get('notes') or None
        db.session.commit()
        flash('Rescue option updated.', 'success')
        return redirect(url_for('protocols'))

    return render_template('edit_rescue_option.html', option=option)


@app.route('/protocols/<int:protocol_id>/delete', methods=['POST'])
def delete_protocol(protocol_id):
    user = get_user()
    protocol = Protocol.query.filter_by(id=protocol_id, user_id=user.id).first_or_404()
    db.session.delete(protocol)
    db.session.commit()
    flash('Deleted.', 'success')
    return redirect(url_for('protocols'))


# ---------------------------------------------------------------------------
# Dev utilities
# ---------------------------------------------------------------------------

@app.route('/dev/reset', methods=['GET', 'POST'])
def dev_reset():
    if not app.debug:
        return 'Not available in production.', 403

    if request.method == 'POST':
        user = get_user()
        CheckIn.query.filter_by(user_id=user.id).delete()
        ProtocolCompliance.query.filter_by(user_id=user.id).delete()
        Experiment.query.filter_by(user_id=user.id).delete()
        SymptomScore.query.filter(
            SymptomScore.episode_id.in_(
                db.session.query(Episode.id).filter_by(user_id=user.id)
            )
        ).delete(synchronize_session=False)
        Episode.query.filter_by(user_id=user.id).delete()
        Protocol.query.filter_by(user_id=user.id).delete()
        Symptom.query.filter_by(user_id=user.id).delete()
        user.onboarding_complete = False
        user.ai_logging_enabled = False
        user.baseline_episodes_per_month = None
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
                    rescue_protocol=rescue.name if used_rescue else None,
                    rescue_effectiveness=random.randint(4, 9) if used_rescue else None,
                    time_to_relief_hours=round(random.uniform(0.5, 4.0), 1) if used_rescue else None,
                )
                db.session.add(episode)
                db.session.flush()

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
          <li>1 rescue option (Sumatriptan 50mg) used ~once per week</li>
          <li>Daily protocol compliance entries</li>
        </ul>
        <p style="color:#888; font-size:13px;">Only runs if you have fewer than 20 existing episodes.</p>
        <form method="POST">
          <button type="submit" style="background:#5a6fd6;color:#fff;border:none;padding:10px 20px;border-radius:6px;cursor:pointer;font-size:15px;">
            Seed test data
          </button>
          <a href="/" style="margin-left:12px;">Cancel</a>
        </form></body></html>'''


@app.route('/dev/bootstrap')
def dev_bootstrap():
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
    run_data_migrations()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
