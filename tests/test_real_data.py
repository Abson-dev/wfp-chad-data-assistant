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


def test_the_known_coping_defect_is_flagged():
    ind = pd.read_csv(tools.DATA / "indicators.csv")
    r = ind.query("indicator == 'lcs_em' and breakdown == 'national' and round_id == 'R10'").iloc[0]
    assert r["flagged"] and r["as_merged"] > 5 * r["with_corrections"]


def test_rounds_and_series():
    r = pd.read_csv(tools.DATA / "rounds.csv")
    assert len(r) == 10 and r["final_rows"].sum() == 17255
    assert set(r.loc[r["survey"] == "PDM", "series"]) == {"Lean season"}
