# Changelog

## 0.2.1 (2026-11-10)

- **Changed:** the tables follow the merged file v7, whose village matches include the first manual review (69 households take their own village's cost). No tool changed.

## 0.2.0 (2026-11-08)

- **Added:** the costs per household of the merged file v6 (do-file 21 of the merging repository). A new tool, `get_household_costs`, gives the dashboard's aggregates (by round, intervention, asset type and region), the ledger of the community template, the template against WFP's revised workbook, the unit costs, the exchange rates and the dictionary of the 44 cost variables.
- **Added:** `get_costs` gives the costs by partner. The instructions describe the cost process, its decisions and its caveats, and forbid naming villages.
- **Added:** invented cost tables in the demo, tests for every cost view, and four cost questions in the evals.
- **Fixed:** the build empties `data/` instead of deleting it, which OneDrive can refuse.

## 0.1.0 (2026-11-06)

First version, built on the design of the ENSA Chad data assistant. Not yet run with the live model.

- 15 read-only tools over the dashboard aggregates of the WFP Chad merging repository: indicators (as merged and with corrections, with flags), regions, the harmonisation mapping, variable distributions, merge steps, validation checks, data issues, WFP interventions and costs, and checked result publishing.
- `build_agent_data.py` reads the dashboard's aggregates only and refuses household-level columns.
- Web app, command line, and a skill for other assistants.
- An invented dashboard (`demo/`) so the tests and the demo run with no data and no API key; 18 live eval questions.
