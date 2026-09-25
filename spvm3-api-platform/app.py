"""SPVM3 API Platform: users register, log in and create their own API keys."""
import logging
from datetime import timedelta

from flask import Flask, g, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

import core
from core import COOKIE_SECURE, V1_CORS_ORIGINS, db, fail, hash_ip, new_request_id
import routes_admin
import routes_auth
import routes_keys
import routes_v1

import os

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("spvm3")

app = Flask(__name__, static_folder="static")
app.config.update(
    SECRET_KEY=core.SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=1024 * 1024,
    JSON_SORT_KEYS=False,
)
if os.environ.get("BEHIND_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

for module in (routes_auth, routes_keys, routes_v1, routes_admin):
    app.register_blueprint(module.bp)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.before_request
def before():
    g.request_id = new_request_id()
    if request.method not in ("GET", "HEAD", "OPTIONS") and not request.path.startswith("/v1/") \
            and not core.origin_ok():
        return fail("FORBIDDEN_ORIGIN", "Cross-site request blocked.", 403)


@app.after_request
def after(resp):
    h = resp.headers
    h["X-Request-ID"] = g.get("request_id", "")
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"
    h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    h["Content-Security-Policy"] = CSP
    if COOKIE_SECURE:
        h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if resp.mimetype == "application/json":
        h["Cache-Control"] = "no-store"

    rate = g.get("rate_info")
    if rate:
        h["X-RateLimit-Limit"] = str(rate[0])
        h["X-RateLimit-Remaining"] = str(rate[1])

    origin = request.headers.get("Origin", "").rstrip("/")
    if request.path.startswith("/v1/") and origin and (origin in V1_CORS_ORIGINS or "*" in V1_CORS_ORIGINS):
        h["Access-Control-Allow-Origin"] = origin if "*" not in V1_CORS_ORIGINS else "*"
        h["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
        h["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        h["Access-Control-Expose-Headers"] = "X-Request-ID, X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After"
        h["Vary"] = "Origin"

    # Usage telemetry is best effort: a logging problem must not break the API response.
    key_id = g.get("api_key_id")
    if key_id and resp.status_code != 429:
        try:
            with db() as cur:
                cur.execute("INSERT INTO api_usage (api_key_id, endpoint, method, status_code, request_id, ip_hash) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (key_id, request.path[:200], request.method, resp.status_code,
                             g.get("request_id"), hash_ip(core.client_ip())))
        except Exception:
            log.exception("Could not record API usage")
    return resp


@app.errorhandler(Exception)
def handle_error(exc):
    if isinstance(exc, HTTPException):
        codes = {400: "BAD_REQUEST", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 413: "PAYLOAD_TOO_LARGE"}
        return fail(codes.get(exc.code, "HTTP_ERROR"), exc.description or exc.name, exc.code)
    log.exception("Unhandled error (request %s)", g.get("request_id"))
    return fail("INTERNAL_ERROR", "Something went wrong on our side.", 500)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.get("/<path:filename>")
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)


@app.get("/app")
def dashboard():
    return send_from_directory(app.static_folder, "app.html")


@app.get("/healthz")
def healthz():
    with db() as cur:
        cur.execute("SELECT 1")
    return {"status": "ok"}


core.init_db()

if __name__ == "__main__":
    app.run(debug=False, port=5000)
