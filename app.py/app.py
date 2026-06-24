from flask import Flask, jsonify, request, send_from_directory, render_template, redirect, url_for, flash
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import feedparser
import math
import os
import threading
import concurrent.futures
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, 'docs')

app = Flask(__name__,
            template_folder=os.path.join(MODULE_DIR, 'templates'),
            static_folder=BASE_DIR,
            static_url_path='')
CORS(app)

# Railway termina SSL en el proxy — necesario para que request.url sea https://
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

def _rate_limit_key():
    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        return f"user:{current_user.id}"
    return get_remote_address()

limiter = Limiter(_rate_limit_key, app=app, default_limits=[])

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'spiyd-dev-secret-change-in-prod-2026')
_db_url = os.environ.get('DATABASE_URL', 'sqlite:///' + os.path.join(BASE_DIR, 'spiyd.db'))
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SESSION_COOKIE_HTTPONLY']  = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['REMEMBER_COOKIE_DURATION'] = 0   # "recordarme" deshabilitado
app.config['PERMANENT_SESSION_LIFETIME'] = __import__('datetime').timedelta(hours=8)

from models import db, User, UsageLog, SmnAlerta, AiInforme, FocoLog, Recurso
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Iniciá sesión para acceder al sistema'
login_manager.login_message_category = 'warning'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@login_manager.unauthorized_handler
def unauthorized():
    if (request.is_json or
        'application/json' in request.headers.get('Accept', '') or
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'):
        return jsonify({"error": "Sesión no iniciada. Iniciá sesión en /login"}), 401
    return redirect(url_for('auth.login', next=request.path))

from auth import auth_bp
from admin import admin_bp
from superadmin import superadmin_bp
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(superadmin_bp, url_prefix='/superadmin')

with app.app_context():
    db.create_all()
    # Auto-migrate: each column uses its own connection so failures don't cascade
    from sqlalchemy import text as _text
    _new_cols = [
        ("ai_informe", "analysis_text", "TEXT"),
        ("ai_informe", "lat",           "DOUBLE PRECISION"),
        ("ai_informe", "lon",           "DOUBLE PRECISION"),
        ("ai_informe", "satellite",     "VARCHAR(50)"),
        ("ai_informe", "conf",          "VARCHAR(20)"),
        ("ai_informe", "fwi_val",       "DOUBLE PRECISION"),
        ("ai_informe", "tipo_foco",     "VARCHAR(30)"),
        ('"user"',      "pais",                "VARCHAR(50)"),
        ('"user"',      "region_tipo",         "VARCHAR(30)"),
        ('"user"',      "region_nombre",       "VARCHAR(100)"),
        ('"user"',      "institucion_nombre",  "VARCHAR(150)"),
        ('"user"',      "institucion_titulo",  "VARCHAR(150)"),
        ('"user"',      "institucion_logo",    "TEXT"),
        ('"user"',      "created_by_admin",    "INTEGER"),
    ]
    for _tbl, _col, _type in _new_cols:
        try:
            with db.engine.connect() as _c:
                _c.execute(_text(f"ALTER TABLE {_tbl} ADD COLUMN IF NOT EXISTS {_col} {_type}"))
                _c.commit()
        except Exception:
            pass

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"error": f"Límite de uso alcanzado. Intentá en unos minutos."}), 429

@app.errorhandler(500)
def internal_error(e):
    return jsonify({"error": f"Error interno del servidor: {str(e)}"}), 500


@app.route('/')
def landing():
    return render_template('landing.html')

@app.route('/styles.css')
def docs_styles():
    return send_from_directory(DOCS_DIR, 'styles.css')

@app.route('/main.js')
def docs_mainjs():
    return send_from_directory(DOCS_DIR, 'main.js')

@app.route('/assets/<path:filename>')
def docs_assets(filename):
    return send_from_directory(os.path.join(DOCS_DIR, 'assets'), filename)

@app.route('/lib/<path:filename>')
def docs_lib(filename):
    return send_from_directory(os.path.join(DOCS_DIR, 'lib'), filename)

@app.route('/demo.html')
def docs_demo():
    return send_from_directory(DOCS_DIR, 'demo.html')

@app.route('/contacto.html')
def docs_contacto():
    return send_from_directory(DOCS_DIR, 'contacto.html')

@app.route('/contacto', methods=['POST'])
@limiter.limit("3 per hour")
def contacto_form():
    nombre = request.form.get('nombre', '').strip()
    email = request.form.get('email', '').strip()
    organizacion = request.form.get('organizacion', '').strip()
    mensaje = request.form.get('mensaje', '').strip()
    if not nombre or not email:
        flash('Por favor completá nombre y correo.', 'warning')
        return redirect(url_for('landing') + '#demo')
    app.logger.info(f"[CONTACTO] {nombre} <{email}> — {organizacion}: {mensaje[:100]}")
    flash(f'Gracias {nombre}, recibimos tu solicitud. Te contactaremos a {email}.', 'success')
    return redirect(url_for('landing') + '#demo')

@app.route('/reunion.html')
def docs_reunion():
    return send_from_directory(DOCS_DIR, 'reunion.html')

@app.route('/mapa')
@login_required
def mapa():
    if current_user.is_authenticated:
        try:
            db.session.add(UsageLog(user_id=current_user.id, action='mapa'))
            db.session.commit()
        except Exception:
            db.session.rollback()
    maptiler_key = os.environ.get('MAPTILER_KEY', '')
    with open(os.path.join(BASE_DIR, 'mapa.html'), 'r', encoding='utf-8') as f:
        html = f.read()
    html = html.replace('__MAPTILER_KEY__', maptiler_key)
    import json as _json
    region_cfg = _json.dumps({
        'pais':         getattr(current_user, 'pais', None),
        'region_tipo':  getattr(current_user, 'region_tipo', None),
        'region_nombre':getattr(current_user, 'region_nombre', None),
        'role':         current_user.role,
    })
    html = html.replace('__USER_REGION__', region_cfg)
    build_ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    html = html.replace('</head>', f'<!-- build:{build_ts} -->\n</head>', 1)
    return html, 200, {
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
        'Pragma': 'no-cache',
        'Expires': '0',
        'Vary': '*',
    }

