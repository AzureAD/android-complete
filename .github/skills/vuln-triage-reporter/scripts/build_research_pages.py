#!/usr/bin/env python3
"""Render per-finding research markdown into self-contained, shareable HTML subpages.

Part of the `vuln-triage-reporter` skill. Each subpage is the FULL evidence record for one finding:
classification, confirmed sink, defense-in-depth sweep, recommended fix, AND a verbatim "Searches Run"
audit trail — so a reviewer can independently verify every "no mitigation found" / reachability claim.

Self-contained (CSS inlined) so pages can be shared without external files.
File:line citations are rendered as highlighted monospace "evidence" so they're visible and copyable.

Usage:
    python build_research_pages.py <input_dir_or_files...> --out research/ --index

Input markdown files are the per-finding reports (itd-investigations/*/README.md and
msrc-investigations/*.md). Output is one HTML per input plus an index.html linking them all.
"""
import argparse
import glob
import html as htmllib
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

CSS = """
:root{--bg:#f3f4f6;--card:#fff;--ink:#1a1a2e;--ink2:#5b6470;--line:#e2e6ea;--brand:#0f6cbd;--brand-d:#084e8a;
--crit:#b91c1c;--crit-bg:#fde8e8;--imp:#c2410c;--imp-bg:#fdebd9;--mod:#a16207;--mod-bg:#fdf3d3;--low:#15803d;--low-bg:#dcfce7;
--agree:#374151;--agree-bg:#e5e7eb;--down:#15803d;--down-bg:#dcfce7;--up:#b91c1c;--up-bg:#fde8e8;--ev:#0b4f6c;--ev-bg:#e7f3f8;}
*{box-sizing:border-box}body{font-family:'Segoe UI',Inter,-apple-system,sans-serif;background:var(--bg);color:var(--ink);margin:0;line-height:1.6;font-size:14px}
.wrap{max-width:980px;margin:0 auto;padding:24px 22px 80px}
.back{display:inline-block;margin-bottom:14px;color:var(--brand);text-decoration:none;font-size:.85rem}
.back:hover{text-decoration:underline}
header.top{background:linear-gradient(120deg,var(--brand-d),var(--brand));color:#fff;border-radius:12px;padding:20px 24px;box-shadow:0 4px 16px rgba(0,0,0,.12)}
header.top h1{margin:0;font-size:1.25rem;line-height:1.35}
header.top .sub{opacity:.92;font-size:.85rem;margin-top:6px}
section,.body{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 22px;margin:18px 0;box-shadow:0 1px 3px rgba(0,0,0,.04)}
h1{font-size:1.3rem;margin:.2rem 0 .6rem}h2{font-size:1.08rem;margin:1.3rem 0 .5rem;border-bottom:2px solid var(--line);padding-bottom:6px}
h3{font-size:.98rem;margin:1rem 0 .3rem}
p{margin:.5rem 0}ul,ol{padding-left:1.4rem;margin:.5rem 0}li{margin:.3rem 0}
.kv{margin:.55rem 0;line-height:1.65}
table{border-collapse:collapse;width:100%;font-size:.82rem;margin:.6rem 0}
th{text-align:left;padding:8px 10px;background:#f7f8fa;border-bottom:2px solid var(--line);font-size:.7rem;text-transform:uppercase;letter-spacing:.03em;color:var(--ink2)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}
code{background:#f0f2f4;padding:.05rem .3rem;border-radius:4px;font-family:'Cascadia Mono',Consolas,monospace;font-size:.85em}
.ev{background:var(--ev-bg);color:var(--ev);border:1px solid #cfe4ee;padding:.05rem .35rem;border-radius:4px;font-family:'Cascadia Mono',Consolas,monospace;font-size:.82em;white-space:nowrap}
blockquote{border-left:3px solid var(--brand);background:#f7f8fa;margin:.6rem 0;padding:.5rem 1rem;border-radius:0 6px 6px 0}
hr{border:none;border-top:1px solid var(--line);margin:1rem 0}
.chip{display:inline-block;padding:2px 9px;border-radius:11px;font-size:.72rem;font-weight:600}
.sev-crit{background:var(--crit-bg);color:var(--crit)}.sev-imp{background:var(--imp-bg);color:var(--imp)}
.sev-mod{background:var(--mod-bg);color:var(--mod)}.sev-low{background:var(--low-bg);color:var(--low)}
.v-agree{background:var(--agree-bg);color:var(--agree)}.v-down{background:var(--down-bg);color:var(--down)}.v-up{background:var(--up-bg);color:var(--up)}
.audit{border:1px solid #cfe4ee;border-left:4px solid var(--ev);background:#f4fafd;border-radius:10px;padding:14px 18px;margin:18px 0}
.audit h2{border:none;color:var(--ev);margin-top:0}
.didbox{border:1px solid #cfe9d6;border-left:4px solid var(--low);background:#f3fbf6;border-radius:10px;padding:14px 18px;margin:18px 0}
.didbox h2{border:none;color:var(--low);margin-top:0}
.disclaimer{border:1px solid #f0e0b8;border-left:4px solid var(--mod);background:#fdfaf0;border-radius:10px;padding:14px 18px;margin:18px 0}
.disclaimer h2{border:none;color:var(--mod);margin-top:0}
.verifybox{border:1px solid #d6c9ec;border-left:4px solid #6d28d9;background:#f8f5fd;border-radius:10px;padding:14px 18px;margin:18px 0}
.verifybox h2{border:none;color:#5b21b6;margin-top:0}
.fixbox{border:1px solid #c7dbef;border-left:4px solid var(--brand);background:#f4f8fd;border-radius:10px;padding:14px 18px;margin:18px 0}
.fixbox h2{border:none;color:var(--brand-d);margin-top:0}
.gapbox{border:1px solid #f6c6d3;border-left:4px solid #be123c;background:#fff1f4;border-radius:10px;padding:14px 18px;margin:18px 0}
.gapbox h2{border:none;color:#9f1239;margin-top:0}
.decisionbox{border:1px solid #d6c9ec;border-left:4px solid #6d28d9;background:#f8f5fd;border-radius:10px;padding:14px 18px;margin:18px 0}
.decisionbox h2{border:none;color:#5b21b6;margin-top:0}
.tldr{background:#eef6ff;border:1px solid #c7dbef;border-left:4px solid var(--brand);border-radius:10px;padding:13px 18px;margin:16px 0;font-size:.95rem}
.tldr strong{color:var(--brand-d)}
.agentlink{display:inline-flex;align-items:center;gap:6px;background:#6d28d9;color:#fff;border-radius:8px;padding:7px 13px;font-size:.82rem;font-weight:600;text-decoration:none;margin-top:10px}
.agentlink:hover{background:#5b21b6;text-decoration:none}
details.audit{border:1px solid #cfe4ee;border-left:4px solid var(--ev);background:#f4fafd;border-radius:10px;padding:8px 18px;margin:18px 0}
details.audit summary{cursor:pointer;color:var(--ev);font-weight:700;font-size:1.08rem;padding:6px 0;list-style:revert}
details.audit[open] summary{margin-bottom:6px}
.glossary{border:1px solid var(--line);background:#fbfbfc;border-radius:10px;padding:14px 18px;margin:18px 0}
.glossary dl{margin:.4rem 0;display:grid;grid-template-columns:max-content 1fr;gap:.3rem .9rem}
.glossary dt{font-weight:700;color:var(--brand-d);font-family:'Cascadia Mono',Consolas,monospace;font-size:.84rem}
.glossary dd{margin:0;color:var(--ink2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:18px 0}
.tile{border-radius:12px;padding:13px 16px;color:#fff;box-shadow:0 2px 8px rgba(0,0,0,.10);min-height:84px;display:flex;flex-direction:column;justify-content:center}
.tile .lbl{font-size:.64rem;text-transform:uppercase;letter-spacing:.06em;opacity:.92;font-weight:600}
.tile .val{font-size:1.18rem;font-weight:700;margin-top:5px;line-height:1.15}
.tile .sub{font-size:.7rem;opacity:.92;margin-top:4px;line-height:1.3;font-weight:400}
.t-crit{background:linear-gradient(135deg,#b91c1c,#7f1212)}.t-imp{background:linear-gradient(135deg,#c2410c,#9a3209)}
.t-mod{background:linear-gradient(135deg,#a16207,#7c4a05)}.t-low{background:linear-gradient(135deg,#15803d,#0f5f2d)}
.t-high{background:linear-gradient(135deg,#15803d,#0f5f2d)}.t-med{background:linear-gradient(135deg,#b45309,#8a3f07)}.t-lowc{background:linear-gradient(135deg,#b91c1c,#7f1212)}
.t-pass{background:linear-gradient(135deg,#0f6cbd,#084e8a)}.t-eng{background:linear-gradient(135deg,#6d28d9,#4c1d95)}.t-intern{background:linear-gradient(135deg,#0e7490,#0a586e)}
.t-ext-yes{background:linear-gradient(135deg,#b45309,#8a3f07)}.t-ext-no{background:linear-gradient(135deg,#15803d,#0f5f2d)}
.t-agree{background:linear-gradient(135deg,#475569,#334155)}.t-down{background:linear-gradient(135deg,#15803d,#0f5f2d)}.t-up{background:linear-gradient(135deg,#b91c1c,#7f1212)}
.t-sev2{background:linear-gradient(135deg,#b91c1c,#7f1212)}.t-sev25{background:linear-gradient(135deg,#c2410c,#9a3209)}.t-sev3{background:linear-gradient(135deg,#a16207,#7c4a05)}.t-sev4{background:linear-gradient(135deg,#15803d,#0f5f2d)}
.muted{color:var(--ink2)}.idx a{display:block;padding:6px 0;border-bottom:1px solid var(--line)}
footer{margin-top:24px;font-size:.78rem;color:var(--ink2);text-align:center;line-height:1.6}
"""

