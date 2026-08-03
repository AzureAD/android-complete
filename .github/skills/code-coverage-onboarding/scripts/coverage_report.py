#!/usr/bin/env python3
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""
Portable code-coverage parser, reporter, and week-over-week (WoW) renderer.

Designed to be dropped into any repo's CI (Azure DevOps, GitHub Actions, or any
runner that can execute a Python script after a test run). Stdlib only - no
third-party dependencies - so it runs on any pipeline image.

Reads JaCoCo XML (Gradle/Maven/Android), Cobertura XML (Coverlet/VSTest,
coverage.py, gcovr, etc.), OR LCOV tracefiles (lcov.info; the default for many
JS/TS and C/C++ setups) and normalizes to a single row schema that matches a
Kusto tracking table:

    Date, Repo, Module, Metric, Covered, Missed, Percentage, CommitId, BuildId, Branch

Subcommands:
  parse  - parse one or more coverage files -> append normalized NDJSON rows.
  report - aggregate NDJSON rows -> Markdown summary (+ optional combined JSON).
  gaps    - rank the biggest uncovered classes/files -> a test-writing worklist
            for raising coverage (per-class/file, not aggregate).
  compare - diff a base report vs a PR/branch report per-class -> Markdown+JSON
            showing where coverage regressed; exits non-zero on a drop beyond
            --tolerance so it can gate a PR check.
  gate    - gate a build on a coverage drop vs the last-ingested Kusto baseline
            for a branch (module-level; no rebuild of the target branch needed).
            Exits non-zero on a drop and lists the modules that regressed.
  wow     - query a Kusto table for week-over-week coverage -> HTML fragment for
            an email/report. Supports splitting modules into named tables, each
            with an "Overall" summary row.

Typical CI flow:
  1) Run tests so the build tool emits a JaCoCo/Cobertura XML report.
  2) `parse` each XML into a shared NDJSON file (one row per module+metric).
  3) `report` the NDJSON into a Markdown summary artifact.
  4) Ingest the NDJSON into Kusto (see references/kusto-setup.md) for history.
  5) On the scheduled reporting run, `wow` renders the trend into the email.
"""

import argparse
import glob
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# Metrics normalized across both report formats. LINE is the primary tracking
# metric; BRANCH is captured too when the report provides it.
METRICS = ("LINE", "BRANCH")


def _detect_format(root):
    """Return 'jacoco' or 'cobertura' based on the XML root element."""
    tag = root.tag.lower()
    if tag == "report":
        return "jacoco"
    if tag == "coverage":
        return "cobertura"
    raise ValueError(f"Unrecognized coverage XML root element: <{root.tag}>")


def _parse_jacoco(root):
    """Extract {metric: (covered, missed)} from a JaCoCo report root.

    The top-level <counter> children of <report> hold the aggregate totals.
    """
    result = {}
    for counter in root.findall("./counter"):
        ctype = counter.get("type", "").upper()
        if ctype in METRICS:
            covered = int(counter.get("covered", 0))
            missed = int(counter.get("missed", 0))
            result[ctype] = (covered, missed)
    return result


def _parse_cobertura(root):
    """Extract {metric: (covered, missed)} from a Cobertura coverage root.

    Coverlet/VSTest 'Format=Cobertura', coverage.py, and gcovr emit
    lines-covered / lines-valid / branches-covered / branches-valid attributes.
    Fall back to counting <line> elements when those attributes are absent.
    """
    result = {}

    lines_covered = root.get("lines-covered")
    lines_valid = root.get("lines-valid")
    if lines_covered is not None and lines_valid is not None:
        covered = int(lines_covered)
        valid = int(lines_valid)
        result["LINE"] = (covered, max(valid - covered, 0))
    else:
        covered = missed = 0
        for line in root.iter("line"):
            hits = int(line.get("hits", 0))
            if hits > 0:
                covered += 1
            else:
                missed += 1
        result["LINE"] = (covered, missed)

    br_covered = root.get("branches-covered")
    br_valid = root.get("branches-valid")
    if br_covered is not None and br_valid is not None:
        covered = int(br_covered)
        valid = int(br_valid)
        result["BRANCH"] = (covered, max(valid - covered, 0))

    return result


def _parse_lcov(text):
    """Extract {metric: (covered, missed)} from an LCOV tracefile (lcov.info).

    LCOV is line-oriented text with one block per source file. Aggregate over all
    files using the per-file summary records:
      LF:/LH:  = lines found / lines hit          -> LINE
      BRF:/BRH:= branches found / branches hit     -> BRANCH
    Fall back to counting DA:<line>,<hits> records when LF/LH are absent (some
    tools omit the summaries). FNF/FNH (functions) are ignored - we track LINE
    and BRANCH to stay consistent with the JaCoCo/Cobertura output.
    """
    lines_found = lines_hit = 0
    br_found = br_hit = 0
    have_line_summary = have_branch = False
    da_covered = da_missed = 0

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("LF:"):
            lines_found += int(line[3:] or 0)
            have_line_summary = True
        elif line.startswith("LH:"):
            lines_hit += int(line[3:] or 0)
        elif line.startswith("BRF:"):
            br_found += int(line[4:] or 0)
            have_branch = True
        elif line.startswith("BRH:"):
            br_hit += int(line[4:] or 0)
        elif line.startswith("DA:"):
            parts = line[3:].split(",")
            if len(parts) >= 2:
                try:
                    hits = int(parts[1])
                except ValueError:
                    hits = 0
                if hits > 0:
                    da_covered += 1
                else:
                    da_missed += 1

    result = {}
    if have_line_summary:
        result["LINE"] = (lines_hit, max(lines_found - lines_hit, 0))
    else:
        result["LINE"] = (da_covered, da_missed)
    if have_branch:
        result["BRANCH"] = (br_hit, max(br_found - br_hit, 0))
    return result


def _looks_like_lcov(path):
    """Sniff whether a file is an LCOV tracefile (text, not XML)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for _ in range(50):
                line = handle.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                if stripped.startswith("<"):
                    return False  # XML
                if stripped.startswith(("TN:", "SF:", "DA:", "LF:", "LH:",
                                        "BRF:", "BRH:", "FNF:", "FNH:")) \
                        or stripped == "end_of_record":
                    return True
    except OSError:
        return False
    return False


