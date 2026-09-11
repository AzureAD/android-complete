"""`distribute-tests` — preview or apply the Phase-3 bug-bash test distribution.

  --preview (default) : re-run the distribute_tests step to (re)compute the plan and print
                        the per-tester table. Read-only; stores the plan on the step.
  --apply             : write manual and owner-triage case assignees per the STORED plan,
                        then align selected plan testers without changing outcomes.
                        Records done only after both assignment surfaces succeed.

The owner must first answer the Bug Bash OOF question: --no-oof or repeatable --oof.
No answer means reuse an existing confirmation, never assume nobody is OOF.
The OCE is supplied with --oce before availability choices; the owner comes from state.
--json includes only eligible candidates when owner input is needed, or the plan and its review inputs.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from orchestrator import mocks as mocks_mod
from orchestrator.outcomes import as_dict
from steps.lib import mockctx
from tools import distribution as D
from steps.bug_bash import distribute_tests as distribution_step


def _print_table(plan):
    counts = plan.get("counts") or {}
    print(f"Eligible testers: {len(plan.get('eligible') or [])} | "
          f"Broker {plan.get('broker_total')} + Auth {plan.get('auth_total')} = "
          f"{plan.get('broker_total',0)+plan.get('auth_total',0)} tests | "
          f"applied={plan.get('applied')}")
    print(f"  excluded owner: {plan.get('owner_excluded')} | OCE: {plan.get('oce_excluded') or '(none)'}")
    excluded = ", ".join(f"{m['name']} <{m['upn']}>" for m in plan.get("oof_excluded", []))
    print(f"  owner-confirmed OOF: {excluded or 'nobody'}")
    for u in sorted(plan.get("eligible") or [], key=lambda e: -counts.get(e, 0)):
        print(f"    {counts.get(u,0):3}  {u}")
    for key, triage in plan.get("owner_triage", {}).items():
        print(f"  owner triage: {key} -> {triage['assignee']} ({', '.join(triage['reasons'])})")


def cmd_distribute_tests(args):
    selection = [] if args.no_oof else args.oof
    if args.apply and (selection is not None or args.oce is not None):
        message = "Do not combine --apply with --oof/--no-oof/--oce. Create and review a new preview first."
        print(_json.dumps({"error": message}) if args.json else message)
        return 1
    st = C.load_state(args.runs_root, args.release)
    spec = dict(mocks_mod.load_mocks().get("bug_bash.distribute_tests", {}))
    with mockctx.active(spec):
        if args.apply:
            return _apply(args, st)
        try:
            out = as_dict(distribution_step.build(st, oof=selection, oce=args.oce))
        finally:
            # Persist invalidation even if gathering a replacement preview raises.
            C.save_state(st, args.runs_root, args.release)
    if out["kind"] == "blocked":
        data = st.get_step("bug_bash", "distribute_tests").data or {}
        candidates = data.get("oof_candidates", [])
        if args.json:
            print(_json.dumps({"error": out["reason"], "candidates": candidates,
                              "oof": data.get("oof")}, indent=2))
        else:
            print(f"BLOCKED: {out['reason']}")
            for member in candidates:
                print(f"  {member['name']} <{member['upn']}>")
        return 1
    plan = (st.get_step("bug_bash", "distribute_tests").data or {}).get("plan") or {}
    if args.json:
        print(_json.dumps(plan, indent=2))
    else:
        print(out["note"]); print(); _print_table(plan)
    return 0


def _apply(args, st):
    try:
        distribution_step.validate_stored_plan(st)
    except ValueError as exc:
        distribution_step.invalidate_preview(st)
        C.save_state(st, args.runs_root, args.release)
        print(_json.dumps({"error": str(exc)}) if args.json else f"BLOCKED: {exc}")
        return 1
    plan = (st.get_step("bug_bash", "distribute_tests").data or {}).get("plan") or {}
    manual = plan.get("assignments") or {}
    triage = {key: entry["assignee"] for key, entry in plan.get("owner_triage", {}).items()}
    assignments = {**manual, **triage}
    if not assignments:
        print("No stored distribution plan — run the preview first "
              "(distribute-tests --release <id>).")
        return 1
    if plan.get("applied"):
        print("This distribution was already applied.")
        return 0

    ok_n, fail = 0, []
    for key, upn in assignments.items():
        case_id = key.split(":", 1)[1]          # 'B:123' / 'A:123' -> '123'
        ok, detail = D.set_assigned_to(case_id, upn)
        if ok:
            ok_n += 1
        else:
            fail.append((case_id, upn, detail))
            if str(detail).startswith("AUTH"):
                break                            # stop on auth failure — nothing will work
    if not fail:
        ok, detail = distribution_step.sync_plan_testers(st, assignments)
        if not ok:
            fail.append(("plan testers", "", detail))

    plan["applied"] = not fail
    step = st.get_step("bug_bash", "distribute_tests")
    step.data = dict(step.data or {}); step.data["plan"] = plan
    st.set_step("bug_bash", "distribute_tests", step)
    C.save_state(st, args.runs_root, args.release)

    if fail:
        C.emit(args.runs_root, args.release,
               f"[distribute] applied {ok_n}/{len(assignments)}; {len(fail)} failed", kind="step")
        print(f"Applied {ok_n}/{len(assignments)} assignments; {len(fail)} FAILED:")
        for cid, upn, d in fail[:10]:
            print(f"  case {cid} -> {upn}: {d}")
        return 2
    # mark the step done on a clean apply
    st2, orch = C.load_orch(args.runs_root, args.release, args.config)
    orch.record_scout_step("bug_bash", "distribute_tests", "pass",
                           f"Assigned {len(manual)} manual cases across {len(plan.get('eligible') or [])} testers "
                           f"and {len(triage)} cases to the release owner for triage; plan testers aligned.")
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[distribute] assigned {len(manual)} manual cases across {len(plan.get('eligible') or [])} "
           f"testers and {len(triage)} owner-triage cases; plan testers aligned", kind="step")
    print(f"Applied {len(manual)} manual assignments and {len(triage)} owner-triage assignments; "
          "plan testers aligned. Outcomes unchanged.")
    return 0


def register(sub):
    p = sub.add_parser("distribute-tests",
                       help="Preview or apply the Phase-3 bug-bash test distribution")
    p.add_argument("--release", required=True)
    p.add_argument("--oce", default=None,
                   help="Verified primary on-call UPN; required before availability choices unless already recorded")
    choice = p.add_mutually_exclusive_group()
    choice.add_argument("--oof", action="append", metavar="UPN",
                       help="Owner-confirmed OOF tester; repeat per person (exact roster name also accepted)")
    choice.add_argument("--no-oof", action="store_true",
                       help="Record the release owner's explicit answer that nobody is OOF")
    p.add_argument("--apply", action="store_true",
                   help="Apply reviewed manual/triage case assignees and plan testers; preserve outcomes")
    p.add_argument("--json", action="store_true", help="Emit the raw plan JSON")
    p.set_defaults(func=cmd_distribute_tests)
