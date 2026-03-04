from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, date

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, default='Default User')
    email = db.Column(db.String(255), unique=True, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)
    invite_code_used = db.Column(db.String(100), nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    onboarding_complete = db.Column(db.Boolean, default=False, nullable=False)
    baseline_episodes_per_month = db.Column(db.Integer, nullable=True)

    ai_logging_enabled = db.Column(db.Boolean, default=False, nullable=False)

    episodes = db.relationship('Episode', backref='user', lazy=True, cascade='all, delete-orphan')
    protocols = db.relationship('Protocol', backref='user', lazy=True, cascade='all, delete-orphan')
    symptoms = db.relationship('Symptom', backref='user', lazy=True, cascade='all, delete-orphan')
    experiments = db.relationship('Experiment', backref='user', lazy=True, cascade='all, delete-orphan')
    checkins = db.relationship('CheckIn', backref='user', lazy=True, cascade='all, delete-orphan')
    protocol_compliance = db.relationship('ProtocolCompliance', backref='user', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.name}>'


class Symptom(db.Model):
    __tablename__ = 'symptoms'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
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


class Experiment(db.Model):
    __tablename__ = 'experiments'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    hypothesis = db.Column(db.Text, nullable=True)
    protocol_id = db.Column(db.Integer, db.ForeignKey('protocols.id'), nullable=True)
    start_date = db.Column(db.Date, nullable=False)
    stabilization_weeks = db.Column(db.Integer, default=3, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='active')  # active/completed/abandoned
    baseline_episodes_per_month = db.Column(db.Integer, nullable=True)
    outcome_rating = db.Column(db.Integer, nullable=True)  # 1-10
    outcome_notes = db.Column(db.Text, nullable=True)
    decision = db.Column(db.String(20), nullable=True)  # continue/pause/stop
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    protocol = db.relationship('Protocol', backref=db.backref('experiments', lazy=True))

    def __repr__(self):
        return f'<Experiment {self.name}>'

    @property
    def assessment_date(self):
        return self.start_date + timedelta(weeks=self.stabilization_weeks)

    @property
    def weeks_elapsed(self):
        elapsed = (date.today() - self.start_date).days / 7
        return min(max(0.0, elapsed), float(self.stabilization_weeks))

    @property
    def weeks_remaining(self):
        remaining = (self.assessment_date - date.today()).days / 7
        return max(0.0, remaining)

    @property
    def progress_pct(self):
        return min(int(self.weeks_elapsed / self.stabilization_weeks * 100), 100)

    @property
    def ready_to_assess(self):
        return date.today() >= self.assessment_date


class CheckIn(db.Model):
    __tablename__ = 'checkins'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    role = db.Column(db.String(10), nullable=False)   # 'user' | 'assistant'
    content = db.Column(db.Text, nullable=False)
    episode_id = db.Column(db.Integer, db.ForeignKey('episodes.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<CheckIn {self.role} {self.created_at}>'


class ProtocolCompliance(db.Model):
    __tablename__ = 'protocol_compliance'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    protocol_id = db.Column(db.Integer, db.ForeignKey('protocols.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    took = db.Column(db.Boolean, nullable=False, default=True)   # True=taken, False=missed
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    protocol = db.relationship('Protocol')

    def __repr__(self):
        return f'<ProtocolCompliance user={self.user_id} protocol={self.protocol_id} date={self.date} took={self.took}>'


class ProtocolEvent(db.Model):
    """Status changes and dose changes recorded automatically or on creation."""
    __tablename__ = 'protocol_events'

    id = db.Column(db.Integer, primary_key=True)
    protocol_id = db.Column(db.Integer, db.ForeignKey('protocols.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # event_type: 'started' | 'paused' | 'stopped' | 'reactivated' | 'dose_changed'
    event_type = db.Column(db.String(30), nullable=False)
    detail = db.Column(db.Text, nullable=True)   # e.g. dose change description
    date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<ProtocolEvent {self.event_type} {self.date}>'


class InviteCode(db.Model):
    __tablename__ = 'invite_codes'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used_at = db.Column(db.DateTime, nullable=True)
    used_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    def __repr__(self):
        return f'<InviteCode {self.code}>'
