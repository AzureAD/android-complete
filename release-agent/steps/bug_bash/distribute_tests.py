"""Step: `distribute_tests` — evenly distribute the manual bug-bash tests across the
eligible team (Phase 3, bug_bash).

READ-ONLY PREVIEW. This step computes the distribution (combined Broker + Authenticator
test sets, even split preserving default assignments) and STORES the resulting plan on the
step (`data.plan`), reporting a summary. It does NOT write `System.AssignedTo` — applying
the plan (which mutates the shared test-case work items) is a separate, explicit action:
`distribute-tests --release <id> --apply`.

Eligible = roster DL minus owner-confirmed OOF people, always-excluded people, the owner,
and the current on-call engineer (OCE). The OCE's team id comes from readiness.yaml (single source with the
entry gate); the OCE identity is resolved via ICM by the skill and passed in as the `oce`
input. Owner = state.owner_email. OOF availability comes ONLY from that owner's explicit
answer for this Bug Bash, saved in step.data.oof (including an explicit empty list).
Neither Graph calendars nor presence nor automatic OOF detection is used.

The Authenticator set EXCLUDES cases recorded as automated by the completed `ui_test_status`
result, NOT from Phase-2 projections or the cross-platform 'Automated' tag (iOS owns that
tag, so a case automated only on Android must not carry it). Both passed and failed automated
cases are dropped from the manual split — failures are triaged by the owner via
`ui_test_status`, not by a manual tester. Blocked-tagged cases and applied automated
failures are explicit owner-triage assignments. Apply aligns selected plan testers too,
without writing outcomes or removing tags.

Depends on the two clone steps: the Broker plan id is read from clone_plans_broker's
stashed data (falls back to the master plan). If the Broker plan hasn't been cloned yet,
the step blocks.

Mock knobs (mocks.local.yaml / tests):
  oce            : the on-call engineer's identifier to exclude (skill resolves via ICM).
  roster         : inject the team roster [{name, upn}] (skip Graph).
  broker_cases   : inject the Broker cases [{id, assignee}] (skip ADO).
  auth_cases     : inject the Authenticator cases [{id, assignee, tags}] (skip ADO).
  auth_automated : inject automated auth case ids to exclude [..] (offline receipt substitute).
  fail           : force a Blocked with this detail.
"""
from __future__ import annotations

from orchestrator.outcomes import Done, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import distribution as D
from steps.bug_bash.ui_results import completed_result

ID = "distribute_tests"
KIND = "agent"

MOCKABLE = {
    "oce": {"kind": "input", "desc": "On-call engineer identifier to exclude (skill resolves via ICM)."},
    "roster": {"kind": "input", "desc": "Inject the team roster [{name, upn}] (skip Graph)."},
    "broker_cases": {"kind": "input", "desc": "Inject Broker cases [{id, assignee}] (skip ADO)."},
    "auth_cases": {"kind": "input", "desc": "Inject Authenticator cases [{id, assignee}] (skip ADO)."},
    "auth_automated": {"kind": "input", "desc": "Inject automated auth case ids [..] (offline receipt substitute)."},
    "auth_failed": {"kind": "input", "desc": "With auth_automated, inject applied failed case ids for owner triage."},
    "fail": {"kind": "input", "desc": "Force a Blocked with this detail."},
}


def _broker_plan_id(state):
    """The release's cloned Broker plan id (from clone_plans_broker), or None."""
    return (state.get_step("bug_bash", "clone_plans_broker").data or {}).get("plan_id")


def _fill_input(state):
    """Stable owner API; an absent/partial receipt is never an empty automated set."""
    injected = mock_input("auth_automated", MISSING)
    if injected is not MISSING:
        ids = sorted({int(i) for i in (injected or [])})
        return ids, {"offline_automated_ids": ids}
    result = completed_result(state)
    return result["auth"]["automated_case_ids"], {"id": result["id"], "binding": result["binding"]}


def _auth_automated_ids(state):
    return set(_fill_input(state)[0])


def invalidate_preview(state, *, clear_oof=False):
    """A failed/revised preview must not leave an earlier plan available to --apply."""
    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data.pop("plan", None)
    if clear_oof:
        step.data.pop("oof", None)
    step.status = "blocked"
    step.completed_at = None
    step.note = "Distribution preview needs refresh; no assignments changed."
    state.set_step("bug_bash", ID, step)


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
            or any(c.isspace() for c in oce.strip())
            or not all(oce.strip().split("@"))):
        team_id, _ = D.oncall_team()
        raise ValueError(
            f"Resolve the current primary on-call engineer (ICM team {team_id}) and pass "
            "--oce <verified-upn> before showing availability choices or distributing tests.")
    return oce.strip().casefold()


