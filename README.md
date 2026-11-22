# QA PROD agent

External, read-only checks of public web pages, run on a schedule.

What it does: opens each listed public page in a real browser at desktop and
mobile size, confirms it loads and renders, flags script errors and horizontal
overflow, and records which public build of the site was checked. It also walks a
few anonymous-visitor flows (browse, fill a form without sending it, use the menu,
switch language) and reports each as one line.

What it never does: log in, submit forms, send anything except GET/HEAD, store
screenshots or artifacts, hold secrets, or read any private repository.

Output is limited to check codes with PASS / WARN / FAIL and the site's public
build id. Anything deeper is investigated privately, not here.

See `AGENTS.md` before changing anything.
