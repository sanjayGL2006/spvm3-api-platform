"""Administrator API. Every route requires a logged-in admin and every change is audited."""
from flask import Blueprint, g, request
import pymysql.err

from core import admin_required, audit, db, fail, ok
from knowledge import clean_entry
from routes_keys import STATUS_SQL

bp = Blueprint("admin", __name__, url_prefix="/admin/api")


@bp.get("/stats")
@admin_required
def stats():
    with db() as cur:
        cur.execute(f"""
            SELECT (SELECT count(*) FROM users) AS users,
                   (SELECT count(*) FROM users WHERE created_at > now() - INTERVAL 7 DAY) AS new_users_7d,
                   (SELECT count(*) FROM api_keys WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())) AS active_keys,
                   (SELECT count(*) FROM api_usage WHERE created_at > now() - INTERVAL 24 HOUR) AS requests_24h,
                   (SELECT count(*) FROM audit_logs WHERE event_type IN ('auth.login_failed', 'api_key.rejected')
                        AND created_at > now() - INTERVAL 24 HOUR) AS auth_failures_24h,
                   (SELECT count(*) FROM knowledge) AS knowledge_entries
        """)
        return ok(cur.fetchone())


@bp.get("/users")
@admin_required
def users():
    with db() as cur:
        cur.execute("""
            SELECT u.id, u.email, u.name, u.is_admin, u.is_active, u.created_at, u.last_login_at,
                   (SELECT count(*) FROM api_keys k WHERE k.user_id = u.id AND k.revoked_at IS NULL
                        AND (k.expires_at IS NULL OR k.expires_at > now())) AS active_keys
            FROM users u ORDER BY u.id DESC LIMIT 500""")
        return ok({"users": cur.fetchall()})


def _set_active(user_id, active):
    if user_id == g.user["id"]:
        return fail("INVALID_INPUT", "You cannot change your own status.", 400)
    with db() as cur:
        # Disabling bumps session_version (logs the user out everywhere); their API keys stop working too.
        cur.execute("UPDATE users SET is_active = %s, session_version = session_version + 1, updated_at = now() "
                    "WHERE id = %s", (active, user_id))
        if cur.rowcount == 0:
            return fail("NOT_FOUND", "User not found.", 404)
        audit(cur, "admin.user_enabled" if active else "admin.user_disabled", user_id=g.user["id"],
              metadata={"target_user_id": user_id})
    return ok()


@bp.post("/users/<int:user_id>/disable")
@admin_required
def disable_user(user_id):
    return _set_active(user_id, False)


@bp.post("/users/<int:user_id>/enable")
@admin_required
def enable_user(user_id):
    return _set_active(user_id, True)


@bp.get("/keys")
@admin_required
def all_keys():
    with db() as cur:
        cur.execute(f"""
            SELECT k.id, k.name, k.key_prefix, k.environment, k.scopes, k.last_used_at, k.expires_at, k.created_at,
                   {STATUS_SQL} AS status,
                   u.email AS owner_email
            FROM api_keys k JOIN users u ON u.id = k.user_id ORDER BY k.id DESC LIMIT 500""")
        return ok({"keys": cur.fetchall()})


@bp.post("/keys/<int:key_id>/revoke")
@admin_required
def admin_revoke(key_id):
    with db() as cur:
        cur.execute("SELECT key_prefix, user_id FROM api_keys WHERE id = %s AND revoked_at IS NULL FOR UPDATE", (key_id,))
        row = cur.fetchone()
        if row is None:
            return fail("NOT_FOUND", "Key not found or already revoked.", 404)
        cur.execute("UPDATE api_keys SET revoked_at = now() WHERE id = %s", (key_id,))
        audit(cur, "admin.api_key_revoked", user_id=g.user["id"], api_key_id=key_id,
              metadata={"prefix": row["key_prefix"], "owner_id": row["user_id"]})
    return ok()


