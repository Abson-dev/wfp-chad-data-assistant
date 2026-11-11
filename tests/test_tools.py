"""The read-only tools: the analysis, the merging process, and the command line. Run on the invented dashboard
(demo/make_demo_data.py) unless WFP_TEST_REAL_DATA=1, so the tests below only use what both have in common."""
import pandas as pd

from wfp_agent import tools
from wfp_cli import main as cli

N_ROUNDS = 10

# ---- Rounds and indicators ----------------------------------------------------------------------------------

def test_rounds_accept_every_way_of_naming_them():
    assert tools._round_ids("R10, 10, r1, 2023 Annual, 2020 pdm") == ["R10", "R10", "R01", "R10", "R03"]
    assert tools._round_ids("") == []


def test_list_rounds_gives_both_series(rows):
    t, _ = rows(tools.list_rounds)
    assert len(t) == N_ROUNDS and set(t["series"]) == {"Post-harvest", "Lean season"}
    assert list(t.columns) == ["round_id", "round_no", "round", "year", "survey", "series", "final_rows", "raw_rows", "duplicates_removed",
                               "empty_removed", "date_p01", "date_p99", "regions", "departments", "raw_file"]


def test_list_indicators_filters_by_domain():
    assert "fcs_pb" in tools.list_indicators.call({"domain": "fcs"})
    assert "fcs_pb" not in tools.list_indicators.call({"domain": "rcsi"})
    assert "No domain matches" in tools.list_indicators.call({"domain": "zzz"})


def test_indicator_gives_both_figures_and_the_flag(rows):
    # Works on the invented data (2023 Annual flagged and corrected) and on the real data (fixed in v9).
    t, _ = rows(tools.get_indicator, indicator="lcs_em")
    assert t["flagged"].any() and t.loc[t["flagged"], "flag_reason"].map(lambda s: isinstance(s, str)).all()
    differ = (t["as_merged"] - t["with_corrections"]).abs() > 1e-9
    assert t.loc[differ, "flagged"].all()
    one, _ = rows(tools.get_indicator, indicator="lcs_em", rounds="2023 Annual")
    assert len(one) == 1
    ok, _ = rows(tools.get_indicator, indicator="fcs_pb", rounds="R06")
    assert not ok["flagged"].iloc[0] and ok["as_merged"].iloc[0] == ok["with_corrections"].iloc[0]


def test_indicator_errors_and_region_names():
    assert "Unknown indicator" in tools.get_indicator.call({"indicator": "nope"})
    assert "national or region" in tools.get_indicator.call({"indicator": "fcs_pb", "breakdown": "department"})
    assert "Region names" in tools.get_indicator.call({"indicator": "fcs_pb", "breakdown": "region", "groups": "Atlantis"})


def test_region_filter_ignores_accents(rows):
    t, _ = rows(tools.get_indicator, indicator="fcs_pb", breakdown="region", rounds="R08", groups="guera, Ouaddai")
    assert len(t) == 2 and t["as_merged"].notna().all()


def test_small_regions_are_suppressed(rows):
    t, _ = rows(tools.get_indicator, indicator="fcs_pb", breakdown="region")
    small = t[t["households"] < 30]
    assert len(small) and small["as_merged"].isna().all() and small["suppressed"].all()
    assert not t.loc[t["households"] >= 30, "suppressed"].any()


def test_compare_rounds_warns_across_series(rows):
    _, same = rows(tools.compare_rounds, indicator="fcs_pb", round_a="R08", round_b="2023 Annual")
    assert "Different series" not in same
    _, cross = rows(tools.compare_rounds, indicator="fcs_pb", round_a="R09", round_b="R10")
    assert "Different series" in cross
    assert "exactly one round" in tools.compare_rounds.call({"indicator": "fcs_pb", "round_a": "", "round_b": "R10"})
    assert "with_corrections" in tools.compare_rounds.call({"indicator": "fcs_pb", "round_a": "R08", "round_b": "R10", "figures": "x"})


def test_compare_rounds_can_use_the_corrected_figures(rows):
    t, _ = rows(tools.compare_rounds, indicator="lcs_em", round_a="R08", round_b="R10", figures="with_corrections")
    ind = pd.read_csv(tools.DATA / "indicators.csv")
    want = ind.query("indicator == 'lcs_em' and breakdown == 'national' and round_id == 'R10'")["with_corrections"].iloc[0]
    assert abs(t["value_b"].iloc[0] - want) < 1e-9


