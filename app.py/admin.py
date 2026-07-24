from flask import Blueprint, render_template, redirect, url_for, request, flash, Response
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy import func, or_
import csv, io, math, secrets
from models import db, User, UsageLog, SmnAlerta, AiInforme, FocoLog, Recurso, TIPOS_RECURSO, AuditLog, UnidadRecurso, TIPOS_UNIDAD
from superadmin import PROVINCIAS_ARG, DEPARTAMENTOS_PRY
from email_utils import enviar_email_verificacion
from datetime import datetime, timedelta
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

admin_bp = Blueprint('admin', __name__)


def _pais_condition(model, pais):
    if pais == 'argentina':
        return or_(model.region.is_(None), ~model.region.ilike('%paraguay%'))
    if pais == 'paraguay':
        return model.region.ilike('%paraguay%')
    if pais == 'chile':
        return model.region.ilike('%chile%')  # sin cobertura de focos en Chile por ahora
    return None


def _geo_filter(query, model):
    """Filtra una query por el alcance geográfico del current_user (paises + provincia/departamento).
    Solo se aplica cuando el admin tiene paises configurados; sin restricción (o ambos paises) ve todo."""
    u = current_user
    paises = u.paises_list
    if paises:
        conds = [c for c in (_pais_condition(model, p) for p in paises) if c is not None]
        if conds:
            query = query.filter(or_(*conds))
    if len(paises) == 1 and u.region_tipo in ('provincia', 'departamento') and u.region_nombre:
        query = query.filter(model.region.ilike(f'%{u.region_nombre}%'))
    return query


PAISES_VALIDOS = ('argentina', 'paraguay', 'chile')


