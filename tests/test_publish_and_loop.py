"""What the reader sees (checked by the server), the agent loop with a scripted model, and the grading of the evals."""
import json

import pandas as pd
import pytest
from run_evals import grade
from scripted_model import ScriptedClient

from build_agent_data import FORBIDDEN
from wfp_agent import results, tools
from wfp_agent.agent import SYSTEM, WfpAgent


def national(rows):
    return rows(tools.get_indicator, indicator="fcs_pb")[1].split()[1]


# ---- Publishing ---------------------------------------------------------------------------------------------

def test_publish_a_line_by_series(rows):
    rid = national(rows)
    assert tools.publish_result.call({"result_id": rid, "chart": "line", "x": "round", "y": "as_merged", "series": "series"}).startswith("Published")
    p = tools.PUBLISHED[0]
    assert p["chart"]["x_order"][0] == "2018 Baseline" and p["row_count"] == 10 and p["source"]["tool"] == "get_indicator"
    assert "NaN" not in json.dumps(p)  # the browser receives nulls


def test_publish_refuses_joining_series(rows):
    assert "must not join" in tools.publish_result.call({"result_id": national(rows), "chart": "line", "x": "round", "y": "as_merged"})
    assert not tools.PUBLISHED


@pytest.mark.parametrize(("kw", "why"), [
    ({"chart": "pie"}, "Chart must be"),
    ({"chart": "bar", "x": "nope", "y": "as_merged"}, "No column"),
    ({"chart": "bar", "x": "", "y": "as_merged"}, "column is required"),
    ({"chart": "scatter", "x": "round", "y": "as_merged"}, "two numeric"),
    ({"chart": "heatmap", "x": "round", "y": "group", "series": "round"}, "numeric series"),
])
def test_publish_refuses_a_chart_the_data_cannot_support(rows, kw, why):
    assert why in tools.publish_result.call({"result_id": national(rows), **kw})


def test_publish_refuses_duplicate_rows_on_a_chart(rows):
    rid = rows(tools.get_indicator, indicator="fcs_poor", breakdown="region")[1].split()[1]
    assert "More than one row" in tools.publish_result.call({"result_id": rid, "chart": "bar", "x": "round", "y": "as_merged"})
    assert "Too many series" not in tools.publish_result.call({"result_id": rid, "chart": "heatmap", "x": "round", "y": "group", "series": "as_merged"})


def test_publish_unknown_result():
    assert "Unknown result_id" in tools.publish_result.call({"result_id": "R99"})


def test_publish_too_many_rows():
    stored = {"id": "R1", "rows": pd.DataFrame({"a": range(tools.LIMITS["max_rows_shown"] + 1)}), "title": "t", "tool": "x", "args": {}}
    with pytest.raises(results.PublishError):
        results.build_publication(stored, "table", "", "", "", "", "", [], tools.LIMITS)


def test_results_never_carry_household_columns(rows):
    for tool, kw in [(tools.trace_variable, {"variable": "HHHSex"}), (tools.get_merge_status, {}), (tools.search_variables, {"term": "sexe"}),
                     (tools.describe_variable, {"variable": "HHHSex"}), (tools.get_indicator, {"indicator": "fcs_pb", "breakdown": "region"})]:
        t, _ = rows(tool, **kw)
        assert not set(t.columns) & FORBIDDEN


# ---- The loop ----------------------------------------------------------------------------------------------

def scripted(script, answer="Done."):
    return WfpAgent(client=ScriptedClient(script, answer))


def test_loop_publishes_and_traces():
    a = scripted([[("get_indicator", {"indicator": "fcs_pb"})],
                  [("publish_result", {"result_id": "R1", "chart": "line", "x": "round", "y": "as_merged", "series": "series"})]]).ask("trend?")
    assert a.text == "Done." and a.published["chart"]["type"] == "line"
    assert [c["tool"] for c in a.tool_calls] == ["get_indicator", "publish_result"]


