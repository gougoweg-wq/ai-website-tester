"""AI Website Tester — opens a website in a real browser, clicks things, fills forms,
finds problems, asks an AI to explain them, and writes an HTML report with screenshots.

Usage:
  python tester/ai_tester.py                      # tests ./website (starts a local server)
  python tester/ai_tester.py --url https://...    # tests any site
  python tester/ai_tester.py --name before --no-ai
"""
import argparse
import datetime as dt
import functools
import http.server
import json
import re
import socketserver
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

from playwright.sync_api import sync_playwright, Error as PWError

sys.path.insert(0, str(Path(__file__).parent))
from ai import AI  # noqa: E402
import report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- browser-side helpers (JavaScript)

JS_VISIBLE = """const vis = e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e);
  return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };"""

# Lists clickable controls that are NOT inside a form (forms are tested separately).
JS_CONTROLS = "() => {" + JS_VISIBLE + """
  return [...document.querySelectorAll('button, [role=button], input[type=button], select')]
    .filter(e => !e.closest('form') && vis(e) && !e.disabled)
    .map(e => {
      const isSel = e.tagName === 'SELECT';
      const text = (isSel ? (e.getAttribute('aria-label') || e.id || e.name)
                          : (e.innerText || e.value || e.getAttribute('aria-label') || '')).trim().slice(0, 60);
      return { kind: isSel ? 'select' : 'button', text,
               group: e.tagName + '|' + text + '|' + (e.className || ''), html: e.outerHTML.slice(0, 300) };
    });
}"""

# Puts data-qa-target on the n-th control of a group so Playwright can click it.
JS_MARK = "([group, nth]) => {" + JS_VISIBLE + """
  document.querySelectorAll('[data-qa-target]').forEach(e => e.removeAttribute('data-qa-target'));
  const els = [...document.querySelectorAll('button, [role=button], input[type=button], select')]
    .filter(e => !e.closest('form') && vis(e) && !e.disabled)
    .filter(e => {
      const isSel = e.tagName === 'SELECT';
      const text = (isSel ? (e.getAttribute('aria-label') || e.id || e.name)
                          : (e.innerText || e.value || e.getAttribute('aria-label') || '')).trim().slice(0, 60);
      return e.tagName + '|' + text + '|' + (e.className || '') === group;
    });
  if (!els[nth]) return false;
  els[nth].setAttribute('data-qa-target', '1');
  return true;
}"""

# Starts counting DOM changes so we can tell whether an action "did anything".
JS_ARM = """() => {
  window.__qaMut = 0;
  new MutationObserver(ms => { for (const m of ms) {
    if (m.type === 'attributes' && (m.attributeName || '').startsWith('data-qa')) continue;
    window.__qaMut++; } })
  .observe(document.documentElement, { subtree: true, childList: true, attributes: true, characterData: true });
}"""
JS_STATE = """() => ({ mut: window.__qaMut === undefined ? -1 : window.__qaMut, url: location.href,
  ls: JSON.stringify(Object.fromEntries(Object.entries(localStorage))), y: Math.round(scrollY) })"""

JS_FORMS = "() => {" + JS_VISIBLE + """
  return [...document.forms].map((f, i) => ({ index: i, visible: vis(f), id: f.id, role: f.getAttribute('role'),
    html: f.outerHTML.slice(0, 1500),
    fields: [...f.elements].filter(e => !['hidden','submit','button','reset','image'].includes(e.type)
                                      && e.tagName !== 'BUTTON' && e.tagName !== 'FIELDSET')
      .map(e => { const key = e.name || e.id;
        const lab = (e.id && document.querySelector(`label[for="${e.id}"]`)) || e.closest('label');
        return { key, tag: e.tagName.toLowerCase(), type: e.type, label: ((lab && lab.innerText) ||
          e.getAttribute('aria-label') || e.placeholder || key || '').trim(), required: e.required,
          minlength: e.minLength > 0 ? e.minLength : null, maxlength: e.maxLength > 0 ? e.maxLength : null,
          pattern: e.pattern || null, min: e.min || null, max: e.max || null, placeholder: e.placeholder || null,
          options: e.tagName === 'SELECT' ? [...e.options].map(o => o.value) : null }; }) }));
}"""

JS_OUTCOME = "(i) => {" + JS_VISIBLE + """
  const f = document.forms[i];
  const errors = [...document.querySelectorAll('[role=alert], .error, .errors, .invalid-feedback, .field-error')]
    .filter(e => vis(e) && e.innerText.trim()).map(e => e.innerText.trim());
  const ariaInvalid = [...document.querySelectorAll('[aria-invalid=true]')].map(e => e.name || e.id);
  const nativeInvalid = f && !f.noValidate ? [...f.elements].filter(e => e.willValidate && !e.checkValidity())
    .map(e => e.name || e.id) : [];
  return { url: location.href, text: document.body.innerText, formVisible: f ? vis(f) : false,
           errors, ariaInvalid, nativeInvalid };
}"""

DATE_IN = lambda days: (dt.date.today() + dt.timedelta(days=days)).isoformat()  # noqa: E731


# ---------------------------------------------------------------- local server for ./website

def start_local_server(folder):
    handler = functools.partial(QuietHandler, directory=str(folder))
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}/index.html"


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- the tester

