from flask import Flask, jsonify, request, send_from_directory, render_template, redirect, url_for, flash
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import feedparser
import math
import os
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

def _rate_limit_key():
    from flask_login import current_user
    if current_user and current_user.is_authenticated:
        return f"user:{current_user.id}"
    return get_remote_address()

limiter = Limiter(_rate_limit_key, app=app, default_limits=[])

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'spiyd-dev-secret-change-in-prod-2026')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get(
    'DATABASE_URL', 'sqlite:///' + os.path.join(BASE_DIR, 'spiyd.db'))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

from models import db, User, UsageLog
db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Iniciá sesión para acceder al sistema'
login_manager.login_message_category = 'warning'

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

from auth import auth_bp
from admin import admin_bp
from superadmin import superadmin_bp
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')
app.register_blueprint(superadmin_bp, url_prefix='/superadmin')

with app.app_context():
    db.create_all()

@app.route('/')
def landing():
    return send_from_directory(DOCS_DIR, 'index.html')

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

@app.route('/reunion.html')
def docs_reunion():
    return send_from_directory(DOCS_DIR, 'reunion.html')

@app.route('/mapa')
@login_required
def mapa():
    maptiler_key = os.environ.get('MAPTILER_KEY', '')
    with open(os.path.join(BASE_DIR, 'mapa.html'), 'r', encoding='utf-8') as f:
        html = f.read()
    html = html.replace('__MAPTILER_KEY__', maptiler_key)
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
NASA_MAP_KEY = os.environ.get("NASA_MAP_KEY", "")

SMN_FEEDS = [
    {"tipo": "Alertas y advertencias",   "url": "https://ssl.smn.gob.ar/feeds/CAP/rss_alertaCAP_nuevo.xml",             "nivel_base": "naranja"},
    {"tipo": "Temperaturas extremas",    "url": "https://ssl.smn.gob.ar/feeds/CAP/oladecalor/rss_ola_calor_nuevo.xml",  "nivel_base": "naranja"},
    {"tipo": "Avisos a muy corto plazo", "url": "https://ssl.smn.gob.ar/feeds/CAP/avisocortoplazo/rss_acpCAP.xml",      "nivel_base": "amarillo"}
]

GRID_LATS = list(range(-22, -56, -3))
GRID_LONS = list(range(-74, -52, 3))

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
_overpass_cache = {}   # key -> (timestamp, data)
OVERPASS_CACHE_TTL = 3600  # 1 hora

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

def obtener_grilla_clima():
    import time as _t
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
def water_sources():
    s = float(request.args.get("s", -55.8))
    w = float(request.args.get("w", -73.5))
    n = float(request.args.get("n", -21.0))
    e = float(request.args.get("e", -53.5))
    buf = 0.1
    s, w, n, e = s-buf, w-buf, n+buf, e+buf

    # Limitar bbox máximo a ~10° para evitar timeout
    s = max(s, -56.0); n = min(n, -21.0); w = max(w, -74.0); e = min(e, -53.0)

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
  relation["landuse"~"^(forest|wood|meadow|grassland)$"]({s},{w},{n},{e});
  relation["natural"~"^(wood|scrub|heath|grassland|wetland)$"]({s},{w},{n},{e});
);
out center tags;
"""
    try:
        elements = overpass_query(ql, timeout=65)
    except Exception as ex:
        return jsonify({"error": str(ex)}), 502

    vegetacion = []
    vistos     = set()
    for el in elements:
        lat, lon = elemento_punto(el)
        if lat is None or lon is None: continue
        clave = f"{round(lat,3)}|{round(lon,3)}"
        if clave in vistos: continue
        vistos.add(clave)
        tags    = el.get("tags", {})
        tipo    = tags.get("natural") or tags.get("landuse") or "vegetation"
        especie = (tags.get("species") or tags.get("species:es") or
                   tags.get("taxon")   or tags.get("genus")      or
                   tags.get("leaf_type"))
        riesgo_map = {
            "scrub":      "MUY ALTO",  "heath":      "MUY ALTO",
            "wood":       "ALTO",      "forest":     "ALTO",
            "grassland":  "ALTO",      "fell":       "ALTO",
            "meadow":     "MODERADO",  "tundra":     "MODERADO",
            "wetland":    "BAJO",      "farmland":   "MODERADO",
            "vineyard":   "MODERADO",  "orchard":    "MODERADO",
        }
        vegetacion.append({
            "lat":    lat,
            "lon":    lon,
            "tipo":   tipo,
            "nombre": tags.get("name") or tags.get("name:es") or tipo,
            "especie": especie,
            "riesgo_incendio": riesgo_map.get(tipo, "DESCONOCIDO")
        })
    return jsonify(vegetacion)


# ── Análisis IA (Claude) ──────────────────────────────────────────────────────

@app.route("/ai-risk-analysis", methods=["POST"])
@limiter.limit("10 per hour")
def ai_risk_analysis():
    try:
        import anthropic
    except ImportError:
        return jsonify({"error": "Instala: pip install anthropic"}), 503

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return jsonify({"error": "ANTHROPIC_API_KEY no configurada"}), 503

    body         = request.get_json(silent=True) or {}
    weather_data = body.get("weather", [])
    fire_count   = int(body.get("fire_count", 0))
    fire_pts     = body.get("fire_points", [])      # [{lat,lon,conf}]
    agua_data    = body.get("water", [])
    veg_data     = body.get("vegetation", [])
    inpe_count   = int(body.get("inpe_count", 0))
    inpe_sats    = body.get("inpe_satelites", [])
    fwi_max      = float(body.get("fwi_max", 0))
    fwi_extremos = int(body.get("fwi_extremos", 0))

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

    prompt = f"""Eres un experto en combate de incendios forestales en Argentina. Analizá estos datos en tiempo real:

