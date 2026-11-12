"""Accounts of the web app: users, sessions, chats, daily counts and the security log, in one SQLite file.

The administrator creates every account (manage_users.py); nobody signs up. A new account gets a temporary password,
to change at the first sign-in. Passwords are hashed with scrypt. A session is a random token that the server keeps
hashed, so a copy of the file opens no session, and the server can end it (sign-out, disabled account, new password).
After repeated failures an account is locked for some minutes. The file lives in local/, not versioned: the chats
quote regional figures.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def default_state() -> Path:
    """Where the accounts, chats, feedback and log live: WFP_AGENT_STATE, else on Windows the local application data
    (out of OneDrive, which can corrupt a database it syncs, and the chats quote regional figures), else local/."""
    if os.environ.get("WFP_AGENT_STATE"):
        return Path(os.environ["WFP_AGENT_STATE"])
    if os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "wfp-chad-data-assistant"
    return REPO / "local"


def load_limits() -> dict:
    return json.loads((REPO / "limits.json").read_text(encoding="utf-8"))

SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
WRONG = "Wrong email or password, or the account is locked for a few minutes after repeated failures."
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
  pw_hash TEXT NOT NULL, must_change INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1,
  failed INTEGER NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, last_login REAL);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL NOT NULL, expires REAL NOT NULL, ip TEXT);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
  turns TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage (user_id INTEGER NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL, PRIMARY KEY (user_id, day));
CREATE TABLE IF NOT EXISTS audit (at REAL NOT NULL, user_id INTEGER, email TEXT, event TEXT NOT NULL, detail TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS chats_by_user ON chats (user_id, updated);
"""


