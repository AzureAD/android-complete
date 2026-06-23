#!/usr/bin/env python3
"""Build a self-contained master WBR summary HTML from the per-finding reports.

Part of the `vuln-triage-reporter` skill. Produces ONE overview page (`wbr-security-report.html`) that the
per-finding research subpages link back to — so the output folder is fully self-contained (no dependency on
any prior run's report). Contains: summary stat cards, a severity legend (our tiers ↔ IcM Sev), and a master
table linking each finding to its research subpage + machine-readable agent spec.

Reads the finding markdown directly (reusing the parsers in build_research_pages.py) — no CSV needed.

Usage:
    python build_master_report.py "<findings>/*.md" --out <run_dir> \
        --research-dir research --agent-dir agent-specs --window "2026-06-11 -> 2026-06-18"
"""
import argparse
import datetime
import glob
import html as htmllib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

# Reuse the finding parsers/helpers so the master report never drifts from the subpages.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_research_pages as brp  # noqa: E402

SEV_ORDER = {"Sev2": 0, "Sev2.5": 1, "Sev3": 2, "Sev4": 3}
TIER_ORDER = {"critical": 0, "important": 1, "moderate": 2, "low": 3}

CSS = """
:root{--bg:#f3f4f6;--card:#fff;--ink:#1a1a2e;--ink2:#5b6470;--line:#e2e6ea;--brand:#0f6cbd;--brand-d:#084e8a}
*{box-sizing:border-box}body{font-family:'Segoe UI',Inter,-apple-system,sans-serif;background:var(--bg);color:var(--ink);margin:0;line-height:1.55;font-size:14px}
.wrap{max-width:1240px;margin:0 auto;padding:24px 22px 80px}
header.top{background:linear-gradient(120deg,#084e8a,#0f6cbd);color:#fff;border-radius:14px;padding:22px 26px;box-shadow:0 4px 18px rgba(0,0,0,.14)}
header.top h1{margin:0;font-size:1.4rem}header.top .sub{opacity:.92;font-size:.9rem;margin-top:6px}
h2{font-size:1.12rem;margin:1.6rem 0 .6rem}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.card{border-radius:12px;padding:14px 16px;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.10);min-height:92px;display:flex;flex-direction:column;justify-content:center}
.card .lbl{font-size:.64rem;text-transform:uppercase;letter-spacing:.06em;opacity:.92;font-weight:600}
.card .val{font-size:1.5rem;font-weight:800;margin-top:6px;line-height:1.05}
.card .sub{font-size:.72rem;opacity:.93;margin-top:5px;line-height:1.3}
.c-blue{background:linear-gradient(135deg,#0f6cbd,#084e8a)}.c-green{background:linear-gradient(135deg,#15803d,#0f5f2d)}
.c-purple{background:linear-gradient(135deg,#6d28d9,#4c1d95)}.c-amber{background:linear-gradient(135deg,#b45309,#8a3f07)}
.c-teal{background:linear-gradient(135deg,#0e7490,#0a586e)}.c-slate{background:linear-gradient(135deg,#475569,#334155)}
section{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px;margin:16px 0;box-shadow:0 1px 3px rgba(0,0,0,.04)}
table{border-collapse:collapse;width:100%;font-size:.82rem}
th{text-align:left;padding:9px 10px;background:#f7f8fa;border-bottom:2px solid var(--line);font-size:.66rem;text-transform:uppercase;letter-spacing:.03em;color:var(--ink2);position:sticky;top:0}
td{padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:hover td{background:#fafbfc}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
code{background:#f0f2f4;padding:.05rem .3rem;border-radius:4px;font-family:'Cascadia Mono',Consolas,monospace;font-size:.85em}
.chip{display:inline-block;padding:2px 9px;border-radius:11px;font-size:.7rem;font-weight:700;white-space:nowrap}
.s-critical{background:#fde8e8;color:#b91c1c}.s-important{background:#fdebd9;color:#c2410c}.s-moderate{background:#fdf3d3;color:#a16207}.s-low{background:#dcfce7;color:#15803d}
.sev-Sev2{background:#fde8e8;color:#b91c1c}.sev-Sev25{background:#fdebd9;color:#c2410c}.sev-Sev3{background:#fdf3d3;color:#a16207}.sev-Sev4{background:#dcfce7;color:#15803d}
.v-agree{background:#e5e7eb;color:#374151}.v-down{background:#dcfce7;color:#15803d}.v-up{background:#fde8e8;color:#b91c1c}
.a-eng{background:#ede9fe;color:#5b21b6;font-weight:800;min-width:20px;text-align:center}.a-intern{background:#cffafe;color:#0e7490;font-weight:800;min-width:20px;text-align:center}
.conf-High{background:#dcfce7;color:#15803d}.conf-Medium{background:#fdebd9;color:#b45309}.conf-Low{background:#fde8e8;color:#b91c1c}
.tag-msrc{background:#fae8ff;color:#86198f}.tag-itd{background:#e0f2fe;color:#075985}
.ext-yes{background:#fef3c7;color:#92400e}.ext-no{background:#dcfce7;color:#15803d}
.act-keep{background:#ede9fe;color:#5b21b6}.act-deleg{background:#cffafe;color:#0e7490}
.c-rose{background:linear-gradient(135deg,#be123c,#881337)}
.repo{font-weight:600}.muted{color:var(--ink2)}
.legend{font-size:.84rem}.legend td{padding:6px 10px}
td.ctr,th.ctr{text-align:center}td.vuln{min-width:230px}
.footlegend{margin-top:12px;padding-top:10px;border-top:1px solid var(--line);font-size:.78rem;color:var(--ink2);display:flex;flex-direction:column;gap:6px}
.footlegend .chip{margin:0 2px}
footer{margin-top:26px;font-size:.78rem;color:var(--ink2);text-align:center;line-height:1.6}
"""


