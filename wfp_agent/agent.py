"""The WFP Chad data assistant: Claude with read-only tools over the aggregate and metadata tables."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from . import __version__, grounding, results, tools

MODEL = os.environ.get("WFP_AGENT_MODEL", "claude-opus-5-5")
# Claude Opus 5.5 defaults to medium effort; set it explicitly. Thinking is adaptive (always on for this model).
EFFORT = os.environ.get("WFP_AGENT_EFFORT", "high")
SYSTEM = (Path(__file__).with_name("system_prompt.md")).read_text(encoding="utf-8")


@dataclass
class Answer:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False
    published: dict | None = None
    # Which figures of the answer were found in the tool results (grounding.check).
    grounding: dict = field(default_factory=lambda: {"checked": 0, "unverified": [], "ok": True})
    # What produced the answer: model, effort and the version of the data, for the downloadable trace.
    provenance: dict = field(default_factory=dict)
    tool_outputs: list[str] = field(default_factory=list)


class WfpAgent:
    """Multi-turn assistant. Each question runs a tool loop; earlier questions and answers are kept
    as conversation history so follow-ups ("and in Lac?") work."""

    def __init__(self, client: anthropic.Anthropic | None = None, max_iterations: int | None = None):
        # A stalled request must not outlive the time allowed for a question.
        self.client = client or anthropic.Anthropic(timeout=tools.LIMITS["seconds_per_question"])
        self.max_iterations = max_iterations or tools.LIMITS["max_tool_rounds"]
        self.history: list[dict] = []

    def ask(self, question: str, on_event: Callable[[dict], None] | None = None) -> Answer:
        tools.begin_question(on_event)
        deadline = time.monotonic() + tools.LIMITS["seconds_per_question"]
        messages = self.history + [{"role": "user", "content": question}]
        runner = self.client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=16000,
            # The fixed system prompt (and the tool definitions before it) is cached across questions.
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=tools.TOOLS,
            messages=messages,
            output_config={"effort": EFFORT},
            # If the model declines a request, the API retries it on a fallback model automatically.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            max_iterations=self.max_iterations,
        )
        last, n_in, n_out, timed_out = None, 0, 0, False
        for message in runner:
            last = message
            n_in += message.usage.input_tokens + (message.usage.cache_read_input_tokens or 0) \
                + (message.usage.cache_creation_input_tokens or 0)
            n_out += message.usage.output_tokens
            if time.monotonic() > deadline:
                timed_out = True
                break

        if last is None:
            return Answer("No response from the model.")
        if last.stop_reason == "refusal":
            text, refused = "The model declined this request.", True
        else:
            text = "\n".join(b.text for b in last.content if b.type == "text").strip()
            refused = False
            if last.stop_reason == "max_tokens":
                text += "\n\n[Answer cut off at the token limit.]"
            elif timed_out or last.stop_reason == "tool_use":
                # A question that uses up its time or its tool rounds says so rather than end on nothing.
                text = (text + "\n\n[Stopped at the limit for one question: narrow it and ask again.]").strip()
        # Every figure of the answer must come from this question's tool results, the earlier turns or the instructions.
        earlier = [m["content"] for m in self.history if isinstance(m.get("content"), str)]
        checked = {"checked": 0, "unverified": [], "ok": True}
        if text and not refused:
            checked = grounding.check(text, list(tools.OUTPUTS) + [question, SYSTEM] + earlier)
        # Keep only the question and the final answer in the history (append-only).
        self.history += [{"role": "user", "content": question}, {"role": "assistant", "content": text or "(no text)"}]
        published = tools.PUBLISHED[0] if tools.PUBLISHED else results.default_publication(
            tools.RESULTS, tools._table("rounds.csv")["round"].tolist(), tools.LIMITS)
        if published:
            published = {**published, "dashboard": tools.dashboard_link(published["source"]["tool"])}
        return Answer(text, list(tools.TRACE), n_in, n_out, refused, published, checked, provenance(),
                      list(tools.OUTPUTS))

    def reset(self) -> None:
        self.history.clear()


def provenance() -> dict:
    """The model and the data behind an answer, so that a reader can see what produced it and rebuild it."""
    try:
        info = tools.build_info()
    except FileNotFoundError:
        info = {}
    keep = ("merged_file", "built_at", "dashboard_run", "synthetic", "households", "columns")
    return {"model": MODEL, "effort": EFFORT, "agent_version": __version__, "data": {k: info[k] for k in keep if k in info}}
