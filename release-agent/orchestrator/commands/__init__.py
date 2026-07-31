"""Command modules for the Release Orchestrator CLI.

Each module in this package owns one domain of commands. A module exposes a
`register(subparsers)` function that adds its subparser(s) and wires each to its
handler via `set_defaults(func=...)`. `cli.py` imports REGISTRARS and calls each
one, so adding a command is a localized change (new/edited module only).
"""
from . import release, readiness, pipeline, notify, infra_cmd, automation, logs, lockdown, notice

# Order controls how subcommands appear in --help.
REGISTRARS = [
    release.register,
    readiness.register,
    pipeline.register,
    notify.register,
    lockdown.register,
    notice.register,
    logs.register,
    automation.register,
    infra_cmd.register,
]
