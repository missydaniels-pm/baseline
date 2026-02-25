from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default='Default User')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    episodes = db.relationship('Episode', backref='user', lazy=True, cascade='all, delete-orphan')
    protocols = db.relationship('Protocol', backref='user', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.name}>'


class Episode(db.Model):
    __tablename__ = 'episodes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    onset = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    peak_severity = db.Column(db.Integer, nullable=False)  # 1-10
    duration_hours = db.Column(db.Float, nullable=True)
    functional_impairment = db.Column(db.String(50), nullable=True)
    # Options: working_normally, working_reduced, cannot_work, completely_incapacitated

    rescue_protocol = db.Column(db.Text, nullable=True)
    rescue_effectiveness = db.Column(db.Integer, nullable=True)  # 1-10
    time_to_relief_hours = db.Column(db.Float, nullable=True)

    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Episode {self.onset} severity={self.peak_severity}>'

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
        if self.peak_severity <= 3:
            return 'severity-low'
        elif self.peak_severity <= 6:
            return 'severity-mid'
        else:
            return 'severity-high'


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
