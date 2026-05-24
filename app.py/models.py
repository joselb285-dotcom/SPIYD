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
    pais = db.Column(db.String(50))
    region_tipo = db.Column(db.String(30))
    region_nombre = db.Column(db.String(100))
    institucion_nombre = db.Column(db.String(150))
    institucion_titulo = db.Column(db.String(150))
    institucion_logo = db.Column(db.Text)
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
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    detail = db.Column(db.String(256))


class SmnAlerta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    region = db.Column(db.String(100))
    severidad = db.Column(db.String(20), index=True)
    descripcion = db.Column(db.Text)
    fuente = db.Column(db.String(50), default='SMN')


class AiInforme(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
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


TIPOS_RECURSO = [
    ('bomberos',       'Cuartel de Bomberos'),
    ('defensa_civil',  'Defensa Civil'),
    ('hospital',       'Hospital'),
    ('caps',           'CAPS / Centro de Salud'),
    ('avion',          'Avión / Aeronave'),
    ('gubernamental',  'Ente Gubernamental'),
    ('policia',        'Policía / Seguridad'),
    ('municipio',      'Municipio / Intendencia'),
    ('otro',           'Otro'),
]

class Recurso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False)
    nombre = db.Column(db.String(150), nullable=False)
    descripcion = db.Column(db.Text)
    pais = db.Column(db.String(50))
    provincia_departamento = db.Column(db.String(100))
    localidad = db.Column(db.String(100))
    direccion = db.Column(db.String(200))
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    telefono = db.Column(db.String(50))
    email = db.Column(db.String(120))
    contacto_nombre = db.Column(db.String(100))
    horario = db.Column(db.String(100))
    notas = db.Column(db.Text)
    activo = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    @property
    def tipo_label(self):
        return dict(TIPOS_RECURSO).get(self.tipo, self.tipo)


class FocoLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    region = db.Column(db.String(100))
    severidad = db.Column(db.String(20), index=True)
    fuente = db.Column(db.String(50))
    ha = db.Column(db.Float)
    lat = db.Column(db.Float)
    lon = db.Column(db.Float)
    ai_analizado = db.Column(db.Boolean, default=False)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    action = db.Column(db.String(50), index=True)
    target_type = db.Column(db.String(30))
    target_id = db.Column(db.Integer)
    detail = db.Column(db.Text)
