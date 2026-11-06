"""A stand-in for the Anthropic client: it replays scripted tool calls through the agent's real tools, so the tests
exercise the whole loop (tools, results, publishing, streaming) with no API key. It says nothing about how the real
model would choose; the live evals (evals/run_evals.py) do that."""
from types import SimpleNamespace as NS


def text(s):
    return NS(type="text", text=s)


def usage():
    return NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0)


class ScriptedClient:
    """script = a list of steps; a step is [(tool name, arguments), ...]; `answer` is the final text."""

    def __init__(self, script, answer="Done."):
        self.script, self.answer = script, answer
        self.calls = []
        self.beta = NS(messages=NS(tool_runner=self.tool_runner))

    def tool_runner(self, **kw):
        self.calls.append(kw)
        by_name = {t.name: t for t in kw["tools"]}
        self.outputs = []
        for step in self.script:
            for name, args in step:
                self.outputs.append(by_name[name].call(args))
            yield NS(content=[], stop_reason="tool_use", usage=usage())
        yield NS(content=[text(self.answer)], stop_reason="end_turn", usage=usage())
