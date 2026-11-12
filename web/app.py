"""Web app of the WFP Chad data assistant: a browser chat over the agent's read-only tools, with individual accounts.

From the repository root, in the ensa-agent (or wfp-agent) environment:
  python manage_users.py add you@example.org --name "Your Name" --admin     # the first account
  python web/app.py

Every page but the sign-in needs an account, created by the administrator (manage_users.py). The chats, the daily
counts and the security log are kept per account in local/accounts.sqlite (see web/accounts.py). It needs
ANTHROPIC_API_KEY to answer. It listens on 127.0.0.1 unless HOST says otherwise; put HTTPS in front of a server that
others reach.
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from accounts import AccountError, Accounts, default_state  # noqa: E402

from wfp_agent import tools  # noqa: E402

STATIC = Path(__file__).with_name("static")
# Accounts, chats, daily counts, feedback and the security log stay on this computer, never in the repository
# (see accounts.default_state: out of OneDrive on Windows).
STATE = default_state()
COOKIE = "wfp_agent_session"
CSP = "; ".join(["default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
                 "font-src 'self'", "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'",
                 "form-action 'self'"])


class Chat(BaseModel):
    question: str
    chat_id: str | None = None


class Login(BaseModel):
    email: str
    password: str


class NewPassword(BaseModel):
    current: str
    new: str


class Feedback(BaseModel):
    chat_id: str
    turn: int
    rating: Literal["up", "down"]
    comment: str = ""


def open_accounts(state: Path | None = None) -> Accounts:
    return Accounts((state or STATE) / "accounts.sqlite", tools.LIMITS)


def check_startup(accounts: Accounts) -> None:
    """The app never runs without an account to sign in with."""
    if accounts.count_active() == 0:
        raise SystemExit('No account yet. Create the first one: python manage_users.py add you@example.org --name "Your Name" --admin')


def first_sentence(doc: str) -> str:
    """The first sentence of a tool's description, for the "Rounds and data" page."""
    # The SDK splits a docstring's first line from the rest with a blank line, even mid-sentence: join them again.
    text = " ".join(doc.split())
    return text.split(". ")[0].rstrip(".") + "."


def default_agent_factory():
    from wfp_agent.agent import WfpAgent
    return WfpAgent()


