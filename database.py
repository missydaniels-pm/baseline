from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default='Default User')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    onboarding_complete = db.Column(db.Boolean, default=False, nullable=False)
    baseline_episodes_per_month = db.Column(db.Integer, nullable=True)

    episodes = db.relationship('Episode', backref='user', lazy=True, cascade='all, delete-orphan')
    protocols = db.relationship('Protocol', backref='user', lazy=True, cascade='all, delete-orphan')
    symptoms = db.relationship('Symptom', backref='user', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.name}>'


class Symptom(db.Model):
    __tablename__ = 'symptoms'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    baseline_score = db.Column(db.Integer, nullable=True)  # 1-10
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Symptom {self.name}>'


class Episode(db.Model):
    __tablename__ = 'episodes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    onset = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    peak_severity = db.Column(db.Integer, nullable=True)  # kept for migration; not used for new episodes
    duration_hours = db.Column(db.Float, nullable=True)
    functional_impairment = db.Column(db.String(50), nullable=True)
    # Options: working_normally, working_reduced, cannot_work, completely_incapacitated

    rescue_protocol = db.Column(db.Text, nullable=True)
    rescue_effectiveness = db.Column(db.Integer, nullable=True)  # 1-10
    time_to_relief_hours = db.Column(db.Float, nullable=True)

    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    symptom_scores = db.relationship('SymptomScore', backref='episode', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Episode {self.onset}>'

    @property
    def max_score(self):
        if self.symptom_scores:
            return max(ss.score for ss in self.symptom_scores)
        return self.peak_severity

    @property
    def impairment_label(self):
        labels = {
            'working_normally': 'Working normally',
            'working_reduced': 'Working reduced',
            'cannot_work': 'Cannot work',
            'completely_incapacitated': 'Completely incapacitated',
        }
        return labels.get(self.functional_impairment, self.functional_impairment or '—')

    @property
    def severity_class(self):
        score = self.max_score
        if score is None:
            return 'severity-mid'
        if score <= 3:
            return 'severity-low'
        elif score <= 6:
            return 'severity-mid'
        else:
            return 'severity-high'


class SymptomScore(db.Model):
    __tablename__ = 'symptom_scores'

    id = db.Column(db.Integer, primary_key=True)
    episode_id = db.Column(db.Integer, db.ForeignKey('episodes.id'), nullable=False)
    symptom_id = db.Column(db.Integer, db.ForeignKey('symptoms.id'), nullable=False)
    score = db.Column(db.Integer, nullable=False)  # 1-10

    symptom = db.relationship('Symptom')

    def __repr__(self):
        return f'<SymptomScore episode={self.episode_id} symptom={self.symptom_id} score={self.score}>'


class Protocol(db.Model):
    __tablename__ = 'protocols'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # preventative or rescue
    start_date = db.Column(db.Date, nullable=True)
    dose_frequency = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='active')  # active, paused, stopped
    available = db.Column(db.Boolean, nullable=False, default=True)  # rescue options only
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Protocol {self.name}>'

    @property
    def status_class(self):
        return {
            'active': 'status-active',
            'paused': 'status-paused',
            'stopped': 'status-stopped',
        }.get(self.status, '')
