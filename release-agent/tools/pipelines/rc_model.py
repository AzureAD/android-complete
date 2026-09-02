"""RC report model assembly (release_report / assemble_rc_model) + version formatting + the Engineering release-chain coordinates."""
from __future__ import annotations

import json as _json
import shutil
import subprocess

from tools.coordinates import coords
from tools import pipelines as _pp
# The Engineering release-chain coordinates are owned by the orchestrator (run-discovery) module.
from tools.pipelines.orchestrator import (
    IDENTITYDIVISION, MSAZURE, ENGINEERING_ORG, ENGINEERING_PROJECT,
    CHECKER_DEF, ORCHESTRATOR_DEF, MRWP_DEF, TRIGGER_JOB, ORCH_REQUIRED_STAGES, ORCH_PARK_STAGE,
)


def assemble_rc_model(release, checker, orchestrator, mrwp, *, rc=None,
                      id_source=None, io_problems=None, auth=None):
    """The ONE canonical Phase-2 RC report model — built from already-resolved pieces,
    whether they came from LIVE reads (release_report) or the state snapshot
    (steps.build_verify._common.rc_report_model). Both paths call this so they can never
    drift on shape or on how `problems` are derived.

    Sections:
      checker      : {fired, run_id, when} | {fired:False} | {error}
      orchestrator : {found, healthy, run_id, versions, parked, failed_stages, park_stage} | {found:False[,error]}
      mrwp         : {ECS:{...}, Local:{...}}   each: run_id, complete, ran, total,
                     failed_stages, yellow_stages, never_ran, tests, failed_suites [, error]
    `problems` is derived here in section order (checker → orchestrator → io_problems →
    mrwp) from each section's `error`/structural state, so the messages are identical for
    both callers. `io_problems` are non-sectional read failures (e.g. MRWP id resolution).
    Returns {release, checker, orchestrator, mrwp, problems, rc[, mrwp_id_source]}.
    """
    checker = checker or {}
    orchestrator = orchestrator or {}
    mrwp = mrwp or {}
    problems = []

    if checker.get("error"):
        problems.append(f"Checker: could not read runs ({checker['error']}).")
    elif checker.get("fired") is False:
        problems.append("Checker: no run has a succeeded 'Trigger Monthly Release' job "
                        "(release not triggered yet, or before Code Complete Day).")

    o = orchestrator
    if o.get("found") is False:
        problems.append(f"Orchestrator: could not read runs ({o['error']})." if o.get("error")
                        else f"Orchestrator: no run found for {release}.")
    elif o.get("error"):
        problems.append(f"Orchestrator: could not read stages ({o['error']}).")
    elif o.get("failed_stages"):
        problems.append("Orchestrator: pre-gate stage(s) not green: "
                        + ", ".join(o["failed_stages"]) + ".")

    problems.extend(io_problems or [])

    for provider in ("ECS", "Local"):
        e = mrwp.get(provider)
        if not e:
            continue
        if e.get("error"):
            problems.append(f"MRWP {provider}: could not read stages ({e['error']}).")
        elif e.get("complete") is False:
            nv = ", ".join(n for n in (e.get("never_ran") or []) if n) or "(unknown)"
            problems.append(f"MRWP {provider}: did NOT run to completion — never-ran: {nv}.")

    model = {"release": release, "checker": checker, "orchestrator": orchestrator,
             "mrwp": mrwp, "problems": problems, "rc": rc}
    if id_source is not None:
        model["mrwp_id_source"] = id_source
    if auth is not None:
        model["auth"] = auth
    return model


