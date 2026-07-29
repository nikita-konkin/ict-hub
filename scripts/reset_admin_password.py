#!/usr/bin/env python
"""
reset_admin_password.py — out-of-band admin/user password reset.

Use this if you are locked out, forgot the admin password, or need to rotate it
without wiping the database. Run it inside the running container so it targets
the same SQLite volume the app uses:

    docker compose exec converter-hub \
        python scripts/reset_admin_password.py admin 'NewStr0ngPass!'

Or supply the new password via env (avoids it showing in shell history):

    docker compose exec -e NEW_ADMIN_PASSWORD='NewStr0ngPass!' converter-hub \
        python scripts/reset_admin_password.py admin

It sets the new password, clears the forced-change flag, and re-activates the
account if it was disabled. Exit codes: 0 ok, 1 user not found, 2 bad args.
"""
from __future__ import annotations

import os
import sys

# Allow `import app.*` when run as `python scripts/reset_admin_password.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import hash_password  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

MIN_LEN = 8


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "admin"
    new_password = sys.argv[2] if len(sys.argv) > 2 else os.getenv("NEW_ADMIN_PASSWORD", "")

    if not new_password:
        print("ERROR: provide the new password as the 2nd argument or via "
              "NEW_ADMIN_PASSWORD env.", file=sys.stderr)
        return 2
    if len(new_password) < MIN_LEN:
        print(f"ERROR: password must be at least {MIN_LEN} characters.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            print(f"ERROR: user {username!r} not found.", file=sys.stderr)
            return 1
        user.hashed_pw = hash_password(new_password)
        user.must_change_password = False
        if not user.is_active:
            user.is_active = True
        db.commit()
        print(f"OK: password reset for {username!r}; forced-change cleared, account active.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