@bp.get("/audit")
@admin_required
def audit_log():
    event = (request.args.get("event") or "").strip()[:60] or None
    with db() as cur:
        cur.execute("""
            SELECT a.id, a.event_type, a.request_id, a.metadata, a.created_at, u.email AS user_email
            FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id
            WHERE (%s IS NULL OR a.event_type = %s) ORDER BY a.id DESC LIMIT 200""", (event, event))
        return ok({"events": cur.fetchall()})


# ---------------- knowledge management
@bp.get("/knowledge")
@admin_required
def list_knowledge():
    with db() as cur:
        cur.execute("SELECT id, category, title, content, tags, updated_at FROM knowledge "
                    "ORDER BY updated_at DESC LIMIT 1000")
        return ok({"entries": cur.fetchall()})


@bp.post("/knowledge")
@admin_required
def add_knowledge():
    entry = clean_entry(request.get_json(silent=True) or {})
    if not entry:
        return fail("INVALID_INPUT", "'title' and 'content' are required.", 400)
    try:
        with db() as cur:
            cur.execute("INSERT INTO knowledge (category, title, content, tags, created_by) "
                        "VALUES (%s, %s, %s, %s, %s)", (*entry, g.user["id"]))
            new_id = cur.lastrowid
            audit(cur, "admin.knowledge_created", user_id=g.user["id"], metadata={"entry_id": new_id})
    except pymysql.err.IntegrityError as e:
        if e.args[0] == 1062:
            return fail("CONFLICT", "An entry with this category and title already exists.", 409)
        raise
    return ok({"id": new_id}, 201)


@bp.put("/knowledge/<int:entry_id>")
@admin_required
def update_knowledge(entry_id):
    entry = clean_entry(request.get_json(silent=True) or {})
    if not entry:
        return fail("INVALID_INPUT", "'title' and 'content' are required.", 400)
    try:
        with db() as cur:
            cur.execute("UPDATE knowledge SET category=%s, title=%s, content=%s, tags=%s, updated_at=now() "
                        "WHERE id=%s", (*entry, entry_id))
            if cur.rowcount == 0:
                return fail("NOT_FOUND", "Entry not found.", 404)
            audit(cur, "admin.knowledge_updated", user_id=g.user["id"], metadata={"entry_id": entry_id})
    except pymysql.err.IntegrityError as e:
        if e.args[0] == 1062:
            return fail("CONFLICT", "Another entry already has this category and title.", 409)
        raise
    return ok()


@bp.delete("/knowledge/<int:entry_id>")
@admin_required
def delete_knowledge(entry_id):
    with db() as cur:
        cur.execute("DELETE FROM knowledge WHERE id = %s", (entry_id,))
        if cur.rowcount == 0:
            return fail("NOT_FOUND", "Entry not found.", 404)
        audit(cur, "admin.knowledge_deleted", user_id=g.user["id"], metadata={"entry_id": entry_id})
    return ok()


@bp.post("/knowledge/import")
@admin_required
def import_knowledge():
    """Bulk add or refresh. Same category + title updates the existing entry."""
    items = request.get_json(silent=True)
    if not isinstance(items, list) or not items or len(items) > 1000:
        return fail("INVALID_INPUT", "Send a JSON list with 1 to 1000 entries.", 400)
    entries = [clean_entry(i) if isinstance(i, dict) else None for i in items]
    if any(e is None for e in entries):
        return fail("INVALID_INPUT", "Every entry needs 'title' and 'content'.", 400)
    with db() as cur:
        for e in entries:
            cur.execute("INSERT INTO knowledge (category, title, content, tags, created_by) VALUES (%s, %s, %s, %s, %s) "
                        "ON DUPLICATE KEY UPDATE content = VALUES(content), "
                        "tags = VALUES(tags), updated_at = now()", (*e, g.user["id"]))
        audit(cur, "admin.knowledge_imported", user_id=g.user["id"], metadata={"count": len(entries)})
    return ok({"imported": len(entries)})
