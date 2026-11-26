"""CLI commands for inspecting and switching the release environment profile (`test` | `prod`)."""
from __future__ import annotations

import json

from orchestrator import cli_common as C, discovery, environment as E


def cmd_environment(args):
    """Display the active environment profile (`test` vs `prod`), resolved targets, and step defaults."""
    owner_email = None
    explicit_rel = getattr(args, "release", None)
    if explicit_rel:
        try:
            st = C.load_state(args.runs_root, explicit_rel)
            owner_email = st.owner_email
        except Exception:  # noqa: BLE001
            owner_email = None
    else:
        disc = discovery.resolve(args.runs_root, None, args.config)
        if disc.get("release"):
            try:
                st = C.load_state(args.runs_root, disc["release"]["release_id"])
                owner_email = st.owner_email
            except Exception:  # noqa: BLE001
                owner_email = None

    summary = E.environment_summary(owner_email=owner_email, config_path=args.config)
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2))
        return 0

    badge = "🚀 prod (LIVE LAUNCH TARGETS)" if summary["is_production"] else f"🧪 {summary['active_profile']} (SAFE PRE-LAUNCH TARGETS)"
    lines = [
        f"Active Environment Profile: {badge}",
        f"  Config file: {summary['environment_file']}",
    ]
    if summary.get("local_override_file"):
        lines.append(f"  Local override: {summary['local_override_file']}")
    lines.extend([
        "",
        "Emails & Invites:",
        f"  Subject prefix  : {summary['emails']['subject_prefix']!r}",
        f"  Partner status  : {', '.join(summary['emails']['partner_status'])}",
        f"  Bug Bash invite : {', '.join(summary['emails']['bug_bash_invite'])}",
        "",
        "Teams Targets:",
    ])
    for k, v in (summary.get("teams") or {}).items():
        disp = v.get("name") if isinstance(v, dict) else v
        lines.append(f"  {k:22}: {disp}")
    lines.extend(["", "Artifact Prefixes:"])
    for k, v in (summary.get("artifacts") or {}).items():
        lines.append(f"  {k:22}: {v!r}")
    if summary.get("step_defaults_keys"):
        lines.extend(["", "Configured Step Defaults:"])
        for k in summary["step_defaults_keys"]:
            lines.append(f"  - {k}")
    lines.extend([
        "",
        "Switch profile anytime with:  python -m orchestrator.cli set-profile --profile test|prod",
    ])
    print("\n".join(lines))
    return 0


def cmd_set_profile(args):
    """Switch `active_profile` in `config/environment.yaml` (`test` or `prod`)."""
    try:
        updated_path = E.set_active_profile(args.profile, config_path=args.config)
        summary = E.environment_summary(config_path=args.config)
    except (OSError, ValueError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Failed to set profile: {exc}")
        return 1

    if getattr(args, "json", False):
        print(json.dumps({"ok": True, "updated_file": updated_path, **summary}, indent=2))
        return 0
    print(f"Switched active_profile to '{summary['active_profile']}' in {updated_path}.")
    return 0


def register(sub):
    env_p = sub.add_parser(
        "environment",
        help="Show the active environment profile (test | prod), email/Teams targets, and artifact prefixes",
    )
    env_p.add_argument("--release", default=None, help="Optional release ID to resolve @owner preview")
    env_p.add_argument("--json", action="store_true")
    env_p.set_defaults(func=cmd_environment)

    sp_p = sub.add_parser(
        "set-profile",
        help="Switch active_profile in config/environment.yaml (e.g. test -> prod before launch)",
    )
    sp_p.add_argument("--profile", required=True, help="Target profile name in config/environment.yaml (e.g. test or prod)")
    sp_p.add_argument("--json", action="store_true")
    sp_p.set_defaults(func=cmd_set_profile)
