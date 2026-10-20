"""Versioned Authenticator category identities; classification never uses ADO tags."""
from copy import deepcopy

LAYOUT = "auth-manual-automated-v1"
MONTHLY_LAYOUT = "auth-manual-automated-monthly-v2"
AMENDMENT_LAYOUT = "auth-manual-automated-monthly-amendment-v1"
AUTHORITY = "phase2_android_case_mapping_v1"
FIELDS = {"layout", "manual_suite_id", "automated_suite_id", "classification", "queries"}


def positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid Authenticator category identity")
    return value


def classification(rc, projection):
    provenance = projection["provenance"]
    return validate_classification({
        "version": 1, "authority": AUTHORITY, "rc": positive(int(rc["rc"])),
        "apk_build_id": positive(int(provenance["apk_build_id"])),
        "test_build_id": positive(int(provenance["build_id"])),
        "mapped_case_ids": sorted(projection["cases"]),
    })


def validate_classification(source):
    if (not isinstance(source, dict) or set(source) != {
            "version", "authority", "rc", "apk_build_id", "test_build_id", "mapped_case_ids"}
            or type(source.get("version")) is not int or source["version"] != 1
            or source.get("authority") != AUTHORITY):
        raise ValueError("Unknown Authenticator classification source")
    for key in ("rc", "apk_build_id", "test_build_id"):
        positive(source[key])
    ids = source["mapped_case_ids"]
    if not isinstance(ids, list) or ids != sorted({positive(cid) for cid in ids}):
        raise ValueError("Invalid frozen Authenticator mapped-case set")
    return deepcopy(source)


def queries(source):
    from tools import testplans as T
    ids = validate_classification(source)["mapped_case_ids"]
    base = T.auth_bugbash_query()
    if not ids:
        return {"manual": base + " and [System.Id] > 0",
                "automated": base + " and [System.Id] = 0"}
    values = ", ".join(map(str, ids))
    return {"manual": base + f" and [System.Id] not in ({values})",
            "automated": base + f" and [System.Id] in ({values})"}


def is_partition(data):
    if not isinstance(data, dict):
        raise ValueError("Malformed Authenticator clone record")
    if "layout" not in data:
        if (FIELDS | {"creations", "parent_suite_id", "monthly", "monthly_source", "monthly_amendment"}).intersection(data):
            raise ValueError("Authenticator categories missing an explicit layout")
        return False
    if data["layout"] not in (LAYOUT, MONTHLY_LAYOUT):
        raise ValueError("Unknown Authenticator category layout")
    if data["layout"] == LAYOUT and {"monthly", "monthly_source"}.intersection(data):
        raise ValueError("Historical v1 cannot adopt Monthly by replay; use checked amend-auth-monthly or a reviewed fresh v2 clone")
    if data["layout"] != LAYOUT and "monthly_amendment" in data:
        raise ValueError("Monthly amendment belongs only to an unchanged v1 clone")
    return True


