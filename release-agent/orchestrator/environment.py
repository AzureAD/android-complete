"""Environment & target configuration loader (`config/environment.yaml`).

Single tracked configuration file defining `test` (safe pre-launch default) and
`prod` (live launch) profiles for:
  - Outbound email distribution lists (`partner_status`, `bug_bash_invite`, `subject_prefix`)
  - Outbound Teams chats, channels, DMs, and CC mention behavior
  - Created artifact naming prefixes (`broker_plan_prefix`, `auth_suite_prefix`, `wiki_page_prefix`)
  - Optional per-step configuration defaults (`step_defaults`) for pre-launch rehearsals

Precedence for active profile:
  1. `RELEASE_AGENT_PROFILE` env var (when set, e.g. CI/pytest isolation)
  2. `active_profile` in `config/environment.local.yaml` (optional gitignored local overlay)
  3. `active_profile` in `config/environment.yaml`
"""
from __future__ import annotations

from copy import deepcopy
import os
import re
from typing import Any

import yaml

from orchestrator.outcomes import Done, Blocked

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SELF_CHAT_ID = "48:notes"
_DONE_WORDS = {"done", "pass", "ok", "complete"}
_BLOCK_WORDS = {"blocked", "block", "attention", "hold", "fail"}


def environment_path(config_path: str | None = None) -> str:
    """Path to `config/environment.yaml` (or `RELEASE_AGENT_ENVIRONMENT_FILE` override)."""
    env_override = os.environ.get("RELEASE_AGENT_ENVIRONMENT_FILE")
    if env_override:
        return env_override
    if config_path:
        candidate = os.path.join(os.path.dirname(os.path.abspath(config_path)), "environment.yaml")
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(_ROOT, "config", "environment.yaml")


def local_environment_path(base_path: str | None = None) -> str:
    """Optional gitignored `config/environment.local.yaml` beside `environment.yaml`."""
    env_override = os.environ.get("RELEASE_AGENT_LOCAL_ENVIRONMENT_FILE")
    if env_override is not None:
        return env_override
    bp = base_path or environment_path()
    stem, ext = os.path.splitext(bp)
    return f"{stem}.local{ext or '.yaml'}"


def _deep_merge(base: dict, overlay: dict) -> dict:
    merged = deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = deepcopy(v)
    return merged


_ENV_FILE_CACHE: dict[str, tuple[tuple[int, int] | None, dict[str, Any]]] = {}


