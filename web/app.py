"""Web app of the WFP Chad data assistant: a browser chat over the agent's read-only tools.

From the repository root, in the ensa-agent (or wfp-agent) environment:
  python web/app.py

It needs ANTHROPIC_API_KEY to answer, and APP_PASSWORD unless WFP_AGENT_ALLOW_UNLOCKED=true on this computer.
It listens on 127.0.0.1 only unless HOST says otherwise, and then always requires the password.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import secrets
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from wfp_agent import tools  # noqa: E402

STATIC = Path(__file__).with_name("static")
COOKIE = "wfp_agent_session"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CSP = "; ".join(["default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
                 "font-src 'self'", "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"])


class Chat(BaseModel):
    question: str
    history: list[dict] = []


class Unlock(BaseModel):
    password: str


def check_startup(host: str, password: str, allow_unlocked: bool) -> None:
    """A reachable server never runs without a password."""
    if not password and not (allow_unlocked and host in LOOPBACK):
        raise SystemExit("Set APP_PASSWORD (or, on your own computer only, WFP_AGENT_ALLOW_UNLOCKED=true).")


def clean_history(history: list[dict]) -> list[dict]:
    """Keep plain question and answer turns from the browser, within the limits."""
    lim = tools.LIMITS
    turns = [{"role": t["role"], "content": t["content"][:lim["max_history_chars"]]} for t in history
             if t.get("role") in ("user", "assistant") and isinstance(t.get("content"), str) and t["content"]]
    turns = turns[-lim["max_history_turns"] * 2:]
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def default_agent_factory():
    from wfp_agent.agent import WfpAgent
    return WfpAgent()


def create_app(password: str = "", agent_factory: Callable = default_agent_factory) -> FastAPI:
    app = FastAPI(title="WFP Chad data assistant", docs_url=None, redoc_url=None, openapi_url=None)
    secret = secrets.token_bytes(32)
    busy = threading.Semaphore(tools.LIMITS["concurrent_questions"])
    attempts: dict[str, list[float]] = {}

    def token(expires: int) -> str:
        sig = hmac.new(secret, str(expires).encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{sig}"

    def unlocked(request: Request) -> bool:
        if not password:
            return True
        raw = request.cookies.get(COOKIE, "")
        expires = raw.split(".")[0]
        return expires.isdigit() and int(expires) > time.time() and hmac.compare_digest(raw, token(int(expires)))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/unlock")
    def unlock(body: Unlock, request: Request):
        ip = request.client.host if request.client else "?"
        now = time.time()
        for known in [k for k, v in attempts.items() if not v or now - v[-1] >= 60]:
            del attempts[known]
        recent = [t for t in attempts.get(ip, []) if now - t < 60]
        if len(recent) >= tools.LIMITS["unlock_attempts_per_minute"]:
            raise HTTPException(429, "Too many attempts: wait a minute.")
        attempts[ip] = recent + [now]
        if not password or not hmac.compare_digest(body.password.encode(), password.encode()):
            raise HTTPException(401, "Wrong password.")
        hours = tools.LIMITS["session_hours"]
        response = JSONResponse({"ok": True})
        response.set_cookie(COOKIE, token(int(now) + hours * 3600), max_age=hours * 3600, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https")
        return response

    @app.get("/api/status")
    def status(request: Request):
        if not unlocked(request):
            return {"locked": True}
        try:
            info = {**tools.build_info(), "rounds": len(tools._table("rounds.csv"))}
        except FileNotFoundError:
            info = None
        return {"locked": False, "password_set": bool(password), "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "data": info, "tools": [t.name for t in tools.TOOLS], "limits": tools.LIMITS}

    @app.post("/api/chat/stream")
    def chat(body: Chat, request: Request):
        if not unlocked(request):
            raise HTTPException(401, "Locked.")
        question = body.question.strip()
        if not question or len(question) > tools.LIMITS["max_question_chars"]:
            raise HTTPException(400, f"Ask a question of 1 to {tools.LIMITS['max_question_chars']} characters.")
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "Another question is running. Try again in a moment.")
        events: queue.Queue = queue.Queue()

        def work():
            try:
                agent = agent_factory()
                agent.history = clean_history(body.history)
                a = agent.ask(question, on_event=events.put)
                events.put({"type": "answer", "text": a.text, "published": a.published, "tool_calls": a.tool_calls,
                            "input_tokens": a.input_tokens, "output_tokens": a.output_tokens, "refused": a.refused})
            except Exception as e:  # shown to the user, without internals
                events.put({"type": "error", "message": friendly_error(e)})
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

    host = os.environ.get("HOST", "127.0.0.1")
    pw = os.environ.get("APP_PASSWORD", "")
    check_startup(host, pw, os.environ.get("WFP_AGENT_ALLOW_UNLOCKED", "").lower() == "true")
    uvicorn.run(create_app(pw), host=host, port=int(os.environ.get("PORT", "8011")))
