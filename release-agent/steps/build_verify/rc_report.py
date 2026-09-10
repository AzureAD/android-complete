"""Step: `rc_report` — consolidate the RC data, email the report, and make the Phase-2
go/hold decision (Phase 2, build_verify). This is the terminal Phase-2 step and the single
decision point — the verification steps only CAPTURE data; this step decides. No separate
human approval gate.

When verification and telemetry prerequisites have resolved the chain, this step composes the
Phase-2 RC report (checker → orchestrator → ECS/Local MRWP + per-run test failures)
from complete current-RC snapshots and emails it to the release owner. Once evidence is
ready, the report is sent even for failures (the owner gets the dashboard of
failures + links either way). The step's OUTCOME is then decided by TWO independent gates:
(1) the three-tier MRWP UI gate on the combined UI-automation pass rate across both MRWP
runs (100% clean; >= RC_UI_PASS_THRESHOLD (90%) but < 100% warn — proceed + investigate in
parallel; < 90% hold); and (2) the Authenticator ECS gate (both Firebase suites >= 90% and
the auth build succeeded). The release AUTO-ADVANCES into Phase 3 only when BOTH gates clear;
if EITHER holds, the step records `attention` and BLOCKS (the release WAITS for human
attestation).

Sending email needs the WorkIQ MCP the engine can't reach, so this is a `scout`
step: `build()` composes the email deterministically and returns a
NeedsSkill(workiq_send_email); the payload names the `record-rc-report` follow-up
command, which rechecks readiness, applies BOTH gates, records pass|attention, and
stashes the evaluated run links on the step. Redirect for tests with the `send_to`
payload knob (keeps the send real, points it at you).
"""
from __future__ import annotations

from orchestrator.outcomes import NeedsSkill, Blocked
from steps.build_verify import auth_ecs as A, _rc_report_rendering as R
from steps.build_verify._common import build_url, latest_rc, valid_id, valid_counts
from steps.lib.context import release_ctx
from tools import pipelines as P
from tools.coordinates import coords

ID = "rc_report"
KIND = "scout"

MOCKABLE = {
    "send_to": {
        "kind": "payload", "sets": "to", "as": "list", "tag_subject": True,
        "desc": "Send the RC report for real, but only to these address(es) (owner → you).",
    },
}

# The Phase-2 quality bar: at least this % of UI-automation tests must pass for RC to
# clear to bug bash. Below it, the rc_report step blocks for owner investigation
# (a large UI failure usually means a real regression → fix + re-run MRWP).
RC_UI_PASS_THRESHOLD = coords.gate("rc_ui_pass_pct")

# The broker-libraries cherry-pick process — the exit for a REAL product bug behind the
# UI failures (patch → orchestrator triggers a fresh RC). Surfaced in the block detail.
CHERRY_PICK_TSG = ("https://eng.ms/docs/microsoft-security/identity/"
                   "entra-developer-application-platform/auth-client/"
                   "authn-sdk-msal-android/android-auth-libraries/releases/"
                   "internal-release-checklist/cherry-pick-process-for-broker-libraries")

# Map canonical state.versions (lowercase) to the RC report's SDK display shape.
_VMAP = {"common": "Common", "msal": "Msal", "broker": "Broker"}


def _caps_versions(state) -> dict:
    sv = getattr(state, "versions", None) or {}
    return {cap: sv[low] for low, cap in _VMAP.items() if sv.get(low)}


def ui_evidence_issues(model):
    return [f"MRWP {provider}: missing or invalid non-zero UI results"
            for provider in ("ECS", "Local")
            if not valid_counts(((((model.get("mrwp") or {}).get(provider) or {})
                                  .get("tests") or {}).get("categories") or {}).get("ui"))]


