from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import text
from database import db, User, Episode, Protocol
from datetime import datetime

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///migraine_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'migraine-tracker-secret-key'

db.init_app(app)


def run_migrations():
    """Add columns that may not exist in older DB files."""
    with db.engine.connect() as conn:
        for col, ddl in [
            ('available', 'ALTER TABLE protocols ADD COLUMN available BOOLEAN NOT NULL DEFAULT 1'),
        ]:
            try:
                conn.execute(text(ddl))
                conn.commit()
            except Exception:
                pass  # column already exists


def get_user():
    user = User.query.first()
    if not user:
        user = User(name='Default User')
        db.session.add(user)
        db.session.commit()
    return user


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    user = get_user()
    recent_episodes = Episode.query.filter_by(user_id=user.id).order_by(Episode.onset.desc()).limit(5).all()
    active_preventatives = Protocol.query.filter_by(user_id=user.id, type='preventative', status='active').all()
    rescue_options = Protocol.query.filter_by(user_id=user.id, type='rescue').all()
    return render_template('index.html',
                           episodes=recent_episodes,
                           protocols=active_preventatives,
                           rescue_options=rescue_options)


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
    rescue_options = Protocol.query.filter_by(user_id=user.id, type='rescue').order_by(Protocol.available.desc(), Protocol.name).all()

    if request.method == 'POST':
        onset_str = request.form.get('onset')
        onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M') if onset_str else datetime.utcnow()

        episode = Episode(
            user_id=user.id,
            onset=onset,
            peak_severity=int(request.form.get('peak_severity', 5)),
            duration_hours=float(request.form.get('duration_hours') or 0) or None,
            functional_impairment=request.form.get('functional_impairment'),
            rescue_protocol=request.form.get('rescue_protocol') or None,
            rescue_effectiveness=int(v) if (v := request.form.get('rescue_effectiveness')) else None,
            time_to_relief_hours=float(v) if (v := request.form.get('time_to_relief_hours')) else None,
            notes=request.form.get('notes') or None,
        )
        db.session.add(episode)
        db.session.commit()
        flash('Episode logged.', 'success')
        return redirect(url_for('episodes'))

    return render_template('new_episode.html', rescue_options=rescue_options)


@app.route('/episodes/<int:episode_id>/edit', methods=['GET', 'POST'])
def edit_episode(episode_id):
    user = get_user()
    episode = Episode.query.filter_by(id=episode_id, user_id=user.id).first_or_404()
    rescue_options = Protocol.query.filter_by(user_id=user.id, type='rescue').order_by(Protocol.available.desc(), Protocol.name).all()

    if request.method == 'POST':
        onset_str = request.form.get('onset')
        episode.onset = datetime.strptime(onset_str, '%Y-%m-%dT%H:%M') if onset_str else episode.onset
        episode.peak_severity = int(request.form.get('peak_severity', episode.peak_severity))
        episode.duration_hours = float(v) if (v := request.form.get('duration_hours')) else None
        episode.functional_impairment = request.form.get('functional_impairment')
        episode.rescue_protocol = request.form.get('rescue_protocol') or None
        episode.rescue_effectiveness = int(v) if (v := request.form.get('rescue_effectiveness')) else None
        episode.time_to_relief_hours = float(v) if (v := request.form.get('time_to_relief_hours')) else None
        episode.notes = request.form.get('notes') or None
        db.session.commit()
        flash('Episode updated.', 'success')
        return redirect(url_for('episodes'))

    return render_template('edit_episode.html', episode=episode, rescue_options=rescue_options)


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
    app.run(host='0.0.0.0', port=5001, debug=True)
