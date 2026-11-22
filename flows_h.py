"""Anonymous-visitor flows for target H.

Each flow drives public pages the way a visitor would and returns True/False.
Rules (do not weaken):
  * Nothing is ever submitted. The engine's request guard stays in force.
  * No output of any kind from this module: no prints, no page text, no errors.
  * Data-agnostic: always "the first real option", never a named item.
  * Inputs are obviously synthetic and dates are relative to today.
"""
import datetime
from urllib.parse import urlparse

NAV_MS = 20000
WAIT_MS = 10000
FLOWS = {}

SHOWN = "e => !!e && !!(e.offsetWidth || e.offsetHeight)"


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


def _path(page):
    return urlparse(page.url).path.rstrip("/") or "/"


def _follow(page, origin, locator):
    """Click a link and require a clean same-site page load."""
    with page.expect_navigation(wait_until="domcontentloaded", timeout=NAV_MS) as nav:
        locator.click()
    resp = nav.value
    return bool(resp) and resp.status == 200 and _same_site(page, origin)


def _shown(page, selector):
    return page.eval_on_selector_all(
        selector, "els => els.filter(e => e.offsetWidth || e.offsetHeight).length")


def _put(page, selector, value):
    """Fill a field and confirm it kept the value (pages may move focus briefly)."""
    for _ in range(3):
        page.fill(selector, value)
        page.wait_for_timeout(150)
        if page.input_value(selector) == value:
            return
    raise ValueError("field")


def _day(offset):
    return (datetime.date.today() + datetime.timedelta(days=offset)).isoformat()


@flow
def discover(page, origin, path):
    """Route list is enriched from live data and the search box filters it."""
    if not _open(page, origin, path):
        return False
    page.wait_for_function(
        """() => {
          const g = document.querySelector('.trek-grid');
          if (!g || g.getAttribute('data-coverage-loaded') !== 'true') return false;
          return [...g.querySelectorAll('.trek-card a[data-trek-cover-action]')].some(a => {
            const p = new URL(a.getAttribute('href'), location.href).pathname;
            return p.startsWith('/planner') || p.startsWith('/register');
          });
        }""")
    cards = ".trek-grid .trek-card:not([hidden])"
    total = page.locator(cards).count()
    if total < 2:
        return False
    title = page.eval_on_selector(
        cards + " h3", "e => e.textContent.replace(/\\s+/g, ' ').trim()")
    box = page.locator("#trekSearch")
    box.fill(title)
    page.wait_for_function(
        "a => { const n = document.querySelectorAll(a[0]).length; return n >= 1 && n < a[1]; }",
        arg=[cards, total])
    box.fill("zzqqxxzzqq")
    page.wait_for_function(
        "s => document.querySelectorAll(s).length === 0", arg=cards)
    if not page.locator("#trekNone").is_visible():
        return False
    box.fill("")
    page.wait_for_function(
        "a => document.querySelectorAll(a[0]).length === a[1]", arg=[cards, total])
    return True


@flow
def trek_page(page, origin, path):
    """A route tile leads to its own page; browser back returns."""
    if not _open(page, origin, path):
        return False
    start = _path(page)
    tile = page.locator("a.trek-tile").first
    tile.wait_for(state="visible")
    if not _follow(page, origin, tile):
        return False
    if _path(page) == start or not _path(page).startswith("/trails-hike/"):
        return False
    page.wait_for_function(
        "() => { const h = document.querySelector('h1');"
        " return !!h && h.innerText.trim().length > 1 && document.body.innerText.trim().length > 200; }")
    page.go_back(wait_until="domcontentloaded", timeout=NAV_MS)
    page.locator("a.trek-tile").first.wait_for(state="visible")
    return _path(page) == start


HUT_READY = """() => { const s = document.getElementById('field-hut');
  return !!s && !s.disabled && [...s.options].some(o => o.value); }"""
VALUES = "s => [...document.querySelector(s).options].map(o => o.value).filter(Boolean)"


