"""Durable three-suite lifecycle owned by clone_plans_auth."""
from orchestrator.evidence import StepData, RetryDecision
from orchestrator.outcomes import Blocked, Done
from orchestrator.step_context import thaw
from tools import auth_partition as A, testplans as T, pipelines as P
from steps.build_verify._common import latest_rc, valid_id

STEP_ID = "clone_plans_auth"
FROZEN_FIELDS = {"release", "org", "project", "plan_id", "parent_suite_id", "name",
                 "layout", "classification", "queries"}


def validate(frozen, context):
    monthly = isinstance(frozen, dict) and frozen.get("layout") == A.MONTHLY_LAYOUT
    if (not isinstance(frozen, dict) or set(frozen) != FROZEN_FIELDS | ({"monthly_source"} if monthly else set())
            or frozen.get("layout") not in (A.LAYOUT, A.MONTHLY_LAYOUT) or frozen.get("org") != T.ORG
            or frozen.get("project") != T.PROJECT or frozen.get("plan_id") != T.AUTH_PLAN
            or frozen.get("parent_suite_id") != T.AUTH_ROOT_SUITE
            or type(frozen.get("plan_id")) is not int or type(frozen.get("parent_suite_id")) is not int
            or frozen.get("release") != context.release.release_id
            or not isinstance(frozen.get("name"), str) or not frozen["name"].strip()
            or frozen["name"] != frozen["name"].strip()):
        raise ValueError("Unknown/malformed Authenticator category execution")
    source = A.validate_classification(frozen["classification"])
    if frozen["queries"] != A.queries(source):
        raise ValueError("Frozen Authenticator partition queries changed")
    ok, projection, detail = P.project_auth_ui_results(latest_rc(context))
    if not ok:
        raise ValueError(detail)
    if A.classification(latest_rc(context), projection) != source:
        raise ValueError("Authenticator RC/build/mapped-case classification changed; owner recovery required")
    if monthly:
        from tools import auth_monthly as M
        M.validate_source(frozen["monthly_source"])
        if M.source(latest_rc(context), frozen["release"]) != frozen["monthly_source"]:
            raise ValueError("Monthly frozen source changed; no clone writes attempted")


def prepare(context, identity, *, layout=A.LAYOUT):
    data = context.evidence.step("bug_bash", STEP_ID).data or {}
    if layout not in (A.LAYOUT, A.MONTHLY_LAYOUT):
        return Blocked("Unknown Authenticator layout")
    if data and data.get("layout") != layout:
        return Blocked("Existing Authenticator layout cannot be amended by replay; use a new reviewed v2 setup")
    ok, projection, detail = P.project_auth_ui_results(latest_rc(context))
    if not ok:
        return Blocked("clone_plans_auth: " + detail)
    source = A.classification(latest_rc(context), projection)
    result = {**identity, "layout": layout, "classification": source, "queries": A.queries(source)}
    if layout == A.MONTHLY_LAYOUT:
        from tools import auth_monthly as M
        try:
            result["monthly_source"] = M.source(latest_rc(context), identity["release"])
        except ValueError as exc:
            return Blocked(str(exc))
    return result


def specifications(frozen, data):
    yield "parent", "suite_id", frozen["name"], None, "staticTestSuite", T.AUTH_ROOT_SUITE
    if valid_id(data.get("suite_id")):
        for key, field, name in (("manual", "manual_suite_id", "Manual Tests"),
                                 ("automated", "automated_suite_id", "Automated Tests")):
            yield key, field, name, frozen["queries"][key], "dynamicTestSuite", data["suite_id"]


def _options(frozen, suite_type, parent):
    return {"org": frozen["org"], "project": frozen["project"], "plan_id": frozen["plan_id"],
            "parent_suite_id": parent, "suite_type": suite_type}


