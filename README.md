# SPVM3 API Platform

Users register, log in, and create their **own API keys** to use a programming-knowledge API.
Flask + MySQL + plain HTML/CSS/JS. This is the multi-user "open platform" version; it is separate from the single-owner portfolio agent.

## What is inside

| Part | Where |
|---|---|
| Landing page with API docs and curl examples | `/` |
| User dashboard: register, log in, create / rotate / revoke keys, usage, activity, change password | `/app` |
| Admin panel (inside `/app` for admins): users, all keys, knowledge editor + bulk import, audit log, stats | `/app` |
| Public API (needs `X-API-Key`) | `/v1/whoami`, `/v1/categories`, `/v1/knowledge/search`, `/v1/knowledge/{id}`, `/v1/ask` |
| Health check | `/healthz` |

Security details and the list of what is not built yet: see `SECURITY.md`.

## Run it on your computer (Windows, Mac or Linux)

1. Install Python 3.11+ and MySQL 8+. Create an empty database:
   ```
   mysql -u root -p -e "CREATE DATABASE spvm3_platform;"
   ```
   (or create it in a MySQL GUI client)
2. In the project folder:
   ```
   python -m venv venv
   venv\Scripts\activate          # Mac/Linux: source venv/bin/activate
   pip install -r requirements.txt
   copy .env.example .env         # Mac/Linux: cp .env.example .env
   ```
3. Generate two secrets and paste them into `.env` as `SECRET_KEY` and `API_KEY_PEPPER`:
   ```
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
   Also set `DATABASE_URL`, and set `COOKIE_SECURE=0` while you test over plain http.
4. Start it (tables are created automatically):
   ```
   python app.py
   ```
5. Open http://127.0.0.1:5000/app, register, then make yourself admin:
   ```
   python manage.py make-admin you@example.com
   ```
   Log out and in again. You will see the admin tabs.
6. Create a key in **API keys**, then try it:
   ```
   curl http://127.0.0.1:5000/v1/whoami -H "X-API-Key: spvm_live_..."
   ```

## Add knowledge

Admin, **Knowledge** tab: add entries one by one or bulk import a JSON list.
Same `category` + `title` updates the old entry, so a weekly or monthly refresh is just another import:

```json
[{"category": "python", "title": "Lists", "content": "A list is an ordered, mutable collection.", "tags": ["basics
```

`/v1/ask` returns the best matching entry. To get real LLM answers from a model running on your own server, install Ollama and set `OLLAMA_URL` (for example `http://127.0.0.1:11434`) and `OLLAMA_MODEL`. Without it, nothing leaves your server and no outside API key is needed.

## API basics

```
GET  /v1/knowledge/search?q=python list&category=python&limit=5     scope: knowledge:read
POST /v1/ask   {"question": "What is git rebase?"}                  scope: ask
```

Success: `{"success": true, "data": {...}, "request_id": "req_..."}`
Error: `{"success": false, "error": {"code": "INVALID_API_KEY", "message": "..."}, "request_id": "req_..."}`
Codes: 401 bad/expired/revoked key, 403 missing scope, 429 rate limit (see `Retry-After`).
Response headers: `X-Request-ID`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`.

## Tests

The tests drop and rebuild the whole schema, so they need their **own** database (the name must contain `test`, or the tests refuse to run):

```
mysql -u root -p -e "CREATE DATABASE spvm3_test;"
pip install -r requirements-dev.txt
set TEST_DATABASE_URL=mysql://user:password@localhost:3306/spvm3_test     # Mac/Linux: export ...
pytest
```

24 tests cover registration/login, password hashing, throttling, key hashing and one-time display, expiry, revocation, rotation, scopes, rate limits, usage logging, cross-user access (IDOR), admin permissions, SQL injection attempts, CSRF, CORS, security headers and audit contents.

## Deploy

- Run behind nginx (or your host's proxy) with **HTTPS**. Set `COOKIE_SECURE=1` and `BEHIND_PROXY=1`. Minimal nginx location:
  ```
  location / {
      proxy_pass http://127.0.0.1:8000;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $remote_addr;
      proxy_set_header X-Forwarded-Proto $scheme;
  }
  ```
- Start with `gunicorn -w 1 --threads 8 -b 127.0.0.1:8000 app:app`. Keep **one worker**: the rate limiter is in memory.
- Docker: put `DB_PASSWORD`, `SECRET_KEY`, `API_KEY_PEPPER` in `.env`, then `docker compose up -d --build` (the Docker files are provided but were not run in the environment where this was built).
- Schedule `python manage.py prune-usage --days 90` (cron) to keep the usage table small.

## Browser access to /v1

By default browsers on other websites cannot call `/v1` (CORS is off), which is right for server-to-server use. To allow specific websites set `V1_CORS_ORIGINS=https://site1.com,https://site2.com`. Remember: a key placed in browser code is visible to every visitor.

## Files

```
app.py            app setup, security headers, error handling, usage logging
core.py           config, database, key hashing, rate limiter, auth decorators
routes_auth.py    register, login, logout, change password, /me
routes_keys.py    create / list / rotate / revoke / delete keys, usage
routes_v1.py      the public API
routes_admin.py   admin API
knowledge.py      search and answering
manage.py         make-admin, remove-admin, prune-usage
schema.sql        database tables
static/           landing page, dashboard (app.html, app.js), style.css
tests/            pytest suite
```
"