CITATION_RE = re.compile(r'`([^`]*?\.(?:java|kt|kts|xml|gradle|cs|aar)[^`]*?#L[\d,\-]+)`')
CITATION_RE2 = re.compile(r'`([^`]*?\.(?:java|kt|kts|xml|gradle|cs)[^`]*?)`')


def load_glossary(path):
    """Parse `- **TERM** -- definition` lines into an ordered dict."""
    terms = {}
    if not path or not os.path.isfile(path):
        return terms
    for line in open(path, encoding="utf-8"):
        m = re.match(r'\s*-\s*\*\*(.+?)\*\*\s*[\u2014\-]+\s*(.+)', line)
        if m:
            terms[m.group(1).strip()] = m.group(2).strip()
    return terms


def glossary_html(md_text, terms):
    """Return a Glossary section listing only terms that appear in md_text."""
    found = []
    for term, defn in terms.items():
        if " " in term:                                  # phrase -> case-insensitive
            pat, flags = re.escape(term), re.IGNORECASE
        elif re.fullmatch(r'[A-Z0-9_]+', term):          # acronym -> case-sensitive
            pat, flags = r'\b' + re.escape(term) + r'\b', 0
        else:                                            # camelCase/word -> case-insensitive
            pat, flags = r'\b' + re.escape(term) + r'\b', re.IGNORECASE
        if re.search(pat, md_text, flags):
            found.append((term, defn))
    if not found:
        return ""
    rows = "".join(f"<dt>{htmllib.escape(t)}</dt><dd>{md_inline(d)}</dd>" for t, d in found)
    return (f'<div class="glossary"><h2>Glossary</h2>'
            f'<p class="muted" style="margin-top:0;font-size:.82rem">Terms &amp; acronyms used on this page.</p>'
            f'<dl>{rows}</dl></div>')