@flow
def form_fill(page, origin, path):
    """The request form can be completed end to end. Never submitted."""
    if not _open(page, origin, path):
        return False
    page.wait_for_function(HUT_READY)
    # Service choice: switching cards moves the selection, and back again.
    first = page.eval_on_selector("#field-uc", "e => e.value")
    for _ in range(2):
        before = page.eval_on_selector("#field-uc", "e => e.value")
        page.locator(".uc-card:not(.selected)").first.click()
        page.wait_for_function(
            "v => document.getElementById('field-uc').value !== v"
            " && document.querySelectorAll('.uc-card.selected').length === 1", arg=before)
    if page.eval_on_selector("#field-uc", "e => e.value") != first:
        return False
    # Route choice narrows the hut list; then the first real hut.
    all_huts = len(page.evaluate(VALUES, "#field-hut"))
    treks = page.evaluate(VALUES, "#leg-trek-first")
    if not treks or all_huts < 1:
        return False
    page.select_option("#leg-trek-first", treks[0])
    page.wait_for_function(
        "n => { const k = [...document.getElementById('field-hut').options]"
        ".filter(o => o.value).length; return k >= 1 && k <= n; }", arg=all_huts)
    huts = page.evaluate(VALUES, "#field-hut")
    page.select_option("#field-hut", huts[0])
    _put(page, "#field-date-from", _day(40))
    _put(page, "#field-date-to", _day(41))
    _put(page, "#field-name", "[QA-TEST] Bot")
    _put(page, "#field-email", "qa-bot@example.invalid")
    _put(page, "#field-phone", "500000000")
    notes = page.locator("#field-notes")
    if notes.is_visible():
        notes.fill("[QA-TEST]")
    page.check("#field-consent")
    page.locator("#field-name").focus()
    page.wait_for_function(
        "() => document.getElementById('primary-leg-summary').innerText.trim().length > 0")
    if page.eval_on_selector("#field-hut", "e => e.value") != huts[0]:
        return False
    if _shown(page, "#request-form .field-error") != 0:
        return False
    button = page.locator("#submit-btn")
    return button.is_visible() and button.is_enabled()


@flow
def form_guard(page, origin, path):
    """Invalid input is refused on the page with visible messages.

    Consent and phone stay empty throughout, so the form is never valid.
    """
    if not _open(page, origin, path):
        return False
    page.wait_for_function(HUT_READY)
    start = page.url
    button = page.locator("#submit-btn")

    def refused(*must_show):
        button.click()
        for selector in must_show:
            page.locator(selector).wait_for(state="visible")
        page.wait_for_timeout(400)
        return (page.url == start and _shown(page, "#success-box") == 0
                and _shown(page, "#request-form .field-error") >= 1)

    # 1. Empty form.
    if not refused("#err-name", "#err-email", "#err-hut"):
        return False
    # 2. Malformed address and a stay that ends before it starts.
    _put(page, "#field-name", "[QA-TEST] Bot")
    _put(page, "#field-email", "qa-bot-at-example.invalid")
    page.select_option("#field-hut", page.evaluate(VALUES, "#field-hut")[0])
    _put(page, "#field-date-from", _day(41))
    _put(page, "#field-date-to", _day(40))
    if not refused("#err-email", "#err-date-to"):
        return False
    if _shown(page, "#err-name") != 0 or _shown(page, "#err-hut") != 0:
        return False
    # 3. Same date twice, address corrected.
    _put(page, "#field-email", "qa-bot@example.invalid")
    _put(page, "#field-date-to", _day(41))
    if not refused("#err-date-to"):
        return False
    return _shown(page, "#err-email") == 0


@flow
def planner(page, origin, path):
    """The planner draws a day-by-day route and reacts to pace and length."""
    if not _open(page, origin, path):
        return False
    rows = "#timeline .trow"
    page.locator(rows).first.wait_for(state="visible")
    if _shown(page, "#sec-error") != 0:
        return False
    state = """() => document.getElementById('daysVal').innerText.trim() + '/'
                 + document.querySelectorAll('#timeline .trow').length"""
    if not page.evaluate(state).split("/")[0].isdigit():
        return False
    # Pace: another profile becomes the active one and the route is redrawn.
    other = page.locator("#profGrid .prof:not(.on)")
    if other.count() and other.first.is_visible():
        index = page.eval_on_selector_all(
            "#profGrid .prof", "els => els.findIndex(e => !e.classList.contains('on'))")
        other.first.click()
        page.wait_for_function(
            "i => { const p = document.querySelectorAll('#profGrid .prof');"
            " return p[i].classList.contains('on')"
            " && document.querySelectorAll('#profGrid .prof.on').length === 1"
            " && document.querySelectorAll('#timeline .trow').length >= 1; }", arg=index)
    # Length: one more day (or one fewer at the upper limit) changes the plan.
    before = page.evaluate(state)
    for control in ("#daysPlus", "#daysMinus"):
        if not page.locator(control).is_enabled():
            continue  # already at that limit
        page.locator(control).click()
        try:
            page.wait_for_function("a => (" + state + ")() !== a", arg=before, timeout=4000)
        except Exception:
            continue
        after = page.evaluate(state)
        return (after.split("/")[0] != before.split("/")[0]
                and after.split("/")[1] != before.split("/")[1]
                and _shown(page, "#sec-error") == 0)
    return False


