"""
Migra datos de SQLite -> PostgreSQL.
Uso:
  1. Configurá DATABASE_URL en .env apuntando a PostgreSQL
  2. python migrate_to_postgres.py

Requiere que PostgreSQL esté corriendo y la base de datos exista:
  CREATE DATABASE spiyd;
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app.py'))

from dotenv import load_dotenv
load_dotenv()

import sqlite3
from datetime import datetime

SQLITE_PATH = os.path.join(os.path.dirname(__file__), 'spiyd.db')
PG_URL      = os.environ.get('DATABASE_URL', '')

if not PG_URL or PG_URL.startswith('sqlite'):
    print("ERROR: DATABASE_URL debe apuntar a PostgreSQL en .env")
    print(f"  Actual: {PG_URL}")
    sys.exit(1)

if not os.path.exists(SQLITE_PATH):
    print(f"ERROR: No se encontró {SQLITE_PATH}")
    sys.exit(1)

# Crear tablas en PostgreSQL usando los modelos
from app import app
from models import db, User, UsageLog, Invoice

with app.app_context():
    db.create_all()
    print("Tablas creadas en PostgreSQL.")

    # Leer datos de SQLite
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    def parse_dt(s):
        if not s:
            return None
        for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        return None

    # ── Usuarios ──────────────────────────────────────────────────────────────
    cur.execute("SELECT * FROM user")
    users_sqlite = cur.fetchall()
    migrados_u = 0
    for row in users_sqlite:
        if db.session.get(User, row['id']):
            continue
        u = User(
            id           = row['id'],
            username     = row['username'],
            email        = row['email'],
            password_hash= row['password_hash'],
            role         = row['role'],
            active       = bool(row['active']),
            plan         = row['plan'],
            created_at   = parse_dt(row['created_at']),
            last_login   = parse_dt(row['last_login']),
        )
        db.session.add(u)
        migrados_u += 1
    db.session.commit()
    print(f"Usuarios migrados: {migrados_u} (ya existían: {len(users_sqlite)-migrados_u})")

    # ── UsageLog ──────────────────────────────────────────────────────────────
    cur.execute("SELECT * FROM usage_log")
    logs_sqlite = cur.fetchall()
    migrados_l = 0
    for row in logs_sqlite:
        existing = db.session.get(UsageLog, row['id'])
        if existing:
            continue
        l = UsageLog(
            id        = row['id'],
            user_id   = row['user_id'],
            action    = row['action'],
            timestamp = parse_dt(row['timestamp']),
            cost      = row['cost'] or 0.0,
        )
        db.session.add(l)
        migrados_l += 1
    db.session.commit()
    print(f"UsageLogs migrados: {migrados_l}")

    # ── Invoices ──────────────────────────────────────────────────────────────
    cur.execute("SELECT * FROM invoice")
    inv_sqlite = cur.fetchall()
    migrados_i = 0
    for row in inv_sqlite:
        if db.session.get(Invoice, row['id']):
            continue
        inv = Invoice(
            id         = row['id'],
            user_id    = row['user_id'],
            period     = row['period'],
            amount     = row['amount'] or 0.0,
            status     = row['status'],
            created_at = parse_dt(row['created_at']),
            paid_at    = parse_dt(row['paid_at']),
        )
        db.session.add(inv)
        migrados_i += 1
    db.session.commit()
    print(f"Invoices migrados: {migrados_i}")

    # Sincronizar secuencias de PostgreSQL para que los nuevos IDs no colisionen
    from sqlalchemy import text
    for table, col in [('user','id'), ('usage_log','id'), ('invoice','id')]:
        db.session.execute(text(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
            f"COALESCE(MAX({col}), 1)) FROM \"{table}\""
        ))
    db.session.commit()
    print("Secuencias PostgreSQL sincronizadas.")

    con.close()
    print("\nMigración completada exitosamente.")
