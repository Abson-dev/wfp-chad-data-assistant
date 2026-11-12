"""The parts of the web app taken from riapa.ai: the fixed example, the information pages and the feedback."""
import json

import pytest
from webutil import scripted, signed_in, sse

from wfp_agent import tools


def test_the_example_needs_no_model_and_every_figure_is_found(tmp_path):
    def no_model():
        raise AssertionError("the example must not create an agent")

    c = signed_in(tmp_path, no_model)
    r = c.post("/api/example").json()
    e = r["turn"]
    assert e["example"] and e["question"] and e["text"].startswith("A fixed example")
    assert e["grounding"]["checked"] >= 4 and e["grounding"]["ok"], e["grounding"]
    assert e["published"]["chart"]["type"] == "line" and e["published"]["chart"]["series"] == "series"
    assert [t["tool"] for t in e["tool_calls"]] == ["profile_indicator", "publish_result"]
    assert e["provenance"]["model"].startswith("none")
    assert "unweighted" in e["text"] and "do not explain" in e["text"]
    # Kept as a chat of the account, titled as an example.
    assert c.get("/api/chats").json()[0]["title"].startswith("Example: ")
    assert c.get(f"/api/chats/{r['chat_id']}").json()["turns"][0]["example"]


def test_the_example_does_not_count_against_the_daily_limit(tmp_path):
    c = signed_in(tmp_path)
    c.post("/api/example")
    assert c.get("/api/status").json()["usage"]["today"] == 0


def test_the_example_is_not_sent_to_the_model_as_history(tmp_path):
    c = signed_in(tmp_path)
    chat_id = c.post("/api/example").json()["chat_id"]
    events = sse(c.post("/api/chat/stream", json={"question": "and in Lac?", "chat_id": chat_id}))
    assert events[-1]["type"] == "answer" and events[-1]["chat_id"] == chat_id


def test_about_gives_the_data_the_rounds_and_the_tools(tmp_path):
    a = signed_in(tmp_path).get("/api/about").json()
    assert a["data"]["merged_file"] and len(a["rounds"]) == len(tools._table("rounds.csv"))
    assert {"round_id", "round", "series", "final_rows"} <= set(a["rounds"][0])
    assert {t["name"] for t in a["tools"]} == {t.name for t in tools.TOOLS} and all(t["does"].endswith(".") for t in a["tools"])
    assert a["limits"]["questions_per_day_per_user"] > 0


def test_feedback_is_kept_with_the_turn_and_on_this_computer(tmp_path):
    c = signed_in(tmp_path, scripted([[("list_rounds", {})]], "Ten rounds."))
    chat_id = sse(c.post("/api/chat/stream", json={"question": "rounds?"}))[-1]["chat_id"]
    r = c.post("/api/feedback", json={"chat_id": chat_id, "turn": 0, "rating": "down", "comment": "Missing the flag."})
    assert r.json() == {"ok": True}
    rows = [json.loads(x) for x in (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()]
    # The question and answer come from the server's copy, not from the browser.
    assert rows[0]["rating"] == "down" and rows[0]["question"] == "rounds?" and rows[0]["answer"] == "Ten rounds."
    assert rows[0]["user"] == "ana@example.org" and rows[0]["tool_calls"][0]["tool"] == "list_rounds"
    assert c.get(f"/api/chats/{chat_id}").json()["turns"][0]["feedback"] == "down"


@pytest.mark.parametrize("body", [{"turn": 0, "rating": "maybe"}, {"turn": 0, "rating": "up", "comment": "x" * 1001},
                                  {"turn": 5, "rating": "up"}])
def test_feedback_refuses_a_bad_rating_a_long_comment_or_a_missing_turn(tmp_path, body):
    c = signed_in(tmp_path)
    chat_id = sse(c.post("/api/chat/stream", json={"question": "q"}))[-1]["chat_id"]
    assert c.post("/api/feedback", json={"chat_id": chat_id, **body}).status_code in (400, 404, 422)
    assert not (tmp_path / "feedback.jsonl").exists()
