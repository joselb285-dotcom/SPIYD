from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, UsageLog
from datetime import datetime
from urllib.parse import urlparse
import io, base64
import pyotp
import qrcode

auth_bp = Blueprint('auth', __name__)

from app import limiter


def _qr_data_uri(data):
    img = qrcode.make(data)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


def _finish_login(user, remember, next_page):
    login_user(user, remember=remember)
    session.permanent = True
    user.last_login = datetime.utcnow()
    try:
        db.session.add(UsageLog(user_id=user.id, action='login'))
        db.session.commit()
    except Exception:
        db.session.rollback()
    # Solo aceptar rutas relativas (sin scheme ni host)
    parsed = urlparse(next_page or '')
    if parsed.scheme or parsed.netloc:
        next_page = ''
    if not next_page:
        if user.role == 'superadmin':
            next_page = url_for('superadmin.dashboard')
        elif user.role == 'admin':
            next_page = url_for('admin.dashboard')
        else:
            next_page = url_for('mapa') + '?auth=1'
    return redirect(next_page)


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for('mapa'))
    if request.method == 'POST':
        identifier = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first()
        if user and user.check_password(password) and not user.email_verified:
            flash('Verificá tu email antes de iniciar sesión. Revisá tu casilla de correo.', 'error')
            return render_template('auth/login.html')
        if user and user.check_password(password) and user.trial_expires_at and datetime.utcnow() > user.trial_expires_at:
            flash('Tu período de prueba venció. Contactá a tu administrador.', 'error')
            return render_template('auth/login.html')
        if user and user.check_password(password) and user.active:
            if user.role == 'superadmin':
                session['pending_2fa_uid'] = user.id
                session['pending_2fa_remember'] = request.form.get('remember') == 'on'
                session['pending_2fa_next'] = request.args.get('next', '')
                if user.totp_enabled and user.totp_secret:
                    return redirect(url_for('auth.login_2fa'))
                return redirect(url_for('auth.login_2fa_setup'))
            return _finish_login(user, request.form.get('remember') == 'on', request.args.get('next', ''))
        flash('Usuario o contraseña incorrectos', 'error')
    return render_template('auth/login.html')


@auth_bp.route('/login/2fa-setup', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login_2fa_setup():
    if current_user.is_authenticated:
        return redirect(url_for('mapa'))
    uid = session.get('pending_2fa_uid')
    user = db.session.get(User, uid) if uid else None
    if not user or user.role != 'superadmin' or user.totp_enabled:
        session.pop('pending_2fa_uid', None)
        return redirect(url_for('auth.login'))
    if 'setup_secret' not in session:
        session['setup_secret'] = pyotp.random_base32()
    secret = session['setup_secret']
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if pyotp.TOTP(secret).verify(code, valid_window=1):
            user.totp_secret = secret
            user.totp_enabled = True
            db.session.commit()
            session.pop('setup_secret', None)
            remember = session.pop('pending_2fa_remember', False)
            next_page = session.pop('pending_2fa_next', '')
            session.pop('pending_2fa_uid', None)
            flash('Verificación en dos pasos activada correctamente', 'success')
            return _finish_login(user, remember, next_page)
        flash('Código inválido. Probá de nuevo.', 'error')
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name='SPIYD')
    return render_template('auth/2fa_setup.html', qr_data_uri=_qr_data_uri(uri), secret=secret)


@auth_bp.route('/login/2fa', methods=['GET', 'POST'])
@limiter.limit("10 per minute")
def login_2fa():
    if current_user.is_authenticated:
        return redirect(url_for('mapa'))
    uid = session.get('pending_2fa_uid')
    user = db.session.get(User, uid) if uid else None
    if not user or user.role != 'superadmin' or not user.totp_enabled:
        session.pop('pending_2fa_uid', None)
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if pyotp.TOTP(user.totp_secret).verify(code, valid_window=1):
            remember = session.pop('pending_2fa_remember', False)
            next_page = session.pop('pending_2fa_next', '')
            session.pop('pending_2fa_uid', None)
            return _finish_login(user, remember, next_page)
        flash('Código inválido', 'error')
    return render_template('auth/2fa_verify.html')


@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login'))


@auth_bp.route('/verify-email/<token>')
def verify_email(token):
    user = User.query.filter_by(email_verify_token=token).first()
    if not user:
        flash('Enlace de verificación inválido o ya utilizado', 'error')
        return redirect(url_for('auth.login'))
    user.email_verified = True
    user.email_verify_token = None
    db.session.commit()
    flash('Email verificado correctamente. Ya podés iniciar sesión.', 'success')
    return redirect(url_for('auth.login'))
