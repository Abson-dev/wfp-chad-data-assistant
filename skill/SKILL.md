---
name: wfp-chad-data
description: Answer questions about the merged WFP Chad resilience surveys (10 rounds, 2018 Baseline to 2023 Annual, 17,255 households) and how they were merged. Use for FCS, rCSI, LCS, HDDS, ABI, shocks, assets, markets and the other 2026 modules by round or region; for how a variable was merged, its source column and agreement with the raw value; for validation checks and data issues; and for WFP interventions and programme costs.
metadata:
  version: "0.1.0"
---

# WFP Chad data

Read `../wfp_agent/system_prompt.md` first: it holds the rules for reading the data and for answering. Every figure must come from a tool result. Never open the merged file or any household file.

Run the tools from the root of this repository, in the `wfp-agent` (or `ensa-agent`) conda environment:

```bash
python wfp_cli.py list
python wfp_cli.py trace_variable variable=rCSIBorrow
python wfp_cli.py describe_variable variable=HHHSex round_id=R10
python wfp_cli.py search_variables term=sécheresse
python wfp_cli.py get_merge_status module=Markets
python wfp_cli.py get_validation_checks status=fail
python wfp_cli.py search_data_issues keyword=coping
python wfp_cli.py get_indicator indicator=fcs_pb rounds="R08,R10"
python wfp_cli.py rank_regions indicator=shk_any12m round_id=R10
python wfp_cli.py get_costs table=reconciliation
```

The tables come from `python build_agent_data.py`, which reads the dashboard data of the merging repository. If a tool says a table is missing, ask the user to rerun the dashboard's `prepare_app_data.R` and then the build.

There is no `publish_result` in a shell: show the relevant rows as a table in your answer.

The figures are unweighted, and regional results are shared under WFP's agreement: keep them out of anything public.