def _parse_pais_scope(form):
    """Lee los checkboxes 'pais' (uno o varios) y el alcance regional del form.
    El alcance por provincia/departamento solo tiene sentido si se eligió un único país."""
    paises = [p for p in form.getlist('pais') if p in PAISES_VALIDOS]
    pais = ','.join(paises) or None
    region_tipo = form.get('region_tipo', 'pais').strip()
    region_nombre = form.get('region_nombre', '').strip() or None
    if len(paises) != 1 or region_tipo == 'pais':
        region_tipo = 'pais'
        region_nombre = None
    return pais, region_tipo, region_nombre


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('admin', 'superadmin'):
            flash('Acceso restringido a administradores', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def _own_id():
    """Retorna current_user.id para admins; None para superadmins (sin restricción)."""
    return None if current_user.role == 'superadmin' else current_user.id


def _parse_trial_fields(form):
    """Lee trial_expires_at (date) y ai_informes_max (int) de un form. Vacío = sin límite."""
    trial_str = form.get('trial_expires_at', '').strip()
    trial_expires_at = None
    if trial_str:
        try:
            trial_expires_at = datetime.strptime(trial_str, '%Y-%m-%d') + timedelta(days=1) - timedelta(seconds=1)
        except ValueError:
            pass
    ai_max_str = form.get('ai_informes_max', '').strip()
    ai_informes_max = int(ai_max_str) if ai_max_str.isdigit() else None
    return trial_expires_at, ai_informes_max


def _audit(action, target_type, target_id, detail=''):
    try:
        db.session.add(AuditLog(
            user_id=current_user.id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=(detail or '')[:500]
        ))
    except Exception:
        pass


@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    hoy = datetime.utcnow().date()
    inicio_hoy = datetime.combine(hoy, datetime.min.time())
    inicio_mes = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    own = _own_id()

    # Usuarios — solo los del admin
    users_q = User.query.filter_by(role='user')
    if own:
        users_q = users_q.filter_by(created_by_admin=own)
    total_users = users_q.count()
    active_users = users_q.filter_by(active=True).count()

    # SMN — datos de entorno, filtrado geográfico (no por admin)
    smn_q = _geo_filter(SmnAlerta.query, SmnAlerta)
    smn_total = smn_q.count()
    smn_hoy = smn_q.filter(SmnAlerta.timestamp >= inicio_hoy).count()
    smn_recientes = smn_q.order_by(SmnAlerta.timestamp.desc()).limit(8).all()

    # IA — solo informes generados por este admin
    ai_q = AiInforme.query
    if own:
        ai_q = ai_q.filter_by(user_id=own)
    ai_total = ai_q.count()
    ai_mes = ai_q.filter(AiInforme.timestamp >= inicio_mes).count()
    ai_recientes = ai_q.order_by(AiInforme.timestamp.desc()).limit(8).all()

    # Focos — datos de entorno, filtrado geográfico
    foco_q = _geo_filter(FocoLog.query, FocoLog)
    focos_hoy = foco_q.filter(FocoLog.timestamp >= inicio_hoy).count()
    focos_mes = foco_q.filter(FocoLog.timestamp >= inicio_mes).count()
    focos_criticos = foco_q.filter_by(severidad='critical').count()
    focos_altos = foco_q.filter_by(severidad='high').count()
    focos_medios = foco_q.filter_by(severidad='medium').count()

    fuente_counts = dict(
        _geo_filter(db.session.query(FocoLog.fuente, func.count(FocoLog.id)), FocoLog)
        .group_by(FocoLog.fuente).all()
    )

    ultima_focos = foco_q.order_by(FocoLog.timestamp.desc()).first()
    ultima_actualizacion = ultima_focos.timestamp if ultima_focos else None

    # Logins recientes — solo los usuarios de este admin + el admin mismo
    login_q = (
        db.session.query(UsageLog, User)
        .join(User, UsageLog.user_id == User.id)
        .filter(UsageLog.action.in_(['login', 'mapa']))
    )
    if own:
        login_q = login_q.filter(
            or_(UsageLog.user_id == own,
                User.created_by_admin == own)
        )
    logins_recientes = login_q.order_by(UsageLog.timestamp.desc()).limit(6).all()

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
        ultima_actualizacion=ultima_actualizacion,
    )


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    search = request.args.get('q', '').strip()
    page   = request.args.get('page', 1, type=int)
    # superadmin ve todos; admin solo ve sus propios usuarios
    query = User.query.filter_by(role='user')
    if current_user.role != 'superadmin':
        query = query.filter_by(created_by_admin=current_user.id)
    if search:
        query = query.filter(
            (User.username.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%'))
        )
    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=30, error_out=False)
    ids = [u.id for u in pagination.items]
    ai_counts = dict(
        db.session.query(AiInforme.user_id, func.count(AiInforme.id))
        .filter(AiInforme.user_id.in_(ids)).group_by(AiInforme.user_id).all()
    ) if ids else {}
    return render_template('admin/users.html', users=pagination.items,
                           pagination=pagination, search=search, ai_counts=ai_counts)


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
            pais, region_tipo, region_nombre = _parse_pais_scope(request.form)
            admin_id = current_user.id if current_user.role != 'superadmin' else None
            trial_expires_at, ai_informes_max = _parse_trial_fields(request.form)
            user = User(username=username, email=email, role='user',
                        pais=pais, region_tipo=region_tipo, region_nombre=region_nombre,
                        created_by_admin=admin_id,
                        trial_expires_at=trial_expires_at, ai_informes_max=ai_informes_max,
                        email_verified=False, email_verify_token=secrets.token_urlsafe(32))
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            if enviar_email_verificacion(user, request.url_root):
                flash(f'Usuario {username} creado exitosamente. Se envió un email de verificación.', 'success')
            else:
                flash(f'Usuario {username} creado, pero el email de verificación no pudo enviarse (revisá la configuración SMTP). Podés verificarlo manualmente desde la lista de usuarios.', 'warning')
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', user=None, action='new',
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


