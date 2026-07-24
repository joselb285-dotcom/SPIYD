import base64
import secrets
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy import func
from models import db, User, UsageLog, SmnAlerta, AiInforme, FocoLog
from datetime import datetime, timedelta
from email_utils import enviar_email_verificacion

superadmin_bp = Blueprint('superadmin', __name__)

ROLES = ['user', 'admin', 'superadmin']
ADMIN_ROLES = ['admin', 'superadmin']

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


_ALLOWED_LOGO_MIMES = {'image/png', 'image/jpeg', 'image/webp', 'image/gif'}

def _procesar_logo(file_storage):
    """Convierte un FileStorage a data URL base64. Retorna None si no hay archivo válido."""
    if not file_storage or file_storage.filename == '':
        return None
    mime = (file_storage.content_type or '').split(';')[0].strip().lower()
    if mime not in _ALLOWED_LOGO_MIMES:
        return None
    data = file_storage.read()
    if len(data) > 300 * 1024:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


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
    search_admin = request.args.get('qa', '').strip()
    admin_q = User.query.filter(User.role.in_(['admin', 'superadmin']))
    if search_admin:
        admin_q = admin_q.filter(
            (User.username.ilike(f'%{search_admin}%')) | (User.email.ilike(f'%{search_admin}%'))
        )
    admin_list = admin_q.order_by(User.created_at.desc()).all()

    user_counts = dict(
        db.session.query(User.created_by_admin, func.count(User.id))
        .filter(User.role == 'user')
        .group_by(User.created_by_admin).all()
    )

    all_users = User.query.filter_by(role='user').order_by(User.created_at.desc()).all()
    users_by_admin = {}
    unassigned_users = []
    for u in all_users:
        if u.created_by_admin:
            users_by_admin.setdefault(u.created_by_admin, []).append(u)
        else:
            unassigned_users.append(u)

    total_users = len(all_users)
    active_users = sum(1 for u in all_users if u.active)
    pending_verif = sum(1 for u in all_users if not u.email_verified)
    admins = sum(1 for a in admin_list if a.role == 'admin')
    superadmins = sum(1 for a in admin_list if a.role == 'superadmin')

    return render_template('superadmin/dashboard.html',
        admin_list=admin_list,
        user_counts=user_counts,
        users_by_admin=users_by_admin,
        unassigned_users=unassigned_users,
        total_users=total_users,
        active_users=active_users,
        pending_verif=pending_verif,
        admins=admins,
        superadmins=superadmins,
        search_admin=search_admin,
    )


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
        elif role not in ROLES:
            flash('Rol inválido', 'error')
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
            logo = _procesar_logo(request.files.get('institucion_logo'))
            user = User(
                username=username, email=email, role=role or 'user',
                pais=pais, region_tipo=region_tipo, region_nombre=region_nombre,
                institucion_nombre=request.form.get('institucion_nombre', '').strip() or None,
                institucion_titulo=request.form.get('institucion_titulo', '').strip() or None,
                institucion_logo=logo,
                email_verified=False, email_verify_token=secrets.token_urlsafe(32),
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            enviar_email_verificacion(user, request.url_root)
            flash(f'Usuario {username} creado exitosamente. Se envió un email de verificación.', 'success')
            return redirect(url_for('superadmin.dashboard'))
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
        if role not in ROLES:
            flash('Rol inválido', 'error')
            return render_template('superadmin/user_form.html', user=user, action='edit', roles=ROLES,
                                   provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)
        existing = User.query.filter(User.email == email, User.id != user_id).first()
        if existing:
            flash('El email ya está en uso por otro usuario', 'error')
        else:
            pais = request.form.get('pais', '').strip() or None
            region_tipo = request.form.get('region_tipo', 'pais').strip()
            region_nombre = request.form.get('region_nombre', '').strip() or None
            if region_tipo == 'pais':
                region_nombre = None
            nuevo_logo = _procesar_logo(request.files.get('institucion_logo'))
            user.email = email
            user.role = role
            user.active = active
            user.pais = pais
            user.region_tipo = region_tipo
            user.region_nombre = region_nombre
            user.institucion_nombre = request.form.get('institucion_nombre', '').strip() or None
            user.institucion_titulo = request.form.get('institucion_titulo', '').strip() or None
            if nuevo_logo:
                user.institucion_logo = nuevo_logo
            elif 'quitar_logo' in request.form:
                user.institucion_logo = None
            if new_password:
                user.set_password(new_password)
            db.session.commit()
            flash(f'Usuario {user.username} actualizado', 'success')
            return redirect(url_for('superadmin.dashboard'))
    return render_template('superadmin/user_form.html', user=user, action='edit', roles=ROLES,
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


@superadmin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@superadmin_required
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('No podés eliminarte a vos mismo', 'error')
        return redirect(url_for('superadmin.dashboard'))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {username} eliminado', 'success')
    return redirect(url_for('superadmin.dashboard'))


@superadmin_bp.route('/users/<int:user_id>/toggle', methods=['POST'])
@login_required
@superadmin_required
def user_toggle(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('No podés desactivarte a vos mismo', 'error')
        return redirect(url_for('superadmin.dashboard'))
    user.active = not user.active
    db.session.commit()
    estado = 'activado' if user.active else 'desactivado'
    flash(f'Usuario {user.username} {estado}', 'success')
    return redirect(url_for('superadmin.dashboard'))


@superadmin_bp.route('/users/<int:user_id>/verify', methods=['POST'])
@login_required
@superadmin_required
def user_verify(user_id):
    user = db.get_or_404(User, user_id)
    user.email_verified = True
    user.email_verify_token = None
    db.session.commit()
    flash(f'{user.username} verificado manualmente', 'success')
    return redirect(url_for('superadmin.dashboard'))


# ── Gestión de Administradores ───────────────────────────────────────────────


@superadmin_bp.route('/admins/new', methods=['GET', 'POST'])
@login_required
@superadmin_required
def admin_new():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email    = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        role     = request.form.get('role', 'admin')
        if role not in ('admin', 'superadmin'):
            role = 'admin'
        if not username or not email or not password:
            flash('Todos los campos son obligatorios', 'error')
        elif User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya existe', 'error')
        elif User.query.filter_by(email=email).first():
            flash('El email ya está registrado', 'error')
        else:
            pais         = request.form.get('pais', '').strip() or None
            region_tipo  = request.form.get('region_tipo', 'pais').strip()
            region_nombre= request.form.get('region_nombre', '').strip() or None
            if region_tipo == 'pais':
                region_nombre = None
            logo = _procesar_logo(request.files.get('institucion_logo'))
            user = User(
                username=username, email=email, role=role,
                pais=pais, region_tipo=region_tipo, region_nombre=region_nombre,
                institucion_nombre=request.form.get('institucion_nombre','').strip() or None,
                institucion_titulo=request.form.get('institucion_titulo','').strip() or None,
                institucion_logo=logo,
                email_verified=False, email_verify_token=secrets.token_urlsafe(32),
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            enviar_email_verificacion(user, request.url_root)
            flash(f'Administrador {username} creado exitosamente. Se envió un email de verificación.', 'success')
            return redirect(url_for('superadmin.dashboard'))
    return render_template('superadmin/admin_form.html', admin=None, action='new',
                           roles=ADMIN_ROLES,
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


@superadmin_bp.route('/admins/<int:admin_id>/edit', methods=['GET', 'POST'])
@login_required
@superadmin_required
def admin_edit(admin_id):
    admin = db.get_or_404(User, admin_id)
    if admin.role not in ('admin', 'superadmin'):
        flash('El usuario no es administrador', 'error')
        return redirect(url_for('superadmin.dashboard'))
    if request.method == 'POST':
        email        = request.form.get('email', '').strip()
        role         = request.form.get('role', admin.role)
        active       = 'active' in request.form
        new_password = request.form.get('password', '').strip()
        if role not in ('admin', 'superadmin'):
            flash('Rol inválido para administrador', 'error')
        elif User.query.filter(User.email == email, User.id != admin_id).first():
            flash('El email ya está en uso', 'error')
        else:
            pais         = request.form.get('pais', '').strip() or None
            region_tipo  = request.form.get('region_tipo', 'pais').strip()
            region_nombre= request.form.get('region_nombre', '').strip() or None
            if region_tipo == 'pais':
                region_nombre = None
            nuevo_logo = _procesar_logo(request.files.get('institucion_logo'))
            admin.email             = email
            admin.role              = role
            admin.active            = active
            admin.pais              = pais
            admin.region_tipo       = region_tipo
            admin.region_nombre     = region_nombre
            admin.institucion_nombre= request.form.get('institucion_nombre','').strip() or None
            admin.institucion_titulo= request.form.get('institucion_titulo','').strip() or None
            if nuevo_logo:
                admin.institucion_logo = nuevo_logo
            elif 'quitar_logo' in request.form:
                admin.institucion_logo = None
            if new_password:
                admin.set_password(new_password)
            db.session.commit()
            flash(f'Administrador {admin.username} actualizado', 'success')
            return redirect(url_for('superadmin.dashboard'))
    return render_template('superadmin/admin_form.html', admin=admin, action='edit',
                           roles=ADMIN_ROLES,
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


@superadmin_bp.route('/admins/<int:admin_id>/delete', methods=['POST'])
@login_required
@superadmin_required
def admin_delete(admin_id):
    admin = db.get_or_404(User, admin_id)
    if admin.id == current_user.id:
        flash('No podés eliminarte a vos mismo', 'error')
        return redirect(url_for('superadmin.dashboard'))
    if admin.role not in ('admin', 'superadmin'):
        flash('El usuario no es administrador', 'error')
        return redirect(url_for('superadmin.dashboard'))
    # Desvincular usuarios que pertenecían a este admin
    User.query.filter_by(created_by_admin=admin.id).update({'created_by_admin': None})
    username = admin.username
    db.session.delete(admin)
    db.session.commit()
    flash(f'Administrador {username} eliminado. Sus usuarios quedaron sin asignar.', 'success')
    return redirect(url_for('superadmin.dashboard'))


@superadmin_bp.route('/admins/<int:admin_id>/toggle', methods=['POST'])
@login_required
@superadmin_required
def admin_toggle(admin_id):
    admin = db.get_or_404(User, admin_id)
    if admin.id == current_user.id:
        flash('No podés desactivarte a vos mismo', 'error')
        return redirect(url_for('superadmin.dashboard'))
    admin.active = not admin.active
    db.session.commit()
    estado = 'activado' if admin.active else 'desactivado'
    flash(f'Administrador {admin.username} {estado}', 'success')
    return redirect(url_for('superadmin.dashboard'))