def report_readiness(model):
    """Failures are reportable; unresolved, in-flight or invalid evidence is not."""
    issues = []
    if not valid_id(model.get("rc")):
        issues.append("Current RC iteration has not been identified")
    for name in ("checker", "orchestrator"):
        section = model.get(name) or {}
        if not valid_id(section.get("run_id")) or section.get("error"):
            issues.append(f"{name}: missing verified run id")
    for provider in ("ECS", "Local"):
        run = (model.get("mrwp") or {}).get(provider) or {}
        if (not valid_id(run.get("run_id")) or run.get("complete") is not True
                or type(run.get("total")) is not int or run["total"] <= 0
                or type(run.get("ran")) is not int or run["ran"] != run["total"]
                or run.get("never_ran") or run.get("error")):
            issues.append(f"MRWP {provider}: missing completed stage snapshot")
    issues.extend(ui_evidence_issues(model))
    issues.extend(A.auth_evidence_issues(model))
    return {"ready": not issues, "issues": issues,
            "detail": "RC report evidence not ready: " + "; ".join(issues) if issues else
                      "Current RC evidence is ready."}


def rc_report_model(state, timeout=120):
    """The Phase-2 RC report model — assembled from the RECORD in state.pipeline_runs
    (the verification steps stored it), NOT a live re-discovery. Uses the LATEST RC
    iteration (rcs[-1]) and routes through `tools.pipelines.assemble_rc_model` — the SAME
    assembler the live path uses — so the state-based model can't drift from the live one.
    """
    pr = getattr(state, "pipeline_runs", None) or {}
    ch = pr.get("checker") or {}
    o = pr.get("orchestrator") or {}
    rc = latest_rc(state)

    checker = {"fired": bool(ch.get("run_id")), "run_id": ch.get("run_id"), "when": ch.get("when")}
    # healthy=True is true by construction here: rc_report only runs AFTER orchestrator_health
    # passed (a failed pre-gate stage blocks that step, so we never reach this with an
    # unhealthy orchestrator). The live path (release_report) derives it from stages.
    orchestrator = {"found": bool(o.get("run_id")), "healthy": True,
                    "run_id": o.get("run_id"), "versions": _caps_versions(state),
                    "parked": o.get("parked")}
    mrwp = {}
    for slot, prov in (("ecs", "ECS"), ("local", "Local")):
        s = rc.get(slot)
        if not s:
            continue
        mrwp[prov] = {
            "run_id": s.get("run_id"), "complete": s.get("complete"),
            "ran": s.get("ran"), "total": s.get("total"),
            "failed_stages": s.get("failed_stages") or [],
            "yellow_stages": s.get("yellow_stages") or [],
            "never_ran": s.get("never_ran") or [],
            "tests": s.get("tests"), "failed_suites": s.get("failed_suites"),
        }
    return P.assemble_rc_model(state.release_id, checker, orchestrator, mrwp, rc=rc.get("rc"),
                               auth=rc.get("auth"))


def rc_run_links(model) -> list:
    """Durable links to EVERY pipeline run the RC verification evaluated — the Code
    Complete Checker, the Release Orchestrator, and both MRWP (ECS + Local) runs — so the
    recorded step points at each artifact behind the verdict (surfaced in the step's
    Details). Only runs with a resolved id are included."""
    out = []
    ch = (model.get("checker") or {}).get("run_id")
    if ch:
        out.append({"name": "Code Complete Checker run", "url": build_url(ch)})
    orid = (model.get("orchestrator") or {}).get("run_id")
    if orid:
        out.append({"name": "Release Orchestrator run", "url": build_url(orid)})
    for prov in ("ECS", "Local"):
        rid = ((model.get("mrwp") or {}).get(prov) or {}).get("run_id")
        if rid:
            out.append({"name": f"MRWP {prov} run", "url": build_url(rid)})
    # Authenticator ECS build + its post-build UI-test run (msazure/One), when captured.
    a = model.get("auth") or {}
    if a:
        ab = (a.get("build") or {}).get("run_id")
        if ab:
            out.append({"name": "Authenticator ECS build", "url": A.auth_build_url(ab)})
        at = (a.get("test") or {}).get("run_id")
        if at:
            out.append({"name": "Authenticator ECS UI tests", "url": A.auth_build_url(at)})
    return out


