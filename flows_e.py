"""Anonymous-visitor flows for target E.

Each flow drives public pages the way a visitor would and returns True/False.
Rules (do not weaken):
  * Nothing is ever submitted. A send button is pressed only on input the page
    itself must refuse. The conversation box is never used.
    The engine's request guard stays in force.
  * No output of any kind from this module: no prints, no page text, no errors.
  * Data-agnostic: always "the first real option", never a named item.
  * Inputs are obviously synthetic.
"""
from urllib.parse import urlparse

NAV_MS = 20000
WAIT_MS = 10000
FLOWS = {}

HEADLINE = """() => { const h = document.querySelector('h1');
  return !!h && (h.offsetWidth || h.offsetHeight) && h.innerText.trim().length > 3; }"""
DIGIT = "s => /\\d/.test((document.querySelector(s) || {}).innerText || '')"
TEXT = "s => ((document.querySelector(s) || {}).innerText || '').trim()"


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


def _home(page, origin, path):
    """Open the home page and wait until its wording is in."""
    if not _open(page, origin, path):
        return False
    page.wait_for_function(HEADLINE)
    return True


def _close_helper(page):
    """The floating helper slides in a little after load; close it as a visitor would."""
    close = page.locator("#lav .lav-close")
    try:
        close.wait_for(state="visible", timeout=4500)
    except Exception:
        return
    close.click()
    close.wait_for(state="hidden")


@flow
def cta(page, origin, path):
    """The main call to action opens the first setup step; browser back returns."""
    if not _home(page, origin, path):
        return False
    start = _path(page)
    _close_helper(page)
    button = page.locator("#hero-cta")
    if not button.is_visible() or page.evaluate(TEXT, "#hero-cta") == "":
        return False
    if not _follow(page, origin, button):
        return False
    if _path(page) == start:
        return False
    page.wait_for_function(
        "() => { const m = document.getElementById('start-step');"
        " return !!m && m.querySelectorAll('button').length >= 2"
        " && !!m.querySelector('h1') && m.querySelector('h1').innerText.trim().length > 3; }")
    page.go_back(wait_until="domcontentloaded", timeout=NAV_MS)
    page.wait_for_function(HEADLINE)
    return _path(page) == start and page.locator("#hero-cta").is_visible()


@flow
def content(page, origin, path):
    """The home page is fully worded and its demo panel is ready to use."""
    crashed = []
    page.on("pageerror", lambda _e: crashed.append(1))
    if not _home(page, origin, path):
        return False
    # Every shown label carries real wording: none empty, none still a placeholder name.
    page.wait_for_function("""() => {
      const shown = [...document.querySelectorAll('[data-i18n]')]
        .filter(e => e.offsetWidth || e.offsetHeight);
      return shown.length >= 20 && shown.every(e => {
        const s = e.textContent.trim();
        return s.length > 0 && s !== e.getAttribute('data-i18n');
      });
    }""")
    root = page.evaluate(
        "() => ({ lang: document.documentElement.lang, dir: document.documentElement.dir,"
        " title: document.title.trim() })")
    if root["lang"][:2] != "he" or root["dir"] != "rtl" or len(root["title"]) < 3:
        return False
    answers = page.eval_on_selector_all(
        ".faq-item",
        "els => els.filter(e => e.querySelector('h3').innerText.trim().length > 3"
        " && e.querySelector('p').innerText.trim().length > 10).length")
    if answers < 3 or answers != page.locator(".faq-item").count():
        return False
    # Demo panel: an opening line, suggested questions, and an open text box.
    page.wait_for_function(
        "() => document.querySelectorAll('#demo-thread > *').length >= 1"
        " && document.querySelector('#demo-thread').innerText.trim().length > 3"
        " && document.querySelectorAll('#demo-chips button').length >= 2")
    box = page.locator("#demo-ask-input")
    if not (box.is_visible() and box.is_editable() and page.locator("#demo-ask-send").is_visible()):
        return False
    if _shown(page, "footer a[href]") < 3:
        return False
    return not crashed


@flow
def calc(page, origin, path):
    """The savings estimate reacts to its sliders and explains itself."""
    if not _home(page, origin, path):
        return False
    page.locator("#calc").wait_for(state="visible")
    page.wait_for_function(DIGIT, arg="#calc-hours")
    _close_helper(page)
    figures = """() => ['calc-hours', 'calc-value', 'calc-price']
                 .map(i => document.getElementById(i).innerText.trim()).join('|')"""
    page.wait_for_timeout(700)
    before = page.evaluate(figures)
    slider = page.locator("#calc-new")
    slider.scroll_into_view_if_needed()
    slider.focus()
    start_value = slider.input_value()
    page.keyboard.press("End")
    if slider.input_value() == start_value:
        page.keyboard.press("Home")
    moved = slider.input_value()
    if moved == start_value:
        return False
    if page.evaluate(TEXT, "#calc-new-v") != moved:
        return False
    page.wait_for_function(
        "a => (" + figures + ")() !== a && /\\d/.test(document.getElementById('calc-hours').innerText)",
        arg=before)
    page.wait_for_timeout(700)
    after = page.evaluate(figures)
    if after.split("|")[0] == before.split("|")[0]:
        return False
    if page.evaluate(TEXT, "#calc-note") != "":
        return False
    # The "how is this worked out" control opens its explanation and closes it again.
    info, note = page.locator("#calc-how-btn"), page.locator("#calc-how")
    info.click()
    note.wait_for(state="visible")
    if info.get_attribute("aria-expanded") != "true" or len(page.evaluate(TEXT, "#calc-how")) < 10:
        return False
    info.click()
    note.wait_for(state="hidden")
    return info.get_attribute("aria-expanded") == "false"