@admin_bp.route('/users/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def user_edit(user_id):
    user = db.get_or_404(User, user_id)
    if current_user.role != 'superadmin' and user.created_by_admin != current_user.id:
        flash('No tenés permiso para editar este usuario', 'error')
        return redirect(url_for('admin.users'))
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        active = 'active' in request.form
        new_password = request.form.get('password', '').strip()
        existing = User.query.filter(User.email == email, User.id != user_id).first()
        if existing:
            flash('El email ya está en uso por otro usuario', 'error')
        else:
            pais, region_tipo, region_nombre = _parse_pais_scope(request.form)
            trial_expires_at, ai_informes_max = _parse_trial_fields(request.form)
            user.email = email
            user.active = active
            user.pais = pais
            user.region_tipo = region_tipo
            user.region_nombre = region_nombre
            user.trial_expires_at = trial_expires_at
            user.ai_informes_max = ai_informes_max
            if new_password:
                user.set_password(new_password)
            _audit('edit_user', 'User', user_id, f'email={email} active={active} pais={pais}')
            db.session.commit()
            flash(f'Usuario {user.username} actualizado', 'success')
            return redirect(url_for('admin.users'))
    ai_informes_count = AiInforme.query.filter_by(user_id=user.id).count()
    return render_template('admin/user_form.html', user=user, action='edit',
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY,
                           ai_informes_count=ai_informes_count)


@admin_bp.route('/recursos')
@login_required
@admin_required
def recursos():
    tipo_filter = request.args.get('tipo', '').strip()
    pais_filter = request.args.get('pais', '').strip()
    search = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    query = Recurso.query
    own = _own_id()
    if own:
        query = query.filter_by(created_by=own)
    if tipo_filter:
        query = query.filter_by(tipo=tipo_filter)
    if pais_filter:
        query = query.filter_by(pais=pais_filter)
    if search:
        query = query.filter(
            Recurso.nombre.ilike(f'%{search}%') |
            Recurso.localidad.ilike(f'%{search}%') |
            Recurso.provincia_departamento.ilike(f'%{search}%')
        )
    pagination = query.order_by(Recurso.tipo, Recurso.nombre).paginate(page=page, per_page=50, error_out=False)
    return render_template('admin/recursos.html',
                           recursos=pagination.items, pagination=pagination,
                           tipos=TIPOS_RECURSO, tipo_filter=tipo_filter,
                           pais_filter=pais_filter, search=search)


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
    own = _own_id()
    if own and recurso.created_by != own:
        flash('No tenés permiso para editar este recurso', 'error')
        return redirect(url_for('admin.recursos'))
    if request.method == 'POST':
        f = request.form
        lat_str = f.get('lat', '').strip()
        lon_str = f.get('lon', '').strip()
        try:
            lat_val = float(lat_str) if lat_str else None
            lon_val = float(lon_str) if lon_str else None
        except ValueError:
            flash('Coordenadas inválidas', 'error')
            return render_template('admin/recurso_form.html', recurso=recurso, action='edit',
                                   tipos=TIPOS_RECURSO, tipos_unidad=TIPOS_UNIDAD)
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
        _audit('edit_recurso', 'Recurso', recurso_id, f'nombre={recurso.nombre} activo={recurso.activo}')
        db.session.commit()
        flash(f'Recurso "{recurso.nombre}" actualizado', 'success')
        return redirect(url_for('admin.recursos'))
    return render_template('admin/recurso_form.html', recurso=recurso, action='edit',
                           tipos=TIPOS_RECURSO, tipos_unidad=TIPOS_UNIDAD)


@admin_bp.route('/recursos/<int:recurso_id>/delete', methods=['POST'])
@login_required
@admin_required
def recurso_delete(recurso_id):
    recurso = db.get_or_404(Recurso, recurso_id)
    own = _own_id()
    if own and recurso.created_by != own:
        flash('No tenés permiso para eliminar este recurso', 'error')
        return redirect(url_for('admin.recursos'))
    nombre = recurso.nombre
    _audit('delete_recurso', 'Recurso', recurso_id, f'nombre={nombre}')
    db.session.delete(recurso)
    db.session.commit()
    flash(f'Recurso "{nombre}" eliminado', 'success')
    return redirect(url_for('admin.recursos'))


@admin_bp.route('/ai-informes')
@login_required
@admin_required
def ai_informes():
    page = request.args.get('page', 1, type=int)
    tipo = request.args.get('tipo', '').strip()
    severidad = request.args.get('severidad', '').strip()
    region_q = request.args.get('region', '').strip()
    own = _own_id()
    query = AiInforme.query
    if own:
        query = query.filter_by(user_id=own)
    if tipo:
        query = query.filter_by(tipo_foco=tipo)
    if severidad:
        query = query.filter_by(severidad=severidad)
    if region_q:
        query = query.filter(AiInforme.region.ilike(f'%{region_q}%'))
    pagination = query.order_by(AiInforme.timestamp.desc()).paginate(page=page, per_page=20, error_out=False)
    tipos_foco = [t[0] for t in db.session.query(AiInforme.tipo_foco).distinct().all() if t[0]]
    return render_template('admin/ai_informes.html',
                           informes=pagination, tipo_filter=tipo,
                           severidad_filter=severidad, region_filter=region_q,
                           tipos_foco=tipos_foco)


@admin_bp.route('/mapa-recursos')
@login_required
@admin_required
def mapa_recursos():
    own = _own_id()
    rec_q = Recurso.query.filter_by(activo=True).filter(Recurso.lat.isnot(None), Recurso.lon.isnot(None))
    if own:
        rec_q = rec_q.filter_by(created_by=own)
    recursos_activos = rec_q.all()
    ai_q2 = AiInforme.query.filter(AiInforme.lat.isnot(None))
    if own:
        ai_q2 = ai_q2.filter_by(user_id=own)
    ai_recientes = ai_q2.order_by(AiInforme.timestamp.desc()).limit(50).all()

    recursos_json = [{'id': r.id, 'tipo': r.tipo, 'tipo_label': r.tipo_label,
                      'nombre': r.nombre, 'lat': r.lat, 'lon': r.lon,
                      'localidad': r.localidad, 'telefono': r.telefono, 'horario': r.horario}
                     for r in recursos_activos]
    ai_json = [{'lat': a.lat, 'lon': a.lon, 'tipo_foco': a.tipo_foco,
                'severidad': a.severidad,
                'timestamp': a.timestamp.isoformat() if a.timestamp else None}
               for a in ai_recientes]

    return render_template('admin/mapa_recursos.html',
                           recursos=recursos_activos, recursos_json=recursos_json,
                           ai_informes_json=ai_json)


@admin_bp.route('/recursos/<int:recurso_id>/unidades/new', methods=['POST'])
@login_required
@admin_required
def unidad_new(recurso_id):
    recurso = db.get_or_404(Recurso, recurso_id)
    own = _own_id()
    if own and recurso.created_by != own:
        flash('No tenés permiso para modificar este recurso', 'error')
        return redirect(url_for('admin.recursos'))
    f = request.form
    unidad = UnidadRecurso(
        recurso_id=recurso_id,
        tipo_unidad=f.get('tipo_unidad', 'otro_vehiculo'),
        nombre=f.get('nombre', '').strip() or None,
        descripcion=f.get('descripcion', '').strip() or None,
        capacidad=f.get('capacidad', '').strip() or None,
        tiempo_recarga_min=int(f['tiempo_recarga_min']) if f.get('tiempo_recarga_min', '').isdigit() else None,
        tiempo_respuesta_min=int(f['tiempo_respuesta_min']) if f.get('tiempo_respuesta_min', '').isdigit() else None,
        activo='activo' in f,
    )
    db.session.add(unidad)
    _audit('add_unidad', 'Recurso', recurso_id, f'tipo={unidad.tipo_unidad} nombre={unidad.nombre}')
    db.session.commit()
    flash('Unidad agregada', 'success')
    return redirect(url_for('admin.recurso_edit', recurso_id=recurso_id))


@admin_bp.route('/unidades/<int:unidad_id>/delete', methods=['POST'])
@login_required
@admin_required
def unidad_delete(unidad_id):
    unidad = db.get_or_404(UnidadRecurso, unidad_id)
    recurso_id = unidad.recurso_id
    own = _own_id()
    if own and unidad.recurso.created_by != own:
        flash('No tenés permiso para modificar este recurso', 'error')
        return redirect(url_for('admin.recursos'))
    _audit('delete_unidad', 'Recurso', recurso_id, f'tipo={unidad.tipo_unidad} nombre={unidad.nombre}')
    db.session.delete(unidad)
    db.session.commit()
    flash('Unidad eliminada', 'success')
    return redirect(url_for('admin.recurso_edit', recurso_id=recurso_id))


@admin_bp.route('/usuarios/export.csv')
@login_required
@admin_required
def export_usuarios_csv():
    own = _own_id()
    uq = User.query.filter_by(role='user')
    if own:
        uq = uq.filter_by(created_by_admin=own)
    usuarios = uq.order_by(User.created_at.desc()).all()
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['id', 'username', 'email', 'activo', 'pais', 'region_tipo', 'region_nombre', 'creado', 'ultimo_acceso'])
    for u in usuarios:
        cw.writerow([u.id, u.username, u.email, u.active, u.pais or '', u.region_tipo or '',
                     u.region_nombre or '',
                     u.created_at.strftime('%Y-%m-%d %H:%M') if u.created_at else '',
                     u.last_login.strftime('%Y-%m-%d %H:%M') if u.last_login else ''])
    return Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=usuarios.csv'})


