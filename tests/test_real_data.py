"""Checks on the real tables, on the maintainer's computer only: WFP_TEST_REAL_DATA=1 python -m pytest."""
import os
from pathlib import Path

import pandas as pd
import pytest

import build_agent_data as b
from wfp_agent import tools

pytestmark = pytest.mark.skipif(os.environ.get("WFP_TEST_REAL_DATA") != "1", reason="needs the real tables")
APP = Path(os.environ.get("WFP_DASHBOARD_APP", b.DEFAULT_APP))


def test_real_tables_are_not_labelled_synthetic():
    info = tools.build_info()
    assert not info.get("synthetic") and info["households"] == 17255


def test_national_figures_match_the_dashboard():
    src = APP / "data" / "indicators_by_round.csv"
    if not src.exists():
        pytest.skip("dashboard data not built")
    d = pd.read_csv(src).set_index("round_id")
    ind = pd.read_csv(tools.DATA / "indicators.csv").query("breakdown == 'national' and indicator == 'fcs_pb'").set_index("round_id")
    assert (ind["as_merged"] - d["fcs_pb_m"].reindex(ind.index)).abs().max() < 1e-9


def test_the_coping_defects_are_fixed_in_v9():
    ind = pd.read_csv(tools.DATA / "indicators.csv")
    em = ind.query("indicator == 'lcs_em' and breakdown == 'national'").set_index("round_id")
    assert (em.loc[["R01", "R03", "R10"], "as_merged"].round(1) == [21.2, 8.5, 5.8]).all()
    assert not em.loc[["R01", "R03", "R10"], "flagged"].any() and em.loc["R02", "flagged"]
    abi = ind.query("indicator == 'abi_mean' and breakdown == 'national' and round_id == 'R08'").iloc[0]
    assert round(abi["as_merged"], 1) == 5.8 and not abi["flagged"]


def test_rounds_and_series():
    r = pd.read_csv(tools.DATA / "rounds.csv")
    assert len(r) == 10 and r["final_rows"].sum() == 17255
    assert set(r.loc[r["survey"] == "PDM", "series"]) == {"Lean season"}


def test_original_against_merged_on_the_real_tables(rows):
    t, text = rows(tools.compare_original)
    fcs = t[(t["round_id"] == "R03") & (t["indicator"] == "Mean FCS")].iloc[0]
    assert "analysts" in fcs["note"] and abs(fcs["merged_value"] - 53.2) < 0.1
    assert not (t["note"] == "Unexplained difference.").any()
    codes, _ = rows(tools.compare_original, variable="HHHSex", round_id="R01")
    recode = codes[codes["raw_code"] == 1].iloc[0]
    assert recode["merged_code"] == 2 and not recode["same_code"]


def test_the_dropped_workbook_variables_are_back_in_v10(rows):
    v = pd.read_csv(tools.DATA / "variables.csv")
    assert len(v) == 769 and {"RESPAge", "RESPSex", "RESPConsent", "SCIEvolRessSociales", "DebutAssistance"} <= set(v["variable"])
    ov = pd.read_csv(tools.DATA / "original_vs_merged.csv")
    assert not (ov["status"] == "Not in the merged file").any()
    t, _ = rows(tools.compare_original, variable="MigrationEmploi")
    assert (t["status"] == "Merged under another name").all()


def test_v11_restores_education_and_documents_every_missing_answer(rows):
    lost = pd.read_csv(tools.DATA / "original_vs_merged_missing.csv")
    assert len(lost) and not lost["reason"].str.startswith("Not documented").any()
    assert not lost["variable"].isin(["HHHEdu", "HHSourceIncome"]).any()
    assert "HHHMainActivityGroup" in set(pd.read_csv(tools.DATA / "variables.csv")["variable"])
    t, text = rows(tools.compare_original, variable="HHHMainActivity")
    assert "HHHMainActivityGroup" in text


def test_the_diet_records_are_linked_once(rows):
    k, _ = rows(tools.get_diet, view="link")
    assert k.groupby("module")["linked_rows"].sum().to_dict() == {"MAD": 2197, "MDDW": 6452}
    assert (k["pct_count_match"] >= 90).all()
    # The single-form 2023 Annual links every record.
    r10 = k[k["round_id"] == "R10"]
    assert (r10["linked_rows"] == r10["raw_rows"]).all()


def test_the_wfp_documents_are_searchable(rows):
    idx = pd.read_csv(tools.DATA / "documents.csv")
    assert len(idx) >= 50 and {"WFP-0000164253", "WFP-0000155096", "WFP-0000165233"} <= set(idx["doc_id"])
    # Every document has text: a scanned PDF without a text layer would need OCR.
    pages = pd.read_csv(tools.DATA / "document_pages.csv")
    assert set(idx["doc_id"]) == set(pages["doc_id"])
    t, _ = rows(tools.search_documents, query="benefit-cost ratio", document="WFP-0000164253")
    assert len(t) and (t["doc_id"] == "WFP-0000164253").all()
    assert "295" in tools.read_document.call({"document": "WFP-0000164253", "pages": ",".join(map(str, t["page"].head(3)))})
    fr, _ = rows(tools.search_documents, query="convergence FFA", document="Chad resilience evaluation")
    assert len(fr)
