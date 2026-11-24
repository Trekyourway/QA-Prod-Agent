"""Anonymous-visitor flows for target Y.

Each flow drives public pages the way a visitor would and returns True/False.
Rules (do not weaken):
  * Nothing is ever submitted and no sign-in is ever started. The engine's
    request guard stays in force.
  * No output of any kind from this module: no prints, no page text, no errors.
  * Data-agnostic: always "the first real option", never a named item.
  * Inputs are obviously synthetic.
"""
from urllib.parse import urlparse

NAV_MS = 20000
WAIT_MS = 10000
FLOWS = {}

CARD = "[data-testid='provider-card']"
SIGN_IN = "[data-testid='google-login-btn']"  # looked at, never pressed
ASK = "[data-testid='search-chat-input']"
ASK_SEND = "[data-testid='search-chat-send']"  # never pressed


def flow(fn):
    def safe(page, origin, path):
        try:
            page.set_default_timeout(WAIT_MS)
            return fn(page, origin, path) is True
        except Exception:
            return False
    FLOWS[fn.__name__] = safe
    return safe


def _same_site(page, origin):
    return urlparse(page.url).hostname == urlparse(origin).hostname


def _path(page):
    return urlparse(page.url).path.rstrip("/") or "/"


def _watch(page, origin):
    """Collect same-site paths that answered with an error status."""
    host, bad = urlparse(origin).hostname, set()

    def seen(resp):
        u = urlparse(resp.url)
        if u.hostname == host and resp.status >= 400:
            bad.add(u.path.rstrip("/") or "/")
    page.on("response", seen)
    return bad


def _open(page, origin, path):
    resp = page.goto(origin + path, wait_until="domcontentloaded", timeout=NAV_MS)
    return bool(resp) and resp.status == 200 and _same_site(page, origin)


def _arrive(page, origin, bad, start):
    """After an in-page navigation: a different same-site page that really rendered."""
    page.wait_for_function(
        "s => (location.pathname.replace(/\\/+$/, '') || '/') !== s", arg=start, timeout=NAV_MS)
    page.wait_for_function(
        "() => { const h = document.querySelector('h1');"
        " return !!h && h.innerText.trim().length > 1"
        " && document.body.innerText.trim().length > 50; }", timeout=NAV_MS)
    return _same_site(page, origin) and _path(page) not in bad


def _back(page, start):
    page.go_back(wait_until="domcontentloaded", timeout=NAV_MS)
    page.wait_for_function(
        "s => (location.pathname.replace(/\\/+$/, '') || '/') === s", arg=start, timeout=NAV_MS)


def _put(page, selector, value):
    """Fill a field and confirm it kept the value (pages may move focus briefly)."""
    for _ in range(3):
        page.fill(selector, value)
        page.wait_for_timeout(150)
        if page.input_value(selector) == value:
            return
    raise ValueError("field")


# State of the result list: number of cards, the number the heading announces,
# values of the filter boxes and how many "active filter" tags are shown.
LIST = """() => {
  const cards = document.querySelectorAll("[data-testid='provider-card']").length;
  const heads = [...document.querySelectorAll('main h3')];
  const m = heads.length ? heads[heads.length - 1].innerText.match(/\\d+/) : null;
  const boxes = [...document.querySelectorAll('main select')];
  const panel = boxes.length ? boxes[0].closest('main > *') : null;
  return { cards, told: m ? Number(m[0]) : -1, picked: boxes.map(b => b.value),
           tags: panel ? panel.querySelectorAll('button').length : -1 };
}"""
SETTLED = "(a) => { const s = (" + LIST + ")(); return s.cards === s.told && s.tags === a[0] && s.picked[a[1]] === a[2]; }"
OPTIONS = "i => [...document.querySelectorAll('main select')[i].options].filter(o => o.value).map(o => [o.value, o.textContent.trim()])"


def _list_ready(page):
    page.locator(CARD).first.wait_for(state="visible", timeout=NAV_MS)
    page.wait_for_function("() => { const s = (" + LIST + ")(); return s.cards >= 1 && s.cards === s.told && s.tags === 0; }")
    return page.evaluate(LIST)["cards"]


