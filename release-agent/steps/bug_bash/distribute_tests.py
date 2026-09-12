"""Step: `distribute_tests` - validate manual bug-bash assignments (Phase 3, bug_bash).

ADO is the source of truth for case assignees (System.AssignedTo) and plan testers.
This step READS those values, checks eligibility, workload balance and owner triage,
and reports only needed corrections. Valid manual changes are preserved, not reset to
an internal allocation. Availability decisions (OOF/OCE) and workflow status are durable;
assignment lists, preview baselines, approval digests and replay ledgers are not saved.

Eligible = roster DL minus always-excluded people, the release owner, the current primary
on-call engineer (OCE), and people the owner explicitly says are OOF for this Bug Bash.
The OCE team comes from readiness.yaml (the same source as the entry gate); the skill
resolves its primary through ICM and supplies the verified UPN. Owner = state.owner_email.
Resolve the OCE before showing availability candidates. OOF comes ONLY from the owner's
answer, saved in step.data.oof (including an explicit empty list for nobody OOF).
Never infer availability from calendars, presence or automatic OOF detection.

The manual set combines the Broker 'Manual Tests (Android Broker)' subtree and the
Authenticator ReleaseBugBash query. Auth cases automated by this release are excluded
using ui_test_status's completed result API, NOT raw Phase-2 projections or the shared
cross-platform 'Automated' tag. Passed automated cases need no manual assignment;
applied automated failures and Blocked-tagged cases are separate owner-triage work,
not part of the balanced manual workload. The owner is excluded from manual work,
not triage. Corrections align selected plan testers too, without changing outcomes/tags.

Depends on clone_plans_broker (release plan), clone_plans_auth (release suite), and
ui_test_status (a valid completed fill result for the current release/RC/targets).
Missing/stale dependencies block; there is no fallback to a master plan or an empty
automated-case set.

build() is the engine entry point: Done only when live ADO is valid, otherwise Blocked.
inspect_distribution() also returns the transient report for the CLI. After reviewing
the exact corrections, use `distribute-tests --apply --review-hash <hash> --release <id>`.
Apply rereads ADO, uses native revision checks on changed cases, and validates read-back.
Partial failures require inspecting the remaining live corrections, not replay/rollback.

Mock knobs (mocks.local.yaml / tests):
  oce            : verified primary on-call UPN to exclude (skill normally uses ICM).
  roster         : team roster [{name, upn}] (skip Graph).
  broker_cases   : Broker selection [{id, assignee}] (skip ADO case selection).
  auth_cases     : Authenticator selection [{id, assignee, tags}] (skip ADO selection).
  auth_automated : automated auth case IDs [..] (offline completed-result substitute).
  auth_failed    : applied failed auth IDs for triage, used with auth_automated.
  case_snapshot  : string case ID -> {assignee, identity_id, revision}; mocked current
                   ownership and same-read ADO revision (skip live assignment reads).
  point_sets     : [{prefix: 'B' or 'A', plan_id, suite_id,
                   points: [{id, case_id, tester_id}]}] (skip live plan-tester reads).
                   identity_id/tester_id are ADO identity IDs, not Teams user IDs.
  fail           : force a Blocked result with this detail.

Case-selection mocks alone do NOT mock current ownership or plan testers: supply
case_snapshot and point_sets for fully offline validation. These input knobs do not
stub apply writes. Tests must stub the writers explicitly and still confirm OOF via
build(..., oof=[]), inspect_distribution(..., oof=[...]) or the CLI availability flags.
"""
from __future__ import annotations

import hashlib
import json

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import distribution as D, testplans as T
from steps.bug_bash.ui_results import completed_result

ID = "distribute_tests"
KIND = "agent"

MOCKABLE = {
    "oce": {"kind": "input", "desc": "Verified primary on-call UPN."},
    "roster": {"kind": "input", "desc": "Team roster [{name, upn}] (skip Graph)."},
    "broker_cases": {"kind": "input", "desc": "Broker cases [{id, assignee}] (skip ADO selection)."},
    "auth_cases": {"kind": "input", "desc": "Authenticator cases [{id, assignee, tags}] (skip ADO selection)."},
    "auth_automated": {"kind": "input", "desc": "Automated auth case IDs (offline result substitute)."},
    "auth_failed": {"kind": "input", "desc": "Applied automated failures, with auth_automated."},
    "case_snapshot": {"kind": "input", "desc": "Current ownership by string case ID: {assignee,identity_id,revision}; skip ADO assignment reads."},
    "point_sets": {"kind": "input", "desc": "ADO tester groups [{prefix,plan_id,suite_id,points:[{id,case_id,tester_id}]}]; skip point reads."},
    "fail": {"kind": "input", "desc": "Force a blocked result."},
}


def _broker_plan_id(state):
    """The release's cloned Broker plan ID, or None; never fall back to the master."""
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _fill_input(state):
    """Completed-fill owner API; missing/partial evidence is never an empty automated set."""
    injected = mock_input("auth_automated", MISSING)
    if injected is not MISSING:
        ids = sorted({int(i) for i in injected or []})
        return ids, {"offline_automated_ids": ids}
    result = completed_result(state)
    return result["auth"]["automated_case_ids"], {"id": result["id"], "binding": result["binding"]}


