"""A reviewed, locked Monthly-only amendment; never a clone replay or workflow adoption."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4
import os
import time

from orchestrator import cli_common as C, revision as R
from tools import auth_partition as A, auth_monthly as M, testplans as T

COMMAND = "amend-auth-monthly"
KEY = "bug_bash.clone_plans_auth"
VERSION = 1
RECEIPT = "monthly_amendment"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _persist(state):
    # Retry only Windows receipt replacement interference, never a provider request.
    delays = (0.05, 0.1, 0.2, 0.4)
    for attempt in range(len(delays) + 1):
        try:
            state.checkpoint()
            return
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def base_clone(state):
    base = asdict(state.get_step("bug_bash", "clone_plans_auth"))
    _require(isinstance(base["data"], dict), "Monthly amendment clone data is malformed")
    base["data"].pop(RECEIPT, None)
    _require(base["status"] == "done" and base["completed_at"]
             and base["invalidated_at"] is None and base["execution"] is None,
             "Monthly amendment requires an intact completed clone, not a reopened execution")
    data = base["data"]
    _require(data.get("layout") == A.LAYOUT, "Monthly amendment requires an existing v1 category clone")
    A.target(data)
    creations = data.get("creations", {})
    _require(set(creations) == {"parent", "manual", "automated"},
             "Monthly amendment requires original clone creation ownership")
    for key, field in (("parent", "suite_id"), ("manual", "manual_suite_id"),
                       ("automated", "automated_suite_id")):
        receipt = creations[key]
        _require(receipt.get("status") == "ready" and receipt.get("suite_id") == data[field]
                 and isinstance(receipt.get("operation_key"), str) and receipt["operation_key"],
                 "Monthly amendment clone creation acknowledgement changed")
    _require(len({r["operation_key"] for r in creations.values()}) == 1,
             "Monthly amendment clone execution ownership changed")
    return base


def _identity(state):
    return {key: deepcopy(getattr(state, key)) for key in (
        "release_id", "ccd", "target_month", "owner_email", "workflow_revision")}


def _rc(state):
    rows = state.pipeline_runs.get("rcs")
    _require(isinstance(rows, list) and rows, "Monthly amendment requires captured current RC evidence")
    return rows[-1]


def observe(data, monthly=None, timeout=120):
    """Read only the original categories/configurations and exact Monthly ownership."""
    categories = []
    for key, field, name, query, kind, parent in (
        ("parent", "suite_id", data["suite_name"], None, "staticTestSuite", T.AUTH_ROOT_SUITE),
        ("manual", "manual_suite_id", "Manual Tests", data["queries"]["manual"], "dynamicTestSuite", data["suite_id"]),
        ("automated", "automated_suite_id", "Automated Tests", data["queries"]["automated"], "dynamicTestSuite", data["suite_id"]),
    ):
        options = {"plan_id": T.AUTH_PLAN, "parent_suite_id": parent, "suite_type": kind}
        ok, info, detail = T.validate_auth_suite(data[field], name, query, timeout, **options)
        _require(ok and isinstance(info, dict), "Original Authenticator category changed: " + detail)
        ok, found, detail = T.find_auth_suite(name, query, timeout, **options)
        _require(ok and isinstance(found, dict) and int(found["id"]) == data[field],
                 "Original Authenticator category not unique: " + detail)
        categories.append({"key": key, "id": data[field], "name": name, "query": query,
                           "suite_type": kind, "parent_id": parent, "inherit": True})
    configs = M._configs({"plan_id": T.AUTH_PLAN, "parent_suite_id": data["suite_id"]}, timeout)
    ok, found, detail = T.find_auth_suite(M.NAME, None, timeout, plan_id=T.AUTH_PLAN,
                                         parent_suite_id=data["suite_id"], suite_type="staticTestSuite")
    _require(ok, "Cannot establish Monthly leaf absence/ownership: " + detail)
    acknowledged = (monthly or {}).get("operations", {}).get("suite", {})
    owned = acknowledged.get("id") if acknowledged.get("status") == "created" else None
    _require(found is None and owned is None or isinstance(found, dict) and int(found["id"]) == owned,
             "Foreign or missing Monthly leaf; never adopt by name or repeat an unknown create")
    return {"categories": categories, "configuration_ids": configs}


def make_plan(state, runtime, observed):
    """Pure renderer; offline callers must label supplied observations as captured, not live."""
    base = base_clone(state)
    data = base["data"]
    from tools import pipelines as P
    rc = _rc(state)
    ok, projection, detail = P.project_auth_ui_results(rc)
    _require(ok, detail)
    _require(A.classification(rc, projection) == data["classification"],
             "Monthly amendment original classification is stale")
    source = M.source(rc, state.release_id)
    configs = observed["configuration_ids"]
    _require(isinstance(configs, list) and configs == sorted({A.positive(c) for c in configs}) and configs,
             "Monthly amendment requires proven actual inherited configurations")
    creates = [{"scenario_key": row["key"], "payload": M.case_payload(row, source)}
               for row in source["rows"]]
    payload = {"suite": M.suite_payload(data["suite_id"]) if creates else None, "cases": creates,
               "memberships": [{"workItem": {"id": {"created_scenario_key": row["key"]}},
                                "pointAssignments": [{"configurationId": c} for c in configs]}
                               for row in source["rows"]],
               "results": [], "testers": [], "notifications": [], "pipelines": []}
    plan = {"version": VERSION, "command": COMMAND, "scope": "one Monthly leaf and owned investigations only",
            "identity": _identity(state), "runtime": deepcopy(runtime), "base_clone": base,
            "base_sha256": R.digest(base), "observed": deepcopy(observed),
            "source": source, "payload": payload, "payload_sha256": R.digest(payload)}
    return plan


def _hash(plan):
    return R.digest(plan)


def preview(orch):
    state = orch.state
    _require(not state.halt and not state.cancellation, "Monthly amendment blocked by release halt/cancellation")
    base = base_clone(state)
    saved = base_receipt(state)
    if saved:
        _checked_receipt(saved)
        _require(saved["status"] != "cancelled", "Monthly amendment was cancelled; retain its history")
    runtime = R.monthly_amendment_runtime(orch)
    plan = make_plan(state, runtime, observe(base["data"], (saved or {}).get("monthly")))
    if saved:
        _require(saved["plan"] == plan and saved["review_hash"] == _hash(plan),
                 "Monthly amendment source/configuration/base/runtime drift; original receipt retained")
    return {"plan": plan, "review_hash": _hash(plan), "payload_sha256": plan["payload_sha256"],
            "status": saved["status"] if saved else "preview", "read_only": True}


def base_receipt(state):
    data = state.steps.get(KEY, {}).get("data", {})
    if RECEIPT not in data:
        return None
    value = data[RECEIPT]
    _require(isinstance(value, dict) and value, "Malformed Monthly amendment receipt; retain its history")
    return value


def _seal(receipt):
    return R.digest({key: receipt[key] for key in (
        "version", "command", "id", "owner", "approved_by", "approved_at", "review_hash",
        "plan", "monthly", "completed_at")})


def _checked_receipt(receipt):
    _require(isinstance(receipt, dict) and set(receipt) <= {
        "version", "command", "id", "owner", "approved_by", "approved_at", "review_hash",
        "plan", "status", "monthly", "completed_at", "proof_sha256", "cancellation"}
             and type(receipt.get("version")) is int and receipt.get("version") == VERSION
             and receipt.get("command") == COMMAND
             and receipt.get("status") in ("claimed", "incomplete", "complete", "cancelled"),
             "Malformed Monthly amendment receipt")
    _require(all(isinstance(receipt.get(k), str) and receipt[k].strip() for k in (
        "id", "owner", "approved_by", "approved_at")), "Monthly amendment missing reviewed claim")
    _require(isinstance(receipt.get("monthly"), dict), "Monthly amendment ownership ledger is malformed")
    plan = receipt.get("plan")
    _require(isinstance(plan, dict) and set(plan) == {
        "version", "command", "scope", "identity", "runtime", "base_clone", "base_sha256",
        "observed", "source", "payload", "payload_sha256"}
             and plan.get("command") == COMMAND and type(plan.get("version")) is int
             and plan.get("version") == VERSION
             and receipt.get("review_hash") == _hash(plan)
             and plan.get("base_sha256") == R.digest(plan.get("base_clone"))
             and plan.get("payload_sha256") == R.digest(plan.get("payload")),
             "Monthly amendment frozen review changed")
    if receipt["status"] == "complete":
        _require(isinstance(receipt.get("completed_at"), str) and receipt["completed_at"].strip(),
                 "Monthly amendment completion acknowledgement missing")
        _require(receipt.get("proof_sha256") == _seal(receipt), "Monthly amendment completion proof changed")
        monthly = M.checked(receipt.get("monthly"))
        _require(monthly["source"] == plan["source"]
                 and monthly["parent_suite_id"] == plan["base_clone"]["data"]["suite_id"],
                 "Monthly amendment acknowledged target/source changed")
        _require({b["config_id"] for b in monthly["bindings"]} == (
            set(plan["observed"]["configuration_ids"]) if monthly["source"]["rows"] else set()),
            "Monthly amendment configuration receipt changed")
    return receipt


def completed(data):
    receipt = _checked_receipt(data.get(RECEIPT))
    _require(receipt["status"] == "complete", "Monthly amendment incomplete/cancelled; hold P3S3")
    base = deepcopy(data)
    base.pop(RECEIPT)
    _require(base == receipt["plan"]["base_clone"]["data"], "Monthly amendment original clone data changed")
    return receipt


def _locked(orch):
    scope = C._LOCKED_STATE.get()
    _require(scope is not None and getattr(orch.state, "_checkpoint", None) is not None
             and getattr(orch.state, "_loaded_from_disk", False)
             and scope[0] == os.path.abspath(C.state_path(orch._monthly_runs_root, orch.state.release_id)),
             "Monthly amendment requires its exact locked, persisted release transaction")


def execute(orch, *, review_hash, approved_by, executor, reserve=False):
    _locked(orch)
    _require(all(isinstance(x, str) and x.strip() for x in (review_hash, approved_by, executor)),
             "Monthly amendment requires exact review hash, approved-by and executor")
    reviewed = preview(orch)
    _require(review_hash == reviewed["review_hash"], "Stale Monthly amendment review hash")
    state = orch.state
    receipt = base_receipt(state)
    if receipt:
        _checked_receipt(receipt)
        _require(receipt["owner"] == executor and receipt["approved_by"] == approved_by
                 and receipt["review_hash"] == review_hash, "Monthly amendment claim/reviewer mismatch")
    else:
        _require(reserve, "Monthly amendment requires --reserve for its independent claim")
        _require(not any(s.get("execution") is not None for s in state.steps.values()),
                 "Monthly amendment cannot overlap another owned execution")
        receipt = {"version": VERSION, "command": COMMAND, "id": uuid4().hex, "owner": executor,
                   "approved_by": approved_by, "approved_at": datetime.now(timezone.utc).isoformat(),
                   "review_hash": review_hash, "plan": deepcopy(reviewed["plan"]),
                   "status": "claimed", "monthly": {}}
        state.steps[KEY]["data"][RECEIPT] = receipt
        _persist(state)

    def fence():
        _locked(orch)
        _checked_receipt(receipt)
        _require(receipt["owner"] == executor and receipt["approved_by"] == approved_by
                 and receipt["review_hash"] == review_hash, "Monthly amendment claim/reviewer changed")
        _require(not state.halt and not state.cancellation, "Monthly amendment halted/cancelled")
        _require(not any(s.get("execution") is not None for s in state.steps.values()),
                 "Monthly amendment cannot overlap another owned execution")
        _require(base_receipt(state) is receipt and receipt["status"] != "cancelled",
                 "Monthly amendment lost its independent claim")
        _require(_identity(state) == receipt["plan"]["identity"]
                 and base_clone(state) == receipt["plan"]["base_clone"]
                 and R.monthly_amendment_runtime(orch) == receipt["plan"]["runtime"]
                 and M.source(_rc(state), state.release_id) == receipt["plan"]["source"],
                 "Monthly amendment base/source/runtime changed; no further writes")
        _require(observe(receipt["plan"]["base_clone"]["data"], receipt["monthly"])
                 == receipt["plan"]["observed"], "Monthly amendment live target/configuration changed")

    def checkpoint(value):
        # Persist ACKs even when a subsequent live fence fails.
        receipt["monthly"] = deepcopy(value)
        _persist(state)
        fence()

    fence()
    if receipt["status"] == "complete":
        M.validate_live(receipt["monthly"])
        return deepcopy(receipt)
    from orchestrator.authority import WriteCapabilities, WriteOperation
    from orchestrator.service_adapters import production_effects
    effects = production_effects(WriteCapabilities((WriteOperation.AMEND_AUTH_MONTHLY,)),
                                 validate=fence, committer=None, clock=None)
    receipt["status"] = "incomplete"
    _persist(state)
    ok, value, detail = effects.amend_auth_monthly(
        receipt["plan"]["source"], receipt["plan"]["base_clone"]["data"]["suite_id"],
        deepcopy(receipt["monthly"]), checkpoint)
    _require(ok, "Monthly amendment incomplete (retain intent/ACKs): " + detail)
    fence()
    receipt.update(monthly=deepcopy(value), status="complete",
                   completed_at=datetime.now(timezone.utc).isoformat())
    receipt["proof_sha256"] = _seal(receipt)
    _checked_receipt(receipt)
    _persist(state)
    return deepcopy(receipt)


def cancel(orch, *, review_hash, approved_by, executor, reason):
    _locked(orch)
    receipt = _checked_receipt(base_receipt(orch.state))
    _require(receipt["status"] in ("claimed", "incomplete") and receipt["owner"] == executor
             and receipt["approved_by"] == approved_by and receipt["review_hash"] == review_hash
             and isinstance(reason, str) and reason.strip(), "Cancellation requires the original claim and reason")
    receipt.update(status="cancelled", cancellation={"reason": reason,
                   "at": datetime.now(timezone.utc).isoformat()})
    _persist(orch.state)
    return deepcopy(receipt)