═══ FOCOS NASA FIRMS ═══
Total focos activos: {fire_count}
Focos de alta confianza (≥80%):
{focos_txt}

═══ REGLA 30-30-30 ═══
Zonas con 3/3 condiciones (temp>30°C, viento>30 km/h, humedad<30%): {len(zonas_3)}
Zonas con 2/3 condiciones: {len(zonas_2)}
Temperatura máxima registrada: {max(temps, default='N/D')}°C
Humedad mínima: {min(humedds, default='N/D')}%
Viento máximo: {max(vientos, default='N/D')} km/h

═══ FUENTES DE AGUA DETECTADAS (OSM) ═══
{resumen_agua}
Total fuentes: {len(agua_data)}

═══ VEGETACIÓN EN EL ÁREA ═══
Tipos detectados: {resumen_veg}
Especies identificadas: {resumen_especies}

Respondé en español estructurado con estas secciones (máx 280 palabras total):

1. 🚨 NIVEL DE RIESGO: [BAJO/MODERADO/ALTO/CRÍTICO] — una oración explicando por qué.

2. 📍 ZONAS PRIORITARIAS: Provincias/regiones en mayor peligro (identificá por coordenadas de focos).

3. 💧 ESTRATEGIA DE AGUA: Qué fuentes usar, accesibilidad estimada, cantidad disponible.

4. 🌿 RIESGO POR VEGETACIÓN: Cuáles tipos/especies presentes son más combustibles y por qué.

5. 🚗 ACCESO VEHICULAR: Recomendaciones de acceso al terreno según topografía y vegetación.

