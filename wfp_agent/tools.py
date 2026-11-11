"""Read-only tools for the WFP Chad data assistant.

Every tool reads the aggregate and metadata tables in data/ (built by build_agent_data.py from the
dashboard of the merging repository). None of them can open a household file, so nothing the model sees
is a household record. Each call is logged in TRACE so the answer can show where its numbers came from,
and each table it returns is kept under a result id that publish_result can show to the reader.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Callable
from functools import cache, wraps
from pathlib import Path

import pandas as pd
from anthropic import beta_tool

from . import results as res

AGENT_DIR = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("WFP_AGENT_DATA", AGENT_DIR / "data"))
LIMITS = json.loads((AGENT_DIR / "limits.json").read_text(encoding="utf-8"))
TRACE: list[dict] = []
# What each tool returned to the model in this question: the sources of the answer's figures (see grounding.py).
OUTPUTS: list[str] = []
RESULTS: dict[str, dict] = {}
PUBLISHED: list[dict] = []
ON_EVENT: list[Callable[[dict], None]] = []
MAX_ROWS = LIMITS["max_rows_to_model"]
FIGURES = ("as_merged", "with_corrections")
# Links back to the dashboard: the page that holds the figures of each tool (#page=... opens it, even after the login).
DASHBOARD_URL = os.environ.get("WFP_DASHBOARD_URL", "https://abson-dev.shinyapps.io/wfp-chad-data-merging/")
DASHBOARD_PAGES = {
    "list_indicators": ("data", "Data"), "list_rounds": ("rounds", "Rounds"), "get_indicator": ("trends", "Trends"),
    "compare_rounds": ("trends", "Trends"), "profile_indicator": ("trends", "Trends"),
    "rank_regions": ("regions", "Regions"), "relate_indicators": ("regions", "Regions"),
    "get_assistance": ("assistance", "Assistance"), "get_costs": ("costs", "Costs"), "get_household_costs": ("costs", "Costs"),
    "trace_variable": ("crosswalk", "Crosswalk"), "describe_variable": ("variables", "Variables"), "search_variables": ("variables", "Variables"),
    "get_merge_status": ("merge", "Merge"), "get_validation_checks": ("validation", "Validation"), "search_data_issues": ("issues", "Data issues"),
}


def dashboard_link(tool: str) -> dict | None:
    """The dashboard page that shows what a tool returned, or None when there is no dashboard address."""
    if tool not in DASHBOARD_PAGES or not DASHBOARD_URL.startswith(("https://", "http://")):
        return None
    slug, page = DASHBOARD_PAGES[tool]
    return {"page": page, "url": f"{DASHBOARD_URL.split('#')[0]}#page={slug}"}


def begin_question(on_event: Callable[[dict], None] | None = None) -> None:
    """Forget the tool calls and results of the previous question. The agent runs one question at a time."""
    TRACE.clear()
    OUTPUTS.clear()
    RESULTS.clear()
    PUBLISHED.clear()
    ON_EVENT[:] = [on_event] if on_event else []


def _keep(f: Callable[..., str]) -> Callable[..., str]:
    """Record what a tool returns, so the answer's figures can be checked against it."""
    @wraps(f)
    def run(*args, **kwargs) -> str:
        out = f(*args, **kwargs)
        OUTPUTS.append(out)
        return out
    return run


@cache
def _table(name: str) -> pd.DataFrame:
    path = DATA / name
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Build it first: python build_agent_data.py")
    return pd.read_csv(path, dtype={"round_id": str}, keep_default_na=True)


def _log(tool: str, **args) -> dict:
    call = {"tool": tool, "args": {k: v for k, v in args.items() if v not in ("", None)}}
    TRACE.append(call)
    for f in ON_EVENT:
        f({"type": "tool", "n": len(TRACE), **call})
    return call


def _csv(df: pd.DataFrame) -> str:
    if len(df) > MAX_ROWS:
        return f"{len(df)} rows: too many, narrow the request (rounds, groups, family or keyword)."
    return df.to_csv(index=False, float_format="%.2f")


def _result(df: pd.DataFrame, call: dict, title: str, head: str = "") -> str:
    """Keep a table for publish_result and return it to the model with its result id."""
    text = _csv(df)
    if len(df) > MAX_ROWS:
        return text
    rid = f"R{len(RESULTS) + 1}"
    RESULTS[rid] = {"id": rid, "rows": df.reset_index(drop=True), "title": title, "tool": call["tool"], "args": call["args"]}
    demo = "# SYNTHETIC DEMO DATA: every value is invented. Say so in your answer.\n" if _is_demo() else ""
    return f"result_id: {rid}\n{demo}{head}{text}"


