from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

PLAN_PRICES = {'basic': 0.0, 'pro': 29.0, 'enterprise': 99.0}


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user')
    active = db.Column(db.Boolean, default=True)
    plan = db.Column(db.String(20), default='basic')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    usage_logs = db.relationship('UsageLog', backref='user', lazy=True, cascade='all, delete-orphan')
    invoices = db.relationship('Invoice', backref='user', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return str(self.id)

    @property
    def is_active(self):
        return self.active

    @property
    def monthly_amount(self):
        return PLAN_PRICES.get(self.plan, 0.0)

    @property
    def plan_label(self):
        labels = {'basic': 'Básico', 'pro': 'Pro', 'enterprise': 'Enterprise'}
        return labels.get(self.plan, self.plan)

    @property
    def role_label(self):
        return 'Administrador' if self.role == 'admin' else 'Usuario'


class UsageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    cost = db.Column(db.Float, default=0.0)


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    period = db.Column(db.String(7))
    amount = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime)

    @property
    def status_label(self):
        labels = {'pending': 'Pendiente', 'paid': 'Pagado', 'overdue': 'Vencido'}
        return labels.get(self.status, self.status)

    @property
    def status_class(self):
        classes = {'pending': 'warning', 'paid': 'success', 'overdue': 'danger'}
        return classes.get(self.status, 'secondary')