@admin_bp.route('/recursos/export.csv')
@login_required
@admin_required
def export_recursos_csv():
    own = _own_id()
    rq = Recurso.query
    if own:
        rq = rq.filter_by(created_by=own)
    recursos = rq.order_by(Recurso.tipo, Recurso.nombre).all()
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['id', 'tipo', 'nombre', 'pais', 'provincia_departamento', 'localidad',
                 'direccion', 'lat', 'lon', 'telefono', 'email', 'contacto_nombre', 'horario', 'activo'])
    for r in recursos:
        cw.writerow([r.id, r.tipo, r.nombre, r.pais or '', r.provincia_departamento or '',
                     r.localidad or '', r.direccion or '', r.lat or '', r.lon or '',
                     r.telefono or '', r.email or '', r.contacto_nombre or '', r.horario or '', r.activo])
    return Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=recursos.csv'})


@admin_bp.route('/auditoria')
@login_required
@admin_required
def auditoria():
    page = request.args.get('page', 1, type=int)
    accion = request.args.get('accion', '').strip()
    fecha_desde = request.args.get('desde', '').strip()
    fecha_hasta = request.args.get('hasta', '').strip()
    own = _own_id()
    query = AuditLog.query
    if own:
        query = query.filter_by(user_id=own)
    if accion:
        query = query.filter_by(action=accion)
    if fecha_desde:
        try:
            query = query.filter(AuditLog.timestamp >= datetime.strptime(fecha_desde, '%Y-%m-%d'))
        except ValueError:
            pass
    if fecha_hasta:
        try:
            query = query.filter(AuditLog.timestamp < datetime.strptime(fecha_hasta, '%Y-%m-%d') + timedelta(days=1))
        except ValueError:
            pass
    pagination = query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=30, error_out=False)
    # distinct solo sobre las acciones visibles para este admin
    acciones_q = db.session.query(AuditLog.action).distinct()
    if own:
        acciones_q = acciones_q.filter(AuditLog.user_id == own)
    acciones = [a[0] for a in acciones_q.all() if a[0]]
    admin_ids = {log.user_id for log in pagination.items if log.user_id}
    admin_names = {u.id: u.username for u in User.query.filter(User.id.in_(admin_ids)).all()} if admin_ids else {}
    return render_template('admin/auditoria.html', logs=pagination, accion_filter=accion, acciones=acciones,
                           admin_names=admin_names, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)


