"""`record-telemetry` — the follow-up the `telemetry_verify` scout step names
(`payload.followup_command`). After the skill runs the Kusto query and reads the row count,
it calls this with `--rows <N>`: the step passes only when telemetry is flowing (rows > 0),
otherwise it records `attention` (a blocked owner task: post the heads-up in the Android Core
Team channel before the bug bash is declared complete).
"""
from __future__ import annotations
import json as _json

from orchestrator import cli_common as C
from tools.coordinates import coords


def cmd_record_telemetry(args):
    _, orch = C.load_orch(args.runs_root, args.release, args.config, C.parse_as_of(args))
    try:
        rows = int(args.rows)
    except (TypeError, ValueError):
        print(_json.dumps({"error": f"--rows must be an integer (got {args.rows!r})."}))
        return 1
    version = (args.version or "the bug-bash version").strip()

    if rows > 0:
        status = "pass"
        detail = f"telemetry flowing for {version} — {rows} row(s) in loadaccountsoperations."
    else:
        status = "attention"
        chan = coords.team("android_core")
        detail = (f"no telemetry rows for {version} yet — telemetry is NOT reaching Kusto from "
                  f"any tester device. Post a heads-up in the {chan.get('name', 'Android Core Team')} "
                  f"channel before declaring the bug bash complete, then re-run this check.")

    orch.record_scout_step("build_verify", "telemetry_verify", status, detail)
    step = orch.state.get_step("build_verify", "telemetry_verify")
    step.by = "scout"
    orch.state.set_step("build_verify", "telemetry_verify", step)
    C.save_state(orch.state, args.runs_root, args.release)

    C.emit(args.runs_root, args.release,
           f"[{'ok' if status == 'pass' else 'attention'}] telemetry_verify: {detail}",
           kind="step")
    print(_json.dumps({"version": version, "rows": rows, "status": status, "detail": detail}))
    return 0 if status == "pass" else 2


def register(sub):
    rt = sub.add_parser(
        "record-telemetry",
        help="Record the telemetry_verify step after the Kusto query: pass if rows>0, else "
             "attention (post the Android Core Team heads-up)")
    rt.add_argument("--release", required=True)
    rt.add_argument("--rows", required=True, help="Row count returned by the telemetry query")
    rt.add_argument("--version", default=None, help="The bug-bash APK version that was queried")
    rt.add_argument("--as-of", default=None, help="Simulated clock (YYYY-MM-DD); default today")
    rt.set_defaults(func=cmd_record_telemetry)
