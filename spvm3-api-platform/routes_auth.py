"""Registration, login, logout, and the current user."""
import re
from datetime import timedelta

from argon2.exceptions import InvalidHashError, VerificationError
from flask import Blueprint, g, request, session
import pymysql.err

from core import (DUMMY_HASH, audit, client_ip, db, fail, hit, login_required, ok, ph, rate_fail)

bp = Blueprint("auth", __name__)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(value):
    email = str(value or "").strip().lower()
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


def password_problem(password, email=None):
    if not isinstance(password, str) or not (10 <= len(password) <= 128):
        return "Password must be 10 to 128 characters."
    if password.isdigit() or (email and password.lower() == email):
        return "Choose a stronger password (not only digits, not your email)."
    return None


def _verify(stored_hash, password):
    try:
        return ph.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def _start_session(user_id, version):
    session.clear()
    session["uid"] = user_id
    session["sv"] = version
    session.permanent = True


def _public_user(u):
    return {"id": u["id"], "email": u["email"], "name": u["name"], "is_admin": u["is_admin"]}


@bp.post("/auth/register")
def register():
    allowed, _, retry = hit(f"register:{client_ip()}", 5, 3600)
    if not allowed:
        return rate_fail(retry)
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email"))
    name = str(data.get("name") or "").strip()[:80]
    password = data.get("password")
    if not email:
        return fail("INVALID_INPUT", "Enter a valid email address.", 400)
    problem = password_problem(password, email)
    if problem:
        return fail("WEAK_PASSWORD", problem, 400)
    password_hash = ph.hash(password)
    try:
        with db() as cur:
            cur.execute("INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s)", (email, name, password_hash))
            user_id = cur.lastrowid
            cur.execute("SELECT id, email, name, is_admin, session_version FROM users WHERE id = %s", (user_id,))
            user = cur.fetchone()
            audit(cur, "user.registered", user_id=user["id"])
    except pymysql.err.IntegrityError as e:
        if e.args[0] == 1062:
            return fail("EMAIL_TAKEN", "That email is already registered.", 409)
        raise
    _start_session(user["id"], user["session_version"])
    return ok(_public_user(user), 201)


@bp.post("/auth/login")
def login():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email")) or ""
    password = data.get("password")
    ok_ip, _, retry_ip = hit(f"login-ip:{client_ip()}", 10)
    ok_email, _, retry_email = hit(f"login-email:{email}", 5)
    if not (ok_ip and ok_email):
        return rate_fail(max(retry_ip, retry_email))

    generic = fail("INVALID_CREDENTIALS", "Email or password is incorrect.", 401)
    if not isinstance(password, str) or len(password) > 128 or not email:
        _verify(DUMMY_HASH, "x")
        return generic

    with db() as cur:
        cur.execute("SELECT id, email, name, is_admin, is_active, password_hash, session_version "
                    "FROM users WHERE email = %s", (email,))
        user = cur.fetchone()
        if user is None:
            _verify(DUMMY_HASH, password)  # same work as a real check, so timing does not reveal the email
            return generic
        if not _verify(user["password_hash"], password):
            audit(cur, "auth.login_failed", user_id=user["id"])
            return generic
        if not user["is_active"]:
            audit(cur, "auth.login_blocked", user_id=user["id"], metadata={"reason": "account_disabled"})
            return generic
        if ph.check_needs_rehash(user["password_hash"]):
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (ph.hash(password), user["id"]))
        cur.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user["id"],))
        audit(cur, "auth.login", user_id=user["id"])
    _start_session(user["id"], user["session_version"])
    return ok(_public_user(user))


@bp.post("/auth/logout")
@login_required
def logout():
    with db() as cur:
        audit(cur, "auth.logout", user_id=g.user["id"])
    session.clear()
    return ok()


@bp.post("/auth/change-password")
@login_required
def change_password():
    data = request.get_json(silent=True) or {}
    current, new = data.get("current_password"), data.get("new_password")
    with db() as cur:
        cur.execute("SELECT password_hash FROM users WHERE id = %s", (g.user["id"],))
        stored = cur.fetchone()["password_hash"]
        if not isinstance(current, str) or len(current) > 128 or not _verify(stored, current):
            audit(cur, "auth.password_change_failed", user_id=g.user["id"])
            return fail("INVALID_CREDENTIALS", "Current password is incorrect.", 401)
        problem = password_problem(new, g.user["email"])
        if problem:
            return fail("WEAK_PASSWORD", problem, 400)
        # Bumping session_version logs out every other session of this account.
        cur.execute("UPDATE users SET password_hash = %s, session_version = session_version + 1, updated_at = now() "
                    "WHERE id = %s", (ph.hash(new), g.user["id"]))
        cur.execute("SELECT session_version FROM users WHERE id = %s", (g.user["id"],))
        version = cur.fetchone()["session_version"]
        audit(cur, "auth.password_changed", user_id=g.user["id"])
    _start_session(g.user["id"], version)
    return ok()


@bp.get("/me")
@login_required
def me():
    return ok(_public_user(g.user))


@bp.get("/me/audit")
@login_required
def my_audit():
    with db() as cur:
        cur.execute("SELECT event_type, request_id, metadata, created_at FROM audit_logs "
                    "WHERE user_id = %s ORDER BY id DESC LIMIT 100", (g.user["id"],))
        return ok({"events": cur.fetchall()})