def target(data):
    """Legacy suite_id is dynamic; category suite_id is the static parent link."""
    from tools import testplans as T
    if not is_partition(data):
        value = data.get("suite_id")
        if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit():
            raise ValueError("Invalid legacy Authenticator suite identity")
        return {"plan_id": T.AUTH_PLAN, "suite_id": positive(int(value))}
    if "monthly_amendment" in data:
        from orchestrator import auth_monthly_amendment as E
        receipt = E.completed(data)
        monthly = receipt["monthly"]
        base = {**receipt["plan"]["base_clone"]["data"], "layout": MONTHLY_LAYOUT,
                "monthly": monthly, "monthly_source": monthly["source"]}
        result = target(base)
        result["layout"] = AMENDMENT_LAYOUT
        result["amendment"] = {key: deepcopy(receipt[key]) for key in (
            "version", "id", "review_hash", "proof_sha256")}
        result["amendment"]["base_sha256"] = receipt["plan"]["base_sha256"]
        return result
    source = validate_classification(data.get("classification"))
    if (data.get("queries") != queries(source)
            or positive(data.get("plan_id")) != T.AUTH_PLAN):
        raise ValueError("Authenticator frozen category queries/plan changed")
    parent, manual, automated = (positive(data.get(key)) for key in (
        "suite_id", "manual_suite_id", "automated_suite_id"))
    if len({parent, manual, automated, T.AUTH_ROOT_SUITE}) != 4:
        raise ValueError("Conflicting Authenticator category identities")
    result = {"layout": data["layout"], "plan_id": T.AUTH_PLAN, "suite_id": automated,
            "parent_suite_id": parent, "manual_suite_id": manual,
            "classification": source, "queries": deepcopy(data["queries"])}
    if data["layout"] == MONTHLY_LAYOUT:
        from tools import auth_monthly as M
        monthly = M.checked(data.get("monthly"))
        if (monthly["parent_suite_id"] != parent or monthly["source"] != data.get("monthly_source")
                or monthly["suite_id"] in (parent, manual, automated, T.AUTH_ROOT_SUITE)
                or {b["case_id"] for b in monthly["bindings"]} & set(source["mapped_case_ids"])):
            raise ValueError("Monthly target conflicts with canonical Authenticator categories")
        result["monthly"] = deepcopy(monthly)
    return result


def validate_target(value):
    if isinstance(value, dict) and value.get("layout") == AMENDMENT_LAYOUT:
        from orchestrator import revision as R
        proof = value.get("amendment")
        if (not isinstance(proof, dict) or set(proof) != {
                "version", "id", "review_hash", "proof_sha256", "base_sha256"}
                or type(proof.get("version")) is not int or proof["version"] != 1
                or not isinstance(proof.get("id"), str) or not proof["id"].strip()
                or not all(R.is_hash(proof.get(k)) for k in ("review_hash", "proof_sha256", "base_sha256"))):
            raise ValueError("Malformed Monthly amendment target proof")
        base = {k: deepcopy(v) for k, v in value.items() if k != "amendment"}
        base["layout"] = MONTHLY_LAYOUT
        validate_target(base)
        return
    expected = {
            "layout", "plan_id", "suite_id", "parent_suite_id", "manual_suite_id",
            "classification", "queries"}
    if isinstance(value, dict) and value.get("layout") == MONTHLY_LAYOUT:
        expected.add("monthly")
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Malformed Authenticator category target")
    data = {**value, "suite_id": value["parent_suite_id"],
            "automated_suite_id": value["suite_id"]}
    if "monthly" in value:
        data["monthly_source"] = value["monthly"]["source"]
    if target(data) != value:
        raise ValueError("Conflicting Authenticator category target")


def current(context, data):
    """Validate only captured Phase-2 evidence, never read/reconstruct provider evidence."""
    from steps.build_verify._common import latest_rc
    from tools import pipelines as P
    value = target(data)
    if "monthly_amendment" in data:
        from orchestrator import auth_monthly_amendment as E
        from orchestrator.step_context import thaw
        receipt = E.completed(data)
        step = context.evidence.step("bug_bash", "clone_plans_auth")
        # Context step views carry their own metadata, not an engine StepState instance.
        base = {key: thaw(getattr(step, key)) for key in receipt["plan"]["base_clone"]}
        base["data"].pop("monthly_amendment", None)
        if (base != receipt["plan"]["base_clone"] or any(
                receipt["plan"]["identity"][key] != getattr(context.release, key)
                for key in ("release_id", "ccd", "target_month", "owner_email"))):
            raise ValueError("Monthly amendment original completed clone/release identity changed")
    if is_partition(data):
        rc = latest_rc(context)
        ok, projection, detail = P.project_auth_ui_results(rc)
        if not ok:
            raise ValueError(detail)
        if classification(rc, projection) != value["classification"]:
            raise ValueError("Authenticator RC/build/mapped-case classification changed; owner recovery required")
        if "monthly" in value:
            from tools import auth_monthly as M
            M.current(rc, value["monthly"])
    return value


def has_monthly(data):
    return "monthly" in target(data)
