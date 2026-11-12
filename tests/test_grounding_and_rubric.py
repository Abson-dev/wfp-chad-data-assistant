"""The design taken from IFPRI's RIAPA-AI: the automatic check of the answer's figures, the indicator profile that
does the arithmetic, the trace of each answer, and the expert grading against the model alone."""
import math

import pandas as pd
import pytest
import run_evals
import score_rubric
from scripted_model import ScriptedClient
from webutil import scripted, signed_in, sse

from wfp_agent import grounding, tools
from wfp_agent.agent import WfpAgent

SRC = ["result_id: R1\nround_id,as_merged,households\nR10,5.80,1747\nR01,21.16,3979",
       "The merged file has 17,255 households; 1.25 billion XAF."]


# ---- The check of the figures -------------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "En 2023 Annual (R10), 5,8 % des ménages ; 21,2 % en R01 (3 979 ménages).",
    "Emergency coping: 5.8% against 21.16% in 2018; 17,255 households; 1,25 milliard de XAF.",
    "17 255 ménages, 1.25 billion XAF, 21 % en 2018.",
])
def test_figures_from_the_tools_are_found(answer):
    g = grounding.check(answer, SRC)
    assert g["ok"] and g["checked"] >= 3, g


def test_an_invented_or_recomputed_figure_is_reported():
    g = grounding.check("Une baisse de 15,4 points, à 5,8 %, et 42.0 % ailleurs.", SRC)
    assert g["unverified"] == ["15,4", "42.0"] and not g["ok"]


def test_the_precision_written_is_the_precision_checked():
    # 21.16 rounds to 21.2, not to 21.3; 5.80 is 5.8 but not 5.85.
    assert grounding.check("21.2", SRC)["ok"] and not grounding.check("21.3", SRC)["ok"]
    assert not grounding.check("5.85", SRC)["ok"]


@pytest.mark.parametrize("answer", ["R10, v9, LhCSIStress1, q09", "3 régions sur 12", "en 2023 et 2026", "le 2026-11-11"])
def test_names_small_counts_years_and_dates_are_not_checked(answer):
    assert grounding.check(answer, [])["checked"] == 0


def test_the_agent_reports_the_check_with_its_answer():
    value = tools.RESULTS[tools.get_indicator.call({"indicator": "fcs_pb", "rounds": "R10"}).split()[1]]["rows"]["as_merged"].iloc[0]
    tools.begin_question()
    written = f"{value:.1f}".replace(".", ",")
    assert not grounding.check("987,6", [tools.get_indicator.call({"indicator": "fcs_pb"})])["ok"]
    tools.begin_question()
    agent = WfpAgent(client=ScriptedClient([[("get_indicator", {"indicator": "fcs_pb", "rounds": "R10"})]],
                                           f"{written} % des ménages, contre 987,6 % ailleurs."))
    a = agent.ask("FCS ?")
    assert a.grounding == {"checked": 2, "unverified": ["987,6"], "ok": False}
    assert a.tool_outputs and a.tool_outputs[0].startswith("result_id:")
    assert a.provenance["model"] and a.provenance["data"]["merged_file"]


def test_a_follow_up_may_repeat_a_figure_of_the_earlier_answer():
    agent = WfpAgent(client=ScriptedClient([[("list_rounds", {})]], "Il y a 3979 ménages en R01."))
    first = agent.ask("Combien ?")
    agent.client.script, agent.client.answer = [], "Oui, 3979 ménages."
    assert agent.ask("Tu confirmes ?").grounding["ok"] and first.grounding["checked"] == 1


def test_the_web_answer_carries_the_check_and_the_trace(tmp_path):
    c = signed_in(tmp_path, scripted([[("get_merge_status", {})]]))
    last = sse(c.post("/api/chat/stream", json={"question": "merge?"}))[-1]
    assert last["grounding"]["ok"] and last["provenance"]["agent_version"] and last["tool_outputs"]


# ---- The indicator profile ----------------------------------------------------------------------------------

