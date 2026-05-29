from flask import Blueprint, render_template, redirect, url_for, request, flash, Response
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy import func, or_
import csv, io, math
from models import db, User, UsageLog, SmnAlerta, AiInforme, FocoLog, Recurso, TIPOS_RECURSO, AuditLog, UnidadRecurso, TIPOS_UNIDAD
from superadmin import PROVINCIAS_ARG, DEPARTAMENTOS_PRY
from datetime import datetime, timedelta

admin_bp = Blueprint('admin', __name__)


def _geo_filter(query, model):
    """Filtra una query por el alcance geográfico del current_user (pais + provincia/departamento).
    Solo se aplica cuando el admin tiene pais configurado; superadmins sin pais ven todo."""
    u = current_user
    if not u.pais:
        return query  # sin restricción
    if u.pais == 'argentina':
        query = query.filter(
            or_(model.region.is_(None), ~model.region.ilike('%paraguay%'))
        )
    elif u.pais == 'paraguay':
        query = query.filter(model.region.ilike('%paraguay%'))
    if u.region_tipo in ('provincia', 'departamento') and u.region_nombre:
        query = query.filter(model.region.ilike(f'%{u.region_nombre}%'))
    return query


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ('admin', 'superadmin'):
            flash('Acceso restringido a administradores', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


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

    total_users = User.query.filter_by(role='user').count()
    active_users = User.query.filter_by(role='user', active=True).count()

    smn_q = _geo_filter(SmnAlerta.query, SmnAlerta)
    smn_total = smn_q.count()
    smn_hoy = smn_q.filter(SmnAlerta.timestamp >= inicio_hoy).count()
    smn_recientes = smn_q.order_by(SmnAlerta.timestamp.desc()).limit(8).all()

    ai_q = _geo_filter(AiInforme.query, AiInforme)
    ai_total = ai_q.count()
    ai_mes = ai_q.filter(AiInforme.timestamp >= inicio_mes).count()
    ai_recientes = ai_q.order_by(AiInforme.timestamp.desc()).limit(8).all()

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
    page = request.args.get('page', 1, type=int)
    query = User.query.filter_by(role='user')
    if search:
        query = query.filter(
            (User.username.ilike(f'%{search}%')) | (User.email.ilike(f'%{search}%'))
        )
    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=30, error_out=False)
    return render_template('admin/users.html', users=pagination.items,
                           pagination=pagination, search=search)


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
            pais = request.form.get('pais', '').strip() or None
            region_tipo = request.form.get('region_tipo', 'pais').strip()
            region_nombre = request.form.get('region_nombre', '').strip() or None
            if region_tipo == 'pais':
                region_nombre = None
            user = User(username=username, email=email, role='user',
                        pais=pais, region_tipo=region_tipo, region_nombre=region_nombre)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash(f'Usuario {username} creado exitosamente', 'success')
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', user=None, action='new',
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


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
            pais = request.form.get('pais', '').strip() or None
            region_tipo = request.form.get('region_tipo', 'pais').strip()
            region_nombre = request.form.get('region_nombre', '').strip() or None
            if region_tipo == 'pais':
                region_nombre = None
            user.email = email
            user.active = active
            user.pais = pais
            user.region_tipo = region_tipo
            user.region_nombre = region_nombre
            if new_password:
                user.set_password(new_password)
            _audit('edit_user', 'User', user_id, f'email={email} active={active} pais={pais}')
            db.session.commit()
            flash(f'Usuario {user.username} actualizado', 'success')
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', user=user, action='edit',
                           provincias=PROVINCIAS_ARG, departamentos=DEPARTAMENTOS_PRY)


@admin_bp.route('/recursos')
@login_required
@admin_required
def recursos():
    tipo_filter = request.args.get('tipo', '').strip()
    pais_filter = request.args.get('pais', '').strip()
    search = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    query = Recurso.query
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
    query = _geo_filter(AiInforme.query, AiInforme)
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
    recursos_activos = Recurso.query.filter_by(activo=True).filter(
        Recurso.lat.isnot(None), Recurso.lon.isnot(None)
    ).all()
    ai_recientes = AiInforme.query.filter(
        AiInforme.lat.isnot(None)
    ).order_by(AiInforme.timestamp.desc()).limit(50).all()

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
    _audit('delete_unidad', 'Recurso', recurso_id, f'tipo={unidad.tipo_unidad} nombre={unidad.nombre}')
    db.session.delete(unidad)
    db.session.commit()
    flash('Unidad eliminada', 'success')
    return redirect(url_for('admin.recurso_edit', recurso_id=recurso_id))


@admin_bp.route('/usuarios/export.csv')
@login_required
@admin_required
def export_usuarios_csv():
    usuarios = User.query.filter_by(role='user').order_by(User.created_at.desc()).all()
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
    recursos = Recurso.query.order_by(Recurso.tipo, Recurso.nombre).all()
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
    query = AuditLog.query
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
    acciones = [a[0] for a in db.session.query(AuditLog.action).distinct().all() if a[0]]
    admin_ids = {log.user_id for log in pagination.items if log.user_id}
    admin_names = {u.id: u.username for u in User.query.filter(User.id.in_(admin_ids)).all()} if admin_ids else {}
    return render_template('admin/auditoria.html', logs=pagination, accion_filter=accion, acciones=acciones,
                           admin_names=admin_names, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)


@admin_bp.route('/ai-informes/export.csv')
@login_required
@admin_required
def export_ai_informes_csv():
    query = _geo_filter(AiInforme.query, AiInforme).order_by(AiInforme.timestamp.desc())
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
    ai_hoy = _geo_filter(AiInforme.query, AiInforme).filter(AiInforme.timestamp >= inicio_hoy).count()
    return jsonify({'focos_criticos': focos_criticos, 'smn_rojo': smn_rojo, 'ai_hoy': ai_hoy})


@admin_bp.route('/users/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(user_id):
    user = db.get_or_404(User, user_id)
    if user.id == current_user.id:
        flash('No puedes eliminar tu propio usuario', 'error')
        return redirect(url_for('admin.users'))
    username = user.username
    _audit('delete_user', 'User', user_id, f'username={username}')
    db.session.delete(user)
    db.session.commit()
    flash(f'Usuario {username} eliminado', 'success')
    return redirect(url_for('admin.users'))