def validate_stored_plan(state):
    """Recheck current roster/exclusions, without recomputing or writing assignments."""
    data = state.get_step("bug_bash", ID).data or {}
    cfg = D.load_config()
    # Missing confirmation must fail even if roster access is unavailable.
    if not isinstance(data.get("oof"), dict):
        D.validate_oof(None, [], state.owner_email, state.release_id)
    D.validate_distribution_plan(data.get("plan"), data["oof"], _roster(cfg), cfg,
                                 state.owner_email, _oce(data), state.release_id)
    ids, binding = _fill_input(state)
    if (data["plan"].get("auth_automated_ids") != ids
            or data["plan"].get("ui_fill_result") != binding):
        raise ValueError("UI fill result changed; refresh the distribution preview")
    triage = _owner_triage(state, _auth_cases(), cfg)
    if data["plan"].get("owner_triage") != triage or set(triage) & set(data["plan"]["assignments"]):
        raise ValueError("Owner triage changed or overlaps manual work; refresh the distribution preview")


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
        cid = int(case["id"])
        reasons = []
        if blocked_tags & {t.casefold() for t in case.get("tags", [])}:
            reasons.append("blocked")
        if cid in failed:
            reasons.append("failed_automation")
        if reasons:
            triage[f"A:{cid}"] = {"assignee": state.owner_email.strip().casefold(), "reasons": reasons}
    return dict(sorted(triage.items()))


def sync_plan_testers(state, assignments):
    cfg = D.load_config()
    plan = state.get_step("bug_bash", ID).data["plan"]
    binding = plan["ui_fill_result"]["binding"]
    bp = binding["broker"]["plan_id"]
    ok, sid, detail = D.find_suite_id_by_name(bp, cfg["broker"]["suite_name"])
    if not ok or not sid:
        return False, detail or "Broker manual suite not found"
    for prefix, pid, suite in (("B:", bp, sid),
                               ("A:", binding["auth"]["plan_id"], binding["auth"]["suite_id"])):
        targets = {int(key[2:]): upn for key, upn in assignments.items() if key.startswith(prefix)}
        ok, detail = D.sync_point_testers(pid, suite, targets)
        if not ok:
            return False, detail
    return True, ""


def build(state, *, oof=None, oce=None):
    # None means no new answer, [] means the owner explicitly said nobody is OOF.
    invalidate_preview(state, clear_oof=oof is not None)
    step = state.get_step("bug_bash", ID)
    step.data.pop("oof_candidates", None)
    if oce is not None:
        step.data["oce"] = oce.strip()
    state.set_step("bug_bash", ID, step)
    try:
        outcome = _build(state, oof=oof)
    except ValueError as exc:
        outcome = Blocked(f"distribute_tests: {exc}")
    step = state.get_step("bug_bash", ID)
    step.note = outcome.reason if isinstance(outcome, Blocked) else outcome.note
    # The engine owns completion; CLI previews leave successful work ready for next.
    step.status = "blocked" if isinstance(outcome, Blocked) else "pending"
    state.set_step("bug_bash", ID, step)
    return outcome


