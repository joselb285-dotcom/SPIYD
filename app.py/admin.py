from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy import func
import math
from models import db, User, UsageLog, SmnAlerta, AiInforme, FocoLog, Recurso, TIPOS_RECURSO
from datetime import datetime, timedelta

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('admin', 'superadmin'):
            flash('Acceso restringido a administradores', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    hoy = datetime.utcnow().date()
    inicio_hoy = datetime.combine(hoy, datetime.min.time())
    inicio_mes = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_users = User.query.filter_by(role='user').count()
    active_users = User.query.filter_by(role='user', active=True).count()

    smn_total = SmnAlerta.query.count()
    smn_hoy = SmnAlerta.query.filter(SmnAlerta.timestamp >= inicio_hoy).count()
    smn_recientes = SmnAlerta.query.order_by(SmnAlerta.timestamp.desc()).limit(8).all()

    ai_total = AiInforme.query.count()
    ai_mes = AiInforme.query.filter(AiInforme.timestamp >= inicio_mes).count()
    ai_recientes = AiInforme.query.order_by(AiInforme.timestamp.desc()).limit(8).all()

    focos_hoy = FocoLog.query.filter(FocoLog.timestamp >= inicio_hoy).count()
    focos_mes = FocoLog.query.filter(FocoLog.timestamp >= inicio_mes).count()
    focos_criticos = FocoLog.query.filter_by(severidad='critical').count()
    focos_altos = FocoLog.query.filter_by(severidad='high').count()
    focos_medios = FocoLog.query.filter_by(severidad='medium').count()

    fuente_counts = dict(
        db.session.query(FocoLog.fuente, func.count(FocoLog.id)).group_by(FocoLog.fuente).all()
    )

    logins_recientes = (
        db.session.query(UsageLog, User)
        .join(User, UsageLog.user_id == User.id)
        .filter(UsageLog.action.in_(['login', 'mapa']))
        .order_by(UsageLog.timestamp.desc())
        .limit(6)
        .all()
    )

    return render_template('admin/dashboard.html',
        total_users=total_users,
        active_users=active_users,
        smn_total=smn_total,
        smn_hoy=smn_hoy,
        smn_recientes=smn_recientes,
        ai_total=ai_total,
        ai_mes=ai_mes,
        ai_recientes=ai_recientes,
        focos_hoy=focos_hoy,
        focos_mes=focos_mes,
        focos_criticos=focos_criticos,
        focos_altos=focos_altos,
        focos_medios=focos_medios,
        fuente_counts=fuente_counts,
        logins_recientes=logins_recientes,
    )


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    search = request.args.get('q', '').strip()
    query = User.query.filter_by(role='user')
    if search:
        query = query.filter(
            (User.username.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%'))
        )
    all_users = query.order_by(User.created_at.desc()).all()
    return render_template('admin/users.html', users=all_users, search=search)


@admin_bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@admin_required
def user_new():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if not username or not email or not password:
            flash('Todos los campos son obligatorios', 'error')
        elif User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya existe', 'error')
        elif User.query.filter_by(email=email).first():
            flash('El email ya está registrado', 'error')
        else:
            user = User(username=username, email=email, role='user')
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'Usuario {username} creado exitosamente', 'success')
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', user=None, action='new')


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def user_edit(user_id):
    user = db.get_or_404(User, user_id)
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        active = 'active' in request.form
        new_password = request.form.get('password', '').strip()
        existing = User.query.filter(User.email == email, User.id != user_id).first()
        if existing:
            flash('El email ya está en uso por otro usuario', 'error')
        else:
            user.email = email
            user.active = active
            if new_password:
                user.set_password(new_password)
            db.session.commit()
            flash(f'Usuario {user.username} actualizado', 'success')
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', user=user, action='edit')


@admin_bp.route('/recursos')
@login_required
@admin_required
def recursos():
    tipo_filter = request.args.get('tipo', '').strip()
    search = request.args.get('q', '').strip()
    query = Recurso.query
    if tipo_filter:
        query = query.filter_by(tipo=tipo_filter)
    if search:
        query = query.filter(
            Recurso.nombre.ilike(f'%{search}%') |
            Recurso.localidad.ilike(f'%{search}%') |
            Recurso.provincia_departamento.ilike(f'%{search}%')
        )
    all_recursos = query.order_by(Recurso.tipo, Recurso.nombre).all()
    return render_template('admin/recursos.html',
                           recursos=all_recursos, tipos=TIPOS_RECURSO,
                           tipo_filter=tipo_filter, search=search)


@admin_bp.route('/recursos/new', methods=['GET', 'POST'])
@login_required
@admin_required
def recurso_new():
    if request.method == 'POST':
        f = request.form
        lat_str = f.get('lat', '').strip()
        lon_str = f.get('lon', '').strip()
        try:
            lat_val = float(lat_str) if lat_str else None
            lon_val = float(lon_str) if lon_str else None
        except ValueError:
            flash('Coordenadas inválidas', 'error')
            return render_template('admin/recurso_form.html', recurso=None, action='new', tipos=TIPOS_RECURSO)
        recurso = Recurso(
            tipo=f.get('tipo', 'otro'),
            nombre=f.get('nombre', '').strip(),
            descripcion=f.get('descripcion', '').strip() or None,
            pais=f.get('pais', '').strip() or None,
            provincia_departamento=f.get('provincia_departamento', '').strip() or None,
            localidad=f.get('localidad', '').strip() or None,
            direccion=f.get('direccion', '').strip() or None,
            lat=lat_val, lon=lon_val,
            telefono=f.get('telefono', '').strip() or None,
            email=f.get('email', '').strip() or None,
            contacto_nombre=f.get('contacto_nombre', '').strip() or None,
            horario=f.get('horario', '').strip() or None,
            notas=f.get('notas', '').strip() or None,
            activo='activo' in f,
            created_by=current_user.id,
        )
        db.session.add(recurso)
        db.session.commit()
        flash(f'Recurso "{recurso.nombre}" creado', 'success')
        return redirect(url_for('admin.recursos'))
    return render_template('admin/recurso_form.html', recurso=None, action='new', tipos=TIPOS_RECURSO)


@admin_bp.route('/recursos/<int:recurso_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def recurso_edit(recurso_id):
    recurso = db.get_or_404(Recurso, recurso_id)
    if request.method == 'POST':
        f = request.form
        lat_str = f.get('lat', '').strip()
        lon_str = f.get('lon', '').strip()
        try:
            lat_val = float(lat_str) if lat_str else None
            lon_val = float(lon_str) if lon_str else None
        except ValueError:
            flash('Coordenadas inválidas', 'error')
            return render_template('admin/recurso_form.html', recurso=recurso, action='edit', tipos=TIPOS_RECURSO)
        recurso.tipo = f.get('tipo', recurso.tipo)
        recurso.nombre = f.get('nombre', '').strip()
        recurso.descripcion = f.get('descripcion', '').strip() or None
        recurso.pais = f.get('pais', '').strip() or None
        recurso.provincia_departamento = f.get('provincia_departamento', '').strip() or None
        recurso.localidad = f.get('localidad', '').strip() or None
        recurso.direccion = f.get('direccion', '').strip() or None
        recurso.lat = lat_val
        recurso.lon = lon_val
        recurso.telefono = f.get('telefono', '').strip() or None
        recurso.email = f.get('email', '').strip() or None
        recurso.contacto_nombre = f.get('contacto_nombre', '').strip() or None
        recurso.horario = f.get('horario', '').strip() or None
        recurso.notas = f.get('notas', '').strip() or None
        recurso.activo = 'activo' in f
        db.session.commit()
        flash(f'Recurso "{recurso.nombre}" actualizado', 'success')
        return redirect(url_for('admin.recursos'))
    return render_template('admin/recurso_form.html', recurso=recurso, action='edit', tipos=TIPOS_RECURSO)


@admin_bp.route('/recursos/<int:recurso_id>/delete', methods=['POST'])
@login_required
@admin_required
def recurso_delete(recurso_id):
    recurso = db.get_or_404(Recurso, recurso_id)
    nombre = recurso.nombre
    db.session.delete(recurso)
    db.session.commit()
    flash(f'Recurso "{nombre}" eliminado', 'success')
    return redirect(url_for('admin.recursos'))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('No puedes eliminar tu propio usuario', 'error')
        return redirect(url_for('admin.users'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {username} eliminado', 'success')
    return redirect(url_for('admin.users'))