def test_loop_shows_the_only_table_when_nothing_is_published():
    a = scripted([[("trace_variable", {"variable": "HHHSex"})]]).ask("how?")
    assert a.published and a.published["chart"] is None and a.published["row_count"] == 10


def test_loop_shows_nothing_when_several_tables_are_unpublished():
    assert scripted([[("list_rounds", {})], [("get_merge_status", {})]]).ask("?").published is None


def test_a_new_question_forgets_earlier_results():
    agent = scripted([[("get_merge_status", {})]])
    agent.ask("one")
    agent.client.script = []
    assert agent.ask("two").published is None


def test_follow_up_keeps_only_question_and_answer():
    agent = scripted([], "First answer.")
    agent.ask("first")
    agent.ask("second")
    assert [m["content"] for m in agent.client.calls[-1]["messages"]] == ["first", "First answer.", "second"]


def test_the_request_uses_the_limits_and_the_cached_prompt():
    agent = scripted([])
    agent.ask("q")
    kw = agent.client.calls[0]
    assert kw["max_iterations"] == tools.LIMITS["max_tool_rounds"]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert {t.name for t in kw["tools"]} == {t.name for t in tools.TOOLS}


def test_a_refusal_is_reported():
    client = ScriptedClient([])
    from scripted_model import NS, usage

    client.tool_runner = lambda **kw: iter([NS(content=[], stop_reason="refusal", usage=usage())])
    client.beta.messages.tool_runner = client.tool_runner
    a = WfpAgent(client=client).ask("q")
    assert a.refused and "declined" in a.text


def test_running_out_of_rounds_says_so():
    client = ScriptedClient([])
    from scripted_model import NS, usage

    client.tool_runner = lambda **kw: iter([NS(content=[], stop_reason="tool_use", usage=usage())])
    client.beta.messages.tool_runner = client.tool_runner
    assert "limit for one question" in WfpAgent(client=client).ask("q").text


def test_tool_events_reach_the_listener():
    seen = []
    scripted([[("list_rounds", {})]]).ask("rounds", on_event=seen.append)
    assert seen and seen[0]["type"] == "tool" and seen[0]["tool"] == "list_rounds"


def test_the_instructions_name_every_merge_tool_and_cover_the_cautions():
    for name in ("trace_variable", "describe_variable", "get_merge_status", "search_data_issues", "get_validation_checks", "publish_result"):
        assert name in SYSTEM
    for caution in ("Never estimate", "significant", "unweighted", "lean season", "flagged", "WFP's agreement"):
        assert caution in SYSTEM


# ---- Evals: the grading itself -------------------------------------------------------------------------------

def test_grade_finds_the_value_within_tolerance():
    assert grade("It was 41.4% in the 2023 Annual.", 41.5, "")[0]
    assert grade("Il était de 41,4 %.", 41.5, "")[0]
    assert not grade("It was 45%.", 41.5, "")[0]


def test_grade_checks_required_words_and_tools():
    assert grade("Not available in that round", None, "not available|missing")[0]
    assert not grade("It is 5", None, "not available|missing")[0]
    assert grade("x", None, "", "trace_variable|describe_variable", ("list_rounds", "describe_variable"))[0]
    assert not grade("x", None, "", "trace_variable", ("list_rounds",))[0]


def test_every_eval_question_is_well_formed():
    from pathlib import Path

    q = pd.read_csv(Path(tools.AGENT_DIR) / "evals" / "questions.csv", dtype=str, keep_default_na=False)
    assert q["id"].is_unique and len(q) >= 18 and (q["question"].str.len() > 5).all()
    names = {t.name for t in tools.TOOLS}
    for calls in q["must_call"]:
        assert not calls or set(calls.split("|")) <= names
    known = set(pd.read_csv(tools.DATA / "indicator_catalog.csv")["id"])
    assert set(q.loc[q["indicator"] != "", "indicator"]) <= known