def test_the_profile_computes_the_changes_within_a_series(rows):
    df, text = rows(tools.profile_indicator, indicator="fcs_pb")
    assert list(df.columns[:6]) == ["round_id", "round", "series", "value", "previous_round", "change_since_previous"]
    order = tools._table("rounds.csv")["round_id"].tolist()
    assert df["round_id"].tolist() == [r for r in order if r in set(df["round_id"])]
    for _, r in df.dropna(subset=["previous_round"]).iterrows():
        prev = df.set_index("round_id").loc[r["previous_round"]]
        assert prev["series"] == r["series"]
        assert math.isclose(r["change_since_previous"], round(r["value"] - prev["value"], 2), abs_tol=0.011)
    assert "Improved most:" in text and "Worsened most:" in text


def test_the_profile_names_winners_by_the_worse_end(rows):
    # For a share where high is worse, every region named as improved fell.
    _, text = rows(tools.profile_indicator, indicator="fcs_pb")
    for line in text.splitlines():
        if "Improved most:" in line:
            improved = line.split("Improved most:")[1].split("Worsened most:")[0]
            assert "(+" not in improved, line


def test_the_profile_refuses_an_unknown_indicator():
    assert tools.profile_indicator.call({"indicator": "nope"}).startswith("Unknown indicator")


# ---- The expert grading -------------------------------------------------------------------------------------

def test_clopper_pearson_matches_the_riapa_paper():
    # 76 of 76 ready gives the 95.3 to 100% interval reported for RIAPA-AI.
    lo, hi = score_rubric.clopper_pearson(76, 76)
    assert round(100 * lo, 1) == 95.3 and hi == 1.0
    lo, hi = score_rubric.clopper_pearson(56, 60)
    assert round(100 * lo, 1) == 83.8 and round(100 * hi, 1) == 98.2


def test_mcnemar_exact_matches_the_riapa_paper():
    # 4 discordant pairs, all one way: p = 0.125.
    assert score_rubric.mcnemar_exact(4, 0) == 0.125 and score_rubric.mcnemar_exact(0, 0) == 1.0


def test_the_sheet_is_blind_and_the_scores_come_back(tmp_path):
    rows = [{"id": f"q{i}", "topic": "t", "question": "?", "agent_answer": f"agent {i}", "baseline_answer": f"plain {i}"}
            for i in range(6)]
    fs, fk = run_evals.write_sheet(rows, tmp_path, "x")
    sheet = pd.read_csv(fs, encoding="utf-8-sig", dtype=str)
    assert "system" not in sheet.columns and len(sheet) == 12
    key = pd.read_csv(fk, dtype=str).set_index("item")["system"]
    sheet["correctness"] = [("1" if key[i] == "agent" else "0") for i in sheet["item"]]
    sheet["depth"] = [("1" if key[i] == "agent" else "0,5") for i in sheet["item"]]
    sheet["objectivity"] = "1"
    sheet.to_csv(fs, index=False, encoding="utf-8-sig")
    out = score_rubric.score(score_rubric.read(fs, fk)).set_index(["dimension", "system"])
    assert out.loc[("correctness", "agent"), "ready_pct"] == 100 and out.loc[("correctness", "baseline"), "ready_pct"] == 0
    assert out.loc[("correctness", "agent vs baseline"), "mcnemar_p"] == round(2 * 0.5 ** 6, 4)
    assert out.loc[("objectivity", "agent vs baseline"), "wilcoxon_p"] == 1.0


def test_a_score_outside_the_scale_is_refused(tmp_path):
    fs, fk = run_evals.write_sheet([{"id": "q1", "topic": "t", "question": "?", "agent_answer": "a"}], tmp_path, "y")
    sheet = pd.read_csv(fs, encoding="utf-8-sig", dtype=str).assign(correctness="0.7")
    sheet.to_csv(fs, index=False, encoding="utf-8-sig")
    with pytest.raises(ValueError, match="1, 0.5, 0"):
        score_rubric.read(fs, fk)


def test_every_eval_question_has_a_topic():
    q = pd.read_csv(run_evals.HERE / "questions.csv", dtype=str).fillna("")
    assert (q["topic"] != "").all() and q["id"].is_unique
    assert {"objectivity", "depth"} <= set(q["topic"])
