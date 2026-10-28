"""Resolve installed tools without relying on a desktop host's stale PATH."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys

WINDOWS = sys.platform == "win32"


def find_azure_cli():
    """Keep PATH precedence; recognize both standard Windows MSI install roots."""
    found = shutil.which("az")
    if found:
        return found
    if not WINDOWS:
        return None
    found = shutil.which("az.cmd")
    if found:
        return found
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "Microsoft SDKs" / "Azure" / "CLI2" / "wbin" / "az.cmd"
            if candidate.is_file():
                return str(candidate)
    return None
