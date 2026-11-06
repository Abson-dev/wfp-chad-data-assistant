# Contributing

## Never commit survey data

This repository holds no survey data. Do not commit household files, `data/`, `.sav`, `.dta` or `.xlsx` files, evaluation answers, API keys, or screenshots with real estimates. Take screenshots on the invented dashboard (`demo/make_demo_data.py`).

Real regional estimates are shared only with WFP's agreement.

## Set up

```bash
python -m venv .venv && source .venv/bin/activate    # or the conda environment in environment.yml
pip install -r requirements.txt -r requirements-dev.txt
```

## Before a pull request

```bash
ruff check .
python -m pytest
```

Both run in GitHub Actions with no data and no API key. If you have the real tables, also run `WFP_TEST_REAL_DATA=1 python -m pytest`.

## Changing the assistant

- **A new tool.** Add it to `wfp_agent/tools.py` and to `TOOLS`. It must read only `data/`. Return tables through `_result`, so they can be published, and return a message the model can act on when an argument is wrong. Add tests for both.
- **New data for the tools.** Compute it in the dashboard's `prepare_app_data.R` (the only step that reads household data), copy it in `build_agent_data.py`, and add an invented version to `demo/make_demo_data.py` so the tests cover it. It must hold aggregates or metadata only. Add any new identifying column name to `FORBIDDEN`.
- **The instructions** (`wfp_agent/system_prompt.md`) or a tool description. Run `python evals/run_evals.py` and report the result: offline tests cannot judge the model's answers. Add an eval to `evals/questions.csv` for any new behaviour.
- **A new merged file or module.** Rerun the dashboard data, then `python build_agent_data.py`, and update the known defects and the module list in the instructions.
- **The browser client.** Text from the model or the data goes through `textContent`, never `innerHTML`. Keep every asset local: the Content-Security-Policy allows nothing else, and `tests/test_browser.py` fails on outside requests.