def _clear_tag(page, index, total):
    """Remove the single active filter through its tag; the full list returns."""
    page.locator("main select").first.locator("xpath=ancestor::*[parent::main]").locator("button").first.click()
    page.wait_for_function(
        "(a) => { const s = (" + LIST + ")(); return s.tags === 0 && s.picked[a[0]] === '' && s.cards === a[1] && s.told === a[1]; }",
        arg=[index, total])


@flow
def browse(page, origin, path):
    """The list shows real entries, its count is truthful, and a filter splits it."""
    if not _open(page, origin, path):
        return False
    total = _list_ready(page)
    choices = page.evaluate(OPTIONS, 1)
    if len(choices) < 2:
        return False
    box = page.locator("main select").nth(1)
    shown = 0
    for value, _label in choices:
        box.select_option(value)
        page.wait_for_function(SETTLED, arg=[1, 1, value])
        page.wait_for_timeout(250)
        state = page.evaluate(LIST)
        if state["cards"] != state["told"] or state["cards"] > total:
            return False
        shown += state["cards"]
    # The choices are mutually exclusive: together they cannot exceed the full list.
    if shown > total:
        return False
    _clear_tag(page, 1, total)
    return True


@flow
def category(page, origin, path):
    """Choosing a category reloads the list with entries of that category only."""
    if not _open(page, origin, path):
        return False
    total = _list_ready(page)
    choices = page.evaluate(OPTIONS, 0)
    if not choices:
        return False
    box = page.locator("main select").first
    matched = False
    for value, label in choices[:6]:
        box.select_option(value)
        page.wait_for_function(
            """(a) => { const s = (""" + LIST + """)();
              const lines = [...document.querySelectorAll("[data-testid='provider-card'] h4 + p")];
              return s.picked[0] === a[0] && s.tags === 1 && s.cards === s.told
                && lines.length === s.cards && lines.every(p => p.innerText.trim().startsWith(a[1])); }""",
            arg=[value, label])
        page.wait_for_timeout(400)
        state = page.evaluate(LIST)
        if state["cards"] != state["told"]:
            return False
        if state["cards"] >= 1:
            matched = True
            break
    if not matched:
        return False
    _clear_tag(page, 0, total)
    return True


@flow
def home(page, origin, path):
    """The search box takes text (never sent) and the sign-in entry leads to the sign-in page."""
    bad = _watch(page, origin)
    if not _open(page, origin, path):
        return False
    start = _path(page)
    box = "[data-testid='home-search-input']"
    page.locator(box).wait_for(state="visible")
    _put(page, box, "[QA-TEST]")
    _put(page, box, "")
    entry = page.locator("[data-testid='nav-login-btn']")
    entry.wait_for(state="visible")
    entry.click()
    if not _arrive(page, origin, bad, start):
        return False
    page.locator(SIGN_IN).wait_for(state="visible")
    if not page.locator(SIGN_IN).is_enabled():
        return False
    _back(page, start)
    page.locator(box).wait_for(state="visible")
    return page.input_value(box) == "" and _path(page) == start


@flow
def site_links(page, origin, path):
    """Every footer link opens its own page and browser back returns."""
    bad = _watch(page, origin)
    if not _open(page, origin, path):
        return False
    start = _path(page)
    links = page.locator("footer button")
    links.first.wait_for(state="attached")
    count = min(links.count(), 8)
    if count < 3:
        return False
    visited = set()
    for index in range(count):
        link = page.locator("footer button").nth(index)
        link.scroll_into_view_if_needed()
        link.click()
        if not _arrive(page, origin, bad, start):
            return False
        visited.add(_path(page))
        _back(page, start)
        page.locator("footer button").first.wait_for(state="attached")
    return len(visited) == count


TABS = "() => [...document.querySelectorAll('main > *')].findIndex(e => e.querySelectorAll(':scope > button').length === 2)"
GROUP = """() => { const n = document.querySelector('main input[type=number]');
  const card = n ? n.closest('main > *') : null;
  return card && card.firstElementChild ? [...card.firstElementChild.querySelectorAll('button')] : []; }"""


