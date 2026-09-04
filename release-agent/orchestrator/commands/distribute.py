"""`distribute-tests` — preview or apply the Phase-3 bug-bash test distribution.

  --preview (default) : re-run the distribute_tests step to (re)compute the plan and print
                        the per-tester table. Read-only; stores the plan on the step.
  --apply             : write System.AssignedTo on every test case per the STORED plan
                        (from a prior preview). Mutates the shared work items. Records the
                        step done with an applied summary.

The OCE is supplied with --oce (the skill resolves it via ICM team 78848); the owner comes
from state. --json emits the raw plan for the skill.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from orchestrator import mocks as mocks_mod
from orchestrator.outcomes import as_dict
from steps.lib import mockctx
from tools import distribution as D
import steps


def _print_table(plan):
    counts = plan.get("counts") or {}
    print(f"Eligible testers: {len(plan.get('eligible') or [])} | "
          f"Broker {plan.get('broker_total')} + Auth {plan.get('auth_total')} = "
          f"{plan.get('broker_total',0)+plan.get('auth_total',0)} tests | "
          f"applied={plan.get('applied')}")
    print(f"  excluded owner: {plan.get('owner_excluded')} | OCE: {plan.get('oce_excluded') or '(none)'}")
    for u in sorted(plan.get("eligible") or [], key=lambda e: -counts.get(e, 0)):
        print(f"    {counts.get(u,0):3}  {u}")


def cmd_distribute_tests(args):
    st = C.load_state(args.runs_root, args.release)

    if args.apply:
        return _apply(args, st)

    # PREVIEW: run the step's build() with the OCE injected, persist the plan, print it.
    spec = dict(mocks_mod.load_mocks().get("bug_bash.distribute_tests", {}))
    if args.oce:
        spec["oce"] = args.oce
    with mockctx.active(spec):
        out = as_dict(steps.get_step("bug_bash", "distribute_tests").build(st))
    C.save_state(st, args.runs_root, args.release)
    if out["kind"] == "blocked":
        print(_json.dumps({"error": out["reason"]}) if args.json else f"BLOCKED: {out['reason']}")
        return 1
    plan = (st.get_step("bug_bash", "distribute_tests").data or {}).get("plan") or {}
    if args.json:
        print(_json.dumps(plan, indent=2))
    else:
        print(out["note"]); print(); _print_table(plan)
    return 0


def _apply(args, st):
    plan = (st.get_step("bug_bash", "distribute_tests").data or {}).get("plan") or {}
    assignments = plan.get("assignments") or {}
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
                           f"Assigned {ok_n} bug-bash tests across {len(plan.get('eligible') or [])} testers.")
    C.save_state(orch.state, args.runs_root, args.release)
    C.emit(args.runs_root, args.release,
           f"[distribute] assigned {ok_n} tests across {len(plan.get('eligible') or [])} testers", kind="step")
    print(f"Applied {ok_n} assignments across {len(plan.get('eligible') or [])} testers.")
    return 0


def register(sub):
    p = sub.add_parser("distribute-tests",
                       help="Preview or apply the Phase-3 bug-bash test distribution")
    p.add_argument("--release", required=True)
    p.add_argument("--oce", default=None,
                   help="On-call engineer identifier to exclude (skill resolves via ICM 78848)")
    p.add_argument("--apply", action="store_true",
                   help="Write System.AssignedTo per the stored plan (mutates shared work items)")
    p.add_argument("--json", action="store_true", help="Emit the raw plan JSON")
    p.set_defaults(func=cmd_distribute_tests)
