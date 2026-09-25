"""API key management for the logged-in user."""
from flask import Blueprint, g, request

import json
from core import (DEFAULT_RATE_LIMIT, MAX_ACTIVE_KEYS, SCOPES, audit, db, fail, generate_key, login_required, ok)

bp = Blueprint("keys", __name__)

STATUS_SQL = ("CASE WHEN revoked_at IS NOT NULL THEN 'revoked' "
              "WHEN expires_at IS NOT NULL AND expires_at <= now() THEN 'expired' ELSE 'active' END")
KEY_COLUMNS = (f"id, name, key_prefix, environment, scopes, rate_limit_per_min, expires_at, last_used_at, "
               f"revoked_at, rotated_from, created_at, {STATUS_SQL} AS status")


def _active_count(cur, user_id):
    cur.execute("SELECT count(*) AS n FROM api_keys WHERE user_id = %s AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at > now())", (user_id,))
    return cur.fetchone()["n"]


@bp.get("/me/keys")
@login_required
def list_keys():
    with db() as cur:
        cur.execute(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE user_id = %s ORDER BY id DESC", (g.user["id"],))
        return ok({"keys": cur.fetchall(), "scopes": SCOPES, "max_active_keys": MAX_ACTIVE_KEYS})


@bp.post("/me/keys")
@login_required
def create_key():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()[:80]
    environment = data.get("environment", "live")
    scopes = data.get("scopes") or ["knowledge:read"]
    days = data.get("expires_in_days")
    if not name:
        return fail("INVALID_INPUT", "Give the key a name.", 400)
    if environment not in ("live", "test"):
        return fail("INVALID_INPUT", "Environment must be 'live' or 'test'.", 400)
    if not isinstance(scopes, list) or not scopes or any(s not in SCOPES for s in scopes):
        return fail("INVALID_INPUT", f"Scopes must be a non-empty list from: {', '.join(SCOPES)}.", 400)
    if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 365):
        return fail("INVALID_INPUT", "expires_in_days must be between 1 and 365, or empty for no expiry.", 400)

    raw, prefix, key_hash = generate_key(environment)
    with db() as cur:
        cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (g.user["id"],))  # serialise concurrent creates
        if _active_count(cur, g.user["id"]) >= MAX_ACTIVE_KEYS:
            return fail("KEY_LIMIT", f"You can have at most {MAX_ACTIVE_KEYS} active keys. Revoke one first.", 409)
        cur.execute(
            "INSERT INTO api_keys (user_id, name, key_hash, key_prefix, environment, scopes, rate_limit_per_min, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s IS NULL THEN NULL ELSE now() + INTERVAL %s DAY END) ",
            (g.user["id"], name, key_hash, prefix, environment, json.dumps(sorted(set(scopes))),
             DEFAULT_RATE_LIMIT[environment], days, days),
        )
        new_id = cur.lastrowid
        cur.execute(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = %s", (new_id,))
        key = cur.fetchone()
        audit(cur, "api_key.created", user_id=g.user["id"], api_key_id=key["id"],
              metadata={"prefix": prefix, "environment": environment, "scopes": sorted(set(scopes))})
    return ok({"key": key, "api_key": raw, "message": "Copy this API key now. You will not be able to see it again."}, 201)


@bp.post("/me/keys/<int:key_id>/rotate")
@login_required
def rotate_key(key_id):
    data = request.get_json(silent=True) or {}
    grace = data.get("grace_hours", 24)
    if not isinstance(grace, int) or isinstance(grace, bool) or not 0 <= grace <= 168:
        return fail("INVALID_INPUT", "grace_hours must be between 0 and 168.", 400)
    with db() as cur:
        cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (g.user["id"],))
        cur.execute("SELECT * FROM api_keys WHERE id = %s AND user_id = %s AND revoked_at IS NULL "
                    "AND (expires_at IS NULL OR expires_at > now()) FOR UPDATE", (key_id, g.user["id"]))
        old = cur.fetchone()
        if old is None:
            return fail("NOT_FOUND", "Key not found.", 404)
        raw, prefix, key_hash = generate_key(old["environment"])
        cur.execute(
            "INSERT INTO api_keys (user_id, name, key_hash, key_prefix, environment, scopes, rate_limit_per_min, "
            "expires_at, rotated_from) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ",
            (g.user["id"], old["name"], key_hash, prefix, old["environment"], json.dumps(old["scopes"]),
             old["rate_limit_per_min"], old["expires_at"], old["id"]),
        )
        new_id = cur.lastrowid
        cur.execute(f"SELECT {KEY_COLUMNS} FROM api_keys WHERE id = %s", (new_id,))
        new = cur.fetchone()
        if grace == 0:
            cur.execute("UPDATE api_keys SET revoked_at = now() WHERE id = %s", (old["id"],))
        else:  # old key keeps working for a while so deployments can switch without downtime
            cur.execute("UPDATE api_keys SET expires_at = LEAST(COALESCE(expires_at, CAST('2099-12-31' AS DATETIME)), "
                        "now() + INTERVAL %s HOUR) WHERE id = %s", (grace, old["id"]))
        audit(cur, "api_key.rotated", user_id=g.user["id"], api_key_id=new["id"],
              metadata={"old_key_id": old["id"], "old_prefix": old["key_prefix"], "new_prefix": prefix, "grace_hours": grace})
    return ok({"key": new, "api_key": raw, "message": "Copy the new key now. It will not be shown again."}, 201)