@flow
def offer(page, origin, path):
    """Choosing an add-on changes the quoted total; undoing the choice restores it."""
    if not _home(page, origin, path):
        return False
    page.locator("#pricing-card").wait_for(state="visible")
    page.wait_for_function(DIGIT, arg="#capability-total")
    _close_helper(page)
    boxes = "#offer-caps input[type=checkbox]"
    if page.locator(boxes).count() < 1:
        return False
    state = """() => document.getElementById('capability-total').innerText.trim() + '/'
                 + [...document.querySelectorAll('#offer-caps input[type=checkbox]')]
                     .map(b => b.checked ? 1 : 0).join('')"""
    # Let the first quote settle (the total counts up to its value).
    page.wait_for_timeout(1200)
    before = page.evaluate(state)
    index = page.eval_on_selector_all(boxes, "els => Math.max(0, els.findIndex(b => !b.checked))")
    box = page.locator(boxes).nth(index)
    was_on = box.is_checked()
    box.scroll_into_view_if_needed()
    box.click()
    page.wait_for_function(
        "a => { const s = (" + state + ")(); return s.split('/')[0] !== a.split('/')[0]"
        " && s.split('/')[1] !== a.split('/')[1] && /\\d/.test(s); }", arg=before)
    if box.is_checked() == was_on:
        return False
    marked = page.eval_on_selector_all(
        "#offer-caps .loffer-cap",
        "els => els.every(c => c.classList.contains('on') === c.querySelector('input').checked)")
    if marked is not True:
        return False
    listed = page.locator("#offer-wins-list li").count()
    if listed != page.eval_on_selector_all(boxes, "els => els.filter(b => b.checked).length"):
        return False
    # Way back: the page's own undo when it offers one, otherwise the same box again.
    undo = page.locator("#offer-undo")
    if undo.is_visible():
        undo.click()
    else:
        box.click()
    page.wait_for_function("a => (" + state + ")() === a", arg=before)
    page.wait_for_timeout(700)
    return page.evaluate(state) == before


@flow
def legal(page, origin, path):
    """Each footer document opens as a readable page and browser back returns."""
    if not _home(page, origin, path):
        return False
    start = _path(page)
    links = "footer nav a[href]"
    total = page.locator(links).count()
    if total < 2:
        return False
    seen = set()
    _close_helper(page)
    for index in range(total):
        link = page.locator(links).nth(index)
        link.scroll_into_view_if_needed()
        if not _follow(page, origin, link):
            return False
        if _path(page) == start or _path(page) in seen:
            return False
        seen.add(_path(page))
        page.wait_for_function(
            "() => { const h = document.querySelector('h1');"
            " return !!h && h.innerText.trim().length > 3"
            " && document.body.innerText.trim().length > 400; }")
        page.go_back(wait_until="domcontentloaded", timeout=NAV_MS)
        page.wait_for_function(HEADLINE)
        if _path(page) != start:
            return False
    return len(seen) == total


STEP = "() => document.getElementById('start-step').getAttribute('data-step') || ''"
CHOSEN = """() => document.querySelectorAll('#start-step button.opt[aria-pressed="true"]').length"""


def _first_choice(page):
    """First setup step: pick the first option and continue to the next step."""
    choice = page.locator("#start-step button.opt").first
    choice.wait_for(state="visible")
    first = page.evaluate(STEP)
    if first == "" or page.evaluate(CHOSEN) != 0:
        return None
    # No way forward is offered before a choice is made.
    if _shown(page, "#start-next") != 0:
        return None
    choice.click()
    page.wait_for_function("() => (" + CHOSEN + ")() === 1")
    forward = page.locator("#start-next")
    forward.wait_for(state="visible")
    if not forward.is_enabled():
        return None
    forward.click()
    page.wait_for_function("a => (" + STEP + ")() !== a && (" + STEP + ")() !== ''", arg=first)
    return first


@flow
def wizard(page, origin, path):
    """Setup steps move forward, follow browser back/forward and survive a reload."""
    if not _open(page, origin, path):
        return False
    first = _first_choice(page)
    if first is None:
        return False
    second = page.evaluate(STEP)
    marker = """() => [...document.querySelectorAll('#start-prog button')]
                  .findIndex(b => b.getAttribute('aria-current') === 'step')"""
    if page.evaluate(marker) != 1:
        return False
    # Nothing was entered on the second step, so its main button stays unavailable.
    if page.locator("#start-next").is_enabled():
        return False
    page.go_back()
    page.wait_for_function("a => (" + STEP + ")() === a", arg=first)
    if page.evaluate(CHOSEN) != 1 or page.evaluate(marker) != 0:
        return False
    page.go_forward()
    page.wait_for_function("a => (" + STEP + ")() === a", arg=second)
    page.reload(wait_until="domcontentloaded", timeout=NAV_MS)
    page.wait_for_function("a => (" + STEP + ")() === a", arg=second)
    page.wait_for_function(
        "() => { const h = document.querySelector('#start-step h1');"
        " return !!h && h.innerText.trim().length > 3; }")
    return _path(page) == (urlparse(origin + path).path.rstrip("/") or "/") and page.evaluate(marker) == 1


