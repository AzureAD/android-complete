"""`paths` command — report the engine's self-located roots.

The engine already knows where it lives: cli_common derives every path from
this file's location (__file__), so nothing is hardcoded. This command exposes
those resolved absolute paths so the SKILL layer can (a) confirm the
android-complete clone on first run and (b) `cd` to the right place on any
machine instead of a hardcoded C:\\repos assumption.

  agent_root : the release-agent/ folder — run all `python -m orchestrator.cli` here
  repo_root  : the android-complete/ clone root (agent_root's parent)
  runs_root  : where release run-state lives (repo_root/.release-runs by default,
               or the --runs-root override)
"""
from __future__ import annotations
import json as _json
import os

from orchestrator import cli_common as C


def resolve_paths(runs_root: str | None = None) -> dict:
    """The engine's self-located roots. All absolute + normalized. `runs_root`
    honors the --runs-root override; otherwise the default (repo_root/.release-runs)."""
    agent_root = os.path.abspath(C.ROOT)
    repo_root = os.path.abspath(os.path.dirname(C.ROOT))
    rr = os.path.abspath(runs_root or C.DEFAULT_RUNS_ROOT)
    return {
        "agent_root": agent_root,
        "repo_root": repo_root,
        "runs_root": rr,
        "repo_name": os.path.basename(repo_root),
    }


def cmd_paths(args):
    """Print the engine's resolved roots (agent_root / repo_root / runs_root).
    Read-only, release-independent — use it to confirm the clone on first run and
    to `cd` correctly on any machine. --json for machine output."""
    p = resolve_paths(getattr(args, "runs_root", None))
    if getattr(args, "json", False):
        print(_json.dumps(p, indent=2))
        return 0
    print("Release-agent resolved paths:")
    print(f"  agent_root : {p['agent_root']}   (run `python -m orchestrator.cli …` here)")
    print(f"  repo_root  : {p['repo_root']}   ({p['repo_name']} clone)")
    print(f"  runs_root  : {p['runs_root']}   (release run-state)")
    return 0


def register(sub):
    pp = sub.add_parser("paths", help="Report the engine's self-located roots (agent_root / repo_root / runs_root)")
    pp.add_argument("--json", action="store_true")
    pp.set_defaults(func=cmd_paths)
