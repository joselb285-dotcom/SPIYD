from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from functools import wraps
from sqlalchemy import func
from models import db, User, UsageLog, Invoice, PLAN_PRICES
from datetime import datetime

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Acceso restringido a administradores', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.route('/')
@login_required
@admin_required
def dashboard():
    total_users = User.query.count()
    active_users = User.query.filter_by(active=True).count()
    monthly_revenue = db.session.query(func.sum(Invoice.amount)).filter_by(status='paid').scalar() or 0.0
    pending_revenue = db.session.query(func.sum(Invoice.amount)).filter_by(status='pending').scalar() or 0.0
    overdue_revenue = db.session.query(func.sum(Invoice.amount)).filter_by(status='overdue').scalar() or 0.0
    recent_logins = (
        db.session.query(UsageLog, User)
        .join(User, UsageLog.user_id == User.id)
        .filter(UsageLog.action == 'login')
        .order_by(UsageLog.timestamp.desc())
        .limit(10)
        .all()
    )
    plan_counts = dict(
        db.session.query(User.plan, func.count(User.id)).group_by(User.plan).all()
    )
    return render_template('admin/dashboard.html',
        total_users=total_users,
        active_users=active_users,
        monthly_revenue=monthly_revenue,
        pending_revenue=pending_revenue,
        overdue_revenue=overdue_revenue,
        recent_logins=recent_logins,
        plan_counts=plan_counts
    )


@admin_bp.route('/users')
@login_required
@admin_required
def users():
    search = request.args.get('q', '').strip()
    query = User.query
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
        role = request.form.get('role', 'user')
        plan = request.form.get('plan', 'basic')
        if not username or not email or not password:
            flash('Todos los campos son obligatorios', 'error')
        elif User.query.filter_by(username=username).first():
            flash('El nombre de usuario ya existe', 'error')
        elif User.query.filter_by(email=email).first():
            flash('El email ya está registrado', 'error')
        else:
            user = User(username=username, email=email, role=role, plan=plan)
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
        role = request.form.get('role', user.role)
        plan = request.form.get('plan', user.plan)
        active = 'active' in request.form
        new_password = request.form.get('password', '').strip()
        existing = User.query.filter(User.email == email, User.id != user_id).first()
        if existing:
            flash('El email ya está en uso por otro usuario', 'error')
        else:
            user.email = email
            user.role = role
            user.plan = plan
            user.active = active
            if new_password:
                user.set_password(new_password)
            db.session.commit()
            flash(f'Usuario {user.username} actualizado', 'success')
            return redirect(url_for('admin.users'))
    return render_template('admin/user_form.html', user=user, action='edit')


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


@admin_bp.route('/billing')
@login_required
@admin_required
def billing():
    invoices = (
        Invoice.query.join(User)
        .order_by(Invoice.created_at.desc())
        .all()
    )
    total_paid = db.session.query(func.sum(Invoice.amount)).filter_by(status='paid').scalar() or 0.0
    total_pending = db.session.query(func.sum(Invoice.amount)).filter_by(status='pending').scalar() or 0.0
    total_overdue = db.session.query(func.sum(Invoice.amount)).filter_by(status='overdue').scalar() or 0.0
    return render_template('admin/billing.html',
        invoices=invoices,
        total_paid=total_paid,
        total_pending=total_pending,
        total_overdue=total_overdue
    )


@admin_bp.route('/billing/generate', methods=['POST'])
@login_required
@admin_required
def billing_generate():
    period = datetime.utcnow().strftime('%Y-%m')
    paid_users = User.query.filter(User.plan != 'basic', User.active == True).all()
    count = 0
    for user in paid_users:
        existing = Invoice.query.filter_by(user_id=user.id, period=period).first()
        if not existing:
            invoice = Invoice(
                user_id=user.id,
                period=period,
                amount=PLAN_PRICES.get(user.plan, 0.0),
                status='pending'
            )
            db.session.add(invoice)
            count += 1
    db.session.commit()
    flash(f'{count} facturas generadas para el período {period}', 'success')
    return redirect(url_for('admin.billing'))


@admin_bp.route('/billing/<int:invoice_id>/pay', methods=['POST'])
@login_required
@admin_required
def invoice_pay(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    invoice.status = 'paid'
    invoice.paid_at = datetime.utcnow()
    db.session.commit()
    flash(f'Factura #{invoice_id} marcada como pagada', 'success')
    return redirect(url_for('admin.billing'))


@admin_bp.route('/billing/<int:invoice_id>/overdue', methods=['POST'])
@login_required
@admin_required
def invoice_overdue(invoice_id):
    invoice = db.get_or_404(Invoice, invoice_id)
    invoice.status = 'overdue'
    db.session.commit()
    flash(f'Factura #{invoice_id} marcada como vencida', 'success')
    return redirect(url_for('admin.billing'))