@flow
def wizard_guard(page, origin, path):
    """The details form refuses incomplete and malformed input on the page.

    The agreement box stays unticked and one required field stays empty throughout,
    so the form is never valid.
    """
    if not _open(page, origin, path):
        return False
    if _first_choice(page) is None:
        return False
    page.locator("#throw-skip").click()
    page.locator("#w-email").wait_for(state="visible")
    form_step = page.evaluate(STEP)
    start = page.url
    agree = page.locator("#w-consent")
    if agree.count() != 1 or agree.is_checked():
        return False
    _put(page, "#w-name", "[QA-TEST] Bot")
    _put(page, "#w-biz", "")
    _put(page, "#w-email", "qa-bot-at-example.invalid")
    button = page.locator("#start-next")
    if not button.is_enabled():
        # The page keeps its button unavailable until the form is complete.
        return page.evaluate(STEP) == form_step and not agree.is_checked()

    def refused():
        button.click()
        page.locator("#start-step .field-err").first.wait_for(state="visible")
        page.wait_for_timeout(400)
        return (page.url == start and page.evaluate(STEP) == form_step
                and _shown(page, "#start-step .reward") == 0)

    if not refused():
        return False
    if page.get_attribute("#w-email", "aria-invalid") != "true":
        return False
    if page.get_attribute("#w-biz", "aria-invalid") != "true":
        return False
    if page.get_attribute("#w-name", "aria-invalid") == "true":
        return False
    count = _shown(page, "#start-step .field-err")
    if count < 3:
        return False
    # Address corrected: its message goes, the others stay, the form is still refused.
    _put(page, "#w-email", "qa-bot@example.invalid")
    if not refused():
        return False
    if page.get_attribute("#w-email", "aria-invalid") == "true":
        return False
    return _shown(page, "#start-step .field-err") == count - 1 and not agree.is_checked()


@flow
def login_guard(page, origin, path):
    """The sign-in form refuses an empty and a malformed address on the page."""
    if not _open(page, origin, path):
        return False
    send = page.locator("#login-send")
    page.wait_for_function(
        "() => { const b = document.getElementById('login-send'), c = document.getElementById('login-card');"
        " return !!b && !!c && (c.offsetWidth || c.offsetHeight) && b.innerText.trim().length > 1; }")
    start = page.url
    if _shown(page, "#login-code-step") != 0:
        return False

    def still_first_step():
        page.wait_for_timeout(400)
        return (page.url == start and _shown(page, "#login-code-step") == 0
                and send.is_enabled() and page.locator("#login-card").is_visible())

    # 1. Nothing typed.
    _put(page, "#login-email", "")
    send.click()
    page.wait_for_function(
        "() => { const n = document.getElementById('login-note');"
        " return n.innerText.trim().length > 3 && n.classList.contains('error'); }")
    if not still_first_step():
        return False
    # 2. Not an address.
    _put(page, "#login-email", "qa-bot-at-example.invalid")
    send.click()
    page.wait_for_function(
        "() => document.getElementById('login-email').getAttribute('aria-invalid') === 'true'")
    if _shown(page, "#login-form .field-err") != 1:
        return False
    return still_first_step()


@flow
def contact(page, origin, path):
    """The "talk to a person" link opens its form, which refuses bad input and closes."""
    if not _home(page, origin, path):
        return False
    link = page.locator("#human-link")
    if not link.is_visible() or not _follow(page, origin, link):
        return False
    box = page.locator("#human-help-modal")
    box.wait_for(state="visible")
    start = page.url
    send, note = page.locator("#human-help-submit"), "#human-help-note"
    # 1. Empty form.
    send.click()
    page.wait_for_function(
        "s => { const n = document.querySelector(s);"
        " return n.innerText.trim().length > 3 && n.classList.contains('error'); }", arg=note)
    # 2. A name and a number that is far too short.
    _put(page, "#human-help-name", "[QA-TEST] Bot")
    _put(page, "#human-help-phone", "05")
    send.click()
    page.wait_for_function(
        "() => document.getElementById('human-help-phone').getAttribute('aria-invalid') === 'true'")
    page.wait_for_timeout(400)
    if _shown(page, "#human-help-modal .field-err") != 1 or not box.is_visible():
        return False
    if page.url != start or not send.is_enabled():
        return False
    if page.input_value("#human-help-name") != "[QA-TEST] Bot":
        return False
    page.locator("#human-help-cancel").click()
    box.wait_for(state="hidden")
    # The setup page underneath is intact.
    return page.locator("#start-step button.opt").first.is_visible()