def _build(state, *, oof):
    fail = mock_input("fail", MISSING)
    if fail is not MISSING:
        return Blocked(f"distribute_tests: {fail}")

    cfg = D.load_config()

    owner = state.owner_email
    if not owner:
        return Blocked("distribute_tests: release owner missing; resolve the owner before showing availability choices.")
    oce = _oce(state.get_step("bug_bash", ID).data or {})
    roster = _roster(cfg)
    step = state.get_step("bug_bash", ID)
    candidate_ids = set(D.eligible_testers(
        [m["upn"] for m in roster], cfg.get("always_excluded", []), owner=owner, oce=oce))
    step.data["oof_candidates"] = [m for m in roster if m["upn"] in candidate_ids]
    state.set_step("bug_bash", ID, step)
    if not candidate_ids:
        return Blocked("distribute_tests: no eligible testers after owner, on-call and configured exclusions.")
    if oof is not None:
        step.data["oof"] = D.confirm_oof(oof, roster, owner, state.release_id)
        state.set_step("bug_bash", ID, step)
    confirmation = step.data.get("oof")
    excluded_oof = D.validate_oof(confirmation, roster, owner, state.release_id)
    eligible = D.eligible_testers([m["upn"] for m in roster], cfg.get("always_excluded", []),
                                  owner=owner, oce=oce, oof=excluded_oof)
    if not eligible:
        return Blocked("distribute_tests: no eligible testers after exclusions — check the "
                       "roster, owner, on-call and owner-confirmed OOF exclusions.")

    recorded_ids, fill_binding = _fill_input(state)
    # Read the two test sets only after owner-confirmed availability.
    bcases = mock_input("broker_cases", MISSING)
    if bcases is MISSING:
        plan_id = _broker_plan_id(state)
        if not plan_id:
            return Blocked("distribute_tests: the Broker test plan hasn't been cloned yet "
                           "(clone_plans_broker) — run that first so the manual tests exist.")
        ok, sid, d = D.find_suite_id_by_name(plan_id, cfg["broker"]["suite_name"])
        if not ok or not sid:
            return Blocked(f"distribute_tests: couldn't find the '{cfg['broker']['suite_name']}' "
                           f"suite in Broker plan {plan_id} ({d or 'not found'}).")
        ok, bcases, d = D.broker_manual_cases(plan_id, sid)
        if not ok:
            hint = " — run `az login`" if str(d).startswith("AUTH") else ""
            return Blocked(f"distribute_tests: couldn't read Broker manual tests ({d}){hint}.")

    acases = _auth_cases()
    owner_triage = _owner_triage(state, acases, cfg)

    # The completed fill owns this set, including unmatched automated cases requiring triage.
    auto_ids = set(recorded_ids)
    auth_before = len(acases)
    if auto_ids:
        acases = [c for c in acases if int(c.get("id")) not in auto_ids]
    auth_excluded = auth_before - len(acases)
    acases = [c for c in acases if f"A:{c['id']}" not in owner_triage]

    tests = [{"id": f"B:{c['id']}", "assignee": c.get("assignee")} for c in bcases] + \
            [{"id": f"A:{c['id']}", "assignee": c.get("assignee")} for c in acases]

    # Distribute the combined set evenly, preserving preferences where possible.
    result = D.distribute(tests, eligible)

    # Store the preview and its reviewed inputs for the separate apply command.
    name_by_upn = {m.get("upn"): m.get("name") for m in roster if m.get("upn")}
    counts = result["counts"]
    step = state.get_step("bug_bash", ID)
    step.data = dict(step.data or {})
    step.data["plan"] = {
        "assignments": result["assignments"],      # {"B:<id>"/"A:<id>": upn}
        "counts": counts,
        "eligible": eligible,
        "owner_excluded": owner,
        "oce_excluded": oce,
        "oof_excluded": [m for m in roster if m["upn"] in excluded_oof],
        "review_inputs": D.review_inputs(roster, cfg.get("always_excluded"), owner, oce,
                                         confirmation),
        "broker_total": len(bcases),
        "auth_total": len(acases),
        "auth_excluded_automated": auth_excluded,
        "auth_automated_ids": sorted(auto_ids),
        "ui_fill_result": fill_binding,
        "owner_triage": owner_triage,
        "applied": False,
    }
    state.set_step("bug_bash", ID, step)

    lo, hi = (min(counts.values()), max(counts.values())) if counts else (0, 0)
    top = ", ".join(f"{name_by_upn.get(u, u)} {counts[u]}"
                    for u in sorted(eligible, key=lambda e: -counts[e])[:3])
    oce_note = f", OCE {oce}"
    auto_note = (f" Excluded {auth_excluded} already-automated auth case(s)."
                 if auth_excluded else "")
    return Done(
        f"Distribution PREVIEW ready: {len(tests)} tests (Broker {len(bcases)} + Auth "
        f"{len(acases)}) across {len(eligible)} testers — {lo}–{hi} each "
        f"({result['kept']} kept, {result['reassigned']} reassigned).{auto_note} Excluded owner "
        f"{owner}{oce_note}. Owner-confirmed OOF: "
        f"{', '.join(f'{name_by_upn[u]} <{u}>' for u in excluded_oof) or 'nobody'}. "
        f"e.g. {top}. Separate owner triage: {len(owner_triage)} cases for {owner} "
        f"({', '.join(owner_triage) or 'none'}); excluded from the manual split. Review, then apply with "
        f"`distribute-tests --release {state.release_id} --apply`.")


run = legacy_run(build)
