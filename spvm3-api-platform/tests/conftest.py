import os
import sys

import psycopg2
import pytest

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://t:t@127.0.0.1:5433/spvm3_test")
os.environ.update(
    DATABASE_URL=TEST_DB_URL,
    SECRET_KEY="test-secret-key-" + "x" * 32,
    API_KEY_PEPPER="test-pepper-" + "p" * 32,
    COOKIE_SECURE="0",
    V1_CORS_ORIGINS="",
    TRUSTED_ORIGINS="",
    OLLAMA_URL="",
    MAX_ACTIVE_KEYS_PER_USER="10",
)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The tests DROP the whole public schema. Refuse to run unless the database name contains "test".
from psycopg2.extensions import parse_dsn  # noqa: E402
if "test" not in (parse_dsn(TEST_DB_URL).get("dbname") or ""):
    raise SystemExit("Refusing to run: TEST_DATABASE_URL must point to a dedicated database whose name contains 'test'.")

# Start every test run from an empty schema (the test database is dedicated to tests).
_conn = psycopg2.connect(TEST_DB_URL)
_conn.autocommit = True
_conn.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
_conn.close()

import core  # noqa: E402
from app import app as flask_app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_state():
    core.reset_rate_limits()
    with core.db() as cur:
        cur.execute("TRUNCATE users, api_keys, knowledge, api_usage, audit_logs RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def client():
    return flask_app.test_client()


@pytest.fixture
def make_client():
    return flask_app.test_client


PASSWORD = "correct-horse-battery"


def register(c, email="alice@example.com", password=PASSWORD, name="Alice"):
    return c.post("/auth/register", json={"email": email, "password": password, "name": name})


def create_key(c, **body):
    body.setdefault("name", "test key")
    r = c.post("/me/keys", json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]


def sql(query, params=()):
    with core.db() as cur:
        cur.execute(query, params)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return None


def make_admin(email):
    sql("UPDATE users SET is_admin = TRUE WHERE email = %s", (email,))
