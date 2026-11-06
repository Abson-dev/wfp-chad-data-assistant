# Security

## Reporting a problem

Please report a vulnerability, or any survey data you find in this repository, privately to the maintainer (through GitHub's private vulnerability reporting once the repository is on GitHub), not in a public issue. If a report concerns exposed data, it will also be raised with WFP.

## What the design protects

- **Household data.** Only the dashboard's `prepare_app_data.R`, in the merging repository, reads the merged file, on the maintainer's computer. `build_agent_data.py` reads its aggregates and stops if any table carries an identifying column. The model and the web app see aggregates and metadata only.
- **Small groups.** Regional cells under 30 households are suppressed, and answer counts under 5 households are hidden in the variable distributions.
- **The browser.** Model output is inserted as text and never runs as code. A Content-Security-Policy restricts scripts to the app itself, and no asset is loaded from outside.
- **The web app.** It refuses to start on a reachable host without a password. Sessions are signed and end after 12 hours or a restart, and unlock attempts are limited per address.

## What it does not protect

- **Regional aggregates leave the computer.** They are sent to the Anthropic API when a question needs them. Agree this with WFP first.
- **The browser keeps the conversation.** Chat history, which can quote regional values, stays in the browser's local storage: avoid shared computers.
- **There is one shared password,** with no individual accounts or audit log, and the unlock limit is per address, so it needs a trusted proxy to be meaningful behind one.
- **HTTPS is not included.** Put it in front of any server reachable from a network.
- **Text in the data is trusted.** Question and value labels come from the survey files and reach the model as data. The tools are read-only, which limits what a misleading label could do.
- **Answers can be wrong.** Check the sources under each answer before using a figure.