def parse_file(path, fmt="auto"):
    """Parse one coverage file -> {metric: (covered, missed)}.

    Supports JaCoCo XML, Cobertura XML, and LCOV tracefiles. With fmt='auto',
    LCOV is detected by content sniffing and XML by its root element.
    """
    if fmt == "lcov" or (fmt == "auto" and _looks_like_lcov(path)):
        with open(path, encoding="utf-8", errors="replace") as handle:
            return _parse_lcov(handle.read())

    tree = ET.parse(path)
    root = tree.getroot()
    detected = _detect_format(root) if fmt == "auto" else fmt
    if detected == "jacoco":
        return _parse_jacoco(root)
    if detected == "cobertura":
        return _parse_cobertura(root)
    raise ValueError(f"Unsupported coverage format: {detected}")


def _gaps_jacoco(root, metric):
    """Per-class (covered, missed) for a metric from a JaCoCo report."""
    units = []
    for cls in root.iter("class"):
        name = (cls.get("name") or "").replace("/", ".")
        for counter in cls.findall("counter"):
            if counter.get("type", "").upper() == metric:
                units.append({
                    "Unit": name,
                    "Covered": int(counter.get("covered", 0)),
                    "Missed": int(counter.get("missed", 0)),
                })
                break
    return units


def _gaps_cobertura(root, metric):
    """Per-class (covered, missed) for a metric from a Cobertura report.

    LINE counts <line hits=..> elements. BRANCH reads the
    'condition-coverage="P% (c/t)"' attribute Coverlet/coverage.py emit.
    """
    units = []
    for cls in root.iter("class"):
        name = cls.get("filename") or cls.get("name") or ""
        covered = missed = 0
        if metric == "LINE":
            for line in cls.iter("line"):
                if int(line.get("hits", 0)) > 0:
                    covered += 1
                else:
                    missed += 1
        elif metric == "BRANCH":
            for line in cls.iter("line"):
                cond = line.get("condition-coverage") or ""
                if "(" in cond and "/" in cond:
                    frac = cond[cond.find("(") + 1:cond.find(")")]
                    try:
                        cov, tot = (int(x) for x in frac.split("/"))
                    except ValueError:
                        continue
                    covered += cov
                    missed += max(tot - cov, 0)
        if covered + missed > 0:
            units.append({"Unit": name, "Covered": covered, "Missed": missed})
    return units


def _gaps_lcov(text, metric):
    """Per-source-file (covered, missed) for a metric from an LCOV tracefile."""
    units = []
    cur = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            cur = {"Unit": line[3:], "LF": 0, "LH": 0, "BRF": 0, "BRH": 0,
                   "da_c": 0, "da_m": 0, "has_line": False, "has_br": False}
        elif cur is None:
            continue
        elif line.startswith("LF:"):
            cur["LF"] = int(line[3:] or 0)
            cur["has_line"] = True
        elif line.startswith("LH:"):
            cur["LH"] = int(line[3:] or 0)
        elif line.startswith("BRF:"):
            cur["BRF"] = int(line[4:] or 0)
            cur["has_br"] = True
        elif line.startswith("BRH:"):
            cur["BRH"] = int(line[4:] or 0)
        elif line.startswith("DA:"):
            parts = line[3:].split(",")
            if len(parts) >= 2:
                try:
                    hits = int(parts[1])
                except ValueError:
                    hits = 0
                if hits > 0:
                    cur["da_c"] += 1
                else:
                    cur["da_m"] += 1
        elif line == "end_of_record":
            if metric == "LINE":
                if cur["has_line"]:
                    covered, missed = cur["LH"], max(cur["LF"] - cur["LH"], 0)
                else:
                    covered, missed = cur["da_c"], cur["da_m"]
                units.append({"Unit": cur["Unit"], "Covered": covered, "Missed": missed})
            elif metric == "BRANCH" and cur["has_br"]:
                units.append({"Unit": cur["Unit"], "Covered": cur["BRH"],
                              "Missed": max(cur["BRF"] - cur["BRH"], 0)})
            cur = None
    return units


def gap_units(path, metric, fmt="auto"):
    """Parse one coverage file -> per-class/file [{Unit, Covered, Missed}] for a metric."""
    if fmt == "lcov" or (fmt == "auto" and _looks_like_lcov(path)):
        with open(path, encoding="utf-8", errors="replace") as handle:
            return _gaps_lcov(handle.read(), metric)

    tree = ET.parse(path)
    root = tree.getroot()
    detected = _detect_format(root) if fmt == "auto" else fmt
    if detected == "jacoco":
        return _gaps_jacoco(root, metric)
    if detected == "cobertura":
        return _gaps_cobertura(root, metric)
    raise ValueError(f"Unsupported coverage format: {detected}")


def _pct(covered, missed):
    total = covered + missed
    return round(100.0 * covered / total, 2) if total else 0.0


