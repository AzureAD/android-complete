"""`approve-orchestrator-gate` — approve a Phase-4 Release Orchestrator gate in one step.

The finalize phase has TWO orchestrator gates, each a normal engine gate whose approval must ALSO
submit the real ADO pipeline approval:
  * `gate_watch`         → the 'Remove RC Tags' stage (publishes the release),
  * `publish_notes_gate` → the 'Publish GitHub Release Notes' stage (after the integration PRs merge).

Rather than teach the engine about that ADO action, this command composes it: it looks up whichever
gate step is currently holding, calls its `submit_approval` (which submits the ADO approval), then
records the release-agent gate the usual way (the same `approve_gate` + advance the plain `approve`
command uses). The engine and the shared `approve` command are untouched. Plain `deny` still denies.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C


def cmd_approve_orchestrator_gate(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    # Works for ANY finalize gate whose step module exposes submit_approval (gate_watch =
    # 'Remove RC Tags', publish_notes_gate = 'Publish GitHub Release Notes').
    import steps as _steps
    mod = (_steps.get_step(st.current_phase, st.current_step)
           if st.status == "holding_gate" else None)
    if not mod or not hasattr(mod, "submit_approval"):
        print(_json.dumps({
            "error": "not holding at a Release Orchestrator gate (gate_watch / publish_notes_gate) "
                     "— nothing to approve here.",
            "status": st.status, "phase": st.current_phase, "step": st.current_step}))
        return 1

    # 1) submit the REAL ADO approval first — don't record the gate if this fails.
    ok, detail = mod.submit_approval(st, args.comment or "")
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
        help="Approve a Phase-4 orchestrator gate (gate_watch 'Remove RC Tags' or "
             "publish_notes_gate 'Publish GitHub Release Notes') AND submit the real ADO approval.")
    sp.add_argument("--release", required=True)
    sp.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    sp.add_argument("--comment", default="")
    sp.set_defaults(func=cmd_approve_orchestrator_gate)