def test_a_module_not_asked_says_so():
    assert "not asked" in tools.compare_rounds.call({"indicator": "shk_any12m", "round_a": "R01", "round_b": "R10"})
    assert "not asked" in tools.rank_regions.call({"indicator": "shk_any12m", "round_id": "R01"})


def test_rank_regions_gap_to_all_surveyed(rows):
    t, text = rows(tools.rank_regions, indicator="fcs_pb", round_id="R10")
    assert t["value"].is_monotonic_decreasing and t["value"].notna().all()
    assert (t["value"] - t["all_surveyed"] - t["gap"]).abs().max() < 1e-9


def test_relate_indicators_reports_correlation(rows):
    t, text = rows(tools.relate_indicators, indicator_x="fcs_pb", indicator_y="rcsi_mean", round_id="R10")
    assert f"Pearson r = {t['fcs_pb'].corr(t['rcsi_mean']):.2f}" in text and len(t) >= 5


def test_relate_refuses_when_it_cannot_relate():
    assert "too few" in tools.relate_indicators.call({"indicator_x": "shk_any12m", "indicator_y": "fcs_pb", "round_id": "R01"})
    assert "two different" in tools.relate_indicators.call({"indicator_x": "fcs_pb", "indicator_y": "fcs_pb", "round_id": "R10"})


# ---- Assistance and costs -----------------------------------------------------------------------------------

def test_assistance_lists_then_reports(rows):
    lst, _ = rows(tools.get_assistance)
    var = lst["variable"].iloc[0]
    t, _ = rows(tools.get_assistance, variable=var.lower(), round_id="R10")
    assert set(t["round_id"]) == {"R10"} and "yes_pct" in t.columns
    reg, _ = rows(tools.get_assistance, variable=var, breakdown="region")
    assert "region" in reg.columns
    assert "Unknown intervention" in tools.get_assistance.call({"variable": "nope"})


def test_costs_tables(rows):
    for table in ("reconciliation", "category", "area", "survey_link"):
        t, _ = rows(tools.get_costs, table=table)
        assert len(t) and any(c.endswith("_xaf") for c in t.columns)
    t, _ = rows(tools.get_costs, table="area", year="2022")
    assert set(t["year"].astype(str)) == {"2022"}
    assert "table must be" in tools.get_costs.call({"table": "nope"})
    assert "No cost rows" in tools.get_costs.call({"table": "category", "year": "1999"})


def test_household_costs_every_view(rows):
    for view in tools.HOUSEHOLD_COST_VIEWS:
        t, text = rows(tools.get_household_costs, view=view)
        assert len(t) and "XAF" in text
    assert "view must be one of" in tools.get_household_costs.call({"view": "nope"})


def test_household_costs_by_round_add_up(rows):
    t, _ = rows(tools.get_household_costs, view="by_round")
    assert (t["source_village"] + t["source_area"] + t["source_none"] + t["source_unknown"] == t["households"]).all()
    assert (t["with_total"] == t["households"] - t["source_unknown"]).all()
    assert "R01" not in set(t["round_id"])  # no cost before the programme years


def test_household_costs_filters(rows):
    t, _ = rows(tools.get_household_costs, view="items", round_id="R10", keyword="LEAN")
    assert set(t["round_id"]) == {"R10"} and t["label"].str.lower().str.contains("lean").all()
    assert "No rows" in tools.get_household_costs.call({"view": "items", "keyword": "zzz-none"})


def test_household_cost_cells_under_30_are_hidden(rows):
    items, _ = rows(tools.get_household_costs, view="items")
    assert items.loc[items["recipients"] < 30, "mean_if_recipient"].isna().all()
    region, _ = rows(tools.get_household_costs, view="region")
    assert region.loc[region["households"] < 30, "total_mean"].isna().all()


def test_programme_costs_include_partners(rows):
    t, _ = rows(tools.get_costs, table="partner")
    assert {"partner", "cash_xaf"} <= set(t.columns)


# ---- Merging process ----------------------------------------------------------------------------------------

