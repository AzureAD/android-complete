"""Pure target-plan mapping, used by the Phase-3 fill and Broker structure owner.

Evidence validation is neutral; distinct source scenarios use Failed-wins at a
case/config point. Phase-2 capture and reporting never call these projections.
"""
from collections import Counter

from .test_evidence import require, validate_snapshot_tests, result_links
from .tests_results import MRWP_COUNT_BASIS, _positive_test_id, _ui_case_id_from_result
from .auth_evidence import inspect_auth_ui_evidence, MONTHLY_REPORT_ONLY
from tools.ui_mapping import route_suite, config_for


def project_mrwp_ui_results(rc):
    """Map one current RC's frozen MRWP evidence; no I/O or gate decisions."""
    verdicts, failures, providers = {}, [], []
    try:
        require(isinstance(rc, dict) and _positive_test_id(rc.get("rc")),
                "current RC not identified")
        for slot, flight in (("ecs", "ECS"), ("local", "Local")):
            try:
                bid, tests = validate_snapshot_tests(rc.get(slot))
            except ValueError as exc:
                raise ValueError(f"MRWP {flight}: {exc}") from exc
            skipped, sources = [], []
            for suite, test in tests:
                cid = _positive_test_id(_ui_case_id_from_result({"testCaseTitle": test["title"]}))
                variant, routing = route_suite(suite)
                source = {"suite": suite, "title": test["title"], "case_id": cid,
                          "verdict": test["verdict"], "routing": routing,
                          "status": "mapped" if cid and variant else "unmapped",
                          "links": result_links(test["attempts"], "engineering")}
                if cid and variant:
                    source["config_id"] = config_for(flight, variant)
                sources.append(source)
                if test["verdict"] == "Failed":
                    failures.append({**source, "flight": flight, "build_id": bid})
                if not cid or not variant:
                    skipped.append({**source, "status": "skipped_mapping",
                                    "reason": "missing_case_id" if not cid else "unknown_suite_variant"})
                    continue
                fv = (flight, variant)
                previous = verdicts.setdefault(cid, {}).get(fv, "NotApplicable")
                represented = (previous, test["verdict"])
                verdicts[cid][fv] = ("Failed" if "Failed" in represented else
                                     "Passed" if "Passed" in represented else "NotApplicable")
            providers.append({"flight": flight, "build_id": bid, "ui_tests": len(tests),
                              "mapped_tests": len(tests) - len(skipped), "skipped_mapping": skipped,
                              "sources": sources})
        require(providers[0]["build_id"] != providers[1]["build_id"],
                "ECS and Local cannot reference the same MRWP build")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return False, None, f"{exc}; refresh Phase-2 MRWP verification before filling results"
    verdicts = {cid: dict(sorted(values.items())) for cid, values in sorted(verdicts.items())}
    return True, {"verdicts": verdicts, "failures": failures,
                  "provenance": {"rc": int(rc["rc"]), "count_basis": MRWP_COUNT_BASIS,
                                 "projection_rule": "distinct_tests_failed_wins",
                                 "mapping_version": 2, "providers": providers}}, ""


def project_auth_ui_results(rc):
    """Map validated source scenarios, excluding the intentionally report-only Monthly suite."""
    ok, evidence, detail = inspect_auth_ui_evidence(rc)
    if not ok:
        return False, None, detail
    cases, sources = {}, []
    for source in evidence["sources"]:
        cid = source["case_id"]
        report_only = source["suite"] == MONTHLY_REPORT_ONLY
        status = "intentional_report_only" if report_only else "mapped" if cid else "unmapped"
        sources.append({**source, "case_id": None if report_only else cid, "status": status})
        if status == "mapped":
            case = cases.setdefault(cid, {"outcome": "NotApplicable", "titles": []})
            case["titles"].append(source["title"])
            represented = (case["outcome"], source["verdict"])
            case["outcome"] = ("Failed" if "Failed" in represented else
                               "Passed" if "Passed" in represented else "NotApplicable")
    return True, {
        "cases": {cid: {**case, "titles": sorted(set(case["titles"])), "title": min(case["titles"])}
                  for cid, case in sorted(cases.items())},
        "sources": sources, "failures": [s for s in sources if s["verdict"] == "Failed"],
        "provenance": {**evidence["provenance"], "mapping_version": 1, "mapped_cases": len(cases),
                       "dispositions": dict(sorted(Counter(s["status"] for s in sources).items()))},
    }, ""


__all__ = ["project_mrwp_ui_results", "project_auth_ui_results"]