@cache
def _is_demo() -> bool:
    path = DATA / "build_info.json"
    return path.exists() and bool(json.loads(path.read_text(encoding="utf-8")).get("synthetic"))


def _split(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _plain(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def _round_ids(rounds: str) -> list[str]:
    """Accept 'R10', 'R1', '10' or '2023 Annual' (comma-separated); empty means all rounds."""
    if not rounds.strip():
        return []
    r = _table("rounds.csv")
    lookup = {_plain(lab): rid for lab, rid in zip(r["round"], r["round_id"], strict=True)}
    for rid, no in zip(r["round_id"], r["round_no"], strict=True):
        lookup.update({rid.lower(): rid, f"r{no}": rid, str(no): rid})
    return [lookup.get(_plain(x), x) for x in _split(rounds)]


def _known_indicator(indicator: str) -> bool:
    return indicator in set(_table("indicator_catalog.csv")["id"])


UNKNOWN = "Unknown indicator '{}'. Call list_indicators to see the valid ids."


def _figures(figures: str) -> str | None:
    f = (figures or "as_merged").strip().lower()
    return f if f in FIGURES else None


# ---- Analysis ------------------------------------------------------------------------------------

@beta_tool
@_keep
def list_indicators(domain: str = "") -> str:
    """List the indicators: id, label, unit, domain, which end is worse, and the flag group (the known-defect
    group whose cells can be flagged). Call this first when you are unsure of an indicator id.

    Args:
        domain: Optional filter on the domain, e.g. FCS, rCSI, LCS, Shocks, Markets (case and accents ignored).
    """
    _log("list_indicators", domain=domain)
    cat = _table("indicator_catalog.csv")
    if domain:
        cat = cat[cat["domain"].map(_plain).str.contains(_plain(domain), regex=False)]
        if cat.empty:
            return f"No domain matches '{domain}'. Domains: {sorted(_table('indicator_catalog.csv')['domain'].unique())}"
    return _csv(cat)


@beta_tool
@_keep
def list_rounds() -> str:
    """List the 10 survey rounds: id (R01 to R10), label, survey type, series (post-harvest or lean season),
    households in the merged file, raw rows, duplicates and refused interviews removed, fieldwork window."""
    call = _log("list_rounds")
    return _result(_table("rounds.csv"), call, "Survey rounds")


@beta_tool
@_keep
def get_indicator(indicator: str, breakdown: str = "national", rounds: str = "", groups: str = "") -> str:
    """Get an indicator by round: the value as merged, the value with the dashboard's indicative corrections,
    households, and whether the cell is flagged for a known defect (with the reason). Unweighted estimates over
    the households surveyed. Empty values: not asked in that round, or a regional cell under 30 households.

    Args:
        indicator: Indicator id from list_indicators, e.g. fcs_pb.
        breakdown: national or region.
        rounds: Optional comma-separated rounds, e.g. "R09,R10" or "2023 PDM, 2023 Annual". Empty = all.
        groups: Optional comma-separated regions, e.g. "Lac, Kanem". Empty = all.
    """
    call = _log("get_indicator", indicator=indicator, breakdown=breakdown, rounds=rounds, groups=groups)
    cat = _table("indicator_catalog.csv").set_index("id")
    if indicator not in cat.index:
        return UNKNOWN.format(indicator)
    if breakdown not in ("national", "region"):
        return "breakdown must be national or region."
    t = _table("indicators.csv")
    t = t[(t["indicator"] == indicator) & (t["breakdown"] == breakdown)]
    if rid := _round_ids(rounds):
        t = t[t["round_id"].isin(rid)]
    if grp := _split(groups):
        t = t[t["group"].map(_plain).isin([_plain(g) for g in grp])]
        if t.empty:
            names = sorted(_table("indicators.csv").query("breakdown == 'region'")["group"].unique())
            return f"No rows for groups {grp}. Region names: {names}"
    info = cat.loc[indicator]
    head = (f"# {info['label']} ({info['unit']}). Unweighted. as_merged = the merged file; with_corrections = the dashboard's "
            "indicative rebuild where a defect is known. flagged = known defect in that round.\n")
    cols = ["round_id", "round", "series", "group", "as_merged", "with_corrections", "households", "flagged", "flag_reason", "suppressed"]
    return _result(t[cols], call, f"{info['label']} ({info['unit']})", head)


@beta_tool
@_keep
def profile_indicator(indicator: str, figures: str = "as_merged") -> str:
    """The full profile of one indicator, computed here so that you need no arithmetic: its value for all surveyed
    households in every round, the change since the previous round of the same series (lean season or post-harvest),
    households and flags; then, for the last two comparable rounds of each series, the regions that improved and
    worsened most (the relative winners and losers, read with the indicator's worse end) and how many regions were
    compared. Use it for "how did X evolve", "where did it improve" or a summary of one indicator.

    Args:
        indicator: Indicator id from list_indicators, e.g. lcs_em.
        figures: as_merged (default) or with_corrections.
    """
    call = _log("profile_indicator", indicator=indicator, figures=figures)
    cat = _table("indicator_catalog.csv").set_index("id")
    if indicator not in cat.index:
        return UNKNOWN.format(indicator)
    col = _figures(figures)
    if col is None:
        return "figures must be as_merged or with_corrections."
    info = cat.loc[indicator]
    t = _table("indicators.csv")
    t = t[t["indicator"] == indicator]
    order = {r: i for i, r in enumerate(_table("rounds.csv")["round_id"])}
    nat = t[(t["breakdown"] == "national")].dropna(subset=[col]).copy()
    if nat.empty:
        return f"No values for {indicator}."
    nat = nat.sort_values("round_id", key=lambda s: s.map(order))
    nat["previous_round"] = nat.groupby("series")["round_id"].shift()
    nat["change_since_previous"] = nat.groupby("series")[col].diff().round(2)
    out = nat[["round_id", "round", "series", col, "previous_round", "change_since_previous", "households", "flagged",
               "flag_reason"]].rename(columns={col: "value"})
    worse = str(info["worse"]).lower()
    lines = [f"# {info['label']} ({info['unit']}), {col}. Unweighted. The worse end is {worse}. A change is a difference between "
             "two samples (no panel, no significance test)."]
    reg = t[t["breakdown"] == "region"].dropna(subset=[col])
    for series, s in nat.groupby("series", sort=False):
        if len(s) < 2:
            lines.append(f"# {series}: one round only ({s['round_id'].iloc[-1]}), no change to report.")
            continue
        a, b = s["round_id"].iloc[-2], s["round_id"].iloc[-1]
        ra, rb = reg[reg["round_id"] == a].set_index("group")[col], reg[reg["round_id"] == b].set_index("group")[col]
        d = (rb - ra).dropna().round(2).sort_values()
        if d.empty:
            lines.append(f"# {series}, {a} -> {b}: no region has a value in both rounds.")
            continue
        def top(x: pd.Series) -> str:
            return ", ".join(f"{g} ({v:+.2f})" for g, v in x.head(3).items()) or "none"

        fell, rose = d[d < 0], d[d > 0][::-1]
        head = f"# {series}, {a} -> {b}, {len(d)} regions in both rounds."
        if worse in ("high", "low"):
            # The winners are the regions that moved towards the better end.
            better, worsen = (fell, rose) if worse == "high" else (rose, fell)
            lines.append(f"{head} Improved most: {top(better)}. Worsened most: {top(worsen)}.")
        else:
            lines.append(f"{head} Rose most: {top(rose)}. Fell most: {top(fell)}.")
    if out["flagged"].any():
        lines.append("# Flagged rounds: " + ", ".join(out.loc[out["flagged"], "round_id"]) + " (see flag_reason).")
    return _result(out, call, f"{info['label']}: profile ({col})", "\n".join(lines) + "\n")


@beta_tool
@_keep
def compare_rounds(indicator: str, round_a: str, round_b: str, breakdown: str = "national", figures: str = "as_merged") -> str:
    """Compare an indicator between two rounds: value in each, change (b - a), households, and flags. Point estimates
    only: no significance test, and the samples differ between rounds (no panel).

    Args:
        indicator: Indicator id from list_indicators.
        round_a: Earlier round, e.g. "R08" or "2022 Annual".
        round_b: Later round, e.g. "R10" or "2023 Annual".
        breakdown: national or region.
        figures: as_merged (default) or with_corrections.
    """
    call = _log("compare_rounds", indicator=indicator, round_a=round_a, round_b=round_b, breakdown=breakdown, figures=figures)
    if not _known_indicator(indicator):
        return UNKNOWN.format(indicator)
    col = _figures(figures)
    if col is None:
        return "figures must be as_merged or with_corrections."
    ra, rb = _round_ids(round_a), _round_ids(round_b)
    if len(ra) != 1 or len(rb) != 1:
        return "Give exactly one round for round_a and one for round_b, e.g. R08 and R10."
    t = _table("indicators.csv")
    t = t[(t["indicator"] == indicator) & (t["breakdown"] == breakdown)]
    x, y = t[t["round_id"] == ra[0]].set_index("group"), t[t["round_id"] == rb[0]].set_index("group")
    if x[col].isna().all() or y[col].isna().all():
        return f"No values for {indicator} in {ra[0] if x[col].isna().all() else rb[0]}: not asked in that round."
    out = pd.DataFrame({"value_a": x[col], "value_b": y[col], "change": y[col] - x[col], "households_a": x["households"],
                        "households_b": y["households"], "flagged_a": x["flagged"], "flagged_b": y["flagged"]})
    out = out.reset_index().sort_values("change", ascending=False)
    series = _table("rounds.csv").set_index("round_id")["series"]
    note = "" if series[ra[0]] == series[rb[0]] else " Different series (lean season vs post-harvest): not comparable as a trend."
    return _result(out, call, f"{indicator}: {ra[0]} to {rb[0]} ({col})", f"# {indicator} ({col}): {ra[0]} -> {rb[0]}.{note}\n")


@beta_tool
@_keep
def rank_regions(indicator: str, round_id: str, figures: str = "as_merged") -> str:
    """Rank the regions on an indicator in one round, highest first, with the gap to all surveyed households.

    Args:
        indicator: Indicator id from list_indicators.
        round_id: One round, e.g. "R10" or "2023 Annual".
        figures: as_merged (default) or with_corrections.
    """
    call = _log("rank_regions", indicator=indicator, round_id=round_id, figures=figures)
    if not _known_indicator(indicator):
        return UNKNOWN.format(indicator)
    col = _figures(figures)
    if col is None:
        return "figures must be as_merged or with_corrections."
    rid = _round_ids(round_id)
    if len(rid) != 1:
        return "Give exactly one round, e.g. R10."
    t = _table("indicators.csv")
    t = t[(t["indicator"] == indicator) & (t["round_id"] == rid[0])]
    nat = t[t["breakdown"] == "national"][col]
    reg = t[t["breakdown"] == "region"].dropna(subset=[col])
    if reg.empty or nat.empty:
        return f"No regional values for {indicator} in {rid[0]}: not asked in that round."
    out = reg[["group", col, "households", "flagged"]].rename(columns={"group": "region", col: "value"}).copy()
    out["all_surveyed"] = float(nat.iloc[0])
    out["gap"] = out["value"] - out["all_surveyed"]
    suppressed = int(t[(t["breakdown"] == "region") & t["suppressed"]].shape[0])
    head = f"# {suppressed} region(s) suppressed (under 30 households).\n" if suppressed else ""
    return _result(out.sort_values("value", ascending=False), call, f"{indicator} by region, {rid[0]}", head)


@beta_tool
@_keep
def relate_indicators(indicator_x: str, indicator_y: str, round_id: str) -> str:
    """Relate two indicators across the regions of one round (as merged): both values per region, with Pearson and
    Spearman correlations. A descriptive association between regions, not causation, not a household result.

    Args:
        indicator_x: First indicator id.
        indicator_y: Second indicator id.
        round_id: One round, e.g. "R10".
    """
    call = _log("relate_indicators", indicator_x=indicator_x, indicator_y=indicator_y, round_id=round_id)
    for i in (indicator_x, indicator_y):
        if not _known_indicator(i):
            return UNKNOWN.format(i)
    if indicator_x == indicator_y:
        return "Give two different indicators."
    rid = _round_ids(round_id)
    if len(rid) != 1:
        return "Give exactly one round, e.g. R10."
    t = _table("indicators.csv")
    t = t[(t["breakdown"] == "region") & (t["round_id"] == rid[0])]

    def values_of(i: str) -> pd.DataFrame:
        return t[t["indicator"] == i][["group", "as_merged"]].rename(columns={"as_merged": i})

    out = values_of(indicator_x).merge(values_of(indicator_y), on="group").dropna().rename(columns={"group": "region"})
    if len(out) < 5:
        return f"Only {len(out)} regions have both indicators in {rid[0]}: too few to relate."
    pearson = out[indicator_x].corr(out[indicator_y])
    spearman = out[indicator_x].rank().corr(out[indicator_y].rank())
    head = f"# {len(out)} regions, {rid[0]}. Pearson r = {pearson:.2f}, Spearman rho = {spearman:.2f}.\n"
    return _result(out, call, f"{indicator_x} and {indicator_y} by region, {rid[0]}", head)


@beta_tool
@_keep
def get_assistance(variable: str = "", round_id: str = "", breakdown: str = "national") -> str:
    """WFP interventions reported by households: share answering yes, by round (national) or by region and round.
    Without a variable: the list of the 20 interventions with their labels.

    Args:
        variable: Intervention variable, e.g. CashTransfert, ArgentContreTravail. Empty = list them.
        round_id: Optional round, e.g. "R10".
        breakdown: national or region.
    """
    call = _log("get_assistance", variable=variable, round_id=round_id, breakdown=breakdown)
    if not variable:
        return _result(_table("interventions.csv"), call, "WFP interventions in the merged file")
    t = _table("assistance_by_round.csv" if breakdown == "national" else "assistance_by_region.csv")
    t = t[t["variable"].str.lower() == variable.strip().lower()]
    if t.empty:
        return f"Unknown intervention '{variable}'. Call get_assistance with no variable for the list."
    if rid := _round_ids(round_id):
        t = t[t["round_id"].isin(rid)]
    return _result(t, call, f"Households reporting {variable}")


@beta_tool
@_keep
def get_costs(table: str = "reconciliation", year: str = "") -> str:
    """WFP's resilience costs (XAF) in WFP's revised workbook, as reconciled in the merging repository: programme-level
    tables, not per household (for that, use get_household_costs). Tables: reconciliation (stated against reconciled
    totals by period), category (by activity category and assistance type), area (by cost area and region), partner
    (by implementing partner and year), survey_link (costs linked to the surveyed households by department and year).

    Args:
        table: reconciliation, category, area, partner or survey_link.
        year: Optional year or period filter, e.g. "2022".
    """
    call = _log("get_costs", table=table, year=year)
    names = {"reconciliation": "cost_reconciliation.csv", "category": "cost_by_category.csv", "area": "cost_by_area.csv",
             "partner": "cost_by_partner.csv", "survey_link": "cost_survey_link.csv"}
    if table not in names:
        return f"table must be one of {', '.join(names)}."
    t = _table(names[table])
    if year:
        col = "year" if "year" in t.columns else "period"
        t = t[t[col].astype(str).str.contains(year.strip(), regex=False)]
    return _result(t, call, f"Costs: {table}{' ' + year if year else ''}") if len(t) else f"No cost rows for '{year}'."


HOUSEHOLD_COST_VIEWS = {
    "by_round": ("cost_household_by_round.csv", "Cost per household by round: households, community cost source (village, cost area, none, "
                 "unknown), mean transfer, capacity, intervention and total cost (XAF, over households with a cost), median total"),
    "items": ("cost_household_items.csv", "Cost by intervention and round: households asked, recipients, mean cost per recipient (XAF; "
              "hidden under 30 recipients), total"),
    "assets": ("cost_household_assets.csv", "Community cost by asset type and round: mean per household with a community cost (XAF)"),
    "region": ("cost_household_region.csv", "Cost per household by region and round: mean and median total, community and intervention "
               "means (XAF; hidden under 30 households)"),
    "ledger": ("cost_ledger.csv", "Ledger of IFPRI's community template: where every franc goes, by period and destination"),
    "template_vs_revised": ("cost_template_vs_revised.csv", "Community template (transfer, capacity) against WFP's revised workbook, by period"),
    "unit_costs": ("cost_unit_costs.csv", "Unit cost, currency, unit rule and months of each household-level intervention, with its status"),
    "exchange_rates": ("cost_exchange_rates.csv", "XAF per USD by year (World Bank, official rate, annual average)"),
    "variables": ("cost_variables.csv", "Dictionary of the 44 cost variables of the merged file: group, unit, source and rule"),
}


@beta_tool
@_keep
def get_household_costs(view: str = "by_round", round_id: str = "", keyword: str = "") -> str:
    """The cost of WFP's assistance per household: the 44 cost variables added to the merged file by do-file 21, as the
    dashboard aggregates them, and the rules that built them. Views:
    by_round (means and sources of the community cost), items (by intervention), assets (community cost by asset type),
    region (by region), ledger (where every franc of the community template goes), template_vs_revised (the two cost
    files compared), unit_costs (unit costs and rules), exchange_rates, variables (dictionary of the cost variables).

    Args:
        view: One of by_round, items, assets, region, ledger, template_vs_revised, unit_costs, exchange_rates, variables.
        round_id: Optional round, e.g. "R10" (by_round, items, assets, region).
        keyword: Optional word to filter rows, e.g. "lean", "school", "water", "capacity" (accents ignored).
    """
    call = _log("get_household_costs", view=view, round_id=round_id, keyword=keyword)
    if view not in HOUSEHOLD_COST_VIEWS:
        return f"view must be one of {', '.join(HOUSEHOLD_COST_VIEWS)}."
    name, title = HOUSEHOLD_COST_VIEWS[view]
    t = _table(name)
    if round_id and "round_id" in t.columns:
        rid = _round_ids(round_id)
        t = t[t["round_id"].isin(rid)]
    if keyword:
        want = _plain(keyword)
        t = t[t.apply(lambda r: want in _plain(" ".join(r.astype(str))), axis=1)]
    if t.empty:
        return f"No rows in {view} for round '{round_id}' and keyword '{keyword}'."
    if "round_id" in t.columns:
        t = _table("rounds.csv")[["round_id", "round"]].merge(t, on="round_id")
    head = ("# Costs per household come from IFPRI's community and household templates and WFP's cost data (merging repository, "
            "costs/). XAF. Unweighted. See the cost rules in your instructions.\n")
    return _result(t, call, title, head)


# ---- Merging process -----------------------------------------------------------------------------

@beta_tool
@_keep
def trace_variable(variable: str, round_id: str = "") -> str:
    """Trace one harmonised variable from the raw SPSS files to the merged file, round by round: the source column,
    its type, coverage, range, question label and value labels, and how often the merged value equals the raw one
    (pct_same; recoded variables differ on purpose). Also its label and group in the merged file.

    Args:
        variable: Harmonised variable name, e.g. HHHSex, FCSStap, rCSIBorrow, Shk12m_Drought.
        round_id: Optional round, e.g. "R10". Empty = all rounds.
    """
    call = _log("trace_variable", variable=variable, round_id=round_id)
    cw = _table("crosswalk.csv")
    cw = cw[cw["variable"].str.lower() == variable.strip().lower()]
    if cw.empty:
        names = sorted(set(_table("crosswalk.csv")["variable"]) | set(_table("variables.csv")["variable"]))
        near = [n for n in names if _plain(variable)[:4] in _plain(n)][:12]
        return f"No mapping for '{variable}'. Similar names: {near}. Use search_variables for a concept."
    if rid := _round_ids(round_id):
        cw = cw[cw["round_id"].isin(rid)]
    ag = _table("source_agreement.csv")
    ag = ag[ag["variable"].str.lower() == variable.strip().lower()][["round_id", "compared", "pct_same"]]
    out = cw[["round_id", "source", "type", "coverage", "range", "label", "value_labels"]].merge(ag, on="round_id", how="left")
    out = _table("rounds.csv")[["round_id", "round"]].merge(out, on="round_id")
    meta = _table("variables.csv")
    m = meta[meta["variable"].str.lower() == variable.strip().lower()]
    head = (f"# {variable}: " + (f"in the merged file as '{m['label'].iloc[0]}' ({m['family'].iloc[0]}). " if len(m) else
            "not kept in the merged file (an input to another variable). ")
            + "Empty source = not asked in that round. coverage = % of raw rows answered.\n")
    return _result(out, call, f"How {variable} was merged", head)


@beta_tool
@_keep
def describe_variable(variable: str, round_id: str = "") -> str:
    """The distribution of a column of the merged file by round: answer counts and shares for a categorical variable
    (counts under 5 households are hidden), or mean, 10th/50th/90th percentiles, min and max for a numeric one;
    plus the share of households answering in each round.

    Args:
        variable: Column of the merged file, e.g. HHHSex, FCS, LvsTLU, MigMainReason.
        round_id: Optional round, e.g. "R10".
    """
    call = _log("describe_variable", variable=variable, round_id=round_id)
    meta = _table("variables.csv")
    m = meta[meta["variable"].str.lower() == variable.strip().lower()]
    if m.empty:
        return f"No column '{variable}' in the merged file. Use search_variables."
    name, kind = m["variable"].iloc[0], m["kind"].iloc[0]
    rid = _round_ids(round_id)
    if kind == "hidden":
        return f"{name} is an identifying column (ID, village, GPS, dates): only its completeness is available."
    if kind == "categorical":
        t = _table("variable_values.csv")
        t = t[t["variable"] == name][["round_id", "value", "label", "households", "answered", "pct", "suppressed"]]
    else:
        t = _table("variable_numeric.csv")
        t = t[t["variable"] == name][["round_id", "answered", "mean", "p10", "median", "p90", "min", "max"]]
    if rid:
        t = t[t["round_id"].isin(rid)]
    cov = _table("variable_coverage.csv")
    cov = cov[cov["variable"] == name][["round_id", "pct"]].rename(columns={"pct": "pct_answered"})
    t = t.merge(cov, on="round_id", how="left")
    head = f"# {name}: {m['label'].iloc[0]} ({m['family'].iloc[0]}, {kind}). Value labels: {m['value_labels'].iloc[0]}\n"
    return _result(t, call, f"{name} by round", head)


@beta_tool
@_keep
def search_variables(term: str, family: str = "") -> str:
    """Search the variables by concept, name or label (accents ignored): the columns of the merged file (with their
    group and rounds with data) and the harmonisation workbooks' variables (with how many rounds are mapped).

    Args:
        term: Word or name, e.g. "drought", "bétail", "HHSize", "complaint".
        family: Optional group, e.g. "Shocks", "Food consumption (FCS)".
    """
    call = _log("search_variables", term=term, family=family)
    v = _table("variables.csv")[["variable", "family", "label", "rounds_with_data", "coverage"]]
    want = _plain(term)
    hit = v.apply(lambda r: want in _plain(f"{r['variable']} {r['label']}"), axis=1)
    v = v[hit]
    if family:
        v = v[v["family"].map(_plain).str.contains(_plain(family), regex=False)]
    if v.empty:
        cw = _table("crosswalk.csv")
        cw = cw[cw.apply(lambda r: want in _plain(f"{r['variable']} {r['label']}"), axis=1)]
        if cw.empty:
            return f"No variable matches '{term}'."
        out = cw.groupby(["variable", "family"], as_index=False).agg(rounds_mapped=("source", lambda s: int(s.notna().sum())),
                                                                       label=("label", "first"), in_final=("in_final", "first"))
        return _result(out.head(LIMITS["max_search_rows"]), call, f"Mapped variables matching '{term}'")
    return _result(v.head(LIMITS["max_search_rows"]), call, f"Columns matching '{term}'")


@beta_tool
@_keep
def get_merge_status(module: str = "") -> str:
    """How the modules were merged 1:1 on household ID. Without a module: each merge step with rows, matches, households
    in the master only or the module only, columns added, and its stage (2025 first harmonisation or 2026 extension).
    With a module: the households it reaches in each round.

    Args:
        module: Optional module name or label, e.g. FCS, Shocks, Markets.
    """
    call = _log("get_merge_status", module=module)
    steps = _table("merge_steps.csv").merge(_table("modules.csv")[["module", "stage"]], on="module", how="left")
    if not module:
        cols = ["merge_order", "module", "label", "stage", "using_rows", "using_duplicates", "matched", "master_only", "using_only",
                "columns_added", "rmd"]
        info = build_info()
        head = (f"# Merged file {info.get('merged_file')}: {info.get('households')} households, {info.get('columns')} columns. "
                "Nutrition (MAD, MDD-W) is not merged: see search_data_issues('MAD').\n")
        return _result(steps[cols].sort_values("merge_order"), call, "Merge steps", head)
    want = _plain(module)
    m = steps[(steps["module"].map(_plain) == want) | steps["label"].map(_plain).str.contains(want, regex=False)]
    if m.empty:
        return f"Unknown module '{module}'. Modules: {steps['module'].tolist()}"
    mr = _table("module_by_round.csv")
    out = mr[mr["module"] == m["module"].iloc[0]].merge(_table("rounds.csv")[["round_id", "round"]], on="round_id")
    # A module file has a row for every household; whether the round asked its questions shows in its columns' coverage.
    label = m["label"].iloc[0]
    fam = _table("variables.csv")
    cols = fam[fam["family"].map(_plain) == _plain(label)]["variable"]
    cov = _table("variable_coverage.csv")
    cov = cov[cov["variable"].isin(cols)].groupby("round_id", as_index=False)["pct"].max().rename(columns={"pct": "pct_answering_best_column"})
    out = out.merge(cov, on="round_id", how="left")
    head = (f"# {label}: {m['description'].iloc[0]} households = rows in the module file; pct_answering_best_column = share of households "
            "answering its best-covered column (0 = not asked in that round; empty = no column of this group).\n")
    keep = ["round_id", "round", "households", "round_total", "pct", "pct_answering_best_column"]
    return _result(out[keep], call, f"Households reached: {label}", head)


@beta_tool
@_keep
def get_validation_checks(group: str = "", status: str = "") -> str:
    """The dashboard's validation checks, recomputed from the files: status (pass, warn, fail), what was observed and
    expected, and the detail. Groups: Rows, Merge, Workbook, Formulas, Plausibility, Assistance, Documentation, New modules.

    Args:
        group: Optional group.
        status: Optional: pass, warn or fail.
    """
    call = _log("get_validation_checks", group=group, status=status)
    t = _table("checks.csv")
    if group:
        t = t[t["group"].map(_plain) == _plain(group)]
    if status:
        t = t[t["status"] == status.strip().lower()]
    if t.empty:
        return "No check matches. Groups: " + ", ".join(sorted(_table("checks.csv")["group"].unique()))
    counts = _table("checks.csv")["status"].value_counts().to_dict()
    return _result(t, call, "Validation checks", f"# All checks: {counts}.\n")


@beta_tool
@_keep
def search_data_issues(keyword: str = "", status: str = "", severity: str = "") -> str:
    """Documented issues of the source data and the merge: severity, status (Open, Handled, Documented), rounds
    affected, what was found, how it is handled and where in the code.

    Args:
        keyword: Optional word, e.g. "coping", "FCS", "2023 Annual", "nutrition". Empty = all issues.
        status: Optional: Open, Handled or Documented.
        severity: Optional: High, Medium or Low.
    """
    call = _log("search_data_issues", keyword=keyword, status=status, severity=severity)
    t = _table("issues.csv")
    if keyword:
        want = _plain(keyword)
        t = t[t.apply(lambda r: want in _plain(" ".join(r.astype(str))), axis=1)]
    if status:
        t = t[t["status"].str.lower() == status.strip().lower()]
    if severity:
        t = t[t["severity"].str.lower() == severity.strip().lower()]
    return _result(t, call, f"Data issues{': ' + keyword if keyword else ''}") if len(t) else f"No documented issue matches '{keyword}'."


# ---- Publishing ----------------------------------------------------------------------------------

@beta_tool
@_keep
def publish_result(result_id: str, chart: str = "table", x: str = "", y: str = "", series: str = "", title: str = "",
                   note: str = "") -> str:
    """Show a table returned by another tool to the reader, with an optional chart and a CSV download. Call it once,
    last, for the table that best supports your answer. You choose columns and chart type; the server checks them and
    refuses a chart the data cannot support.

    Choosing: a trend over rounds -> line, x="round", y="as_merged", series="series" (never join lean season to
    post-harvest); regions in one round -> bar; two indicators -> scatter; region x round -> heatmap, x="round",
    y="group", series="as_merged". Merge tables, rules, checks and issues -> table.

    Args:
        result_id: The result_id printed by the tool, e.g. R2.
        chart: table, line, bar, scatter or heatmap.
        x: Column for the x axis.
        y: Column for the y axis (numeric, except for a heatmap or a bar's category).
        series: Column that splits lines or colours; for a heatmap, the numeric column of the cells.
        title: Short title stating the finding or the subject.
        note: Optional one-line caveat shown under the chart, e.g. which rounds are flagged.
    """
    _log("publish_result", result_id=result_id, chart=chart, x=x, y=y, series=series)
    stored = RESULTS.get(result_id.strip())
    if stored is None:
        return f"Unknown result_id '{result_id}'. Available: {list(RESULTS) or 'none yet'}."
    round_order = _table("rounds.csv")["round"].tolist()
    try:
        published = res.build_publication(stored, chart, x, y, series, title, note, round_order, LIMITS)
    except res.PublishError as e:
        return f"Not published: {e} Columns: {list(stored['rows'].columns)}."
    PUBLISHED[:] = [published]
    return f"Published {result_id} ({published['row_count']} rows, chart: {published['chart']['type'] if published['chart'] else 'none'})."


def build_info() -> dict:
    return json.loads((DATA / "build_info.json").read_text(encoding="utf-8"))


TOOLS = [list_indicators, list_rounds, get_indicator, profile_indicator, compare_rounds, rank_regions, relate_indicators,
         get_assistance, get_costs, get_household_costs,
         trace_variable, describe_variable, search_variables, get_merge_status, get_validation_checks, search_data_issues,
         publish_result]