def _auth_automated_ids(state):
    return set(_fill_input(state)[0])


def _roster(cfg):
    roster = mock_input("roster", MISSING)
    if roster is MISSING:
        ok, roster, detail = D.resolve_roster(cfg["roster_group"])
        if not ok:
            raise ValueError(f"Couldn't resolve roster '{cfg['roster_group']}' ({detail}).")
    return D.canonical_roster(roster)


def _oce(data):
    oce = data.get("oce", mock_input("oce", None))
    if (not isinstance(oce, str) or oce.strip().count("@") != 1
            or any(c.isspace() for c in oce.strip()) or not all(oce.strip().split("@"))):
        team_id, _ = D.oncall_team()
        raise ValueError(f"Resolve the current primary on-call engineer (ICM team {team_id}) and pass "
                         "--oce <verified-upn> before showing availability choices or distributing tests.")
    return oce.strip().casefold()


def _auth_cases():
    cases = mock_input("auth_cases", MISSING)
    if cases is MISSING:
        ok, cases, detail = D.auth_bugbash_cases()
        if not ok:
            raise ValueError(f"Couldn't read Authenticator bug-bash cases: {detail}")
    return cases


def _owner_triage(state, cases, cfg):
    failed = (mock_input("auth_failed", []) if mock_input("auth_automated", MISSING) is not MISSING
              else completed_result(state)["auth"]["failed_case_ids"])
    blocked_tags = {t.casefold() for t in cfg["authenticator"]["triage_tags"]}
    triage = {}
    for case in cases:
        reasons = []
        if blocked_tags & {t.casefold() for t in case.get("tags", [])}:
            reasons.append("blocked")
        if int(case["id"]) in failed:
            reasons.append("failed_automation")
        if reasons:
            triage[f"A:{case['id']}"] = {
                "assignee": state.owner_email.strip().casefold(), "reasons": reasons}
    return dict(sorted(triage.items()))