def md_inline(s):
    s = htmllib.escape(s)
    # Protect code/citation spans in placeholders so later bold/italic passes can't mangle the
    # identifiers inside them (e.g. app_link, BROKER_APP_LINK, src/main/**).
    stash = []

    def keep(html):
        stash.append(html)
        return f"\x00{len(stash) - 1}\x00"

    # citations in backticks with #Lnn -> evidence chip (before plain code so chips win)
    s = re.sub(r'`([^`]+?#L[\d,\u2013\-]+)`',
               lambda m: keep(f'<span class="ev">{m.group(1)}</span>'), s)
    # plain inline code
    s = re.sub(r'`([^`]+)`', lambda m: keep('<code>' + m.group(1) + '</code>'), s)
    # bold **...**
    s = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', s)
    # italics: *text* and _text_ (underscores must be word-boundaried so app_link/FOO_BAR are safe)
    s = re.sub(r'(?<![\w\x00])_(?!_)([^_\n]+?)_(?![\w\x00])', r'<em>\1</em>', s)
    s = re.sub(r'(?<!\*)\*(?!\*)([^*\n]+?)\*(?!\*)', r'<em>\1</em>', s)
    # markdown links [t](u): http(s)/relative -> real link; repo-relative path/file:line -> evidence chip
    def link_sub(m):
        text, url = m.group(1), m.group(2)
        if url.startswith(("http://", "https://", "../", "./")):
            return f'<a href="{url}">{text}</a>'
        return f'<span class="ev">{text}</span>'
    s = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', link_sub, s)
    # restore protected code/citation spans
    s = re.sub(r'\x00(\d+)\x00', lambda m: stash[int(m.group(1))], s)
    return s


