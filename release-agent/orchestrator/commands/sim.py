"""`sim` — seed the release to a mid-release point so you can test a phase.

  release-agent sim list                       # list available scenarios
  release-agent sim run --scenario <name>      # seed the REAL release to the target

A scenario fast-forwards the real engine (earlier phases + entry gate completed from
mocks) and writes the produced state as the real release — any existing state at that
id is backed up first. After it runs you just talk to the skill normally: `status`,
`rc-report`, `next`, `approve`. Pass `--runs-root` to target a throwaway sandbox instead.
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
    print(f"{tick} Seeded release {res.release_id} → {res.target['phase']} · {res.target['at']}  "
          f"(scenario '{res.scenario}')")
    print(f"   CCD {res.ccd}  ·  as_of {res.as_of}  ·  "
          f"forwarded {res.steps_forwarded} step(s), approved {len(res.gates_approved)} gate(s)")
    print(f"   stop: [{res.stop_kind}] {res.stop_message}")
    if res.problems:
        print("   problems:")
        for p in res.problems:
            print(f"     - {p}")
    if res.backed_up_to:
        print(f"   ↩ previous state backed up → {res.backed_up_to}")
    if res.frozen_to:
        print(f"   ❄ frozen → {res.frozen_to}")
    print("   → now just talk to the release skill: status · rc-report · next · approve")
    print()
    from orchestrator.engine import Orchestrator
    orch = Orchestrator(getattr(args, "config", None) or S.C.DEFAULT_CONFIG, res.state,
                        as_of=S.schedule.parse_date(res.as_of))
    print(render.status_view(orch.status_report()))
    return 0 if res.reached else 1


def register(sub):
    sp = sub.add_parser("sim", help="Seed the release to a mid-release point (testing)")
    ssub = sp.add_subparsers(dest="sim_cmd", required=True)

    lp = ssub.add_parser("list", help="List available scenarios")
    lp.add_argument("--json", action="store_true")
    lp.set_defaults(func=cmd_sim_list)

    rp = ssub.add_parser("run", help="Seed the real release to a scenario's target point")
    rp.add_argument("--scenario", required=True, help="Scenario name (config/scenarios/<name>.yaml)")
    rp.add_argument("--runs-root", default=S.DEFAULT_SEED_RUNS,
                    help="Runs-root to seed (default: the REAL runs-root; pass a path for a sandbox)")
    rp.add_argument("--freeze", action="store_true", help="Also snapshot the produced state to tests/fixtures/<name>.json")
    rp.add_argument("--json", action="store_true", help="Emit the raw SimResult")
    rp.set_defaults(func=cmd_sim_run)
