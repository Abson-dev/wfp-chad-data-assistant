"""Build the assistant's tables from the dashboard of the WFP Chad merging repository.

The dashboard's prepare_app_data.R is the only step that reads household files; it writes aggregates and
metadata only (regional cells under 30 households suppressed). This script reshapes those tables for the
tools, adds the issue list, the module list and the indicator catalogue, and refuses any household-level column.

From this folder, in the wfp-agent (or ensa-agent) environment:
  python build_agent_data.py                       # reads ../WFP_Chad_Cost_Benefit_Analysis_data_merging/app
  python build_agent_data.py --app path/to/app     # another copy of the dashboard folder
  python build_agent_data.py --demo                # the dashboard's synthetic demo_data/ instead of data/

Writes data/ (or WFP_AGENT_DATA).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

AGENT = Path(__file__).resolve().parent
DEFAULT_APP = AGENT.parent / "WFP_Chad_Cost_Benefit_Analysis_data_merging" / "app"
OUT = Path(os.environ.get("WFP_AGENT_DATA", AGENT / "data"))

# Columns that would identify a household or a place below the department. None may leave the build.
FORBIDDEN = {"ID", "hh_id", "hh_key", "uuid", "village", "Longitude", "Latitude", "SvyDate", "Nom", "nom", "telephone"}

# Copied as they are (aggregates and metadata of the dashboard).
COPY = {
    "crosswalk_long.csv": "crosswalk.csv",
    "merged_columns.csv": "variables.csv",
    "merged_column_coverage.csv": "variable_coverage.csv",
    "variable_values.csv": "variable_values.csv",
    "variable_numeric.csv": "variable_numeric.csv",
    "checks.csv": "checks.csv",
    "flags.csv": "flags.csv",
    "merge_steps.csv": "merge_steps.csv",
    "module_by_round.csv": "module_by_round.csv",
    "source_agreement_variable.csv": "source_agreement.csv",
    "original_vs_merged_variables.csv": "original_vs_merged.csv",
    "original_vs_merged_categories.csv": "original_vs_merged_codes.csv",
    "original_vs_merged_indicators.csv": "original_vs_merged_scores.csv",
    "assistance_by_round.csv": "assistance_by_round.csv",
    "assistance_by_region.csv": "assistance_by_region.csv",
    "cost_reconciliation.csv": "cost_reconciliation.csv",
    "cost_by_category.csv": "cost_by_category.csv",
    "cost_by_area.csv": "cost_by_area.csv",
    "cost_survey_link.csv": "cost_survey_link.csv",
    "cost_by_partner.csv": "cost_by_partner.csv",
    # Costs per household (do-file 21 of the merging repository), as the dashboard aggregates them.
    "cost_household_by_round.csv": "cost_household_by_round.csv",
    "cost_household_items.csv": "cost_household_items.csv",
    "cost_household_assets.csv": "cost_household_assets.csv",
    "cost_household_region.csv": "cost_household_region.csv",
    "cost_ledger.csv": "cost_ledger.csv",
    "cost_template_vs_revised.csv": "cost_template_vs_revised.csv",
    "nutrition_rows.csv": "nutrition_rows.csv",
    "ch_phase_by_round.csv": "ch_phase_by_round.csv",
}
# Rules of the cost process, from the merging repository's costs/ folder (parameters, no household data).
COST_RULES = {"cost_variables.csv": "cost_variables.csv", "household_unit_costs.csv": "cost_unit_costs.csv", "exchange_rates.csv": "cost_exchange_rates.csv",
              "department_crosswalk.csv": "cost_department_crosswalk.csv"}
ROUND_COLS = ["round_id", "round_no", "round", "year", "survey", "series", "final_rows", "raw_rows", "duplicates_removed", "empty_removed",
              "date_p01", "date_p99", "regions", "departments", "raw_file"]


def export_catalogue(app: Path, dest: Path) -> None:
    """The indicator catalogue lives in the dashboard's R/utils.R (IND). Export it with Rscript."""
    code = ("suppressMessages(source('R/utils.R')); "
            f"utils::write.csv(IND, '{dest.as_posix()}', row.names = FALSE, fileEncoding = 'UTF-8')")
    subprocess.run(["Rscript", "-e", code], cwd=app, check=True, capture_output=True)