class Tester:
    def __init__(self, start_url, out_dir, ai, max_pages=15, video=True):
        self.start_url, self.out, self.ai = start_url, out_dir, ai
        self.origin = "{0.scheme}://{0.netloc}".format(urlparse(start_url))
        self.max_pages, self.video = max_pages, video
        self.findings, self.log = [], []
        self.page_texts, self.page_ids, self.links = {}, {}, {}
        self.shot_n = 0
        self.errors_seen = []  # JS errors, appended by event listeners
        (out_dir / "shots").mkdir(parents=True, exist_ok=True)

    # ---------- small helpers
    def path(self, url):
        p = urlparse(url)
        return (p.path or "/") + (("?" + p.query) if p.query else "")

    def record(self, page_url, check, target, result, note=""):
        self.log.append({"page": self.path(page_url), "check": check, "target": target,
                         "result": result, "note": note})
        mark = {"pass": "✓", "FAIL": "✗", "skip": "–"}.get(result, "?")
        print(f"   {mark} {check}: {target} {('— ' + note) if note else ''}")

    def add_finding(self, **f):
        f["id"] = f"F{len(self.findings) + 1}"
        f.setdefault("screenshots", [])
        self.findings.append(f)
        print(f"   >>> FINDING {f['id']} [{f['severity']}] {f['title']}")
        return f

    def shot(self, page, label, locator=None):
        """Screenshot with the element of interest outlined in red."""
        self.shot_n += 1
        name = f"shots/{self.shot_n:02d}.jpg"
        try:
            if locator is not None and locator.count():
                locator.first.scroll_into_view_if_needed(timeout=2000)
                locator.first.evaluate("e => { e.dataset.qaOld = e.style.outline; "
                                       "e.style.outline = '4px solid #e00'; e.style.outlineOffset = '3px'; }")
            page.screenshot(path=str(self.out / name), type="jpeg", quality=70)
            if locator is not None and locator.count():
                locator.first.evaluate("e => { e.style.outline = e.dataset.qaOld || ''; "
                                       "e.style.outlineOffset = ''; delete e.dataset.qaOld; }")
        except PWError:
            page.screenshot(path=str(self.out / name), type="jpeg", quality=70)
        return {"file": name, "caption": label}

    def storage(self, page):
        return page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")

    def restore(self, page, url, snapshot):
        """Put localStorage back to a known state and reload, so every test starts the same way."""
        page.evaluate("s => { localStorage.clear(); for (const k in s) localStorage.setItem(k, s[k]); }", snapshot)
        page.goto(url, wait_until="load")

    def new_errors(self, since):
        return self.errors_seen[since:]

    # ---------- main loop
    def run(self):
        t0 = time.time()
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx_args = {"viewport": {"width": 1280, "height": 800}}
            if self.video:
                ctx_args["record_video_dir"] = str(self.out / "video")
                ctx_args["record_video_size"] = {"width": 1280, "height": 800}
            ctx = browser.new_context(**ctx_args)
            page = ctx.new_page()
            page.on("pageerror", lambda e: self.errors_seen.append(
                f"Uncaught {e.name}: {e.message}" + "\n" + "\n".join((e.stack or "").splitlines()[1:3])))
            page.on("console", lambda m: m.type == "error" and self.errors_seen.append("console.error: " + m.text))
            page.on("dialog", lambda d: d.dismiss())
            self.failed_resources = []
            page.on("response", lambda r: r.status >= 400 and not r.url.endswith("favicon.ico")
                    and r.request.resource_type != "document" and self.failed_resources.append((r.url, r.status)))

            queue, seen = [urldefrag(self.start_url)[0]], set()
            while queue and len(seen) < self.max_pages:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                for link in self.test_page(page, url):
                    if link not in seen and link not in queue:
                        queue.append(link)

            self.check_links(ctx, page)
            self.ai_content_review(page)
            video_path = page.video.path() if self.video and page.video else None
            ctx.close()
            browser.close()
        self.video_file = None
        if video_path:
            target = self.out / "test-run.webm"
            Path(video_path).rename(target)
            self.video_file = target.name
        self.duration = round(time.time() - t0, 1)

    # ---------- one page
    def test_page(self, page, url):
        print(f"\n== Page {self.path(url)}")
        err_mark, res_mark = len(self.errors_seen), len(self.failed_resources)
        try:
            resp = page.goto(url, wait_until="load")
        except PWError as e:
            self.record(url, "page loads", url, "FAIL", str(e)[:120])
            return []
        status = resp.status if resp else 0
        if status >= 400:
            self.record(url, "page loads", self.path(url), "FAIL", f"HTTP {status}")
            return []
        self.record(url, "page loads", self.path(url), "pass", f"HTTP {status}")
        page.wait_for_timeout(300)

        # JS errors and missing files while loading
        load_errors = self.new_errors(err_mark)
        if load_errors:
            self.add_finding(type="js-error", severity="high", page=self.path(url), key=f"js-load:{self.path(url)}",
                             title=f"JavaScript error when {self.path(url)} loads",
                             details={"errors": load_errors},
                             steps=[f"Open {self.path(url)}"], screenshots=[self.shot(page, "Page after load")])
        self.record(url, "no JS errors on load", self.path(url), "FAIL" if load_errors else "pass")
        for res_url, code in self.failed_resources[res_mark:]:
            self.add_finding(type="missing-resource", severity="medium", page=self.path(url),
                             key=f"resource:{self.path(res_url)}", title=f"File {self.path(res_url)} returns HTTP {code}",
                             details={"resource": res_url, "status": code}, steps=[f"Open {self.path(url)}"])

        # Broken images
        broken_imgs = page.evaluate("() => [...document.images].filter(i => i.complete && i.naturalWidth === 0)"
                                    ".map(i => i.getAttribute('src'))")
        for src in broken_imgs:
            self.add_finding(type="broken-image", severity="medium", page=self.path(url), key=f"img:{src}",
                             title=f"Image does not load: {src}", details={"src": src},
                             steps=[f"Open {self.path(url)}"],
                             screenshots=[self.shot(page, "Broken image", page.locator(f'img[src="{src}"]'))])
        self.record(url, "images load", f"{page.locator('img').count()} image(s)", "FAIL" if broken_imgs else "pass")

        # Remember page text + element ids (used for anchor checks and AI review)
        self.page_texts[url] = page.inner_text("body")
        self.page_ids[url] = set(page.evaluate("() => [...document.querySelectorAll('[id],[name]')]"
                                               ".map(e => e.id || e.getAttribute('name'))"))
        self.content_checks(page, url)

        # Links (checked later in bulk)
        anchors = page.evaluate("() => [...document.querySelectorAll('a[href]')].map(a => "
                                "({href: a.getAttribute('href'), text: a.innerText.trim(), html: a.outerHTML}))")
        new_pages = []
        for a in anchors:
            href = a["href"]
            if href.startswith(("mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(url, href)
            self.links.setdefault(absolute, []).append({"page": url, **a})
            base = urldefrag(absolute)[0]
            if base.startswith(self.origin) and base not in new_pages:
                new_pages.append(base)

        start_state = self.storage(page)
        best_state = self.test_controls(page, url, start_state)
        self.test_forms(page, url, start_state)
        # Keep the "richest" state reached (e.g. items added to the cart) so later pages have data to show
        self.restore(page, url, best_state)
        return new_pages

    # ---------- content checks (math + placeholder text)
    def content_checks(self, page, url):
        text = self.page_texts[url]
        bad = re.findall(r"\b(undefined|NaN)\b|\[object Object\]", text)
        if bad:
            self.add_finding(type="content", severity="medium", page=self.path(url), key=f"placeholder:{self.path(url)}",
                             title=f"Page shows broken placeholder text ({', '.join(sorted(set(b or '[object Object]' for b in bad)))})",
                             details={"matches": bad[:5]}, steps=[f"Open {self.path(url)}"],
                             screenshots=[self.shot(page, "Placeholder text")])
        amounts = {}
        for label, minus, value in re.findall(r"(?m)^\s*(Subtotal|Shipping|Tax|Discount|Total)\b[:\s]*(-?)\s*\$\s?([\d,]+\.\d{2})", text):
            amounts.setdefault(label, float(value.replace(",", "")) * (-1 if minus else 1))
        if "Total" in amounts and "Subtotal" in amounts:
            expected = (amounts["Subtotal"] + amounts.get("Shipping", 0) + amounts.get("Tax", 0)
                        - abs(amounts.get("Discount", 0)))
            ok = abs(expected - amounts["Total"]) < 0.01
            parts = " + ".join(f"{k} ${v:.2f}" for k, v in amounts.items() if k != "Total")
            self.record(url, "totals add up", parts + f" = Total ${amounts['Total']:.2f}", "pass" if ok else "FAIL",
                        "" if ok else f"expected ${expected:.2f}")
            if not ok:
                self.add_finding(type="calculation", severity="high", page=self.path(url), key=f"math:{self.path(url)}",
                                 title=f"Order total is wrong: shows ${amounts['Total']:.2f}, expected ${expected:.2f}",
                                 details={"amounts": amounts, "expected_total": round(expected, 2)},
                                 steps=[f"Add a product to the cart", f"Open {self.path(url)}",
                                        "Compare the subtotal + shipping with the total"],
                                 screenshots=[self.shot(page, "Order summary",
                                                        page.get_by_text(re.compile(r"^Subtotal$")).locator("xpath=../.."))])

    # ---------- buttons & dropdowns
    def test_controls(self, page, url, start_state):
        controls = page.evaluate(JS_CONTROLS)
        groups = {}
        for c in controls:
            groups.setdefault(c["group"], []).append(c)
        best_state, best_size = start_state, len(json.dumps(start_state))
        if not controls:
            self.record(url, "buttons", "none outside forms", "skip")
        for group, items in groups.items():
            for nth, ctrl in enumerate(items[:2]):  # test up to 2 of each kind (e.g. 2 "Add to cart" buttons)
                label = f'{ctrl["kind"]} "{ctrl["text"]}"' + (f" #{nth + 1}" if len(items) > 1 else "")
                self.restore(page, url, start_state)
                if not page.evaluate(JS_MARK, [group, nth]):
                    self.record(url, "click", label, "skip", "not found after reload")
                    continue
                target = page.locator("[data-qa-target]")
                page.evaluate(JS_ARM)
                before = page.evaluate(JS_STATE)
                err_mark = len(self.errors_seen)
                try:
                    if ctrl["kind"] == "select":
                        opts = target.evaluate("e => [...e.options].map(o => o.value)")
                        current = target.input_value()
                        choice = next((o for o in reversed(opts) if o != current), current)
                        target.select_option(choice)
                        action = f'Choose "{choice}" in {label}'
                    else:
                        target.click(timeout=3000)
                        action = f"Click {label}"
                except PWError as e:
                    self.record(url, "click", label, "FAIL", "could not click: " + str(e).splitlines()[0][:80])
                    continue
                page.wait_for_timeout(700)
                after = page.evaluate(JS_STATE)
                errors = self.new_errors(err_mark)
                effect = []
                if after["url"] != before["url"]:
                    effect.append("navigated")
                if after["mut"] > 0:
                    effect.append(f"{after['mut']} DOM change(s)")
                if after["ls"] != before["ls"]:
                    effect.append("saved data changed")
                if abs(after["y"] - before["y"]) > 40:
                    effect.append("scrolled")
                state = json.loads(after["ls"]) if after["url"] == before["url"] else self.storage(page)
                if len(json.dumps(state)) > best_size:
                    best_state, best_size = state, len(json.dumps(state))

                if errors:
                    self.record(url, "click", label, "FAIL", errors[0][:90])
                    self.add_finding(type="js-error", severity="high", page=self.path(url),
                                     key=f"js-click:{self.path(url)}:{ctrl['group']}",
                                     title=f'{label} throws a JavaScript error', element=ctrl["html"],
                                     details={"errors": errors, "observed_effect": effect or ["nothing changed"]},
                                     steps=[f"Open {self.path(url)}", action],
                                     screenshots=[self.shot(page, "After the click", page.locator("[data-qa-target]"))])
                elif not effect:
                    self.record(url, "click", label, "FAIL", "no visible effect")
                    self.add_finding(type="dead-button", severity="medium", page=self.path(url),
                                     key=f"dead:{self.path(url)}:{ctrl['group']}",
                                     title=f"{label} does nothing when used", element=ctrl["html"],
                                     details={"observed_effect": "no DOM change, no navigation, no saved data"},
                                     steps=[f"Open {self.path(url)}", action],
                                     screenshots=[self.shot(page, "After the click", page.locator("[data-qa-target]"))])
                else:
                    self.record(url, "click", label, "pass", ", ".join(effect))
        return best_state

    # ---------- forms
    def test_forms(self, page, url, start_state):
        for form in page.evaluate(JS_FORMS):
            if not form["visible"] or not form["fields"]:
                continue
            name = "#" + form["id"] if form["id"] else f"form {form['index'] + 1}"
            text_fields = [f for f in form["fields"] if f["tag"] in ("input", "textarea")]
            is_search = len(text_fields) <= 1 and (form["role"] == "search" or any(
                f["type"] == "search" or re.search(r"search|^q$", f["key"] or "", re.I) for f in text_fields))
            if is_search:
                self.test_search_form(page, url, start_state, form, name)
            else:
                self.test_input_form(page, url, start_state, form, name)

    def fill_and_submit(self, page, url, start_state, form, values, highlight_key=None):
        """Fresh page -> fill fields -> submit. Returns (before_text, outcome, js_errors, screenshot_before)."""
        self.restore(page, url, start_state)
        f = page.locator("form").nth(form["index"])
        for fld in form["fields"]:
            if fld["key"] not in values:
                continue
            loc = f.locator(f'[name="{fld["key"]}"], [id="{fld["key"]}"]').first
            val = values[fld["key"]]
            try:
                if fld["tag"] == "select":
                    loc.select_option(str(val))
                elif fld["type"] in ("checkbox", "radio"):
                    loc.set_checked(bool(val))
                else:
                    loc.fill(str(val))
                    if loc.input_value() != str(val):
                        return None
            except PWError:
                return None  # the browser itself refuses this value (e.g. "2026-02-30" in a date field)
        shot_before = None
        if highlight_key:
            shot_before = self.shot(page, "Invalid value typed in", f.locator(f'[name="{highlight_key}"]'))
        before_text = page.inner_text("body")
        err_mark = len(self.errors_seen)
        submit = f.locator("button[type=submit], input[type=submit], button:not([type])")
        if submit.count():
            submit.first.click()
        else:
            f.evaluate("f => f.requestSubmit()")
        page.wait_for_timeout(800)
        return before_text, page.evaluate(JS_OUTCOME, form["index"]), self.new_errors(err_mark), shot_before

    def test_search_form(self, page, url, start_state, form, name):
        field = next(f for f in form["fields"] if f["tag"] in ("input", "textarea"))
        heading = page.evaluate("() => [...document.querySelectorAll('h2,h3')].map(h => h.innerText)"
                                ".find(t => /[A-Za-z]{4,}/.test(t)) || 'book'")
        term = next((w for w in re.findall(r"[A-Za-z]{4,}", heading)), "book")
        self.restore(page, url, start_state)
        f = page.locator("form").nth(form["index"])
        f.locator(f'[name="{field["key"]}"], [id="{field["key"]}"]').first.fill(term)
        page.evaluate(JS_ARM)
        before = page.evaluate(JS_STATE)
        err_mark = len(self.errors_seen)
        submit = f.locator("button[type=submit], input[type=submit], button:not([type])")
        submit.first.click() if submit.count() else f.evaluate("f => f.requestSubmit()")
        page.wait_for_timeout(800)
        after = page.evaluate(JS_STATE)
        errors = self.new_errors(err_mark)
        changed = after["url"] != before["url"] or after["mut"] > 0
        label = f'search {name} with "{term}"'
        steps = [f"Open {self.path(url)}", f'Type "{term}" into the search box', "Press the search button"]
        if errors:
            self.record(url, "form", label, "FAIL", errors[0][:90])
            self.add_finding(type="js-error", severity="high", page=self.path(url), key=f"search:{self.path(url)}",
                             title=f"Search does not work — submitting it throws a JavaScript error",
                             element=form["html"], details={"errors": errors, "search_term": term,
                                                            "results_changed": changed},
                             steps=steps, screenshots=[self.shot(page, "After searching", f)])
        elif not changed:
            self.record(url, "form", label, "FAIL", "results did not change")
            self.add_finding(type="dead-button", severity="medium", page=self.path(url), key=f"search:{self.path(url)}",
                             title="Search button does nothing", element=form["html"],
                             details={"search_term": term}, steps=steps,
                             screenshots=[self.shot(page, "After searching", f)])
        else:
            self.record(url, "form", label, "pass", f"{after['mut']} DOM change(s)")

    def test_input_form(self, page, url, start_state, form, name):
        plan = make_test_plan(form, self.ai)
        print(f"   test plan for {name}: {len(plan['invalid'])} invalid case(s) "
              f"({'AI + rules' if plan['ai_used'] else 'rules only'})")
        # 1) Baseline: realistic valid data should be accepted
        result = self.fill_and_submit(page, url, start_state, form, plan["valid"])
        if result is None and plan.get("fallback_valid"):
            plan["valid"] = plan.pop("fallback_valid")
            result = self.fill_and_submit(page, url, start_state, form, plan["valid"])
        before_text, base, errors, _ = result
        base_new = new_lines(before_text, base["text"])
        base_ok = not (base["errors"] or base["ariaInvalid"] or base["nativeInvalid"]) and (
            base["url"] != url or not base["formVisible"] or base_new)
        if not base_ok and plan.get("fallback_valid"):
            before_text, base, errors, _ = self.fill_and_submit(page, url, start_state, form, plan["fallback_valid"])
            base_new = new_lines(before_text, base["text"])
            base_ok = not (base["errors"] or base["ariaInvalid"] or base["nativeInvalid"]) and (
                base["url"] != url or not base["formVisible"] or base_new)
            plan["valid"] = plan["fallback_valid"]
        if not base_ok:
            self.record(url, "form", f"{name} with valid data", "FAIL", "; ".join(base["errors"])[:90] or "no response")
            self.add_finding(type="form-rejects-valid", severity="medium", page=self.path(url),
                             key=f"form-valid:{self.path(url)}:{name}", title=f"Form {name} rejects valid data",
                             element=form["html"], details={"values": plan["valid"], "errors_shown": base["errors"],
                                                            "js_errors": errors},
                             steps=[f"Open {self.path(url)}", "Fill the form with valid data", "Submit"],
                             screenshots=[self.shot(page, "After submitting valid data")])
            return
        self.record(url, "form", f"{name} with valid data", "pass", "accepted")

        # 2) Each invalid value should be rejected
        for case in plan["invalid"]:
            values = dict(plan["valid"], **{case["field"]: case["value"]})
            label = f'{name}: {case["field"]} = "{case["value"]}"'
            result = self.fill_and_submit(page, url, start_state, form, values, highlight_key=case["field"])
            if result is None:
                self.record(url, "invalid input rejected", label, "pass", "the input field refuses this value")
                continue
            before_text, out, errors, shot_before = result
            rejected = bool(out["errors"] or out["ariaInvalid"] or out["nativeInvalid"])
            same_as_success = (out["url"] == base["url"] != url) or (not out["formVisible"] and not base["formVisible"]) \
                or overlap(new_lines(before_text, out["text"]), base_new) >= 0.5
            if rejected or not same_as_success:
                self.record(url, "invalid input rejected", label, "pass", (out["errors"] or ["blocked"])[0][:70])
                continue
            self.record(url, "invalid input rejected", label, "FAIL", "form accepted it")
            sev = "high" if case.get("source") == "rules" else "low"
            self.add_finding(type="form-accepts-invalid", severity=sev, page=self.path(url),
                             key=f"form-invalid:{self.path(url)}:{name}:{case['field']}:{case['kind']}",
                             title=f'Form {name} accepts invalid {case["field"]}: "{case["value"]}"',
                             element=form["html"],
                             details={"field": case["field"], "invalid_value": case["value"], "why_invalid": case["why"],
                                      "case_source": "AI-suggested" if case.get("source") == "ai" else "built-in rule",
                                      "page_response": new_lines(before_text, out["text"])[:4]},
                             steps=[f"Open {self.path(url)}", "Fill every field with valid data",
                                    f'Change {case["field"]} to "{case["value"]}"', "Submit the form"],
                             screenshots=[s for s in [shot_before, self.shot(page, "Form was accepted")] if s])

    # ---------- links
    def check_links(self, ctx, page):
        print("\n== Checking links")
        status_cache = {}
        for absolute, sources in self.links.items():
            base, frag = urldefrag(absolute)
            if not base.startswith(self.origin):
                self.record(sources[0]["page"], "link", absolute, "skip", "external link")
                continue
            if base not in status_cache:
                try:
                    status_cache[base] = ctx.request.get(base, timeout=10000).status
                except PWError as e:
                    status_cache[base] = str(e).splitlines()[0][:60]
            st = status_cache[base]
            src = sources[0]
            where = sorted({self.path(s["page"]) for s in sources})
            if not isinstance(st, int) or st >= 400:
                self.record(src["page"], "link", src["href"], "FAIL", f"HTTP {st}")
                shots = []
                page.goto(src["page"])
                shots.append(self.shot(page, f'Link "{src["text"]}" on {self.path(src["page"])}',
                                       page.locator(f'a[href="{src["href"]}"]')))
                page.goto(base)
                shots.append(self.shot(page, f"What the visitor gets: HTTP {st}"))
                self.add_finding(type="broken-link", severity="high", page=self.path(src["page"]),
                                 key=f"broken-link:{self.path(base)}",
                                 title=f'Broken link "{src["text"]}" → {self.path(base)} (HTTP {st})',
                                 element=src["html"], details={"href": src["href"], "status": st, "found_on": where,
                                                               "existing_pages": sorted(self.path(u) for u in self.page_texts)},
                                 steps=[f"Open {where[0]}", f'Click the link "{src["text"]}"'], screenshots=shots)
            elif frag and base in self.page_ids and frag not in self.page_ids[base]:
                self.record(src["page"], "anchor", src["href"], "FAIL", f"#{frag} not found")
                self.add_finding(type="broken-link", severity="low", page=self.path(src["page"]),
                                 key=f"anchor:{self.path(base)}#{frag}",
                                 title=f'Link "{src["text"]}" points to missing section #{frag}', element=src["html"],
                                 details={"href": src["href"], "found_on": where},
                                 steps=[f"Open {where[0]}", f'Click "{src["text"]}"'])
            else:
                self.record(src["page"], "link", src["href"], "pass", f"HTTP {st}")

    # ---------- AI reads each page like a human tester
    def ai_content_review(self, page):
        if not self.ai.enabled:
            return
        print("\n== AI content review")
        # Drop lines that appear on every page (menu, footer) — they are boilerplate, not page content
        counts = {}
        for t in self.page_texts.values():
            for line in {l.strip() for l in t.splitlines() if l.strip()}:
                counts[line] = counts.get(line, 0) + 1
        pages = {self.path(u): "\n".join(l.strip() for l in t.splitlines()
                                         if l.strip() and counts[l.strip()] < len(self.page_texts))[:900]
                 for u, t in self.page_texts.items()}
        answer = self.ai.ask_json(
            "You are a meticulous website QA tester. You get the visible text of each page of an online shop "
            "(menus and footers removed). Report ONLY definite defects you can verify from the text: wrong "
            "arithmetic (check every subtotal/shipping/total), contradictory facts between pages, or "
            "garbage/placeholder text. Do NOT report design, wording, repeated text, missing features or guesses. "
            'Return {"issues": [{"page": "/path", "title": "short title", "evidence": "exact short quote '
            'copied from that page", "why": "one sentence"}]}. Return {"issues": []} if everything is correct.',
            json.dumps(pages, ensure_ascii=False))
        issues = (answer or {}).get("issues", []) if isinstance(answer, dict) else []
        norm = lambda s: re.sub(r"\s+", " ", str(s)).strip().lower()  # noqa: E731
        kept = 0
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            url = next((u for u in self.page_texts if self.path(u) == issue.get("page")), None)
            evidence = norm(issue.get("evidence", ""))
            if not url or len(evidence) < 10 or evidence[:60] not in norm(pages[issue["page"]]):
                continue  # AI quoted something that isn't on the page -> ignore (avoids hallucinations)
            kept += 1
            same_page = [f for f in self.findings if f["page"] == issue["page"] and f["type"] == "calculation"]
            if same_page:  # the rule-based check already caught it -> attach the AI's confirmation
                same_page[0]["details"]["confirmed_by_ai_review"] = issue.get("why") or issue.get("title")
                print(f"   AI confirms {same_page[0]['id']}: {issue.get('title')}")
                continue
            page.goto(url)
            self.add_finding(type="ai-content", severity="medium", page=issue["page"],
                             key=f"ai-content:{issue['page']}", title="AI review: " + str(issue.get("title", "content problem")),
                             details={"evidence": issue["evidence"], "why": issue.get("why", "")},
                             steps=[f"Open {issue['page']}", "Read the page"],
                             screenshots=[self.shot(page, "AI flagged this text",
                                                    page.get_by_text(str(issue["evidence"])[:40], exact=False))])
        self.record(self.start_url, "AI content review", f"{len(pages)} page(s)", "FAIL" if kept else "pass",
                    f"{kept} verified issue(s)" if kept else ("no issues" if answer is not None else "AI unavailable"))

    # ---------- AI explains every finding (one small request per finding)
    def ai_explain(self, sources):
        if not self.findings:
            return
        print(f"\n== Asking AI to explain {len(self.findings)} finding(s)")
        for f in self.findings:
            brief = {"problem": f["title"], "page": f["page"], "type": f["type"],
                     "element": (f.get("element") or "")[:200],
                     "evidence": json.dumps(f.get("details", {}), ensure_ascii=False, default=str)[:500]}
            answer = self.ai.ask_json(
                "You are a senior web developer helping a student fix a bug found by an automated browser test. "
                'Return {"explanation": "what is wrong and why it matters to a visitor", "likely_cause": "...", '
                '"suggested_fix": "...", "code_fix": "short corrected code"}',
                json.dumps({"finding": brief, "code_excerpt": relevant_code(f, sources)[:1000]},
                           ensure_ascii=False, default=str))
            ok = isinstance(answer, dict) and answer.get("explanation")
            f["ai"] = {k: answer.get(k, "") for k in ("explanation", "likely_cause", "suggested_fix", "code_fix")} \
                if ok else fallback_explanation(f)
            f["ai"]["source"] = self.ai.label if ok else "built-in rule (AI unavailable)"
            print(f"   {f['id']}: {'AI explanation received' if ok else 'fallback text'}")


def relevant_code(f, sources):
    """Pick the few source lines most related to a finding (keeps AI prompts small)."""
    d = f.get("details", {})
    hits = []
    stack = " ".join(d.get("errors", []))
    m = re.search(r"(/[\w./-]+\.js):(\d+)", stack)
    if m and m.group(1) in sources:
        lines = sources[m.group(1)].splitlines()
        n = int(m.group(2))
        hits.append((m.group(1), n, lines[max(0, n - 7):n + 5]))
    words = {"broken-link": [d.get("href", "")], "calculation": ["total", "shipping"],
             "form-accepts-invalid": [d.get("field", ""), "data." + d.get("field", "")],
             "dead-button": re.findall(r'(?:id|class)="([^"]+)"', f.get("element") or "")}.get(f["type"], [])
    for path, text in sources.items():
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if any(w and w.lower() in line.lower() for w in words) and len(hits) < 6:
                hits.append((path, i + 1, lines[max(0, i - 2):i + 3]))
    return "\n\n".join(f"{p} (around line {n}):\n" + "\n".join(ls) for p, n, ls in hits)[:2500]


# ---------------------------------------------------------------- test data (rules + AI)

def classify(field):
    s = f'{field["key"]} {field["label"]} {field["type"]} {field.get("placeholder") or ""}'.lower()
    for kind, pattern in [("email", r"e-?mail"), ("phone", r"phone|tel\b|mobile"), ("zip", r"zip|postal|postcode"),
                          ("date", r"\bdate\b"), ("number", r"\bnumber\b|qty|quantity|guests"),
                          ("message", r"message|comment|textarea"), ("address", r"address|street"),
                          ("city", r"city|town"), ("name", r"name")]:
        if field["type"] == kind or re.search(pattern, s):
            return kind
    return "text"


VALID = {"email": "alex.tester@example.com", "phone": "+1 555 123 4567", "zip": "12345", "date": DATE_IN(14),
         "number": "2", "message": "Hello! I would like to know when my order will arrive. Thank you.",
         "address": "12 Pine Street", "city": "Booktown", "name": "Alex Tester", "text": "Test value"}
INVALID = {
    "email": [("not-an-email", "format", "missing @ and domain"), ("", "empty", "email is required")],
    "phone": [("call me maybe", "format", "a phone number cannot contain letters")],
    "zip": [("ABCDE", "format", "ZIP codes are digits")],
    "date": [(DATE_IN(-3), "past", "a delivery/booking date cannot be in the past")],
    "number": [("-1", "negative", "quantities cannot be negative")],
    "message": [("", "empty", "an empty message is useless"), ("Hi", "short", "too short to be a real message")],
    "address": [("", "empty", "address is required to deliver")],
    "name": [("", "empty", "name is required")],
}


def make_test_plan(form, ai):
    """Valid values for every field + a list of invalid cases that a good form must reject."""
    valid, invalid = {}, []
    for f in form["fields"]:
        kind = classify(f)
        if f["tag"] == "select":
            opts = [o for o in f["options"] or [] if o]
            valid[f["key"]] = opts[0] if opts else ""
            continue
        if f["type"] == "checkbox":
            valid[f["key"]] = True
            continue
        valid[f["key"]] = VALID[kind]
        if "optional" in f["label"].lower():
            continue
        for value, case_kind, why in INVALID.get(kind, []):
            invalid.append({"field": f["key"], "value": value, "kind": case_kind, "why": why, "source": "rules"})
    plan = {"valid": valid, "invalid": invalid, "ai_used": False}

    answer = ai.ask_json(
        "You design test data for web forms. Given the fields of one form, return realistic VALID values for "
        "every field, plus up to one extra INVALID value per field that any reasonable website must reject "
        "(only clear-cut cases). Dates use YYYY-MM-DD; today is " + dt.date.today().isoformat() + ". "
        'Return {"valid": {"<key>": "value"}, "invalid": [{"field": "<key>", "value": "...", "why": "..."}]}',
        json.dumps([{k: f[k] for k in ("key", "label", "required", "minlength", "maxlength", "pattern",
                                        "min", "max", "options")} for f in form["fields"]]))
    if isinstance(answer, dict):
        keys = {f["key"] for f in form["fields"]}
        ai_valid = {k: v for k, v in (answer.get("valid") or {}).items() if k in keys}
        if set(ai_valid) == {k for k in valid}:
            plan["fallback_valid"], plan["valid"] = valid, ai_valid
        seen = {(c["field"], str(c["value"]).strip().lower()) for c in invalid}
        for c in (answer.get("invalid") or [])[:8]:
            if not isinstance(c, dict) or c.get("field") not in keys:
                continue
            if (c["field"], str(c.get("value", "")).strip().lower()) in seen:
                continue
            seen.add((c["field"], str(c.get("value", "")).strip().lower()))
            invalid.append({"field": c["field"], "value": str(c.get("value", "")), "kind": "ai",
                            "why": c.get("why", ""), "source": "ai"})
        plan["ai_used"] = True
    return plan


def new_lines(before, after):
    old = {line.strip() for line in before.splitlines()}
    return [line.strip() for line in after.splitlines() if line.strip() and line.strip() not in old]


def overlap(a, b):
    return len(set(a) & set(b)) / len(set(b)) if b else 0.0


def fallback_explanation(f):
    t = f["type"]
    text = {
        "broken-link": ("The link points to a page that does not exist, so visitors get an error page.",
                        "The href has a typo or the target file was renamed/deleted.",
                        "Change the href to an existing page, e.g. one of: " +
                        ", ".join(f["details"].get("existing_pages", []))),
        "js-error": ("JavaScript crashes when this feature is used, so the feature silently stops working.",
                     "A bug in the event handler: " + "; ".join(f["details"].get("errors", []))[:200],
                     "Open the browser console, find the line from the error message and fix the property/variable name."),
        "dead-button": ("The control does nothing when clicked, so users think the site is broken.",
                        "No event listener is attached, or it targets the wrong element id.",
                        "Check the selector used in addEventListener and that the handler updates the page."),
        "form-accepts-invalid": ("The form accepted data that should have been rejected, so bad data reaches the business.",
                                 "The validation rule for this field is missing or too weak.",
                                 "Add a check for this field in the submit handler (and use the right input type)."),
        "calculation": ("The total shown to the customer does not match its parts, so they would be over/under-charged.",
                        "The total formula adds a wrong value.",
                        "Recalculate total = subtotal + shipping (+ tax − discount)."),
    }.get(t, ("A problem was detected.", "See details.", "Inspect the page and the related code."))
    return {"explanation": text[0], "likely_cause": text[1], "suggested_fix": text[2], "code_fix": ""}


def collect_sources(ctx_request, pages, origin):
    """Fetch the site's HTML and JS so the AI can point at the exact line to fix."""
    files = {}
    urls = list(pages)
    for u in pages:
        html = ctx_request.get(u).text()
        urls += [urljoin(u, s) for s in re.findall(r'<script[^>]+src="([^"]+)"', html)]
    for u in dict.fromkeys(urls):
        if not u.startswith(origin):
            continue
        r = ctx_request.get(u)
        if r.status != 200:
            continue
        files[urlparse(u).path] = r.text()
    return files


# ---------------------------------------------------------------- entry point

def main():
    ap = argparse.ArgumentParser(description="AI website tester")
    ap.add_argument("--url", help="website to test (default: serve ./website locally)")
    ap.add_argument("--site", default=str(ROOT / "website"), help="folder to serve when --url is not given")
    ap.add_argument("--name", default=dt.datetime.now().strftime("run-%Y%m%d-%H%M%S"), help="report folder name")
    ap.add_argument("--no-ai", action="store_true", help="skip AI (rule-based explanations only)")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--max-pages", type=int, default=15)
    args = ap.parse_args()

    server = None
    url = args.url
    if not url:
        server, url = start_local_server(Path(args.site))
    out = ROOT / "reports" / args.name
    if out.exists():
        import shutil
        shutil.rmtree(out)
    ai = AI(enabled=not args.no_ai)
    print(f"AI Website Tester\n  target: {url}\n  AI: {ai.label}\n  report: {out}")

    t_start = time.time()
    tester = Tester(url, out, ai, max_pages=args.max_pages, video=not args.no_video)
    tester.run()
    if ai.enabled:
        with sync_playwright() as pw:
            req = pw.request.new_context()
            sources = collect_sources(req, tester.page_texts.keys(), tester.origin)
            req.dispose()
    else:
        sources = {}
    tester.ai_explain(sources)

    data = {"name": args.name, "url": url, "started": dt.datetime.now().isoformat(timespec="seconds"),
            "duration_s": round(time.time() - t_start, 1), "ai": ai.label, "ai_calls": ai.calls,
            "pages": [tester.path(u) for u in tester.page_texts], "findings": tester.findings, "log": tester.log,
            "video": tester.video_file}
    (out / "results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    report.write_report(data, out / "report.html")
    if server:
        server.shutdown()
    fails = sum(1 for e in tester.log if e["result"] == "FAIL")
    print(f"\nDone in {data['duration_s']}s: {len(tester.log)} checks, {fails} failed, "
          f"{len(tester.findings)} finding(s).\nReport: {out / 'report.html'}")


if __name__ == "__main__":
    main()
