#!/usr/bin/env python3
"""QA PROD agent - external, read-only browser checks of public web pages.

Design rules (do not weaken):
  * No logins, no secrets, no form submissions, no screenshots, no artifacts.
  * The browser may only send GET/HEAD. Every other method is aborted.
  * Public output is limited to check codes, PASS/WARN/FAIL and the public
    build id of the site. Never print page content, URLs of failures or errors.
"""
import importlib.util
import json
import os
import re
import sys
import time
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
FLOW_RE = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
KINDS = ("page", "json_ok", "flow")
FLOW_BUDGET_S = 300  # flows of one target stop starting after this long


def load_targets(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    targets = data["targets"]
    for t in targets:
        u = urlparse(t["origin"])
        local = u.hostname == "127.0.0.1"
        if not (u.scheme == "https" or (local and u.scheme == "http")):
            raise ValueError("origin must be https")
        for c in t["checks"]:
            if not CODE_RE.match(c["code"]) or c["kind"] not in KINDS:
                raise ValueError("bad check")
            if c["kind"] == "flow" and not FLOW_RE.match(str(c.get("flow", ""))):
                raise ValueError("bad flow")
            if not isinstance(c.get("enforce", True), bool):
                raise ValueError("bad enforce")
            if not c["path"].startswith("/"):
                raise ValueError("bad path")
    return targets


# Second layer under the request guard: the page itself cannot issue a write.
# The guard aborts writes on the wire; this stops them before they are created,
# so nothing is left pending when a page or context is closed.
READ_ONLY_JS = """(() => {
  const safe = m => ['GET', 'HEAD'].includes(String(m || 'GET').toUpperCase());
  const realFetch = window.fetch;
  window.fetch = function (input, init) {
    const m = (init && init.method) || (input && typeof input === 'object' && input.method) || 'GET';
    return safe(m) ? realFetch.apply(this, arguments) : Promise.reject(new TypeError('Failed to fetch'));
  };
  const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m) { this.__ro = safe(m); return open.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    if (this.__ro) return send.apply(this, arguments);
    setTimeout(() => this.dispatchEvent(new Event('error')), 0);
  };
  if (navigator.sendBeacon) navigator.sendBeacon = () => false;
  const formSafe = f => safe(f.getAttribute('method'));
  const submit = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function () { if (formSafe(this)) return submit.apply(this, arguments); };
  addEventListener('submit', e => {
    if (e.target instanceof HTMLFormElement && !formSafe(e.target) && !e.defaultPrevented) e.preventDefault();
  });
})();"""


def read_only(ctx):
    """Every browsing context used for a check gets the in-page write block."""
    ctx.add_init_script(READ_ONLY_JS)
    return ctx


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


def park(page):
    """Leave the site before a page or context is torn down. A request the page
    has only just issued could otherwise be released, unguarded, by the teardown."""
    try:
        page.goto("about:blank", timeout=5000)
        page.wait_for_timeout(150)  # let any queued request reach the guard
    except Exception:
        pass


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
        park(page)
        page.close()
    return out


def check_json_ok(request, target, check):
    for attempt in (1, 2):  # one retry, as for page loads: a single blip is not a finding
        try:
            r = request.get(target["origin"] + check["path"], timeout=NAV_TIMEOUT_MS, max_redirects=0)
            if r.status == 200 and r.json().get("ok") is True:
                return "PASS"
        except Exception:
            pass
        if attempt == 1:
            time.sleep(3)
    return "FAIL"


def load_flows(target):
    """Flow functions of a target live in flows_<id>.py next to this file."""
    try:
        name = "flows_" + target["id"].lower()
        if not re.fullmatch(r"flows_[a-z]", name):
            return {}
        sys.dont_write_bytecode = True  # leave no cache files behind
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return dict(module.FLOWS)
    except Exception:
        return {}


def check_flow(browser, target, check, vp, flows):
    """Returns PASS|FAIL. Fresh context per attempt; the guard covers every tab."""
    fn = flows.get(check["flow"])
    if fn is None:
        return "FAIL"
    host = urlparse(target["origin"]).hostname
    for _attempt in (1, 2):  # one retry, as for page loads
        ctx = read_only(browser.new_context(service_workers="block", accept_downloads=False, **vp))
        try:
            ctx.route("**/*", make_guard(host))
            if fn(ctx.new_page(), target["origin"], check["path"]) is True:
                return "PASS"
        except Exception:
            pass
        finally:
            for open_page in ctx.pages:
                park(open_page)
            ctx.close()
    return "FAIL"


def soften(check, result):
    """A check marked "enforce": false reports WARN instead of FAIL."""
    return "WARN" if result == "FAIL" and check.get("enforce", True) is False else result


def run_target(pw, target, wanted):
    lines, failed = [], False
    launch = {"headless": True}
    if os.environ.get("QA_BROWSER_PATH"):
        launch["executable_path"] = os.environ["QA_BROWSER_PATH"]
    browser = pw.chromium.launch(**launch)
    try:
        api = browser.new_context(service_workers="block").request
        before = read_version(api, target)
        flows, flows_started = None, None
        for check in target["checks"]:
            if wanted and check["code"] not in wanted:
                continue
            if check["kind"] == "json_ok":
                res = soften(check, check_json_ok(api, target, check))
                failed |= res == "FAIL"
                lines.append(f"{check['code']} - api {res}")
                continue
            if check["kind"] == "flow":
                if flows is None:
                    flows, flows_started = load_flows(target), time.monotonic()
                for vp_name, vp in VIEWPORTS.items():
                    if time.monotonic() - flows_started > FLOW_BUDGET_S:
                        res = soften(check, "FAIL")
                    else:
                        res = soften(check, check_flow(browser, target, check, vp, flows))
                    failed |= res == "FAIL"
                    lines.append(f"{check['code']} {vp_name} flow {res}")
                continue
            for vp_name, vp in VIEWPORTS.items():
                ctx = read_only(browser.new_context(service_workers="block",
                                                    accept_downloads=False, **vp))
                try:
                    res = check_page(ctx, target, check)
                    if res["load"] == "FAIL":  # one retry: a single slow load is not a finding
                        res = check_page(ctx, target, check)
                finally:
                    ctx.close()
                res["load"] = soften(check, res["load"])
                failed |= res["load"] == "FAIL"
                for part in ("load", "js", "fit"):
                    lines.append(f"{check['code']} {vp_name} {part} {res[part]}")
        after = read_version(api, target)
    finally:
        browser.close()
    return before, after, lines, failed


def write_record(record):
    """Optional machine-readable copy of exactly what was printed: codes, outcomes,
    build ids and the time. Nothing else is ever added to it."""
    path = os.environ.get("QA_RESULTS_FILE")
    if not path:
        return
    try:
        Path(path).write_text(json.dumps(record, separators=(",", ":")), encoding="utf-8")
    except Exception:
        pass


def main(argv):
    targets_file = argv[1] if len(argv) > 1 else str(Path(__file__).with_name("targets.json"))
    raw = os.environ.get("QA_CODES", "")
    wanted = {c for c in raw.replace(" ", "").split(",") if CODE_RE.match(c)}
    overall_fail = False
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "targets": []}
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
                record["targets"].append(
                    {"id": target["id"], "build": after, "state": state, "lines": lines})
    except Exception:
        overall_fail = True
    record["result"] = "FAIL" if overall_fail else "PASS"
    write_record(record)
    print("RESULT FAIL" if overall_fail else "RESULT PASS", flush=True)
    return 1 if overall_fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