@admin_bp.route('/ai-informes/export.csv')
@login_required
@admin_required
def export_ai_informes_csv():
    own = _own_id()
    query = AiInforme.query
    if own:
        query = query.filter_by(user_id=own)
    query = query.order_by(AiInforme.timestamp.desc())
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['id','fecha','region','severidad','tipo_foco','satelite','confianza','fwi','hectareas','lat','lon'])
    for r in query.all():
        cw.writerow([
            r.id,
            r.timestamp.strftime('%Y-%m-%d %H:%M') if r.timestamp else '',
            r.region or '', r.severidad or '', r.tipo_foco or '',
            r.satellite or '', r.conf or '', r.fwi_val or '',
            r.ha or '', r.lat or '', r.lon or '',
        ])
    return Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=ai_informes.csv'})


@admin_bp.route('/focos/export.csv')
@login_required
@admin_required
def export_focos_csv():
    query = _geo_filter(FocoLog.query, FocoLog).order_by(FocoLog.timestamp.desc())
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(['id','fecha','region','severidad','fuente','hectareas','lat','lon','analizado_ia'])
    for r in query.all():
        cw.writerow([
            r.id,
            r.timestamp.strftime('%Y-%m-%d %H:%M') if r.timestamp else '',
            r.region or '', r.severidad or '', r.fuente or '',
            r.ha or '', r.lat or '', r.lon or '', r.ai_analizado,
        ])
    return Response(si.getvalue().encode('utf-8-sig'), mimetype='text/csv',
                    headers={'Content-Disposition': 'attachment;filename=focos.csv'})