def slug_for(path):
    slug = re.sub(r'[^a-z0-9]+', '-', os.path.splitext(os.path.basename(path))[0].lower()).strip('-')
    if slug == "readme":
        slug = re.sub(r'[^a-z0-9]+', '-', os.path.basename(os.path.dirname(path)).lower()).strip('-')
    return slug


def filed_tier(md):
    for line in md.splitlines():
        if re.match(r'\s*\|\s*\*\*?Filed', line):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) >= 3:
                return brp._clean(cells[2])
    return ""


def eng_days(md):
    m = re.search(r'^##\s*Estimated Eng-Days\s*\n+\s*\*\*([\d.]+)\*\*', md, re.MULTILINE)
    return float(m.group(1)) if m else 0.0


def ext_validation_needed(md, meta):
    """True when the verdict leans on a server-side/downstream control we cannot statically verify.
    Mirrors build_research_pages.tiles_html so the master signal matches each finding's tile."""
    ext = meta.get('external validation', meta.get('external dependency', ''))
    ext_l = ext.lower()
    if ext:
        return ext_l.startswith(("yes", "y ")) or "unverified" in ext_l or "inferred" in ext_l
    return bool(re.search(r'cannot (conclude|verify)|server-side|inferred|downstream', md, re.IGNORECASE))


def extract(md, path):
    meta = brp.parse_meta(md)
    head = md.split("##", 1)[0]
    title = next((l[2:].strip() for l in md.splitlines() if l.startswith("# ")), os.path.basename(path))
    fid = ""
    mfid = re.search(r'Linked IcM:\*\*\s*([0-9]+)', head)
    if mfid:
        fid = mfid.group(1)
    short = re.sub(r'^\s*(MSRC|ITD)\s*\[[^\]]*\]\s*[—-]\s*', '', title)
    sev_icm = brp._clean(meta.get('icm severity', meta.get('icm sev', '')))
    component = meta.get('component', '')
    our_tier = brp._clean(meta.get('our_tier', ''))
    # Tag: MSRC vs ITD — from the title prefix, or a FireWatch GUID implies ITD
    tag = "MSRC" if re.match(r'^\s*MSRC\b', title) and not re.search(r'\bITD\b', title) else "ITD"
    assignment = brp.compute_assignment(our_tier, component)
    ext_needed = ext_validation_needed(md, meta)
    # Action = ownership-based next step (orthogonal to ext-validation, which is its own ⚗ signal).
    if assignment == "Engineer-owned":
        action = ("act-keep", "Keep & fix")
    else:
        action = ("act-deleg", "Delegate")
    return {
        "id": fid,
        "tag": tag,
        "title_short": short,
        "component": brp.canonical_repo(component),
        "filed": filed_tier(md),
        "our_tier": our_tier,
        "icm_sev": sev_icm,
        "confidence": brp._clean(meta.get('confidence', '')).title(),
        "verdict": brp._clean(meta.get('verdict', '')),
        "assignment": assignment,
        "ext_needed": ext_needed,
        "action": action,
        "eng_days": eng_days(md),
        "slug": slug_for(path),
        "bottomline": (re.search(r'^\*\*Bottom line:\*\*\s*(.+)$', md, re.MULTILINE) or [None, ""])[1]
        if re.search(r'^\*\*Bottom line:\*\*', md, re.MULTILINE) else "",
    }


