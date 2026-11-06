# Changelog

## 0.1.0 (2026-11-06)

First version, built on the design of the ENSA Chad data assistant. Not yet run with the live model.

- 15 read-only tools over the dashboard aggregates of the WFP Chad merging repository: indicators (as merged and with corrections, with flags), regions, the harmonisation mapping, variable distributions, merge steps, validation checks, data issues, WFP interventions and costs, and checked result publishing.
- `build_agent_data.py` reads the dashboard's aggregates only and refuses household-level columns.
- Web app, command line, and a skill for other assistants.
- An invented dashboard (`demo/`) so the tests and the demo run with no data and no API key; 18 live eval questions.