# h2 sections whose names get wrapped in a styled callout box (matched by substring, case-insensitive).
# Match strings avoid characters that get HTML-escaped in the heading (e.g. '&' -> '&amp;').
CALLOUT_SECTIONS = [
    ("why likely not exploited", "didbox"),
    ("defense-in-depth: why", "didbox"),
    ("defense-in-depth: partial", "didbox"),
    ("verification gaps", "gapbox"),
    ("what we need", "gapbox"),
    ("open questions", "gapbox"),
    ("verification boundary", "disclaimer"),
    ("decisions needed", "decisionbox"),
    ("decision needed", "decisionbox"),
    ("adversarial verification", "verifybox"),
    ("remediation spec", "fixbox"),
    ("remediation", "fixbox"),
    ("searches run", "audit"),
]


def wrap_callouts(html_body):
    """Wrap specific h2 sections (and their content up to the next h2) in styled callout boxes."""
    parts = re.split(r'(<h2>.*?</h2>)', html_body, flags=re.DOTALL)
    out, i = [], 0
    while i < len(parts):
        seg = parts[i]
        m = re.match(r'<h2>(.*?)</h2>', seg, flags=re.DOTALL)
        if m:
            title = re.sub(r'<[^>]+>', '', m.group(1)).strip().lower()
            cls = next((c for name, c in CALLOUT_SECTIONS if name in title), None)
            content = parts[i + 1] if i + 1 < len(parts) else ""
            if cls == "audit":
                # Collapse the heavy audit trail into a <details> so humans aren't drowned,
                # while keeping the full evidence one click away.
                out.append(f'<details class="audit"><summary>{m.group(1)} '
                           f'<span style="font-weight:400;font-size:.8rem">(click to expand)</span></summary>'
                           f'{content}</details>')
            else:
                out.append(f'<div class="{cls}">{seg}{content}</div>' if cls else seg + content)
            i += 2
        else:
            out.append(seg)
            i += 1
    return "".join(out)


