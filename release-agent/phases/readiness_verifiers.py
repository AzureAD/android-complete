"""Readiness AUTO verifiers.

An auto verifier must FULLY verify its item — it returns pass or fail, never a
half-measure. If something cannot be fully proven programmatically, it must NOT
be an auto item (make it an attest item in readiness.yaml instead).

Contract:  verify(item) -> VerifyResult(status, message)
  status: "pass" | "fail"
"""
from __future__ import annotations
from dataclasses import dataclass
import os


@dataclass
class VerifyResult:
    status: str        # "pass" | "fail"
    message: str
    details: list = None   # optional per-check breakdown: [{name, url, ok, detail}]

    @property
    def ok(self) -> bool:
        return self.status == "pass"


def verify_build_defs(item: dict) -> VerifyResult:
    """Confirm the engineer can access every configured ADO build definition,
    using `az pipelines build definition show`. Fully verified access — pass/fail.
    Returns per-check details (name, url, ok) so the display can link each one."""
    from tools.checks import check_ado_build_def

    checks = [c for c in item.get("checks", []) if c.get("type") == "ado_build_def"]
    if not checks:
        return VerifyResult("fail", "no build definitions configured to check", [])
    details, any_fail = [], False
    for c in checks:
        r = check_ado_build_def(c["org"], c["project"], c["id"])
        details.append({"name": c.get("name", str(c["id"])), "url": c.get("url"),
                        "ok": r.ok, "detail": r.detail})
        if not r.ok:
            any_fail = True
    msg = "; ".join(f"{'OK' if d['ok'] else 'FAIL'} {d['name']}" for d in details)
    return VerifyResult("fail" if any_fail else "pass", msg, details)


def verify_mcp_servers(item: dict) -> VerifyResult:
    """Confirm every MCP server the skill needs (ICM, Kusto/ADX) is registered in
    Scout's config. Reuses the infra preflight in READ-ONLY mode (register=False)
    against config/requirements.yaml — the single source of truth for MCP deps.
    Fully verified: pass only if all are `present`, else fail listing the missing
    ones (fix = run bootstrap / `infra --register`, then RESTART Scout)."""
    from orchestrator import infra

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    req_path = os.path.join(root, "config", "requirements.yaml")
    try:
        req = infra.load_requirements(req_path)
    except OSError as e:
        return VerifyResult("fail", f"cannot read requirements.yaml: {e}", [])
    results = infra.ensure_mcp_servers(req, register=False)
    if not results:
        return VerifyResult("fail", "no MCP servers configured to check", [])
    details, missing = [], []
    for r in results:
        ok = r.get("status") == "present"
        details.append({"name": r.get("name", r.get("scout_key")), "url": None,
                        "ok": ok, "detail": r.get("detail", "")})
        if not ok:
            missing.append(r.get("scout_key") or r.get("id"))
    if missing:
        return VerifyResult(
            "fail",
            "not registered: " + ", ".join(missing)
            + " — run bootstrap (or `python -m orchestrator.cli infra`), then RESTART Scout",
            details)
    return VerifyResult(
        "pass", "registered: " + ", ".join(r.get("scout_key", "?") for r in results), details)


REGISTRY = {
    "build_defs": verify_build_defs,
    "mcp_servers": verify_mcp_servers,
}


def get_verifier(verifier_id: str):
    return REGISTRY.get(verifier_id)
