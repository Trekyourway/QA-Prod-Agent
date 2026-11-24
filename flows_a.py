"""Anonymous-visitor flows for target A.

Each flow drives public pages the way a visitor would and returns True/False.
Rules (do not weaken):
  * Nothing is ever submitted and the sign-in control is never pressed.
    The engine's request guard stays in force.
  * No output of any kind from this module: no prints, no page text, no errors.
  * Data-agnostic: nothing named, nothing compared with fixed wording.
"""
from urllib.parse import urlparse

NAV_MS = 20000
WAIT_MS = 10000
FLOWS = {}

# The sign-in screen is up, the working screens are not, and nothing is still loading.
SIGN_IN_ONLY = """() => {
  const seen = id => { const e = document.getElementById(id);
    return !!e && !!(e.offsetWidth || e.offsetHeight); };
  return seen('auth-gate') && seen('auth-google-btn')
    && !seen('app') && !seen('admission-loader');
}"""
SEEN = "e => !!(e.offsetWidth || e.offsetHeight)"


def flow(fn):
    def safe(page, origin, path):
        try:
            page.set_default_timeout(WAIT_MS)
            return fn(page, origin, path) is True
        except Exception:
            return False
    FLOWS[fn.__name__] = safe
    return safe


def _open(page, origin, path):
    resp = page.goto(origin + path, wait_until="domcontentloaded", timeout=NAV_MS)
    return bool(resp) and resp.status == 200 and _same_site(page, origin)


def _same_site(page, origin):
    return urlparse(page.url).hostname == urlparse(origin).hostname


def _settled_sign_in(page):
    """Wait for the sign-in screen, then confirm it stays the only screen."""
    page.wait_for_function(SIGN_IN_ONLY)
    page.wait_for_timeout(700)
    return page.evaluate(SIGN_IN_ONLY) is True


@flow
def gate(page, origin, path):
    """A visitor without an account gets a working sign-in screen and nothing else."""
    crashed = []
    page.on("pageerror", lambda _e: crashed.append(1))
    if not _open(page, origin, path):
        return False
    if not _settled_sign_in(page):
        return False
    root = page.evaluate(
        "() => ({ lang: document.documentElement.lang, dir: document.documentElement.dir })")
    if root["lang"][:2] != "he" or root["dir"] != "rtl":
        return False
    button = page.locator("#auth-google-btn")
    if not button.is_enabled():
        return False
    if page.eval_on_selector("#auth-google-btn", "e => e.innerText.trim().length") < 3:
        return False
    # No problem notice: the sign-in service answered and the screen is ready to use.
    if page.eval_on_selector("#auth-error", SEEN):
        return False
    # The button is reachable: inside the viewport and not covered by anything.
    on_top = page.eval_on_selector("#auth-google-btn", """e => {
      const r = e.getBoundingClientRect();
      if (r.width < 40 || r.height < 24 || r.left < 0 || r.top < 0
          || r.right > window.innerWidth || r.bottom > window.innerHeight) return false;
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return !!hit && (hit === e || e.contains(hit));
    }""")
    return on_top is True and _same_site(page, origin) and not crashed


@flow
def cancelled(page, origin, path):
    """Coming back from an abandoned sign-in shows a notice and leaves the screen usable."""
    if not _open(page, origin, path):
        return False
    if not _settled_sign_in(page):
        return False
    notice = page.locator("#auth-error")
    notice.wait_for(state="visible")
    if page.eval_on_selector("#auth-error", "e => e.innerText.trim().length") < 5:
        return False
    # The address is tidied, so a reload does not repeat the notice.
    here = urlparse(page.url)
    if here.query or here.fragment:
        return False
    if not page.locator("#auth-google-btn").is_enabled():
        return False
    page.reload(wait_until="domcontentloaded", timeout=NAV_MS)
    if not _settled_sign_in(page):
        return False
    return not page.eval_on_selector("#auth-error", SEEN)


@flow
def install(page, origin, path):
    """The install description is valid, its icon loads, and its start page is the sign-in screen."""
    if not _open(page, origin, path):
        return False
    page.wait_for_function(SIGN_IN_ONLY)
    info = page.evaluate("""async () => {
      const link = document.querySelector('link[rel="manifest"]');
      if (!link) return null;
      const base = new URL(link.getAttribute('href'), location.href);
      if (base.origin !== location.origin) return null;
      const res = await fetch(base.href);
      if (!res.ok) return null;
      const m = await res.json();
      const icon = (m.icons || [])[0];
      if (!m.name || !m.start_url || !icon || !icon.src) return null;
      const iconUrl = new URL(icon.src, base);
      const start = new URL(m.start_url, base);
      if (iconUrl.origin !== location.origin || start.origin !== location.origin) return null;
      const shown = await new Promise(done => {
        const img = new Image();
        img.onload = () => done(true);
        img.onerror = () => done(false);
        img.src = iconUrl.href;
      });
      return { shown, start: start.pathname + start.search, display: String(m.display || '') };
    }""")
    if not info or info["shown"] is not True or not info["start"].startswith("/"):
        return False
    if info["display"] not in ("standalone", "fullscreen", "minimal-ui"):
        return False
    if not _open(page, origin, info["start"]):
        return False
    return _settled_sign_in(page)
