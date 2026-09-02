"""Command modules for the Release Orchestrator CLI.

Each module in this package owns one domain of commands. A module exposes a
`register(subparsers)` function that adds its subparser(s) and wires each to its
handler via `set_defaults(func=...)`. `cli.py` imports REGISTRARS and calls each
one, so adding a command is a localized change (new/edited module only).
"""
from . import (release, readiness, pipeline, notify, infra_cmd, automation,
               logs, lockdown, notice, step_action, localization, rc_report, rc_poll,
               distribute, bugbash_chat, bugbash_update, sim, gate_approve, integ_prs_cmd,
               oneauth_pr_cmd, telemetry_cmd, payload_wiki_cmd, status_email_cmd, paths_cmd)

# Order controls how subcommands appear in --help.
REGISTRARS = [
    release.register,
    readiness.register,
    pipeline.register,
    notify.register,
    lockdown.register,
    step_action.register,
    notice.register,
    localization.register,
    rc_report.register,
    rc_poll.register,
    telemetry_cmd.register,
    distribute.register,
    bugbash_chat.register,
    bugbash_update.register,
    gate_approve.register,
    integ_prs_cmd.register,
    oneauth_pr_cmd.register,
    payload_wiki_cmd.register,
    status_email_cmd.register,
    sim.register,
    logs.register,
    automation.register,
    infra_cmd.register,
    paths_cmd.register,
]
