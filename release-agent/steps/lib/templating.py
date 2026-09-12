"""Templating helpers — parse a delimited template file and fill placeholders.

Reused by any step that renders a message from `templates/*.md`. The template
format marks each variant's fields with `===VARIANT:SUBJECT===` / `===VARIANT:BODY===`.
"""
from __future__ import annotations

import os

# release-agent/ root (steps/lib/ -> steps/ -> release-agent/)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def template_path(rel: str) -> str:
    """Absolute path to a template referenced relative to the repo root."""
    return os.path.join(ROOT, rel)


def parse_template(text: str, variant: str):
    """Pull (subject, body) for a variant from the delimited template text, or None.
    Sections are marked '===INITIAL:SUBJECT===' / '===INITIAL:BODY===' etc."""
    key = variant.upper()
    marks = {"subject": f"==={key}:SUBJECT===", "body": f"==={key}:BODY==="}
    out = {}
    for field, mark in marks.items():
        if mark not in text:
            return None
        after = text.split(mark, 1)[1]
        end = after.find("\n===")           # body runs to next '===' marker or EOF
        out[field] = (after[:end] if end != -1 else after).strip("\n")
    return out["subject"].strip(), out["body"]


def load_template(rel_path: str, variant: str):
    """Read a template file (relative to root) and return (subject, body) or an
    {'error': ...} dict on failure — callers decide how to surface it."""
    path = template_path(rel_path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            parsed = parse_template(fh.read(), variant)
    except OSError:
        return {"error": f"template not found: {path}"}
    if not parsed:
        return {"error": f"variant '{variant}' not in template"}
    return parsed


def fill(s: str, ctx: dict) -> str:
    """Replace {key} placeholders from ctx."""
    for k, v in ctx.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def esc(s) -> str:
    """Minimal HTML escaping for email-safe content."""
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th', …"""
    if 10 <= (n % 100) <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"
