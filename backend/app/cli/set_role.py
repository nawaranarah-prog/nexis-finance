"""Give an account a role: ``python -m app.cli.set_role someone@example.com moderator``.

Roles are member, moderator and admin. Moderators can review reported and flagged Pulse content. Roles are only ever
set here, by someone with access to the server and database — never from the web.
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, or_, select

from app.db import session as db_session
from app.models import User

ROLES = ("member", "moderator", "admin")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("account", help="email, username or phone number of the account")
    p.add_argument("role", choices=ROLES)
    a = p.parse_args()
    ident = a.account.strip().lower()
    with db_session.session_scope() as db:
        u = db.scalars(select(User).where(or_(func.lower(User.email) == ident, func.lower(User.username) == ident, User.phone == ident))).first()
        if u is None:
            raise SystemExit(f"no account matches {a.account!r}")
        u.role = a.role
        print(f"{u.username}: role set to {a.role}")


if __name__ == "__main__":
    main()