def _record(frozen, data):
    if data:
        if not A.is_partition(data):
            raise ValueError("Legacy Authenticator suites cannot migrate to categories")
        if (any(data.get(key) != frozen[key] for key in
                ("layout", "classification", "queries", "plan_id"))
                or data.get("suite_name") != frozen["name"] or "creations" not in data):
            raise ValueError("Recorded Authenticator category source/name changed")
    data.update({key: frozen[key] for key in ("layout", "classification", "queries", "plan_id")})
    if frozen["layout"] == A.MONTHLY_LAYOUT:
        if data.get("monthly_source", frozen["monthly_source"]) != frozen["monthly_source"]:
            raise ValueError("Recorded Monthly frozen source changed")
        data["monthly_source"] = frozen["monthly_source"]
    data["suite_name"] = frozen["name"]
    receipts = data.setdefault("creations", {})
    if not isinstance(receipts, dict) or set(receipts) - {"parent", "manual", "automated"}:
        raise ValueError("Malformed Authenticator category creation receipts")
    for key, field in (("parent", "suite_id"), ("manual", "manual_suite_id"),
                       ("automated", "automated_suite_id")):
        receipt = receipts.get(key)
        if receipt is None:
            if field in data:
                raise ValueError("Authenticator acknowledged id lost its creation receipt")
            continue
        if (not isinstance(receipt, dict) or receipt.get("status") not in
                ("creating", "created", "ready", "retry_authorized")
                or not isinstance(receipt.get("operation_key"), str)
                or not receipt["operation_key"].strip()):
            raise ValueError("Invalid Authenticator category creation receipt")
        acknowledged = receipt["status"] in ("created", "ready")
        if acknowledged:
            if (type(receipt.get("suite_id")) is not int or receipt["suite_id"] <= 0
                    or data.get(field) != receipt["suite_id"]):
                raise ValueError("Authenticator creation acknowledgement changed")
        elif field in data or "suite_id" in receipt:
            raise ValueError("Uncertain Authenticator intent contains conflicting acknowledged identity")
        if "attempt_history" in receipt and not isinstance(receipt["attempt_history"], list):
            raise ValueError("Malformed Authenticator retry provenance")
    ids = [A.positive(data[field]) for field in ("suite_id", "manual_suite_id", "automated_suite_id")
           if field in data]
    if len(set(ids + [T.AUTH_ROOT_SUITE])) != len(ids) + 1:
        raise ValueError("Conflicting Authenticator category ids")
    if ("manual_suite_id" in data or "automated_suite_id" in data) and "suite_id" not in data:
        raise ValueError("Authenticator category parent missing")
    return data