@admin_bp.route('/api/alert-counts')
@login_required
@admin_required
def alert_counts():
    from flask import jsonify
    hoy = datetime.utcnow().date()
    inicio_hoy = datetime.combine(hoy, datetime.min.time())
    focos_criticos = _geo_filter(FocoLog.query, FocoLog).filter_by(severidad='critical').filter(FocoLog.timestamp >= inicio_hoy).count()
    smn_rojo = _geo_filter(SmnAlerta.query, SmnAlerta).filter_by(severidad='rojo').filter(SmnAlerta.timestamp >= inicio_hoy).count()
    own = _own_id()
    ai_q = AiInforme.query
    if own:
        ai_q = ai_q.filter_by(user_id=own)
    ai_hoy = ai_q.filter(AiInforme.timestamp >= inicio_hoy).count()
    return jsonify({'focos_criticos': focos_criticos, 'smn_rojo': smn_rojo, 'ai_hoy': ai_hoy})


@admin_bp.route('/configuracion', methods=['GET', 'POST'])
@login_required
@admin_required
def configuracion():
    from app import get_cfg, set_cfg
    from superadmin import _procesar_logo
    es_superadmin = current_user.role == 'superadmin'

    CAMPOS = [
        ('SUMMARY_ENABLED',    'Resumen diario activo',          'bool',     'true'),
        ('SUMMARY_HOUR_UTC',   'Hora de envío (UTC)',             'number',   '11'),
    ]
    CAMPOS_SUPERADMIN = [
        ('TELEGRAM_ENABLED',   'Telegram activo',                 'bool',     'true'),
        ('TELEGRAM_BOT_TOKEN', 'Telegram Bot Token',              'password', ''),
        ('TELEGRAM_CHAT_ID',   'Telegram Chat ID',                'text',     ''),
        ('EMAIL_ENABLED',      'Email activo',                    'bool',     'false'),
        ('SMTP_HOST',          'SMTP Host',                       'text',     ''),
        ('SMTP_PORT',          'SMTP Puerto',                     'number',   '587'),
        ('SMTP_USER',          'SMTP Usuario',                    'text',     ''),
        ('SMTP_PASS',          'SMTP Contraseña',                 'password', ''),
        ('SMTP_FROM',          'Email remitente (From)',          'text',     ''),
        ('NASA_MAP_KEY',       'NASA FIRMS API Key',              'password', ''),
        ('ANTHROPIC_API_KEY',  'Anthropic API Key (Claude IA)',   'password', ''),
    ]
    if es_superadmin:
        CAMPOS = CAMPOS + CAMPOS_SUPERADMIN

    if request.method == 'POST':
        for key, _, tipo, _ in CAMPOS:
            if tipo == 'bool':
                set_cfg(key, 'true' if request.form.get(key) else 'false')
            else:
                val = request.form.get(key, '').strip()
                if val:
                    set_cfg(key, val)

        nuevo_logo = _procesar_logo(request.files.get('institucion_logo'))
        if nuevo_logo:
            current_user.institucion_logo = nuevo_logo
            db.session.commit()
        elif 'quitar_logo' in request.form:
            current_user.institucion_logo = None
            db.session.commit()

        flash('Configuración guardada correctamente', 'success')
        return redirect(url_for('admin.configuracion'))
    valores = {key: get_cfg(key, default) for key, _, _, default in CAMPOS}
    return render_template('admin/configuracion.html', campos=CAMPOS, valores=valores,
                           es_superadmin=es_superadmin)


@admin_bp.route('/users/<int:user_id>/verify', methods=['POST'])
@login_required
@admin_required
def user_verify(user_id):
    user = db.get_or_404(User, user_id)
    if current_user.role != 'superadmin' and user.created_by_admin != current_user.id:
        flash('No tenés permiso para verificar este usuario', 'error')
        return redirect(url_for('admin.users'))
    user.email_verified = True
    user.email_verify_token = None
    _audit('verify_user', 'User', user_id, f'username={user.username}')
    db.session.commit()
    flash(f'Usuario {user.username} verificado manualmente', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    if current_user.role != 'superadmin' and user.created_by_admin != current_user.id:
        flash('No tenés permiso para eliminar este usuario', 'error')
        return redirect(url_for('admin.users'))
    if user.id == current_user.id:
        flash('No puedes eliminar tu propio usuario', 'error')
        return redirect(url_for('admin.users'))
    username = user.username
    _audit('delete_user', 'User', user_id, f'username={username}')
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {username} eliminado', 'success')
    return redirect(url_for('admin.users'))