def test_trace_gives_every_round_and_the_agreement(rows):
    t, text = rows(tools.trace_variable, variable="rcsiborrow")
    assert len(t) == N_ROUNDS and "in the merged file" in text
    assert t["source"].notna().all() and t["pct_same"].notna().all()


def test_trace_marks_rounds_where_a_module_was_not_asked(rows):
    t, _ = rows(tools.trace_variable, variable="Shk12m_Drought")
    assert t["source"].isna().any() and t["source"].notna().any()
    assert t[t["source"].isna()]["pct_same"].isna().all()


def test_trace_one_round_and_unknown_names(rows):
    t, _ = rows(tools.trace_variable, variable="HHHSex", round_id="R03")
    assert list(t["round_id"]) == ["R03"]
    assert "search_variables" in tools.trace_variable.call({"variable": "zzznope"})


def test_trace_an_input_that_is_not_kept(rows):
    name = tools._table("crosswalk.csv").query("in_final == False")["variable"].iloc[0]
    _, text = rows(tools.trace_variable, variable=name)
    assert "not kept in the merged file" in text


def test_describe_categorical_numeric_and_hidden(rows):
    cat, _ = rows(tools.describe_variable, variable="HHHSex", round_id="R10")
    assert {"value", "label", "pct", "pct_answered"} <= set(cat.columns) and set(cat["round_id"]) == {"R10"}
    num, _ = rows(tools.describe_variable, variable="FCS")
    assert {"mean", "median", "pct_answered"} <= set(num.columns)
    assert "identifying column" in tools.describe_variable.call({"variable": "ID"})
    assert "No column" in tools.describe_variable.call({"variable": "nope"})


def test_search_ignores_accents_and_case(rows):
    a, _ = rows(tools.search_variables, term="SÉCHERESSE")
    b, _ = rows(tools.search_variables, term="secheresse")
    assert len(a) == len(b) > 0
    assert "No variable matches" in tools.search_variables.call({"term": "zzz-no-such"})


def test_search_falls_back_to_the_workbook(rows):
    name = tools._table("crosswalk.csv").query("in_final == False")["variable"].iloc[0]
    t, _ = rows(tools.search_variables, term=name)
    assert "rounds_mapped" in t.columns or name in set(t["variable"])


def test_merge_status_overall_and_by_module(rows):
    t, text = rows(tools.get_merge_status)
    assert t["merge_order"].is_monotonic_increasing and "households" in text and "Nutrition" in text
    assert (t["using_only"] == 0).all() and t["stage"].notna().all()
    s, _ = rows(tools.get_merge_status, module="shocks")
    assert len(s) == N_ROUNDS
    asked = s["pct_answering_best_column"]
    assert (asked == 0).any() and (asked > 0).any()
    assert "Unknown module" in tools.get_merge_status.call({"module": "zzz"})


def test_validation_checks(rows):
    # The real data may have no failing check left: test a status that exists.
    status = "fail" if tools.get_validation_checks.call({"status": "fail"}).startswith("result_id:") else "warn"
    t, text = rows(tools.get_validation_checks, status=status)
    assert len(t) and set(t["status"]) == {status} and "All checks" in text
    assert "Groups:" in tools.get_validation_checks.call({"group": "zzz"})


def test_data_issues(rows):
    t, _ = rows(tools.search_data_issues, keyword="rcsiborrow")
    assert len(t) >= 1
    o, _ = rows(tools.search_data_issues, status="open")
    assert set(o["status"]) == {"Open"}
    assert "No documented issue" in tools.search_data_issues.call({"keyword": "zzzz-no-match"})


def test_synthetic_results_say_so():
    if tools.build_info().get("synthetic"):
        assert "SYNTHETIC" in tools.list_rounds.call({})


# ---- Command line -------------------------------------------------------------------------------------------

def test_cli_lists_tools_without_publishing(capsys):
    assert cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "trace_variable" in out and "publish_result" not in out


def test_cli_runs_a_tool_and_reports_errors(capsys):
    assert cli(["trace_variable", "variable=HHHSex"]) == 0
    assert "HHHSex" in capsys.readouterr().out
    assert cli(["nope"]) == 2
    assert cli(["trace_variable", "HHHSex"]) == 2