6. ⚡ ACCIÓN INMEDIATA: Las 3 acciones más urgentes en orden de prioridad."""

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}]
    )

    return jsonify({
        "analysis": msg.content[0].text,
        "zonas_3":  zonas_3,
        "zonas_2":  zonas_2,
        "fire_count": fire_count
    })


# ── Precipitación grid ───────────────────────────────────────────────────────

@app.route("/precipitacion")
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

@app.route("/debug-cache")
def debug_cache():
    import time as _t
    return jsonify({
        "clima_cached": _clima_cache["ts"] > 0,
        "clima_pts": len(_clima_cache["data"]) if _clima_cache["data"] else 0,
        "wind_cached": _wind_cache["ts"] > 0,
        "now": _t.time(),
        "clima_age_s": round(_t.time() - _clima_cache["ts"], 1) if _clima_cache["ts"] else None
    })

@app.route("/weather-grid")
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

def _build_wind_data():
    import time as _t
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
def wind_data():
    import time as _t
    if _wind_cache["data"] and (_t.time() - _wind_cache["ts"]) < WIND_CACHE_TTL:
        return jsonify(_wind_cache["data"])
    return jsonify(_build_wind_data())


# ── SMN alertas ───────────────────────────────────────────────────────────────

@app.route("/smn-alertas")
def smn_alertas():
    return jsonify(obtener_alertas_smn(request.args.get("provincia","").strip()))


# ── Telegram ──────────────────────────────────────────────────────────────────

@app.route("/telegram-alerta", methods=["POST"])
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


# ── INPE BDQueimadas ──────────────────────────────────────────────────────────

INPE_SATELITES = ["AQUA_M-T","TERRA_M-T","GOES-16","NPP-375","NOAA-20","METOP-B","METOP-C"]

@app.route("/inpe-focos")
def inpe_focos():
    """
    Proxy para INPE BDQueimadas — focos detectados en Argentina por satélites
    distintos a NASA FIRMS (GOES-16, METOP, etc.).
    """
    periodo = request.args.get("periodo", "10")   # 10=24h, 20=48h, 30=72h

    todos   = []
    vistos  = set()

    for sat in INPE_SATELITES:
        try:
            url = (f"https://queimadas.dgi.inpe.br/api/focos/"
                   f"?pais_id=33&satelite={sat}&periodo_id={periodo}")
            r = requests.get(url, timeout=20,
                             headers={"User-Agent": "ArgentinaFireMonitor/1.0"})
            if not r.ok:
                continue
            focos = r.json()
            if not isinstance(focos, list):
                focos = focos.get("features", []) if isinstance(focos, dict) else []

            for f in focos:
                # GeoJSON Feature o dict plano
                if isinstance(f, dict) and "geometry" in f:
                    coords = f["geometry"].get("coordinates", [])
                    props  = f.get("properties", {})
                    lat = coords[1] if len(coords) >= 2 else None
                    lon = coords[0] if len(coords) >= 2 else None
                    datahora = props.get("datahora") or props.get("data_hora_gmt")
                    frp      = props.get("frp")
                else:
                    lat      = f.get("lat") or f.get("latitude")
                    lon      = f.get("lon") or f.get("longitude")
                    datahora = f.get("datahora") or f.get("data_hora_gmt")
                    frp      = f.get("frp")

                if lat is None or lon is None:
                    continue
                # Filtrar bounding box Argentina
                if not (-55.8 <= float(lat) <= -21.0 and -73.5 <= float(lon) <= -53.5):
                    continue
                clave = f"{round(float(lat),3)}|{round(float(lon),3)}"
                if clave in vistos:
                    continue
                vistos.add(clave)
                todos.append({
                    "lat":      float(lat),
                    "lon":      float(lon),
                    "satelite": sat,
                    "datahora": datahora,
                    "frp":      frp
                })
        except Exception:
            continue   # si un satélite falla, continúa con los demás

    return jsonify({"focos": todos, "total": len(todos), "satelites": INPE_SATELITES})


# ── FWI por grilla ────────────────────────────────────────────────────────────

@app.route("/fwi-grid")
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
BBOX_ARG = "-73.5,-55.8,-53.5,-21.0"

@app.route("/nasa-focos")
def nasa_focos():
    fuente = request.args.get("fuente", "VIIRS_SNPP_NRT")
    dias   = request.args.get("dias", "1")
    if fuente not in FUENTES_NASA:
        return jsonify({"error": "fuente inválida"}), 400
    key = NASA_MAP_KEY
    if not key:
        return jsonify({"error": "NASA_MAP_KEY no configurada"}), 503
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{fuente}/{BBOX_ARG}/{dias}"
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "ArgentinaFireMonitor/1.0"})
        r.raise_for_status()
        return r.text, 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as ex:
        return jsonify({"error": str(ex)}), 502


if __name__ == "__main__":
    import threading
    threading.Thread(target=_build_wind_data, daemon=True).start()
    threading.Thread(target=obtener_grilla_clima, daemon=True).start()
    app.run(debug=False, host='0.0.0.0', port=5000, threaded=True)
