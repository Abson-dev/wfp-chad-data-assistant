# Changelog

## 0.7.0 (2026-11-12)

- **Added:** `compare_original`, the dashboard's new Original vs merged page: WFP's raw files against the merged file on the same households. With no variable, FCS and rCSI recomputed from the raw items against the merged scores, and the status of every variable by round; with a variable, its answers, mean, median and range on both sides; with a variable and a round, how each raw code became a merged code.
- **Changed:** the instructions say that a variable whose values differ is mostly recoded on purpose, and to check the issue register before calling it a defect. One eval question on it.

## 0.6.0 (2026-11-11)

Individual accounts, for security.

- **Added:** accounts created by the administrator with `manage_users.py` (add, list, reset-password, disable, enable, remove, audit); nobody signs up. A new account has a temporary password, typed by the administrator and never shown, to change at the first sign-in.
- **Added:** passwords hashed with scrypt; sessions as random tokens kept hashed on the server, ended by signing out, a new password, a reset or a disabled account; an account locked for 15 minutes after 5 failures; sign-in attempts limited per address; one refusal message for every failure.
- **Added:** a security log of sign-ins, failures, locks, password changes and account changes (`manage_users.py audit`).
- **Changed:** the chats are kept per account on the server (`accounts.sqlite` in the state folder: `%LOCALAPPDATA%\wfp-chad-data-assistant` on Windows, out of OneDrive), visible to their owner only, on any computer; the browser keeps none, and the chats saved by earlier versions are cleared from it. The model's history comes from the server's copy of the chat, not from the browser. A chat can be deleted.
- **Changed:** the daily limit counts per account (50) and for all accounts (300). The feedback records the account and takes the question and answer from the server's copy.
- **Removed:** the shared password (`APP_PASSWORD`) and the unlocked mode (`WFP_AGENT_ALLOW_UNLOCKED`). `run_web.bat` asks for the first account when there is none.

## 0.5.0 (2026-11-11)

Ideas from the riapa.ai web app.

- **Added:** a caution on the welcome page: the figures are unweighted and preliminary, not forecasts and not official WFP results, and they describe changes without explaining them.
- **Added:** a fixed example, written by the server from the tables with no model and no API key (`wfp_agent/example.py`), with its chart, check of the figures, sources and trace.
- **Added:** "How it works" and "Rounds and data" pages: how an answer is made and checked, how to read the figures, the limits, the data version, the rounds and the tools.
- **Added:** an opinion (useful or not, with a comment) under each answer, kept in `local/feedback.jsonl` on this computer to find weak answers and new eval questions.
- **Added:** a limit of questions per day (`questions_per_day` in `limits.json`, 100), counted in `local/usage.json` so a restart does not reset it. The example does not count.
- **Fixed:** the agent trusts the system's certificates, so the API is reached behind networks that inspect HTTPS, such as CGIAR's.

## 0.4.0 (2026-11-11)

Design ideas from IFPRI's RIAPA-AI (Discussion Paper 2424, 2026).

- **Added:** every figure of an answer is checked against the tool results of the question, the earlier turns and the instructions (`wfp_agent/grounding.py`). The web app and `ask.py` show the figures not found as unverified; the evals record them.
- **Added:** `profile_indicator`: one indicator in every round, the change since the previous round of the same series, and the regions that improved and worsened most, so the model does no arithmetic.
- **Added:** each answer carries its trace (tool calls, what they returned, model, effort and data version), downloadable as JSON from the web app.
- **Added:** expert grading as in RIAPA-AI's evaluation: `run_evals.py --baseline` asks the model alone, `--sheet` writes a blind grading sheet (correctness, depth, objectivity; 1, 0.5, 0), and `evals/score_rubric.py` gives the shares ready to use with exact intervals, McNemar's exact test and the Wilcoxon signed-rank test. See `evals/RUBRIC.md`.
- **Changed:** the instructions forbid arithmetic and causes the tables do not measure, and ask where a change happened. Three eval questions test objectivity and depth; q04 no longer expects a flag, since v9 fixed the 2023 Annual coping.

## 0.3.1 (2026-11-11)

- **Changed:** the tables follow the merged file v9, where do-file 23 corrects the coping answers (2018 Baseline, 2020 PDM, 2023 Annual, LhCSIStress1 in 9 rounds) and the 2022 Annual ABI answers. The instructions list these defects as fixed; the merged and corrected figures now agree for them.

## 0.3.0 (2026-11-11)

- **Added:** the merged file v8: crop production and sales (`Crp*`) and the sources of the food eaten (`Fsr*`), from do-file 22 of the merging repository, with four new indicators and seven new data issues.
- **Changed:** the instructions ask the model to check the issue register before reporting any figure or variable, and state the issues that bear on it. They describe the crop and food-source caveats.
- **Added:** two eval questions on the crop units and the food sources.

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