def release_report(org, project, release_month, checker_def=CHECKER_DEF,
                   orch_def=ORCHESTRATOR_DEF, timeout=90, with_failed_tests=True):
    """Assemble the full Phase-2 RC-pipeline + test report for a release month by LIVE
    reads (does NOT gate; it reports). Resolves the checker / orchestrator / both MRWP
    runs, then hands the pieces to `assemble_rc_model` (the shared assembler) so this live
    path and the state-snapshot path produce an identical model shape + problems. Any
    field that couldn't be read carries an `error` note (surfaced as a problem).

    `with_failed_tests` (default True) also fetches the individual failing test names per
    suite (extra REST calls — set False for a faster stage-only view)."""
    # --- checker (did the release fire?) ---
    ok, runs, detail = _pp.find_checker_runs(org, project, checker_def, release_month, timeout)
    if not ok:
        checker = {"error": detail}
    else:
        fired = None
        for run in (runs or [])[:25]:
            ok2, recs, _ = _pp.get_timeline(org, project, run.get("id"), timeout)
            if not ok2:
                continue
            rec = _pp.named_record(recs, TRIGGER_JOB)
            if rec is not None and rec.get("result") == "succeeded":
                fired = run
                break
        checker = ({"fired": True, "run_id": fired.get("id"),
                    "when": (fired.get("queueTime") or "")[:16]} if fired else {"fired": False})

    # --- orchestrator (healthy? parked?) ---
    ok, orun, detail = _pp.find_orchestrator_run(org, project, orch_def, release_month, timeout)
    if not ok:
        return _pp.assemble_rc_model(release_month, checker, {"found": False, "error": detail}, {})
    if not orun:
        return _pp.assemble_rc_model(release_month, checker, {"found": False}, {})

    oid, tags = orun.get("id"), (orun.get("tags") or [])
    versions = {k: _pp._tag_value(tags, f"Next{k}Version") for k in ("Common", "Msal", "Broker")}
    ok, ostages, detail = _pp.get_stages(org, project, oid, timeout)
    o = {"found": True, "run_id": oid, "versions": versions,
         "park_stage": ORCH_PARK_STAGE, "failed_stages": [], "healthy": None, "parked": None}
    if not ok:
        o["error"] = detail
    else:
        by = {s.get("name"): s for s in ostages}
        o["failed_stages"] = [n for n in ORCH_REQUIRED_STAGES
                              if (by.get(n) or {}).get("result") != "succeeded"]
        o["healthy"] = not o["failed_stages"]
        park = by.get(ORCH_PARK_STAGE)
        o["parked"] = bool(park and park.get("state") != "completed")

    # --- the two MRWP runs (ran to completion? tests?) ---
    ok, ids, detail, source = _pp.mrwp_run_ids(org, project, orun, timeout)
    if not ok:
        return _pp.assemble_rc_model(release_month, checker, o, {},
                                 io_problems=[f"MRWP: could not resolve run ids ({detail})."])
    mrwp = {}
    for provider in ("ECS", "Local"):
        bid = ids.get(provider)
        entry = {"run_id": bid}
        ok, stages, detail = _pp.get_stages(org, project, bid, timeout)
        if not ok:
            entry["error"] = detail
        else:
            comp = _pp.stage_completion(stages)
            entry.update({"complete": comp["complete"], "ran": comp["ran"],
                          "total": comp["total"], "failed_stages": comp["failed"],
                          "yellow_stages": comp["yellow"], "never_ran": comp["never_ran"]})
        okt, tests, _ = _pp.get_test_summary(org, project, bid, timeout)
        entry["tests"] = tests if okt else None
        # Individual failing tests, aggregated by suite (deduped across repeated runs).
        if with_failed_tests and bid and tests and tests.get("failed"):
            okf, suites, _ = _pp.get_failed_tests(org, project, bid, timeout=timeout)
            entry["failed_suites"] = suites if okf else None
        mrwp[provider] = entry
    return _pp.assemble_rc_model(release_month, checker, o, mrwp, rc=ids.get("rc"),
                             id_source=source)

__all__ = ['CHECKER_DEF', 'ENGINEERING_ORG', 'ENGINEERING_PROJECT', 'IDENTITYDIVISION', 'MRWP_DEF', 'MSAZURE', 'ORCHESTRATOR_DEF', 'ORCH_PARK_STAGE', 'ORCH_REQUIRED_STAGES', 'TRIGGER_JOB', 'assemble_rc_model', 'release_report']