@app.route('/dashboard')
@login_required
def dashboard_alertas():
    from sqlalchemy import or_
    from datetime import timedelta as _td

    cutoff = datetime.utcnow() - _td(days=30)

    def _geo(q, model):
        u = current_user
        if not getattr(u, 'pais', None):
            return q
        if u.pais == 'argentina':
            q = q.filter(or_(model.region.is_(None), ~model.region.ilike('%paraguay%')))
        elif u.pais == 'paraguay':
            q = q.filter(model.region.ilike('%paraguay%'))
        if getattr(u, 'region_tipo', None) in ('provincia', 'departamento') and getattr(u, 'region_nombre', None):
            q = q.filter(model.region.ilike(f'%{u.region_nombre}%'))
        return q

    ai_list   = _geo(AiInforme.query.filter(AiInforme.timestamp >= cutoff).order_by(AiInforme.timestamp.desc()), AiInforme).limit(60).all()
    focos_raw = _geo(FocoLog.query.filter(FocoLog.timestamp >= cutoff).order_by(FocoLog.timestamp.desc()), FocoLog).limit(30).all()
    smn_list  = _geo(SmnAlerta.query.filter(SmnAlerta.timestamp >= cutoff).order_by(SmnAlerta.timestamp.desc()), SmnAlerta).limit(15).all()

    def _safe_float(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def _safe_int(v, default=0):
        try:
            return int(float(str(v).replace('%', '').strip()))
        except (TypeError, ValueError):
            return default

    def _safe_ts(dt):
        try:
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    focos_data = []
    for inf in ai_list:
        focos_data.append({
            'id': f'IA-{inf.id:04d}',
            'region': inf.region or 'Sin región',
            'lat': _safe_float(inf.lat, None),
            'lon': _safe_float(inf.lon, None),
            'severity': inf.severidad or 'medium',
            'source': inf.satellite or 'IA-SPIYD',
            'ha': _safe_int(inf.ha),
            'confidence': _safe_int(inf.conf, 75),
            'frp': round(_safe_float(inf.fwi_val), 1),
            'temp': 400,
            'ts': _safe_ts(inf.timestamp),
            'status': 'active' if inf.severidad in ('critical', 'high') else 'monitoring',
            'daynight': 'D',
            'analysis_text': (inf.analysis_text or '')[:600],
            'tipo_foco': inf.tipo_foco or '',
        })
    for foco in focos_raw:
        focos_data.append({
            'id': f'FCO-{foco.id:04d}',
            'region': foco.region or 'Sin región',
            'lat': _safe_float(foco.lat, None),
            'lon': _safe_float(foco.lon, None),
            'severity': foco.severidad or 'medium',
            'source': foco.fuente or 'Satelital',
            'ha': _safe_int(foco.ha),
            'confidence': 70, 'frp': 0, 'temp': 400,
            'ts': _safe_ts(foco.timestamp),
            'status': 'active' if foco.severidad in ('critical', 'high') else 'monitoring',
            'daynight': 'D', 'analysis_text': '', 'tipo_foco': '',
        })

    smn_data = [{'region': a.region or '', 'severidad': a.severidad or 'medium',
                 'fuente': a.fuente or 'SMN', 'descripcion': (a.descripcion or '')[:200],
                 'ts': _safe_ts(a.timestamp)} for a in smn_list]

    config = {
        'pais': current_user.pais or '',
        'region_tipo': getattr(current_user, 'region_tipo', '') or '',
        'region_nombre': getattr(current_user, 'region_nombre', '') or '',
        'username': current_user.username,
        'institucion': getattr(current_user, 'institucion_nombre', '') or current_user.username,
    }

    return render_template('dashboard_design.html',
        config=config,
        focos_data=focos_data,
        smn_data=smn_data,
    )

@app.route('/api/firms-data')
@login_required
def firms_data_proxy():
    if not NASA_MAP_KEY:
        return jsonify({"error": "NASA_MAP_KEY no configurada"}), 503
    try:
        import csv, io
        _conf_map = {'l': 40, 'n': 65, 'h': 85}
        bbox = '-73.5,-55.8,-53.5,-19.0'
        url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{NASA_MAP_KEY}/VIIRS_SNPP_NRT/{bbox}/1"
        resp = requests.get(url, timeout=20, headers={"User-Agent": "SPIYD/1.0"})
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = []
        for r in reader:
            conf_raw = r.get('confidence', 'n')
            conf = _conf_map.get(conf_raw.lower(), None) or (int(conf_raw) if conf_raw.isdigit() else 65)
            rows.append({
                'latitude':  r.get('latitude', ''),
                'longitude': r.get('longitude', ''),
                'confidence': conf,
                'frp':       r.get('frp', '0'),
                'bright_ti4': r.get('bright_ti4', '400'),
                'scan':      r.get('scan', '1'),
                'track':     r.get('track', '1'),
                'acq_date':  r.get('acq_date', ''),
                'acq_time':  r.get('acq_time', '0000'),
                'satellite': r.get('satellite', 'VIIRS'),
                'daynight':  r.get('daynight', 'D'),
            })
        return jsonify(rows)
    except Exception as e:
        return jsonify({"error": str(e)}), 503

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/provincias-arg.geojson')
def provincias_geojson():
    resp = send_from_directory(BASE_DIR, 'provincias_arg.geojson',
                               mimetype='application/json')
    resp.headers['Cache-Control'] = 'public, max-age=86400'
    return resp

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID      = os.environ.get("TELEGRAM_CHAT_ID", "")
NASA_MAP_KEY = os.environ.get("NASA_MAP_KEY", "")


def get_cfg(key, default=''):
    """Lee configuración de DB (cfg_KEY); si no existe usa env var o default."""
    from models import SystemLog as _SL
    try:
        row = _SL.query.filter_by(key=f'cfg_{key}').first()
        if row and row.value is not None:
            return row.value
    except Exception:
        pass
    return os.environ.get(key, default)


def set_cfg(key, value):
    """Guarda configuración en DB."""
    from models import SystemLog as _SL
    row = _SL.query.filter_by(key=f'cfg_{key}').first()
    if row:
        row.value = str(value)
        row.updated_at = datetime.utcnow()
    else:
        db.session.add(_SL(key=f'cfg_{key}', value=str(value)))
    db.session.commit()

SMN_FEEDS = [
    {"tipo": "Alertas y advertencias",   "url": "https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo.xml",             "nivel_base": "naranja"},
    {"tipo": "Temperaturas extremas",    "url": "https://ssl.smn.gob.ar/feeds/CAP/oladecalor/rss_ola_calor_nuevo.xml",  "nivel_base": "naranja"},
    {"tipo": "Avisos a muy corto plazo", "url": "https://ssl.smn.gob.ar/feeds/CAP/avisocortoplazo/rss_acpCAP.xml",      "nivel_base": "amarillo"}
]

GRID_LATS = list(range(-19, -56, -3))
GRID_LONS = list(range(-74, -52, 3))

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
_overpass_cache = {}   # key -> (timestamp, data)
OVERPASS_CACHE_TTL = 7200  # 2 horas
_overpass_lock = threading.RLock()

ultimas_alertas_enviadas = set()


# ── helpers ──────────────────────────────────────────────────────────────────

def normalizar_texto(texto: str) -> str:
    texto = texto or ""
    return texto.translate(str.maketrans("áéíóúÁÉÍÓÚñÑ", "aeiouAEIOUnN")).lower().strip()

def detectar_nivel_alerta(texto: str, nivel_base: str = "verde") -> str:
    t = normalizar_texto(texto)
    # keywords explícitos de color (si estuvieran en el texto)
    if "nivel rojo"    in t or "alerta roja"    in t or " rojo"    in t: return "rojo"
    if "nivel naranja" in t or "alerta naranja" in t or " naranja" in t: return "naranja"
    if "nivel amarillo"in t or "alerta amarilla"in t or " amarillo"in t: return "amarillo"
    # escalar a rojo por keywords de intensidad extrema
    KEYWORDS_ROJO = ["extremas", "extremo", "excepcional", "muy intensas", "muy intensos",
                     "catastrofico", "catastrofica", "desbordamiento", "emergencia"]
    if any(k in t for k in KEYWORDS_ROJO): return "rojo"
    # escalar a naranja si es base naranja + intensidad alta
    if nivel_base == "naranja":
        KEYWORDS_NARANJA_ALTA = ["intensas", "intensos", "fuertes", "granizo", "rafagas",
                                  "abundantes", "copiosas", "tormenta", "tormentas", "vientos fuertes"]
        if any(k in t for k in KEYWORDS_NARANJA_ALTA): return "naranja"
        return "naranja"  # el feed de alertas ya es mínimo naranja
    return nivel_base

def coincide_provincia(texto, provincia):
    if not provincia or provincia == "Argentina completa": return True
    return normalizar_texto(provincia) in normalizar_texto(texto)

def wind_to_uv(speed, direction):
    rad = math.radians(direction)
    return round(-speed * math.sin(rad), 2), round(-speed * math.cos(rad), 2)


# ── Persistencia de datos al DB ───────────────────────────────────────────────

def _conf_to_severidad(conf_str):
    s = str(conf_str).strip().lower()
    if s in ('h', 'high'):    return 'critical'
    if s in ('n', 'nominal'): return 'high'
    if s in ('l', 'low'):     return 'medium'
    try:
        c = int(s)
        if c >= 80: return 'critical'
        if c >= 50: return 'high'
        return 'medium'
    except (ValueError, TypeError):
        return 'medium'

def _fwi_to_severidad(fwi):
    if fwi >= 24: return 'critical'
    if fwi >= 12: return 'high'
    if fwi >= 5:  return 'medium'
    return 'low'

def _guardar_focos(focos_list):
    """focos_list: [{lat, lon, fuente, conf, frp, region?}]"""
    if not focos_list:
        return
    try:
        hoy = datetime.utcnow().date()
        inicio_hoy = datetime.combine(hoy, datetime.min.time())
        rows = db.session.query(FocoLog.lat, FocoLog.lon).filter(
            FocoLog.timestamp >= inicio_hoy
        ).all()
        existentes = set((round(r[0], 2), round(r[1], 2)) for r in rows)
        nuevos = []
        for f in focos_list:
            try:
                lat = round(float(f['lat']), 2)
                lon = round(float(f['lon']), 2)
                if (lat, lon) in existentes:
                    continue
                existentes.add((lat, lon))
                frp = None
                try: frp = float(f['frp']) if f.get('frp') else None
                except (ValueError, TypeError): pass
                nuevos.append(FocoLog(
                    lat=lat, lon=lon,
                    fuente=str(f.get('fuente', ''))[:50],
                    severidad=_conf_to_severidad(f.get('conf', '50')),
                    region=f.get('region') or _region_argentina(lat, lon),
                    ha=frp,
                ))
            except Exception:
                continue
        if nuevos:
            db.session.bulk_save_objects(nuevos)
            db.session.commit()
    except Exception:
        db.session.rollback()

def _guardar_smn(alertas):
    """alertas: lista de dicts con keys title, description, nivel, tipoFeed"""
    if not alertas:
        return
    try:
        from datetime import timedelta
        desde = datetime.utcnow() - timedelta(hours=24)
        rows = db.session.query(SmnAlerta.descripcion).filter(
            SmnAlerta.timestamp >= desde
        ).all()
        existentes = set((r[0] or '')[:80] for r in rows)
        nuevos = []
        for a in alertas:
            desc = (a.get('description') or a.get('title') or '')[:256]
            clave = desc[:80]
            if clave in existentes:
                continue
            existentes.add(clave)
            nivel = a.get('nivel', 'amarillo')
            sev = 'critical' if nivel == 'rojo' else 'high' if nivel == 'naranja' else 'medium'
            nuevos.append(SmnAlerta(
                region=(a.get('title') or '')[:100],
                severidad=sev,
                descripcion=desc,
                fuente=(a.get('tipoFeed') or 'SMN')[:50],
            ))
        if nuevos:
            db.session.bulk_save_objects(nuevos)
            db.session.commit()
    except Exception:
        db.session.rollback()


# ── FWI — Canadian Forest Fire Weather Index ──────────────────────────────────

def calc_fwi(temp, rh, wind, rain, month, ffmc0=85.0, dmc0=6.0, dc0=15.0):
    """
    Calcula el Índice Meteorológico de Incendio (FWI) canadiense.
    Hemisferio sur: se desplazan 6 meses los factores estacionales.
    rain: precipitación diaria en mm.
    """
    # Factores de longitud del día (hemisferio norte base, desplazado 6 meses para sur)
    LE = [6.5, 7.5, 9.0, 12.8, 13.9, 13.9, 12.4, 10.9, 9.4, 8.0, 7.0, 6.0]
    LF = [-1.6,-1.6,-1.6, 0.9,  3.8,  5.8,  6.4,  5.0, 2.4, 0.4,-1.6,-1.6]
    idx = (month - 1 + 6) % 12   # giro 6 meses hemisferio sur
    le, lf = LE[idx], LF[idx]

    # ── FFMC ──
    mo = 147.2 * (101 - ffmc0) / (59.5 + ffmc0)
    if rain > 0.5:
        rf = rain - 0.5
        mo += 42.5 * rf * math.exp(-100/(251-mo)) * (1-math.exp(-6.93/rf))
        if mo > 150:
            mo += 0.0015 * (mo-150)**2 * rf**0.5
        mo = min(mo, 250.0)
    ed = 0.942*rh**0.679 + 11*math.exp((rh-100)/10) + 0.18*(21.1-temp)*(1-math.exp(-0.115*rh))
    ew = 0.618*rh**0.753 + 10*math.exp((rh-100)/10) + 0.18*(21.1-temp)*(1-math.exp(-0.115*rh))
    if mo > ed:
        kd = (0.424*(1-(rh/100)**1.7) + 0.0694*wind**0.5*(1-(rh/100)**8)) * 0.581*math.exp(0.0365*temp)
        m  = ed + (mo-ed)*10**(-kd)
    elif mo < ew:
        kw = (0.424*(1-((100-rh)/100)**1.7) + 0.0694*wind**0.5*(1-((100-rh)/100)**8)) * 0.581*math.exp(0.0365*temp)
        m  = ew - (ew-mo)*10**(-kw)
    else:
        m = mo
    ffmc = max(0.0, min(101.0, 59.5*(250-m)/(147.2+m)))

    # ── DMC ──
    pr = dmc0
    if rain > 1.5:
        re  = 0.92*rain - 1.27
        mo2 = 20 + math.exp(5.6348 - dmc0/43.43)
        b   = (100/(0.5+0.3*dmc0) if dmc0<=33 else 14-1.3*math.log(max(dmc0,1)) if dmc0<=65 else 6.2*math.log(max(dmc0,1))-17.2)
        mr  = mo2 + 1000*re/(48.77+b*re)
        pr  = max(0.0, 244.72 - 43.43*math.log(max(mr-20, 0.001)))
    k   = max(0.0, 1.894*(temp+1.1)*(100-rh)*le*0.0001) if temp > -1.1 else 0.0
    dmc = max(0.0, pr + k)

    # ── DC ──
    dr = dc0
    if rain > 2.8:
        rd = 0.83*rain - 1.27
        qo = 800*math.exp(-dc0/400)
        qr = qo + 3.937*rd
        dr = max(0.0, 400*math.log(800/max(qr, 0.001)))
    v  = max(0.0, 0.36*(temp+2.8)+lf) if temp > -2.8 else max(0.0, lf)
    dc = max(0.0, dr + 0.5*v)

    # ── ISI ──
    m2  = 147.2*(101-ffmc)/(59.5+ffmc)
    ff  = 91.9*math.exp(-0.1386*m2)*(1+m2**5.31/4.93e7)
    isi = 0.208*math.exp(0.05039*wind)*ff

    # ── BUI ──
    if dmc <= 0.4*dc:
        bui = 0.8*dmc*dc/(dmc+0.4*dc) if (dmc+0.4*dc)>0 else 0.0
    else:
        bui = dmc - (1-0.8*dc/(dmc+0.4*dc))*(0.92+(0.0114*dmc)**1.7)
    bui = max(0.0, bui)

    # ── FWI ──
    fd  = 0.626*bui**0.809+2 if bui<=80 else 1000/(25+108.64*math.exp(-0.023*bui))
    B   = 0.1*isi*fd
    fwi = math.exp(2.72*(0.434*math.log(B))**0.647) if B>1 else B
    fwi = max(0.0, fwi)

    if fwi < 5:   clase = "MUY BAJO"
    elif fwi < 12: clase = "MODERADO"
    elif fwi < 24: clase = "ALTO"
    elif fwi < 38: clase = "MUY ALTO"
    else:          clase = "EXTREMO"

    return {"ffmc": round(ffmc,1), "dmc": round(dmc,1), "dc": round(dc,1),
            "isi": round(isi,1), "bui": round(bui,1), "fwi": round(fwi,1), "clase": clase}

def overpass_query(ql: str, timeout: int = 40) -> list:
    import time as _time
    headers = {
        "User-Agent": "ArgentinaFireMonitor/1.0 (fire-monitoring-app)",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    cache_key = ql[:200]
    with _overpass_lock:
        if cache_key in _overpass_cache:
            ts, data = _overpass_cache[cache_key]
            if _time.time() - ts < OVERPASS_CACHE_TTL:
                return data
    last_err = None
    for server in OVERPASS_SERVERS:
        try:
            r = requests.post(server, data={"data": ql}, headers=headers, timeout=timeout)
            r.raise_for_status()
            data = r.json().get("elements", [])
            with _overpass_lock:
                _overpass_cache[cache_key] = (_time.time(), data)
            return data
        except Exception as e:
            last_err = e
            continue
    raise last_err

def elemento_punto(el: dict):
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lon = el.get("lon") or (el.get("center") or {}).get("lon")
    return lat, lon


# ── SMN ──────────────────────────────────────────────────────────────────────

def obtener_alertas_smn(provincia: str):
    alertas = []
    for feed in SMN_FEEDS:
        parsed = feedparser.parse(feed["url"])
        for entry in parsed.entries:
            title, description = entry.get("title",""), entry.get("description","")
            link, pub_date     = entry.get("link",""), entry.get("published","")
            categorias = [t.get("term","") for t in entry.get("tags",[])]
            txt = f"{title} {description} {' '.join(categorias)}"
            if not coincide_provincia(txt, provincia): continue
            alertas.append({
                "tipoFeed": feed["tipo"], "title": title,
                "description": description, "link": link,
                "pubDate": pub_date, "category": categorias,
                "nivel": detectar_nivel_alerta(txt, feed.get("nivel_base", "verde"))
            })
    return alertas


# ── Clima / Open-Meteo ────────────────────────────────────────────────────────

def fetch_multi_points(lats, lons):
    """Una sola request para múltiples puntos (Open-Meteo multi-location API)."""
    lat_str = ",".join(str(l) for l in lats)
    lon_str = ",".join(str(l) for l in lons)
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat_str}&longitude={lon_str}"
           f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,precipitation"
           f"&daily=precipitation_sum"
           f"&wind_speed_unit=kmh&timezone=auto")
    data = requests.get(url, timeout=30).json()
    # Respuesta es lista cuando son múltiples ubicaciones, dict cuando es una
    if isinstance(data, dict):
        data = [data]
    results = []
    for i, d in enumerate(data):
        c = d.get("current", {})
        rain_daily = (d.get("daily", {}).get("precipitation_sum") or [0])[0] or 0
        results.append({
            "lat": lats[i], "lon": lons[i],
            "temp":       c.get("temperature_2m"),
            "humidity":   c.get("relative_humidity_2m"),
            "wind_speed": c.get("wind_speed_10m"),
            "wind_dir":   c.get("wind_direction_10m"),
            "rain":       rain_daily,
            "rain_now":   c.get("precipitation") or 0,
            "time":       c.get("time")
        })
    return results

