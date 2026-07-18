from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta, date
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()


@event.listens_for(Engine, 'connect')
def _enforce_sqlite_foreign_keys(dbapi_connection, connection_record):
    # SQLite doesn't enforce foreign keys by default; production PostgreSQL
    # always does. Enforcing them locally makes delete-path bugs reproducible
    # in dev instead of surfacing as production IntegrityErrors (root cause of
    # the 7/12/26 protocol-delete incident).
    if type(dbapi_connection).__module__.startswith('sqlite3'):
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()


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
    has_seen_tour = db.Column(db.Boolean, default=False, nullable=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    email_updates_enabled = db.Column(db.Boolean, default=True, nullable=False)
    # IANA timezone (e.g. 'America/Los_Angeles'), auto-detected from the browser
    # (baseline_tz cookie) and persisted so "today"/day-boundary logic is durable
    # server-side instead of cookie-dependent. Null until the first authed request
    # syncs it; callers fall back to the cookie, then server UTC.
    timezone = db.Column(db.String(64), nullable=True)

    episodes = db.relationship('Episode', backref='user', lazy=True, cascade='all, delete-orphan')
    protocols = db.relationship('Protocol', backref='user', lazy=True, cascade='all, delete-orphan')
    symptoms = db.relationship('Symptom', backref='user', lazy=True, cascade='all, delete-orphan')
    experiments = db.relationship('Experiment', backref='user', lazy=True, cascade='all, delete-orphan')
    checkins = db.relationship('CheckIn', backref='user', lazy=True, cascade='all, delete-orphan')
    protocol_compliance = db.relationship('ProtocolCompliance', backref='user', lazy=True, cascade='all, delete-orphan')
    # Custom triggers only (user_id set); global triggers have user_id NULL and
    # belong to no user, so they never appear in any user's collection. Declared
    # for consistency with the other owned children and to make db.session.delete(user)
    # FK-safe. Note: delete_account uses bulk deletes (which bypass this cascade),
    # so it still removes custom triggers explicitly.
    triggers = db.relationship('Trigger', backref='user', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.name}>'


class Symptom(db.Model):
    __tablename__ = 'symptoms'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    baseline_score = db.Column(db.Integer, nullable=True)  # 1-10; null for binary
    # 'scale' (1-10 slider) or 'binary' (yes/no). Locked once any SymptomScore exists.
    input_type = db.Column(db.String(10), nullable=False, default='scale')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint("input_type IN ('scale', 'binary')",
                           name='symptoms_input_type_check'),
    )

    def __repr__(self):
        return f'<Symptom {self.name}>'


class Trigger(db.Model):
    """A trigger dimension for episodes. Hybrid model: rows with user_id IS NULL
    are the curated global seed list (shared, admin-curated); rows with a user_id
    are that user's custom triggers. Uniqueness is enforced by two partial unique
    indexes created in run_migrations() (globals unique by lower(name); customs
    unique per user by lower(name)) — the app-level match-and-link on the write
    path is primary, the indexes are the invariant backstop. Custom "deletion" is
    a soft-deactivate (is_active=False) so linked EpisodeTrigger history survives;
    globals are soft-retired the same way, never hard-deleted."""
    __tablename__ = 'triggers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # NULL = global seed
    name = db.Column(db.String(100), nullable=False)  # tag-like; deliberately tighter than Symptom.name (200)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        scope = 'global' if self.user_id is None else f'user={self.user_id}'
        return f'<Trigger {self.name} ({scope})>'


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
        scale_scores = [ss.score for ss in self.symptom_scores if ss.score is not None]
        if scale_scores:
            return max(scale_scores)
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
    # Exactly one of score / value_bool is populated, determined by Symptom.input_type.
    # Filtering on `score IS NOT NULL` excludes binary entries from scale-only aggregates;
    # filtering on `value_bool IS NOT NULL` excludes scale entries from binary aggregates.
    # PDF/CSV export should join Symptom and branch on input_type.
    score = db.Column(db.Integer, nullable=True)  # 1-10 for scale symptoms
    value_bool = db.Column(db.Boolean, nullable=True)  # True/False for binary symptoms

    symptom = db.relationship('Symptom')

    def __repr__(self):
        if self.value_bool is not None:
            return f'<SymptomScore episode={self.episode_id} symptom={self.symptom_id} bool={self.value_bool}>'
        return f'<SymptomScore episode={self.episode_id} symptom={self.symptom_id} score={self.score}>'


