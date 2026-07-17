import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _get_cfg(key, default=''):
    from models import SystemLog
    try:
        row = SystemLog.query.filter_by(key=f'cfg_{key}').first()
        if row and row.value is not None:
            return row.value
    except Exception:
        pass
    return os.environ.get(key, default)


def enviar_email(destinatarios, asunto, html):
    smtp_host = _get_cfg('SMTP_HOST')
    smtp_port = int(_get_cfg('SMTP_PORT', '587'))
    smtp_user = _get_cfg('SMTP_USER')
    smtp_pass = _get_cfg('SMTP_PASS')
    smtp_from = _get_cfg('SMTP_FROM', smtp_user)
    if not smtp_host or not smtp_user or not smtp_pass or not destinatarios:
        return False
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = asunto
        msg['From'] = smtp_from
        msg['To'] = ', '.join(destinatarios)
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, destinatarios, msg.as_string())
        return True
    except Exception:
        return False


def enviar_email_verificacion(user, base_url):
    link = f"{base_url.rstrip('/')}/verify-email/{user.email_verify_token}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;background:#0d0f17;color:#e0e0e0;border-radius:12px;overflow:hidden">
      <div style="background:linear-gradient(135deg,#f97316,#dc2626);padding:20px 28px">
        <h2 style="margin:0;color:#fff;font-size:18px">🔥 SPIYD — Verificá tu cuenta</h2>
      </div>
      <div style="padding:24px 28px">
        <p>Hola {user.username}, confirmá tu correo para activar tu cuenta.</p>
        <div style="margin-top:20px;text-align:center">
          <a href="{link}" style="background:#f97316;color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px">Verificar email →</a>
        </div>
        <p style="color:#888;font-size:12px;margin-top:20px">Si el botón no funciona, copiá este enlace: {link}</p>
      </div>
    </div>"""
    return enviar_email([user.email], '[SPIYD] Verificá tu email', html)
