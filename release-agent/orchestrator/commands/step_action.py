"""Generic dispatcher for co-located step modules (`step-action`).

This is the ONE command the skill calls to resolve any migrated step into its
uniform outcome. It replaces the old per-step `prepare-X` commands: instead of a
bespoke `prepare-notice`, `prepare-flight-reminder`, … the skill runs

    python -m orchestrator.cli step-action --release <id> --phase preflight --step notice

The command looks up the step module (`steps.get_step`), calls its `build(state)`
(passing through any `--param k=v` the module's signature accepts, e.g. variant),
and prints the outcome as JSON. The skill reads `kind` and reacts uniformly:

    done         → already complete, nothing to run.
    blocked      → surface `reason` to the owner.
    needs_human  → show `prompt` (attestation or reminder).
    needs_skill  → run `tool` with `payload`, then `record-step --step <record_as>`.

Adding a scout step is now: write ONE module under steps/<phase>/ and register it
in steps/__init__._STEPS — no new CLI command, no skill-reference edits.
"""
from __future__ import annotations
import inspect
import json as _json

from orchestrator import cli_common as C
from orchestrator.outcomes import as_dict
import steps


def _parse_params(pairs) -> dict:
    """Turn ['variant=update', 'x=y'] into {'variant': 'update', 'x': 'y'}."""
    out = {}
    for item in pairs or []:
        if "=" not in item:
            raise ValueError(f"--param must be KEY=VALUE, got: {item!r}")
        k, v = item.split("=", 1)
        out[k.strip()] = v
    return out


def _accepted_kwargs(build, params: dict) -> dict:
    """Filter params to only the keyword args `build` actually declares, so an
    unrelated --param never crashes a step that doesn't take it."""
    try:
        sig = inspect.signature(build)
    except (TypeError, ValueError):
        return dict(params)
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(params)  # build(**kwargs) takes anything
    names = {name for name in sig.parameters if name != "state"}
    return {k: v for k, v in params.items() if k in names}


def cmd_step_action(args):
    mod = steps.get_step(args.phase, args.step)
    if mod is None:
        print(_json.dumps({
            "error": f"step '{args.phase}.{args.step}' is not migrated to the "
                     f"uniform contract; no co-located handler in steps/.",
            "phase": args.phase, "step": args.step,
        }))
        return 1
    if not hasattr(mod, "build"):
        print(_json.dumps({
            "error": f"step module '{args.phase}.{args.step}' has no build()",
        }))
        return 1

    # Agent steps run IN-PROCESS inside the engine's `next` (they perform the real
    # deterministic action). Executing their build() here would run that action a
    # second time, out of band — refuse and point to `next`.
    if getattr(mod, "KIND", None) == "agent":
        print(_json.dumps({
            "error": f"step '{args.phase}.{args.step}' is an agent step — the engine "
                     f"runs it in-process via `next`; don't dispatch it with step-action.",
            "phase": args.phase, "step": args.step, "kind": "agent",
        }))
        return 1

    try:
        params = _parse_params(getattr(args, "param", None))
    except ValueError as e:
        print(_json.dumps({"error": str(e)}))
        return 1

    st = C.load_state(args.runs_root, args.release)
    kwargs = _accepted_kwargs(mod.build, params)
    outcome = mod.build(st, **kwargs)

    out = as_dict(outcome)
    out["phase"] = args.phase
    out["step"] = getattr(mod, "ID", args.step)
    out["release"] = args.release
    print(_json.dumps(out))
    return 0


def register(sub):
    sp = sub.add_parser(
        "step-action",
        help="Resolve a migrated step into its uniform outcome JSON "
             "(done|blocked|needs_human|needs_skill)")
    sp.add_argument("--release", required=True)
    sp.add_argument("--phase", default="preflight")
    sp.add_argument("--step", required=True)
    sp.add_argument("--param", action="append", default=[],
                    help="Optional KEY=VALUE passed to the step's build() "
                         "(e.g. --param variant=update). Repeatable.")
    sp.set_defaults(func=cmd_step_action)
