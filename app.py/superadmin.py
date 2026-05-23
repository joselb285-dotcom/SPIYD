from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy import func
from models import db, User, UsageLog, SmnAlerta, AiInforme, FocoLog
from datetime import datetime, timedelta

superadmin_bp = Blueprint('superadmin', __name__)

ROLES = ['user', 'admin', 'superadmin']

PROVINCIAS_ARG = [
    'Buenos Aires', 'Catamarca', 'Chaco', 'Chubut', 'Ciudad Autónoma de Buenos Aires',
    'Córdoba', 'Corrientes', 'Entre Ríos', 'Formosa', 'Jujuy', 'La Pampa', 'La Rioja',
    'Mendoza', 'Misiones', 'Neuquén', 'Río Negro', 'Salta', 'San Juan', 'San Luis',
    'Santa Cruz', 'Santa Fe', 'Santiago del Estero', 'Tierra del Fuego', 'Tucumán',
]

DEPARTAMENTOS_PRY = [
    'Alto Paraguay', 'Alto Paraná', 'Amambay', 'Asunción', 'Boquerón', 'Caaguazú',
    'Caazapá', 'Canindeyú', 'Central', 'Concepción', 'Cordillera', 'Guairá',
    'Itapúa', 'Misiones', 'Ñeembucú', 'Paraguarí', 'Presidente Hayes', 'San Pedro',
]


def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'superadmin':
            flash('Acceso restringido a superadministradores', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


@superadmin_bp.route('/')
@login_required
@superadmin_required
def dashboard():
    hoy = datetime.utcnow().date()
    inicio_hoy = datetime.combine(hoy, datetime.min.time())
    total_users = User.query.count()
    active_users = User.query.filter_by(active=True).count()
    admins = User.query.filter_by(role='admin').count()
    superadmins = User.query.filter_by(role='superadmin').count()
    role_counts = dict(
        db.session.query(User.role, func.count(User.id)).group_by(User.role).all()
    )
    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()
    admin_list = User.query.filter_by(role='admin').order_by(User.created_at.desc()).all()
    smn_total = SmnAlerta.query.count()
    ai_total = AiInforme.query.count()
    focos_total = FocoLog.query.count()
    focos_hoy = FocoLog.query.filter(FocoLog.timestamp >= inicio_hoy).count()
    return render_template('superadmin/dashboard.html',
        total_users=total_users,
        active_users=active_users,
        admins=admins,
        superadmins=superadmins,
        role_counts=role_counts,
        recent_users=recent_users,
        admin_list=admin_list,
        smn_total=smn_total,
        ai_total=ai_total,
        focos_total=focos_total,
        focos_hoy=focos_hoy,
    )


@superadmin_bp.route('/users')
@login_required
@superadmin_required
def users():
    search = request.args.get('q', '').strip()
    role_filter = request.args.get('role', '').strip()
    query = User.query
    if search:
        query = query.filter(
            (User.username.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%'))
        )
    if role_filter:
        query = query.filter_by(role=role_filter)
    all_users = query.order_by(User.created_at.desc()).all()
    return render_template('superadmin/users.html', users=all_users, search=search, role_filter=role_filter, roles=ROLES)


@superadmin_bp.route('/users/new', methods=['GET', 'POST'])
@login_required
@superadmin_required
def user_new():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role = request.form.get('role', 'user')
        plan = request.form.get('plan', 'basic')
        if not username or not email or not password:
            flash('Todos los campos son obligatorios', 'error')
        elif User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya existe', 'error')
        elif User.query.filter_by(email=email).first():
            flash('El email ya está registrado', 'error')
        else:
            pais = request.form.get('pais', '').strip() or None
            region_tipo = request.form.get('region_tipo', 'pais').strip()
            region_nombre = request.form.get('region_nombre', '').strip() or None
            if region_tipo == 'pais':
                region_nombre = None
            user = User(username=username, email=email, role=role or 'user',
                        pais=pais, region_tipo=region_tipo, region_nombre=region_nombre)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'Usuario {username} creado exitosamente', 'success')
            return redirect(url_for('superadmin.users'))
    return render_template('superadmin/user_form.html', user=None, action='new', roles=ROLES,
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


@superadmin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@superadmin_required
def user_edit(user_id):
    user = db.get_or_404(User, user_id)
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        role = request.form.get('role', user.role)
        active = 'active' in request.form
        new_password = request.form.get('password', '').strip()
        existing = User.query.filter(User.email == email, User.id != user_id).first()
        if existing:
            flash('El email ya está en uso por otro usuario', 'error')
        else:
            pais = request.form.get('pais', '').strip() or None
            region_tipo = request.form.get('region_tipo', 'pais').strip()
            region_nombre = request.form.get('region_nombre', '').strip() or None
            if region_tipo == 'pais':
                region_nombre = None
            user.email = email
            user.role = role
            user.active = active
            user.pais = pais
            user.region_tipo = region_tipo
            user.region_nombre = region_nombre
            if new_password:
                user.set_password(new_password)
            db.session.commit()
            flash(f'Usuario {user.username} actualizado', 'success')
            return redirect(url_for('superadmin.users'))
    return render_template('superadmin/user_form.html', user=user, action='edit', roles=ROLES,
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


@superadmin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@superadmin_required
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('No podés eliminarte a vos mismo', 'error')
        return redirect(url_for('superadmin.users'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {username} eliminado', 'success')
    return redirect(url_for('superadmin.users'))


@superadmin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@superadmin_required
def user_toggle(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('No podés desactivarte a vos mismo', 'error')
        return redirect(url_for('superadmin.users'))
    user.active = not user.active
    db.session.commit()
    estado = 'activado' if user.active else 'desactivado'
    flash(f'Usuario {user.username} {estado}', 'success')
    return redirect(url_for('superadmin.users'))