def _point_sets(state, selected, cfg):
    injected = mock_input("point_sets", MISSING)
    if injected is not MISSING:
        return injected
    bp = _broker_plan_id(state)
    ok, root, detail = D.find_suite_id_by_name(bp, cfg["broker"]["suite_name"])
    if not ok or not root:
        raise ValueError(detail or "Broker manual suite not found")
    auth_suite = (state.get_step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")
    if not auth_suite:
        raise ValueError("Authenticator release suite missing; refresh the owning clone step")
    groups = []
    for prefix, pid, sid in (("B", bp, root), ("A", T.AUTH_PLAN, auth_suite)):
        ids = [key[2:] for key in selected if key.startswith(prefix + ":")]
        ok, rows, detail = D.read_point_testers(pid, sid, ids)
        if not ok:
            raise ValueError(detail)
        groups.extend({**row, "prefix": prefix} for row in rows)
    return groups


def inspect_distribution(state, *, oof=None, oce=None):
    """Return an outcome and a transient report; persist only availability/workflow data."""
    record = state.get_step("bug_bash", ID)
    was_done, completed_at = record.status == "done", record.completed_at
    record.data = dict(record.data or {})
    record.data.pop("plan", None)              # Retire legacy saved allocations; never consume them.
    record.data.pop("oof_candidates", None)
    if oof is not None:
        record.data.pop("oof", None)
    if oce is not None:
        record.data["oce"] = oce.strip()
    record.status, record.completed_at = "blocked", None
    record.note = "Live ADO distribution validation pending; no assignments changed."
    state.set_step("bug_bash", ID, record)
    report = {}
    try:
        _inspect(state, report, oof)
        note = (f"Live ADO: {report['broker_total']} Broker + {report['auth_total']} Auth manual cases, "
                f"{len(report['eligible'])} eligible testers; {len(report['owner_triage'])} owner-triage cases.")
        outcome = (Done(note + " Assignments and plan testers are valid.") if report["valid"] else
                   Blocked(note + f" {len(report['case_changes'])} case and {len(report['point_changes'])} "
                           "point-tester corrections need review. Run distribute-tests --json; "
                           "apply only the reviewed corrections with --apply --review-hash <hash>."))
    except ValueError as exc:
        outcome = Blocked(f"distribute_tests: {exc}")
        report["error"] = outcome.reason
    record = state.get_step("bug_bash", ID)
    record.note = outcome.reason if isinstance(outcome, Blocked) else outcome.note
    record.status = "blocked" if isinstance(outcome, Blocked) else ("done" if was_done else "pending")
    record.completed_at = completed_at if record.status == "done" else None
    state.set_step("bug_bash", ID, record)
    return outcome, report


def _inspect(state, report, oof):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        raise ValueError(str(fail))
    cfg = D.load_config()
    owner = state.owner_email
    if not owner:
        raise ValueError("release owner missing; resolve the owner before showing availability choices.")
    record = state.get_step("bug_bash", ID)
    oce = _oce(record.data)
    roster = _roster(cfg)
    candidates = set(D.eligible_testers(
        [m["upn"] for m in roster], cfg.get("always_excluded", []), owner=owner, oce=oce))
    report["candidates"] = [m for m in roster if m["upn"] in candidates]
    if not candidates:
        raise ValueError("no eligible testers after owner, on-call and configured exclusions.")
    if oof is not None:
        record.data["oof"] = D.confirm_oof(oof, roster, owner, state.release_id)
        state.set_step("bug_bash", ID, record)
    confirmation = record.data.get("oof")
    excluded = D.validate_oof(confirmation, roster, owner, state.release_id)
    eligible = D.eligible_testers(
        [m["upn"] for m in roster], cfg.get("always_excluded", []), owner=owner, oce=oce, oof=excluded)
    if not eligible:
        raise ValueError("no eligible testers after owner-confirmed OOF exclusions.")
    automated, fill = _fill_input(state)
    bcases = mock_input("broker_cases", MISSING)
    if bcases is MISSING:
        bp = _broker_plan_id(state)
        if not bp:
            raise ValueError("the Broker test plan hasn't been cloned yet (clone_plans_broker).")
        ok, sid, detail = D.find_suite_id_by_name(bp, cfg["broker"]["suite_name"])
        if not ok or not sid:
            raise ValueError(detail or "Broker manual suite not found")
        ok, bcases, detail = D.broker_manual_cases(bp, sid)
        if not ok:
            raise ValueError(f"couldn't read Broker manual tests: {detail}")
    acases = _auth_cases()
    triage = _owner_triage(state, acases, cfg)
    manual_auth = [c for c in acases if int(c["id"]) not in automated and f"A:{c['id']}" not in triage]
    keys = [f"B:{c['id']}" for c in bcases] + [f"A:{c['id']}" for c in manual_auth]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate cases in ADO selection; resolve before distributing.")
    selected = sorted(set(keys) | set(triage))
    if len({key[2:] for key in selected}) != len(selected):
        raise ValueError("A shared case occurs in both distribution sets; resolve overlap before assigning.")
    snapshot = mock_input("case_snapshot", MISSING)
    if snapshot is MISSING:
        ok, snapshot, detail = D.case_assignment_snapshot(key[2:] for key in selected)
        if not ok:
            raise ValueError(f"Cannot read current ADO assignments: {detail}")
    if not {key[2:] for key in selected} <= set(snapshot):
        raise ValueError("Incomplete live case assignments.")
    current = {key: snapshot[key[2:]] for key in selected}
    for key, case in current.items():
        if case.get("assignee") and not case.get("identity_id"):
            raise ValueError(f"Missing ADO assignee identity for {key}; cannot validate plan testers.")
    result = D.distribute([{"id": key, "assignee": current[key]["assignee"]} for key in keys], eligible)
    targets = {**result["assignments"], **{key: row["assignee"] for key, row in triage.items()}}
    case_changes = [{"case": key, "from": current[key]["assignee"], "to": target,
                     "reason": ", ".join(triage[key]["reasons"]) if key in triage else "eligibility/workload balance"}
                    for key, target in targets.items()
                    if D._identity(current[key]["assignee"]) != D._identity(target)]
    groups = _point_sets(state, selected, cfg)
    covered, point_changes = set(), []
    for group in groups:
        for point in group["points"]:
            key = f"{group['prefix']}:{point['case_id']}"
            if key not in current:
                raise ValueError("Unexpected case in live point-tester data.")
            covered.add(key)
            if (point["tester_id"] != current[key]["identity_id"] or
                    D._identity(current[key]["assignee"]) != D._identity(targets[key])):
                point_changes.append({"case": key, "plan_id": group["plan_id"], "suite_id": group["suite_id"],
                                      "point_id": point["id"], "from": point["tester_id"], "to": targets[key]})
    if covered != set(selected):
        raise ValueError("Selected cases are missing from the release plans; refresh the owning clone step.")
    report.update(
        eligible=eligible, owner_excluded=owner, oce_excluded=oce,
        oof_excluded=[m for m in roster if m["upn"] in excluded],
        broker_total=len(bcases), auth_total=len(manual_auth),
        auth_excluded_automated=sum(int(c["id"]) in automated for c in acases),
        owner_triage=triage,
        current_counts={upn: sum(D._identity(current[key]["assignee"]) == upn for key in keys) for upn in eligible},
        proposed_counts=result["counts"], case_changes=case_changes, point_changes=point_changes,
        valid=not case_changes and not point_changes,
        _current=current, _targets=targets, _point_sets=groups)
    # Only a digest crosses the approval boundary; no proposal or digest is stored in run-state.
    review = {"release": state.release_id,
              "inputs": D.review_inputs(roster, cfg.get("always_excluded"), owner, oce, confirmation),
              "fill": fill, "current": {key: {"assignee": D._identity(row["assignee"]),
                                             "identity_id": row["identity_id"]}
                                        for key, row in current.items()},
              "targets": targets, "points": groups}
    report["review_hash"] = hashlib.sha256(json.dumps(review, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build(state, *, oof=None, oce=None):
    return inspect_distribution(state, oof=oof, oce=oce)[0]


run = legacy_run(build)