def create_app(accounts: Accounts | None = None, agent_factory: Callable = default_agent_factory, state: Path | None = None) -> FastAPI:
    app = FastAPI(title="WFP Chad data assistant", docs_url=None, redoc_url=None, openapi_url=None)
    state = state or STATE
    accounts = accounts or open_accounts(state)
    feedback_lock = threading.Lock()
    busy = threading.Semaphore(tools.LIMITS["concurrent_questions"])
    attempts: dict[str, list[float]] = {}
    secure_cookie = os.environ.get("WFP_AGENT_SECURE_COOKIE", "").lower() == "true"

    def ip_of(request: Request) -> str:
        return request.client.host if request.client else "?"

    def user_of(request: Request, allow_must_change: bool = False) -> dict:
        """The signed-in person, or 401; 403 while a temporary password is not yet changed."""
        u = accounts.session_user(request.cookies.get(COOKIE, ""))
        if u is None:
            raise HTTPException(401, "Sign in first.")
        if u["must_change"] and not allow_must_change:
            raise HTTPException(403, "Change your temporary password first.")
        return u

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # ---- Sign-in -------------------------------------------------------------------------------------------

    @app.post("/api/login")
    def login(body: Login, request: Request):
        ip, now = ip_of(request), time.time()
        for known in [k for k, v in attempts.items() if not v or now - v[-1] >= 60]:
            del attempts[known]
        recent = [t for t in attempts.get(ip, []) if now - t < 60]
        if len(recent) >= tools.LIMITS["login_attempts_per_minute"]:
            raise HTTPException(429, "Too many attempts: wait a minute.")
        attempts[ip] = recent + [now]
        try:
            token, user = accounts.login(body.email, body.password, ip)
        except AccountError as e:
            raise HTTPException(401, str(e)) from None
        hours = tools.LIMITS["session_hours"]
        response = JSONResponse({"ok": True, "user": user})
        response.set_cookie(COOKIE, token, max_age=hours * 3600, httponly=True, samesite="strict",
                            secure=secure_cookie or request.url.scheme == "https")
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        accounts.logout(request.cookies.get(COOKIE, ""))
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE)
        return response

    @app.post("/api/password")
    def password(body: NewPassword, request: Request):
        u = user_of(request, allow_must_change=True)
        try:
            accounts.change_password(u, request.cookies.get(COOKIE, ""), body.current, body.new)
        except AccountError as e:
            raise HTTPException(400, str(e)) from None
        return {"ok": True}

    @app.get("/api/status")
    def status(request: Request):
        u = accounts.session_user(request.cookies.get(COOKIE, ""))
        if u is None:
            return {"locked": True}
        if u["must_change"]:
            return {"locked": False, "user": u}
        try:
            info = {**tools.build_info(), "rounds": len(tools._table("rounds.csv"))}
        except FileNotFoundError:
            info = None
        return {"locked": False, "user": u, "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "data": info, "tools": [t.name for t in tools.TOOLS], "limits": tools.LIMITS, "usage": accounts.usage(u)}

    # ---- Pages and the fixed example ------------------------------------------------------------------------

    @app.get("/api/about")
    def about(request: Request):
        """What the "How it works" and "Rounds and data" pages show: the data version, the rounds and the tools."""
        user_of(request)
        try:
            rounds = tools._table("rounds.csv")
            cols = [c for c in ("round_id", "round", "survey", "series", "final_rows", "regions", "date_p01", "date_p99") if c in rounds]
            info, rows = tools.build_info(), rounds[cols].to_dict(orient="records")
        except FileNotFoundError:
            info, rows = None, []
        described = [{"name": t.name, "does": first_sentence(t.description or "")} for t in tools.TOOLS]
        return {"data": info, "rounds": rows, "tools": described, "limits": tools.LIMITS}

    @app.post("/api/example")
    def example(request: Request):
        """A fixed answer written by the server from the tables (no model, no API key, no cost), kept as a new chat."""
        u = user_of(request)
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "Another question is running. Try again in a moment.")
        try:
            from wfp_agent import example as fixed
            turn = json.loads(json.dumps(fixed.build(), ensure_ascii=False, default=str))
        except FileNotFoundError:
            raise HTTPException(503, "The agent's tables are not built: run python build_agent_data.py.") from None
        finally:
            busy.release()
        turn["at"] = datetime.now(UTC).isoformat(timespec="seconds")
        return {"chat_id": accounts.add_turn(u, None, turn), "turn": turn}

    # ---- Chats -----------------------------------------------------------------------------------------------

    @app.get("/api/chats")
    def chats(request: Request):
        return accounts.chats(user_of(request))

    @app.get("/api/chats/{chat_id}")
    def chat_get(chat_id: str, request: Request):
        c = accounts.chat(user_of(request), chat_id)
        if c is None:
            raise HTTPException(404, "No such chat.")
        return {"id": c["id"], "title": c["title"], "turns": c["turns"]}

    @app.delete("/api/chats/{chat_id}")
    def chat_delete(chat_id: str, request: Request):
        if not accounts.delete_chat(user_of(request), chat_id):
            raise HTTPException(404, "No such chat.")
        return {"ok": True}

    @app.post("/api/feedback")
    def feedback(body: Feedback, request: Request):
        """An opinion on one answer of the person's own chat, kept on this computer to find weak answers."""
        u = user_of(request)
        if len(body.comment) > tools.LIMITS["feedback_comment_chars"]:
            raise HTTPException(400, f"Keep the comment under {tools.LIMITS['feedback_comment_chars']} characters.")
        try:
            turn = accounts.set_feedback(u, body.chat_id, body.turn, body.rating)
        except AccountError as e:
            raise HTTPException(404, str(e)) from None
        row = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "user": u["email"], "rating": body.rating, "comment": body.comment,
               "question": turn.get("question"), "answer": turn.get("text"), "example": bool(turn.get("example")),
               "tool_calls": turn.get("tool_calls"), "grounding": turn.get("grounding"), "provenance": turn.get("provenance")}
        with feedback_lock:
            state.mkdir(parents=True, exist_ok=True)
            with open(state / "feedback.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return {"ok": True}

    @app.post("/api/chat/stream")
    def chat(body: Chat, request: Request):
        u = user_of(request)
        question = body.question.strip()
        if not question or len(question) > tools.LIMITS["max_question_chars"]:
            raise HTTPException(400, f"Ask a question of 1 to {tools.LIMITS['max_question_chars']} characters.")
        if body.chat_id and accounts.chat(u, body.chat_id) is None:
            raise HTTPException(404, "No such chat.")
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "Another question is running. Try again in a moment.")
        if refusal := accounts.take_question(u):
            busy.release()
            raise HTTPException(429, refusal + " The fixed example still works.")
        events: queue.Queue = queue.Queue()

        def work():
            turn = {"question": question, "at": datetime.now(UTC).isoformat(timespec="seconds")}
            try:
                agent = agent_factory()
                # The history comes from the server's copy of the chat, not from the browser.
                agent.history = accounts.history(u, body.chat_id)
                a = agent.ask(question, on_event=events.put)
                turn.update(text=a.text, published=a.published, tool_calls=a.tool_calls, input_tokens=a.input_tokens,
                            output_tokens=a.output_tokens, refused=a.refused, grounding=a.grounding, provenance=a.provenance,
                            tool_outputs=a.tool_outputs)
                turn = json.loads(json.dumps(turn, ensure_ascii=False, default=str))
                chat_id = accounts.add_turn(u, body.chat_id, turn)
                events.put({"type": "answer", "chat_id": chat_id, **turn})
            except Exception as e:  # shown to the user, without internals
                turn["error"] = friendly_error(e)
                chat_id = accounts.add_turn(u, body.chat_id, turn)
                events.put({"type": "error", "chat_id": chat_id, "message": turn["error"]})
            finally:
                busy.release()
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while (event := events.get()) is not None:
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def friendly_error(e: Exception) -> str:
    name = type(e).__name__
    if name == "AuthenticationError" or "authentication" in str(e).lower():
        return "The Anthropic API key is missing or not valid. Set ANTHROPIC_API_KEY and restart."
    if name == "RateLimitError":
        return "The API rate limit was reached. Try again in a minute."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "Could not reach the Anthropic API. Check the connection and try again."
    if isinstance(e, FileNotFoundError):
        return "The agent's tables are not built: run python build_agent_data.py."
    return f"The question failed ({name}). Try again or narrow it."


if __name__ == "__main__":
    import uvicorn

    store = open_accounts()
    check_startup(store)
    uvicorn.run(create_app(store), host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8011")))
