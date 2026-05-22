from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), default='user')
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime)
    usage_logs = db.relationship('UsageLog', backref='user', lazy=True, cascade='all, delete-orphan')

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
    def role_label(self):
        labels = {'superadmin': 'SuperAdmin', 'admin': 'Administrador', 'user': 'Usuario'}
        return labels.get(self.role, self.role)


class UsageLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    action = db.Column(db.String(100))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    detail = db.Column(db.String(256))


class SmnAlerta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    region = db.Column(db.String(100))
    severidad = db.Column(db.String(20))
    descripcion = db.Column(db.Text)
    fuente = db.Column(db.String(50), default='SMN')


class AiInforme(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    region = db.Column(db.String(100))
    severidad = db.Column(db.String(20))
    ha = db.Column(db.Float)
    pdf_path = db.Column(db.String(256))
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    analysis_text = db.Column(db.Text)
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    satellite = db.Column(db.String(50))
    conf = db.Column(db.String(20))
    fwi_val = db.Column(db.Float)
    tipo_foco = db.Column(db.String(30))


class FocoLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    region = db.Column(db.String(100))
    severidad = db.Column(db.String(20))
    fuente = db.Column(db.String(50))
    ha = db.Column(db.Float)
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    ai_analizado = db.Column(db.Boolean, default=False)