def indicators_long(app_data: Path, catalogue: pd.DataFrame, rounds: pd.DataFrame, flags: pd.DataFrame) -> pd.DataFrame:
    """One row per round x geography x indicator: the value as merged, the value with the dashboard's
    indicative corrections, and whether the cell is flagged for a known defect."""
    nat = pd.read_csv(app_data / "indicators_by_round.csv").assign(breakdown="national", group="All surveyed households")
    reg = pd.read_csv(app_data / "indicators_by_round_region.csv").rename(columns={"region": "group"}).assign(breakdown="region")
    rows = []
    for ind in catalogue["id"]:
        for df in (nat, reg):
            m, c = f"{ind}_m", f"{ind}_c"
            if m not in df.columns:
                continue
            part = df[["round_id", "breakdown", "group", "households", m, c]].rename(columns={m: "as_merged", c: "with_corrections"})
            part.insert(3, "indicator", ind)
            rows.append(part)
    out = pd.concat(rows, ignore_index=True)
    out = out[~((out["breakdown"] == "national") & out["as_merged"].isna() & out["with_corrections"].isna())]
    group_of = dict(zip(catalogue["id"], catalogue["group"], strict=True))
    fl = flags.assign(key=flags["indicator_group"].astype(str) + "|" + flags["round_id"].astype(str))
    reason = fl.groupby("key")["reason"].agg(" ".join).to_dict()
    fixed = fl.groupby("key")["fixed"].agg(lambda s: bool(s.astype(str).str.upper().eq("TRUE").all())).to_dict()
    key = out["indicator"].map(group_of).astype(str) + "|" + out["round_id"]
    out["flagged"] = key.isin(reason.keys())
    out["flag_reason"] = key.map(reason)
    out["fixed_by_correction"] = key.map(fixed)
    out["suppressed"] = (out["breakdown"] == "region") & out["as_merged"].isna() & (out["households"] < 30)
    out = out.merge(rounds[["round_id", "round", "series"]], on="round_id", how="left")
    cols = ["round_id", "round", "series", "breakdown", "group", "indicator", "as_merged", "with_corrections", "households",
            "flagged", "flag_reason", "fixed_by_correction", "suppressed"]
    return out[cols]


def boundary_check(folder: Path) -> None:
    for f in folder.glob("*.csv"):
        bad = set(pd.read_csv(f, nrows=0).columns) & FORBIDDEN
        if bad:
            raise SystemExit(f"Household-level column {sorted(bad)} in {f.name}: refusing to build.")


def build(app: Path, out: Path, demo: bool = False, catalogue_csv: Path | None = None, costs_dir: Path | None = None) -> dict:
    """app: the dashboard's app/ folder (data/ or demo_data/, config/, R/utils.R). catalogue_csv skips Rscript.
    costs_dir: the merging repository's costs/ folder (default: next to app/)."""
    costs_dir = costs_dir or app.parent / "costs"
    app_data = app / ("demo_data" if demo else "data")
    if not app_data.exists():
        raise SystemExit(f"{app_data} not found. Run the dashboard's prepare_app_data.R first.")
    tmp = Path(tempfile.mkdtemp(prefix="wfp_build_"))
    try:
        for name, new in COPY.items():
            src = app_data / name
            if not src.exists():
                raise SystemExit(f"{src} not found: the dashboard data are older than this assistant. Rerun prepare_app_data.R.")
            shutil.copy(src, tmp / new)
        rounds = pd.read_csv(app_data / "rounds.csv").rename(columns={"round": "round_no"}).rename(columns={"label": "round"})
        rounds[ROUND_COLS].to_csv(tmp / "rounds.csv", index=False)
        catalogue_path = tmp / "indicator_catalog.csv"
        if catalogue_csv:
            shutil.copy(catalogue_csv, catalogue_path)
        else:
            export_catalogue(app, catalogue_path)
        catalogue = pd.read_csv(catalogue_path)
        indicators_long(app_data, catalogue, rounds, pd.read_csv(app_data / "flags.csv")).to_csv(tmp / "indicators.csv", index=False)
        for cfg in ("issues.csv", "modules.csv", "interventions.csv"):
            shutil.copy(app / "config" / cfg, tmp / cfg)
        for name, new in COST_RULES.items():
            if not (costs_dir / name).exists():
                raise SystemExit(f"{costs_dir / name} not found: the merging repository is older than this assistant.")
            shutil.copy(costs_dir / name, tmp / new)
        manifest = json.loads((app_data / "manifest.json").read_text(encoding="utf-8"))
        keep = ("merged_file", "households", "columns", "min_cell", "mapped_variables", "mapped_variables_extension", "modules_2025", "modules_2026")
        info = {"built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "synthetic": bool(demo),
                "dashboard_run": manifest.get("run_utc"), **{k: manifest.get(k) for k in keep}}
        (tmp / "build_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        boundary_check(tmp)
        # Empty the folder rather than delete it: OneDrive and open editors can lock a folder, not its files.
        out.mkdir(parents=True, exist_ok=True)
        for f in out.iterdir():
            shutil.rmtree(f) if f.is_dir() else f.unlink()
        shutil.copytree(tmp, out, dirs_exist_ok=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return info


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Build the assistant's tables from the dashboard's aggregates.")
    p.add_argument("--app", type=Path, default=Path(os.environ.get("WFP_DASHBOARD_APP", DEFAULT_APP)))
    p.add_argument("--demo", action="store_true", help="use the dashboard's synthetic demo_data/")
    a = p.parse_args()
    info = build(a.app, OUT, a.demo)
    print(f"Built {OUT} from {a.app} ({'synthetic' if a.demo else 'real'}): {info['households']} households, {info['columns']} columns")
    for f in sorted(OUT.glob("*.csv")):
        print(f"  {f.name:28s} {len(pd.read_csv(f)):6d} rows")