def md_to_html(md):
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            i += 1; continue
        if ln.startswith("# "):
            out.append(f"<h1>{md_inline(ln[2:])}</h1>"); i += 1; continue
        if ln.startswith("## "):
            out.append(f"<h2>{md_inline(ln[3:])}</h2>"); i += 1; continue
        if ln.startswith("### "):
            out.append(f"<h3>{md_inline(ln[4:])}</h3>"); i += 1; continue
        if ln.strip() in ("---", "***"):
            out.append("<hr>"); i += 1; continue
        if ln.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip(">").strip()); i += 1
            out.append(f"<blockquote>{md_inline(' '.join(buf))}</blockquote>"); continue
        if ln.lstrip().startswith(("- ", "* ")):
            buf = []
            while i < len(lines) and lines[i].lstrip().startswith(("- ", "* ")):
                buf.append(f"<li>{md_inline(lines[i].lstrip()[2:])}</li>"); i += 1
            out.append("<ul>" + "".join(buf) + "</ul>"); continue
        if re.match(r'^\d+\. ', ln.lstrip()):
            buf = []
            while i < len(lines) and re.match(r'^\d+\. ', lines[i].lstrip()):
                buf.append(f"<li>{md_inline(re.sub(r'^\d+\. ', '', lines[i].lstrip()))}</li>"); i += 1
            out.append("<ol>" + "".join(buf) + "</ol>"); continue
        if ln.lstrip().startswith("|"):
            tbl = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                tbl.append(lines[i]); i += 1
            out.append(render_table(tbl)); continue
        # paragraph
        buf = []
        while i < len(lines) and lines[i].strip() and not lines[i].lstrip()[:2] in ("- ", "* ") \
                and not lines[i].startswith(("#", ">", "|")) and lines[i].strip() not in ("---",):
            buf.append(lines[i]); i += 1
        # Preserve semantic line breaks: each source line in the block is its own visual line.
        # A line that begins with a bold label (e.g. **Verdict:**) becomes its own spaced paragraph
        # so labeled key/value lines don't run together; other lines join under it with <br>.
        para, group = [], []

        def flush_group():
            if group:
                para.append('<p class="kv">' + "<br>".join(md_inline(x) for x in group) + "</p>")
                group.clear()

        for raw in buf:
            line = raw.strip()
            if re.match(r'^\*\*[^*]+:\*\*', line):  # bold "Label:" start -> new spaced block
                flush_group()
                group.append(line)
            else:
                group.append(line)
        flush_group()
        out.append("".join(para))
    return "\n".join(out)


def render_table(rows):
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    if len(cells) >= 2 and all(set(c) <= set("-: ") for c in cells[1]):
        head, body = cells[0], cells[2:]
    else:
        head, body = None, cells
    h = "<table>"
    if head:
        h += "<thead><tr>" + "".join(f"<th>{md_inline(c)}</th>" for c in head) + "</tr></thead>"
    h += "<tbody>" + "".join("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</tbody></table>"
    return h


def _clean(v):
    """Strip markdown emphasis/parentheticals for tile display."""
    if not v:
        return ""
    v = re.sub(r'`[^`]*`', '', v)            # drop code spans
    v = re.sub(r'\*\*([^*]+)\*\*', r'\1', v)  # unbold
    v = re.sub(r'[_*]', '', v)                # stray emphasis
    v = re.sub(r'\s*[_(].*$', '', v).strip()  # drop trailing parenthetical/italic note
    v = v.split('·')[0].strip()
    return v


def parse_meta(md):
    """Pull the at-a-glance fields from a finding's markdown for the stat tiles."""
    meta = {}
    for m in re.finditer(r'^\*\*([\w /&-]+):\*\*\s*(.+)$', md, re.MULTILINE):
        meta[m.group(1).strip().lower()] = m.group(2).strip()
    # our severity tier from the 'Ours' classification-table row
    for line in md.splitlines():
        if re.match(r'\s*\|\s*\*\*?Ours', line):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) >= 3:
                meta['our_tier'] = cells[2]
            break
    meta['passes'] = 2 if re.search(r'^##\s*Adversarial Verification', md, re.MULTILINE) else 1
    return meta


def _sev_cls(tier):
    t = (tier or "").lower()
    if "critical" in t:
        return "t-crit", "Critical"
    if "important" in t:
        return "t-imp", "Important"
    if "moderate" in t:
        return "t-mod", "Moderate"
    if "low" in t or "won't" in t or "wont" in t:
        return "t-low", "Low"
    return "t-pass", tier or "—"


