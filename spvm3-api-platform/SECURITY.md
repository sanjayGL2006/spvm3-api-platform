# Security notes

This is a solid MVP foundation. It is **not** "100% secure", and nothing here is a substitute for a security review before you hold real customer data.

## What is implemented

| Area | Control |
|---|---|
| Passwords | Argon2id hashing, 10-128 characters, login throttling per IP and per email, equal-time check for unknown emails |
| API keys | 256-bit random keys (`spvm_live_...` / `spvm_test_...`), stored only as HMAC-SHA-256 with a server secret (the pepper), shown once |
| Key lifecycle | Optional expiry, revoke, delete, rotate with a grace period, max active keys per user |
| Least privilege | Scopes (`knowledge:read`, `ask`) checked on every request; new keys default to read-only |
| Fail closed | Unknown, malformed, expired, revoked and disabled-owner keys all return the same 401 |
| Abuse control | Per-key rate limit (429 + `Retry-After`), per-IP limit on failed key attempts, on logins and on registrations |
| Sessions | Signed HttpOnly cookie, `SameSite=Strict`, `Secure` in production, 8 hour lifetime, logout-everywhere when the password changes or the account is disabled |
| CSRF | SameSite cookie + JSON-only writes + Origin check |
| Injection | Parameterised SQL everywhere; the dashboard builds the page with `textContent` (no `innerHTML`) |
| Headers | CSP (no inline scripts or styles), HSTS, nosniff, frame denial, no-referrer, no-store on JSON |
| Audit | Login, failed login, key create/rotate/revoke/delete, rejected keys, admin actions. No secrets are ever written. IPs are stored only as keyed hashes |
| Access control | Every key/usage route filters by owner (other users get 404, no information leak). Admin routes need `is_admin`, which can only be granted from the command line |

## Not implemented yet (planned for later versions)

- Email verification and password reset (needs an email service)
- MFA (TOTP) and recovery codes
- Redis-backed rate limiting (current limiter is in memory; run one gunicorn worker)
- IP allow-lists per key, signed requests with replay protection
- Tamper-evident (hash-chained) audit log and optional blockchain anchoring of Merkle roots
- Anomaly detection and automatic responses
- Automated dependency and container scanning

## Threats to keep in mind

| Threat | Mitigation here | Your job |
|---|---|---|
| Database leak | Keys and passwords are hashed; the pepper is not in the database | Keep `API_KEY_PEPPER` and `SECRET_KEY` out of git and out of backups that sit next to the database |
| Key leaked (e.g. pushed to GitHub) | Revoke and rotate in one click; audit shows use | Tell users never to put keys in browser code or public repos |
| Brute-forcing keys | 256-bit keys, per-IP failed-attempt limit, generic errors | Keep HTTPS on so keys are not sniffed |
| Password guessing | Argon2id + throttling | Consider MFA before opening registration widely |
| Stolen admin session | 8 hour sessions, SameSite=Strict, admin only via CLI grant | Use a strong admin password |
| Rate-limit bypass with many workers | Limits are per process | Use `-w 1 --threads N` or move the limiter to Redis |
| Proxy misconfiguration | `BEHIND_PROXY=1` trusts one proxy hop | Make sure only your proxy can reach the app port |

## Before going live

- [ ] HTTPS in front of the app, `COOKIE_SECURE=1`, `BEHIND_PROXY=1` if behind a proxy
- [ ] `SECRET_KEY` and `API_KEY_PEPPER` are long, random and backed up somewhere safe (losing the pepper invalidates every key)
- [ ] `TRUSTED_ORIGINS` set to your public address if the proxy changes the Host header
- [ ] Database backups tested by restoring one
- [ ] `python manage.py prune-usage --days 90` scheduled (cron) so usage logs do not grow forever
- [ ] Run `pytest` and `pip list --outdated` regularly