class AccountError(ValueError):
    """A refusal to show to the person (wrong password, weak password, unknown account...)."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${_b64(salt)}${_b64(h)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, h = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32,
                             maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# Checked when the account is unknown, so that a wrong email takes as long as a wrong password.
_DUMMY = hash_password(secrets.token_hex(16))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def password_problem(password: str, email: str, min_chars: int) -> str | None:
    """Why a new password is refused, or None."""
    if len(password) < min_chars:
        return f"Use at least {min_chars} characters."
    if len(set(password)) < 4:
        return "Use more varied characters."
    if email and email.split("@")[0].lower() in password.lower():
        return "Do not use your email address in the password."
    return None


class Accounts:
    """All the server-side state of the web app. One connection, guarded by a lock: the app answers one question at
    a time, and these calls are short."""

    def __init__(self, path: Path, limits: dict):
        self.path, self.lim, self.lock = Path(path), limits, threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.executescript(SCHEMA)

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        rows = self._all(sql, args)
        return rows[0] if rows else None

    def _run(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            return self.db.execute(sql, args).rowcount

    def log(self, event: str, user: dict | None = None, email: str = "", detail: str = "", ip: str = "") -> None:
        self._run("INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?)",
                  (time.time(), user["id"] if user else None, (user or {}).get("email", email), event, detail[:300], ip))

    def audit(self, last: int = 50) -> list[dict]:
        return self._all("SELECT * FROM audit ORDER BY at DESC LIMIT ?", (last,))

    # ---- Users (the administrator, through manage_users.py) ---------------------------------------------------

    @staticmethod
    def _email(email: str) -> str:
        e = (email or "").strip().lower()
        if not EMAIL.match(e):
            raise AccountError(f"Not an email address: {email!r}.")
        return e

    def add_user(self, email: str, name: str, password: str, role: str = "user") -> int:
        e = self._email(email)
        if role not in ("user", "admin"):
            raise AccountError("The role is user or admin.")
        if not name.strip():
            raise AccountError("Give the person's name.")
        if problem := password_problem(password, e, self.lim["password_min_chars"]):
            raise AccountError(problem)
        if self.user_by_email(e):
            raise AccountError(f"{e} already has an account.")
        with self.lock:
            cur = self.db.execute("INSERT INTO users (email, name, role, pw_hash, must_change, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                                  (e, name.strip(), role, hash_password(password), time.time()))
        self.log("account_created", email=e, detail=role)
        return int(cur.lastrowid)

    def user_by_email(self, email: str) -> dict | None:
        return self._one("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),))

    def users(self) -> list[dict]:
        return self._all("SELECT id, email, name, role, active, must_change, created_at, last_login, locked_until FROM users ORDER BY email")

    def count_active(self) -> int:
        return int(self._one("SELECT COUNT(*) AS n FROM users WHERE active = 1")["n"])

    def _need(self, email: str) -> dict:
        u = self.user_by_email(email)
        if not u:
            raise AccountError(f"No account for {email}.")
        return u

    def set_active(self, email: str, active: bool) -> None:
        u = self._need(email)
        self._run("UPDATE users SET active = ?, failed = 0, locked_until = 0 WHERE id = ?", (int(active), u["id"]))
        if not active:
            self._run("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
        self.log("account_enabled" if active else "account_disabled", u)

    def reset_password(self, email: str, password: str) -> None:
        """A new temporary password: every session ends, and the person must change it at the next sign-in."""
        u = self._need(email)
        if problem := password_problem(password, u["email"], self.lim["password_min_chars"]):
            raise AccountError(problem)
        self._run("UPDATE users SET pw_hash = ?, must_change = 1, failed = 0, locked_until = 0 WHERE id = ?",
                  (hash_password(password), u["id"]))
        self._run("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
        self.log("password_reset", u)

    def remove_user(self, email: str) -> None:
        u = self._need(email)
        for table in ("sessions", "chats", "usage"):
            self._run(f"DELETE FROM {table} WHERE user_id = ?", (u["id"],))  # noqa: S608 (fixed table names)
        self._run("DELETE FROM users WHERE id = ?", (u["id"],))
        self.log("account_removed", email=u["email"])

    # ---- Sign-in and sessions ---------------------------------------------------------------------------------

    def login(self, email: str, password: str, ip: str = "") -> tuple[str, dict]:
        """A new session token for a right email and password; the same refusal for every failure."""
        now = time.time()
        u = self.user_by_email(email)
        if not u or not u["active"] or u["locked_until"] > now:
            verify_password(password, _DUMMY)
            self.log("login_refused", u, email=(email or "")[:120], detail="unknown" if not u else "disabled" if not u["active"] else "locked", ip=ip)
            raise AccountError(WRONG)
        if not verify_password(password, u["pw_hash"]):
            failed = u["failed"] + 1
            if failed >= self.lim["login_failures_before_lock"]:
                self._run("UPDATE users SET failed = 0, locked_until = ? WHERE id = ?", (now + 60 * self.lim["lock_minutes"], u["id"]))
                self.log("account_locked", u, ip=ip)
            else:
                self._run("UPDATE users SET failed = ? WHERE id = ?", (failed, u["id"]))
                self.log("login_failed", u, ip=ip)
            raise AccountError(WRONG)
        token = secrets.token_urlsafe(32)
        self._run("DELETE FROM sessions WHERE expires < ?", (now,))
        self._run("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)",
                  (_token_hash(token), u["id"], now, now + 3600 * self.lim["session_hours"], ip))
        self._run("UPDATE users SET failed = 0, last_login = ? WHERE id = ?", (now, u["id"]))
        self.log("login", u, ip=ip)
        return token, self.public(u)

    def session_user(self, token: str) -> dict | None:
        if not token:
            return None
        u = self._one("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                      "WHERE s.token_hash = ? AND s.expires > ? AND u.active = 1", (_token_hash(token), time.time()))
        return self.public(u) if u else None

    def logout(self, token: str) -> None:
        u = self.session_user(token)
        self._run("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        if u:
            self.log("logout", u)

    def change_password(self, user: dict, token: str, old: str, new: str) -> None:
        """The person's own change: the old password is required, and every other session ends."""
        u = self._one("SELECT * FROM users WHERE id = ?", (user["id"],))
        if not u or not verify_password(old, u["pw_hash"]):
            self.log("password_change_refused", user)
            raise AccountError("The current password is wrong.")
        if old == new:
            raise AccountError("Choose a password different from the current one.")
        if problem := password_problem(new, u["email"], self.lim["password_min_chars"]):
            raise AccountError(problem)
        self._run("UPDATE users SET pw_hash = ?, must_change = 0 WHERE id = ?", (hash_password(new), u["id"]))
        self._run("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (u["id"], _token_hash(token)))
        self.log("password_changed", user)

    @staticmethod
    def public(u: dict) -> dict:
        return {"id": u["id"], "email": u["email"], "name": u["name"], "role": u["role"], "must_change": bool(u["must_change"])}

    # ---- Questions per day ------------------------------------------------------------------------------------

    def usage(self, user: dict) -> dict:
        day = date.today().isoformat()
        mine = self._one("SELECT count FROM usage WHERE user_id = ? AND day = ?", (user["id"], day))
        everyone = self._one("SELECT COALESCE(SUM(count), 0) AS n FROM usage WHERE day = ?", (day,))
        return {"today": mine["count"] if mine else 0, "per_day": self.lim["questions_per_day_per_user"],
                "all_today": int(everyone["n"]), "all_per_day": self.lim["questions_per_day"]}

    def take_question(self, user: dict) -> str | None:
        """Count one question, or say which daily limit stops it."""
        with self.lock:
            u = self.usage(user)
            if u["today"] >= u["per_day"]:
                return f"You have reached your limit of {u['per_day']} questions today. It resets tomorrow."
            if u["all_today"] >= u["all_per_day"]:
                return f"Today's limit of {u['all_per_day']} questions for all accounts is reached. It resets tomorrow."
            self._run("INSERT INTO usage VALUES (?, ?, 1) ON CONFLICT (user_id, day) DO UPDATE SET count = count + 1",
                      (user["id"], date.today().isoformat()))
            return None

    # ---- Chats, each visible to its owner only ----------------------------------------------------------------

    def chats(self, user: dict) -> list[dict]:
        return self._all("SELECT id, title, updated FROM chats WHERE user_id = ? ORDER BY updated DESC", (user["id"],))

    def chat(self, user: dict, chat_id: str) -> dict | None:
        c = self._one("SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id or "", user["id"]))
        if c:
            c["turns"] = json.loads(c["turns"])
        return c

    def add_turn(self, user: dict, chat_id: str | None, turn: dict) -> str:
        """Append a turn to the person's chat, or start a chat. Returns the chat id."""
        now = time.time()
        with self.lock:
            c = self.chat(user, chat_id) if chat_id else None
            if c is None:
                chat_id = uuid.uuid4().hex
                title = ("Example: " if turn.get("example") else "") + turn["question"][:60]
                self._run("INSERT INTO chats VALUES (?, ?, ?, ?, ?, ?)", (chat_id, user["id"], title, now, now, json.dumps([turn], default=str)))
                keep = self.lim["max_chats_per_user"]
                self._run("DELETE FROM chats WHERE user_id = ? AND id NOT IN "
                          "(SELECT id FROM chats WHERE user_id = ? ORDER BY updated DESC LIMIT ?)", (user["id"], user["id"], keep))
            else:
                turns = c["turns"] + [turn]
                self._run("UPDATE chats SET turns = ?, updated = ? WHERE id = ?", (json.dumps(turns, default=str), now, chat_id))
        return chat_id

    def set_feedback(self, user: dict, chat_id: str, index: int, rating: str) -> dict:
        """Mark a turn with the person's opinion; returns the turn."""
        with self.lock:
            c = self.chat(user, chat_id)
            if c is None or not 0 <= index < len(c["turns"]):
                raise AccountError("No such answer.")
            c["turns"][index]["feedback"] = rating
            self._run("UPDATE chats SET turns = ? WHERE id = ?", (json.dumps(c["turns"], default=str), chat_id))
            return c["turns"][index]

    def delete_chat(self, user: dict, chat_id: str) -> bool:
        return self._run("DELETE FROM chats WHERE id = ? AND user_id = ?", (chat_id, user["id"])) > 0

    def history(self, user: dict, chat_id: str | None) -> list[dict]:
        """The earlier questions and answers of the chat, as the model sees them, within the limits."""
        c = self.chat(user, chat_id) if chat_id else None
        if not c:
            return []
        n, chars = self.lim["max_history_turns"], self.lim["max_history_chars"]
        turns = [t for t in c["turns"] if not t.get("error") and not t.get("example") and t.get("text")][-n:]
        out = []
        for t in turns:
            out += [{"role": "user", "content": t["question"][:chars]}, {"role": "assistant", "content": t["text"][:chars]}]
        return out
