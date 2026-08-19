"""`sim` — fast-forward the engine to a mid-release point to validate a phase.

  release-agent sim list                       # list available scenarios
  release-agent sim run --scenario <name>      # fast-forward + print status
  release-agent sim run --scenario <name> --freeze   # + snapshot to tests/fixtures/

Writes state under a dedicated .sim-runs root (so it never clobbers a real release);
the printed hint shows the --runs-root to pass to follow-up commands (status, rc-report).
"""
from __future__ import annotations
import json as _json

from orchestrator import sim as S
from orchestrator import render


def cmd_sim_list(args):
    names = S.list_scenarios()
    if getattr(args, "json", False):
        print(_json.dumps(names, indent=2)); return 0
    if not names:
        print("No scenarios in config/scenarios/."); return 0
    print("Scenarios:")
    for n in names:
        print(f"  - {n}")
    return 0


def cmd_sim_run(args):
    res = S.run_scenario(args.scenario, runs_root=getattr(args, "runs_root", None),
                         config_path=getattr(args, "config", None),
                         freeze=getattr(args, "freeze", False))
    if getattr(args, "json", False):
        d = {k: v for k, v in res.__dict__.items() if k != "state"}
        print(_json.dumps(d, indent=2)); return 0 if res.reached else 1

    tick = "✅" if res.reached else "⚠"
    print(f"{tick} Scenario '{res.scenario}' — target {res.target['phase']} · {res.target['at']}")
    print(f"   release {res.release_id}  ·  CCD {res.ccd}  ·  as_of {res.as_of}")
    print(f"   fast-forwarded {res.steps_forwarded} step(s); "
          f"approved {len(res.gates_approved)} gate(s): {', '.join(res.gates_approved) or 'none'}")
    print(f"   stop: [{res.stop_kind}] {res.stop_message}")
    if res.problems:
        print("   problems:")
        for p in res.problems:
            print(f"     - {p}")
    if res.frozen_to:
        print(f"   ❄ frozen → {res.frozen_to}")
    print(f"   runs-root: {res.runs_root}")
    print(f"   → inspect: python -m orchestrator.cli --runs-root {res.runs_root} "
          f"status --release {res.release_id}")
    print()
    # Reuse the real render so the sim shows exactly what the release would.
    from orchestrator.engine import Orchestrator
    orch = Orchestrator(getattr(args, "config", None) or S.C.DEFAULT_CONFIG, res.state,
                        as_of=S.schedule.parse_date(res.as_of))
    print(render.status_view(orch.status_report()))
    return 0 if res.reached else 1


def cmd_sim_status(args):
    """Render the status of the sim sandbox (the last scenario's state) so you don't
    need the long --runs-root path or to remember the release id."""
    from orchestrator import discovery
    from orchestrator.engine import Orchestrator
    runs_root = getattr(args, "runs_root", None) or S.DEFAULT_SIM_RUNS
    rel = getattr(args, "release", None)
    if not rel:
        rel = ((discovery.resolve(runs_root, None) or {}).get("release") or {}).get("release_id")
    if not rel:
        print(f"No sim release under {runs_root}. Run `sim run --scenario <name>` first.")
        return 1
    st = S.C.load_state(runs_root, rel)
    orch = Orchestrator(getattr(args, "config", None) or S.C.DEFAULT_CONFIG, st,
                        as_of=S.schedule.parse_date(st.ccd) if st.ccd else None)
    print(f"(sim sandbox · runs-root {runs_root})\n")
    print(render.status_view(orch.status_report()))
    return 0


def register(sub):
    sp = sub.add_parser("sim", help="Fast-forward the engine to a mid-release point (testing)")
    ssub = sp.add_subparsers(dest="sim_cmd", required=True)

    lp = ssub.add_parser("list", help="List available scenarios")
    lp.add_argument("--json", action="store_true")
    lp.set_defaults(func=cmd_sim_list)

    rp = ssub.add_parser("run", help="Fast-forward to a scenario's target point")
    rp.add_argument("--scenario", required=True, help="Scenario name (config/scenarios/<name>.yaml)")
    rp.add_argument("--runs-root", default=S.DEFAULT_SIM_RUNS,
                    help="Where sim state is written (default: .sim-runs — kept apart from real releases)")
    rp.add_argument("--freeze", action="store_true", help="Snapshot produced state to tests/fixtures/<name>.json")
    rp.add_argument("--json", action="store_true", help="Emit the raw SimResult")
    rp.set_defaults(func=cmd_sim_run)

    stp = ssub.add_parser("status", help="Show the status of the sim sandbox (the last run)")
    stp.add_argument("--runs-root", default=S.DEFAULT_SIM_RUNS,
                     help="Sim runs-root to read (default: .sim-runs)")
    stp.add_argument("--release", help="Release id (auto-discovered when a single sim release exists)")
    stp.set_defaults(func=cmd_sim_status)
