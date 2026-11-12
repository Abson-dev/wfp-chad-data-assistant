"""Build the assistant's tables from an invented dashboard, so anyone can try it and the tests run without WFP data.

    python demo/make_demo_data.py            # writes demo_data/
    WFP_AGENT_DATA=demo_data python wfp_cli.py get_merge_status

It writes a dashboard app folder (demo_data/*.csv, config/, the indicator catalogue) in the shape that the merging
repository's prepare_app_data.R produces, then runs the real build_agent_data.py on it. Only the names of the
rounds, the regions, the modules and a few variables are real. Every value, count, cost and agreement figure is
invented from a fixed seed, and the tools and the web app label the result as synthetic.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import build_agent_data  # noqa: E402

SEED = 20261106
# round_id, label, year, survey, series
ROUNDS = [("R01", "2018 Baseline", 2018, "Baseline", "Post-harvest"), ("R02", "2019 Annual", 2019, "Annual survey", "Post-harvest"),
          ("R03", "2020 PDM", 2020, "PDM", "Lean season"), ("R04", "2020 Annual", 2020, "Annual survey", "Post-harvest"),
          ("R05", "2021 PDM", 2021, "PDM", "Lean season"), ("R06", "2021 Annual", 2021, "Annual survey", "Post-harvest"),
          ("R07", "2022 PDM", 2022, "PDM", "Lean season"), ("R08", "2022 Annual", 2022, "Annual survey", "Post-harvest"),
          ("R09", "2023 PDM", 2023, "PDM", "Lean season"), ("R10", "2023 Annual", 2023, "Annual survey", "Post-harvest")]
RIDS = [r[0] for r in ROUNDS]
# The last region always has 20 households, under the 30-household threshold: its cells are suppressed.
REGIONS = ["Barh-El-Gazel", "Batha", "Guéra", "Kanem", "Lac", "Ouaddaï", "Ennedi Est"]
SMALL = "Ennedi Est"
SHOCK_ROUNDS = RIDS[5:]  # the shock module is asked from the 2021 Annual on

# id, label, domain, unit, worse, flag group, typical range
CATALOGUE = [
    ("fcs_pb", "Poor + borderline food consumption (28/42)", "FCS", "%", "high", "fcs", (20, 60)),
    ("fcs_poor", "Poor food consumption (28/42)", "FCS", "%", "high", "fcs", (5, 25)),
    ("fcs_mean", "Mean food consumption score", "FCS", "score", "low", "fcs", (35, 60)),
    ("rcsi_mean", "Mean reduced coping strategies index", "rCSI", "score", "high", "rcsi", (4, 14)),
    ("rcsi_p3", "rCSI phase 3 or above (19+)", "rCSI", "%", "high", "rcsi", (5, 30)),
    ("lcs_em", "Emergency livelihood coping", "LCS", "%", "high", "lcs", (2, 15)),
    ("lcs_ce", "Crisis or emergency livelihood coping", "LCS", "%", "high", "lcs", (10, 40)),
    ("abi_mean", "Mean asset benefit indicator score", "Resilience", "score", "low", "abi", (3, 8)),
    ("shk_any12m", "Any shock in the last 12 months", "Shocks", "%", "high", "shocks", (40, 90)),
]
FLAGS = [("lcs", "R01", "Raw code 0 is recoded to Yes.", True), ("lcs", "R03", "Raw code 0 is recoded to Yes.", True),
         ("lcs", "R10", "The answer scale is not recoded: code 2 (No need) counts as coping.", True),
         ("lcs", "R02", "The codes have no value labels. Check against the questionnaire.", False),
         ("abi", "R08", "The recode turns every answer into 0, so the score is 0 for all households.", True)]

# module, label, rmd, stage, description
MODULES = [("admin", "Administrative", "01_WFP_Chad_Admin.Rmd", 2025, "Interview date, survey, region and department."),
           ("household", "Household", "02_WFP_Chad_Household.Rmd", 2025, "Head of household and household size."),
           ("fcs", "Food consumption (FCS)", "04_WFP_Chad_FCS.Rmd", 2025, "The food consumption score and its groups."),
           ("rcsi", "rCSI", "05_WFP_Chad_rCSI.Rmd", 2025, "The reduced coping strategies index."),
           ("shocks", "Shocks", "16_WFP_Chad_Shocks.Rmd", 2026, "Shocks in the last 12 months."),
           ("markets", "Markets", "31_WFP_Chad_Markets.Rmd", 2026, "Market access and prices.")]
MARKET_ROUNDS = RIDS[6:]

# variable, family, label, kind, value labels, rounds asked, source column
VARIABLES = [
    ("ID", "Identification", "_id", "hidden", "", RIDS, "_id"),
    ("HHHSex", "Household", "Sexe du chef de ménage", "categorical", "1 = Male; 2 = Female", RIDS, "HHHSex"),
    ("HHSize", "Household", "Taille du ménage", "numeric", "", RIDS, "HHSize"),
    ("FCS", "Food consumption (FCS)", "Score de consommation alimentaire", "numeric", "", RIDS, "FCS"),
    ("rCSIBorrow", "rCSI", "Emprunter de la nourriture", "numeric", "", RIDS, "rCSIBorrow"),
    ("Shk12m_Drought", "Shocks", "Sécheresse au cours des 12 derniers mois", "categorical", "0 = No; 1 = Yes", SHOCK_ROUNDS,
     "Choc_secheresse"),
    ("MktAccess", "Markets", "Accès au marché", "categorical", "0 = No; 1 = Yes", MARKET_ROUNDS, "acces_marche"),
]
# A workbook variable that is an input to another one and is not kept in the merged file.
INPUT_ONLY = ("FCSStap_days", "Food consumption (FCS)", "Céréales et tubercules (jours)")
INTERVENTIONS = [("ArgentContreTravail", "Cash for assets", "Asset creation"), ("CashTransfert", "Unconditional cash transfer", "Transfers"),
                 ("Cantine", "School meals", "Education")]


def households(rng) -> pd.DataFrame:
    rows = [(rid, reg, 20 if reg == SMALL else int(rng.integers(60, 400))) for rid in RIDS for reg in REGIONS]
    return pd.DataFrame(rows, columns=["round_id", "region", "households"])


def indicator_tables(rng, hh: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    reg = hh.copy()
    for ind, *_, group, (lo, hi) in CATALOGUE:
        m = rng.uniform(lo, hi, len(reg)).round(2)
        if ind == "fcs_pb":
            m = np.maximum(m, reg["fcs_poor_m"].to_numpy() + 5) if "fcs_poor_m" in reg else m
        if ind == "shk_any12m":
            m = np.where(reg["round_id"].isin(SHOCK_ROUNDS), m, np.nan)
        if ind == "abi_mean":
            m = np.where(reg["round_id"] == "R08", 0.0, m)
        c = m.copy()
        for g, rid, _, fixed in FLAGS:
            if g == group and fixed:
                sel = (reg["round_id"] == rid).to_numpy()
                fix = np.full(len(m), 5.8) if g == "abi" else m / 8 if rid == "R10" else m * 0.5
                c = np.where(sel, fix.round(2), c)
        reg[f"{ind}_m"], reg[f"{ind}_c"] = m, c
    cols = [c for c in reg.columns if c.endswith(("_m", "_c"))]

    def national(d: pd.DataFrame) -> pd.Series:
        w = d["households"]
        out = {"households": int(w.sum())}
        for c in cols:
            ok = d[c].notna()
            out[c] = round(float(np.average(d.loc[ok, c], weights=w[ok])), 2) if ok.any() else np.nan
        return pd.Series(out)

    nat = reg.groupby("round_id").apply(national, include_groups=False).reset_index()
    nat["households"] = nat["households"].astype(int)
    reg.loc[reg["region"] == SMALL, cols] = np.nan
    return nat, reg


def uni(rng, lo: float, hi: float) -> int:
    return round(float(rng.uniform(lo, hi)))


def write_costs(app: Path, d: Path, rng, per_round: pd.Series, hh: pd.DataFrame) -> None:
    """Costs per household (do-file 21) as the dashboard aggregates them, and the rules of the costs/ folder."""

    def w(df: pd.DataFrame, name: str, folder: Path = d) -> None:
        df.to_csv(folder / name, index=False, encoding="utf-8")

    partners = [(p, y, str(y), uni(rng, 1e7, 2e8), 6) for y in range(2019, 2024) for p in ("ALPHA", "BETA")]
    w(pd.DataFrame(partners, columns=["partner", "year", "period", "cash_xaf", "lines"]), "cost_by_partner.csv")

    rounds = RIDS[1:]  # no cost before the programme years
    by_round = []
    for rid in rounds:
        n = int(per_round[rid])
        unknown, village, area = int(n * 0.1), int(n * 0.6), int(n * 0.2)
        tr, ca, iv = uni(rng, 2e4, 9e4), uni(rng, 3e4, 1.2e5), uni(rng, 1e5, 3e5)
        by_round.append((rid, n, n - unknown, village, area, n - unknown - village - area, unknown, int(n * 0.7),
                         20 if rid >= "R06" else 0, tr, ca, iv, tr + ca + iv, round((tr + ca + iv) * 0.9), 46000))
    w(pd.DataFrame(by_round, columns=["round_id", "households", "with_total", "source_village", "source_area", "source_none", "source_unknown",
                                      "participants", "repeat_flag", "transfer_mean", "capacity_mean", "interv_mean", "total_mean",
                                      "total_median", "transfer_participant_median"]), "cost_household_by_round.csv")

    items = [("cost_comm_transfer_xaf", "Community transfer cost per household, XAF", RIDS[1:]),
             ("cost_lean_food_xaf", "Lean-season food distribution", RIDS[5:]),
             ("cost_lean_combined_xaf", "Lean-season food or cash (combined item)", RIDS[1:5]),
             ("cost_school_meals_xaf", "School feeding", RIDS[1:])]
    rows = []
    for v, lab, asked in items:
        for rid in asked:
            n = int(per_round[rid])
            rec = int(n * rng.uniform(0.01, 0.4))
            rows.append((rid, n, rec, uni(rng, 2e4, 4e5) if rec >= 30 else None, uni(rng, 1e6, 1e8), v, lab, 0 < rec < 30))
    w(pd.DataFrame(rows, columns=["round_id", "asked", "recipients", "mean_if_recipient", "total_xaf", "variable", "label", "suppressed"]),
      "cost_household_items.csv")

    assets = [("cost_asset_water_xaf", "Water: wells, ponds, dams, irrigation"),
              ("cost_asset_land_xaf", "Land recovery: dikes, half-moons, anti-erosion"),
              ("cost_asset_lump2020_xaf", "Partner lump sum (COVID-19), not allocated")]
    rows = [(rid, int(per_round[rid]), int(per_round[rid] * 0.5), 0 if v.endswith("lump2020_xaf") and rid not in ("R03", "R04") else uni(rng, 0, 8e4), v, a)
            for rid in rounds for v, a in assets]
    w(pd.DataFrame(rows, columns=["round_id", "households", "with_cost", "mean_xaf", "variable", "asset"]), "cost_household_assets.csv")

    rows = []
    for rid, reg, k in hh[hh.round_id.isin(rounds)][["round_id", "region", "households"]].itertuples(index=False):
        hidden = k < 30
        vals = [None] * 4 if hidden else [uni(rng, 1e5, 6e5), uni(rng, 1e5, 5e5), uni(rng, 5e4, 2e5), uni(rng, 5e4, 3e5)]
        rows.append((rid, reg, k, *vals, hidden))
    w(pd.DataFrame(rows, columns=["round_id", "region", "households", "total_mean", "total_median", "community_mean", "interv_mean", "suppressed"]),
      "cost_household_region.csv")

    dests = ["1 Surveyed village, same year (reaches households)", "2 Surveyed village, other years only", "3 Village not surveyed",
             "4 No village named (kept at department level)", "5 Department not surveyed"]
    periods = ["2019", "2020 Jan-Mar", "2020 Apr-Dec", "2021", "2022", "2023"]
    rows = [(p, dst, uni(rng, 1e7, 9e8), 0 if dst[0] in "45" else uni(rng, 1e7, 6e8), int(rng.integers(5, 90))) for p in periods for dst in dests]
    w(pd.DataFrame(rows, columns=["period", "destination", "transfer_xaf", "capacity_xaf", "village_shares"]), "cost_ledger.csv")

    rows = []
    for p in periods:
        tr = uni(rng, 3e8, 3e9)
        rv = tr if p in ("2021", "2020 Jan-Mar") else round(tr * float(rng.uniform(0.5, 1.1)))
        rows.append((p, 100, tr, uni(rng, 3e8, 1.5e9), 6, 0, rv, tr - rv))
    w(pd.DataFrame(rows, columns=["period", "lines", "transfer_xaf", "capacity_xaf", "subtotals_checked", "subtotals_skipping_text",
                                  "revised_cash_xaf", "transfer_minus_revised_xaf"]), "cost_template_vs_revised.csv")

    costs = app / "costs"
    costs.mkdir()
    w(pd.DataFrame([("cost_comm_transfer_xaf", "Community cost", "XAF", "Community template", "Participants: transfer cost / beneficiaries"),
                    ("cost_total_xaf", "Totals", "XAF", "All of the above", "Community plus household-level costs")],
                   columns=["variable", "group", "unit", "source", "rule"]), "cost_variables.csv", costs)
    w(pd.DataFrame([("DistribVivresSoudure", "lean_food", "all", 31.75, "USD", "persons_6", 3, "template", "Invented note."),
                    ("BoursesAdo", "bursary", "all", 20000, "XAF", "household", 1, "template", "Invented note.")],
                   columns=["variable", "cost_name", "year", "amount", "currency", "unit_rule", "months", "status", "source_note"]),
      "household_unit_costs.csv", costs)
    w(pd.DataFrame([(y, 600.0, "Invented rate") for y in range(2019, 2024)], columns=["year", "xaf_per_usd", "source"]), "exchange_rates.csv", costs)
    w(pd.DataFrame([("Dept A", "Area A", REGIONS[0], "Dept A", "TD0001", "Same name", "")],
                   columns=["cost_department", "cost_area", "region", "survey_department", "adm2_ocha", "link_type", "note"]),
      "department_crosswalk.csv", costs)


def write_app(app: Path) -> None:
    rng = np.random.default_rng(SEED)
    d, cfg = app / "demo_data", app / "config"
    d.mkdir(parents=True)
    cfg.mkdir()

    def w(df: pd.DataFrame, name: str, folder: Path = d) -> None:
        df.to_csv(folder / name, index=False, encoding="utf-8")

    hh = households(rng)
    per_round = hh.groupby("round_id")["households"].sum()
    rounds = pd.DataFrame([{"round": i + 1, "round_id": rid, "dataset": f"demo_{rid}", "label": lab, "year": yr, "survey": sv, "series": se,
                            "raw_rows": int(per_round[rid]) + (2 if rid == "R05" else 0), "duplicates_removed": 2 if rid == "R05" else 0,
                            "final_rows": int(per_round[rid]), "empty_removed": 0, "date_p01": f"{yr}-{'11' if se == 'Post-harvest' else '06'}-02",
                            "date_p99": f"{yr}-{'12' if se == 'Post-harvest' else '07'}-20", "regions": len(REGIONS), "departments": 2 * len(REGIONS),
                            "raw_file": f"demo_{rid}.sav"} for i, (rid, lab, yr, sv, se) in enumerate(ROUNDS)])
    w(rounds, "rounds.csv")
    total = int(per_round.sum())

    nat, reg = indicator_tables(rng, hh)
    w(nat, "indicators_by_round.csv")
    w(reg, "indicators_by_round_region.csv")
    w(pd.DataFrame(FLAGS, columns=["indicator_group", "round_id", "reason", "fixed"]), "flags.csv")
    pd.DataFrame([(i, lab, lab.split(" (")[0], dom, unit, worse, grp) for i, lab, dom, unit, worse, grp, _ in CATALOGUE],
                 columns=["id", "label", "short", "domain", "unit", "worse", "group"]).to_csv(app / "indicator_catalog.csv", index=False)

    # Variables of the merged file and the harmonisation mapping.
    cw, cov, vals, nums, agree = [], [], [], [], []
    for var, fam, lab, kind, vl, asked, src in VARIABLES:
        for rid in RIDS:
            n = int(per_round[rid])
            on = rid in asked
            cw.append((var, rid, src if on else None, "numeric" if on else None, 100.0 if on else None, "1-9" if on and kind != "hidden" else None,
                       lab if on else None, vl if on else None, fam, True))
            cov.append((var, rid, round(float(rng.uniform(95, 100)), 1) if on else 0.0))
            if not on or kind == "hidden":
                continue
            agree.append((var, fam, rid, n, n if var != "rCSIBorrow" or rid not in ("R07", "R09", "R10") else int(n * 0.4),
                          100.0 if var != "rCSIBorrow" or rid not in ("R07", "R09", "R10") else 40.0))
            if kind == "categorical":
                codes = [(c.split(" = ")[0], c.split(" = ")[1]) for c in vl.split("; ")]
                share = rng.dirichlet(np.ones(len(codes)))
                for (code, clab), s in zip(codes, share, strict=True):
                    k = int(round(s * n))
                    vals.append((var, rid, code, clab, k, n, round(100 * k / n, 1), False))
            else:
                mean = float(rng.uniform(4, 50))
                nums.append((var, rid, n, round(mean, 2), round(mean * 0.5, 1), round(mean, 1), round(mean * 1.6, 1), 0, round(mean * 3, 1)))
    for rid in RIDS:
        cw.append((INPUT_ONLY[0], rid, "jours_cereales", "numeric", 100.0, "0-7", INPUT_ONLY[2], None, INPUT_ONLY[1], False))
    w(pd.DataFrame(cw, columns=["variable", "round_id", "source", "type", "coverage", "range", "label", "value_labels", "family", "in_final"]),
      "crosswalk_long.csv")
    covdf = pd.DataFrame(cov, columns=["variable", "round_id", "pct"])
    w(covdf, "merged_column_coverage.csv")
    w(pd.DataFrame(vals, columns=["variable", "round_id", "value", "label", "households", "answered", "pct", "suppressed"]), "variable_values.csv")
    w(pd.DataFrame(nums, columns=["variable", "round_id", "answered", "mean", "p10", "median", "p90", "min", "max"]), "variable_numeric.csv")
    w(pd.DataFrame(agree, columns=["variable", "family", "round_id", "compared", "same", "pct_same"]), "source_agreement_variable.csv")

    # The original files against the merged file (the dashboard's Original vs merged page).
    src_of = {v: s for v, _, _, _, _, _, s in VARIABLES}
    ovm = []
    for var, fam, rid, n, same, pct in agree:
        m = round(float(rng.uniform(1, 40)), 3)
        differ = pct < 100
        ovm.append({"round_id": rid, "variable": var, "family": fam, "workbook": "First harmonisation", "raw_column": src_of[var],
                    "raw_rows_answered": n, "raw_answered": n, "merged_answered": n, "raw_mean": round(m * (1.3 if differ else 1), 3),
                    "merged_mean": m, "raw_median": round(m), "merged_median": round(m), "raw_min": 0, "merged_min": 0,
                    "raw_max": round(m * 3), "merged_max": round(m * 3), "compared": n, "same_value": same, "pct_same": pct,
                    "status": "Values differ: recoded on purpose, or a defect" if differ else "Identical"})
    w(pd.DataFrame(ovm), "original_vs_merged_variables.csv")
    codes = []
    for var, rid, code, clab, k, _, _, _ in vals:
        try:
            c = float(code)
        except ValueError:
            continue
        codes.append({"round_id": rid, "variable": var, "raw_code": c, "raw_label": clab, "merged_code": c, "merged_label": clab,
                      "households": k if k >= 5 else None, "raw_code_total": k if k >= 5 else None, "merged_code_total": k if k >= 5 else None,
                      "pct_of_raw_code": 100.0 if k >= 5 else None, "same_code": True, "suppressed": k < 5})
    w(pd.DataFrame(codes), "original_vs_merged_categories.csv")
    scores = []
    for rid in RIDS:
        n = int(per_round[rid])
        for ind, unit, lo, hi in (("Mean FCS", "score", 35, 60), ("Poor + borderline FCS (28/42)", "%", 20, 60), ("Mean rCSI", "score", 3, 12)):
            v = round(float(rng.uniform(lo, hi)), 3)
            fixed = rid == "R03" and "FCS" in ind
            raw = round(v * 0.3, 3) if fixed else v
            scores.append({"round_id": rid, "indicator": ind, "unit": unit, "raw_households": n, "raw_value": raw, "merged_households": n,
                           "merged_value": v, "difference": round(v - raw, 3),
                           "note": "Fixed on purpose: the merged FCS takes the analysts' SCA (do-file 15)." if fixed else "Same."})
    w(pd.DataFrame(scores), "original_vs_merged_indicators.csv")
    w(pd.DataFrame([(v, f, lab, len(asked), kind, "text" if kind == "hidden" else "numeric",
                     round(100 * sum(per_round[r] for r in asked) / total, 1), vl) for v, f, lab, kind, vl, asked, _ in VARIABLES],
                   columns=["variable", "family", "label", "rounds_with_data", "kind", "type", "coverage", "value_labels"]), "merged_columns.csv")

    # Modules and their merge.
    mods = pd.DataFrame([(m, f"WFP_Chad_{m}", lab, rmd, i, desc, st) for i, (m, lab, rmd, st, desc) in enumerate(MODULES)],
                        columns=["module", "file", "label", "rmd", "merge_order", "description", "stage"])
    w(mods, "modules.csv", cfg)
    w(pd.DataFrame([(m, total, total, 0, total, 0, 0, int(rng.integers(3, 30)), lab, rmd, i, desc) for i, (m, lab, rmd, _, desc) in enumerate(MODULES)],
                   columns=["module", "master_before", "using_rows", "using_duplicates", "matched", "master_only", "using_only", "columns_added",
                            "label", "rmd", "merge_order", "description"]), "merge_steps.csv")
    w(pd.DataFrame([(m, rid, int(per_round[rid]), int(per_round[rid]), 100) for m, *_ in MODULES for rid in RIDS],
                   columns=["module", "round_id", "households", "round_total", "pct"]), "module_by_round.csv")

    # Checks and issues.
    w(pd.DataFrame([("Rows", "Every household appears once", "pass", f"{total} unique", f"{total} unique", "No duplicate ID after the merge."),
                    ("Merge", "Every module matches the master", "pass", "0 unmatched", "0 unmatched", "All modules merged 1:1."),
                    ("Plausibility", "ABI score is not zero for a whole round", "fail", "2022 Annual", "None", "The recode reads 1 as No."),
                    ("Plausibility", "Household size under 30", "warn", "3 households", "None", "Large households in the 2021 Annual."),
                    ("New modules", "Shock module asked from the 2021 Annual", "pass", "R06-R10", "R06-R10", "")],
                   columns=["group", "check", "status", "observed", "expected", "detail"]), "checks.csv")
    w(pd.DataFrame([("High", "Open", "Livelihood coping scale not recoded", "2023 Annual", "Code 2 (No need) counts as coping.",
                     "Indicative correction in the dashboard.", "Rmd 06"),
                    ("Low", "Handled", "rCSIBorrow pointed to the wrong column", "2022 PDM, 2023 PDM, 2023 Annual",
                     "The workbook mapped rCSIBorrow to another rCSI column.", "Fixed in the workbook.", "Mapping workbook"),
                    ("Medium", "Documented", "Child and women's diet left out", "2021 Annual to 2023 Annual",
                     "The MAD and MDD-W rows do not link to households in four rounds.", "Not merged.", "Rmd 33")],
                   columns=["severity", "status", "title", "rounds", "found", "handling", "where"]), "issues.csv", cfg)

    # Assistance, costs, nutrition rows, Cadre Harmonisé phases.
    w(pd.DataFrame([(v, lab, g, ";".join(str(i) for i in range(2, 11))) for v, lab, g in INTERVENTIONS],
                   columns=["variable", "label", "group", "documented_rounds"]), "interventions.csv", cfg)
    ab, ar = [], []
    for v, *_ in INTERVENTIONS:
        for rid in RIDS:
            n = int(per_round[rid])
            ans = 0 if rid == "R01" else n
            yes = int(ans * rng.uniform(0.2, 0.9))
            ab.append((rid, v, yes, ans - yes, 0, ans, n, round(100 * yes / ans, 1) if ans else None, round(100 * ans / n, 1)))
            if ans:
                ar += [(rid, reg, v, int(k), round(float(rng.uniform(20, 95)), 1))
                       for reg, k in hh[(hh.round_id == rid) & (hh.region != SMALL)][["region", "households"]].itertuples(index=False)]
    w(pd.DataFrame(ab, columns=["round_id", "variable", "yes", "no", "dont_know", "answered", "households", "yes_pct", "answered_pct"]),
      "assistance_by_round.csv")
    w(pd.DataFrame(ar, columns=["round_id", "region", "variable", "answered", "yes_pct"]), "assistance_by_region.csv")
    w(pd.DataFrame([(str(y), "Activity", 50, 50, 1.0e9 + y, 1.1e9 + y, 0.1e9, 1 if y == 2019 else 0) for y in range(2019, 2024)],
                   columns=["period", "cost_basis", "lines", "lines_with_amount", "stated_total_xaf", "reconciled_total_xaf", "difference_xaf",
                            "unlabelled_subtotals"]), "cost_reconciliation.csv")
    w(pd.DataFrame([(str(y), cat, typ, round(float(rng.uniform(1e7, 5e8)), 0), int(rng.integers(2, 20))) for y in range(2019, 2024)
                    for cat, typ in [("Water harvesting", "Asset creation"), ("School meals", "Food")]],
                   columns=["period", "amount_category", "assistance_type", "cash_xaf", "lines"]), "cost_by_category.csv")
    w(pd.DataFrame([(f"{r} Centre", r, "Direct", y, str(y), "Activity", round(float(rng.uniform(1e7, 3e8)), 0), 5, 1)
                    for r in REGIONS[:4] for y in range(2019, 2024)],
                   columns=["cost_area", "region", "link_type", "year", "period", "cost_basis", "cash_xaf", "lines", "partners"]), "cost_by_area.csv")
    w(pd.DataFrame([(f"{r} Centre", r, "Direct", y, "Cost and survey", 1e8, 1e8, 0, 120, 60, 60, 55.0) for r in REGIONS[:4] for y in range(2019, 2024)],
                   columns=["cost_area", "region", "link_type", "year", "status", "cost_xaf", "cost_activity_xaf", "cost_lump_sum_xaf",
                            "households_surveyed", "households_pdm", "households_annual", "pct_cash_for_assets"]), "cost_survey_link.csv")
    w(pd.DataFrame([("MAD", rid, 300, 450) for rid in RIDS[5:]], columns=["module", "round_id", "raw_rows", "module_rows"]), "nutrition_rows.csv")
    w(pd.DataFrame([(rid, p, int(rng.integers(50, 900)), int(rng.integers(1, 8))) for rid in RIDS for p in (1, 2, 3)],
                   columns=["round_id", "phase", "households", "districts"]), "ch_phase_by_round.csv")

    write_costs(app, d, rng, per_round, hh)

    manifest = {"run_utc": "2026-11-06T00:00:00Z", "merged_file": "DEMO_merged_file.dta", "households": total, "columns": 99, "min_cell": 30,
                "mapped_variables": len(VARIABLES), "mapped_variables_extension": 2, "modules_2025": 4, "modules_2026": 2}
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def build(out: Path, keep_app: Path | None = None) -> dict:
    """Write the invented dashboard folder, then build the assistant's tables from it into `out`."""
    app = keep_app or Path(tempfile.mkdtemp(prefix="wfp_demo_app_"))
    try:
        if app.exists() and keep_app:
            shutil.rmtree(app)
        write_app(app)
        return build_agent_data.build(app, out, demo=True, catalogue_csv=app / "indicator_catalog.csv", costs_dir=app / "costs")
    finally:
        if not keep_app:
            shutil.rmtree(app, ignore_errors=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=REPO / "demo_data")
    a = p.parse_args()
    info = build(a.out)
    print(f"Synthetic tables written to {a.out} ({info['households']} invented households).\n"
          f"Try: WFP_AGENT_DATA={a.out.name} python wfp_cli.py get_merge_status")