def tiles_html(md):
    """Build the colorful stat-tile band from the finding metadata."""
    m = parse_meta(md)
    tiles = []

    sev_cls, sev_txt = _sev_cls(m.get('our_tier', ''))
    filed = ""
    for line in md.splitlines():
        if re.match(r'\s*\|\s*\*\*?Filed', line):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if len(cells) >= 3:
                filed = _clean(cells[2])
            break
    tiles.append((sev_cls, "Our Severity", sev_txt, (f"filed: {filed}" if filed else "")))

    # IcM Sev (team response-urgency) tile
    sev_icm_raw = _clean(m.get('icm severity', m.get('icm sev', '')))
    sev_icm = sev_icm_raw.replace(" ", "").lower()
    icm_map = {
        "sev2": ("t-sev2", "Sev2", "page on-call — outside business hrs"),
        "sev2.5": ("t-sev25", "Sev2.5", "immediate — business hrs"),
        "sev25": ("t-sev25", "Sev2.5", "immediate — business hrs"),
        "sev3": ("t-sev3", "Sev3", "soon, not drop-everything"),
        "sev4": ("t-sev4", "Sev4", "low priority / hygiene"),
    }
    if sev_icm in icm_map:
        c, v, s = icm_map[sev_icm]
        tiles.append((c, "IcM Severity (urgency)", v, s))

    conf = _clean(m.get('confidence', '')).lower()
    conf_cls = {"high": "t-high", "medium": "t-med", "low": "t-lowc"}.get(conf, "t-pass")
    tiles.append((conf_cls, "Confidence", conf.title() or "—", "adversarial-verified"))

    verdict = _clean(m.get('verdict', ''))
    vlow = verdict.lower()
    v_cls = "t-down" if "down" in vlow else "t-up" if "up" in vlow else "t-agree"
    tiles.append((v_cls, "Verdict vs. filed", verdict or "—", ""))

    passes = m.get('passes', 1)
    tiles.append(("t-pass", "Investigation Passes", f"{passes}-pass",
                  "investigate + adversarial" if passes == 2 else "single pass"))

    ext = m.get('external validation', m.get('external dependency', ''))
    ext_l = ext.lower()
    if ext:
        is_yes = ext_l.startswith(("yes", "y ")) or "unverified" in ext_l or "inferred" in ext_l
    else:
        # fall back: a Scope & Verification Boundary disclaimer always implies some external dependency
        is_yes = bool(re.search(r'cannot (conclude|verify)|server-side|inferred|downstream', md, re.IGNORECASE))
    ext_cls = "t-ext-yes" if is_yes else "t-ext-no"
    ext_val = "Yes — partly theoretical" if is_yes else "No — fully in our code"
    ext_sub = _clean_sub(ext) if ext else ("verdict leans on server/downstream we can't verify" if is_yes
                                           else "all controls verified in code we own")
    tiles.append((ext_cls, "External Validation Needed", ext_val, ext_sub))

    asn = _clean(m.get('assignment', ''))
    asn_cls = "t-eng" if "engineer" in asn.lower() else "t-intern"
    tiles.append((asn_cls, "Assignment", asn or "—",
                  "remediation spec" if "engineer" in asn.lower() else "delegatable / fix notes"))

    cells = "".join(
        f'<div class="tile {cls}"><div class="lbl">{htmllib.escape(lbl)}</div>'
        f'<div class="val">{htmllib.escape(val)}</div>'
        + (f'<div class="sub">{htmllib.escape(sub)}</div>' if sub else "")
        + "</div>"
        for cls, lbl, val, sub in tiles
    )
    return f'<div class="tiles">{cells}</div>'


def _clean_sub(v):
    """Short subtitle from the external-validation note (keep the 'what', trim markers)."""
    v = re.sub(r'`[^`]*`', '', v)
    v = re.sub(r'[_*]', '', v)
    v = re.sub(r'^(yes|no)\s*[—\-:]\s*', '', v, flags=re.IGNORECASE).strip()
    return (v[:90] + "…") if len(v) > 92 else v