def build(context, frozen):
    try:
        validate(frozen, context)
        data = _record(frozen, thaw(context.recovery().step("bug_bash", STEP_ID).data or {}))
        receipts = data["creations"]
        for key, field, name, query, suite_type, parent in specifications(frozen, data):
            receipt = receipts.get(key)
            options = _options(frozen, suite_type, parent)
            stored = data.get(field)
            if stored is not None:
                if not receipt or receipt.get("suite_id") != stored:
                    raise ValueError("Authenticator acknowledged id lost its creation receipt")
                ok, info, detail = context.services.testplans.validate_auth_suite(
                    stored, name, query, **options)
                if not ok or not isinstance(info, dict) or str(info.get("id")) != str(stored):
                    raise ValueError(f"Recorded {key} suite failed validation: {detail}")
            ok, found, detail = context.services.testplans.find_auth_suite(name, query, **options)
            if not ok or found is not None and (
                    not isinstance(found, dict) or not valid_id(found.get("id"))):
                raise ValueError(f"Cannot establish unique {key} category: {detail}")
            if stored is not None:
                if not found or int(found["id"]) != stored:
                    raise ValueError(f"Recorded {key} suite not uniquely discoverable")
            elif found:
                if not receipt or receipt["status"] != "creating":
                    raise ValueError(f"Unowned same-named {key} suite; explicit owner recovery required")
                stored = int(found["id"])
                data[field] = stored
                receipts[key] = {**receipt, "suite_id": stored, "status": "created"}
                context.effect.commit.commit(StepData(data))
            else:
                if receipt and receipt["status"] != "retry_authorized":
                    raise ValueError(f"{key} creation may have started; do not POST again")
                operation = context.effect.execution.get("operation_key") or context.effect.execution.get("id")
                if not isinstance(operation, str) or not operation:
                    raise ValueError("Missing Authenticator execution operation identity")
                receipts[key] = {**(receipt or {}), "operation_key": operation, "status": "creating"}
                context.effect.commit.commit(StepData(data))
                ok, sid, detail = context.effect.services.create_auth_suite(name, query, **options)
                if not ok:
                    if T.auth_suite_create_definitely_absent(detail):
                        receipts[key].update(status="retry_authorized", last_failure=detail)
                        context.effect.commit.commit(StepData(data))
                    raise ValueError(f"Could not create {key} suite: {detail}")
                if not valid_id(sid):
                    raise ValueError(f"{key} create returned no valid id; owner recovery required")
                stored = int(sid)
                data[field] = stored
                receipts[key].update(status="created", suite_id=stored)
                context.effect.commit.commit(StepData(data))
                if stored in [data.get(other) for other in ("suite_id", "manual_suite_id", "automated_suite_id")
                              if other != field] or stored == T.AUTH_ROOT_SUITE:
                    raise ValueError("Create returned conflicting Authenticator category id")
                ok, info, detail = context.services.testplans.validate_auth_suite(
                    stored, name, query, **options)
                if not ok or not isinstance(info, dict) or str(info.get("id")) != str(stored):
                    raise ValueError(f"Created {key} suite failed validation: {detail}")
                ok, found, detail = context.services.testplans.find_auth_suite(name, query, **options)
                if not ok or not isinstance(found, dict) or int(found.get("id", 0)) != stored:
                    raise ValueError(f"Created {key} suite not uniquely discoverable: {detail}")
            receipts[key].update(status="ready", suite_id=stored)
            context.effect.commit.commit(StepData(data))
        monthly_links = []
        if frozen["layout"] == A.MONTHLY_LAYOUT:
            from tools import auth_monthly as M
            ok, monthly, detail = context.effect.services.ensure_auth_monthly(
                frozen["monthly_source"], data["suite_id"], data.get("monthly", {}))
            if not ok:
                raise ValueError("Monthly tracker incomplete: " + detail)
            data["monthly"] = monthly
            context.effect.commit.commit(StepData(data))
            monthly_links = M.links(monthly)
        A.target(data)
        return Done(f"Authenticator release categories ready: {frozen['name']}.", links=[
            {"name": name, "url": T.plan_web_url(T.AUTH_PLAN, data[field])}
            for name, field in (("Authenticator release", "suite_id"), ("Manual Tests", "manual_suite_id"),
                                ("Automated Tests", "automated_suite_id"))] + monthly_links)
    except (ValueError, TypeError, KeyError) as exc:
        return Blocked(f"clone_plans_auth: {exc}")


def authorize_retry(context, frozen, reason):
    try:
        validate(frozen, context)
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("Suite retry requires explicit owner-reviewed evidence")
        data = thaw(context.evidence.step("bug_bash", STEP_ID).data)
        if not A.is_partition(data):
            raise ValueError("Category retry missing its recorded layout")
        data = _record(frozen, data)
        for key, field, name, query, suite_type, parent in specifications(frozen, data):
            receipt = data.get("creations", {}).get(key)
            if field in data or not receipt:
                continue
            if receipt.get("status") != "creating":
                raise ValueError("Category retry has no uncertain create intent")
            ok, found, detail = context.services.testplans.find_auth_suite(
                name, query, **_options(frozen, suite_type, parent))
            if not ok or found is not None:
                raise ValueError(f"Category absence not established: {detail}")
            history = receipt.setdefault("attempt_history", [])
            history.append({"operation_key": receipt["operation_key"], "status": receipt["status"],
                            "reason": reason})
            receipt.update(status="retry_authorized", retry_reason=reason)
            return RetryDecision(True, "Absent category retry authorized", (StepData(data),))
        raise ValueError("Recorded categories must be reconciled, never recreated")
    except (ValueError, TypeError, KeyError) as exc:
        return RetryDecision(False, str(exc))
