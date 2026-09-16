"""Command modules for the Release Orchestrator CLI.

Each module in this package owns one domain of commands. A module exposes a
`register(subparsers)` function that adds its subparser(s) and wires each to its
handler via `set_defaults(func=...)`. The source-only command catalog owns the
registration order and workflow capabilities; modules are imported lazily.
"""
