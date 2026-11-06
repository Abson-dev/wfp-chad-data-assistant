"""Ask the WFP Chad data assistant from the command line.

    python ask.py "Which regions had the highest share of poor or borderline FCS in the 2023 Annual survey?"
    python ask.py            # interactive: type questions, 'reset' to start over, 'quit' to leave

Options: --sources prints the tool calls behind each answer.
Needs an Anthropic API key (ANTHROPIC_API_KEY) or a profile from `ant auth login`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic  # noqa: E402

from wfp_agent import tools  # noqa: E402
from wfp_agent.agent import MODEL, WfpAgent  # noqa: E402


def show(answer, sources: bool) -> None:
    print("\n" + answer.text + "\n")
    if sources and answer.tool_calls:
        print("Sources (tool calls):")
        for c in answer.tool_calls:
            args = ", ".join(f"{k}={v!r}" for k, v in c["args"].items())
            print(f"  - {c['tool']}({args})")
    print(f"[{MODEL} · {answer.input_tokens:,} input / {answer.output_tokens:,} output tokens]\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question", nargs="*", help="question; omit for interactive mode")
    p.add_argument("--sources", action="store_true", help="print the tool calls behind each answer")
    a = p.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # accented region names on the Windows console
    if not (tools.DATA / "indicators.csv").exists():
        print(f"Agent data not found in {tools.DATA}. Run first: python build_agent_data.py")
        return 1
    try:
        agent = WfpAgent()
    except anthropic.AnthropicError as e:
        print(f"Could not create the API client: {e}\nSet ANTHROPIC_API_KEY or run `ant auth login`.")
        return 1

    def run(q: str) -> None:
        try:
            show(agent.ask(q), a.sources)
        except anthropic.AuthenticationError:
            print("Authentication failed: check ANTHROPIC_API_KEY.")
        except anthropic.RateLimitError:
            print("Rate limited: wait a minute and ask again.")
        except anthropic.APIConnectionError:
            print("Network error: check the internet connection.")
        except anthropic.APIStatusError as e:
            print(f"API error {e.status_code}: {e.message}")
        except TypeError as e:
            if "authentication" not in str(e):
                raise
            print("No API credentials found. Set ANTHROPIC_API_KEY (see README.md) or run `ant auth login`.")

    if a.question:
        run(" ".join(a.question))
        return 0
    print("WFP Chad data assistant. Type a question; 'reset' clears the conversation, 'quit' exits.\n")
    while True:
        try:
            q = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if q.lower() in ("quit", "exit", "q"):
            return 0
        if q.lower() == "reset":
            agent.reset()
            print("Conversation cleared.\n")
        elif q:
            run(q)


if __name__ == "__main__":
    sys.exit(main())
