"""Reusable step utilities — shared by any step handler in `steps/`.

Extracted from the old `commands/notice.py` so multiple steps can reuse the same
template + context logic instead of re-implementing it. Add helpers here whenever
two steps would otherwise duplicate logic.
"""
