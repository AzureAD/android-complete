"""`approve-orchestrator-gate` — approve a Phase-4 Release Orchestrator gate in one step.

The finalize phase has TWO orchestrator gates, each a normal engine gate whose approval must ALSO
submit the real ADO pipeline approval:
  * `remove_rc_tags_gate` → the 'Remove RC Tags' stage (publishes the release),
  * `publish_notes_gate` → the 'Publish GitHub Release Notes' stage (after the integration PRs merge).

Preview the exact provider target, then submit only its reviewed hash. The engine checkpoints
ownership, the attempt, the provider receipt and local completion separately. Interrupted work
uses its exact execution ID to reconcile; this adapter never infers permission to resubmit.
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C


def cmd_approve_orchestrator_gate(args):
    st, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    selection = orch.scheduling()
    try:
        phase_id, step_id = getattr(args, "phase", None), getattr(args, "step", None)
        execution_id = getattr(args, "execution_id", None)
        if bool(phase_id) != bool(step_id):
            raise ValueError("--phase and --step must be provided together")
        if phase_id is None and execution_id:
            owners = [item.definition for item in selection.steps
                      if orch.step_execution(item.definition.phase_id, item.definition.id).get("id") == execution_id]
            if len(owners) != 1:
                raise ValueError("No unique gate owns that --execution-id")
            phase_id, step_id = owners[0].phase_id, owners[0].id
        elif phase_id is None:
            hold = selection.focus_hold
            phase_id, step_id = (hold.phase_id, hold.step_id) if hold else (None, None)
        definition = next((item.definition for item in selection.steps
                           if (item.definition.phase_id, item.definition.id) == (phase_id, step_id)), None)
        if not definition or definition.approval_command != "approve-orchestrator-gate":
            raise ValueError("Not holding at a Release Orchestrator gate; use its exact --phase/--step or --execution-id")
        if getattr(args, "preview", False):
            print(_json.dumps(orch.preview_gate_approval(phase_id, step_id, comment=args.comment), indent=2))
            return 0
        result = orch.execute_gate_approval(
            phase_id, step_id, comment=args.comment,
            review_hash=getattr(args, "review_hash", None), approved_by=getattr(args, "approved_by", None),
            executor=getattr(args, "executor", None), execution_id=execution_id,
            reserve_only=getattr(args, "reserve", False))
    except ValueError as exc:
        print(_json.dumps({"error": str(exc), "permission_to_execute": False}))
        return 1
    if result["status"] != "approved":
        print(_json.dumps(result, indent=2))
        return 0 if result["status"] in ("reserved", "receipt_recorded") else 1
    el = C.elog(args.runs_root, args.release)
    el.log("gate_approved", phase=phase_id, step=step_id, driver=args.comment or None)
    actions = orch.run_until_gate()
    C.save_state(st, args.runs_root, args.release)
    C.log_actions(el, actions, state=st)
    C.emit(args.runs_root, args.release,
           C.advance_block(actions, orch, lead=[f"  {result['message']}"]), kind="advance",
           log_text=C.advance_log_summary(actions, lead=[result["message"]]))
    return 0


def register(sub):
    sp = sub.add_parser(
        "approve-orchestrator-gate",
        help="Approve a Phase-4 orchestrator gate (remove_rc_tags_gate 'Remove RC Tags' or "
             "publish_notes_gate 'Publish GitHub Release Notes') AND submit the real ADO approval.")
    sp.add_argument("--release", required=True)
    sp.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    sp.add_argument("--comment", default="")
    sp.add_argument("--phase", default=None)
    sp.add_argument("--step", default=None)
    sp.add_argument("--preview", action="store_true", help="Read the exact target and review hash; no state changes")
    sp.add_argument("--review-hash", default=None, help="Exact hash from the human-reviewed preview")
    sp.add_argument("--approved-by", default=None, help="Human reviewer recorded with the frozen request")
    sp.add_argument("--executor", default=None, help="Claiming session/worker; defaults to approved-by")
    sp.add_argument("--reserve", action="store_true", help="Checkpoint the reviewed request without submitting")
    sp.add_argument("--execution-id", default=None, help="Resume/reconcile exactly this owned approval; never resend an attempted write")
    sp.set_defaults(func=cmd_approve_orchestrator_gate)
