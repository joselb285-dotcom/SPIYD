import os
bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"
workers = 4        # OK con PostgreSQL. Cache en memoria es por proceso (ineficiencia menor).
threads = 2        # Para Redis compartido: pip install redis y configurar REDIS_URL en .env
worker_class = "gthread"
timeout = 120
keepalive = 5
accesslog = "-"
errorlog = "-"
