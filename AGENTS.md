# Rules for anyone (human or AI agent) changing this repository

This repository is public. Treat every byte, commit message and log line as published.

Allowed
- Add a check to `targets.json`: a public path that any anonymous visitor can open.
  Give it a neutral code (letter of the target + number). Kinds: `page`, `json_ok`, `flow`.
- Add a `flow` check: a function in `flows_<target>.py` that walks public pages as an
  anonymous visitor and returns pass/fail. It may fill fields with obviously synthetic
  values and may press a submit button only on input the page itself must refuse; it
  must never complete a submission. It refers only to what any visitor sees in the page.
- `"enforce": false` on a check reports `WARN` instead of `FAIL` while it is in calibration.
- Improve the engine without weakening the rules below.

Never
- Secrets, tokens, keys, cookies, session files, test-account details.
- Checkout or download of any private repository, or copies of private code,
  fixtures, product logic, bug descriptions or incident details.
- Logins, form submissions, payments, or any request other than GET/HEAD.
- Screenshots, videos, traces, HAR files, artifacts, caches.
- Printing page content, error text, response bodies or failing URLs.
- Triggers other than `schedule`, `workflow_dispatch`, and the canary's own push.
  No `pull_request`, `pull_request_target`, `workflow_run`, `repository_dispatch`.
- Self-hosted or larger runners. Unpinned actions.

Before every push: `python3 -I prepublish_validate.py` must print `SAFE_TO_REVIEW`.
Flows that need a login or internal knowledge belong in the private project
repositories and run in a private environment.