def sev_key(s):
    return SEV_ORDER.get((s or "").replace(" ", "").replace("Sev25", "Sev2.5"), 9)


def card(cls, lbl, val, sub=""):
    return (f'<div class="card {cls}"><div class="lbl">{htmllib.escape(lbl)}</div>'
            f'<div class="val">{htmllib.escape(val)}</div>'
            + (f'<div class="sub">{htmllib.escape(sub)}</div>' if sub else "") + "</div>")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="Finding markdown files or globs")
    ap.add_argument("--out", required=True, help="Run dir to write wbr-security-report.html into")
    ap.add_argument("--research-dir", default="research", help="Subdir (under --out) with the research HTML")
    ap.add_argument("--agent-dir", default="agent-specs", help="Subdir (under --out) with the agent specs")
    ap.add_argument("--window", default="", help="Window label for the header")
    ap.add_argument("--title", default="MSRC/ITD Security Triage — WBR Overview", help="Report title")
    ap.add_argument("--shift", default="", help="Shift label, e.g. 'Wed 2026-06-11 -> Wed 2026-06-18'. "
                    "When set, the report is framed as an on-call shift report.")
    ap.add_argument("--owner", default="", help="On-call owner label for the shift (free text; kept in the "
                    "private workspace output only — no alias is committed to the repo).")
    ap.add_argument("--csv", default="classifications.csv",
                    help="Filename (under --out) of the classifications CSV to link for export. "
                         "Set to '' to omit the link.")
    ap.add_argument("--rollup", default="_ROLLUP.md",
                    help="Filename (under --out) of the roll-up markdown to link. Set to '' to omit.")
    args = ap.parse_args()

    files = []
    for inp in args.inputs:
        files.extend(glob.glob(inp, recursive=True) if any(c in inp for c in "*?[") else [inp])
    files = sorted(f for f in files if f.endswith(".md") and not f.endswith(".agent.md"))

    findings = [extract(open(f, encoding="utf-8").read(), f) for f in files]
    findings.sort(key=lambda x: (TIER_ORDER.get(x["our_tier"].lower(), 9), sev_key(x["icm_sev"])))

    n = len(findings)
    downs = sum(1 for f in findings if "down" in f["verdict"].lower())
    ups = sum(1 for f in findings if "up" in f["verdict"].lower())
    agrees = sum(1 for f in findings if "agree" in f["verdict"].lower())
    eng = [f for f in findings if f["assignment"] == "Engineer-owned"]
    intern = [f for f in findings if f["assignment"] != "Engineer-owned"]
    ext_needed = [f for f in findings if f["ext_needed"]]
    total_days = sum(f["eng_days"] for f in findings)
    from collections import Counter
    sev_c = Counter(f["icm_sev"].replace(" ", "") for f in findings if f["icm_sev"])
    conf_c = Counter(f["confidence"] for f in findings if f["confidence"])
    conf_str = " · ".join(f"{c} {lvl}" for lvl, c in conf_c.most_common())

    # ---- cards ----
    cards = [
        card("c-blue", "Findings triaged", str(n), "two-pass: investigate + adversarial"),
        card("c-green", "Down-classified", f"{downs} of {n}",
             f"{agrees} agreed · {ups} up — vs. filed"),
        card("c-amber", "IcM Sev breakdown",
             " · ".join(f"{v}× {k}" for k, v in sorted(sev_c.items(), key=lambda kv: sev_key(kv[0]))) or "—",
             "team response urgency"),
        card("c-purple", "Engineer-owned", str(len(eng)),
             f"~{sum(f['eng_days'] for f in eng):g} eng-days · remediation specs"),
        card("c-teal", "Intern queue", str(len(intern)), "Moderate↓ + Authenticator only"),
        card("c-rose", "Needs external validation", str(len(ext_needed)),
             "verdict leans on a server/downstream control we can't statically prove"),
        card("c-slate", "Est. eng-days", f"{total_days:g}", "summed across all findings (estimate)"),
    ]

    # ---- legend ----
    legend = """
    <section><h2 style="margin-top:0">Severity legend — our tier ↔ IcM Sev (response urgency)</h2>
    <table class="legend"><thead><tr><th>Our tier</th><th>IcM Sev</th><th>Urgency</th><th>Meaning</th></tr></thead><tbody>
    <tr><td><span class="chip s-critical">Critical</span></td><td><span class="chip sev-Sev2">Sev2</span>/<span class="chip sev-Sev25">Sev2.5</span></td><td>Immediate (Sev2 = outside business hrs)</td><td>Reachable in prod, no mitigation, real-world exploitable</td></tr>
    <tr><td><span class="chip s-important">Important</span></td><td><span class="chip sev-Sev3">Sev3</span></td><td>Soon, not drop-everything</td><td>Genuine weakness, partial mitigation / elevated prereq</td></tr>
    <tr><td><span class="chip s-moderate">Moderate</span></td><td><span class="chip sev-Sev3">Sev3</span>/<span class="chip sev-Sev4">Sev4</span></td><td>Soon → hygiene</td><td>Defense-in-depth gap; needs unlikely preconditions</td></tr>
    <tr><td><span class="chip s-low">Low</span></td><td><span class="chip sev-Sev4">Sev4</span></td><td>Low priority / hygiene</td><td>Not reachable in shipping config, or gated off</td></tr>
    </tbody></table>
    <p class="muted" style="font-size:.8rem;margin-bottom:0">Sev2.5+ is a rare, high bar (High confidence + proven reachable + no safeguard + not boundary-dependent). <strong>Intern-eligible</strong> = our tier is Moderate or lower <em>and</em> the component is the Authenticator app; everything else (Important+, or any Broker/Common/MSAL) is Engineer-owned.</p></section>
    """

    # ---- master table ----
    def chip(cls, txt):
        return f'<span class="chip {cls}">{htmllib.escape(txt)}</span>' if txt else "—"

    def verdict_short(v):
        vl = v.lower()
        if "down" in vl:
            return "v-down", "DOWN"
        if "up" in vl:
            return "v-up", "UP"
        return "v-agree", "AGREE"

    rows = []
    for f in findings:
        tier_cls = "s-" + (f["our_tier"].lower() if f["our_tier"].lower() in ("critical", "important", "moderate", "low") else "moderate")
        v_cls, v_txt = verdict_short(f["verdict"])
        is_eng = f["assignment"] == "Engineer-owned"
        a_cls, a_txt = ("a-eng", "E") if is_eng else ("a-intern", "I")
        act_cls, act_txt = f["action"]
        tag_cls = "tag-msrc" if f["tag"] == "MSRC" else "tag-itd"
        icm = (f'<a href="https://portal.microsofticm.com/imp/v5/incidents/details/{f["id"]}/summary" '
               f'target="_blank">{f["id"]}</a>' if f["id"] else "—")
        # ⚗ flag on the vulnerability cell when the verdict needs external (server/downstream) validation
        ext_flag = (' <span class="chip ext-yes" title="Verdict leans on a server/downstream control we '
                    'cannot statically verify — confirm before closing">⚗ ext</span>') if f["ext_needed"] else ""
        research = f'{args.research_dir}/{f["slug"]}.html'
        rows.append(
            "<tr>"
            f"<td>{icm}</td>"
            f"<td>{chip(tag_cls, f['tag'])}</td>"
            f'<td><span class="repo">{htmllib.escape(f["component"])}</span></td>'
            f'<td class="muted">{htmllib.escape(f["filed"])}</td>'
            f"<td>{chip(tier_cls, f['our_tier'])}</td>"
            f"<td>{chip('conf-' + f['confidence'], f['confidence'])}</td>"
            f'<td class="ctr">{chip(v_cls, v_txt)}</td>'
            f'<td class="ctr">{chip(a_cls, a_txt)}</td>'
            f'<td class="ctr">{chip(act_cls, act_txt)}</td>'
            f'<td class="ctr">{f["eng_days"]:g}</td>'
            f'<td class="vuln">{htmllib.escape(f["title_short"])}{ext_flag}</td>'
            f'<td><a href="{research}">Research&nbsp;&rarr;</a></td>'
            "</tr>")

    table = (
        '<section><h2 style="margin-top:0">Findings</h2><table><thead><tr>'
        "<th>IcM</th><th>Tag</th><th>Component</th><th>Filed</th><th>Ours</th><th>Conf</th>"
        '<th class="ctr">Verdict</th><th class="ctr">Owner</th><th class="ctr">Action</th>'
        '<th class="ctr">Eng-days</th><th>Vulnerability</th><th>Evidence</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
        # bottom legends (verdict + owner + action) — keep the table columns compact
        '<div class="footlegend">'
        '<span><strong>Verdict</strong> (vs. filed): '
        '<span class="chip v-agree">AGREE</span> we concur · '
        '<span class="chip v-down">DOWN</span> down-classified (filed too high) · '
        '<span class="chip v-up">UP</span> up-classified (filed too low)</span>'
        '<span><strong>Owner</strong>: '
        '<span class="chip a-eng">E</span> Engineer-owned (Important+ or any Broker/Common/MSAL) · '
        '<span class="chip a-intern">I</span> Intern-eligible (Moderate↓ AND Authenticator app)</span>'
        '<span><strong>Action</strong>: '
        '<span class="chip act-keep">Keep &amp; fix</span> engineer remediates · '
        '<span class="chip act-deleg">Delegate</span> hand to intern. '
        '<span class="chip ext-yes">⚗ ext</span> = severity confirmation still needs a server/downstream '
        'check we can\'t statically verify (the fix may still proceed — see the finding\'s '
        '"can proceed now vs. blocked").</span>'
        '</div></section>')

    # ---- header: shift framing + freshness stamp ----
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if args.shift:
        title = args.title if "Shift" in args.title else f"On-Call Shift Security Report"
        sub_bits = [f"Shift: {args.shift}"]
        if args.owner:
            sub_bits.append(f"on-call: {args.owner}")
        sub_bits.append(f"{n} findings")
        sub_bits.append("findings appended as IcMs arrive")
        sub = " · ".join(sub_bits)
    else:
        title = args.title
        sub = (args.window + " · " if args.window else "") + f"{n} findings · two-pass evidence-based triage"
    stamp = (f'<div class="sub" style="margin-top:8px;font-size:.78rem;opacity:.85">'
             f'Generated {generated} · confidence: {htmllib.escape(conf_str)}'
             f'{" · ⚠ run may be stale if older than your shift" if args.shift else ""}</div>')

    # ---- export links (self-contained: link the rollup + CSV that ship in the same folder) ----
    links = []
    if args.rollup and os.path.isfile(os.path.join(args.out, args.rollup)):
        links.append(f'<a href="{htmllib.escape(args.rollup)}">Roll-up (markdown)</a>')
    if args.csv and os.path.isfile(os.path.join(args.out, args.csv)):
        links.append(f'<a href="{htmllib.escape(args.csv)}">Classifications (CSV export)</a>')
    links_html = (f'<section style="padding:12px 20px"><strong>Exports:</strong> ' + " · ".join(links)
                  + '</section>') if links else ""

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{htmllib.escape(title)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header class="top"><h1>{htmllib.escape(title)}</h1><div class="sub">{htmllib.escape(sub)}</div>{stamp}</header>
<div class="cards">{''.join(cards)}</div>
{legend}
{table}
{links_html}
<footer>Generated by the <code>vuln-triage-reporter</code> skill — parallel <code>codebase-researcher</code> two-pass investigation (investigate + adversarial).<br>
Each row links to a self-contained research evidence page; each evidence page has a one-click <strong>"Fix this with an AI agent"</strong> dispatch spec. No exploit PoC or PII included.</footer>
</div></body></html>"""

    outp = os.path.join(args.out, "wbr-security-report.html")
    open(outp, "w", encoding="utf-8").write(html)
    print("  +", outp)
    print(f"\nDone. {n} findings · {len(eng)} engineer-owned · {len(intern)} intern.")


if __name__ == "__main__":
    main()