def _leave_and_return(page, origin, links):
    """Follow the first shown link that goes to another page, then go back."""
    start = _path(page)
    index = links.evaluate_all(
        """(els, here) => els.findIndex(a => (a.offsetWidth || a.offsetHeight)
             && new URL(a.href).origin === location.origin
             && (new URL(a.href).pathname.replace(/\\/+$/, '') || '/') !== here)""", start)
    if index < 0 or not _follow(page, origin, links.nth(index)):
        return False
    if _path(page) == start:
        return False
    page.wait_for_function("() => document.body.innerText.trim().length > 50")
    page.go_back(wait_until="domcontentloaded", timeout=NAV_MS)
    page.wait_for_function("() => document.body.innerText.trim().length > 50")
    return _path(page) == start


@flow
def menu(page, origin, path):
    """Site menu opens, shows its links, closes, and navigation round-trips."""
    if not _open(page, origin, path):
        return False
    burger = page.locator("#hamburger-btn")
    page.wait_for_function(
        "() => [document.getElementById('hamburger-btn'), document.getElementById('trailsTrigger')]"
        ".some(e => e && (e.offsetWidth || e.offsetHeight))")
    if burger.is_visible():
        panel, links = page.locator("#mobile-menu"), page.locator("#mobile-menu a[href]")
        burger.click()
        panel.wait_for(state="visible")
        if _shown(page, "#mobile-menu a[href]") < 3:
            return False
        if burger.get_attribute("aria-expanded") != "true":
            return False
        page.locator("#mobile-menu-close").click()
        panel.wait_for(state="hidden")
        burger.click()
        panel.wait_for(state="visible")
        sub = page.locator("#mobTrailsToggle")
        if sub.is_visible():
            sub.click()
            page.locator("#mobTrailsSub a[href]").first.wait_for(state="visible")
            if sub.get_attribute("aria-expanded") != "true":
                return False
    else:
        trigger = page.locator("#trailsTrigger")
        panel, links = page.locator("#trailsMenu"), page.locator("#trailsMenu a[href]")
        trigger.click()
        panel.wait_for(state="visible")
        if _shown(page, "#trailsMenu a[href]") < 2:
            return False
        if trigger.get_attribute("aria-expanded") != "true":
            return False
        page.keyboard.press("Escape")
        panel.wait_for(state="hidden")
        trigger.click()
        panel.wait_for(state="visible")
    return _leave_and_return(page, origin, links)


@flow
def language(page, origin, path):
    """The language picker switches the page language, direction and text."""
    if not _open(page, origin, path):
        return False
    page.wait_for_function("() => document.querySelectorAll('ywt-lang-picker .ylp-opt').length > 1")
    probe = """() => ({ lang: document.documentElement.lang, dir: document.documentElement.dir,
                       text: ((document.querySelector('h1') || {}).innerText || '') + '|' + document.title })"""
    page.wait_for_function("() => !!document.querySelector('h1')")
    page.wait_for_timeout(600)
    before = page.evaluate(probe)
    burger = page.locator("#hamburger-btn")
    if burger.is_visible():
        burger.click()
        page.locator("#mobile-menu").wait_for(state="visible")
    index = page.eval_on_selector_all(
        "ywt-lang-picker", "els => els.findIndex(e => e.offsetWidth || e.offsetHeight)")
    if index < 0:
        return False
    picker = page.locator("ywt-lang-picker").nth(index)
    picker.locator(".ylp-btn").click()
    option = picker.locator(".ylp-opt[aria-selected='false']").first
    option.wait_for(state="visible")
    wanted = option.get_attribute("data-lang")
    if not wanted or wanted == before["lang"]:
        return False
    with page.expect_navigation(wait_until="domcontentloaded", timeout=NAV_MS):
        option.click()
    page.wait_for_function(
        "w => document.documentElement.lang.slice(0, 2) === w && !!document.querySelector('h1')"
        " && !document.documentElement.classList.contains('i18n-loading')", arg=wanted)
    page.wait_for_timeout(600)
    after = page.evaluate(probe)
    rtl = ("he", "ar")
    return (_same_site(page, origin)
            and after["dir"] == ("rtl" if wanted in rtl else "ltr")
            and (after["dir"] != before["dir"]) == ((wanted in rtl) != (before["lang"][:2] in rtl))
            and after["text"] != before["text"]
            and len(after["text"]) > 3)


@flow
def no_order(page, origin, path):
    """Opened with no order, the payment page explains itself and offers no pay action."""
    crashed = []
    page.on("pageerror", lambda _e: crashed.append(1))
    if not _open(page, origin, path):
        return False
    page.wait_for_function(
        "() => { const s = document.getElementById('status');"
        " return !!s && (s.offsetWidth || s.offsetHeight) && s.innerText.trim().length > 10; }")
    pay = page.locator("#pay")
    if pay.count() != 1 or pay.is_enabled():
        return False
    page.wait_for_function("() => !!document.querySelector('h1') && document.querySelector('h1').innerText.trim().length > 1")
    return _path(page) == (urlparse(origin + path).path.rstrip("/") or "/") and not crashed
