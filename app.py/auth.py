from flask import Blueprint, render_template, redirect, url_for, request, flash, session
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, UsageLog
from datetime import datetime
from urllib.parse import urlparse

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('mapa'))
    if request.method == 'POST':
        identifier = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first()
        if user and user.check_password(password) and user.active:
            remember = request.form.get('remember') == 'on'
            login_user(user, remember=remember)
            session.permanent = True
            user.last_login = datetime.utcnow()
            try:
                db.session.add(UsageLog(user_id=user.id, action='login'))
                db.session.commit()
            except Exception:
                db.session.rollback()
            next_page = request.args.get('next', '')
            # Solo aceptar rutas relativas (sin scheme ni host)
            parsed = urlparse(next_page)
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
        flash('Usuario o contraseña incorrectos', 'error')
    return render_template('auth/login.html')


@auth_bp.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('auth.login'))
