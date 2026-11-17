"""Version-four MRWP failure inventory; never an execution/pass-rate denominator."""
from collections import Counter
from copy import deepcopy

from tools import broker_scenarios as S, testplans as T, pipelines as P
from tools.coordinates import coords
from tools.pipelines.test_evidence import require, result_links, validate_snapshot_tests
from tools.ui_mapping import config_for, route_suite


VERSION = 4
LAYOUT = "mrwp_failure_triage_by_flight"
MARKER = "release-agent:broker-source:v4"
CATEGORIES = ("UI Automation", "Stress Tests") + S.CATEGORIES
CLASSIFICATION_POLICY = 2


def projection_policy(projection):
    policy = projection.get("classification_policy", CLASSIFICATION_POLICY)
    require(type(policy) is int and policy == CLASSIFICATION_POLICY,
            "Unknown failure classification policy")
    return policy


def _category(name, _policy):
    category = P.classify_test_run(name)
    if category == "stress":
        return "Stress Tests", category
    variant, _ = route_suite(name)
    return ("UI Automation" if category == "ui" and variant is not None else
            "Instrumented Tests" if category == "instrumented" else
            "Unit Tests" if category == "unit" else
            "Lab API" if name == "Lab Api Tests" else
            "Native Auth E2E" if name == "Native Auth E2E Tests" else
            "Other UI Automation"), category


def category_role(flight, category):
    return f"category:{flight}:{category}"


def build_url(build_id):
    return (f"{coords.org_url('engineering')}/{coords.project('engineering')}"
            f"/_build/results?buildId={build_id}")


def project(rc, *, tracked=(), classification_policy=CLASSIFICATION_POLICY):
    try:
        require(isinstance(rc, dict), "Missing current RC evidence")
        policy = projection_policy({"classification_policy": classification_policy})
        number = rc.get("rc")
        require(type(number) is int and number > 0, "Missing explicit current RC")
        rows, warnings, providers = [], [], []
        tracked = {r["key"]: r for r in tracked}
        for flight in ("ECS", "Local"):
            snapshot = rc.get(flight.lower()) or {}
            bid, suites = validate_snapshot_tests(snapshot, allow_empty=True, include_all=True)
            providers.append({"flight": flight, "build_id": bid, "skipped_mapping": []})
            for name, test in suites:
                title = test["title"]
                key = S.scenario_key(number, flight, name, title)
                if test.get("recovered"):
                    warnings.append({"key": key, "title": title, "suite": name, "flight": flight,
                                     "reason": "failed_attempt_recovered", "attempts": deepcopy(test["attempts"])})
                if test["verdict"] != "Failed" and key not in tracked:
                    continue
                category, source_category = _category(name, policy)
                variant, routing = route_suite(name)
                variant = variant or "rc_msal_rc_broker"
                config = config_for(flight, variant)
                require(config, f"Unsupported configuration for {name}")
                parsed = (S._positive_test_id(S._ui_case_id_from_result({"testCaseTitle": title}))
                          if source_category in ("ui", "stress") else None)
                ids = [parsed] if parsed else []
                original = ids[0] if len(ids) == 1 else None
                rows.append({"key": key, "kind": "failure", "category": category, "rc": number,
                             "flight": flight, "build_id": bid, "suite": name, "title": title,
                             "build_url": build_url(bid),
                             "config_id": config, "original_case_id": original, "case_ids": ids,
                             "verdict": test["verdict"], "recovered": bool(test.get("recovered")),
                             "links": result_links(test["attempts"], "engineering"),
                             "routing": routing, "attempts": deepcopy(test["attempts"])})
        rows.sort(key=lambda r: (r["flight"], r["category"], r["suite"], r["title"]))
        require(set(tracked).issubset({r["key"] for r in rows}),
                "Previously tracked failure lacks current exact source observation; history retained")
        projection = {"schema_version": VERSION, "layout": LAYOUT, "rc": number,
                      "count_basis": S.MRWP_COUNT_BASIS, "inventory": [], "triage": rows,
                      "warnings": warnings, "providers": providers,
                      "counts": dict(Counter(r["verdict"] for r in rows))}
        if policy != 1:
            projection["classification_policy"] = policy
        validate_projection(projection, initial=not tracked)
        return True, projection, ""
    except (ValueError, KeyError, TypeError) as exc:
        return False, None, f"Cannot prepare failure-only MRWP source: {exc}"


def validate_projection(projection, *, initial=False):
    require(isinstance(projection, dict) and projection.get("schema_version") == VERSION
            and projection.get("layout") == LAYOUT and projection.get("inventory") == []
            and projection.get("count_basis") == S.MRWP_COUNT_BASIS
            and type(projection.get("rc")) is int and projection["rc"] > 0,
            "Unsupported failure-only source contract")
    require(isinstance(projection.get("triage"), list), "Missing failure scenario inventory")
    policy = projection_policy(projection)
    providers = projection.get("providers")
    require(isinstance(providers, list) and len(providers) == 2
            and all(isinstance(p, dict) for p in providers)
            and [p.get("flight") for p in providers] == ["ECS", "Local"]
            and all(type(p.get("build_id")) is int and p["build_id"] > 0 for p in providers)
            and providers[0]["build_id"] != providers[1]["build_id"],
            "Missing pinned flight build identities")
    seen = set()
    for row in projection["triage"]:
        S._validate_scenario_row(row, projection)
        require(row["key"] not in seen and row["kind"] == "failure"
                and row["category"] == _category(row["suite"], policy)[0]
                and row.get("build_url") == build_url(row["build_id"])
                and (not initial or (row["verdict"] == "Failed" and not row["recovered"])),
                "Invalid/duplicate/nonfailed initial investigation")
        require(row["build_id"] == next(p["build_id"] for p in providers
                                       if p["flight"] == row["flight"]),
                "Failure/provider build identity changed")
        seen.add(row["key"])
    require(projection.get("counts") == dict(Counter(r["verdict"] for r in projection["triage"])),
            "Failure inventory counts changed")


