"""Step: `distribute_tests` - validate manual bug-bash assignments (Phase 3, bug_bash).

ADO release test-point Tester is the ownership source of truth, never case AssignedTo.
This step READS those values, checks eligibility, workload balance and owner triage,
and reports only needed corrections. Valid manual changes are preserved, not reset to
an internal allocation. Availability decisions (OOF/OCE) and workflow status are durable;
assignment lists, preview baselines, approval digests and replay ledgers are not saved.

Eligible = roster DL minus always-excluded people, the release owner, the current primary
on-call engineer (OCE), and people the owner explicitly says are OOF for this Bug Bash.
The OCE team comes from readiness.yaml (the same source as the entry gate); the skill
resolves its primary through ICM and supplies the verified UPN. Owner = state.owner_email.
Resolve the OCE before showing availability candidates. OOF comes ONLY from the owner's
answer; repeat the reviewed selection flags on execute, or reuse existing step.data.oof.
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

build() is the engine entry point: Done only when live ADO is valid, otherwise Blocked
or a read-only NeedsSkill request for missing connector evidence.
inspect_distribution() also returns the transient report for the CLI. After reviewing
the exact corrections, use `distribute-tests --apply --review-hash <hash> --approved-by <reviewer>
--release <id>` with the same availability flags. CLI previews never save evidence.
Apply rereads ADO, checks reviewed point Testers, and validates read-back.
Partial failures require inspecting the remaining live corrections, not replay/rollback.
The exact roster DL must be read through the installed WorkIQ structured entity connector.
Pass --roster-file (step-action: --param roster_file=<path>) containing {observations: [...]}
with a fresh release-owner-bound envelope for the exact mail lookup and every actual member
page. Their sources and typed identities are reviewed and revalidated before tester writes.

Mock knobs (mocks.local.yaml / tests):
  oce            : verified primary on-call UPN to exclude (skill normally uses ICM).
  roster         : team roster [{name, upn}] (offline connector substitute).
  broker_cases   : Broker selection [{id}] (skip ADO case selection).
  auth_cases     : Authenticator selection [{id, tags}] (skip ADO selection).
  auth_automated : automated auth case IDs [..] (offline completed-result substitute).
  auth_failed    : applied failed auth IDs for triage, used with auth_automated.
  point_sets     : [{prefix: 'B' or 'A', plan_id, suite_id,
                   points: [{id, case_id, config_id, tester_id, tester_upn}]}]
                   (skip live plan-tester reads). tester_id is an ADO identity ID.
  fail           : force a Blocked result with this detail.

Case-selection mocks alone do NOT mock current ownership or plan testers: supply
point_sets for fully offline validation. These input knobs do not
stub apply writes. Tests must stub the writers explicitly and still confirm OOF via
build(..., oof=[]), inspect_distribution(..., oof=[...]) or the CLI availability flags.
"""
from __future__ import annotations
from dataclasses import dataclass

from orchestrator.step_context import StepContext, thaw

from orchestrator.outcomes import Done, Blocked, NeedsSkill
from orchestrator.evidence import StepData
from steps.lib.mockctx import MISSING
from tools import distribution as D, testplans as T
from steps.bug_bash.ui_results import completed_result

from orchestrator.authority import OwnStepData

EVIDENCE = (OwnStepData(),)
ID = "distribute_tests"
KIND = "scout"
WRITE_COMMAND = "distribute-tests"

MOCKABLE = {
    "oce": {"kind": "input", "desc": "Verified primary on-call UPN."},
    "roster": {"kind": "input", "desc": "Team roster [{name, upn}] (offline connector substitute)."},
    "broker_cases": {"kind": "input", "desc": "Broker cases [{id}] (skip ADO selection)."},
    "auth_cases": {"kind": "input", "desc": "Authenticator cases [{id, tags}] (skip ADO selection)."},
    "auth_automated": {"kind": "input", "desc": "Automated auth case IDs (offline result substitute)."},
    "auth_failed": {"kind": "input", "desc": "Applied automated failures, with auth_automated."},
    "point_sets": {"kind": "input", "desc": "ADO groups [{prefix,plan_id,suite_id,points:[{id,case_id,config_id,tester_id,tester_upn}]}]; skip point reads."},
    "fail": {"kind": "input", "desc": "Force a blocked result."},
}