def cmd_parse(args):
    paths = []
    for pattern in args.input:
        paths.extend(glob.glob(pattern, recursive=True))
    if not paths:
        sys.stderr.write(f"ERROR: no coverage files matched: {', '.join(args.input)}\n")
        return 2

    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for path in sorted(set(paths)):
        try:
            metrics = parse_file(path, args.format)
        except (ET.ParseError, ValueError, OSError) as exc:
            sys.stderr.write(f"WARNING: skipping {path}: {exc}\n")
            continue
        for metric, (covered, missed) in metrics.items():
            rows.append(
                {
                    "Date": date,
                    "Repo": args.repo,
                    "Module": args.module,
                    "Metric": metric,
                    "Covered": covered,
                    "Missed": missed,
                    "Percentage": _pct(covered, missed),
                    "CommitId": args.commit,
                    "BuildId": args.build_id,
                    "Branch": args.branch,
                }
            )

    if not rows:
        sys.stderr.write(f"ERROR: no usable coverage data parsed from {paths}\n")
        return 2

    with open(args.out, "a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    for row in rows:
        print(f"{row['Repo']}/{row['Module']} {row['Metric']}: {row['Percentage']}% "
              f"({row['Covered']}/{row['Covered'] + row['Missed']})")
    return 0


def _load_rows(patterns):
    rows = []
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
    return rows


def cmd_report(args):
    rows = _load_rows(args.input)
    if not rows:
        sys.stderr.write("ERROR: no coverage rows found to report on\n")
        return 2

    metric = args.metric.upper()
    filtered = [r for r in rows if r["Metric"] == metric]

    # Aggregate covered/missed to a per-repo total across its modules.
    per_repo = {}
    for r in filtered:
        agg = per_repo.setdefault(r["Repo"], {"Covered": 0, "Missed": 0})
        agg["Covered"] += r["Covered"]
        agg["Missed"] += r["Missed"]

    lines = [
        f"# Weekly Code Coverage ({metric})",
        "",
        f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} "
        f"- goal: {args.goal}%_",
        "",
        "| Repo | Coverage | Covered | Total | Goal gap |",
        "| --- | --- | --- | --- | --- |",
    ]
    for repo in sorted(per_repo):
        covered = per_repo[repo]["Covered"]
        missed = per_repo[repo]["Missed"]
        total = covered + missed
        pct = _pct(covered, missed)
        gap = round(args.goal - pct, 2)
        gap_txt = "met" if gap <= 0 else f"+{gap}%"
        lines.append(f"| {repo} | {pct}% | {covered} | {total} | {gap_txt} |")

    markdown = "\n".join(lines) + "\n"

    if args.out_md:
        with open(args.out_md, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as handle:
            json.dump(filtered, handle, indent=2)

    print(markdown)
    return 0


def _kusto_query(cluster, database, kql, token):
    """POST a KQL query to the Kusto v1 REST endpoint; return list-of-dict rows.

    Stdlib only. The bearer token must already be scoped to the Kusto cluster
    resource (https://kusto.kusto.windows.net or the cluster URL).
    """
    url = cluster.rstrip("/") + "/v1/rest/query"
    payload = json.dumps({"db": database, "csl": kql}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    tables = data.get("Tables") or []
    if not tables:
        return []
    primary = tables[0]
    columns = [c.get("ColumnName") for c in primary.get("Columns", [])]
    rows = []
    for raw in primary.get("Rows", []):
        rows.append(dict(zip(columns, raw)))
    return rows


def _wow_kql(table, metric):
    """KQL comparing each module's latest coverage row to last week's run.

    Calendar semantics (NOT a rolling 7-day span): 'curr' is the most recent row
    per module; 'prior' is the most recent row from BEFORE the current week
    started. startofweek() anchors to Sunday 00:00, so same-week manual re-runs
    never count as the WoW baseline. If two rows exist for a module on the same
    day, arg_max(Date, ...) keeps the latest run of that day.

    Emits PrevCovered/PrevMissed so an aggregate "Overall" row can compute a true
    summed delta rather than averaging per-module percentages.
    """
    return (
        'let m = "{metric}";\n'
        "let curr = {table}\n"
        "| where Metric == m\n"
        "| summarize arg_max(Date, Covered, Missed, Percentage) by Repo, Module;\n"
        "let prior = {table}\n"
        "| where Metric == m\n"
        "| join kind=inner (curr | project Repo, Module, LatestDate = Date) "
        "on Repo, Module\n"
        "| where Date < startofweek(LatestDate)\n"
        "| summarize arg_max(Date, Percentage, Covered, Missed) by Repo, Module;\n"
        "curr\n"
        "| join kind=leftouter (prior | project Repo, Module, "
        "PrevPercentage = Percentage, PrevDate = Date, "
        "PrevCovered = Covered, PrevMissed = Missed) on Repo, Module\n"
        "| extend PrevFromLastWeek = iff(isnull(PrevDate), bool(null), "
        "PrevDate >= startofweek(Date) - 7d)\n"
        "| project Repo, Module, CurrentPct = Percentage, Covered, Missed, "
        "PrevPct = PrevPercentage, PrevDate, PrevFromLastWeek, "
        "PrevCovered, PrevMissed\n"
        "| order by Repo asc, Module asc"
    ).format(table=table, metric=metric)


def _coverage_table(title, rows):
    """Render one coverage table (with an Overall summary row) as HTML lines.

    Returns [] when there are no rows so the caller can omit the section. The
    Overall row sums Covered/Total across the table's modules; its WoW delta is
    computed from summed prior Covered/Missed (a true aggregate, not an average
    of per-module percentages).
    """
    if not rows:
        return []

    out = [
        f"<u>{title}</u>",
        "<table>",
        "<tr><th>Module</th><th>Coverage</th><th>&Delta; WoW</th>"
        "<th>Covered/Total</th></tr>",
    ]
    stale_baseline = False
    sum_covered = sum_missed = 0
    sum_prev_covered = sum_prev_missed = 0
    have_prev = False

    for r in rows:
        module = r.get("Module", "")
        cur = r.get("CurrentPct")
        prev = r.get("PrevPct")
        covered = int(r.get("Covered") or 0)
        missed = int(r.get("Missed") or 0)
        total = covered + missed
        cur_val = round(float(cur), 2) if cur is not None else 0.0

        sum_covered += covered
        sum_missed += missed

        if prev is None:
            delta_cell = "<td>n/a</td>"
        else:
            have_prev = True
            sum_prev_covered += int(r.get("PrevCovered") or 0)
            sum_prev_missed += int(r.get("PrevMissed") or 0)
            delta = round(cur_val - float(prev), 2)
            color = "green" if delta >= 0 else "red"
            sign = "+" if delta >= 0 else ""
            marker = ""
            if r.get("PrevFromLastWeek") is False:
                marker = " *"
                stale_baseline = True
            delta_cell = f'<td style="color:{color}">{sign}{delta}%{marker}</td>'

        out.append(
            f"<tr><td>{module}</td><td>{cur_val}%</td>"
            f"{delta_cell}<td>{covered}/{total}</td></tr>"
        )

    overall_total = sum_covered + sum_missed
    overall_pct = _pct(sum_covered, sum_missed)
    if have_prev:
        overall_prev_pct = _pct(sum_prev_covered, sum_prev_missed)
        overall_delta = round(overall_pct - overall_prev_pct, 2)
        color = "green" if overall_delta >= 0 else "red"
        sign = "+" if overall_delta >= 0 else ""
        overall_delta_cell = (
            f'<td style="color:{color}"><b>{sign}{overall_delta}%</b></td>'
        )
    else:
        overall_delta_cell = "<td><b>n/a</b></td>"
    out.append(
        f"<tr><td><b>Overall</b></td><td><b>{overall_pct}%</b></td>"
        f"{overall_delta_cell}<td><b>{sum_covered}/{overall_total}</b></td></tr>"
    )

    out.append("</table>")
    if stale_baseline:
        out.append(
            "<i>* last week's run had no coverage data for this module; "
            "&Delta; WoW is measured against an earlier week's run.</i>"
        )
    return out


def _load_groups(args):
    """Return an ordered list of (title, module_predicate) for wow tables.

    --group-file is a JSON object mapping table title -> list of module names
    (case-insensitive). Modules not listed in any group fall into a trailing
    catch-all table (title from --other-title). With no --group-file, all
    modules render in a single table titled --single-title.
    """
    if not args.group_file:
        return [(args.single_title, lambda r: True)], None

    with open(args.group_file, encoding="utf-8") as handle:
        mapping = json.load(handle)

    groups = []
    assigned = set()
    for title, modules in mapping.items():
        wanted = {str(m).strip().lower() for m in modules}
        assigned |= wanted
        groups.append(
            (title, (lambda w: (lambda r: (r.get("Module") or "").strip().lower() in w))(wanted))
        )
    other_pred = (lambda r: (r.get("Module") or "").strip().lower() not in assigned)
    return groups, (args.other_title, other_pred)


def _wow_html(rows, metric, groups, other):
    """Render WoW coverage rows as an HTML fragment, split into grouped tables."""
    out = ["<br/>", f"<u>Weekly Code Coverage ({metric})</u>"]
    tables = []
    for title, pred in groups:
        tables.append(_coverage_table(title, [r for r in rows if pred(r)]))
    if other:
        other_title, other_pred = other
        tables.append(_coverage_table(other_title, [r for r in rows if other_pred(r)]))

    rendered = [t for t in tables if t]
    for i, table in enumerate(rendered):
        out.extend(table)
        if i < len(rendered) - 1:
            out.append("<br/>")
    return "\n".join(out) + "\n"


def cmd_wow(args):
    """Query Kusto for WoW coverage and emit an HTML fragment.

    Non-fatal by design: the report must still send when the coverage section
    can't be produced, so failures emit a short note instead of a non-zero exit.
    """
    metric = args.metric.upper()
    groups, other = _load_groups(args)

    def _emit(html):
        if args.out_html:
            with open(args.out_html, "w", encoding="utf-8") as handle:
                handle.write(html)
        print(html)
        return 0

    token = os.environ.get(args.token_env, "").strip()
    if not token:
        sys.stderr.write(
            f"WARNING: no Kusto token in ${args.token_env}; skipping coverage section\n"
        )
        return _emit(
            f"<br/><u>Weekly Code Coverage ({metric})</u><br/>"
            "Coverage data unavailable this run (no Kusto access token).\n"
        )

    try:
        rows = _kusto_query(args.cluster, args.db, _wow_kql(args.table, metric), token)
    except (urllib.error.URLError, ValueError, KeyError) as exc:
        sys.stderr.write(f"WARNING: Kusto coverage query failed: {exc}\n")
        return _emit(
            f"<br/><u>Weekly Code Coverage ({metric})</u><br/>"
            "Coverage data unavailable this run (Kusto query failed).\n"
        )

    if not rows:
        return _emit(
            f"<br/><u>Weekly Code Coverage ({metric})</u><br/>"
            "No coverage rows found in Kusto yet.\n"
        )

    return _emit(_wow_html(rows, metric, groups, other))


def cmd_gaps(args):
    """Rank the biggest uncovered classes/files as a test-writing worklist."""
    paths = []
    for pattern in args.input:
        paths.extend(glob.glob(pattern, recursive=True))
    if not paths:
        sys.stderr.write(f"ERROR: no coverage files matched: {', '.join(args.input)}\n")
        return 2

    metric = args.metric.upper()
    agg = {}
    for path in sorted(set(paths)):
        try:
            units = gap_units(path, metric, args.format)
        except (ET.ParseError, ValueError, OSError) as exc:
            sys.stderr.write(f"WARNING: skipping {path}: {exc}\n")
            continue
        for u in units:
            entry = agg.setdefault(u["Unit"], {"Covered": 0, "Missed": 0})
            entry["Covered"] += u["Covered"]
            entry["Missed"] += u["Missed"]

    rows = []
    for unit, cm in agg.items():
        covered, missed = cm["Covered"], cm["Missed"]
        if missed < args.min_missed:
            continue
        pct = _pct(covered, missed)
        if pct > args.max_pct:
            continue
        rows.append({"Unit": unit, "Covered": covered, "Missed": missed,
                     "Total": covered + missed, "Percentage": pct})

    if not rows:
        sys.stderr.write("No coverage gaps matched the filters "
                         f"(metric={metric}, min-missed={args.min_missed}, "
                         f"max-pct={args.max_pct}).\n")
        return 0

    if args.sort == "pct":
        rows.sort(key=lambda r: (r["Percentage"], -r["Missed"]))
    else:  # "missed" - biggest absolute win first
        rows.sort(key=lambda r: (-r["Missed"], r["Percentage"]))

    top = rows[:args.top] if args.top > 0 else rows

    lines = [
        f"# Coverage Gaps ({metric}) - top {len(top)} targets",
        "",
        f"_Sorted by {'lowest coverage' if args.sort == 'pct' else 'most missed lines'}; "
        f"{sum(r['Missed'] for r in rows)} total {metric.lower()} units uncovered "
        f"across {len(rows)} classes/files._",
        "",
        "| Class / File | Coverage | Missed | Covered/Total |",
        "| --- | --- | --- | --- |",
    ]
    for r in top:
        lines.append(f"| {r['Unit']} | {r['Percentage']}% | {r['Missed']} | "
                     f"{r['Covered']}/{r['Total']} |")
    markdown = "\n".join(lines) + "\n"

    if args.out_md:
        with open(args.out_md, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    if args.out_json:
        with open(args.out_json, "w", encoding="utf-8") as handle:
            json.dump(top, handle, indent=2)

    print(markdown)
    return 0


def _aggregate_units(paths, metric, fmt):
    """Aggregate per-class/file {Covered, Missed} across one or more reports."""
    agg = {}
    for path in sorted(set(paths)):
        try:
            units = gap_units(path, metric, fmt)
        except (ET.ParseError, ValueError, OSError) as exc:
            sys.stderr.write(f"WARNING: skipping {path}: {exc}\n")
            continue
        for u in units:
            entry = agg.setdefault(u["Unit"], {"Covered": 0, "Missed": 0})
            entry["Covered"] += u["Covered"]
            entry["Missed"] += u["Missed"]
    return agg


def cmd_compare(args):
    """Diff a base coverage report against a PR/branch report and gate on regressions.

    Produces an overall pass/fail verdict plus a per-class breakdown of WHERE coverage
    changed, so a PR author knows exactly which classes to add tests for. Exits non-zero
    when overall coverage drops by more than --tolerance percentage points (unless
    --no-fail-on-drop), so it can be used as a blocking PR check.
    """
    metric = args.metric.upper()
    base_paths, pr_paths = [], []
    for pattern in args.base:
        base_paths.extend(glob.glob(pattern, recursive=True))
    for pattern in args.pr:
        pr_paths.extend(glob.glob(pattern, recursive=True))
    if not pr_paths:
        sys.stderr.write(f"ERROR: no PR coverage files matched: {', '.join(args.pr)}\n")
        return 2
    if not base_paths:
        # No baseline to compare against (e.g. first run / new module). Don't block the PR.
        sys.stderr.write("WARNING: no base coverage files matched: "
                         f"{', '.join(args.base)}; skipping comparison (not gating).\n")
        return 0

    base = _aggregate_units(base_paths, metric, args.format)
    pr = _aggregate_units(pr_paths, metric, args.format)

    base_cov = sum(v["Covered"] for v in base.values())
    base_miss = sum(v["Missed"] for v in base.values())
    pr_cov = sum(v["Covered"] for v in pr.values())
    pr_miss = sum(v["Missed"] for v in pr.values())
    base_pct = _pct(base_cov, base_miss)
    pr_pct = _pct(pr_cov, pr_miss)
    delta = round(pr_pct - base_pct, 2)

    # Per-class deltas. Classes present only in the PR with missed units are "new" untested
    # code; classes whose percentage fell are "regressed".
    regressed, new_gaps = [], []
    for unit in sorted(set(base) | set(pr)):
        b = base.get(unit)
        p = pr.get(unit, {"Covered": 0, "Missed": 0})
        p_pct = _pct(p["Covered"], p["Missed"])
        if b is None:
            if p["Missed"] >= args.min_missed:
                new_gaps.append({"Unit": unit, "Percentage": p_pct,
                                 "Missed": p["Missed"],
                                 "Total": p["Covered"] + p["Missed"]})
            continue
        b_pct = _pct(b["Covered"], b["Missed"])
        d = round(p_pct - b_pct, 2)
        if d < -args.unit_tolerance:
            regressed.append({"Unit": unit, "BasePct": b_pct, "PrPct": p_pct,
                              "Delta": d, "Missed": p["Missed"]})

    regressed.sort(key=lambda r: (r["Delta"], -r["Missed"]))  # biggest drop first
    new_gaps.sort(key=lambda r: (-r["Missed"], r["Percentage"]))

    failed = args.fail_on_drop and (pr_pct < base_pct - args.tolerance - 1e-9)
    verdict = "FAIL" if failed else "PASS"
    sign = "+" if delta >= 0 else ""

    lines = [
        f"# Code Coverage Comparison ({metric}) - {verdict}",
        "",
        f"| | Base | PR | Delta |",
        f"| --- | --- | --- | --- |",
        f"| **{metric} coverage** | {base_pct}% | {pr_pct}% | {sign}{delta} pp |",
        "",
    ]
    if args.tolerance:
        lines.append(f"_Allowed drop (tolerance): {args.tolerance} pp._")
        lines.append("")
    if failed:
        lines.append(f"**Coverage dropped by {abs(delta)} pp** (base {base_pct}% -> PR "
                     f"{pr_pct}%), exceeding the allowed {args.tolerance} pp. "
                     "Add tests for the classes below to restore coverage.")
        lines.append("")

    if regressed:
        top_reg = regressed[:args.top] if args.top > 0 else regressed
        lines += [
            f"## Classes with reduced coverage ({len(regressed)})",
            "",
            "| Class / File | Base | PR | Delta | Missed (PR) |",
            "| --- | --- | --- | --- | --- |",
        ]
        for r in top_reg:
            lines.append(f"| {r['Unit']} | {r['BasePct']}% | {r['PrPct']}% | "
                         f"{r['Delta']} pp | {r['Missed']} |")
        lines.append("")

    if new_gaps:
        top_new = new_gaps[:args.top] if args.top > 0 else new_gaps
        lines += [
            f"## New/changed classes lacking coverage ({len(new_gaps)})",
            "",
            "| Class / File | Coverage | Missed | Covered/Total |",
            "| --- | --- | --- | --- |",
        ]
        for r in top_new:
            covered = r["Total"] - r["Missed"]
            lines.append(f"| {r['Unit']} | {r['Percentage']}% | {r['Missed']} | "
                         f"{covered}/{r['Total']} |")
        lines.append("")

    if not regressed and not new_gaps:
        lines.append("_No per-class coverage regressions detected._")
        lines.append("")

    markdown = "\n".join(lines) + "\n"

    if args.out_md:
        with open(args.out_md, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    if args.out_json:
        payload = {
            "metric": metric, "basePercentage": base_pct, "prPercentage": pr_pct,
            "deltaPp": delta, "tolerancePp": args.tolerance, "failed": failed,
            "regressed": regressed, "newGaps": new_gaps,
        }
        with open(args.out_json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    print(markdown)
    return 1 if failed else 0


def _modules_from_rows(rows, metric):
    """Aggregate NDJSON rows into {module: {"Covered": c, "Missed": m}} for one metric."""
    agg = {}
    for r in rows:
        if str(r.get("Metric", "")).upper() != metric:
            continue
        entry = agg.setdefault(r.get("Module", ""), {"Covered": 0, "Missed": 0})
        entry["Covered"] += int(r.get("Covered", 0) or 0)
        entry["Missed"] += int(r.get("Missed", 0) or 0)
    return agg


def _read_ndjson(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _kusto_baseline_modules(args, metric, token):
    """Latest ingested per-module coverage for the baseline branch, as a dict.

    This is the module-level baseline a PR is gated against - the last coverage
    numbers recorded for the target branch. Kusto only stores per-module rows, so
    the gate is module-level (which module regressed); per-class 'where' comes from
    running `gaps` on the regressed modules' current reports.
    """
    branch_filter = ' and Branch == "%s"' % args.branch if args.branch else ""
    kql = (
        "{table}\n"
        '| where Metric == "{metric}" and Repo == "{repo}"{branch}\n'
        "| summarize arg_max(Date, Covered, Missed) by Module\n"
        "| project Module, Covered, Missed"
    ).format(table=args.table, metric=metric, repo=args.repo, branch=branch_filter)
    agg = {}
    for r in _kusto_query(args.cluster, args.db, kql, token):
        agg[r.get("Module", "")] = {"Covered": int(r.get("Covered", 0) or 0),
                                    "Missed": int(r.get("Missed", 0) or 0)}
    return agg


def cmd_gate(args):
    """Gate a build on a coverage drop vs the last-ingested baseline for a branch.

    Unlike `compare` (which diffs two XML report sets per-class and needs the base
    branch rebuilt), `gate` compares this build's per-module coverage against the
    baseline stored in Kusto - so no second build of the target branch is needed.
    Emits an overall verdict plus the list of modules whose coverage fell (the
    'where'), and exits non-zero on a drop beyond --tolerance unless
    --no-fail-on-drop. The regressed-module list is written to --out-json so a
    pipeline can run `gaps` on exactly those modules.
    """
    metric = args.metric.upper()
    current = _modules_from_rows(_read_ndjson(args.current), metric)
    if not current:
        sys.stderr.write("ERROR: no current %s rows found in %s\n" % (metric, args.current))
        return 2

    if args.baseline:
        baseline = _modules_from_rows(_read_ndjson(args.baseline), metric)
    else:
        token = os.environ.get(args.token_env, "")
        if not token:
            sys.stderr.write("ERROR: no Kusto bearer token in $%s\n" % args.token_env)
            return 2
        baseline = _kusto_baseline_modules(args, metric, token)

    def _overall(mods):
        c = sum(v["Covered"] for v in mods.values())
        m = sum(v["Missed"] for v in mods.values())
        return c, m, _pct(c, m)

    cur_c, cur_m, cur_pct = _overall(current)

    if not baseline:
        # No baseline for this branch yet (first run / new branch / nothing ingested).
        # Report but do not gate - blocking here would fail every PR until the first
        # scheduled ingest lands.
        markdown = ("# Code Coverage Gate (%s) - SKIPPED\n\n"
                    "_No baseline coverage found for repo `%s` branch `%s`; not gating. "
                    "Current overall %s coverage: %s%% (%d/%d)._\n"
                    % (metric, args.repo, args.branch or "(any)", metric, cur_pct,
                       cur_c, cur_c + cur_m))
        if args.out_md:
            open(args.out_md, "w", encoding="utf-8").write(markdown)
        if args.out_json:
            json.dump({"metric": metric, "gated": False, "reason": "no-baseline",
                       "currentPercentage": cur_pct, "regressed": []},
                      open(args.out_json, "w", encoding="utf-8"), indent=2)
        print(markdown)
        return 0

    base_c, base_m, base_pct = _overall(baseline)
    delta = round(cur_pct - base_pct, 2)

    regressed, new_mods = [], []
    for mod in sorted(set(baseline) | set(current)):
        b = baseline.get(mod)
        p = current.get(mod, {"Covered": 0, "Missed": 0})
        p_pct = _pct(p["Covered"], p["Missed"])
        if b is None:
            new_mods.append({"Module": mod, "Percentage": p_pct, "Missed": p["Missed"]})
            continue
        b_pct = _pct(b["Covered"], b["Missed"])
        d = round(p_pct - b_pct, 2)
        if d < -args.unit_tolerance:
            regressed.append({"Module": mod, "BasePct": b_pct, "CurrentPct": p_pct,
                              "Delta": d, "Missed": p["Missed"]})
    regressed.sort(key=lambda r: (r["Delta"], -r["Missed"]))
    new_mods.sort(key=lambda r: (-r["Missed"], r["Percentage"]))

    failed = args.fail_on_drop and (cur_pct < base_pct - args.tolerance - 1e-9)
    verdict = "FAIL" if failed else "PASS"
    sign = "+" if delta >= 0 else ""

    lines = [
        "# Code Coverage Gate (%s) - %s" % (metric, verdict),
        "",
        "| | Baseline | This build | Delta |",
        "| --- | --- | --- | --- |",
        "| **%s coverage** | %s%% | %s%% | %s%s pp |" % (metric, base_pct, cur_pct, sign, delta),
        "",
        "_Baseline = last ingested coverage for `%s` branch `%s`._" % (args.repo, args.branch or "(any)"),
        "",
    ]
    if args.tolerance:
        lines += ["_Allowed drop (tolerance): %s pp._" % args.tolerance, ""]
    if failed:
        lines += ["**Overall coverage dropped by %s pp** (baseline %s%% -> %s%%), "
                  "exceeding the allowed %s pp. Add tests to the modules below "
                  "(run the per-class gaps worklist to see which classes)."
                  % (abs(delta), base_pct, cur_pct, args.tolerance), ""]

    if regressed:
        top_reg = regressed[:args.top] if args.top > 0 else regressed
        lines += ["## Modules with reduced coverage (%d)" % len(regressed), "",
                  "| Module | Baseline | This build | Delta | Missed |",
                  "| --- | --- | --- | --- | --- |"]
        for r in top_reg:
            lines.append("| %s | %s%% | %s%% | %s pp | %d |"
                         % (r["Module"], r["BasePct"], r["CurrentPct"], r["Delta"], r["Missed"]))
        lines.append("")
    if new_mods:
        lines += ["## New modules (no baseline, informational) (%d)" % len(new_mods), "",
                  "| Module | Coverage | Missed |", "| --- | --- | --- |"]
        for r in new_mods[:args.top] if args.top > 0 else new_mods:
            lines.append("| %s | %s%% | %d |" % (r["Module"], r["Percentage"], r["Missed"]))
        lines.append("")
    if not regressed and not new_mods:
        lines += ["_No module coverage regressions detected._", ""]

    markdown = "\n".join(lines) + "\n"
    if args.out_md:
        open(args.out_md, "w", encoding="utf-8").write(markdown)
    if args.out_json:
        json.dump({"metric": metric, "gated": args.fail_on_drop, "failed": failed,
                   "basePercentage": base_pct, "currentPercentage": cur_pct,
                   "deltaPp": delta, "tolerancePp": args.tolerance,
                   "regressed": regressed, "newModules": new_mods},
                  open(args.out_json, "w", encoding="utf-8"), indent=2)
    print(markdown)
    return 1 if failed else 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="Parse coverage XML into normalized NDJSON rows.")
    p.add_argument("--input", nargs="+", required=True,
                   help="Coverage XML file(s) or glob(s).")
    p.add_argument("--format", choices=["auto", "jacoco", "cobertura", "lcov"], default="auto")
    p.add_argument("--repo", required=True)
    p.add_argument("--module", required=True)
    p.add_argument("--branch", default="")
    p.add_argument("--commit", default="")
    p.add_argument("--build-id", default="", dest="build_id")
    p.add_argument("--date", default="", help="UTC date (YYYY-MM-DD); defaults to today.")
    p.add_argument("--out", required=True, help="NDJSON file to append rows to.")
    p.set_defaults(func=cmd_parse)

    r = sub.add_parser("report", help="Aggregate NDJSON rows into Markdown + JSON.")
    r.add_argument("--input", nargs="+", required=True,
                   help="NDJSON file(s) or glob(s) produced by 'parse'.")
    r.add_argument("--metric", default="LINE")
    r.add_argument("--goal", type=float, default=75.0)
    r.add_argument("--out-md", default="", dest="out_md")
    r.add_argument("--out-json", default="", dest="out_json")
    r.set_defaults(func=cmd_report)

    g = sub.add_parser("gaps", help="Rank the biggest uncovered classes/files (worklist "
                                    "for raising coverage).")
    g.add_argument("--input", nargs="+", required=True,
                   help="Coverage report file(s) or glob(s) - JaCoCo/Cobertura/LCOV.")
    g.add_argument("--format", choices=["auto", "jacoco", "cobertura", "lcov"], default="auto")
    g.add_argument("--metric", default="LINE", help="LINE (default) or BRANCH.")
    g.add_argument("--top", type=int, default=25,
                   help="Show only the top N targets (0 = all).")
    g.add_argument("--min-missed", type=int, default=1, dest="min_missed",
                   help="Ignore classes/files with fewer missed units than this.")
    g.add_argument("--max-pct", type=float, default=100.0, dest="max_pct",
                   help="Ignore units already above this coverage %%.")
    g.add_argument("--sort", choices=["missed", "pct"], default="missed",
                   help="'missed' = biggest absolute gain first; 'pct' = lowest coverage first.")
    g.add_argument("--out-md", default="", dest="out_md")
    g.add_argument("--out-json", default="", dest="out_json")
    g.set_defaults(func=cmd_gaps)

    c = sub.add_parser("compare", help="Diff base vs PR coverage; show regressions and "
                                       "gate (non-zero exit) on a drop.")
    c.add_argument("--base", nargs="+", required=True,
                   help="Baseline coverage report file(s) or glob(s) (target/dev branch).")
    c.add_argument("--pr", nargs="+", required=True,
                   help="PR/branch coverage report file(s) or glob(s).")
    c.add_argument("--format", choices=["auto", "jacoco", "cobertura", "lcov"], default="auto")
    c.add_argument("--metric", default="LINE", help="LINE (default) or BRANCH.")
    c.add_argument("--tolerance", type=float, default=0.0,
                   help="Allowed overall drop in percentage points before failing "
                        "(default 0.0 = any drop fails).")
    c.add_argument("--unit-tolerance", type=float, default=0.0, dest="unit_tolerance",
                   help="Per-class drop (pp) below which a class is listed as regressed.")
    c.add_argument("--min-missed", type=int, default=1, dest="min_missed",
                   help="Ignore new classes with fewer missed units than this.")
    c.add_argument("--top", type=int, default=25,
                   help="Show only the top N rows per table (0 = all).")
    c.add_argument("--no-fail-on-drop", action="store_false", dest="fail_on_drop",
                   help="Report the diff but never exit non-zero (non-gating).")
    c.add_argument("--out-md", default="", dest="out_md")
    c.add_argument("--out-json", default="", dest="out_json")
    c.set_defaults(func=cmd_compare, fail_on_drop=True)

    ga = sub.add_parser("gate", help="Gate a build on a coverage drop vs the last "
                                     "ingested Kusto baseline for a branch (module-level; "
                                     "no rebuild of the target branch needed).")
    ga.add_argument("--current", required=True,
                    help="NDJSON produced by 'parse' for THIS build.")
    ga.add_argument("--baseline", default="",
                    help="Optional NDJSON baseline (offline/testing). If omitted, the "
                         "baseline is queried from Kusto.")
    ga.add_argument("--metric", default="LINE", help="LINE (default) or BRANCH.")
    ga.add_argument("--repo", default="", help="Repo value to match in Kusto/NDJSON.")
    ga.add_argument("--branch", default="",
                    help="Baseline branch (e.g. the PR target branch) to look up in Kusto.")
    ga.add_argument("--cluster", default="", help="Kusto cluster URL (when no --baseline).")
    ga.add_argument("--db", default="", help="Kusto database (when no --baseline).")
    ga.add_argument("--table", default="CodeCoverageData")
    ga.add_argument("--token-env", default="KUSTO_TOKEN", dest="token_env",
                    help="Env var holding the Kusto bearer token.")
    ga.add_argument("--tolerance", type=float, default=0.0,
                    help="Allowed overall drop in pp before failing (default 0 = any drop fails).")
    ga.add_argument("--unit-tolerance", type=float, default=0.0, dest="unit_tolerance",
                    help="Per-module drop (pp) below which a module is listed as regressed.")
    ga.add_argument("--top", type=int, default=25, help="Show only the top N rows (0 = all).")
    ga.add_argument("--no-fail-on-drop", action="store_false", dest="fail_on_drop",
                    help="Report the gate but never exit non-zero (non-gating).")
    ga.add_argument("--out-md", default="", dest="out_md")
    ga.add_argument("--out-json", default="", dest="out_json")
    ga.set_defaults(func=cmd_gate, fail_on_drop=True)

    w = sub.add_parser("wow", help="Query Kusto for WoW coverage; emit HTML fragment.")
    w.add_argument("--cluster", required=True, help="Kusto cluster URL.")
    w.add_argument("--db", required=True, help="Kusto database name/alias.")
    w.add_argument("--table", default="CodeCoverageData")
    w.add_argument("--metric", default="LINE")
    w.add_argument("--out-html", default="", dest="out_html",
                   help="File to write the HTML fragment to.")
    w.add_argument("--token-env", default="KUSTO_TOKEN", dest="token_env",
                   help="Env var holding the Kusto bearer token.")
    w.add_argument("--group-file", default="", dest="group_file",
                   help="JSON mapping {table title: [module,...]} to split into "
                        "multiple tables. Omit for a single table.")
    w.add_argument("--single-title", default="All Modules", dest="single_title",
                   help="Table title when --group-file is not used.")
    w.add_argument("--other-title", default="Other Modules", dest="other_title",
                   help="Title for the catch-all table of ungrouped modules.")
    w.set_defaults(func=cmd_wow)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
