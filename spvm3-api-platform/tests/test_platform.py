import json

from conftest import PASSWORD, create_key, make_admin, register, sql


def H(key):
    return {"X-API-Key": key}


# ------------------------------------------------------------ accounts
def test_register_login_logout(client):
    r = register(client)
    assert r.status_code == 201 and r.get_json()["data"]["email"] == "alice@example.com"
    assert client.get("/me").status_code == 200
    assert client.post("/auth/logout", json={}).status_code == 200
    assert client.get("/me").status_code == 401
    r = client.post("/auth/login", json={"email": "ALICE@example.com", "password": PASSWORD})
    assert r.status_code == 200 and client.get("/me").status_code == 200


def test_password_is_hashed_with_argon2id(client):
    register(client)
    stored = sql("SELECT password_hash FROM users")[0]["password_hash"]
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


def test_duplicate_email_and_weak_password(client):
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    assert register(client, email="b@example.com", password="short").status_code == 400
    assert register(client, email="c@example.com", password="1234567890").status_code == 400
    assert register(client, email="not-an-email").status_code == 400


def test_login_errors_are_generic(client):
    register(client)
    other = client.application.test_client()
    wrong = other.post("/auth/login", json={"email": "alice@example.com", "password": "wrong-password-1"})
    unknown = other.post("/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.get_json()["error"] == unknown.get_json()["error"]


def test_login_is_throttled(client):
    register(client)
    other = client.application.test_client()
    bad = {"email": "alice@example.com", "password": "wrong-password-1"}
    codes = [other.post("/auth/login", json=bad).status_code for _ in range(7)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert other.post("/auth/login", json=bad).headers.get("Retry-After")


def test_change_password_logs_out_other_sessions(client, make_client):
    register(client)
    second = make_client()
    assert second.post("/auth/login", json={"email": "alice@example.com", "password": PASSWORD}).status_code == 200
    r = client.post("/auth/change-password", json={"current_password": PASSWORD, "new_password": "brand-new-password-9"})
    assert r.status_code == 200
    assert client.get("/me").status_code == 200      # the session that changed it stays valid
    assert second.get("/me").status_code == 401     # every other session is logged out


# ------------------------------------------------------------ API keys
def test_key_creation_never_stores_raw_key(client):
    register(client)
    data = create_key(client, name="site", scopes=["knowledge:read"])
    raw = data["api_key"]
    assert raw.startswith("spvm_live_") and len(raw) == 53
    listing = json.dumps(client.get("/me/keys").get_json())
    assert raw not in listing and "key_hash" not in listing
    row = sql("SELECT * FROM api_keys")[0]
    assert raw not in json.dumps({k: str(v) for k, v in row.items()})
    assert row["key_hash"] != raw and len(row["key_hash"]) == 64


def test_key_authentication(client, make_client):
    register(client)
    raw = create_key(client)["api_key"]
    api = make_client()
    r = api.get("/v1/whoami", headers=H(raw))
    assert r.status_code == 200 and r.get_json()["data"]["scopes"] == ["knowledge:read"]
    bodies = []
    for headers in ({}, H("garbage"), H("spvm_live_" + "A" * 43)):
        r = api.get("/v1/whoami", headers=headers)
        assert r.status_code == 401
        bodies.append(r.get_json()["error"])
    assert bodies[0] == bodies[1] == bodies[2]


def test_expired_and_revoked_keys_are_rejected(client, make_client):
    register(client)
    expired = create_key(client, name="a", expires_in_days=30)
    revoked = create_key(client, name="b")
    api = make_client()
    sql("UPDATE api_keys SET expires_at = now() - interval '1 minute' WHERE id = %s", (expired["key"]["id"],))
    assert api.get("/v1/whoami", headers=H(expired["api_key"])).status_code == 401
    assert client.post(f"/me/keys/{revoked['key']['id']}/revoke", json={}).status_code == 200
    r = api.get("/v1/whoami", headers=H(revoked["api_key"]))
    assert r.status_code == 401 and r.get_json()["error"]["code"] == "INVALID_API_KEY"
    reasons = [e["metadata"]["reason"] for e in sql("SELECT metadata FROM audit_logs WHERE event_type='api_key.rejected'")]
    assert "revoked" in reasons


def test_scopes_are_enforced(client, make_client):
    register(client)
    raw = create_key(client, scopes=["knowledge:read"])["api_key"]
    r = make_client().post("/v1/ask", json={"question": "hello there"}, headers=H(raw))
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "INSUFFICIENT_SCOPE"
    assert client.post("/me/keys", json={"name": "x", "scopes": ["admin"]}).status_code == 400


def test_rotation_with_grace_period(client, make_client):
    register(client)
    old = create_key(client)
    api = make_client()
    r = client.post(f"/me/keys/{old['key']['id']}/rotate", json={"grace_hours": 24})
    assert r.status_code == 201
    new = r.get_json()["data"]
    assert api.get("/v1/whoami", headers=H(new["api_key"])).status_code == 200
    assert api.get("/v1/whoami", headers=H(old["api_key"])).status_code == 200   # grace period
    r = client.post(f"/me/keys/{new['key']['id']}/rotate", json={"grace_hours": 0})
    assert r.status_code == 201
    assert api.get("/v1/whoami", headers=H(new["api_key"])).status_code == 401   # revoked immediately


def test_rate_limit_per_key(client, make_client):
    register(client)
    data = create_key(client)
    sql("UPDATE api_keys SET rate_limit_per_min = 3 WHERE id = %s", (data["key"]["id"],))
    api = make_client()
    codes = [api.get("/v1/whoami", headers=H(data["api_key"])).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = api.get("/v1/whoami", headers=H(data["api_key"]))
    assert r.headers["Retry-After"] and r.get_json()["error"]["code"] == "RATE_LIMITED"


def test_usage_is_recorded(client, make_client):
    register(client)
    data = create_key(client)
    api = make_client()
    for _ in range(3):
        api.get("/v1/whoami", headers=H(data["api_key"]))
    usage = client.get(f"/me/keys/{data['key']['id']}/usage").get_json()["data"]
    assert usage["total_30d"] == 3 and usage["recent"][0]["endpoint"] == "/v1/whoami"


def test_active_key_limit(client):
    register(client)
    for i in range(10):
        create_key(client, name=f"k{i}")
    r = client.post("/me/keys", json={"name": "one too many"})
    assert r.status_code == 409 and r.get_json()["error"]["code"] == "KEY_LIMIT"


# ------------------------------------------------------------ access control
def test_users_cannot_touch_each_others_keys(client, make_client):
    register(client)
    key = create_key(client)
    bob = make_client()
    register(bob, email="bob@example.com", name="Bob")
    kid = key["key"]["id"]
    assert bob.get("/me/keys").get_json()["data"]["keys"] == []
    assert bob.get(f"/me/keys/{kid}/usage").status_code == 404
    assert bob.post(f"/me/keys/{kid}/revoke", json={}).status_code == 404
    assert bob.post(f"/me/keys/{kid}/rotate", json={}).status_code == 404
    assert bob.delete(f"/me/keys/{kid}", json={}).status_code == 404
    assert make_client().get("/v1/whoami", headers=H(key["api_key"])).status_code == 200  # still valid
    assert client.delete(f"/me/keys/{kid}", json={}).status_code == 200                    # the owner can delete it


def test_admin_routes_need_admin(client, make_client):
    register(client)
    assert client.get("/admin/api/users").status_code == 403
    assert make_client().get("/admin/api/users").status_code == 401
    make_admin("alice@example.com")
    assert client.get("/admin/api/users").status_code == 200


def test_disabling_user_kills_sessions_and_keys(client, make_client):
    register(client)
    make_admin("alice@example.com")
    bob = make_client()
    register(bob, email="bob@example.com", name="Bob")
    raw = create_key(bob)["api_key"]
    bob_id = sql("SELECT id FROM users WHERE email='bob@example.com'")[0]["id"]
    alice_id = sql("SELECT id FROM users WHERE email='alice@example.com'")[0]["id"]
    assert client.post(f"/admin/api/users/{bob_id}/disable", json={}).status_code == 200
    assert bob.get("/me").status_code == 401
    assert make_client().get("/v1/whoami", headers=H(raw)).status_code == 401
    assert make_client().post("/auth/login", json={"email": "bob@example.com", "password": PASSWORD}).status_code == 401
    assert client.post(f"/admin/api/users/{alice_id}/disable", json={}).status_code == 400   # cannot lock yourself out


# ------------------------------------------------------------ knowledge API
def test_knowledge_search_and_ask(client, make_client):
    register(client)
    make_admin("alice@example.com")
    r = client.post("/admin/api/knowledge/import", json=[
        {"category": "python", "title": "Lists", "content": "A Python list is an ordered, mutable collection."},
        {"category": "git", "title": "Rebase", "content": "Git rebase replays commits onto another base."}])
    assert r.get_json()["data"]["imported"] == 2
    key = create_key(client, scopes=["knowledge:read", "ask"])["api_key"]
    api = make_client()
    res = api.get("/v1/knowledge/search?q=python list", headers=H(key)).get_json()["data"]["results"]
    assert res and res[0]["title"] == "Lists"
    assert api.get("/v1/knowledge/search?q=rebase&category=python", headers=H(key)).get_json()["data"]["results"] == []
    ans = api.post("/v1/ask", json={"question": "what is git rebase?"}, headers=H(key)).get_json()["data"]
    assert ans["source"] == "retrieval" and "replays" in ans["answer"]
    none = api.post("/v1/ask", json={"question": "how do rockets fly"}, headers=H(key)).get_json()["data"]
    assert none["source"] == "none"
    cats = api.get("/v1/categories", headers=H(key)).get_json()["data"]["categories"]
    assert {c["category"] for c in cats} == {"python", "git"}


def test_sql_injection_attempts_are_harmless(client, make_client):
    register(client)
    key = create_key(client)["api_key"]
    api = make_client()
    r = api.get("/v1/knowledge/search", query_string={"q": "'; DROP TABLE users; --", "category": "x' OR '1'='1"},
                headers=H(key))
    assert r.status_code == 200
    assert make_client().post("/auth/login", json={"email": "' OR 1=1 --", "password": "whatever-password"}).status_code == 401
    assert sql("SELECT count(*) AS n FROM users")[0]["n"] == 1


# ------------------------------------------------------------ hardening
def test_security_headers_and_request_id(client):
    r = client.get("/healthz")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    r = client.get("/v1/whoami")
    assert r.headers["X-Request-ID"] == r.get_json()["request_id"] and r.headers["X-Request-ID"].startswith("req_")
    assert r.headers["Cache-Control"] == "no-store"


def test_csrf_protection(client):
    register(client)
    assert client.post("/me/keys", data="name=x", content_type="application/x-www-form-urlencoded").status_code == 415
    r = client.post("/me/keys", json={"name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.get_json()["error"]["code"] == "FORBIDDEN_ORIGIN"
    assert client.post("/me/keys", json={"name": "x"}, headers={"Origin": "http://localhost"}).status_code == 201


def test_cors_is_off_by_default_for_v1(client):
    r = client.get("/v1/whoami", headers={"Origin": "https://random.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_audit_trail_has_no_secrets(client):
    register(client)
    data = create_key(client)
    client.post(f"/me/keys/{data['key']['id']}/revoke", json={})
    events = client.get("/me/audit").get_json()["data"]["events"]
    types = {e["event_type"] for e in events}
    assert {"user.registered", "api_key.created", "api_key.revoked"} <= types
    dump = json.dumps(events) + json.dumps([dict(r) for r in sql("SELECT * FROM audit_logs")], default=str)
    assert data["api_key"] not in dump and PASSWORD not in dump


def test_errors_are_json_and_hide_internals(client):
    r = client.get("/no/such/route")
    assert r.status_code == 404 and r.get_json()["success"] is False
