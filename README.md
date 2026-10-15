# Build It. Break It. Fix It. — AI Website Tester

Two parts:

1. **`website/`**: *Page & Pine Books*, a small online bookshop (HTML, CSS and JavaScript, with no frameworks).
2. **`tester/`**: an AI-powered tester. It opens the site in a real Chrome browser (Playwright), clicks every button, fills every form with good and bad data and checks every link. It then asks an AI to explain each problem and writes an HTML report with screenshots and a video.

## 1. The website

| Page | Working features |
|---|---|
| `index.html` | Hero, top picks, navigation |
| `shop.html` | **Search** by title/author, **genre filter**, **sorting**, **Add to cart** (live cart counter) |
| `cart.html` | Change quantity (+/−), remove item, clear cart, subtotal + shipping = total |
| `checkout.html` | Delivery form with validation (name, email, phone, address, 5-digit ZIP, future date); order confirmation |
| `contact.html` | Contact form with validation, FAQ |

The cart is saved in the browser's `localStorage`, so it survives page changes.

## 2. How the AI tester works

```
open start page ──► for every page found (crawler follows same-site links)
                     ├─ page loads? JavaScript errors? missing files? broken images?
                     ├─ content checks: "undefined"/"NaN" text, Subtotal + Shipping = Total?
                     ├─ click every button / change every dropdown → did anything happen?
                     │     (counts DOM changes, URL changes, saved data, scrolling, JS errors)
                     ├─ search forms: type a word, submit → did the results change?
                     └─ input forms:  1) submit realistic VALID data  → must be accepted
                                      2) submit each INVALID value   → must be rejected
                                         (test data = built-in rules + extra cases invented by AI)
              ──► check every link (HTTP status, #anchors)
              ──► AI reads all pages like a human tester (wrong totals, contradictions…)
              ──► AI explains every finding: what is wrong, likely cause, how to fix (with code)
              ──► report.html (screenshots with the problem outlined in red) + results.json + video
```

Where the AI is used:
* **Test data generation**: the AI looks at each form's fields and invents valid values and extra invalid cases.
* **Content review**: the AI reads the page text and looks for logic errors such as wrong arithmetic. The tester only keeps an AI claim if the quoted text really exists on the page, which filters out hallucinations.
* **Explanations**: for each problem the AI gets the evidence and the relevant lines of the site's code. It answers with an explanation, a likely cause and a fix.

The tester never reads `bugs.py` and has no list of where the bugs are. It finds them only by using the site in a browser.

Each test starts from the same saved browser state. For example, the cart is reset before every click. This makes the results repeatable. The tester also keeps the "richest" state it reached (items in the cart), so later pages like the cart and checkout have data to test.

### AI provider
`tester/ai.py` chooses the provider automatically:
1. If `ANTHROPIC_API_KEY` is set, it uses **Claude**.
2. If `OPENAI_API_KEY` is set, it uses an OpenAI-compatible API (also works with Gemini, Groq or OpenRouter through `OPENAI_BASE_URL`).
3. If neither is set, it uses the **free keyless endpoint** `text.pollinations.ai`. It is slow and rate-limited: a full run takes about 10–15 minutes. Page text and code snippets of this demo site are sent there.

Put keys in a `.env` file in this folder, for example `ANTHROPIC_API_KEY=...`. Don't put keys in the code. Answers are cached in `.ai_cache/`, so a second run is faster. Add `--no-ai` to run without AI, which uses rule-based explanations.

## 3. Bugs: break it, test it, fix it

`bugs.py` adds or removes 4 intentional bugs of different kinds:

| # | Bug | Kind |
|---|---|---|
| 1 | Footer link "Contact us" → `contacts.html` (does not exist) | broken link |
| 2 | Search uses `b.name` instead of `b.title` → TypeError on click | button that doesn't work / JS crash |
| 3 | Checkout email check removed + `type="text"` | form accepts incorrect information |
| 4 | Cart total adds shipping twice | wrong calculation |

## How to run

```bash
python3 -m venv .venv && .venv/bin/pip install playwright && .venv/bin/python -m playwright install chromium
```
```bash
python3 bugs.py inject
```
```bash
.venv/bin/python tester/ai_tester.py --name 1-before-fix
```
```bash
python3 bugs.py fix
```
```bash
.venv/bin/python tester/ai_tester.py --name 2-after-fix
```
```bash
.venv/bin/python tester/compare.py 1-before-fix 2-after-fix
```

The tester serves `website/` itself on a local port. To test any other site, use `--url https://...`.

## Results (evidence for submission)

* `reports/1-before-fix/report.html`: the buggy site. All 4 bugs are found, with screenshots and AI explanations.
* `reports/2-after-fix/report.html`: the same tests after fixing. No problems are found.
* `reports/comparison.html`: before vs after, showing which findings were fixed.
* `reports/*/test-run.webm`: a video of the browser while the tester works.

Before the fix there were 58 checks, 7 failed and 5 findings: all 4 bugs, plus a second invalid email the AI invented (`jane.doe@bad`). The AI review also confirmed the wrong cart total on its own. After the fix there were 56 checks, 0 failed and 0 findings.
On the free AI endpoint, some AI requests fail because of rate limits. When that happens, the report says "AI unavailable" or "built-in rule" instead of hiding it. With a Claude/OpenAI key in `.env` this does not happen.

## Files
```
website/            the bookshop (5 pages, css/, js/)
tester/ai_tester.py the tester: crawler, button/form/link/content checks
tester/ai.py        AI client (Claude / OpenAI-compatible / free fallback) with caching
tester/report.py    HTML report + before/after comparison
tester/compare.py   compares two runs
bugs.py             injects / fixes the 4 intentional bugs
```
