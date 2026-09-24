"""One source for CLI registration and workflow command capabilities; imports are lazy."""
from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class CommandModule:
    name: str
    approvals: tuple[str, ...] = ()
    external_writes: tuple[str, ...] = ()


# Order is the public --help order. Command modules still own argument semantics.
COMMAND_MODULES = (
    CommandModule("release"),
    CommandModule("workflow_revision"),
    CommandModule("readiness"),
    CommandModule("pipeline"),
    CommandModule("notify"),
    CommandModule("delivery_cmd"),
    CommandModule("lockdown"),
    CommandModule("step_action"),
    CommandModule("effect"),
    CommandModule("notice"),
    CommandModule("localization", external_writes=("launch-localization",)),
    CommandModule("rc_report"),
    CommandModule("rc_report_publish", external_writes=("publish-rc-report",)),
    CommandModule("rc_poll"),
    CommandModule("telemetry_cmd"),
    CommandModule("distribute", external_writes=("distribute-tests",)),
    CommandModule("broker_plan"),
    CommandModule("bugbash_chat"),
    CommandModule("bugbash_update"),
    CommandModule("gate_approve", approvals=("approve-orchestrator-gate",)),
    CommandModule("finalize_poll"),
    CommandModule("integ_prs_cmd", external_writes=("create-integration-prs",)),
    CommandModule("oneauth_pr_cmd", external_writes=("create-oneauth-common-pr",)),
    CommandModule("payload_wiki_cmd", external_writes=("create-payload-wiki",)),
    CommandModule(
        "signoff_cmd",
        external_writes=(
            "start-release-signoff", "start-upload-whats-new", "start-upload-alpha",
            "start-beta-play-store")),
    CommandModule("status_email_cmd"),
    CommandModule("sim"),
    CommandModule("logs"),
    CommandModule("automation"),
    CommandModule("infra_cmd"),
    CommandModule("paths_cmd"),
    CommandModule("preview_cmd"),
)

APPROVAL_COMMANDS = frozenset(verb for item in COMMAND_MODULES for verb in item.approvals)
EXTERNAL_WRITE_COMMANDS = frozenset(verb for item in COMMAND_MODULES for verb in item.external_writes)


def register_commands(subparsers):
    for item in COMMAND_MODULES:
        before = set(subparsers.choices)
        import_module(f"orchestrator.commands.{item.name}").register(subparsers)
        added = set(subparsers.choices) - before
        missing = set(item.approvals + item.external_writes) - added
        if missing:
            raise ValueError(f"Command module {item.name} did not register declared capabilities: {sorted(missing)}")
