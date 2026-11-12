"""Accounts for the web tests: a store in a temporary folder, and a client signed in as a test user."""
import json

import app as webapp
from fastapi.testclient import TestClient
from scripted_model import ScriptedClient

from wfp_agent.agent import WfpAgent

PASSWORD = "Temporary-pass-42"


def scripted(script=None, answer="Done."):
    return lambda: WfpAgent(client=ScriptedClient(script or [], answer))


def add(acc, email="ana@example.org", must_change=False, name="Ana Test"):
    """An account with the test password; already changed unless must_change."""
    if not acc.user_by_email(email):
        acc.add_user(email, name, PASSWORD)
        if not must_change:
            acc._run("UPDATE users SET must_change = 0 WHERE email = ?", (email,))
    return acc


def store(state, **kw):
    return add(webapp.open_accounts(state), **kw)


def client(acc, state, factory=None):
    return TestClient(webapp.create_app(acc, factory or scripted(), state))


def login(c, email="ana@example.org", password=PASSWORD):
    return c.post("/api/login", json={"email": email, "password": password})


def signed_in(state, factory=None, email="ana@example.org", must_change=False, acc=None):
    acc = add(acc or webapp.open_accounts(state), email=email, must_change=must_change)
    c = client(acc, state, factory)
    r = login(c, email)
    assert r.status_code == 200, r.text
    return c


def sse(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