@flow
def search_modes(page, origin, path):
    """The request box enables sending only with text; the second mode loads its choices."""
    if not _open(page, origin, path):
        return False
    page.locator(ASK).wait_for(state="visible")
    send = page.locator(ASK_SEND)
    if not send.is_disabled():
        return False
    _put(page, ASK, "[QA-TEST]")
    page.wait_for_function("s => !document.querySelector(s).disabled", arg=ASK_SEND)
    _put(page, ASK, "")
    page.wait_for_function("s => document.querySelector(s).disabled", arg=ASK_SEND)
    index = page.evaluate(TABS)
    if index < 0:
        return False
    tabs = page.locator("main > *").nth(index).locator(":scope > button")
    tabs.nth(1).click()
    page.locator("main input[type=number]").wait_for(state="visible")
    if page.locator(ASK).count() != 0:
        return False
    # Choices arrive from the site; picking the first one marks it as chosen.
    page.wait_for_function("() => (" + GROUP + ")().length >= 1")
    before = page.evaluate("() => (" + GROUP + ")()[0].className")
    page.evaluate("() => (" + GROUP + ")()[0].click()")
    page.wait_for_function("b => (" + GROUP + ")()[0].className !== b", arg=before)
    tabs.nth(0).click()
    page.locator(ASK).wait_for(state="visible")
    return page.locator("main input[type=number]").count() == 0 and send.is_disabled()


FIELDS = """() => ['name', 'slogan', 'phone'].map(k => {
  const e = document.querySelector("[data-testid='identity-" + k + "-input']");
  return e ? e.getAttribute('aria-invalid') : null; }).join(',')"""


@flow
def join_guard(page, origin, path):
    """The first joining step refuses empty and incomplete input on the page.

    The phone field stays too short throughout, so the step is never valid.
    """
    if not _open(page, origin, path):
        return False
    start = page.url
    manual = page.locator("[data-testid='onboarding-lander-no']")
    manual.wait_for(state="visible", timeout=NAV_MS)
    manual.click()
    page.locator("[data-testid='identity-name-input']").wait_for(state="visible")
    go = page.locator("[data-testid='identity-continue-btn']")
    done = "[data-testid='identity-success-screen']"

    def refused(expected):
        go.click()
        page.wait_for_function("w => (" + FIELDS + ")() === w", arg=expected)
        page.wait_for_timeout(500)
        return (page.url == start and page.locator(done).count() == 0
                and page.locator("[data-testid='identity-name-input']").is_visible()
                and page.evaluate(FIELDS) == expected)

    if not refused("true,true,true"):
        return False
    _put(page, "[data-testid='identity-name-input']", "[QA-TEST] Bot")
    _put(page, "[data-testid='identity-slogan-input']", "[QA-TEST]")
    _put(page, "[data-testid='identity-phone-input']", "0500")
    return refused("false,false,true")


@flow
def gate(page, origin, path):
    """A members-only page sends an anonymous visitor to the sign-in page, which works."""
    if not _open(page, origin, path):
        return False
    wanted = _path(page)
    page.wait_for_function(
        "s => (location.pathname.replace(/\\/+$/, '') || '/') !== s", arg=wanted, timeout=NAV_MS)
    page.locator(SIGN_IN).wait_for(state="visible")
    here = _path(page)
    if here == wanted or not _same_site(page, origin) or not page.locator(SIGN_IN).is_enabled():
        return False
    links = page.locator("a[href^='/']")
    if links.count() < 2:
        return False
    with page.expect_navigation(wait_until="domcontentloaded", timeout=NAV_MS) as nav:
        links.first.click()
    resp = nav.value
    if not (resp and resp.status == 200 and _same_site(page, origin)) or _path(page) in (here, wanted):
        return False
    page.wait_for_function(
        "() => !!document.querySelector('h1') && document.body.innerText.trim().length > 200")
    _back(page, here)
    page.locator(SIGN_IN).wait_for(state="visible")
    return True


@flow
def report_box(page, origin, path):
    """The floating report box opens, takes text and closes. Never sent."""
    if not _open(page, origin, path):
        return False
    start = page.url
    opener = page.locator("body > button[aria-label]")
    opener.wait_for(state="visible")
    box = page.locator("body > div").filter(has=page.locator("textarea"))
    for _ in range(2):
        opener.click()
        box.locator("textarea").wait_for(state="visible")
        if box.locator("button").count() < 3:
            return False
        box.locator("textarea").fill("[QA-TEST]")
        if box.locator("textarea").input_value() != "[QA-TEST]":
            return False
        box.locator("button").first.click()  # the close control in the box header
        box.locator("textarea").wait_for(state="detached")
    return page.url == start and opener.is_visible()
