"""Build the assistant's tables from the dashboard of the WFP Chad merging repository.

The dashboard's prepare_app_data.R is the only step that reads household files; it writes aggregates and
metadata only (regional cells under 30 households suppressed). This script reshapes those tables for the
tools, adds the issue list, the module list and the indicator catalogue, and refuses any household-level column.

From this folder, in the wfp-agent (or ensa-agent) environment:
  python build_agent_data.py                       # reads ../WFP_Chad_Cost_Benefit_Analysis_data_merging/app
  python build_agent_data.py --app path/to/app     # another copy of the dashboard folder
  python build_agent_data.py --demo                # the dashboard's synthetic demo_data/ instead of data/
  python build_agent_data.py --documents path      # another folder of WFP documents (PDF or text)

It also extracts the text of WFP's published documents (resilience evaluations, country plans, the Sahel
programme's cost-benefit analyses), by default from ../documents/WFP resilience documents, with pdftotext
(poppler; Git for Windows ships it). Public reports only: no household data. Without the folder, the
assistant has no documents and says so.

Writes data/ (or WFP_AGENT_DATA).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

AGENT = Path(__file__).resolve().parent
DEFAULT_APP = AGENT.parent / "WFP_Chad_Cost_Benefit_Analysis_data_merging" / "app"
DEFAULT_DOCS = AGENT.parent / "documents" / "WFP resilience documents"
OUT = Path(os.environ.get("WFP_AGENT_DATA", AGENT / "data"))
# Where Git for Windows puts poppler's pdftotext, when it is not on the PATH.
PDFTOTEXT_PLACES = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Git" / "ucrt64" / "bin" / "pdftotext.exe",
                    Path("C:/Program Files/Git/ucrt64/bin/pdftotext.exe"), Path("C:/Program Files/Git/mingw64/bin/pdftotext.exe")]

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
    "original_vs_merged_lost.csv": "original_vs_merged_missing.csv",
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
    "nutrition_indicators.csv": "nutrition_indicators.csv",
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


def _pdftotext() -> str | None:
    found = os.environ.get("WFP_PDFTOTEXT") or shutil.which("pdftotext")
    return found or next((str(p) for p in PDFTOTEXT_PLACES if p.exists()), None)


def _document_text(path: Path, tool: str | None) -> str:
    """The text of a PDF (pages separated by form feeds, as pdftotext writes them), or of a .txt file in that form."""
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8")
    if tool is None:
        raise SystemExit("pdftotext not found: install poppler (Git for Windows has it), set WFP_PDFTOTEXT, or build with --no-documents.")
    run = subprocess.run([tool, "-enc", "UTF-8", str(path), "-"], check=True, capture_output=True)
    return run.stdout.decode("utf-8", errors="replace")


def extract_documents(folder: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One row per document (id, title, topic, pages, file) and one per page with text. The topic is the subfolder,
    the title the file name; a WFP document number in the name, e.g. "(WFP-0000164253)", becomes the id."""
    tool = _pdftotext()
    docs, pages = [], []
    for path in sorted(p for p in folder.rglob("*") if p.suffix.lower() in (".pdf", ".txt")):
        number = re.search(r"\((WFP-\d+)\)\s*$", path.stem)
        title = re.sub(r"\s*\(WFP-\d+\)\s*$", "", path.stem).strip()
        doc_id = number.group(1) if number else re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
        texts = _document_text(path, tool).split("\f")
        if texts and not texts[-1].strip():
            texts = texts[:-1]
        topic = re.sub(r"^\d+\s+", "", path.parent.name) if path.parent != folder else ""
        docs.append({"doc_id": doc_id, "title": title, "topic": topic, "pages": len(texts), "file": path.relative_to(folder).as_posix()})
        for no, text in enumerate(texts, start=1):
            text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()
            if text:
                pages.append({"doc_id": doc_id, "page": no, "text": text})
    index = pd.DataFrame(docs, columns=["doc_id", "title", "topic", "pages", "file"])
    if index["doc_id"].duplicated().any():
        raise SystemExit(f"Two documents share an id: {sorted(index.loc[index['doc_id'].duplicated(), 'doc_id'])}. Rename one file.")
    return index, pd.DataFrame(pages, columns=["doc_id", "page", "text"])


def boundary_check(folder: Path) -> None:
    for f in folder.glob("*.csv"):
        bad = set(pd.read_csv(f, nrows=0).columns) & FORBIDDEN
        if bad:
            raise SystemExit(f"Household-level column {sorted(bad)} in {f.name}: refusing to build.")


def build(app: Path, out: Path, demo: bool = False, catalogue_csv: Path | None = None, costs_dir: Path | None = None,
          documents: Path | None = None) -> dict:
    """app: the dashboard's app/ folder (data/ or demo_data/, config/, R/utils.R). catalogue_csv skips Rscript.
    costs_dir: the merging repository's costs/ folder (default: next to app/). documents: a folder of WFP documents
    (PDF, or text with form feeds between pages); None or a missing folder builds no documents."""
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
        doc_count = 0
        if documents is not None and documents.is_dir():
            index, pages = extract_documents(documents)
            index.to_csv(tmp / "documents.csv", index=False)
            pages.to_csv(tmp / "document_pages.csv", index=False)
            doc_count = len(index)
        manifest = json.loads((app_data / "manifest.json").read_text(encoding="utf-8"))
        keep = ("merged_file", "households", "columns", "min_cell", "mapped_variables", "mapped_variables_extension", "modules_2025", "modules_2026")
        info = {"built_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "synthetic": bool(demo),
                "dashboard_run": manifest.get("run_utc"), **{k: manifest.get(k) for k in keep}, "documents": doc_count}
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
    p.add_argument("--documents", type=Path, default=Path(os.environ.get("WFP_DOCUMENTS", DEFAULT_DOCS)),
                   help="folder of WFP documents to search (PDF or text)")
    p.add_argument("--no-documents", action="store_true", help="build without the WFP documents")
    a = p.parse_args()
    docs = None if a.no_documents else a.documents
    if docs is not None and not docs.is_dir():
        print(f"No documents folder at {docs}: the assistant will have no WFP documents.")
    info = build(a.app, OUT, a.demo, documents=docs)
    print(f"Built {OUT} from {a.app} ({'synthetic' if a.demo else 'real'}): {info['households']} households, {info['columns']} columns, "
          f"{info['documents']} WFP documents")
    for f in sorted(OUT.glob("*.csv")):
        print(f"  {f.name:28s} {len(pd.read_csv(f)):6d} rows")
