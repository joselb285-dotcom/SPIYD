from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, UsageLog
from datetime import datetime

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('mapa'))
    if request.method == 'POST':
        identifier = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = bool(request.form.get('remember'))
        user = User.query.filter(
            (User.username == identifier) | (User.email == identifier)
        ).first()
        if user and user.check_password(password) and user.active:
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.add(UsageLog(user_id=user.id, action='login'))
            db.session.commit()
            next_page = request.args.get('next')
            if not next_page:
                if user.role == 'superadmin':
                    next_page = url_for('superadmin.dashboard')
                elif user.role == 'admin':
                    next_page = url_for('admin.dashboard')
                else:
                    next_page = url_for('mapa')
            return redirect(next_page)
        flash('Usuario o contraseña incorrectos', 'error')
    return render_template('auth/login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('landing'))
