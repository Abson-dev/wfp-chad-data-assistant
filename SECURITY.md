# Security

## Reporting a problem

Please report a vulnerability, or any survey data you find in this repository, privately through [GitHub's private vulnerability reporting](https://github.com/Abson-dev/wfp-chad-data-assistant/security/advisories/new), not in a public issue. If a report concerns exposed data, it will also be raised with WFP.

## What the design protects

- **Household data.** Only the dashboard's `prepare_app_data.R`, in the merging repository, reads the merged file, on the maintainer's computer. `build_agent_data.py` reads its aggregates and stops if any table carries an identifying column. The model and the web app see aggregates and metadata only.
- **Small groups.** Regional cells under 30 households are suppressed, and answer counts under 5 households are hidden in the variable distributions.
- **The browser.** Model output is inserted as text and never runs as code. A Content-Security-Policy restricts scripts to the app itself, and no asset is loaded from outside.
- **Access.** Every page needs a personal account, created by the administrator with `manage_users.py`; nobody signs up. The temporary password is typed by the administrator, never shown, and must be changed at the first sign-in. New passwords need 10 varied characters without the email address.
- **Passwords and sessions.** Passwords are hashed with scrypt. A session is a random token that the server keeps hashed, so a copy of the database opens no session; it ends after 12 hours, at sign-out, and when the password is changed or reset or the account disabled. The cookie is HttpOnly and SameSite=Strict, and Secure over HTTPS.
- **Guessing.** Every failed sign-in gives the same message, and an unknown email takes as long as a wrong password. Five failures lock an account for 15 minutes, and sign-in attempts are limited per address.
- **Chats.** Each account sees only its own chats, kept on the server; the model's history is taken from the server's copy, so a browser cannot inject earlier turns. The browser keeps no chat.
- **Log.** Sign-ins, refusals, locks, password changes and account changes are logged with their address (`manage_users.py audit`).

## What it does not protect

- **Regional aggregates leave the computer.** They are sent to the Anthropic API when a question needs them. Agree this with WFP first.
- **The state folder holds everything.** `accounts.sqlite` keeps the accounts, the chats (which quote regional values) and the log, and `feedback.jsonl` the opinions. On Windows the folder is `%LOCALAPPDATA%\wfp-chad-data-assistant`, out of OneDrive; `WFP_AGENT_STATE` moves it. Anyone who can read that folder can read the chats: keep it on an encrypted, access-controlled disk, and out of shared or synced folders.
- **No second factor and no password reset by email.** A forgotten password is reset by the administrator.
- **The per-address limit needs a trusted proxy** to be meaningful behind one, since every request then comes from the proxy's address.
- **HTTPS is not included.** Put it in front of any server reachable from a network.
- **Text in the data is trusted.** Question and value labels come from the survey files and reach the model as data. The tools are read-only, which limits what a misleading label could do.
- **Answers can be wrong.** Check the sources under each answer before using a figure.