def bottomline_html(md):
    """Render the **Bottom line:** field as a prominent TL;DR lead callout."""
    m = re.search(r'^\*\*Bottom line:\*\*\s*(.+)$', md, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    return f'<div class="tldr"><strong>Bottom line:</strong> {md_inline(m.group(1).strip())}</div>'


def page(title, body_html, subtitle="", tiles="", tldr="", agent_link=""):
    agent_btn = (f'<a class="agentlink" href="{agent_link}">&#128221; Agent dispatch spec (machine-readable)</a>'
                 if agent_link else "")
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"><title>{htmllib.escape(title)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<a class="back" href="../wbr-security-report.html">&larr; Back to WBR overview</a>
<header class="top"><h1>{htmllib.escape(title)}</h1>{f'<div class="sub">{subtitle}</div>' if subtitle else ''}{('<br>' + agent_btn) if agent_btn else ''}</header>
{tiles}
{tldr}
<div class="body">{body_html}</div>
<footer>Evidence record generated by the <code>vuln-triage-reporter</code> skill via parallel <code>codebase-researcher</code> investigation (two-pass: investigate + adversarial).<br>
File:line citations are repo-relative; no exploit PoC or PII included. Full machine-actionable spec: the linked <code>.agent.md</code>.</footer>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="Markdown files or globs")
    ap.add_argument("--out", default="research", help="Output dir (default: research)")
    ap.add_argument("--index", action="store_true", help="Also write index.html")
    ap.add_argument("--glossary", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "references", "glossary.md"),
                    help="Glossary markdown file (default: the skill's references/glossary.md)")
    ap.add_argument("--agent-dir", default=None,
                    help="Relative dir (from --out) where matching <slug>.agent.md specs live; "
                         "adds an 'Agent dispatch spec' link to each page when the file exists.")
    args = ap.parse_args()

    glossary = load_glossary(args.glossary)

    files = []
    for inp in args.inputs:
        files.extend(glob.glob(inp, recursive=True) if any(c in inp for c in "*?[") else [inp])
    files = [f for f in files if f.endswith(".md")]

    os.makedirs(args.out, exist_ok=True)
    generated = []
    for f in sorted(files):
        md = open(f, encoding="utf-8").read()
        first = next((l[2:].strip() for l in md.split("\n") if l.startswith("# ")), os.path.basename(f))
        slug = re.sub(r'[^a-z0-9]+', '-', os.path.splitext(os.path.basename(f))[0].lower()).strip('-')
        # disambiguate ITD READMEs (all named readme) by parent folder
        if slug in ("readme",):
            slug = re.sub(r'[^a-z0-9]+', '-', os.path.basename(os.path.dirname(f)).lower()).strip('-')
        outp = os.path.join(args.out, slug + ".html")
        agent_link = ""
        if args.agent_dir:
            rel = f"{args.agent_dir.rstrip('/')}/{slug}.agent.md"
            if os.path.isfile(os.path.join(args.out, rel)):
                agent_link = rel
        body = wrap_callouts(md_to_html(md)) + glossary_html(md, glossary)
        open(outp, "w", encoding="utf-8").write(
            page(first, body, tiles=tiles_html(md), tldr=bottomline_html(md), agent_link=agent_link))
        generated.append((first, slug + ".html"))
        print("  +", outp)

    if args.index:
        body = "<h2>Deep-research evidence records</h2><p class='muted'>One page per finding: confirmed sink, defense-in-depth sweep, adversarial verification, remediation spec, and the verbatim <strong>Searches Run</strong> audit trail.</p><div class='idx'>"
        body += "".join(f'<a href="{u}">{htmllib.escape(t)}</a>' for t, u in generated)
        body += "</div>"
        open(os.path.join(args.out, "index.html"), "w", encoding="utf-8").write(page("Research evidence index", body))
        print("  + index.html")
    print(f"\nDone. {len(generated)} subpages.")


if __name__ == "__main__":
    main()