def _broker_plan_id(context):
    """The release's cloned Broker plan ID, or None; never fall back to the master."""
    return (context.evidence.step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _fill_input(context):
    """Completed-fill owner API; missing/partial evidence is never an empty automated set."""
    injected = context.input("auth_automated", MISSING)
    if injected is not MISSING:
        ids = sorted({int(i) for i in injected or []})
        return ids, {"offline_automated_ids": ids}
    result = completed_result(context)
    return result["auth"]["automated_case_ids"], {"id": result["id"], "binding": result["binding"]}


def _auth_automated_ids(context):
    return set(_fill_input(context)[0])


def _roster(context, cfg, roster_file=None):
    roster = context.input("roster", MISSING) if not roster_file else MISSING
    observation = None
    if roster is MISSING:
        if roster_file:
            try:
                observation = context.services.assets.json_file(roster_file)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Cannot read roster observation: {exc}") from None
        ok, roster, detail = context.services.identities.resolve_roster(
            cfg["roster_group"], owner=context.release.owner_email, roster_observation=observation)
        if not ok:
            raise ValueError(f"Couldn't resolve roster '{cfg['roster_group']}' ({detail}).")
        source = {"group_mail": cfg["roster_group"], "observations": observation["observations"],
                  "members": roster}
    else:
        source = {"source": "offline-input", "members": roster}
    return D.canonical_roster(roster), source


def _oce(context, data):
    oce = data.get("oce", context.input("oce", None))
    if (not isinstance(oce, str) or oce.strip().count("@") != 1
            or any(c.isspace() for c in oce.strip()) or not all(oce.strip().split("@"))):
        team_id, _ = context.services.assets.oncall_team()
        raise ValueError(f"Resolve the current primary on-call engineer (ICM team {team_id}) and pass "
                         "--oce <verified-upn> before showing availability choices or distributing tests.")
    return oce.strip().casefold()


def _auth_cases(context):
    cases = context.input("auth_cases", MISSING)
    if cases is MISSING:
        result = completed_result(context)
        target = result["auth"]["target"]
        ok, cases, detail = context.services.testplans.auth_bugbash_cases(
            **({"target": target} if target.get("layout") else {}))
        if not ok:
            raise ValueError(f"Couldn't read Authenticator bug-bash cases: {detail}")
    return cases


def _owner_triage(context, cases, cfg):
    result = None if context.input("auth_automated", MISSING) is not MISSING else completed_result(context)
    failed = context.input("auth_failed", []) if result is None else result["auth"]["failed_case_ids"]
    blocked_tags = {t.casefold() for t in cfg["authenticator"]["triage_tags"]}
    triage = {}
    for case in cases:
        reasons = []
        if (blocked_tags & {t.casefold() for t in case.get("tags", [])}
                and (result is None or not result["auth"]["target"].get("layout")
                     or int(case["id"]) not in result["auth"]["automated_case_ids"])):
            reasons.append("blocked")
        if int(case["id"]) in failed:
            reasons.append("failed_automation")
        if reasons:
            triage[f"A:{case['id']}"] = {
                "assignee": context.release.owner_email.strip().casefold(), "reasons": reasons}
    if result is not None:
        if "monthly" in result:
            for group in _monthly_groups(context, result):
                for point in group["points"]:
                    if point.get("outcome", "").casefold() not in ("passed", "notapplicable"):
                        triage[f"A:{point['case_id']}"] = {
                            "assignee": context.release.owner_email.strip().casefold(),
                            "reasons": ["monthly_investigation"]}
        for cid in result["broker"]["failed_case_ids"]:
            triage[f"B:{cid}"] = {
                "assignee": context.release.owner_email.strip().casefold(), "reasons": ["failed_automation"]}
    return dict(sorted(triage.items()))


def _monthly_groups(context, completed):
    record = completed["monthly"]["target"]
    if not record["bindings"]:
        return []
    sid = record["suite_id"]
    ok, groups, detail = context.services.testplans.read_point_testers(
        record["plan_id"], sid, sorted({b["case_id"] for b in record["bindings"]}), include_outcomes=True)
    if not ok:
        raise ValueError("Monthly live Testers unavailable: " + detail)
    expected = {str(b["point_id"]): b for b in record["bindings"]}
    seen = set()
    for group in groups:
        if group["suite_id"] != sid or group["plan_id"] != record["plan_id"]:
            raise ValueError("Monthly live point destination changed")
        for p in group["points"]:
            b = expected.get(str(p["id"]))
            if (not b or str(p["id"]) in seen or str(p["case_id"]) != str(b["case_id"])
                    or str(p["config_id"]) != str(b["config_id"])):
                raise ValueError("Monthly live point/case/configuration changed")
            seen.add(str(p["id"]))
    if seen != set(expected):
        raise ValueError("Monthly owned points missing from live target")
    return groups


def _point_sets(context, selected, cfg, broker_manual_ids, triage):
    injected = context.input("point_sets", MISSING)
    if injected is not MISSING:
        return injected
    completed = completed_result(context)
    bp = _broker_plan_id(context)
    ok, root, detail = context.services.testplans.find_suite_id_by_name(bp, cfg["broker"]["suite_name"])
    if not ok or not root:
        raise ValueError(detail or "Broker manual suite not found")
    auth_suite = (context.evidence.step("bug_bash", "clone_plans_auth").data or {}).get("suite_id")
    if not auth_suite:
        raise ValueError("Authenticator release suite missing; refresh the owning clone step")
    groups = []
    auth_target = completed["auth"]["target"]
    destinations = [("B", bp, root)]
    if auth_target.get("layout"):
        destinations.extend([("A", T.AUTH_PLAN, auth_target["manual_suite_id"]),
                             ("A", T.AUTH_PLAN, auth_target["suite_id"])])
    else:
        destinations.append(("A", T.AUTH_PLAN, auth_suite))
    for prefix, pid, sid in destinations:
        ids = [key[2:] for key in selected if key.startswith(prefix + ":")
               and (prefix != "B" or key[2:] in broker_manual_ids)]
        if prefix == "A" and auth_target.get("layout"):
            ids = [cid for cid in ids if (int(cid) in completed["auth"]["automated_case_ids"])
                  == (sid == auth_target["suite_id"])
                  and int(cid) not in {b["case_id"] for b in auth_target.get("monthly", {}).get("bindings", [])}]
        ok, rows, detail = context.services.testplans.read_point_testers(pid, sid, ids)
        if not ok:
            raise ValueError(detail)
        if prefix == "A":
            if auth_target.get("layout") and any(
                    str(row["suite_id"]) != str(sid) or str(row["plan_id"]) != str(T.AUTH_PLAN)
                    for row in rows):
                raise ValueError("Authenticator categories gained an unexpected nested point target")
            result = completed["auth"]
            expected_auth = {str(p["point_id"]): p for p in result["applied_points"]
                             if p["outcome"] == "Failed"}
            failure_only = {key[2:] for key, value in triage.items()
                            if key.startswith("A:") and value["reasons"] == ["failed_automation"]}
            found = set()
            filtered = []
            for group in rows:
                points = []
                for point in group["points"]:
                    pid = str(point["id"])
                    if point["case_id"] in failure_only and pid not in expected_auth:
                        continue
                    if pid in expected_auth:
                        if (pid in found or str(point["case_id"]) != str(expected_auth[pid]["case_id"])
                                or auth_target.get("layout")
                                and str(point["config_id"]) != str(expected_auth[pid]["config_id"])):
                            raise ValueError("Applied Auth failure point case/configuration identity changed")
                        found.add(pid)
                    points.append(point)
                if points:
                    filtered.append({**group, "points": points})
            if (not auth_target.get("layout") or sid == auth_target["suite_id"]) and found != set(expected_auth):
                raise ValueError("Applied Auth failure points missing from the release suite")
            rows = filtered
        groups.extend({**row, "prefix": prefix} for row in rows)
    result = completed["broker"]
    expected = {str(p["point_id"]): p for p in result["applied_points"] if p["outcome"] == "Failed"}
    by_suite = {}
    for point in expected.values():
        sid = point["suite_id"] if result.get("mapping_version") in (3, 4) else result["target"]["suite_id"]
        by_suite.setdefault(sid, []).append(point)
    found = set()
    for sid, failures in sorted(by_suite.items()):
        ok, rows, detail = context.services.testplans.read_point_testers(
            bp, sid, sorted({p["case_id"] for p in failures}))
        if not ok:
            raise ValueError(detail)
        for group in rows:
            if str(group["plan_id"]) != str(bp) or str(group["suite_id"]) != str(sid):
                raise ValueError("Applied Broker failure point plan/suite identity changed")
            points = []
            for point in group["points"]:
                pid = str(point["id"])
                if pid not in expected:
                    continue
                if (pid in found or str(point["case_id"]) != str(expected[pid]["case_id"])
                        or str(point["config_id"]) != str(expected[pid]["config_id"])
                        or (result.get("mapping_version") in (3, 4)
                            and str(expected[pid]["suite_id"]) != str(sid))):
                    raise ValueError("Applied Broker failure point case/configuration changed")
                found.add(pid)
                points.append(point)
            if points:
                groups.append({**group, "prefix": "B", "points": points})
    if found != set(expected):
        raise ValueError("Applied Broker failure points missing from the release suites")
    if "monthly" in completed:
        for group in _monthly_groups(context, completed):
            points = [p for p in group["points"] if f"A:{p['case_id']}" in selected
                      and p.get("outcome", "").casefold() not in ("passed", "notapplicable")]
            if points:
                groups.append({**group, "prefix": "A", "points": points})
    return groups


def inspect_distribution(context, *, oof=None, oce=None, roster_file=None):
    """Return an outcome and a transient report; persist only availability/workflow data."""
    data = thaw(context.evidence.step("bug_bash", ID).data or {})
    data.pop("plan", None)
    data.pop("oof_candidates", None)
    if oof is not None:
        data.pop("oof", None)
    if oce is not None:
        data["oce"] = oce.strip()
    report = {}
    try:
        _inspect(context, report, oof, data, roster_file)
        note = (f"Live ADO: {report['broker_total']} Broker + {report['auth_total']} Auth manual cases, "
                f"{len(report['eligible'])} eligible testers; {len(report['owner_triage'])} owner-triage cases.")
        outcome = (Done(note + " Assignments and plan testers are valid.") if report["valid"] else
                   Blocked(note + f" {len(report['case_changes'])} case and {len(report['point_changes'])} "
                           "point-tester corrections need review. Run distribute-tests --json; "
                           "apply only the reviewed corrections with --apply --review-hash <hash> "
                           "--approved-by <reviewer>, repeating the preview's availability flags."))
    except D.ConnectorObservationRequired as exc:
        report.update(error=str(exc), connector_reads=exc.requests,
                      followup="Supply --roster-file <path> (step-action: --param roster_file=<path>) "
                               "and repeat the availability flags; do not record completion.")
        outcome = NeedsSkill(
            tool=exc.tool,
            payload={**exc.arguments, "_connector_reads": exc.requests,
                     "_followup": report["followup"]},
            summary="Gather the exact complete roster through the installed M365 connector",
            note=str(exc))
    except ValueError as exc:
        outcome = Blocked(f"distribute_tests: {exc}")
        report["error"] = outcome.reason
    outcome.updates = (StepData(data),)
    return outcome, report


def _inspect(context, report, oof, data, roster_file=None):
    fail = context.input("fail", MISSING)
    if fail is not MISSING:
        raise ValueError(str(fail))
    cfg = context.services.assets.distribution_config()
    owner = context.release.owner_email
    if not owner:
        raise ValueError("release owner missing; resolve the owner before showing availability choices.")
    oce = _oce(context, data)
    roster, roster_source = _roster(context, cfg, roster_file)
    candidates = set(D.eligible_testers(
        [m["upn"] for m in roster], cfg.get("always_excluded", []), owner=owner, oce=oce))
    report["candidates"] = [m for m in roster if m["upn"] in candidates]
    if not candidates:
        raise ValueError("no eligible testers after owner, on-call and configured exclusions.")
    if oof is not None:
        data["oof"] = D.confirm_oof(oof, roster, owner, context.release.release_id,
                                  now=context.clock.utc(), ccd=context.release.ccd)
    confirmation = data.get("oof")
    excluded = D.validate_oof(confirmation, roster, owner, context.release.release_id,
                              ccd=context.release.ccd)
    eligible = D.eligible_testers(
        [m["upn"] for m in roster], cfg.get("always_excluded", []), owner=owner, oce=oce, oof=excluded)
    if not eligible:
        raise ValueError("no eligible testers after owner-confirmed OOF exclusions.")
    automated, fill = _fill_input(context)
    destination_names = D.release_target_names(
        context.evidence.step("bug_bash", "clone_plans_broker").data,
        context.evidence.step("bug_bash", "clone_plans_auth").data)
    if context.input("auth_automated", MISSING) is MISSING:
        context.services.testplans.validate_release_targets(
            context.release.release_id, context.release.ccd, fill["binding"],
            expected_names=destination_names)
    bcases = context.input("broker_cases", MISSING)
    if bcases is MISSING:
        bp = _broker_plan_id(context)
        if not bp:
            raise ValueError("the Broker test plan hasn't been cloned yet (clone_plans_broker).")
        ok, sid, detail = context.services.testplans.find_suite_id_by_name(bp, cfg["broker"]["suite_name"])
        if not ok or not sid:
            raise ValueError(detail or "Broker manual suite not found")
        ok, bcases, detail = context.services.testplans.broker_manual_cases(bp, sid)
        if not ok:
            raise ValueError(f"couldn't read Broker manual tests: {detail}")
    acases = _auth_cases(context)
    triage = _owner_triage(context, acases, cfg)
    monthly_ids = {b["case_id"] for b in fill.get("monthly", {}).get("target", {}).get("bindings", [])}
    manual_auth = [c for c in acases if int(c["id"]) not in set(automated) | monthly_ids
                   and f"A:{c['id']}" not in triage]
    manual_broker = [c for c in bcases if f"B:{c['id']}" not in triage]
    keys = sorted([f"B:{c['id']}" for c in manual_broker] + [f"A:{c['id']}" for c in manual_auth])
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate cases in ADO selection; resolve before distributing.")
    selected = sorted(set(keys) | set(triage))
    groups = sorted(
        [{**g, "points": sorted(g["points"], key=lambda p: str(p["id"]))}
         for g in _point_sets(context, selected, cfg, {str(c["id"]) for c in bcases}, triage)],
        key=lambda g: (g["prefix"], str(g["plan_id"]), str(g["suite_id"])))
    observed, seen, identities = {key: [] for key in selected}, set(), {}
    for group in groups:
        if group.get("prefix") not in ("B", "A"):
            raise ValueError("Unexpected product in release point data.")
        for point in group["points"]:
            key = f"{group['prefix']}:{point['case_id']}"
            if key not in observed:
                raise ValueError("Unexpected case in live point-tester data.")
            point_key = (str(group["plan_id"]), str(group["suite_id"]), str(point["id"]))
            if point_key in seen:
                raise ValueError("Duplicate release test point.")
            seen.add(point_key)
            upn = D._identity(point.get("tester_upn")) or None
            if bool(upn) != bool(point.get("tester_id")):
                raise ValueError(f"Unresolved release-point Tester for {key}.")
            if upn:
                if upn in identities and identities[upn] != point["tester_id"]:
                    raise ValueError(f"Conflicting ADO identities for {upn}")
                identities[upn] = point["tester_id"]
            observed[key].append((upn, point.get("tester_id")))
    if any(not rows for rows in observed.values()):
        raise ValueError("Selected cases are missing from the release plans; refresh the owning clone step.")
    current = {}
    for key, rows in observed.items():
        unique = set(rows)
        upn, identity = next(iter(unique)) if len(unique) == 1 else (None, None)
        current[key] = {"assignee": upn, "identity_id": identity}
    result = D.distribute([{"id": key, "assignee": current[key]["assignee"]} for key in keys], eligible)
    targets = {**result["assignments"], **{key: row["assignee"] for key, row in triage.items()}}
    case_changes = [{"case": key, "from": current[key]["assignee"], "to": target,
                     "reason": ", ".join(triage[key]["reasons"]) if key in triage else "eligibility/workload balance"}
                    for key, target in targets.items()
                    if D._identity(current[key]["assignee"]) != D._identity(target)]
    point_changes = []
    for group in groups:
        for point in group["points"]:
            key = f"{group['prefix']}:{point['case_id']}"
            if D._identity(point.get("tester_upn")) != D._identity(targets[key]):
                point_changes.append({"case": key, "plan_id": group["plan_id"], "suite_id": group["suite_id"],
                                      "point_id": point["id"], "from": point["tester_id"], "to": targets[key]})
    report.update(
        eligible=eligible, owner_excluded=owner, oce_excluded=oce,
        oof_excluded=[m for m in roster if m["upn"] in excluded],
        broker_total=len(manual_broker), auth_total=len(manual_auth),
        auth_excluded_automated=sum(int(c["id"]) in automated for c in acases),
        owner_triage=triage,
        current_counts={upn: sum(D._identity(current[key]["assignee"]) == upn for key in keys) for upn in eligible},
        proposed_counts=result["counts"], case_changes=case_changes, point_changes=point_changes,
        valid=not case_changes and not point_changes,
        _current=current, _targets=targets, _point_sets=groups)
    # Semantic availability, not the observation clock, belongs in the write review.
    inputs = D.review_inputs(roster, cfg.get("always_excluded"), owner, oce, confirmation)
    inputs["roster_source"] = roster_source
    inputs["oof"].pop("confirmed_at", None)
    report["_review"] = {
        "destination_names": destination_names,
        "inputs": inputs, "configuration": cfg, "ccd": context.release.ccd,
        "provider": {"org": D.ORG, "project": D.PROJECT},
        "broker_plan": _broker_plan_id(context), "auth_plan": T.AUTH_PLAN,
        "auth_suite": context.evidence.step("bug_bash", "clone_plans_auth").data.get("suite_id"),
        "fill": fill, "current": {
            key: {"assignee": D._identity(row["assignee"]), "identity_id": row["identity_id"]}
            for key, row in current.items()},
        "targets": targets, "points": groups,
    }


@dataclass(frozen=True)
class BuildParameters:
    oof: tuple[str, ...] | None = None
    oce: str | None = None
    roster_file: str | None = None


PARAMETERS = {"build": BuildParameters}


def build(context: StepContext[BuildParameters]):
    oof = list(context.parameters.oof) if context.parameters.oof is not None else None
    oce = context.parameters.oce
    return inspect_distribution(context, oof=oof, oce=oce, roster_file=context.parameters.roster_file)[0]