def _ui_failing_suites_summary(model, limit=6) -> str:
    """A compact 'Top UI failures' list across both providers, or '' when none."""
    suites = []
    for prov in ("ECS", "Local"):
        for s in (((model.get("mrwp") or {}).get(prov) or {}).get("failed_suites") or []):
            if s.get("category", "ui") == "ui" and s.get("failed"):
                suites.append((prov, s))
    suites.sort(key=lambda ps: -ps[1]["failed"])
    if not suites:
        return ""
    return "\nTop UI failures:\n" + "\n".join(
        f"  \u2022 [{prov}] {s['name']}: {s['failed']}/{s['total']} failed"
        for prov, s in suites[:limit])


def rc_ui_gate(model) -> dict:
    """The Phase-2 RC quality gate — a THREE-tier decision on the combined UI-automation
    pass rate across both MRWP providers (ECS + Local). Returns
      {ui_total, ui_passed, ui_failed, pass_pct, threshold, verdict, blocking, detail}
    where `verdict` is:
      * 'clean'     — 100% UI pass: proceed, no action.
      * 'unavailable' — missing/invalid UI evidence in either provider: hold.
      * 'warn'      — >= RC_UI_PASS_THRESHOLD (90%) but < 100%: proceed to bug bash, but
                      the owner should investigate the failing UI tests IN PARALLEL (a
                      later step confirms the retest — bug bash is NOT blocked).
      * 'attention' — < 90%: BLOCK. A large failure the owner must investigate and rule
                      on (patch a real bug + re-trigger RC, or proceed as an automation
                      flake to re-run later).
    `blocking` is True for 'attention' or 'unavailable'. `detail` is the note recorded on the step /
    shown to the owner."""
    ui_total = ui_pass = ui_fail = 0
    missing = ui_evidence_issues(model)
    for prov in ("ECS", "Local"):
        ui = (((((model.get("mrwp") or {}).get(prov) or {}).get("tests") or {})
               .get("categories") or {}).get("ui") or {})
        if valid_counts(ui):
            ui_total += ui["total"]
            ui_pass += ui["passed"]
            ui_fail += ui["failed"]
    thr = RC_UI_PASS_THRESHOLD
    base = {"ui_total": ui_total, "ui_passed": ui_pass, "ui_failed": ui_fail, "threshold": thr}
    if missing:
        return {**base, "pass_pct": None, "verdict": "unavailable", "blocking": True,
                "detail": "; ".join(missing) + ". Capture both providers before evaluating."}
    pass_pct = round(ui_pass * 100.0 / ui_total, 1)
    head = (f"UI-automation pass rate {pass_pct}% ({ui_pass}/{ui_total} passed, "
            f"{ui_fail} failed) across ECS + Local")
    if ui_pass == ui_total:
        return {**base, "pass_pct": pass_pct, "verdict": "clean", "blocking": False,
                "detail": (f"UI-automation pass rate 100% ({ui_pass}/{ui_total}) — all UI "
                           f"tests passed. Proceeding to bug bash.")}
    if ui_pass * 100 >= thr * ui_total:
        return {**base, "pass_pct": pass_pct, "verdict": "warn", "blocking": False,
                "detail": (f"{head} \u2014 at or above the {thr:.0f}% gate but not clean. "
                           f"Proceeding to bug bash; release owner: investigate the {ui_fail} "
                           f"failing UI test(s) in parallel (a later step confirms the retest, "
                           f"so bug bash is not blocked)." + _ui_failing_suites_summary(model))}
    return {**base, "pass_pct": pass_pct, "verdict": "attention", "blocking": True,
            "detail": (
                f"{head} \u2014 BELOW the {thr:.0f}% gate. The RC report was emailed; the "
                f"autonomous tick then halted here (it will NOT auto-advance while blocked). "
                f"First decide whether this is automation flakiness or a real product bug, "
                f"then take ONE of three exits:\n"
                f"1) Re-trigger (flaky) \u2014 if these are flaky suites, re-run the failed RC "
                f"test run, then signal `rc-retriggered --release <id> --reason \"...\"`. Scout "
                f"tracks the NEW RC: it holds (no action) while the run is in-flight, polls "
                f"every 30 min, and re-applies this gate the moment it completes.\n"
                f"2) Cherry-pick (real bug) \u2014 if a product bug is driving the failures, patch "
                f"it via the broker cherry-pick process ({CHERRY_PICK_TSG}); the orchestrator "
                f"then triggers a fresh RC. Signal `rc-retriggered --release <id>` so Scout "
                f"tracks the newest RC to completion.\n"
                f"3) Override (LAST RESORT) \u2014 `skip --release <id> --phase build_verify "
                f"--step rc_report --reason \"<why>\"`. Only after discussing with the team: "
                f"proceeding to Bug Bash with this many UI failures is a team decision, not a "
                f"default. The reason is recorded for audit."
                + _ui_failing_suites_summary(model))}


