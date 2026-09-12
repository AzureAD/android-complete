"""Step knowledge base — reference/help content for answering user questions.

When a user asks a detail question about a step ("what does this do?", "where do
I find the Play Console vitals?", "how do I clear this block?"), the skill pulls
the step's knowledge entry and answers ACCURATELY from it instead of guessing.

Two composable sources, resolved by `get_knowledge(phase, step)`:
  1. config/knowledge.yaml  — central data file, keyed "<phase>.<step>". Covers any
     step (migrated or stub); editable without touching code.
  2. a step MODULE's `KNOWLEDGE` dict — co-located with the step, overlaid ON TOP of
     the yaml (module wins per-field) for step-specific detail.

Returns a plain dict (summary/what/where/how/links/faqs) or None when nothing is
authored yet — so callers can say "no knowledge entry yet" honestly.
"""
from __future__ import annotations
import os

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # release-agent/
_CACHE = {"path": None, "data": None}


def knowledge_path() -> str:
    return os.environ.get("RELEASE_AGENT_KNOWLEDGE") or \
        os.path.join(_ROOT, "config", "knowledge.yaml")


def _load_file(path: str | None = None) -> dict:
    p = path or knowledge_path()
    if _CACHE["path"] == p and _CACHE["data"] is not None:
        return _CACHE["data"]
    data = {}
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        data = {k: v for k, v in doc.items() if isinstance(v, dict)}
    _CACHE["path"], _CACHE["data"] = p, data
    return data


def _module_knowledge(phase_id: str, step_id: str) -> dict:
    """A migrated step module's KNOWLEDGE dict, if it declares one."""
    try:
        import steps
        mod = steps.get_step(phase_id, step_id)
    except Exception:  # noqa: BLE001
        mod = None
    return dict(getattr(mod, "KNOWLEDGE", {}) or {}) if mod else {}


def get_knowledge(phase_id: str, step_id: str, path: str | None = None):
    """Merged knowledge for a step (yaml base + module overlay), or None if none.
    Per-field overlay: a field present in the module replaces the yaml's field."""
    base = dict(_load_file(path).get(f"{phase_id}.{step_id}", {}) or {})
    overlay = _module_knowledge(phase_id, step_id)
    merged = {**base, **overlay}
    return merged or None


def render_knowledge(phase_id: str, step_id: str, k: dict) -> str:
    """Human-readable markdown for a step's knowledge (for `step-info`)."""
    lines = [f"## {phase_id}.{step_id}"]
    if k.get("summary"):
        lines += ["", f"_{k['summary']}_"]
    if k.get("what"):
        lines += ["", "**What it does**", k["what"].strip()]
    if k.get("who"):
        lines += ["", "**Who owns it**", k["who"].strip()]
    if k.get("where"):
        lines += ["", "**Where to look**"] + [f"- {w}" for w in k["where"]]
    if k.get("how"):
        lines += ["", "**How to complete / resolve**", k["how"].strip()]
    if k.get("links"):
        lines += ["", "**Links**"] + [f"- [{l.get('name','link')}]({l['url']})"
                                       for l in k["links"] if l.get("url")]
    if k.get("faqs"):
        lines += ["", "**FAQ**"]
        for f in k["faqs"]:
            lines += [f"- **{f.get('q','')}** — {f.get('a','')}"]
    return "\n".join(lines)