def fetch_weather_point(lat, lon):
    res = fetch_multi_points([lat], [lon])
    return res[0] if res else {"lat": lat, "lon": lon, "temp": None, "humidity": None,
                                "wind_speed": None, "wind_dir": None, "rain": 0, "rain_now": 0, "time": None}

_clima_cache = {"data": None, "ts": 0}
CLIMA_CACHE_TTL = 1800
_clima_lock = threading.RLock()

def obtener_grilla_clima():
    import time as _t
    with _clima_lock:
        if _clima_cache["data"] and (_t.time() - _clima_cache["ts"]) < CLIMA_CACHE_TTL:
            return _clima_cache["data"]
        lats = [lat for lat in GRID_LATS for _ in GRID_LONS]
        lons = [lon for _ in GRID_LATS for lon in GRID_LONS]
        data = fetch_multi_points(lats, lons)
        _clima_cache["data"] = data
        _clima_cache["ts"]   = _t.time()
        return data


# ── Fuentes de agua (Overpass / OSM) ─────────────────────────────────────────

@app.route("/water-sources")
@login_required
def water_sources():
    s = float(request.args.get("s", -55.8))
    w = float(request.args.get("w", -73.5))
    n = float(request.args.get("n", -21.0))
    e = float(request.args.get("e", -53.5))
    buf = 0.1
    s, w, n, e = s-buf, w-buf, n+buf, e+buf

    # Limitar bbox máximo a ~10° para evitar timeout
    s = max(s, -56.0); n = min(n, -19.0); w = max(w, -74.0); e = min(e, -53.0)

    ql = f"""
[out:json][timeout:55];
(
  node["natural"="spring"]({s},{w},{n},{e});
  node["amenity"="fire_hydrant"]({s},{w},{n},{e});
  node["man_made"="water_tower"]({s},{w},{n},{e});
  node["man_made"="water_well"]({s},{w},{n},{e});
  way["natural"="water"]({s},{w},{n},{e});
  way["waterway"~"^(river|stream|canal|drain)$"]({s},{w},{n},{e});
  way["water"~"^(lake|reservoir|pond|basin)$"]({s},{w},{n},{e});
  relation["natural"="water"]["water"~"^(lake|reservoir)$"]({s},{w},{n},{e});
);
out center tags;
"""
    try:
        elements = overpass_query(ql, timeout=60)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 502

    # Prioridad: ríos/embalses > lagos > manantiales > hidrantes > tanques
    PRIO = {"river":1,"reservoir":2,"lake":3,"canal":4,"spring":5,
            "fire_hydrant":6,"water_tower":7,"water":8,"stream":9,
            "pond":10,"water_well":11}

    fuentes = []
    vistos  = set()
    for el in elements:
        lat, lon = elemento_punto(el)
        if lat is None or lon is None: continue
        clave = f"{round(lat,3)}|{round(lon,3)}"
        if clave in vistos: continue
        vistos.add(clave)
        tags  = el.get("tags", {})
        tipo  = (tags.get("waterway") or tags.get("water") or
                 tags.get("natural")  or tags.get("amenity") or
                 tags.get("man_made") or "water")
        fuentes.append({
            "lat":      lat,
            "lon":      lon,
            "tipo":     tipo,
            "prio":     PRIO.get(tipo, 20),
            "nombre":   tags.get("name") or tags.get("name:es") or tipo,
            "capacidad": tags.get("capacity") or tags.get("volume"),
            "acceso":   tags.get("access") or "public",
            "ele":      tags.get("ele")
        })

    # Ordenar por prioridad y limitar a 600 más relevantes
    fuentes.sort(key=lambda x: x["prio"])
    for f in fuentes: del f["prio"]
    return jsonify(fuentes[:600])


# ── Vegetación (Overpass / OSM) ───────────────────────────────────────────────

@app.route("/vegetation")
@login_required
def vegetation():
    s = float(request.args.get("s", -55.8))
    w = float(request.args.get("w", -73.5))
    n = float(request.args.get("n", -21.0))
    e = float(request.args.get("e", -53.5))

    ql = f"""
[out:json][timeout:60];
(
  way["landuse"~"^(forest|meadow|grassland|farmland|vineyard|orchard|grass|wood)$"]({s},{w},{n},{e});
  way["natural"~"^(wood|scrub|heath|grassland|wetland|tundra|fell)$"]({s},{w},{n},{e});
);
out geom tags;
"""
    try:
        elements = overpass_query(ql, timeout=65)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 502

    RIESGO = {
        "scrub": "MUY ALTO", "heath": "MUY ALTO",
        "wood": "ALTO",      "forest": "ALTO",
        "grassland": "ALTO", "fell": "ALTO",
        "meadow": "MODERADO","tundra": "MODERADO",
        "wetland": "BAJO",   "farmland": "MODERADO",
        "vineyard": "MODERADO", "orchard": "MODERADO",
    }

    features = []
    vistos   = set()
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry", [])
        if len(geom) < 3:
            continue
        tags   = el.get("tags", {})
        tipo   = tags.get("natural") or tags.get("landuse") or "vegetation"
        nombre = tags.get("name") or tags.get("name:es") or tipo
        especie = (tags.get("species") or tags.get("species:es") or
                   tags.get("taxon")   or tags.get("genus")      or
                   tags.get("leaf_type"))
        coords = [[g["lon"], g["lat"]] for g in geom]
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        clave = f"{round(coords[0][0],2)}|{round(coords[0][1],2)}"
        if clave in vistos:
            continue
        vistos.add(clave)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [coords]},
            "properties": {
                "tipo":    tipo,
                "nombre":  nombre,
                "especie": especie,
                "riesgo":  RIESGO.get(tipo, "DESCONOCIDO")
            }
        })
        if len(features) >= 300:
            break

    return jsonify({"type": "FeatureCollection", "features": features})


