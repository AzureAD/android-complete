"""Step: `ui_test_status` — fill BOTH the Broker + Authenticator UI suites from the RC
pipelines, and reassign failed auth automation to the release owner (Phase 3, bug_bash;
runs right before `distribute_tests`).

BROKER: Phase-2 records one or more RC iterations (`state.pipeline_runs.rcs[]`), each with an
ECS and a Local MRWP build. This step reads the UI-automation results across ALL those RC
builds and fills the flat "UI Automation (Android Broker)" suite (from `clone_plans_broker`).
Each (case, flight-config) point takes its matching run's outcome (PASSED if it passed in >=1
run; FAILED if it ran but never passed; NotApplicable if only skipped) — see
`pipelines.ui_automation_verdicts`.

AUTHENTICATOR: this step ALSO fills the Authenticator bug-bash suite (from `clone_plans_auth`)
from this release's auth ECS post-build UI-test run (`state.rcs[-1].auth.test`, captured by
the Phase-2 `auth_ecs` step). For the automated cases only (join on `test_<caseId>_`), it
writes Passed->Passed / Failed->Failed and LEAVES manual cases untouched; every FAILED
automated case is reassigned to the release owner (`state.owner_email`) for triage. Best-effort
— if the auth suite / run isn't available yet, the auth fill is skipped with a note and the
Broker fill still completes.

Depends on `clone_plans_broker` (and, for the auth fill, `clone_plans_auth` + the Phase-2 RC
runs). Blocks only if the Broker plan hasn't been cloned or no RC runs are recorded.
Idempotent — a re-run re-fills from the current runs.

REASSIGNMENT: every FAILED UI case — Broker (parsed from the failing suite titles) AND
Authenticator (the failed automated cases) — is reassigned to the release owner
(`state.owner_email`) in ADO, so all UI failures land in one queue for investigation. Then the
downstream `ui_failures` human reminder is forward-populated with the full per-test list.

Mock knobs (mocks.local.yaml / tests):
  build_ids     : inject the RC MRWP build ids [..] (skip state.pipeline_runs).
  verdicts      : inject the per-config Broker verdicts (skip ADO pipelines).
  auth_build_id : inject the auth ECS UI-test build id (skip state.rcs[].auth).
  auth_suite_id : inject the Authenticator bug-bash suite id (skip clone_plans_auth).
  auth_outcomes : inject the auth per-case outcomes {case_id: 'Passed'|'Failed'} (skip ADO).
  fail          : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import testplans as T
from tools import pipelines as P
from tools import distribution as D

ID = "ui_test_status"
KIND = "agent"

ORG = T.ORG
PROJECT = T.PROJECT

MOCKABLE = {
    "build_ids": {"kind": "input", "desc": "Inject the RC MRWP build ids [..] (skip state.pipeline_runs)."},
    "verdicts": {"kind": "input", "desc": "Inject per-config verdicts {case_id: {(flight,variant): outcome}} (skip ADO)."},
    "auth_build_id": {"kind": "input", "desc": "Inject the auth ECS UI-test build id (skip state.rcs[].auth)."},
    "auth_suite_id": {"kind": "input", "desc": "Inject the Authenticator bug-bash suite id (skip clone_plans_auth)."},
    "auth_outcomes": {"kind": "input", "desc": "Inject auth per-case outcomes {case_id: 'Passed'|'Failed'} (skip ADO)."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _broker_plan_id(state):
    """The release's cloned Broker plan id (from clone_plans_broker), or None."""
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _auth_suite_id(state):
    """The release's Authenticator bug-bash suite id (from clone_plans_auth), or None."""
    return (state.get_step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")


def _auth_test_build_id(state):
    """The auth ECS post-build UI-test build id captured by the Phase-2 auth_ecs step
    (state.pipeline_runs.rcs[-1].auth.test.run_id), or None."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    if not rcs:
        return None
    return (((rcs[-1].get("auth") or {}).get("test") or {}) or {}).get("run_id")


def _rc_build_ids(state):
    """Every MRWP build id recorded by Phase-2 verification — the ecs + local run of each RC
    iteration in state.pipeline_runs.rcs[] — de-duplicated, order preserved."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    out, seen = [], set()
    for rc in rcs:
        for prov in ("ecs", "local"):
            rid = (rc.get(prov) or {}).get("run_id")
            if rid and rid not in seen:
                seen.add(rid)
                out.append(rid)
    return out


def _fill_auth(state, notes):
    """Fill the Authenticator bug-bash suite from this release's auth ECS UI-test run, and
    reassign the FAILED automated cases to the release owner for triage. Best-effort: appends
    a note and returns None if the auth suite / run data isn't available yet (the Broker fill
    must not be blocked by a missing auth leg). Returns a short summary string on success."""
    suite_id = mock_input("auth_suite_id", MISSING)
    suite_id = suite_id if suite_id is not MISSING else _auth_suite_id(state)
    if not suite_id:
        notes.append("Authenticator suite not created yet (clone_plans_auth) — auth results skipped.")
        return None

    outcomes = mock_input("auth_outcomes", MISSING)
    case_titles = {}                                       # {case_id(int): automation test title}
    if outcomes is MISSING:
        build_id = mock_input("auth_build_id", MISSING)
        build_id = build_id if build_id is not MISSING else _auth_test_build_id(state)
        if not build_id:
            notes.append("No auth ECS UI-test run captured yet (Phase-2 auth_ecs) — auth results skipped.")
            return None
        ok, results, d = P.auth_ui_case_results(build_id)
        if not ok:
            notes.append(f"Could not read auth UI results ({d}) — auth results skipped.")
            return None
        outcomes = {cid: v["outcome"] for cid, v in results.items()}
        case_titles = {cid: v["title"] for cid, v in results.items() if v.get("title")}

    ok, summ, d = T.fill_auth_ui_results(T.AUTH_PLAN, suite_id, outcomes)
    if not ok:
        notes.append(f"Could not fill auth UI results ({d}) — auth results skipped.")
        return None

    # Reassign every FAILED automated case to the release owner for triage.
    failed_ids = summ.get("failed_case_ids") or []
    owner = state.owner_email
    assigned = 0
    if owner:
        for cid in failed_ids:
            oka, _ = D.set_assigned_to(cid, owner)
            if oka:
                assigned += 1
    elif failed_ids:
        notes.append("No release owner on record — failed auth cases not reassigned (set-owner).")

    # Titles for the failed cases (for the ui_failures render), keyed by str(case_id).
    failed_titles = {}
    for cid in failed_ids:
        try:
            t = case_titles.get(int(cid))
        except (TypeError, ValueError):
            t = None
        if t:
            failed_titles[str(cid)] = t

    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["auth"] = {"suite_id": suite_id, "passed": summ.get("set_passed", 0),
                         "failed": summ.get("set_failed", 0), "failed_case_ids": failed_ids,
                         "failed_case_titles": failed_titles,
                         "failed_assigned_to_owner": assigned}
    state.set_step("bug_bash", ID, step)
    owner_note = (f"; {assigned} failed case(s) assigned to owner {owner}"
                  if failed_ids and owner else "")
    return (f"Auth: {summ.get('set_passed', 0)} Passed, {summ.get('set_failed', 0)} Failed "
            f"filled in suite {suite_id}{owner_note}")


def _case_id_from_title(title):
    """The ADO test-case id embedded in a UI-automation test title (`test_<caseId>_...`)."""
    import re
    m = re.search(r"test_(\d+)", str(title or ""), re.I)
    return int(m.group(1)) if m else None


def _broker_failed_case_ids(state):
    """Unique ADO test-case ids of every failing Broker UI test — parsed from the failed_suites
    test titles across all RC iterations (both providers), order preserved. A case that fails in
    several suites/variants (e.g. 831126) appears once."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    out, seen = [], set()
    for rc in rcs:
        for prov in ("ecs", "local"):
            snap = rc.get(prov) or {}
            for s in (snap.get("failed_suites") or []):
                if s.get("category", "ui") != "ui" or not s.get("failed"):
                    continue
                for title in (s.get("tests") or []):
                    cid = _case_id_from_title(title)
                    if cid and cid not in seen:
                        seen.add(cid)
                        out.append(cid)
    return out


def _reassign_broker_failures(state, notes):
    """Reassign every failing Broker UI case to the release owner for investigation — mirrors the
    auth reassignment in _fill_auth so BOTH apps' failures land in the owner's queue. Stores the
    ids + assigned count on the step. Best-effort: a case with no parseable id is skipped, and a
    missing owner is noted rather than fatal."""
    ids = _broker_failed_case_ids(state)
    owner = state.owner_email
    assigned = 0
    if ids and owner:
        for cid in ids:
            oka, _ = D.set_assigned_to(cid, owner)
            if oka:
                assigned += 1
    elif ids and not owner:
        notes.append("No release owner on record — failed Broker UI cases not reassigned (set-owner).")
    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["broker"] = {"failed_case_ids": ids, "failed_assigned_to_owner": assigned}
    state.set_step("bug_bash", ID, step)
    return assigned


def _surface_ui_failures(state):
    """Forward-populate the downstream `ui_failures` HUMAN-review reminder with the combined
    Phase-2 UI failure list — BOTH the Broker MRWP UI suites AND the Authenticator ECS failed
    cases — so the engineer sees everything in one place when the engine holds at that step.

    `ui_failures` is a bare human reminder (no module), so it can't compute anything itself;
    this producer step (which just filled both suites and knows the auth failures) writes the
    reminder's note + links. The note is a rich, step-8-style markdown block: an emoji header, a
    bold summary line, then EVERY failing UI test listed individually (never collapsed to a
    per-suite stat) — Broker MRWP and Authenticator ECS alike — each flagged 🔬 as an
    investigation the RELEASE OWNER owns, with an inline link to its ADO test case. Only sets
    note/links/data — never status — so the step stays a pending human hold. No failures
    anywhere -> leaves the generic prompt."""
    rcs = (getattr(state, "pipeline_runs", None) or {}).get("rcs") or []
    links = []

    def _eng_run_url(rid):
        return f"{ORG}/{PROJECT}/_build/results?buildId={rid}"

    def _case_url(cid):
        return f"{ORG}/{PROJECT}/_workitems/edit/{cid}"

    # Broker MRWP UI failures — every failing test, GROUPED by provider (ECS/Local) then by
    # bucket (suite name, e.g. 'PROD MSAL - RC Broker (API 32)'). Preserves discovery order.
    #   broker = { "ECS": {suite_name: [{title, case_id}, …]}, "Local": {…} }
    from collections import OrderedDict
    broker = OrderedDict()
    broker_count = 0
    for rc in rcs:
        for prov, label in (("ecs", "ECS"), ("local", "Local")):
            snap = rc.get(prov) or {}
            for s in (snap.get("failed_suites") or []):
                if s.get("category", "ui") != "ui" or not s.get("failed"):
                    continue
                buckets = broker.setdefault(label, OrderedDict())
                lst = buckets.setdefault(s["name"], [])
                for title in (s.get("tests") or []):
                    lst.append({"title": title, "case_id": _case_id_from_title(title)})
                    broker_count += 1
            if snap.get("run_id"):
                links.append({"name": f"MRWP {label} run", "url": _eng_run_url(snap["run_id"])})

    # Authenticator ECS failures — the failed automated case ids from the fill.
    astep = (state.get_step("bug_bash", ID).data or {}).get("auth") or {}
    failed_ids = astep.get("failed_case_ids") or []
    auth = (rcs[-1].get("auth") if rcs else None) or {}

    if not broker_count and not failed_ids:
        return                                            # nothing failed — leave the generic reminder

    # ---- build the step-8-style markdown note ----
    try:
        from orchestrator import schedule
        month_year = schedule.target_month_label(state)
    except Exception:
        month_year = ""
    owner = state.owner_email or "the release owner"
    title = f"\U0001f9ea {month_year + ' ' if month_year else ''}Bug Bash \u2014 UI failures to investigate"

    total = broker_count + len(failed_ids)
    summary = (f"**{total} failing UI test(s)** across Broker MRWP + Authenticator ECS \u2014 "
               f"all assigned to {owner} to investigate (flake vs real bug).")
    lines = [title, summary]

    def _mark(cid, text):
        """A 🔬 investigate line — links the case when we could parse its id."""
        if cid:
            return f"- \U0001f52c [{cid}]({_case_url(cid)}) \u2014 {text}"
        return f"- \U0001f52c {text}"

    if broker_count:
        lines.append(f"**Broker (MRWP)** \u2014 {broker_count} failing test(s):")
        # separate by provider (ECS / Local), then by bucket (suite name)
        for label, buckets in broker.items():
            prov_n = sum(len(v) for v in buckets.values())
            lines.append(f"**{label}** \u2014 {prov_n} failing:")
            for suite, tests in buckets.items():
                lines.append(f"_{suite}_ ({len(tests)}):")
                for t in tests:
                    lines.append(_mark(t["case_id"], t["title"]))

    if failed_ids:
        titles = astep.get("failed_case_titles") or {}
        lines.append(f"**Authenticator (ECS)** \u2014 {len(failed_ids)} failing automated test(s):")
        for cid in failed_ids:
            lines.append(_mark(cid, titles.get(str(cid)) or "Automated failure"))
        for key, name in (("build", "Authenticator ECS build"), ("test", "Authenticator ECS UI tests")):
            rid = (auth.get(key) or {}).get("run_id")
            if rid:
                links.append({"name": name,
                              "url": f"https://msazure.visualstudio.com/One/_build/results?buildId={rid}"})

    lines.append("\u25b6 Once you've investigated and re-run all of these, just let me know and "
                 "I'll mark this step complete for you.")

    note = "\n".join(lines)
    ustep = state.get_step("bug_bash", "ui_failures")
    ustep.note = note
    ustep.links = links
    ustep.data = dict(ustep.data or {})
    ustep.data["broker_failed_tests"] = broker_count
    ustep.data["auth_failed_cases"] = failed_ids
    state.set_step("bug_bash", "ui_failures", ustep)


def build(state):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"ui_test_status: {fail}")

    plan_id = _broker_plan_id(state)
    if not plan_id:
        return Blocked("ui_test_status: the Broker test plan hasn't been cloned yet "
                       "(clone_plans_broker) — run that first so the UI Automation suite exists.")

    verdicts = mock_input("verdicts", MISSING)
    if verdicts is MISSING:
        build_ids = mock_input("build_ids", MISSING)
        if build_ids is MISSING:
            build_ids = _rc_build_ids(state)
        if not build_ids:
            return Blocked("ui_test_status: no RC pipeline runs recorded yet (Phase-2 build "
                           "verification) — nothing to fill the UI Automation results from.")
        ok, verdicts, d = P.ui_automation_verdicts(
            P.ENGINEERING_ORG, P.ENGINEERING_PROJECT, build_ids)
        if not ok:
            hint = " — run `az login`" if str(d).startswith("AUTH") else ""
            return Blocked(f"ui_test_status: couldn't read UI results from the RC pipelines ({d}){hint}.")

    ok, summ, d = T.fill_ui_automation_results(plan_id, verdicts)
    if not ok:
        return Blocked(f"ui_test_status: couldn't fill the UI Automation results ({d}).")

    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["plan_id"] = plan_id
    step.data["summary"] = summ
    state.set_step("bug_bash", ID, step)

    # Also fill the Authenticator bug-bash suite from the auth ECS UI-test run (best-effort);
    # every FAILED automated auth case is reassigned to the release owner for triage.
    notes = []
    auth_note = _fill_auth(state, notes)

    # Reassign every FAILED Broker UI case to the release owner too — all UI failures (Broker +
    # Auth) are the owner's to investigate.
    broker_assigned = _reassign_broker_failures(state, notes)

    # Forward-populate the downstream `ui_failures` human-review reminder with the combined
    # Broker + Authenticator Phase-2 UI failure list (it has no module of its own).
    _surface_ui_failures(state)

    p, f, na = summ.get("set_passed", 0), summ.get("set_failed", 0), summ.get("set_not_applicable", 0)
    tail = (f" {auth_note}." if auth_note else "")
    if broker_assigned:
        tail += f" {broker_assigned} failed Broker UI case(s) assigned to owner {state.owner_email}."
    if notes:
        tail += " " + " ".join(notes)
    links = [{"name": f"Broker UI Automation (plan {plan_id})", "url": T.plan_web_url(plan_id)}]
    astep = (state.get_step("bug_bash", ID).data or {}).get("auth") or {}
    if astep.get("suite_id"):
        links.append({"name": f"Authenticator results (suite {astep['suite_id']})",
                      "url": T.plan_web_url(T.AUTH_PLAN, astep["suite_id"])})
    return Done(
        f"Filled UI Automation results in Broker plan {plan_id}: {p + f + na} test points "
        f"({p} Passed, {f} Failed, {na} N/A) across {summ.get('cases_touched', 0)} cases."
        f"{tail}",
        links=links)


run = legacy_run(build)
