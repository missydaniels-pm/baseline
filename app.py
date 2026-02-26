from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import text
from database import db, User, Episode, Protocol, Symptom, SymptomScore
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///migraine_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'baseline-secret-key'

db.init_app(app)


def run_migrations():
    """Add columns that may not exist in older DB files."""
    migrations = [
        'ALTER TABLE protocols ADD COLUMN available BOOLEAN NOT NULL DEFAULT 1',
        'ALTER TABLE users ADD COLUMN onboarding_complete BOOLEAN NOT NULL DEFAULT 0',
        'ALTER TABLE users ADD COLUMN baseline_episodes_per_month INTEGER',
    ]
    with db.engine.connect() as conn:
        for ddl in migrations:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                pass  # column already exists


def run_data_migrations():
    """Migrate existing episode peak_severity values to SymptomScore records."""
    user = get_user()

    episodes_needing_migration = [
        ep for ep in Episode.query.filter_by(user_id=user.id).all()
        if ep.peak_severity is not None and not ep.symptom_scores
    ]

    if not episodes_needing_migration:
        return

    print(f"Migrating {len(episodes_needing_migration)} episode(s) to symptom scores...")

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
    user = User.query.first()
    if not user:
        user = User(name='Default User')
        db.session.add(user)
        db.session.commit()
    return user


# ---------------------------------------------------------------------------
# Onboarding gate
# ---------------------------------------------------------------------------

@app.before_request
def check_onboarding():
    if request.endpoint in (None, 'static'):
        return
    if request.endpoint.startswith('onboarding_'):
        return
    user = get_user()
    if not user.onboarding_complete:
        return redirect(url_for('onboarding_step1'))


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

        user.onboarding_complete = True
        db.session.commit()
        flash('Welcome! Your tracking is set up.', 'success')
        return redirect(url_for('index'))

    return render_template('onboarding_step2.html', symptoms=symptoms)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    user = get_user()
    recent_episodes = Episode.query.filter_by(user_id=user.id).order_by(Episode.onset.desc()).limit(5).all()
    active_preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative', status='active').all()
    rescue_options = Protocol.query.filter_by(user_id=user.id, type='rescue').all()

    active_symptoms = Symptom.query.filter_by(user_id=user.id, is_active=True).all()
    symptom_stats = []
    for symptom in active_symptoms:
        scores = (
            db.session.query(SymptomScore.score)
            .join(Episode, SymptomScore.episode_id == Episode.id)
            .filter(Episode.user_id == user.id, SymptomScore.symptom_id == symptom.id)
            .order_by(Episode.onset.desc())
            .limit(5)
            .all()
        )
        avg = round(sum(s[0] for s in scores) / len(scores), 1) if scores else None
        baseline = symptom.baseline_score
        trend = None
        if avg is not None and baseline is not None:
            if avg > baseline + 0.5:
                trend = 'up'
            elif avg < baseline - 0.5:
                trend = 'down'
            else:
                trend = 'neutral'
        symptom_stats.append({'symptom': symptom, 'avg_score': avg, 'baseline_score': baseline, 'trend': trend})

    return render_template('index.html',
                           episodes=recent_episodes,
                           protocols=active_preventatives,
                           rescue_options=rescue_options,
                           symptom_stats=symptom_stats)


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
            rescue_effectiveness=int(v) if (v := request.form.get('rescue_effectiveness')) else None,
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
        episode.rescue_effectiveness = int(v) if (v := request.form.get('rescue_effectiveness')) else None
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
        db.session.commit()
        flash('Preventative added.', 'success')
        return redirect(url_for('protocols'))

    return render_template('new_protocol.html')


@app.route('/protocols/<int:protocol_id>/edit', methods=['GET', 'POST'])
def edit_protocol(protocol_id):
    user = get_user()
    protocol = Protocol.query.filter_by(id=protocol_id, user_id=user.id, type='preventative').first_or_404()

    if request.method == 'POST':
        start_date_str = request.form.get('start_date')
        protocol.name = request.form.get('name')
        protocol.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date() if start_date_str else None
        protocol.dose_frequency = request.form.get('dose_frequency') or None
        protocol.status = request.form.get('status', protocol.status)
        protocol.notes = request.form.get('notes') or None
        db.session.commit()
        flash('Preventative updated.', 'success')
        return redirect(url_for('protocols'))

    return render_template('edit_protocol.html', protocol=protocol)


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


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        run_migrations()
        get_user()
        run_data_migrations()
    app.run(host='0.0.0.0', port=5001, debug=True)
