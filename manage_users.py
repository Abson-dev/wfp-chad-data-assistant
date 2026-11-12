"""Manage the accounts of the web app. Run it on the computer that runs the app; nobody can sign up on their own.

    python manage_users.py add someone@cgiar.org --name "First Last" [--admin]   a new account, temporary password asked here
    python manage_users.py list                                                 the accounts
    python manage_users.py reset-password someone@cgiar.org                     a new temporary password; every session ends
    python manage_users.py disable someone@cgiar.org                            no more sign-in; every session ends
    python manage_users.py enable someone@cgiar.org
    python manage_users.py remove someone@cgiar.org                             the account and its chats
    python manage_users.py audit [--last 50]                                    the security log: sign-ins, failures, changes
    python manage_users.py first-admin                                          create the first account if there is none (run_web.bat)

The temporary password is typed here twice and never shown. Give it to the person by a separate channel (a call, not
the email that tells them about the account); they must change it at their first sign-in.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "web"))
# Only the standard library: this runs with any Python 3.11, even without the app's packages.
from accounts import AccountError, Accounts, default_state, load_limits  # noqa: E402

LIMITS = load_limits()


def store() -> Accounts:
    return Accounts(default_state() / "accounts.sqlite", LIMITS)


def ask_password(prompt: str = "Temporary password") -> str:
    first = getpass.getpass(f"{prompt} (at least {LIMITS['password_min_chars']} characters, not shown): ")
    if getpass.getpass("Again: ") != first:
        raise AccountError("The two passwords differ.")
    return first


def when(t) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "-"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("email")
    a.add_argument("--name", required=True)
    a.add_argument("--admin", action="store_true")
    sub.add_parser("list")
    for name in ("reset-password", "disable", "enable", "remove"):
        sub.add_parser(name).add_argument("email")
    au = sub.add_parser("audit")
    au.add_argument("--last", type=int, default=50)
    sub.add_parser("first-admin")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    acc = store()
    try:
        if args.cmd == "add":
            # Check the email before asking for a password.
            if acc.user_by_email(Accounts._email(args.email)):
                raise AccountError(f"{args.email} already has an account.")
            acc.add_user(args.email, args.name, ask_password(), "admin" if args.admin else "user")
            print(f"Account created for {args.email.strip().lower()}. They must change the temporary password at the first sign-in.")
        elif args.cmd == "first-admin":
            if acc.count_active():
                return 0
            print("No account yet: create yours (administrator).")
            email = input("Email: ").strip()
            name = input("Name: ").strip()
            acc.add_user(email, name, ask_password("Password"), "admin")
            print("Done. Sign in with it; you will be asked to choose your own password.")
        elif args.cmd == "list":
            rows = acc.users()
            if not rows:
                print("No account.")
            for u in rows:
                state = "active" if u["active"] else "disabled"
                flags = " · must change password" if u["must_change"] else ""
                print(f"{u['email']:<40} {u['name']:<28} {u['role']:<6} {state}{flags} · last sign-in {when(u['last_login'])}")
        elif args.cmd == "reset-password":
            acc.reset_password(args.email, ask_password())
            print("New temporary password set; every session of this account has ended.")
        elif args.cmd in ("disable", "enable"):
            acc.set_active(args.email, args.cmd == "enable")
            print(f"{args.email} {args.cmd}d.")
        elif args.cmd == "remove":
            if input(f"Remove {args.email} and all their chats? Type yes: ").strip().lower() != "yes":
                print("Nothing removed.")
                return 1
            acc.remove_user(args.email)
            print("Removed.")
        elif args.cmd == "audit":
            for r in acc.audit(args.last):
                print(f"{when(r['at'])}  {r['event']:<24} {r['email'] or '-':<36} {r['ip'] or ''} {r['detail'] or ''}")
    except AccountError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
