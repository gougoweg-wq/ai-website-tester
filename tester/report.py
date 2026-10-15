"""Turns results.json into a self-contained HTML report (screenshots embedded)."""
import base64
import html
import json
from pathlib import Path

CSS = """
:root { --bg:#f6f7f9; --card:#fff; --ink:#1d2330; --muted:#667085; --line:#e4e7ec;
  --high:#c62828; --medium:#e08a00; --low:#3b6fd6; --pass:#2e7d32; }
@media (prefers-color-scheme: dark) { :root { --bg:#14171c; --card:#1d2128; --ink:#e7eaf0; --muted:#98a2b3; --line:#2c323c; } }
* { box-sizing:border-box } body { margin:0; font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--ink) }
.wrap { max-width:1100px; margin:0 auto; padding:24px 16px 60px } h1 { margin:0 0 4px } .muted { color:var(--muted) }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:20px 0 }
.stat { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px }
.stat b { display:block; font-size:1.8rem } .finding { background:var(--card); border:1px solid var(--line);
  border-left:6px solid var(--muted); border-radius:10px; padding:16px 18px; margin:16px 0 }
.finding.high { border-left-color:var(--high) } .finding.medium { border-left-color:var(--medium) } .finding.low { border-left-color:var(--low) }
.sev { display:inline-block; color:#fff; border-radius:4px; padding:0 8px; font-size:.8rem; text-transform:uppercase; margin-right:6px }
.sev.high { background:var(--high) } .sev.medium { background:var(--medium) } .sev.low { background:var(--low) }
.finding h3 { margin:6px 0 } .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px }
@media (max-width:760px) { .grid2 { grid-template-columns:1fr } }
.ai { background:color-mix(in srgb, var(--low) 8%, var(--card)); border-radius:8px; padding:10px 14px }
pre { background:#0f1218; color:#e6edf3; padding:10px; border-radius:6px; overflow:auto; font-size:.85rem; white-space:pre-wrap }
figure { margin:0 } figure img { width:100%; border:1px solid var(--line); border-radius:6px } figcaption { font-size:.85rem; color:var(--muted) }
.shots { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px; margin-top:10px }
table { width:100%; border-collapse:collapse; background:var(--card); font-size:.88rem } th,td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top }
.r-pass { color:var(--pass); font-weight:600 } .r-FAIL { color:var(--high); font-weight:700 } .r-skip { color:var(--muted) }
.table-wrap { overflow-x:auto } details summary { cursor:pointer; font-weight:600; margin:18px 0 8px } video { width:100%; border-radius:8px }
.tag { font-size:.8rem; border:1px solid var(--line); border-radius:4px; padding:0 6px; color:var(--muted) }
"""
e = html.escape


def _img(folder, shot):
    data = base64.b64encode((folder / shot["file"]).read_bytes()).decode()
    return (f'<figure><img src="data:image/jpeg;base64,{data}" alt="{e(shot["caption"])}">'
            f'<figcaption>{e(shot["caption"])}</figcaption></figure>')


