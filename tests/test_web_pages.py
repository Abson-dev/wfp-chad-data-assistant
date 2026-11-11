"""The parts of the web app taken from riapa.ai: the fixed example, the information pages, the feedback and the
daily limit of questions."""
import json

import app as webapp
import pytest
from fastapi.testclient import TestClient
from scripted_model import ScriptedClient

from wfp_agent import tools
from wfp_agent.agent import WfpAgent


def client(tmp_path, password="", answer="Done."):
    return TestClient(webapp.create_app(password, lambda: WfpAgent(client=ScriptedClient([[("list_rounds", {})]], answer)),
                                        state=tmp_path))


def test_the_example_needs_no_model_and_every_figure_is_found(tmp_path):
    def no_model():
        raise AssertionError("the example must not create an agent")

    c = TestClient(webapp.create_app("", no_model, state=tmp_path))
    e = c.get("/api/example").json()
    assert e["example"] and e["question"] and e["text"].startswith("A fixed example")
    assert e["grounding"]["checked"] >= 4 and e["grounding"]["ok"], e["grounding"]
    assert e["published"]["chart"]["type"] == "line" and e["published"]["chart"]["series"] == "series"
    assert [t["tool"] for t in e["tool_calls"]] == ["profile_indicator", "publish_result"]
    assert e["provenance"]["model"].startswith("none")
    assert "unweighted" in e["text"] and "do not explain" in e["text"]


def test_the_example_does_not_count_against_the_daily_limit(tmp_path):
    c = client(tmp_path)
    c.get("/api/example")
    assert c.get("/api/status").json()["usage"]["today"] == 0


def test_about_gives_the_data_the_rounds_and_the_tools(tmp_path):
    a = client(tmp_path).get("/api/about").json()
    assert a["data"]["merged_file"] and len(a["rounds"]) == len(tools._table("rounds.csv"))
    assert {"round_id", "round", "series", "final_rows"} <= set(a["rounds"][0])
    assert {t["name"] for t in a["tools"]} == {t.name for t in tools.TOOLS} and all(t["does"].endswith(".") for t in a["tools"])
    assert a["limits"]["questions_per_day"] > 0


def test_feedback_is_kept_on_this_computer(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/feedback", json={"question": "q?", "answer": "a.", "rating": "down", "comment": "Missing the flag.",
                                      "tool_calls": [{"tool": "get_indicator", "args": {}}]})
    assert r.json() == {"ok": True}
    rows = [json.loads(x) for x in (tmp_path / "feedback.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["rating"] == "down" and rows[0]["comment"] == "Missing the flag." and rows[0]["at"]


@pytest.mark.parametrize("body", [{"question": "q", "rating": "maybe"}, {"question": "q", "rating": "up", "comment": "x" * 1001}])
def test_feedback_refuses_a_bad_rating_or_a_long_comment(tmp_path, body):
    assert client(tmp_path).post("/api/feedback", json=body).status_code in (400, 422)
    assert not (tmp_path / "feedback.jsonl").exists()


def test_the_new_pages_are_locked_with_the_app(tmp_path):
    c = client(tmp_path, password="pw")
    assert c.get("/api/example").status_code == 401 and c.get("/api/about").status_code == 401
    assert c.post("/api/feedback", json={"question": "q", "rating": "up"}).status_code == 401


def test_the_daily_limit_stops_questions_and_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setitem(tools.LIMITS, "questions_per_day", 2)
    c = client(tmp_path)
    for _ in range(2):
        assert c.post("/api/chat/stream", json={"question": "rounds?"}).status_code == 200
    r = c.post("/api/chat/stream", json={"question": "rounds?"})
    assert r.status_code == 429 and "limit" in r.json()["detail"]
    assert client(tmp_path).post("/api/chat/stream", json={"question": "rounds?"}).status_code == 429
    assert client(tmp_path).get("/api/status").json()["usage"] == {"today": 2, "per_day": 2}
    # A question refused for its length does not count.
    (tmp_path / "usage.json").unlink()
    assert client(tmp_path).post("/api/chat/stream", json={"question": "x" * 5000}).status_code == 400
    assert client(tmp_path).get("/api/status").json()["usage"]["today"] == 0


def test_yesterdays_count_is_forgotten(tmp_path):
    (tmp_path / "usage.json").write_text(json.dumps({"date": "2000-01-01", "count": 999}), encoding="utf-8")
    assert client(tmp_path).get("/api/status").json()["usage"]["today"] == 0
