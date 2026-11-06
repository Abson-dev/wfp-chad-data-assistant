"""The build step: it reshapes the dashboard's aggregates and refuses any household-level column."""
import os
import shutil
from pathlib import Path

import pandas as pd
import pytest

import build_agent_data as b
from wfp_agent import tools

app_dir = os.environ.get("WFP_TEST_APP")
demo_only = pytest.mark.skipif(not app_dir, reason="needs the invented dashboard")


def dashboard(name):
    return pd.read_csv(Path(app_dir) / "demo_data" / name)


@demo_only
def test_national_figures_are_the_dashboard_figures():
    src = dashboard("indicators_by_round.csv").set_index("round_id")
    ind = pd.read_csv(tools.DATA / "indicators.csv").query("breakdown == 'national' and indicator == 'fcs_pb'").set_index("round_id")
    assert (ind["as_merged"] - src["fcs_pb_m"].reindex(ind.index)).abs().max() < 1e-9
    assert (ind["with_corrections"] - src["fcs_pb_c"].reindex(ind.index)).abs().max() < 1e-9
    assert (ind["households"] == src["households"].reindex(ind.index)).all()


@demo_only
def test_flags_follow_the_indicator_group():
    ind = pd.read_csv(tools.DATA / "indicators.csv")
    flags = dashboard("flags.csv")
    cat = pd.read_csv(tools.DATA / "indicator_catalog.csv")
    lcs = set(cat.loc[cat["group"] == "lcs", "id"])
    flagged = ind[ind["flagged"]]
    assert set(flagged["indicator"]) - set(cat.loc[cat["group"].isin(flags["indicator_group"]), "id"]) == set()
    r10 = flagged[(flagged["round_id"] == "R10") & flagged["indicator"].isin(lcs)]
    assert len(r10) and r10["fixed_by_correction"].all()
    unfixed = flagged[(flagged["round_id"] == "R02") & flagged["indicator"].isin(lcs)]
    assert len(unfixed) and not unfixed["fixed_by_correction"].any()


@demo_only
def test_a_module_not_asked_has_no_national_row():
    ind = pd.read_csv(tools.DATA / "indicators.csv").query("indicator == 'shk_any12m' and breakdown == 'national'")
    assert "R01" not in set(ind["round_id"]) and "R10" in set(ind["round_id"])


@demo_only
def test_a_household_column_stops_the_build(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(app_dir, app)
    f = app / "demo_data" / "checks.csv"
    pd.read_csv(f).assign(Latitude=12.1).to_csv(f, index=False)
    with pytest.raises(SystemExit, match="Household-level column"):
        b.build(app, tmp_path / "out", demo=True, catalogue_csv=app / "indicator_catalog.csv")
    assert not (tmp_path / "out").exists()


@demo_only
def test_a_missing_table_asks_for_a_rerun(tmp_path):
    app = tmp_path / "app"
    shutil.copytree(app_dir, app)
    (app / "demo_data" / "merge_steps.csv").unlink()
    with pytest.raises(SystemExit, match="prepare_app_data.R"):
        b.build(app, tmp_path / "out", demo=True, catalogue_csv=app / "indicator_catalog.csv")


def test_tables_have_no_household_columns():
    for f in tools.DATA.glob("*.csv"):
        cols = set(pd.read_csv(f, nrows=0).columns)
        assert not cols & b.FORBIDDEN, f"{f.name} has household-level columns {cols & b.FORBIDDEN}"


def test_every_table_the_tools_read_is_built():
    for name in [*b.COPY.values(), "rounds.csv", "indicators.csv", "indicator_catalog.csv", "issues.csv", "modules.csv", "interventions.csv"]:
        assert (tools.DATA / name).exists(), name
    assert (tools.DATA / "build_info.json").exists()


def test_every_indicator_is_in_the_catalogue():
    ind = pd.read_csv(tools.DATA / "indicators.csv")
    assert set(ind["indicator"]) <= set(pd.read_csv(tools.DATA / "indicator_catalog.csv")["id"])


def test_regional_cells_under_the_threshold_are_empty():
    ind = pd.read_csv(tools.DATA / "indicators.csv")
    small = ind[(ind["breakdown"] == "region") & (ind["households"] < tools.build_info()["min_cell"])]
    assert small["as_merged"].isna().all() and small["with_corrections"].isna().all()


@demo_only
def test_synthetic_tables_are_labelled():
    assert tools.build_info()["synthetic"] is True