def _load_yaml_cached(path: str) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    st = os.stat(path)
    stamp = (st.st_mtime_ns, st.st_size)
    cached = _ENV_FILE_CACHE.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    with open(path, encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: root must be a YAML mapping")
    _ENV_FILE_CACHE[path] = (stamp, loaded)
    return loaded


def load_raw_environment(config_path: str | None = None) -> dict[str, Any]:
    """Load `config/environment.yaml` merged with optional `config/environment.local.yaml`."""
    base_file = environment_path(config_path)
    doc = deepcopy(_load_yaml_cached(base_file))
    local_file = local_environment_path(base_file)
    if local_file and os.path.isfile(local_file):
        local_doc = _load_yaml_cached(local_file)
        doc = _deep_merge(doc, local_doc)
    return doc


def active_profile_name(config_path: str | None = None, doc: dict | None = None) -> str:
    """Return the active profile name (e.g. `'test'` or `'prod'`)."""
    forced = (os.environ.get("RELEASE_AGENT_PROFILE") or "").strip()
    if forced and doc is None:
        return forced
    raw = doc if doc is not None else load_raw_environment(config_path)
    name = forced or str(raw.get("active_profile") or "prod").strip()
    profiles = raw.get("profiles") or {}
    if profiles and name not in profiles:
        raise ValueError(
            f"Unknown environment profile {name!r}; expected one of: {', '.join(sorted(profiles))}"
        )
    return name


def load_profile(config_path: str | None = None, profile: str | None = None) -> tuple[str, dict[str, Any]]:
    """Return `(profile_name, profile_dict)` for the active (or requested) profile."""
    raw = load_raw_environment(config_path)
    name = (profile or "").strip() or active_profile_name(config_path, raw)
    profiles = raw.get("profiles") or {}
    if profiles and name not in profiles:
        raise ValueError(
            f"Unknown environment profile {name!r}; expected one of: {', '.join(sorted(profiles))}"
        )
    selected = deepcopy(profiles.get(name) or {})
    if not isinstance(selected, dict):
        raise ValueError(f"Profile {name!r} in environment.yaml must be a mapping")
    return name, selected


def set_active_profile(new_profile: str, config_path: str | None = None) -> str:
    """Update `active_profile:` in `config/environment.yaml` while preserving comments & formatting."""
    target_name = str(new_profile or "").strip()
    path = environment_path(config_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Environment file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    parsed = yaml.safe_load(text) or {}
    profiles = (parsed.get("profiles") if isinstance(parsed, dict) else None) or {}
    if target_name not in profiles:
        raise ValueError(
            f"Unknown profile {target_name!r}; available profiles: {', '.join(sorted(profiles))}"
        )
    updated, count = re.subn(
        r"(?m)^(\s*active_profile\s*:\s*)([^\s#]+)(.*)$",
        rf"\g<1>{target_name}\g<3>",
        text,
        count=1,
    )
    if count == 0:
        updated = f"active_profile: {target_name}\n" + text
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return path


def normalize_email_list(val: Any, owner_email: str | None = None) -> list[str]:
    """Expand a string or list of email addresses, resolving `'@owner'` / `'me'` / `'self'` to `owner_email`."""
    if not val:
        return []
    raw_items = (
        [str(x).strip() for x in val if str(x).strip()]
        if isinstance(val, (list, tuple, set))
        else [x.strip() for x in str(val).split(",") if x.strip()]
    )
    out: list[str] = []
    for item in raw_items:
        if item.lower() in ("@owner", "owner", "me", "self"):
            resolved = (owner_email or "").strip()
            if not resolved or "@" not in resolved:
                raise ValueError(
                    "Active environment profile routes email to '@owner', "
                    "but release owner_email is not set (run `set-owner` first)."
                )
            if resolved not in out:
                out.append(resolved)
        elif item not in out:
            out.append(item)
    return out


def normalize_chat_target(val: Any, self_chat_id: str = SELF_CHAT_ID) -> str:
    """Resolve `'self'` / `'me'` aliases to the signed-in user's Teams self-chat (`48:notes`)."""
    if isinstance(val, dict):
        return str(val.get("chat") or val.get("channel") or "").strip()
    s = str(val or "").strip()
    if s.lower() in ("self", "me"):
        return self_chat_id
    return s


def email_subject_prefix(config_path: str | None = None, profile: str | None = None) -> str:
    _, prof = load_profile(config_path, profile)
    emails = prof.get("emails") or {}
    return str(emails.get("subject_prefix") or "")


def resolve_email_targets(
    group_key: str,
    *,
    owner_email: str | None = None,
    override: Any = None,
    config_path: str | None = None,
    profile: str | None = None,
) -> tuple[list[str], str, str]:
    """Resolve `(recipients, subject_prefix, label)` for `'partner_status'` or `'bug_bash_invite'`."""
    prof_name, prof = load_profile(config_path, profile)
    emails_cfg = prof.get("emails") or {}
    if override is not None:
        recipients = normalize_email_list(override, owner_email=owner_email)
        prefix = str(emails_cfg.get("subject_prefix") or "[TEST → me] ")
        return recipients, prefix, f"{', '.join(recipients)} (step override)"
    configured = emails_cfg.get(group_key)
    if not isinstance(configured, list) or not configured:
        raise ValueError(
            f"Configure profiles.{prof_name}.emails.{group_key} as a non-empty list in environment.yaml"
        )
    recipients = normalize_email_list(configured, owner_email=owner_email)
    prefix = str(emails_cfg.get("subject_prefix") or "")
    if prefix and not prefix.endswith(" "):
        prefix = prefix + " "
    label = (
        f"{', '.join(recipients)} ({prof_name} profile)"
        if prof_name != "prod"
        else "recipients"
    )
    return recipients, prefix, label


def prod_teams_entry(team_key: str, config_path: str | None = None) -> dict[str, str]:
    """Return the canonical production Teams target dict (for display labels / readiness checks)."""
    raw = load_raw_environment(config_path)
    prod_teams = ((raw.get("profiles") or {}).get("prod") or {}).get("teams") or {}
    entry = prod_teams.get(team_key)
    if isinstance(entry, dict):
        return {k: str(v) for k, v in entry.items()}
    from tools.coordinates import coords
    return coords.team(team_key)


def resolve_teams_chat(
    team_key: str,
    *,
    override: Any = None,
    config_path: str | None = None,
    profile: str | None = None,
) -> tuple[str, str, bool]:
    """Resolve `(chat_id, target_label, is_self_chat)` for `'android_core'` or `'code_reviews'`."""
    prod_info = prod_teams_entry(team_key, config_path)
    prod_name = prod_info.get("name") or team_key
    if override is not None:
        cid = normalize_chat_target(override)
        is_self = cid == SELF_CHAT_ID
        lbl = f"self-chat (intended: {prod_name})" if is_self else cid
        return cid, lbl, is_self

    prof_name, prof = load_profile(config_path, profile)
    entry = (prof.get("teams") or {}).get(team_key)
    if entry is None:
        entry = prod_info
    if isinstance(entry, dict):
        cid = str(entry.get("chat") or "").strip()
        name = str(entry.get("name") or prod_name).strip()
        return cid, name, (cid == SELF_CHAT_ID)
    cid = normalize_chat_target(entry)
    is_self = cid == SELF_CHAT_ID
    lbl = f"self-chat ({prof_name} profile · intended: {prod_name})" if is_self else cid
    return cid, lbl, is_self


def resolve_general_announce(
    *,
    override_chat: Any = None,
    override_channel: Any = None,
    config_path: str | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Resolve target for `finalize.release_announcement`.

    Returns either:
      `{"mode": "chat", "chat_id": ..., "label": ..., "intended": "General"}` (in `test` / `self`), or
      `{"mode": "channel", "team_id": ..., "channel_id": ..., "label": ...}` (in `prod`).
    """
    prod_info = prod_teams_entry("general_announce", config_path)
    prod_name = prod_info.get("name", "General")

    if isinstance(override_channel, dict) and override_channel.get("channelId"):
        return {
            "mode": "channel",
            "team_id": str(override_channel.get("teamId") or prod_info["team"]),
            "channel_id": str(override_channel["channelId"]),
            "label": "a test channel",
            "is_redirect": True,
        }
    if override_chat is not None:
        cid = normalize_chat_target(override_chat)
        return {
            "mode": "chat",
            "chat_id": cid,
            "label": "self-chat" if cid == SELF_CHAT_ID else cid,
            "intended": prod_name,
            "is_redirect": True,
        }

    prof_name, prof = load_profile(config_path, profile)
    entry = (prof.get("teams") or {}).get("general_announce")
    if entry is None:
        entry = prod_info
    if isinstance(entry, dict) and entry.get("channel"):
        return {
            "mode": "channel",
            "team_id": str(entry.get("team") or prod_info["team"]),
            "channel_id": str(entry["channel"]),
            "label": f"the '{entry.get('name', prod_name)}' channel",
            "is_redirect": False,
        }
    cid = normalize_chat_target(entry)
    return {
        "mode": "chat",
        "chat_id": cid,
        "label": f"self-chat ({prof_name} profile)" if cid == SELF_CHAT_ID else cid,
        "intended": prod_name,
        "is_redirect": True,
    }


def announcement_cc_mode(
    *,
    explicit_mode: Any = None,
    is_redirect: bool = False,
    config_path: str | None = None,
    profile: str | None = None,
) -> str:
    if explicit_mode:
        return str(explicit_mode).strip().lower()
    if is_redirect:
        return "off"
    _, prof = load_profile(config_path, profile)
    configured = str((prof.get("teams") or {}).get("announcement_cc_mode") or "").strip().lower()
    return configured if configured in ("mention", "self", "off") else "mention"


def resolve_bugbash_chat(
    bound_chat_id: str,
    *,
    override: Any = None,
    config_path: str | None = None,
    profile: str | None = None,
) -> tuple[str, bool]:
    """Return `(effective_chat_id, is_self_chat)` for `bug_bash.bugbash_updates`."""
    if override is not None:
        cid = normalize_chat_target(override)
        return cid, (cid == SELF_CHAT_ID)
    _, prof = load_profile(config_path, profile)
    mode = str((prof.get("teams") or {}).get("bug_bash_updates") or "bound_meeting").strip()
    if mode.lower() in ("bound_meeting", "meeting", ""):
        return bound_chat_id, False
    cid = normalize_chat_target(mode)
    return cid, (cid == SELF_CHAT_ID)


def resolve_native_auth_dm(
    engineer_upn: str,
    *,
    owner_email: str | None = None,
    override: Any = None,
    config_path: str | None = None,
    profile: str | None = None,
) -> tuple[str, bool]:
    """Return `(effective_upn, is_redirected)` for `bug_bash.notify_native_auth`."""
    raw_target = override
    if raw_target is None:
        _, prof = load_profile(config_path, profile)
        mode = str((prof.get("teams") or {}).get("native_auth_dm") or "resolved_engineer").strip()
        if mode.lower() not in ("resolved_engineer", "engineer", ""):
            raw_target = mode
    if raw_target is None:
        return engineer_upn, False
    s_val = str(raw_target).strip()
    if s_val.lower() in ("self", "me", "@owner", SELF_CHAT_ID):
        if not owner_email:
            raise ValueError(
                "Active environment profile routes Native Auth DM to 'self', "
                "but release owner_email is not set."
            )
        return owner_email, True
    return s_val, (s_val.casefold() != engineer_upn.casefold())


def artifact_prefix(
    artifact_key: str,
    *,
    config_path: str | None = None,
    profile: str | None = None,
) -> str:
    """Return configured artifact name prefix (`broker_plan_prefix`, `auth_suite_prefix`, `wiki_page_prefix`)."""
    _, prof = load_profile(config_path, profile)
    artifacts = prof.get("artifacts") or {}
    return str(artifacts.get(artifact_key) or "")


def load_step_defaults(config_path: str | None = None, profile: str | None = None) -> dict[str, dict[str, Any]]:
    """Return `step_defaults` map (`<phase>.<step>` -> dict) from the active environment profile."""
    _, prof = load_profile(config_path, profile)
    raw = prof.get("step_defaults") or {}
    if not isinstance(raw, dict):
        return {}
    return {k: dict(v) for k, v in raw.items() if isinstance(v, dict)}


def step_config(overrides: dict | None, phase_id: str, step_id: str) -> dict[str, Any]:
    """Return merged configuration inputs for `<phase>.<step>` from `overrides`."""
    if not isinstance(overrides, dict):
        return {}
    spec = overrides.get(f"{phase_id}.{step_id}")
    return dict(spec) if isinstance(spec, dict) else {}


def outcome_override(overrides: dict | None, phase_id: str, step_id: str) -> Done | Blocked | None:
    """Support `step_defaults` outcome short-circuiting (`outcome: done | blocked`)."""
    spec = step_config(overrides, phase_id, step_id)
    if not spec or "outcome" not in spec:
        return None
    outcome = str(spec.get("outcome", "")).strip().lower()
    if outcome in _DONE_WORDS:
        note = spec.get("note") or f"{phase_id}.{step_id} marked complete via step override"
        return Done(note=str(note), by="config")
    if outcome in _BLOCK_WORDS:
        reason = spec.get("reason") or spec.get("note") or f"{phase_id}.{step_id} held via step override"
        return Blocked(reason=str(reason), by="config")
    return None


def readiness_override(overrides: dict | None, item_id: str) -> tuple[str, str] | None:
    """Support `step_defaults` (`readiness.<item>`) overrides in offline tests."""
    if not isinstance(overrides, dict):
        return None
    spec = overrides.get(f"readiness.{item_id}")
    if not isinstance(spec, dict) or "outcome" not in spec:
        return None
    outcome = str(spec.get("outcome", "")).strip().lower()
    detail = spec.get("detail") or spec.get("note") or spec.get("message")
    if outcome in _DONE_WORDS:
        return "pass", str(detail or f"readiness.{item_id} passed via override")
    if outcome in _BLOCK_WORDS or outcome == "fail":
        return "fail", str(detail or f"readiness.{item_id} failed via override")
    return None


def environment_summary(owner_email: str | None = None, config_path: str | None = None) -> dict[str, Any]:
    """Build a structured summary of the active environment profile for CLI & status display."""
    prof_name, prof = load_profile(config_path)
    emails = prof.get("emails") or {}
    teams = prof.get("teams") or {}
    artifacts = prof.get("artifacts") or {}
    step_defs = prof.get("step_defaults") or {}

    def _preview_emails(key: str) -> list[str]:
        raw_list = emails.get(key) or []
        if owner_email:
            try:
                return normalize_email_list(raw_list, owner_email=owner_email)
            except ValueError:
                pass
        return [str(x) for x in raw_list]

    return {
        "active_profile": prof_name,
        "is_production": prof_name == "prod",
        "environment_file": environment_path(config_path),
        "local_override_file": (
            local_environment_path(environment_path(config_path))
            if os.path.isfile(local_environment_path(environment_path(config_path)))
            else None
        ),
        "emails": {
            "subject_prefix": str(emails.get("subject_prefix") or ""),
            "partner_status": _preview_emails("partner_status"),
            "bug_bash_invite": _preview_emails("bug_bash_invite"),
        },
        "teams": deepcopy(teams),
        "artifacts": deepcopy(artifacts),
        "step_defaults_keys": sorted(step_defs.keys()) if isinstance(step_defs, dict) else [],
    }