# ── Análisis IA (Claude) ──────────────────────────────────────────────────────

_IA_SYSTEM_PROMPT = (
    "Sos un agente de IA especializado en incendios forestales, rurales y de interfaz urbano-forestal "
    "en Argentina y Paraguay. Tenés experiencia en coordinación operativa de brigadas, análisis satelital, "
    "meteorología aplicada al fuego, logística de emergencia y aplicación de la Regla 30-30-30.\n\n"
    "Tu función es analizar en tiempo real focos detectados por satélites y datos meteorológicos, "
    "tomando como referencia las coordenadas seleccionadas por el usuario. Debés generar un análisis "
    "técnico-operativo completo y un protocolo de acción concreto, priorizando siempre la seguridad "
    "del personal y la protección de vidas humanas.\n\n"
    "Para cada coordenada seleccionada, analizá:\n"
    "1. Ubicación del foco.\n"
    "2. Fuente satelital y nivel de confianza.\n"
    "3. Condiciones meteorológicas actuales.\n"
    "4. Aplicación de la Regla 30-30-30.\n"
    "5. Riesgo de propagación.\n"
    "6. Tipo de zona afectada.\n"
    "7. Dirección probable de avance del fuego.\n"
    "8. Cercanía a viviendas, rutas, escuelas, infraestructura crítica, reservas, campos productivos o comunidades.\n"
    "9. Recursos cercanos disponibles: bomberos, Defensa Civil, hospitales o centros de salud, municipios, "
    "policía o fuerzas de seguridad, fuentes de agua, puntos de abastecimiento, rutas y accesos.\n"
    "10. Protocolo de acción recomendado.\n\n"
    "Clasificá el evento como Prioridad 1, 2, 3 o 4:\n"
    "- Prioridad 1: crítica, con riesgo para vidas humanas o propagación extrema.\n"
    "- Prioridad 2: alta, con incendio activo y posible expansión importante.\n"
    "- Prioridad 3: media, con propagación moderada.\n"
    "- Prioridad 4: baja, foco aislado o posible falso positivo.\n\n"
    "Respondé siempre con esta estructura exacta:\n\n"
    "# Análisis operativo de foco de incendio\n\n"
    "## 1. Resumen ejecutivo\n"
    "Incluí coordenadas, ubicación aproximada, prioridad, riesgo meteorológico, riesgo de propagación y recomendación inmediata.\n\n"
    "## 2. Datos detectados\n"
    "Incluí fuente satelital, fecha/hora, confianza y observaciones.\n\n"
    "## 3. Condiciones meteorológicas\n"
    "Incluí temperatura, humedad, viento, dirección, ráfagas, precipitaciones y evaluación Regla 30-30-30.\n\n"
    "## 4. Análisis territorial\n"
    "Describí tipo de zona, vegetación, accesibilidad, infraestructura cercana y dirección probable de avance.\n\n"
    "## 5. Recursos cercanos\n"
    "Listá bomberos, defensa civil, centros de salud, fuentes de agua, rutas de acceso y puntos de abastecimiento más cercanos.\n\n"
    "## 6. Protocolo de acción\n"
    "Dividí en fases: Confirmación · Activación inicial · Seguridad operativa · Ataque inicial o contención · "
    "Logística y abastecimiento · Comunicación y coordinación · Monitoreo · Cierre y reporte.\n\n"
    "## 7. Alertas y advertencias\n"
    "Indicá riesgos principales y medidas preventivas.\n\n"
    "## 8. Incertidumbre del análisis\n"
    "Indicá datos faltantes, supuestos y nivel de confianza general.\n\n"
    "## 9. Recomendación final\n"
    "Cerrá con una acción inmediata, clara y ejecutiva.\n\n"
    "No inventes datos. Si algún dato no está disponible, indicalo claramente. "
    "No recomiendes acciones peligrosas para civiles. Siempre priorizá la seguridad del personal "
    "y recomendá intervención de autoridades competentes cuando el riesgo sea alto, muy alto o extremo."
)


