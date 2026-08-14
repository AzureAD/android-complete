"""Step: `breaking` — detect BREAKING-OneAuth changes + draft comms (Phase 0, S2).

Reads the common-for-android changelog, finds breaking ([MAJOR]) entries in the
unreleased "vNext" section, and drafts the OneAuth comms. Read-only. Deterministic
(HTTP only) → an `agent` step the engine runs in-process; a dry-run simulates.
"""
from __future__ import annotations
from urllib import request as _request

from orchestrator.outcomes import Done, Blocked
from orchestrator.phase_config import load_phase_config
from steps.lib.agent import legacy_run

ID = "breaking"
KIND = "agent"


def _fetch_text(url: str, timeout: int = 20) -> str:
    req = _request.Request(url, headers={"User-Agent": "release-agent-preflight/1.0"})
    with _request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_breaking(changelog_text: str, section: str = "vNext",
                   tag: str = "[MAJOR]") -> list:
    """Return the list of `tag` (breaking) entry lines inside `section`.

    The changelog is a flat text file: a section header line (e.g. "vNext"),
    an underline, then `- [SEVERITY] ... (#PR)` bullets, until the next
    "Version X.Y.Z" header. We scan only the requested section.
    """
    entries, in_section = [], False
    for raw in changelog_text.splitlines():
        s = raw.strip()
        if not in_section:
            if s == section:
                in_section = True
            continue
        if s.startswith("Version "):
            break
        if tag in raw:
            entries.append(s)
    return entries


def _draft_breaking_comms(entries: list, state=None) -> str:
    release = getattr(state, "release_id", None) or "this release"
    bullets = "\n".join(f"- {e}" for e in entries)
    return (
        f"Subject: [Action] Breaking OneAuth changes in {release}\n\n"
        f"Hi OneAuth team,\n\n"
        f"The upcoming Android common release ({release}) contains the following "
        f"breaking change(s). Please review for downstream impact before code "
        f"complete:\n\n{bullets}\n\n"
        f"Thanks,\nRelease Orchestrator"
    )


def build(state):
    cfg = load_phase_config("preflight").get("breaking", {})
    url = cfg.get("changelog_url")
    section = cfg.get("section", "vNext")
    tag = cfg.get("breaking_tag", "[MAJOR]")
    if state.dry_run:
        return Done(f"[dry-run] Would scan the '{section}' changelog section for {tag} "
                    f"(breaking) entries and draft OneAuth comms.")
    if not url:
        return Blocked("breaking: no changelog_url configured")
    try:
        text = _fetch_text(url)
    except Exception as e:  # noqa: BLE001 - network/parse errors -> hold for human
        return Blocked(f"breaking: could not fetch changelog ({e})")
    entries = parse_breaking(text, section, tag)
    if not entries:
        return Done(f"No breaking ({tag}) changes in '{section}' — no OneAuth comms needed.")
    listing = "\n".join(f"  - {e}" for e in entries)
    draft = _draft_breaking_comms(entries, state)
    return Done(
        f"Detected {len(entries)} breaking ({tag}) change(s) in '{section}':\n{listing}\n\n"
        f"--- DRAFT COMMS (send to OneAuth) ---\n{draft}")


run = legacy_run(build)
