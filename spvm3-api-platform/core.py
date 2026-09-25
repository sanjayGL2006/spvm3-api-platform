"""Shared configuration, database access, security helpers and decorators."""
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlparse

from argon2 import PasswordHasher
from dotenv import load_dotenv
from flask import g, jsonify, request, session
import json
import pymysql
import pymysql.cursors
from pymysql.constants import FIELD_TYPE
# pyrefly: ignore [missing-import]
from dbutils.pooled_db import PooledDB

load_dotenv()
log = logging.getLogger("spvm3")


# ------------------------------------------------------------------ config
def need(name, min_len=1):
    value = os.environ.get(name, "")
    if len(value) < min_len:
        raise SystemExit(f"Environment variable {name} is required (at least {min_len} characters).")
    return value


def _csv(name):
    return [x.strip().rstrip("/") for x in os.environ.get(name, "").split(",") if x.strip()]


DATABASE_URL = need("DATABASE_URL")
SECRET_KEY = need("SECRET_KEY", 32)
API_KEY_PEPPER = need("API_KEY_PEPPER", 32)
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"
TRUSTED_ORIGINS = _csv("TRUSTED_ORIGINS")
V1_CORS_ORIGINS = _csv("V1_CORS_ORIGINS")
OLLAMA_URL = (os.environ.get("OLLAMA_URL") or "").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
MAX_ACTIVE_KEYS = int(os.environ.get("MAX_ACTIVE_KEYS_PER_USER", "10"))

SCOPES = {
    "knowledge:read": "Search and read the knowledge base",
    "ask": "Ask questions and receive answers",
}
DEFAULT_RATE_LIMIT = {"live": 60, "test": 20}


# ------------------------------------------------------------------ database
_pool = None


def get_pool():
    global _pool
    if _pool is None:
        parsed = urlparse(DATABASE_URL)
        conv = pymysql.converters.conversions.copy()
        conv[FIELD_TYPE.JSON] = json.loads
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=10,
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
            autocommit=False,
            conv=conv
        )
    return _pool


@contextmanager
def db():
    """Yields a dict cursor. Commits on success, rolls back on error."""
    pool = get_pool()
    conn = pool.connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    with open(path, encoding="utf-8") as f:
        sql = f.read()
    with db() as cur:
        for statement in sql.split(';'):
            statement = statement.strip()
            if statement:
                try:
                    cur.execute(statement)
                except pymysql.err.OperationalError as e:
                    if e.args[0] == 1061:  # Duplicate key name
                        pass
                    else:
                        raise


# ------------------------------------------------------------------ passwords
ph = PasswordHasher()  # Argon2id
DUMMY_HASH = ph.hash("dummy-password-used-to-equalise-login-timing")


# ------------------------------------------------------------------ API keys
KEY_RE = re.compile(r"^spvm_(live|test)_[A-Za-z0-9_-]{43}$")


def hash_key(raw):
    """Keyed hash. Without the pepper, a stolen database cannot be used to check guessed keys."""
    return hmac.new(API_KEY_PEPPER.encode(), raw.encode(), hashlib.sha256).hexdigest()


def generate_key(environment):
    raw = f"spvm_{environment}_{secrets.token_urlsafe(32)}"
    return raw, raw[:14], hash_key(raw)


# ------------------------------------------------------------------ request helpers
def new_request_id():
    return "req_" + secrets.token_hex(8)


def _rid():
    return g.get("request_id") or new_request_id()


def ok(data=None, status=200):
    resp = jsonify(success=True, data={} if data is None else data, request_id=_rid())
    resp.status_code = status
    return resp


def fail(code, message, status, headers=None):
    resp = jsonify(success=False, error={"code": code, "message": message}, request_id=_rid())
    resp.status_code = status
    for key, value in (headers or {}).items():
        resp.headers[key] = value
    return resp


def rate_fail(retry_after):
    return fail("RATE_LIMITED", "Too many requests. Please slow down.", 429, {"Retry-After": str(retry_after)})


def client_ip():
    return request.remote_addr or "unknown"


def hash_ip(ip):
    return hmac.new(API_KEY_PEPPER.encode(), ip.encode(), hashlib.sha256).hexdigest()[:32]


def origin_ok():
    """Blocks cross-site browser requests to cookie-authenticated routes."""
    origin = request.headers.get("Origin")
    if not origin:
        return True
    if urlparse(origin).netloc == request.host:
        return True
    return origin.rstrip("/") in TRUSTED_ORIGINS


