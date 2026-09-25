"""Admin command line tool.

  python manage.py make-admin you@example.com    (the person must register first)
  python manage.py remove-admin you@example.com
  python manage.py prune-usage --days 90         (delete old request logs)
"""
import argparse

import core


def set_admin(email, value):
    core.init_db()
    with core.db() as cur:
        cur.execute("UPDATE users SET is_admin = %s, updated_at = now() WHERE email = %s",
                    (value, email.strip().lower()))
        if cur.rowcount == 0:
            raise SystemExit("No user with that email. Register first, then run this command.")
    print(("Admin rights granted to " if value else "Admin rights removed from ") + email)


def prune_usage(days):
    with core.db() as cur:
        cur.execute("DELETE FROM api_usage WHERE created_at < now() - INTERVAL %s DAY", (days,))
        print(f"Deleted {cur.rowcount} old usage rows.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make-admin").add_argument("email")
    sub.add_parser("remove-admin").add_argument("email")
    sub.add_parser("prune-usage").add_argument("--days", type=int, default=90)
    a = p.parse_args()
    if a.cmd == "make-admin":
        set_admin(a.email, True)
    elif a.cmd == "remove-admin":
        set_admin(a.email, False)
    else:
        prune_usage(a.days)
