"""Release Orchestrator — CLI entry point (thin assembler).

The interface the /release-agent skill calls. This file only wires the parser and
dispatches; the command handlers live in `orchestrator/commands/` (one module per
domain) and shared plumbing in `orchestrator/cli_common.py`.

  python -m orchestrator.cli <command> [options]

State lives in <runs-root>/<release>/release-state.json (gitignored).
Config is release-agent/config/*.yaml.
"""
from __future__ import annotations
import argparse
import os
import sys

# Force UTF-8 stdout so status glyphs don't crash under Windows cp1252 when piped.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):    # non-reconfigurable stream / unsupported encoding
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # release-agent/
sys.path.insert(0, ROOT)

from orchestrator import cli_common as C
from orchestrator.commands import REGISTRARS


def build_parser():
    p = argparse.ArgumentParser(prog="release-agent",
                                description="Release Orchestrator backbone (X4+X5).")
    p.add_argument("--config", default=C.DEFAULT_CONFIG)
    p.add_argument("--runs-root", default=C.DEFAULT_RUNS_ROOT)
    # NOTE: --as-of is defined per-command (status/next/approve/deny/done/resume/notify),
    # where it must appear AFTER the subcommand. It is intentionally NOT a global flag:
    # argparse lets a subparser's own --as-of silently clobber a global one, which is a
    # footgun. Commands that don't take a simulated clock simply omit it.
    sub = p.add_subparsers(dest="cmd", required=True)
    for register in REGISTRARS:
        register(sub)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # Serialize state read-modify-write per release so parallel CLI invocations
    # (e.g. the skill firing record-step calls at once) can't clobber each other.
    runs_root = getattr(args, "runs_root", None)
    release = C.effective_release(runs_root, getattr(args, "release", None))
    with C.state_lock(runs_root, release):
        return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
