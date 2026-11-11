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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

AGENT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AGENT))
from wfp_agent import tools  # noqa: E402

STATIC = Path(__file__).with_name("static")
# The feedback and the count of questions per day stay on this computer (not versioned): answers quote regional figures.
STATE = Path(os.environ.get("WFP_AGENT_STATE", AGENT / "local"))
COOKIE = "wfp_agent_session"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CSP = "; ".join(["default-src 'self'", "script-src 'self'", "style-src 'self' 'unsafe-inline'", "img-src 'self' data:",
                 "font-src 'self'", "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"])


class Chat(BaseModel):
    question: str
    history: list[dict] = []


class Unlock(BaseModel):
    password: str


class Feedback(BaseModel):
    question: str
    answer: str = ""
    rating: Literal["up", "down"]
    comment: str = ""
    tool_calls: list[dict] = []
    grounding: dict = {}
    provenance: dict = {}
    example: bool = False


class DailyCount:
    """Questions sent to the model today, kept in a file so a restart does not reset the limit."""

    def __init__(self, path: Path, per_day: int):
        self.path, self.per_day, self.lock = path, per_day, threading.Lock()

    def _read(self) -> int:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            return int(saved["count"]) if saved.get("date") == date.today().isoformat() else 0
        except (OSError, ValueError, KeyError, TypeError):
            return 0

    def used(self) -> int:
        with self.lock:
            return self._read()

    def take(self) -> bool:
        """Count one question, or refuse when today's limit is reached."""
        with self.lock:
            n = self._read()
            if n >= self.per_day:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"date": date.today().isoformat(), "count": n + 1}), encoding="utf-8")
            return True


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


def first_sentence(doc: str) -> str:
    """The first sentence of a tool's description, for the "Rounds and data" page."""
    # The SDK splits a docstring's first line from the rest with a blank line, even mid-sentence: join them again.
    text = " ".join(doc.split())
    return text.split(". ")[0].rstrip(".") + "."


def default_agent_factory():
    from wfp_agent.agent import WfpAgent
    return WfpAgent()


def create_app(password: str = "", agent_factory: Callable = default_agent_factory, state: Path | None = None) -> FastAPI:
    app = FastAPI(title="WFP Chad data assistant", docs_url=None, redoc_url=None, openapi_url=None)
    state = state or STATE
    count = DailyCount(state / "usage.json", tools.LIMITS["questions_per_day"])
    feedback_lock = threading.Lock()
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
                "data": info, "tools": [t.name for t in tools.TOOLS], "limits": tools.LIMITS,
                "usage": {"today": count.used(), "per_day": tools.LIMITS["questions_per_day"]}}

    @app.get("/api/about")
    def about(request: Request):
        """What the "How it works" and "Rounds and data" pages show: the data version, the rounds and the tools."""
        if not unlocked(request):
            raise HTTPException(401, "Locked.")
        try:
            rounds = tools._table("rounds.csv")
            cols = [c for c in ("round_id", "round", "survey", "series", "final_rows", "regions", "date_p01", "date_p99") if c in rounds]
            info, rows = tools.build_info(), rounds[cols].to_dict(orient="records")
        except FileNotFoundError:
            info, rows = None, []
        described = [{"name": t.name, "does": first_sentence(t.description or "")} for t in tools.TOOLS]
        return {"data": info, "rounds": rows, "tools": described, "limits": tools.LIMITS}

    @app.get("/api/example")
    def example(request: Request):
        """A fixed answer written by the server from the tables: no model, no API key, no cost."""
        if not unlocked(request):
            raise HTTPException(401, "Locked.")
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "Another question is running. Try again in a moment.")
        try:
            from wfp_agent import example as fixed
            return JSONResponse(json.loads(json.dumps(fixed.build(), ensure_ascii=False, default=str)))
        except FileNotFoundError:
            raise HTTPException(503, "The agent's tables are not built: run python build_agent_data.py.") from None
        finally:
            busy.release()

    @app.post("/api/feedback")
    def feedback(body: Feedback, request: Request):
        """An opinion on one answer, kept on this computer to find weak answers and new eval questions."""
        if not unlocked(request):
            raise HTTPException(401, "Locked.")
        if len(body.comment) > tools.LIMITS["feedback_comment_chars"]:
            raise HTTPException(400, f"Keep the comment under {tools.LIMITS['feedback_comment_chars']} characters.")
        row = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **body.model_dump()}
        row["question"], row["answer"] = row["question"][:tools.LIMITS["max_question_chars"]], row["answer"][:20000]
        with feedback_lock:
            state.mkdir(parents=True, exist_ok=True)
            with open(state / "feedback.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return {"ok": True}

    @app.post("/api/chat/stream")
    def chat(body: Chat, request: Request):
        if not unlocked(request):
            raise HTTPException(401, "Locked.")
        question = body.question.strip()
        if not question or len(question) > tools.LIMITS["max_question_chars"]:
            raise HTTPException(400, f"Ask a question of 1 to {tools.LIMITS['max_question_chars']} characters.")
        if not busy.acquire(blocking=False):
            raise HTTPException(429, "Another question is running. Try again in a moment.")
        if not count.take():
            busy.release()
            raise HTTPException(429, f"Today's limit of {tools.LIMITS['questions_per_day']} questions is reached. It resets tomorrow; "
                                     "the fixed example still works.")
        events: queue.Queue = queue.Queue()

        def work():
            try:
                agent = agent_factory()
                agent.history = clean_history(body.history)
                a = agent.ask(question, on_event=events.put)
                events.put({"type": "answer", "text": a.text, "published": a.published, "tool_calls": a.tool_calls,
                            "input_tokens": a.input_tokens, "output_tokens": a.output_tokens, "refused": a.refused,
                            "grounding": a.grounding, "provenance": a.provenance, "tool_outputs": a.tool_outputs})
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