def write_report(data, path):
    folder = path.parent
    findings = data["findings"]
    sev = {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")}
    checks = data["log"]
    fails = sum(1 for c in checks if c["result"] == "FAIL")
    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>AI Test Report – {e(data['name'])}</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>AI Website Test Report</h1>
<p class="muted">Run <b>{e(data['name'])}</b> · {e(data['started'])} · target {e(data['url'])} · AI: {e(data['ai'])}
 ({data['ai_calls']} call(s)) · {data['duration_s']} s</p>
<div class="cards">
 <div class="stat"><b>{len(data['pages'])}</b>pages tested</div>
 <div class="stat"><b>{len(checks)}</b>checks run</div>
 <div class="stat"><b style="color:var(--high)">{fails}</b>checks failed</div>
 <div class="stat"><b>{len(findings)}</b>findings</div>
 <div class="stat"><b style="color:var(--high)">{sev['high']}</b>high · <b style="display:inline;font-size:1rem;color:var(--medium)">{sev['medium']}</b> med · <b style="display:inline;font-size:1rem;color:var(--low)">{sev['low']}</b> low</div>
</div>"""]
    if not findings:
        out.append('<div class="finding" style="border-left-color:var(--pass)"><h3>✅ No problems found</h3>'
                   '<p>Every page loaded, all links work, every button reacted and every form rejected the invalid data.</p></div>')
    for f in findings:
        ai = f.get("ai", {})
        details = json.dumps(f.get("details", {}), indent=2, ensure_ascii=False)
        out.append(f"""<div class="finding {f['severity']}">
<span class="sev {f['severity']}">{f['severity']}</span><span class="tag">{e(f['type'])}</span> <span class="tag">{e(f['page'])}</span> <span class="muted">{f['id']}</span>
<h3>{e(f['title'])}</h3>
<div class="grid2"><div><b>How the tester found it</b><ol>{''.join(f'<li>{e(s)}</li>' for s in f.get('steps', []))}</ol>
<b>Evidence</b><pre>{e(details)}</pre>{f'<b>Element</b><pre>{e(f["element"])}</pre>' if f.get('element') else ''}</div>
<div class="ai"><b>🤖 AI explanation</b> <span class="muted" style="font-size:.8rem">({e(ai.get('source', ''))})</span>
<p>{e(str(ai.get('explanation', '')))}</p><p><b>Likely cause:</b> {e(str(ai.get('likely_cause', '')))}</p>
<p><b>Suggested fix:</b> {e(str(ai.get('suggested_fix', '')))}</p>
{f'<pre>{e(str(ai["code_fix"]))}</pre>' if ai.get('code_fix') else ''}</div></div>
<div class="shots">{''.join(_img(folder, s) for s in f.get('screenshots', []))}</div></div>""")
    if data.get("video"):
        out.append(f'<details><summary>🎥 Video of the whole test run</summary><video controls src="{e(data["video"])}"></video>'
                   '<p class="muted">The video file sits next to this report.</p></details>')
    out.append('<details open><summary>Every check the tester performed</summary><div class="table-wrap"><table>'
               '<tr><th>Page</th><th>Check</th><th>Target</th><th>Result</th><th>Note</th></tr>')
    for c in checks:
        out.append(f'<tr><td>{e(c["page"])}</td><td>{e(c["check"])}</td><td>{e(c["target"])}</td>'
                   f'<td class="r-{c["result"]}">{e(c["result"])}</td><td>{e(c["note"])}</td></tr>')
    out.append("</table></div></details></div></body></html>")
    path.write_text("\n".join(out), encoding="utf-8")


def write_comparison(before, after, path):
    """Before/after table: which findings disappeared after the fix."""
    keys_a = {f["key"]: f for f in before["findings"]}
    keys_b = {f["key"]: f for f in after["findings"]}
    rows = []
    for k, f in keys_a.items():
        rows.append(("✅ Fixed" if k not in keys_b else "❌ Still present", f))
    for k, f in keys_b.items():
        if k not in keys_a:
            rows.append(("⚠️ New", f))
    fixed = sum(1 for r in rows if r[0].endswith("Fixed"))
    fa = sum(1 for c in before["log"] if c["result"] == "FAIL")
    fb = sum(1 for c in after["log"] if c["result"] == "FAIL")
    out = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Before vs After</title><style>{CSS}</style></head>
<body><div class="wrap"><h1>Before vs after fixing the bugs</h1>
<p class="muted">Before: <a href="{e(before['name'])}/report.html">{e(before['name'])}</a> ({e(before['started'])}) ·
 After: <a href="{e(after['name'])}/report.html">{e(after['name'])}</a> ({e(after['started'])})</p>
<div class="cards"><div class="stat"><b>{len(keys_a)}</b>findings before</div><div class="stat"><b>{len(keys_b)}</b>findings after</div>
<div class="stat"><b style="color:var(--pass)">{fixed}</b>fixed</div>
<div class="stat"><b>{fa} → {fb}</b>failed checks ({len(before['log'])} → {len(after['log'])} checks)</div></div>
<div class="table-wrap"><table><tr><th>Status</th><th>Severity</th><th>Page</th><th>Finding</th></tr>"""]
    for status, f in rows:
        out.append(f'<tr><td>{status}</td><td><span class="sev {f["severity"]}">{f["severity"]}</span></td>'
                   f'<td>{e(f["page"])}</td><td>{e(f["title"])}</td></tr>')
    out.append("</table></div></div></body></html>")
    path.write_text("\n".join(out), encoding="utf-8")