@bp.post("/me/keys/<int:key_id>/revoke")
@login_required
def revoke_key(key_id):
    with db() as cur:
        cur.execute("SELECT key_prefix FROM api_keys WHERE id = %s AND user_id = %s AND revoked_at IS NULL FOR UPDATE", (key_id, g.user["id"]))
        row = cur.fetchone()
        if row is None:
            return fail("NOT_FOUND", "Key not found.", 404)
        cur.execute("UPDATE api_keys SET revoked_at = now() WHERE id = %s", (key_id,))
        audit(cur, "api_key.revoked", user_id=g.user["id"], api_key_id=key_id, metadata={"prefix": row["key_prefix"]})
    return ok()


@bp.delete("/me/keys/<int:key_id>")
@login_required
def delete_key(key_id):
    with db() as cur:
        cur.execute("SELECT key_prefix FROM api_keys WHERE id = %s AND user_id = %s", (key_id, g.user["id"]))
        row = cur.fetchone()
        if row is None:
            return fail("NOT_FOUND", "Key not found.", 404)
        cur.execute("DELETE FROM api_keys WHERE id = %s AND user_id = %s", (key_id, g.user["id"]))
        audit(cur, "api_key.deleted", user_id=g.user["id"], metadata={"key_id": key_id, "prefix": row["key_prefix"]})
    return ok()


@bp.get("/me/keys/<int:key_id>/usage")
@login_required
def key_usage(key_id):
    with db() as cur:
        cur.execute("SELECT id FROM api_keys WHERE id = %s AND user_id = %s", (key_id, g.user["id"]))
        if cur.fetchone() is None:
            return fail("NOT_FOUND", "Key not found.", 404)
        cur.execute("SELECT DATE(created_at) AS day, count(*) AS count FROM api_usage "
                    "WHERE api_key_id = %s AND created_at > now() - INTERVAL 7 DAY GROUP BY 1 ORDER BY 1", (key_id,))
        per_day = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM api_usage WHERE api_key_id = %s AND created_at > now() - INTERVAL 30 DAY",
                    (key_id,))
        total = cur.fetchone()["n"]
        cur.execute("SELECT endpoint, method, status_code, created_at FROM api_usage WHERE api_key_id = %s "
                    "ORDER BY id DESC LIMIT 10", (key_id,))
        recent = cur.fetchall()
    return ok({"per_day": per_day, "total_30d": total, "recent": recent})
