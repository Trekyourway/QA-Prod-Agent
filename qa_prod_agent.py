#!/usr/bin/env python3
"""QA PROD agent - external, read-only browser checks of public web pages.

Design rules (do not weaken):
  * No logins, no secrets, no form submissions, no screenshots, no artifacts.
  * The browser may only send GET/HEAD. Every other method is aborted.
  * Public output is limited to check codes, PASS/WARN/FAIL and the public
    build id of the site. Never print page content, URLs of failures or errors.
"""
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

VIEWPORTS = {
    "d": {"viewport": {"width": 1440, "height": 900}},
    "m": {"viewport": {"width": 390, "height": 844}, "is_mobile": True,
          "has_touch": True, "device_scale_factor": 2},
}
# Third-party hosts that must never receive traffic from automated checks.
NO_TRACK = ("google-analytics.com", "googletagmanager.com", "facebook.net",
            "facebook.com", "clarity.ms", "hotjar.com", "doubleclick.net")
MIN_TEXT = 12
NAV_TIMEOUT_MS = 25000
CODE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


def load_targets(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    targets = data["targets"]
    for t in targets:
        u = urlparse(t["origin"])
        local = u.hostname == "127.0.0.1"
        if not (u.scheme == "https" or (local and u.scheme == "http")):
            raise ValueError("origin must be https")
        for c in t["checks"]:
            if not CODE_RE.match(c["code"]) or c["kind"] not in ("page", "json_ok"):
                raise ValueError("bad check")
            if not c["path"].startswith("/"):
                raise ValueError("bad path")
    return targets


def make_guard(origin_host):
    def guard(route):
        req = route.request
        host = urlparse(req.url).hostname or ""
        scheme = urlparse(req.url).scheme
        if req.method not in ("GET", "HEAD"):
            return route.abort()
        if any(host == h or host.endswith("." + h) for h in NO_TRACK):
            return route.abort()
        if scheme not in ("https", "data", "blob") and host != "127.0.0.1":
            return route.abort()
        return route.continue_()
    return guard


def read_version(request, target):
    spec = target.get("version")
    if not spec:
        return "unknown"
    try:
        r = request.get(target["origin"] + spec["path"], timeout=NAV_TIMEOUT_MS, max_redirects=0)
        v = r.json()
        for k in spec["keys"]:
            v = v[k]
        v = str(v)
        return v[:12] if re.fullmatch(r"[0-9a-f]{7,40}", v) else "unknown"
    except Exception:
        return "unknown"


def check_page(context, target, check):
    """Returns {'load': PASS|FAIL, 'js': PASS|WARN, 'fit': PASS|WARN}."""
    host = urlparse(target["origin"]).hostname
    page = context.new_page()
    js_errors = []
    page.on("pageerror", lambda _e: js_errors.append(1))
    out = {"load": "FAIL", "js": "PASS", "fit": "PASS"}
    try:
        page.route("**/*", make_guard(host))
        resp = page.goto(target["origin"] + check["path"],
                         wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        same_host = urlparse(page.url).hostname == host
        # Pages that render client-side start with an empty body: wait for real text.
        page.wait_for_function(
            "n => !!document.body && document.body.innerText.trim().length >= n",
            arg=MIN_TEXT, timeout=10000)
        if resp and resp.status == 200 and same_host:
            out["load"] = "PASS"
        page.wait_for_timeout(1500)
        if js_errors:
            out["js"] = "WARN"
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - window.innerWidth")
        if overflow > 2:
            out["fit"] = "WARN"
    except Exception:
        out["load"] = "FAIL"
    finally:
        page.close()
    return out


def check_json_ok(request, target, check):
    try:
        r = request.get(target["origin"] + check["path"], timeout=NAV_TIMEOUT_MS, max_redirects=0)
        return "PASS" if r.status == 200 and r.json().get("ok") is True else "FAIL"
    except Exception:
        return "FAIL"


def run_target(pw, target, wanted):
    lines, failed = [], False
    launch = {"headless": True}
    if os.environ.get("QA_BROWSER_PATH"):
        launch["executable_path"] = os.environ["QA_BROWSER_PATH"]
    browser = pw.chromium.launch(**launch)
    try:
        api = browser.new_context(service_workers="block").request
        before = read_version(api, target)
        for check in target["checks"]:
            if wanted and check["code"] not in wanted:
                continue
            if check["kind"] == "json_ok":
                res = check_json_ok(api, target, check)
                failed |= res == "FAIL"
                lines.append(f"{check['code']} - api {res}")
                continue
            for vp_name, vp in VIEWPORTS.items():
                ctx = browser.new_context(service_workers="block",
                                          accept_downloads=False, **vp)
                try:
                    res = check_page(ctx, target, check)
                    if res["load"] == "FAIL":  # one retry: a single slow load is not a finding
                        res = check_page(ctx, target, check)
                finally:
                    ctx.close()
                failed |= res["load"] == "FAIL"
                for part in ("load", "js", "fit"):
                    lines.append(f"{check['code']} {vp_name} {part} {res[part]}")
        after = read_version(api, target)
    finally:
        browser.close()
    return before, after, lines, failed


def main(argv):
    targets_file = argv[1] if len(argv) > 1 else str(Path(__file__).with_name("targets.json"))
    raw = os.environ.get("QA_CODES", "")
    wanted = {c for c in raw.replace(" ", "").split(",") if CODE_RE.match(c)}
    overall_fail = False
    try:
        targets = load_targets(targets_file)
        with sync_playwright() as pw:
            for target in targets:
                for attempt in (1, 2):
                    before, after, lines, failed = run_target(pw, target, wanted)
                    if before == after:
                        break
                # A deploy landed mid-run twice: results cannot be tied to one build.
                state = "STABLE" if before == after else "MIXED"
                print(f"BUILD {target['id']} {after} {state}", flush=True)
                for line in lines:
                    print(line, flush=True)
                overall_fail |= failed or state == "MIXED"
    except Exception:
        overall_fail = True
    print("RESULT FAIL" if overall_fail else "RESULT PASS", flush=True)
    return 1 if overall_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