def _llamar_ia(system_prompt, user_prompt, model="claude-sonnet-4-6", max_tokens=2000):
    try:
        import anthropic
    except ImportError:
        return None, (jsonify({"error": "Instala: pip install anthropic"}), 503)
    api_key = get_cfg('ANTHROPIC_API_KEY') or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None, (jsonify({"error": "ANTHROPIC_API_KEY no configurada"}), 503)
    try:
        client = anthropic.Anthropic(api_key=api_key)
        kwargs = {"model": model, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": user_prompt}]}
        if system_prompt:
            kwargs["system"] = system_prompt
        msg = client.messages.create(**kwargs)
        return msg.content[0].text, None
    except Exception as e:
        return None, (jsonify({"error": f"Error al consultar la IA: {str(e)}"}), 502)


@app.route("/ai-risk-analysis", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def ai_risk_analysis():
    body         = request.get_json(silent=True) or {}
    weather_data = body.get("weather", [])
    if not isinstance(weather_data, list) or len(weather_data) > 500:
        return jsonify({"error": "weather inválido"}), 400
    fire_pts = body.get("fire_points", [])
    if not isinstance(fire_pts, list) or len(fire_pts) > 5000:
        return jsonify({"error": "fire_points inválido"}), 400
    fire_count   = max(0, min(int(body.get("fire_count", 0)), 100000))
    agua_data    = body.get("water", [])
    veg_data     = body.get("vegetation", [])
    inpe_count   = max(0, min(int(body.get("inpe_count", 0)), 100000))
    inpe_sats    = body.get("inpe_satelites", [])
    fwi_max      = float(body.get("fwi_max", 0))
    fwi_extremos = max(0, min(int(body.get("fwi_extremos", 0)), 10000))
    smn_amarillo = max(0, min(int(body.get("smn_amarillo", 0)), 1000))
    smn_naranja  = max(0, min(int(body.get("smn_naranja", 0)), 1000))
    smn_rojo     = max(0, min(int(body.get("smn_rojo", 0)), 1000))
    dias         = max(1, min(int(body.get("dias", 1)), 30))
    fwi_alto     = max(0, min(int(body.get("fwi_alto", 0)), 10000))
    fwi_muy_alto = max(0, min(int(body.get("fwi_muy_alto", 0)), 10000))

    # Calcular condiciones 30-30-30
    zonas_3 = [d for d in weather_data if d.get("factores_30") == 3]
    zonas_2 = [d for d in weather_data if d.get("factores_30") == 2]
    temps    = [d["temp"]       for d in weather_data if d.get("temp")       is not None]
    humedds  = [d["humidity"]   for d in weather_data if d.get("humidity")   is not None]
    vientos  = [d["wind_speed"] for d in weather_data if d.get("wind_speed") is not None]

    # Fuentes de agua más relevantes
    tipos_agua = {}
    for a in agua_data:
        t = a.get("tipo","water")
        tipos_agua[t] = tipos_agua.get(t, 0) + 1
    resumen_agua = ", ".join(f"{v} {k}" for k,v in list(tipos_agua.items())[:8]) or "ninguna detectada"

    # Vegetación más frecuente
    tipos_veg = {}
    especies   = set()
    for v in veg_data:
        t = v.get("tipo","?"); tipos_veg[t] = tipos_veg.get(t, 0) + 1
        if v.get("especie"): especies.add(v["especie"])
    resumen_veg     = ", ".join(f"{v} {k}" for k,v in sorted(tipos_veg.items(), key=lambda x:-x[1])[:6]) or "sin datos"
    resumen_especies = ", ".join(list(especies)[:8]) or "no disponible en OSM"

    # Focos alta confianza
    focos_criticos = [f for f in fire_pts if float(f.get("conf",0)) >= 80][:5]
    focos_txt = "\n".join(
        f"  Lat {f['lat']:.3f}, Lon {f['lon']:.3f} — confianza {f['conf']}%"
        for f in focos_criticos
    ) or "  (sin focos de alta confianza)"

    smn_resumen = f"Amarillas: {smn_amarillo} | Naranja: {smn_naranja} | Rojas: {smn_rojo}" if (smn_amarillo + smn_naranja + smn_rojo) > 0 else "Sin alertas activas"
    inpe_resumen = f"{inpe_count} focos — satélites: {', '.join(inpe_sats)}" if inpe_count else "Sin datos INPE"

    prompt = f"""Analizá los siguientes datos satelitales y meteorológicos en tiempo real (período: {dias} día(s)):

═══ FOCOS NASA FIRMS ═══
Total focos activos: {fire_count}
Focos de alta confianza (≥80%):
{focos_txt}

═══ FOCOS INPE (GOES-16 / METOP) ═══
{inpe_resumen}

═══ ALERTAS SMN ARGENTINA ═══
{smn_resumen}

═══ REGLA 30-30-30 ═══
Zonas con 3/3 condiciones críticas (temp>30°C, viento>30km/h, humedad<30%): {len(zonas_3)}
Zonas con 2/3 condiciones: {len(zonas_2)}
Temperatura máxima: {max(temps, default='N/D')}°C | Humedad mínima: {min(humedds, default='N/D')}% | Viento máximo: {max(vientos, default='N/D')} km/h

═══ ÍNDICE FWI (Fire Weather Index) ═══
FWI máximo detectado: {fwi_max:.1f}
Zonas FWI extremo (≥24): {fwi_extremos} | Zonas FWI muy alto (≥17): {fwi_muy_alto} | Zonas FWI alto (≥10): {fwi_alto}

═══ FUENTES DE AGUA (OSM) ═══
{resumen_agua} — Total: {len(agua_data)} fuentes

═══ VEGETACIÓN EN EL ÁREA ═══
Tipos: {resumen_veg}
Especies: {resumen_especies}

Respondé con exactamente esta estructura en markdown:

# Análisis operativo de foco de incendio

## 1. Resumen ejecutivo
Coordenadas de focos críticos, ubicación aproximada, prioridad asignada, riesgo meteorológico, riesgo de propagación y recomendación inmediata.

## 2. Datos detectados
Fuentes satelitales activas, fechas/horas de detección, niveles de confianza y observaciones relevantes.

## 3. Condiciones meteorológicas
Temperatura, humedad, viento (velocidad y dirección), ráfagas, precipitaciones y evaluación de la Regla 30-30-30.

## 4. Análisis territorial
Tipo de zona afectada, vegetación predominante, accesibilidad, infraestructura cercana y dirección probable de avance del fuego.

## 5. Recursos cercanos
Bomberos, Defensa Civil, centros de salud, fuentes de agua, rutas de acceso y puntos de abastecimiento más próximos.

## 6. Protocolo de acción
Fases: Confirmación · Activación inicial · Seguridad operativa · Ataque inicial o contención · Logística · Comunicación y coordinación · Monitoreo · Cierre y reporte.

## 7. Alertas y advertencias
Riesgos principales identificados y medidas preventivas recomendadas.

## 8. Incertidumbre del análisis
Datos faltantes, supuestos utilizados y nivel de confianza general del análisis.

## 9. Recomendación final
Acción inmediata, clara y ejecutiva."""

    analysis_txt, err = _llamar_ia(_IA_SYSTEM_PROMPT, prompt, max_tokens=2400)
    if err:
        return err

    try:
        sev = ('critical' if (fire_count > 100 or fwi_max >= 24)
               else 'high' if (fire_count > 30 or fwi_max >= 12)
               else 'medium')
        db.session.add(AiInforme(
            region='Argentina',
            severidad=sev,
            ha=None,
            user_id=current_user.id,
            analysis_text=analysis_txt,
            tipo_foco='general',
            fwi_val=fwi_max,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    return jsonify({
        "analysis":   analysis_txt,
        "zonas_3":    zonas_3,
        "zonas_2":    zonas_2,
        "fire_count": fire_count
    })


# ── Análisis IA de foco específico ──────────────────────────────────────────

def _region_argentina(lat, lon):
    # Paraguay: lat -19.3 a -27.6, lon -54.2 a -62.6
    if lat > -27.6 and lon > -62.6 and lon < -54.2 and lat < -19.2:
        if lon > -58.0:
            return "Paraguay Oriental — Asunción / Central / Alto Paraná"
        else:
            return "Chaco Paraguayo — Boquerón / Alto Paraguay"
    if lat > -19.2 and lon > -62.6 and lon < -54.2:
        return "Paraguay Norte — Amambay / Concepción"
    # Argentina
    if lat > -23:                        return "NOA — Jujuy / Salta / Tucumán"
    if lat > -28 and lon < -60:          return "Chaco / Formosa"
    if lat > -28 and lon >= -60:         return "Misiones / Corrientes"
    if lat > -32 and lon < -65:          return "Cuyo — Mendoza / San Juan"
    if lat > -32 and lon >= -65:         return "Litoral — Entre Ríos / Santa Fe"
    if lat > -38:                        return "Pampas — Buenos Aires / Córdoba / La Pampa"
    if lat > -42:                        return "Patagonia Norte — Neuquén / Río Negro"
    if lat > -50:                        return "Patagonia Sur — Chubut / Santa Cruz"
    return "Tierra del Fuego"

def _recursos_para_ia(lat_ref=None, lon_ref=None, max_items=30):
    """Devuelve string formateado con todos los recursos activos, ordenados por distancia si se dan coords."""
    try:
        recursos = Recurso.query.filter_by(activo=True).all()
        if not recursos:
            return ""
        if lat_ref is not None and lon_ref is not None:
            def _dist(r):
                if r.lat and r.lon:
                    dlat = r.lat - lat_ref
                    dlon = (r.lon - lon_ref) * math.cos(math.radians(lat_ref))
                    return dlat**2 + dlon**2
                return float('inf')
            recursos = sorted(recursos, key=_dist)[:max_items]
        lines = ["═══ RECURSOS REGISTRADOS EN LA BASE DE DATOS SPIYD ═══"]
        for r in recursos:
            distancia = ""
            if lat_ref and lon_ref and r.lat and r.lon:
                dlat = r.lat - lat_ref
                dlon = (r.lon - lon_ref) * math.cos(math.radians(lat_ref))
                km = math.sqrt(dlat**2 + dlon**2) * 111
                distancia = f" (~{km:.0f} km del foco)"
            partes = [f"[{r.tipo_label}] {r.nombre}{distancia}"]
            if r.localidad or r.provincia_departamento:
                partes.append(f"  Ubicación: {', '.join(filter(None, [r.localidad, r.provincia_departamento, r.pais]))}")
            if r.lat and r.lon:
                partes.append(f"  Coords: {r.lat:.4f}, {r.lon:.4f}")
            if r.telefono:
                partes.append(f"  Tel: {r.telefono}")
            if r.contacto_nombre:
                partes.append(f"  Contacto: {r.contacto_nombre}")
            if r.horario:
                partes.append(f"  Horario: {r.horario}")
            if r.descripcion:
                partes.append(f"  Descripción: {r.descripcion}")
            lines.append("\n".join(partes))
        return "\n\n".join(lines)
    except Exception:
        return ""


@app.route("/ai-foco-analysis", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def ai_foco_analysis():
    body      = request.get_json(silent=True) or {}
    lat       = float(body.get("lat", -34))
    lon       = float(body.get("lon", -64))
    conf      = body.get("conf", "N/D")
    brillo    = float(body.get("brillo", 0) or 0)
    satellite = body.get("satellite", "N/D")
    fecha     = body.get("fecha", "N/D")
    fwi_local = float(body.get("fwi_local", 0) or 0)
    smn_rojo  = int(body.get("smn_rojo", 0))
    smn_nar   = int(body.get("smn_naranja", 0))
    smn_amar  = int(body.get("smn_amarillo", 0))
    frp       = body.get("frp")       # Fire Radiative Power (MW) si viene de INPE
    tipo_foco = body.get("tipo_foco", "NASA FIRMS")  # "NASA FIRMS" | "INPE"

    # ── Clima real del punto ────────────────────────────────────────────────
    wx = {}
    try:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,"
               f"wind_direction_10m,precipitation,weather_code"
               f"&wind_speed_unit=kmh&timezone=America%2FArgentina%2FBuenos_Aires")
        wx = requests.get(url, timeout=8).json().get("current", {})
    except Exception:
        pass

    temp     = wx.get("temperature_2m", "N/D")
    humid    = wx.get("relative_humidity_2m", "N/D")
    wind_spd = wx.get("wind_speed_10m", "N/D")
    wind_dir = wx.get("wind_direction_10m", "N/D")
    precip   = wx.get("precipitation", 0)

    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
    try:
        wind_dir_str = dirs[round(float(wind_dir) / 22.5) % 16]
    except Exception:
        wind_dir_str = "?"

    regla_30 = (
        isinstance(temp, (int, float)) and temp >= 30 and
        isinstance(humid, (int, float)) and humid <= 30 and
        isinstance(wind_spd, (int, float)) and wind_spd >= 30
    )

    # ── FWI nivel ─────────────────────────────────────────────────────────
    if   fwi_local >= 24: fwi_nivel = "EXTREMO"
    elif fwi_local >= 17: fwi_nivel = "MUY ALTO"
    elif fwi_local >= 10: fwi_nivel = "ALTO"
    elif fwi_local >= 5:  fwi_nivel = "MODERADO"
    else:                 fwi_nivel = "BAJO"

    # ── Agua en radio 50 km (Overpass) ─────────────────────────────────────
    agua_txt = "no consultada"
    agua_count = 0
    bomberos_count = 0
    try:
        rad = 50000
        ql = f"""[out:json][timeout:20];
(
  node["natural"~"^(spring|water)$"](around:{rad},{lat},{lon});
  node["amenity"="fire_station"](around:{rad},{lat},{lon});
  node["man_made"~"^(water_tower|reservoir)$"](around:{rad},{lat},{lon});
  way["waterway"~"^(river|stream|canal)$"](around:{rad},{lat},{lon});
);
out center tags;"""
        elems = requests.post(
            "https://overpass-api.de/api/interpreter", data=ql, timeout=20
        ).json().get("elements", [])
        tipos = {}
        for e in elems[:30]:
            tags = e.get("tags", {})
            t = (tags.get("natural") or tags.get("amenity") or
                 tags.get("waterway") or tags.get("man_made") or "agua")
            if t == "fire_station":
                bomberos_count += 1
            else:
                tipos[t] = tipos.get(t, 0) + 1
        agua_count  = sum(tipos.values())
        agua_txt    = ", ".join(f"{v} {k}" for k, v in tipos.items()) or "sin fuentes detectadas"
    except Exception:
        pass

    # ── Prompt ─────────────────────────────────────────────────────────────
    region = _region_argentina(lat, lon)

    frp_txt = f"{frp:.1f} MW" if frp is not None else "N/D"
    prompt = f"""Se detectó un foco activo específico. El usuario seleccionó este punto en el mapa para análisis detallado. Generá un informe de incidente completo y accionable:

═══ FOCO SELECCIONADO POR EL USUARIO ═══
Fuente: {tipo_foco}
Coordenadas: {lat:.4f}°S, {abs(lon):.4f}°O
Región: {region}
Satélite: {satellite} | Confianza: {conf}% | Temperatura radiativa: {brillo:.1f} K
Potencia radiativa del fuego (FRP): {frp_txt}
Fecha/hora detección: {fecha}

═══ METEOROLOGÍA LOCAL (Open-Meteo, tiempo real) ═══
Temperatura: {temp}°C | Humedad: {humid}% | Precipitación reciente: {precip} mm
Viento: {wind_spd} km/h rumbo {wind_dir_str} ({wind_dir}°)
{"⚠️ REGLA 30-30-30 ACTIVA — condiciones de propagación explosiva." if regla_30 else "Regla 30-30-30: no activa."}

═══ ÍNDICE FWI (Fire Weather Index) ═══
FWI estimado local: {fwi_local:.1f} — Nivel: {fwi_nivel}

═══ RECURSOS HÍDRICOS EN RADIO 50 km ═══
Fuentes detectadas: {agua_txt}
Total fuentes: {agua_count} | Cuarteles de bomberos: {bomberos_count}

═══ ALERTAS SMN ═══
Alertas activas — Roja: {smn_rojo} | Naranja: {smn_nar} | Amarilla: {smn_amar}

{_recursos_para_ia(lat, lon)}

Generá exactamente estas 7 secciones numeradas en español técnico-operativo (máx 420 palabras, cada sección 1-3 oraciones directas sin explicaciones innecesarias):

1. 🚨 EVALUACIÓN INICIAL: Nivel [BAJO/MODERADO/ALTO/CRÍTICO] y justificación concisa en base a FWI, viento y confianza del satélite.

2. 🔥 COMPORTAMIENTO DEL FUEGO: Dirección y velocidad de propagación esperada según viento ({wind_dir_str} a {wind_spd} km/h), intensidad estimada y tipo de fuego probable para la región {region}.

3. 🗺️ ZONA DE IMPACTO: Área afectable en las próximas 2-6 horas, dirección de avance prioritaria, infraestructura o poblaciones en riesgo.

4. 💧 RECURSOS HÍDRICOS: Qué fuentes usar primero, distancia estimada al foco, logística de abastecimiento para autobombas.

5. 🚒 ESTRATEGIA DE ATAQUE: Táctica recomendada (ataque directo / indirecto / paralelo), puntos de anclaje, líneas de contención y flancos a priorizar.

6. 🚁 DESPLIEGUE DE RECURSOS: Recursos aéreos (helicópteros, aviones hidrantes) y terrestres (cuadrillas, autobombas) a movilizar, base de operaciones sugerida.

7. ⚡ PRIMERAS ACCIONES (próximos 30 min): Lista de exactamente 5 acciones inmediatas ordenadas por urgencia, con responsable sugerido (bomberos/defensa civil/cuadrilla forestal)."""

    analysis_txt, err = _llamar_ia(_IA_SYSTEM_PROMPT, prompt, max_tokens=2000)
    if err:
        return err

    try:
        db.session.add(AiInforme(
            region=_region_argentina(lat, lon),
            severidad=_fwi_to_severidad(fwi_local),
            ha=None,
            user_id=current_user.id,
            analysis_text=analysis_txt,
            lat=lat,
            lon=lon,
            satellite=satellite,
            conf=str(conf),
            fwi_val=fwi_local,
            tipo_foco=tipo_foco,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

    return jsonify({"analysis": analysis_txt})


# ── Análisis IA de zona geográfica seleccionada por el usuario ───────────────

@app.route("/ai-zona-analysis", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def ai_zona_analysis():
    body        = request.get_json(silent=True) or {}
    s           = float(body.get("s", -35))
    w           = float(body.get("w", -65))
    n           = float(body.get("n", -33))
    e           = float(body.get("e", -63))
    focos_count = int(body.get("focos_count", 0))
    fwi_max     = float(body.get("fwi_max", 0) or 0)
    fwi_prom    = float(body.get("fwi_prom", 0) or 0)
    agua_count  = int(body.get("agua_count", 0))
    smn_rojo    = int(body.get("smn_rojo", 0))
    smn_nar     = int(body.get("smn_naranja", 0))
    smn_amar    = int(body.get("smn_amarillo", 0))

    lat = (s + n) / 2
    lon = (w + e) / 2
    area_km2 = round(abs((n - s) * (e - w)) * 12100)

    # Clima del centro de la zona
    wx = {}
    try:
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,"
               f"wind_direction_10m,precipitation,weather_code"
               f"&wind_speed_unit=kmh&timezone=America%2FArgentina%2FBuenos_Aires")
        wx = requests.get(url, timeout=8).json().get("current", {})
    except Exception:
        pass

    temp     = wx.get("temperature_2m", "N/D")
    humid    = wx.get("relative_humidity_2m", "N/D")
    wind_spd = wx.get("wind_speed_10m", "N/D")
    wind_dir = wx.get("wind_direction_10m", "N/D")
    precip   = wx.get("precipitation", 0)

    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSO","SO","OSO","O","ONO","NO","NNO"]
    try:
        wind_dir_str = dirs[round(float(wind_dir) / 22.5) % 16]
    except Exception:
        wind_dir_str = "?"

    regla_30 = (
        isinstance(temp, (int, float)) and temp >= 30 and
        isinstance(humid, (int, float)) and humid <= 30 and
        isinstance(wind_spd, (int, float)) and wind_spd >= 30
    )

    if   fwi_max >= 24: fwi_nivel = "EXTREMO"
    elif fwi_max >= 17: fwi_nivel = "MUY ALTO"
    elif fwi_max >= 10: fwi_nivel = "ALTO"
    elif fwi_max >= 5:  fwi_nivel = "MODERADO"
    else:               fwi_nivel = "BAJO"

    region = _region_argentina(lat, lon)

    prompt = f"""El usuario seleccionó una zona geográfica en el mapa de monitoreo de incendios de Argentina y Paraguay para análisis con IA. Generá un análisis táctico de riesgo de incendio para esa zona:

═══ ZONA SELECCIONADA POR EL USUARIO ═══
Región: {region}
Bounding box: S{s:.3f}° N{n:.3f}° O{abs(w):.3f}° E{abs(e):.3f}°
Área aproximada: ~{area_km2:,} km²
Centro: {lat:.3f}°S, {abs(lon):.3f}°O

═══ FOCOS ACTIVOS DENTRO DE LA ZONA ═══
Total focos satelitales detectados: {focos_count}

═══ CLIMA EN EL CENTRO DE LA ZONA (Open-Meteo, tiempo real) ═══
Temperatura: {temp}°C | Humedad: {humid}% | Precipitación: {precip} mm/h
Viento: {wind_spd} km/h rumbo {wind_dir_str}
{"⚡ REGLA 30-30-30 ACTIVA — condiciones de propagación explosiva." if regla_30 else "Regla 30-30-30: no activa."}

═══ ÍNDICE FWI EN LA ZONA ═══
FWI máximo: {fwi_max:.1f} — nivel {fwi_nivel}
FWI promedio: {fwi_prom:.1f}

═══ RECURSOS HÍDRICOS EN LA ZONA ═══
Fuentes de agua detectadas: {agua_count}

═══ ALERTAS SMN ACTIVAS ═══
Roja: {smn_rojo} | Naranja: {smn_nar} | Amarilla: {smn_amar}

{_recursos_para_ia(lat, lon)}

Generá exactamente estas 5 secciones numeradas (español, conciso, operativo):
1. 🚨 EVALUACIÓN DE RIESGO: nivel general CRÍTICO/ALTO/MODERADO/BAJO y justificación en 2 oraciones.
2. 🔥 SITUACIÓN ACTUAL: qué está ocurriendo en esta zona, focos, propagación esperada según el viento ({wind_dir_str} a {wind_spd} km/h).
3. 💧 RECURSOS DISPONIBLES: fuentes de agua detectadas y acceso para extinción en la zona.
4. 🚒 ACCIONES RECOMENDADAS: 3 acciones concretas y prioritarias para esta zona.
5. ⚡ FACTOR CRÍTICO: el elemento de mayor riesgo que define la situación en esta zona."""

    analysis_txt, err = _llamar_ia(None, prompt, model="claude-haiku-4-5-20251001", max_tokens=600)
    if err:
        return err

    return jsonify({"analysis": analysis_txt})


# ── Precipitación grid ───────────────────────────────────────────────────────

@app.route("/precipitacion")
@login_required
def precipitacion():
    lats = list(range(-22, -56, -2))
    lons = list(range(-74, -52, 2))
    puntos = [(lat, lon) for lat in lats for lon in lons]

    def fetch_precip(lat, lon):
        url = (f"https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               f"&current=precipitation,rain,showers,snowfall"
               f"&hourly=precipitation&forecast_hours=3"
               f"&timezone=auto")
        try:
            d = requests.get(url, timeout=10).json()
            c = d.get("current", {})
            precip_now  = (c.get("precipitation") or 0)
            rain_now    = (c.get("rain") or 0)
            shower_now  = (c.get("showers") or 0)
            snow_now    = (c.get("snowfall") or 0)
            # próximas 3 horas
            hourly = d.get("hourly", {}).get("precipitation", [0,0,0])
            prox3h = sum(hourly[:3])
            return {"lat": lat, "lon": lon,
                    "precip_mm": round(precip_now, 2),
                    "rain":   round(rain_now, 2),
                    "shower": round(shower_now, 2),
                    "snow":   round(snow_now, 2),
                    "prox3h": round(prox3h, 2)}
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
        resultados = list(ex.map(lambda p: fetch_precip(*p), puntos))

    return jsonify([r for r in resultados if r is not None])


# ── Clima grid / Wind data ────────────────────────────────────────────────────


@app.route("/weather-grid")
@login_required
def weather_grid():
    import time as _t; t0=_t.time()
    datos = obtener_grilla_clima()
    elapsed = _t.time()-t0
    import sys
    sys.stderr.write(f"[weather-grid] {elapsed:.2f}s pts={len(datos)} cached={elapsed<0.1}\n")
    sys.stderr.flush()
    for d in datos:
        f = 0
        if d["temp"]       is not None and d["temp"]       >= 30: f += 1
        if d["wind_speed"] is not None and d["wind_speed"] >= 30: f += 1
        if d["humidity"]   is not None and d["humidity"]   <= 30: f += 1
        d["factores_30"] = f
    return jsonify(datos)

SA_LATS = list(range(12, -57, -5))   # 12,7,2,-3,...,-53  → 14 filas
SA_LONS = list(range(-82, -34, 5))   # -82,-77,...,-37    → 10 columnas
_wind_cache = {"data": None, "ts": 0}
WIND_CACHE_TTL = 1800  # 30 minutos
_wind_lock = threading.RLock()

def _build_wind_data():
    import time as _t
    with _wind_lock:
        if _wind_cache["data"] and (_t.time() - _wind_cache["ts"]) < WIND_CACHE_TTL:
            return _wind_cache["data"]
        lats = [lat for lat in SA_LATS for _ in SA_LONS]
        lons = [lon for _ in SA_LATS for lon in SA_LONS]
        datos = fetch_multi_points(lats, lons)
        nx, ny = len(SA_LONS), len(SA_LATS)
        u_data, v_data, ref_time = [], [], None
        for d in datos:
            if ref_time is None: ref_time = d.get("time")
            u, v = wind_to_uv(d.get("wind_speed") or 0, d.get("wind_dir") or 0)
            u_data.append(u); v_data.append(v)
        header = {
            "parameterCategory": 2,
            "lo1": SA_LONS[0], "la1": SA_LATS[0],
            "lo2": SA_LONS[-1], "la2": SA_LATS[-1],
            "dx": 5, "dy": 5, "nx": nx, "ny": ny,
            "refTime": ref_time or datetime.now().isoformat()
        }
        result = [
            {"header": {**header, "parameterNumber": 2}, "data": u_data},
            {"header": {**header, "parameterNumber": 3}, "data": v_data}
        ]
        _wind_cache["data"] = result
        _wind_cache["ts"]   = _t.time()
        return result

@app.route("/wind-data")
@login_required
def wind_data():
    return jsonify(_build_wind_data())


# ── SMN alertas ───────────────────────────────────────────────────────────────

@app.route("/smn-alertas")
@login_required
def smn_alertas():
    alertas = obtener_alertas_smn(request.args.get("provincia","").strip())
    _guardar_smn(alertas)
    return jsonify(alertas)


# ── Telegram ──────────────────────────────────────────────────────────────────

@app.route("/telegram-alerta", methods=["POST"])
@login_required
@limiter.limit("5 per minute")
def telegram_alerta():
    data          = request.get_json(silent=True) or {}
    provincia     = data.get("provincia","Sin provincia")
    focos         = int(data.get("focos",0))
    alertas_rojas = int(data.get("alertas_rojas",0))
    fuente        = data.get("fuente","")
    dias          = data.get("dias","")
    detalle       = data.get("detalle","")

    if focos <= 0 or alertas_rojas <= 0:
        return jsonify({"ok": True, "enviado": False, "motivo": "No cumple condición"})

    clave = f"{provincia}|{focos}|{alertas_rojas}|{fuente}|{dias}|{detalle[:120]}"
    if clave in ultimas_alertas_enviadas:
        return jsonify({"ok": True, "enviado": False, "motivo": "Ya enviado"})

    mensaje = (f"🔥⚠️ Alerta combinada\nProvincia: {provincia}\n"
               f"Focos: {focos}\nAlertas rojas SMN: {alertas_rojas}\n"
               f"Fuente: {fuente}\nVentana: {dias} día(s)\n"
               f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
    if detalle: mensaje += f"Detalle: {detalle[:1000]}"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r   = requests.post(url, json={"chat_id": CHAT_ID, "text": mensaje}, timeout=20)
    r.raise_for_status()

    ultimas_alertas_enviadas.add(clave)
    if len(ultimas_alertas_enviadas) > 200:
        while len(ultimas_alertas_enviadas) > 150:
            ultimas_alertas_enviadas.pop()

    return jsonify({"ok": True, "enviado": True})


# ── INPE dataserver-coids (nuevo endpoint desde 2025) ─────────────────────────

INPE_BASE = "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv"

@app.route("/inpe-focos")
@login_required
def inpe_focos():
    """
    Proxy para INPE dataserver-coids — focos diarios América del Sur,
    filtrados por bounding box Argentina + Paraguay.
    """
    import csv, io
    dias = int(request.args.get("dias", "1"))
    dias = max(1, min(dias, 3))

    todos  = []
    vistos = set()
    sats_vistos = set()

    for delta in range(dias):
        try:
            from datetime import timedelta
            fecha = (datetime.utcnow() - timedelta(days=delta)).strftime("%Y%m%d")
            url = f"{INPE_BASE}/diario/America_Sul/focos_diario_{fecha}.csv"
            r = requests.get(url, timeout=25, headers={"User-Agent": "SPIYDFireMonitor/2.0"})
            if not r.ok:
                continue
            reader = csv.DictReader(io.StringIO(r.text))
            for row in reader:
                try:
                    lat = float(row.get("lat", ""))
                    lon = float(row.get("lon", ""))
                except (ValueError, TypeError):
                    continue
                if not (-55.8 <= lat <= -19.0 and -73.5 <= lon <= -53.5):
                    continue
                clave = f"{round(lat,3)}|{round(lon,3)}"
                if clave in vistos:
                    continue
                vistos.add(clave)
                sat = row.get("satelite", "INPE")
                sats_vistos.add(sat)
                todos.append({
                    "lat":      lat,
                    "lon":      lon,
                    "satelite": sat,
                    "datahora": row.get("data_hora_gmt", ""),
                    "frp":      row.get("frp") or None,
                    "bioma":    row.get("bioma", ""),
                    "pais":     row.get("pais", ""),
                    "risco":    row.get("risco_fogo", ""),
                })
        except Exception:
            continue

    _guardar_focos([{
        'lat': f['lat'], 'lon': f['lon'],
        'fuente': f['satelite'],
        'conf': '70',
        'frp': f.get('frp'),
    } for f in todos])

    return jsonify({"focos": todos, "total": len(todos), "satelites": sorted(sats_vistos)})


# ── FWI por grilla ────────────────────────────────────────────────────────────

@app.route("/fwi-grid")
@login_required
def fwi_grid():
    """
    Calcula el FWI (Fire Weather Index) canadiense para cada punto de la grilla,
    usando datos meteorológicos de Open-Meteo en tiempo real.
    """
    mes = datetime.now().month
    datos = obtener_grilla_clima()
    resultado = []

    for d in datos:
        t  = d.get("temp")
        rh = d.get("humidity")
        ws = d.get("wind_speed")
        rn = d.get("rain") or 0

        if t is None or rh is None or ws is None:
            resultado.append({**d, "fwi": None, "clase": "SIN DATOS",
                               "ffmc": None, "isi": None, "bui": None})
            continue

        # Factores 30-30-30
        f30 = sum([t >= 30, ws >= 30, rh <= 30])

        fwi_data = calc_fwi(t, rh, ws, rn, mes)
        resultado.append({
            "lat":        d["lat"],
            "lon":        d["lon"],
            "temp":       t,
            "humidity":   rh,
            "wind_speed": ws,
            "rain":       rn,
            "factores_30": f30,
            **fwi_data
        })

    return jsonify(resultado)


# ── NASA FIRMS proxy ─────────────────────────────────────────────────────────

FUENTES_NASA = ['VIIRS_SNPP_NRT', 'VIIRS_NOAA20_NRT', 'VIIRS_NOAA21_NRT', 'MODIS_NRT', 'GOES_NRT']
BBOX_ARG = "-73.5,-55.8,-53.5,-19.0"  # Argentina + Paraguay

@app.route("/nasa-focos")
@login_required
def nasa_focos():
    import csv, io
    fuente = request.args.get("fuente", "VIIRS_SNPP_NRT")
    dias   = request.args.get("dias", "1")
    if fuente not in FUENTES_NASA:
        return jsonify({"error": "fuente inválida"}), 400
    key = get_cfg('NASA_MAP_KEY') or NASA_MAP_KEY
    if not key:
        return jsonify({"error": "NASA_MAP_KEY no configurada"}), 503
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{fuente}/{BBOX_ARG}/{dias}"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "ArgentinaFireMonitor/1.0"})
        r.raise_for_status()
        try:
            reader = csv.DictReader(io.StringIO(r.text))
            focos = []
            for row in reader:
                lat = row.get('latitude') or row.get('lat')
                lon = row.get('longitude') or row.get('lon')
                if not lat or not lon:
                    continue
                focos.append({
                    'lat': lat, 'lon': lon,
                    'fuente': fuente,
                    'conf': row.get('confidence', '50'),
                    'frp': row.get('frp'),
                })
            _guardar_focos(focos)
        except Exception:
            pass
        return r.text, 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as ex:
        return jsonify({"error": str(ex)}), 502


# ── Resumen diario ────────────────────────────────────────────────────────────

def _construir_datos_resumen():
    """Retorna dict con estadísticas del día anterior."""
    from models import SystemLog as _SL
    hoy = datetime.utcnow().date()
    ayer_inicio = datetime.combine(hoy - __import__('datetime').timedelta(days=1), datetime.min.time())
    ayer_fin    = datetime.combine(hoy, datetime.min.time())
    with app.app_context():
        focos_total   = FocoLog.query.filter(FocoLog.timestamp >= ayer_inicio, FocoLog.timestamp < ayer_fin).count()
        focos_criticos = FocoLog.query.filter(FocoLog.timestamp >= ayer_inicio, FocoLog.timestamp < ayer_fin, FocoLog.severidad == 'critical').count()
        focos_altos   = FocoLog.query.filter(FocoLog.timestamp >= ayer_inicio, FocoLog.timestamp < ayer_fin, FocoLog.severidad == 'high').count()
        ai_total      = AiInforme.query.filter(AiInforme.timestamp >= ayer_inicio, AiInforme.timestamp < ayer_fin).count()
        smn_total     = SmnAlerta.query.filter(SmnAlerta.timestamp >= ayer_inicio, SmnAlerta.timestamp < ayer_fin).count()
        admins        = User.query.filter(User.role.in_(['admin', 'superadmin']), User.active == True).all()
    return {
        'fecha': ayer_inicio.strftime('%d/%m/%Y'),
        'focos_total': focos_total,
        'focos_criticos': focos_criticos,
        'focos_altos': focos_altos,
        'ai_total': ai_total,
        'smn_total': smn_total,
        'admins': admins,
    }


def _enviar_resumen_telegram(d):
    if get_cfg('TELEGRAM_ENABLED', 'true') == 'false':
        return
    token = get_cfg('TELEGRAM_BOT_TOKEN') or BOT_TOKEN
    chat  = get_cfg('TELEGRAM_CHAT_ID')   or CHAT_ID
    if not token or not chat:
        return
    texto = (
        f"📊 *Resumen diario SPIYD — {d['fecha']}*\n\n"
        f"🔥 Focos detectados: *{d['focos_total']}*\n"
        f"   ├ Críticos: {d['focos_criticos']}\n"
        f"   └ Altos: {d['focos_altos']}\n\n"
        f"🤖 Análisis IA generados: *{d['ai_total']}*\n"
        f"⚡ Alertas SMN: *{d['smn_total']}*\n\n"
        f"🌐 Ver mapa: https://spiyd.com/mapa"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto, "parse_mode": "Markdown"},
            timeout=15
        )
    except Exception as ex:
        app.logger.error(f"[RESUMEN] Telegram error: {ex}")


def _enviar_resumen_email(d):
    if get_cfg('EMAIL_ENABLED', 'false') == 'false':
        return
    smtp_host = get_cfg('SMTP_HOST') or os.environ.get('SMTP_HOST', '')
    smtp_port = int(get_cfg('SMTP_PORT') or os.environ.get('SMTP_PORT', '587'))
    smtp_user = get_cfg('SMTP_USER') or os.environ.get('SMTP_USER', '')
    smtp_pass = get_cfg('SMTP_PASS') or os.environ.get('SMTP_PASS', '')
    smtp_from = get_cfg('SMTP_FROM') or os.environ.get('SMTP_FROM', smtp_user)
    if not smtp_host or not smtp_user or not smtp_pass:
        return
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    destinatarios = [u.email for u in d['admins'] if u.email]
    if not destinatarios:
        return
    asunto = f"[SPIYD] Resumen diario {d['fecha']}"
    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;background:#0d0f17;color:#e0e0e0;border-radius:12px;overflow:hidden">
      <div style="background:linear-gradient(135deg,#f97316,#dc2626);padding:20px 28px">
        <h2 style="margin:0;color:#fff;font-size:18px">🔥 SPIYD — Resumen {d['fecha']}</h2>
      </div>
      <div style="padding:24px 28px">
        <table style="width:100%;border-collapse:collapse">
          <tr><td style="padding:8px 0;color:#888;font-size:13px">Focos detectados</td><td style="text-align:right;font-weight:700;font-size:16px;color:#f97316">{d['focos_total']}</td></tr>
          <tr><td style="padding:8px 0;color:#888;font-size:13px">— Críticos</td><td style="text-align:right;color:#ef4444">{d['focos_criticos']}</td></tr>
          <tr><td style="padding:8px 0;color:#888;font-size:13px">— Altos</td><td style="text-align:right;color:#f59e0b">{d['focos_altos']}</td></tr>
          <tr><td style="padding:8px 0;color:#888;font-size:13px">Análisis IA</td><td style="text-align:right;color:#a78bfa">{d['ai_total']}</td></tr>
          <tr><td style="padding:8px 0;color:#888;font-size:13px">Alertas SMN</td><td style="text-align:right;color:#60a5fa">{d['smn_total']}</td></tr>
        </table>
        <div style="margin-top:20px;text-align:center">
          <a href="https://spiyd.com/mapa" style="background:#f97316;color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-weight:700;font-size:13px">Ver mapa →</a>
        </div>
      </div>
    </div>"""
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = asunto
        msg['From']    = smtp_from
        msg['To']      = ', '.join(destinatarios)
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, destinatarios, msg.as_string())
        app.logger.info(f"[RESUMEN] Email enviado a {destinatarios}")
    except Exception as ex:
        app.logger.error(f"[RESUMEN] Email error: {ex}")


def _intentar_enviar_resumen():
    """Envía el resumen del día usando DB como lock anti-duplicado entre workers."""
    from models import SystemLog as _SL
    hoy_str = datetime.utcnow().strftime('%Y-%m-%d')
    with app.app_context():
        try:
            existente = _SL.query.filter_by(key='daily_summary_sent').with_for_update(skip_locked=True).first()
            if existente:
                if existente.value == hoy_str:
                    return  # ya enviado hoy
                existente.value = hoy_str
                existente.updated_at = datetime.utcnow()
            else:
                db.session.add(_SL(key='daily_summary_sent', value=hoy_str))
            db.session.commit()
        except Exception:
            db.session.rollback()
            return
        d = _construir_datos_resumen()
        _enviar_resumen_telegram(d)
        _enviar_resumen_email(d)
        app.logger.info(f"[RESUMEN] Enviado para {hoy_str}")


def _daily_summary_loop():
    import time
    while True:
        time.sleep(60)
        try:
            if get_cfg('SUMMARY_ENABLED', 'true') == 'false':
                continue
            hora = int(get_cfg('SUMMARY_HOUR_UTC', '11'))
            if datetime.utcnow().hour == hora:
                _intentar_enviar_resumen()
        except Exception as ex:
            app.logger.error(f"[RESUMEN] Loop error: {ex}")


@app.route('/sismos')
@login_required
@limiter.limit("60 per minute")
def sismos():
    from datetime import timedelta as _td
    try:
        periodo = request.args.get('periodo', '24h')
        minmag_override = request.args.get('minmag')
        periodo_horas = {'1h': 1, '24h': 24, '7d': 168, '30d': 720}.get(periodo, 24)
        if minmag_override:
            minmag = float(minmag_override)
        elif periodo_horas <= 24:
            minmag = 3.5
        elif periodo_horas <= 168:
            minmag = 4.0
        else:
            minmag = 4.5
        starttime = (datetime.utcnow() - _td(hours=periodo_horas)).strftime('%Y-%m-%dT%H:%M:%S')
        params = {
            'format':       'geojson',
            'minlatitude':  -56, 'maxlatitude':  -17,
            'minlongitude': -76, 'maxlongitude': -66,
            'minmagnitude': minmag,
            'starttime':    starttime,
            'orderby':      'time',
            'limit':        500,
        }
        r = requests.get(
            'https://earthquake.usgs.gov/fdsnws/event/1/query',
            params=params, timeout=15,
            headers={'User-Agent': 'SPIYD-FireMonitor/1.0'}
        )
        r.raise_for_status()
        features = r.json().get('features', [])
        result = []
        for f in features:
            p = f['properties']
            c = f['geometry']['coordinates']
            result.append({
                'lon':         round(float(c[0]), 4),
                'lat':         round(float(c[1]), 4),
                'profundidad': round(float(c[2]), 1),
                'mag':         p.get('mag'),
                'lugar':       p.get('place', ''),
                'fecha':       datetime.utcfromtimestamp(p['time'] / 1000).strftime('%Y-%m-%d %H:%M') if p.get('time') else '',
                'tipo':        p.get('type', 'earthquake'),
                'url':         p.get('url', ''),
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Volcanes activos de Chile — datos base SERNAGEOMIN + GVP histórico
_VOLCANES_CHILE = [
    {'nombre': 'Villarrica',           'lat': -39.42, 'lon': -71.93, 'ultima_erupcion': 2024},
    {'nombre': 'Nevados de Chillán',   'lat': -36.86, 'lon': -71.38, 'ultima_erupcion': 2023},
    {'nombre': 'Láscar',               'lat': -23.37, 'lon': -67.73, 'ultima_erupcion': 2023},
    {'nombre': 'Copahue',              'lat': -37.86, 'lon': -71.17, 'ultima_erupcion': 2021},
    {'nombre': 'Llaima',               'lat': -38.69, 'lon': -71.73, 'ultima_erupcion': 2009},
    {'nombre': 'Calbuco',              'lat': -41.33, 'lon': -72.61, 'ultima_erupcion': 2015},
    {'nombre': 'Puyehue-Cordón Caulle','lat': -40.59, 'lon': -72.12, 'ultima_erupcion': 2011},
    {'nombre': 'Hudson',               'lat': -45.90, 'lon': -72.97, 'ultima_erupcion': 2011},
    {'nombre': 'Planchón-Peteroa',     'lat': -35.24, 'lon': -70.57, 'ultima_erupcion': 2011},
    {'nombre': 'Isluga',               'lat': -19.15, 'lon': -68.83, 'ultima_erupcion': 2015},
    {'nombre': 'Callaqui',             'lat': -37.92, 'lon': -71.45, 'ultima_erupcion': 1999},
    {'nombre': 'Lonquimay',            'lat': -38.38, 'lon': -71.59, 'ultima_erupcion': 1990},
    {'nombre': 'Irruputuncu',          'lat': -20.72, 'lon': -68.53, 'ultima_erupcion': 1995},
    {'nombre': 'Guallatiri',           'lat': -18.42, 'lon': -69.09, 'ultima_erupcion': 1985},
    {'nombre': 'Tupungatito',          'lat': -33.36, 'lon': -69.80, 'ultima_erupcion': 1987},
    {'nombre': 'Mocho-Choshuenco',     'lat': -39.93, 'lon': -72.03, 'ultima_erupcion': 1864},
    {'nombre': 'San José',             'lat': -33.79, 'lon': -69.86, 'ultima_erupcion': 1960},
    {'nombre': 'Descabezado Grande',   'lat': -35.58, 'lon': -70.75, 'ultima_erupcion': 1932},
    {'nombre': 'Tinguiririca',         'lat': -34.81, 'lon': -70.35, 'ultima_erupcion': 1917},
    {'nombre': 'Sollipulli',           'lat': -38.97, 'lon': -71.52, 'ultima_erupcion': None},
]

# Endpoints SERNAGEOMIN RNVV a intentar en orden
_SERNAGEOMIN_APIS = [
    'https://rnvv.sernageomin.cl/api/v1/datos/actividad/lista/',
    'https://rnvv.sernageomin.cl/api/actividad/lista',
    'https://rnvv.sernageomin.cl/api/volcanes',
]

@app.route('/volcanes')
@login_required
@limiter.limit("20 per minute")
def volcanes():
    volcanes_out = [dict(v, nivel='verde', fuente='catalogo') for v in _VOLCANES_CHILE]
    for api_url in _SERNAGEOMIN_APIS:
        try:
            r = requests.get(api_url, timeout=8,
                             headers={'User-Agent': 'SPIYD-FireMonitor/1.0',
                                      'Accept': 'application/json'})
            if r.status_code != 200:
                continue
            data = r.json()
            items = data if isinstance(data, list) else data.get('data', data.get('volcanes', data.get('results', [])))
            if not items:
                continue
            nivel_map = {}
            for item in items:
                nombre = (item.get('nombre') or item.get('name') or item.get('NombreVolcan') or '').strip()
                nivel  = (item.get('nivel') or item.get('level') or item.get('alerta') or item.get('NivelAlerta') or 'verde').lower()
                if nombre:
                    nivel_map[nombre.lower()] = nivel
            if nivel_map:
                for v in volcanes_out:
                    k = v['nombre'].lower()
                    if k in nivel_map:
                        v['nivel'] = nivel_map[k]
                        v['fuente'] = 'sernageomin'
                break  # éxito — no seguir intentando
        except Exception:
            continue
    return jsonify(volcanes_out)


@app.route('/admin/api/enviar-resumen-ahora', methods=['POST'])
@login_required
def enviar_resumen_ahora():
    from flask_login import current_user
    if current_user.role not in ('admin', 'superadmin'):
        return jsonify({'error': 'Sin permiso'}), 403
    try:
        d = _construir_datos_resumen()
        _enviar_resumen_telegram(d)
        _enviar_resumen_email(d)
        return jsonify({'ok': True, 'focos': d['focos_total'], 'ai': d['ai_total']})
    except Exception as ex:
        return jsonify({'error': str(ex)}), 500


threading.Thread(target=_daily_summary_loop, daemon=True).start()

if __name__ == "__main__":
    threading.Thread(target=_build_wind_data, daemon=True).start()
    threading.Thread(target=obtener_grilla_clima, daemon=True).start()
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)