class EpisodeIntervention(db.Model):
    __tablename__ = 'episode_interventions'

    id = db.Column(db.Integer, primary_key=True)
    episode_id = db.Column(db.Integer, db.ForeignKey('episodes.id'), nullable=False)
    protocol_id = db.Column(db.Integer, db.ForeignKey('protocols.id'), nullable=False)
    effectiveness = db.Column(db.Integer, nullable=True)  # 1-10
    time_to_relief_hours = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    episode = db.relationship('Episode', backref=db.backref('interventions', lazy=True, cascade='all, delete-orphan'))
    protocol = db.relationship('Protocol')

    def __repr__(self):
        return f'<EpisodeIntervention episode={self.episode_id} protocol={self.protocol_id}>'


class EpisodeTrigger(db.Model):
    """Junction linking an episode to a Trigger (global or custom). Single clean
    FK to triggers regardless of the trigger's scope."""
    __tablename__ = 'episode_triggers'
    __table_args__ = (
        db.UniqueConstraint('episode_id', 'trigger_id', name='ux_episode_trigger'),
    )

    id = db.Column(db.Integer, primary_key=True)
    episode_id = db.Column(db.Integer, db.ForeignKey('episodes.id'), nullable=False)
    # No ondelete on trigger_id: triggers are only ever soft-deactivated
    # (is_active=False), never hard-deleted, so this FK never needs to cascade.
    # DB-level ondelete directives are a deliberate later pass (P2 backlog).
    trigger_id = db.Column(db.Integer, db.ForeignKey('triggers.id'), nullable=False)
    # Provenance: 'user' = picked/typed on the episode form; 'ai' = inferred by
    # AI check-in parsing. Added at the write-path increment while zero rows
    # existed, so no backfill guess. Editing the episode form re-links as 'user'
    # (a user review is a confirmation). Feeds the future AI trigger-analysis
    # item, which can weight confirmed vs inferred triggers differently.
    source = db.Column(db.String(10), nullable=False, default='user')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    episode = db.relationship('Episode', backref=db.backref('episode_triggers', lazy=True, cascade='all, delete-orphan'))
    trigger = db.relationship('Trigger')

    def __repr__(self):
        return f'<EpisodeTrigger episode={self.episode_id} trigger={self.trigger_id}>'


class Protocol(db.Model):
    __tablename__ = 'protocols'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    type = db.Column(db.String(20), nullable=False)  # preventative or rescue
    start_date = db.Column(db.Date, nullable=True)
    dose_frequency = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='active')  # active, paused, stopped, removed (rescue only)
    available = db.Column(db.Boolean, nullable=False, default=True)  # rescue options only
    why = db.Column(db.Text, nullable=True)  # "Why I'm doing this" — surfaced in compliance messaging
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
    __table_args__ = (
        db.UniqueConstraint('user_id', 'protocol_id', 'date', name='ux_protocol_compliance_day'),
    )

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


class UsedVerifyToken(db.Model):
    """Records consumed email-verification tokens to prevent replay within TTL."""
    __tablename__ = 'used_verify_tokens'

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    used_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class InviteCode(db.Model):
    __tablename__ = 'invite_codes'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    used_at = db.Column(db.DateTime, nullable=True)
    used_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    def __repr__(self):
        return f'<InviteCode {self.code}>'


class UserActivity(db.Model):
    """Lightweight event log for first-party usage analytics."""
    __tablename__ = 'user_activity'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    event_type = db.Column(db.String(50), nullable=False)  # signup, login, page_view
    detail = db.Column(db.String(200), nullable=True)       # e.g. endpoint name
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        db.Index('ix_user_activity_type_created', 'event_type', 'created_at'),
    )