def representations(source):
    validate_projection(source["scenario_projection"])
    rows = deepcopy(source["scenario_projection"]["triage"])
    catalog = set(source["ui_cases"])
    collisions = Counter((r["original_case_id"], r["config_id"]) for r in rows)
    for row in rows:
        original = row["original_case_id"]
        if original not in catalog:
            original = row["original_case_id"] = None
        row["reuse_case_id"] = (original if row["category"] == "UI Automation"
                                and original in catalog
                                and collisions[original, row["config_id"]] == 1 else None)
    return rows


def specs(source, root):
    rows = representations(source)
    result = [("broker", T.BROKER_MANUAL_SUITE_NAME, root, source["broker_configs"], False),
              ("native", T.BROKER_NATIVE_AUTH_SUITE_NAME, root, [], True)]
    if rows:
        result.append(("triage", S.TRIAGE, root, source["root_configs"], False))
    for flight in ("ECS", "Local"):
        selected = [r for r in rows if r["flight"] == flight]
        if not selected:
            continue
        result.append((f"flight:{flight}", flight, "triage", source["root_configs"], False))
        for category in CATEGORIES:
            configs = sorted({r["config_id"] for r in selected if r["category"] == category})
            if configs:
                result.append((category_role(flight, category), category, f"flight:{flight}", configs, False))
    return result


def record_specs(record):
    return specs(record["source"], record["plan_root_id"])


def automation_contract(record):
    from tools.broker_plans import positive_id
    suites = record["suites"]
    root = positive_id(record.get("plan_root_id"))
    expected = {role for role, *_ in record_specs(record)}
    require(set(suites) == expected, "Failure hierarchy role IDs changed")
    ids = [positive_id(sid) for sid in suites.values()]
    require(len(ids) == len(set(ids)) and root not in ids, "Duplicate failure hierarchy suite IDs")
    flights = {}
    for flight in ("ECS", "Local"):
        if f"flight:{flight}" in suites:
            flights[flight] = {"suite_id": suites[f"flight:{flight}"],
                              "categories": {category: suites[category_role(flight, category)]
                                             for category in CATEGORIES
                                             if category_role(flight, category) in suites}}
    return {"layout": LAYOUT, "anchor": {"kind": "plan_root", "suite_id": root},
            "root_suite_id": suites.get("triage"), "flights": flights}


def target(record):
    from tools.broker_plans import positive_id
    contract = automation_contract(record)
    require(record.get("automation") == contract and "ui_suite_id" not in record,
            "Failure-only typed destination changed")
    return {"plan_id": positive_id(record["plan_id"]), "layout": LAYOUT,
            "root_suite_id": contract["anchor"]["suite_id"]}


def bind_projection(record, projection):
    validate_projection(projection)
    frozen = record["source"]["scenario_projection"]
    require(projection["rc"] == frozen["rc"]
            and projection["providers"] == frozen["providers"]
            and projection_policy(projection) == policy_for_record(record),
            "Failure-only RC/build identity changed; reviewed new source required before writes")
    current = {r["key"]: r for r in representations(
        {"scenario_projection": projection, "ui_cases": record["source"]["ui_cases"]})}
    saved = record["scenario_bindings"]
    require(set(current) == {r["key"] for r in saved},
            "Failure set changed; new failures require explicit reviewed source, tracked history retained")
    bound = []
    for old in saved:
        row = current[old["key"]]
        for field in ("key", "kind", "category", "rc", "flight", "build_id", "suite",
                      "title", "config_id", "original_case_id", "case_ids", "build_url"):
            require(row.get(field) == old.get(field), "Failure source provenance changed before writes")
        bound.append({**old, **{k: deepcopy(row[k]) for k in ("verdict", "links", "attempts", "recovered")}})
    result = S._bound_projection(projection, bound, VERSION)
    result["provenance"]["tracker_basis"] = "retained_failure_investigations_not_execution_coverage"
    return result


def policy_for_record(record):
    return projection_policy(record["source"]["scenario_projection"])


def expected_rows(record):
    return representations(record["source"])


def preview(source):
    rows = representations(source)
    return {"schema_version": VERSION, "layout": LAYOUT, "source_sha256": S.digest(source),
            "rc": source["scenario_projection"]["rc"], "providers": source["scenario_projection"]["providers"],
            "manual_broker_cases": len(source["broker_cases"]),
            "manual_broker_points": len(source["broker_cases"]) * len(source["broker_configs"]),
            "failure_points": len(rows), "new_cases": sum(not r["reuse_case_id"] for r in rows),
            "original_case_references": sum(bool(r["reuse_case_id"]) for r in rows),
            "suites": [{"role": role, "name": name, "parent_role": parent,
                        "configurations": configs, "query": source["native_query"] if native else None}
                       for role, name, parent, configs, native in specs(source, "plan-root")],
            "counts": source["scenario_projection"]["counts"], "warnings": source["scenario_projection"]["warnings"],
            "tracker_basis": "failures_only_not_execution_coverage"}