def audit(cur, event_type, user_id=None, api_key_id=None, metadata=None):
    """Write an audit event inside the caller's transaction. Never pass secrets in metadata."""
    cur.execute(
        "INSERT INTO audit_logs (user_id, api_key_id, event_type, request_id, ip_hash, user_agent, metadata) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (user_id, api_key_id, event_type, _rid(), hash_ip(client_ip()),
         (request.headers.get("User-Agent") or "")[:200], json.dumps(metadata or {})),
    )


# ------------------------------------------------------------------ rate limiting
# In-memory sliding window. Correct with one worker process (run gunicorn with -w 1 --threads N).
_hits = defaultdict(deque)
_lock = threading.Lock()


def hit(bucket, limit, window=60):
    """Record one hit. Returns (allowed, remaining, retry_after_seconds)."""
    now = time.time()
    with _lock:
        if len(_hits) > 20000:
            for name in [n for n, q in _hits.items() if not q or q[-1] < now - 3600]:
                del _hits[name]
        q = _hits[bucket]
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= limit:
            return False, 0, max(1, int(q[0] + window - now) + 1)
        q.append(now)
        return True, limit - len(q), 0


def reset_rate_limits():
    with _lock:
        _hits.clear()


# ------------------------------------------------------------------ decorators
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        uid = session.get("uid")
        if not uid:
            return fail("AUTH_REQUIRED", "Please log in.", 401)
        with db() as cur:
            cur.execute(
                "SELECT id, email, name, is_admin, is_active, session_version FROM users WHERE id = %s", (uid,)
            )
            user = cur.fetchone()
        if not user or not user["is_active"] or user["session_version"] != session.get("sv"):
            session.clear()
            return fail("AUTH_REQUIRED", "Please log in.", 401)
        # Forms from other sites cannot send JSON, which blocks CSRF on top of SameSite cookies.
        if request.method not in ("GET", "HEAD") and not request.is_json:
            return fail("UNSUPPORTED_MEDIA_TYPE", "Send JSON (Content-Type: application/json).", 415)
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @login_required
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.user["is_admin"]:
            return fail("FORBIDDEN", "Administrator access required.", 403)
        return fn(*args, **kwargs)
    return wrapper


_LOOKUP_SQL = """
    SELECT k.id, k.user_id, k.scopes, k.rate_limit_per_min, k.revoked_at,
           (k.expires_at IS NOT NULL AND k.expires_at <= now()) AS expired,
           k.key_prefix, k.environment, k.expires_at, u.is_active AS user_active
    FROM api_keys k JOIN users u ON u.id = k.user_id
    WHERE k.key_hash = %s
"""


def api_key_required(*required_scopes):
    """Authenticate X-API-Key. Fails closed: unknown, revoked, expired and disabled-owner keys all look the same."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            raw = request.headers.get("X-API-Key", "")
            row = None
            if KEY_RE.match(raw):
                with db() as cur:
                    cur.execute(_LOOKUP_SQL, (hash_key(raw),))
                    row = cur.fetchone()

            reason = None
            if row is None:
                reason = "unknown"
            elif row["revoked_at"] is not None:
                reason = "revoked"
            elif row["expired"]:
                reason = "expired"
            elif not row["user_active"]:
                reason = "owner_disabled"

            if reason:
                allowed, _, retry = hit(f"authfail:{client_ip()}", 30)
                if not allowed:
                    return rate_fail(retry)
                # Record use of a known-but-dead key (max once a minute per key, so it cannot flood the log).
                if row is not None and hit(f"rejlog:{row['id']}", 1)[0]:
                    with db() as cur:
                        audit(cur, "api_key.rejected", user_id=row["user_id"], api_key_id=row["id"],
                              metadata={"reason": reason, "prefix": row["key_prefix"]})
                return fail("INVALID_API_KEY", "The API credential is invalid or inactive.", 401)

            limit = row["rate_limit_per_min"]
            allowed, remaining, retry = hit(f"key:{row['id']}", limit)
            g.rate_info = (limit, remaining)
            if not allowed:
                return rate_fail(retry)

            missing = [s for s in required_scopes if s not in row["scopes"]]
            if missing:
                g.api_key_id = row["id"]
                return fail("INSUFFICIENT_SCOPE", f"This API key needs the scope: {', '.join(missing)}.", 403)

            with db() as cur:
                cur.execute(
                    "UPDATE api_keys SET last_used_at = now() WHERE id = %s "
                    "AND (last_used_at IS NULL OR last_used_at < now() - INTERVAL 60 SECOND)",
                    (row["id"],),
                )
            g.api_key_id = row["id"]
            g.key = row
            return fn(*args, **kwargs)
        return wrapper
    return decorator