def auth_report_gate(model) -> dict:
    """The Authenticator-ECS decision input for the RC report CONSOLIDATION. Reads the
    captured auth section (model['auth']) and returns {present, verdict, blocking, detail}.
    verdict 'clean' -> non-blocking; failures and unavailable evidence both block."""
    a = model.get("auth") or {}
    issues = A.auth_evidence_issues(model)
    if issues:
        return {"present": bool(a), "verdict": "unavailable", "blocking": True,
                "detail": "; ".join(issues)}
    build = a.get("build") or {}
    v = ("attention" if build.get("result") in ("failed", "canceled") else
         A.auth_gate((a.get("test") or {}).get("suites"))["verdict"])
    if v == "clean":
        detail = (f"Authenticator ECS gate: PASS — build {build.get('run_id')} + both "
                  f"Firebase suites >= {A.AUTH_UI_PASS_THRESHOLD:.0f}%.")
    else:
        detail = (f"Authenticator ECS gate: HOLD — build {build.get('run_id')}: a Firebase "
                  f"suite is < {A.AUTH_UI_PASS_THRESHOLD:.0f}% (or the build did not succeed). "
                  f"Investigate + re-run the post-build UI test, then re-evaluate.")
    return {"present": True, "verdict": v, "blocking": v == "attention", "detail": detail}


def rc_next_action(model):
    if rc_ui_gate(model)["blocking"] or auth_report_gate(model)["blocking"]:
        return ("HOLD — investigate the evidence and failures below; re-trigger and re-evaluate, "
                "or use an explicit owner-reviewed skip with a reason. No automatic advance.")
    return ("Both quality gates clear. After recording this report, Phase 2 can advance "
            "automatically once all prerequisites are complete; no separate RC approval is needed. "
            "Investigate any warnings in parallel.")


def rc_email(state):
    """Compose the RC verification email (subject, html, plain) for this release from
    verified snapshots. Returns (subject, html, plain, model); refuses incomplete evidence."""
    model = rc_report_model(state)
    readiness = report_readiness(model)
    if not readiness["ready"]:
        raise ValueError(readiness["detail"])
    ctx = release_ctx(state)
    gate, auth = rc_ui_gate(model), auth_report_gate(model)
    next_action = rc_next_action(model)
    return (R.rc_email_subject(model, gate, auth),
            R.rc_email_html(model, ctx, gate, auth, next_action),
            R.rc_email_plain(model, ctx, gate, auth, next_action), model)


