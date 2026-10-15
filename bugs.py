"""Inject or fix the intentional bugs in ./website  (Step 3 of the assignment).

  python bugs.py inject   # break the site
  python bugs.py fix      # repair it
  python bugs.py status   # show which bugs are currently in the code

The tester never reads this file — it has to discover the bugs on its own in the browser.
"""
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent / "website"

# (file, correct code, buggy code)
BUGS = {
    "1. Broken link (footer 'Contact us' points to a page that does not exist)": [
        ("index.html", '<a href="contact.html">Contact us</a>', '<a href="contacts.html">Contact us</a>'),
    ],
    "2. Search button crashes (typo in property name -> TypeError)": [
        ("js/shop.js", "b.title.toLowerCase().includes(query)", "b.name.toLowerCase().includes(query)"),
    ],
    "3. Checkout accepts an invalid email (validation removed)": [
        ("checkout.html", 'type="email" id="email"', 'type="text" id="email"'),
        ("js/checkout.js", "if (!/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(data.email))", "if (!data.email)"),
    ],
    "4. Cart total is wrong (shipping added twice)": [
        ("js/cartpage.js", "const total = subtotal + shipping;", "const total = subtotal + shipping + shipping;"),
    ],
}


def apply(direction):
    for name, edits in BUGS.items():
        for file, good, bad in edits:
            p = SITE / file
            src = p.read_text()
            old, new = (good, bad) if direction == "inject" else (bad, good)
            if old in src:
                p.write_text(src.replace(old, new))
        print(f"{'injected' if direction == 'inject' else 'fixed   '}  {name}")


def status():
    for name, edits in BUGS.items():
        present = any(bad in (SITE / f).read_text() for f, _, bad in edits)
        print(f"{'BUG ' if present else 'ok  '}  {name}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("inject", "fix"):
        apply(cmd)
    else:
        status()
