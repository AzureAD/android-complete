"""`approve-orchestrator-gate` — approve the Phase-4 `gate_watch` gate in one step.

`gate_watch` is a normal engine gate (human approve/deny), but approving it must ALSO submit the
real Release Orchestrator "Remove RC Tags" approval in ADO. Rather than teach the engine about
that action, this dedicated command composes it: it submits the ADO approval (via the step
module's `submit_approval`) and then records the release-agent gate the usual way (the same
`approve_gate` + advance the plain `approve` command uses). The engine and the shared `approve`
command are untouched.

The skill runs this for the `finalize.gate_watch` gate; plain `deny` still denies.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C


def cmd_approve_orchestrator_gate(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    if st.status != "holding_gate" or st.current_step != "gate_watch":
        print(_json.dumps({
            "error": "not holding at the finalize.gate_watch gate — nothing to approve here.",
            "status": st.status, "phase": st.current_phase, "step": st.current_step}))
        return 1

    # 1) submit the REAL ADO approval first — don't record the gate if this fails.
    from steps.finalize import gate_watch as gw
    ok, detail = gw.submit_approval(st, args.comment or "")
    if not ok:
        print(_json.dumps({"error": f"orchestrator approval NOT submitted: {detail}"}))
        return 1

    # 2) record the release-agent gate + advance (identical to `approve`).
    gate_phase, gate_step = st.current_phase, st.current_step
    act = orch.approve_gate(f"{(args.comment or '').strip()} [ADO: {detail}]".strip())
    el = C.elog(args.runs_root, args.release)
    if act.kind != "idle":
        el.log("gate_approved", phase=gate_phase, step=gate_step, driver=args.comment or None)
    actions = orch.run_until_gate()
    C.save_state(st, args.runs_root, args.release)
    C.log_actions(el, actions, state=st)
    C.emit(args.runs_root, args.release,
           C.advance_block(actions, orch, lead=[f"  {act.message}"]), kind="advance",
           log_text=C.advance_log_summary(actions, lead=[act.message]))
    return 0


def register(sub):
    sp = sub.add_parser(
        "approve-orchestrator-gate",
        help="Approve the Phase-4 gate_watch gate AND submit the real Release Orchestrator "
             "'Remove RC Tags' approval (publishes the release).")
    sp.add_argument("--release", required=True)
    sp.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    sp.add_argument("--comment", default="")
    sp.set_defaults(func=cmd_approve_orchestrator_gate)