def build(state):
    """Compose the RC verification email → NeedsSkill(workiq_send_email). Blocks if the
    owner email is unknown (nowhere to send) — set it with `set-owner`. The email is
    sent only with complete evidence; the gate verdict (recorded by the follow-up) then
    decides whether the step passes or blocks."""
    to = state.owner_email
    if not to:
        return Blocked(
            "rc_report: no release owner email on record — set it with "
            "`set-owner --email <you@microsoft.com>` so the RC report can be sent.")
    try:
        subject, html, plain, model = rc_email(state)
    except Exception as e:                       # pragma: no cover - defensive
        return Blocked(f"rc_report: could not build the RC report ({e}).")

    gate = rc_ui_gate(model)
    auth = auth_report_gate(model)
    v = gate["verdict"]
    if v == "clean":
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"MRWP UI gate CLEAN (100%)")
    elif v == "warn":
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"MRWP UI gate PASS with warning ({gate['pass_pct']}%); proceed + investigate "
                   f"failing UI tests in parallel")
    else:
        summary = (f"Email the RC verification report to the release owner ({to}) — "
                   f"MRWP UI gate FAIL ({gate['pass_pct']}% < {int(RC_UI_PASS_THRESHOLD)}%); "
                   f"will hold for investigation")
    if auth["present"]:
        summary += (f" · Auth ECS {'PASS' if auth['verdict'] == 'clean' else 'HOLD'}")
    note = gate["detail"] + (f"\n\n{auth['detail']}" if auth["present"] else "")
    return NeedsSkill(
        tool="workiq_send_email",
        payload={
            "to": [to],
            "subject": subject,
            "body": html,
            "isHtml": True,
            "_plain_body": plain,
            # After sending, DON'T blind-record pass: run this engine command instead — it
            # consolidates the MRWP UI gate AND the Authenticator-ECS gate (pass|attention)
            # and stashes the run links.
            "followup_command": "record-rc-report",
        },
        record_as=ID,
        summary=summary,
        note=note,
        outbound=True,
    )


def automation_prompt(release: str, spec: dict) -> str:
    """Bespoke instruction for the interval RC poller (owned here, like localization's).
    Only the poller shape is used — rc_report has no time-of-day automation."""
    if not spec.get("interval"):
        return ""       # rc_report is driven by next/tick, not a one-shot automation
    return (
        f"Release {release} — RC verification poller (Phase 2, every 30 min).\n"
        f"Only act if Build & RC Verification is holding on an IN-FLIGHT re-triggered RC "
        f"(the human ran `rc-retriggered`). Poll it once:\n"
        f"1. run `poll-rc --release {release}`.\n"
        f"2. act on the printed decision:\n"
        f"   • waiting  → still running; send nothing.\n"
        f"   • ready    → run step-action for each decision.steps entry in decision.phase, "
        f"execute its tool only for needs_skill, then use its followup_command after "
        f"success. Re-poll afterward; never blind-record a report pass.\n"
        f"   • nudge    → running past 6h; send the courtesy heads-up in decision.nudge "
        f"(email decision.nudge.email to the owner AND post decision.nudge.teams.text to "
        f"the owner's Scout chat). It is stamped, so it goes out at most once.\n"
        f"   • resolved → inspect decision.status: 'passed' means the RC passed the gates; "
        f"'overridden' means Phase 2 completed by an authorized manual override, NOT a "
        f"quality-gate pass. Report the matching outcome and decision.note; never describe "
        f"an override as PASSED. The common cleanup planner removes this poller.\n"
        f"   • blocked  → a Phase-2 prerequisite or quality gate needs attention. "
        f"Surface the block to the owner (the 3-exit choice: re-trigger / cherry-pick / "
        f"override). Follow the existing cleanup planner's decision; a later "
        f"re-trigger can provision a fresh poller.\n"
        f"   • idle     → nothing in-flight; stay silent.\n"
        f"Silently journal: `journal --release {release} --source scout --kind automation "
        f"--text \"rc-poller: <decision>\"`. Stay silent when there is nothing to send.")
