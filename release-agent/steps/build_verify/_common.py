"""Shared config + helpers for the Phase 2 (build_verify) release-verification steps.

Underscore-prefixed so steps.discover() skips it (it's not a step). Holds the ADO
coordinates for the three Engineering release pipelines and the recovery / escalation
links surfaced when a step blocks, plus small resolvers the step modules reuse.
"""
from __future__ import annotations

ORG = "https://identitydivision.visualstudio.com"
PROJECT = "Engineering"

CHECKER_DEF = 3038          # Code Complete Calendar Checker (fires the release on the CCD)
ORCHESTRATOR_DEF = 2828     # Release Orchestrator (the spine)
MRWP_DEF = 2519             # Monthly Release Work Pipeline (RC testing; runs ECS + Local)

# The orchestrator stages that must be green before RC testing is trustworthy, and the
# stage it should be PARKED at (a human approval gate the owner clears in a later phase).
ORCH_REQUIRED_STAGES = [
    "Validate Branch and Versions availability",
    "Create Release Branches",
    "Trigger RC Testing",
]
ORCH_PARK_STAGE = "Remove RC Tags"

# Surfaced in every block reason so the engineer knows how to recover / escalate.
RECOVERY_TSG = ("https://eng.ms/docs/microsoft-security/identity/"
                "entra-developer-application-platform/auth-client/"
                "authn-sdk-msal-android/android-auth-libraries/releases/"
                "internal-release-checklist/release-orchestrator-recovery")
ESCALATION_CHAT = ("https://teams.microsoft.com/l/chat/"
                   "19:976a859f167f44e59c4ceca8b1d23581@thread.v2/conversations")

# Standard help tail appended to orchestrator/MRWP block reasons.
UNBLOCK_HELP = (
    "\n→ Each failed stage's output describes the root cause + corrective action. "
    "Follow it, then click Retry on the failed stage. Recovery TSG: "
    f"{RECOVERY_TSG} . If unresolved within 2h, escalate: {ESCALATION_CHAT}")


def build_url(build_id):
    return f"{ORG}/{PROJECT}/_build/results?buildId={build_id}"


def links_for(build_id, name="ADO run"):
    return [{"name": name, "url": build_url(build_id)}]


def stash_runs(state, **ids):
    """Record resolved pipeline run ids on state.pipeline_runs (drop None values),
    stamped with resolved_at. Called each time Phase 2 resolves the chain so the ids
    are in state (for status details + the digest). Re-resolved on every pass, so a
    re-triggered MRWP run (new id) overwrites the old one."""
    from datetime import datetime, timezone
    pr = dict(getattr(state, "pipeline_runs", {}) or {})
    for k, v in ids.items():
        if v is not None:
            pr[k] = str(v)
    pr["resolved_at"] = datetime.now(timezone.utc).isoformat()
    state.pipeline_runs = pr


# ---------------------------------------------------------------- RC report email
def rc_report_model(state, timeout=120):
    """The full Phase-2 RC report model (checker → orchestrator → ECS/Local MRWP +
    per-run test breakdown) for this release. Pure read; see tools.pipelines."""
    from tools import pipelines as P
    return P.release_report(ORG, PROJECT, state.release_id,
                            checker_def=CHECKER_DEF, orch_def=ORCHESTRATOR_DEF, timeout=timeout)


def rc_email_subject(model) -> str:
    rid = model.get("release", "?")
    return (f"Release {rid} — RC verification report (Phase 2) · "
            f"action: approve to proceed to bug bash")


def _rc_email_plain(model, ctx) -> str:
    """Plain-text form of the RC report email (fallback + logging)."""
    L = []
    rid = model.get("release", "?")
    o = model.get("orchestrator") or {}
    v = o.get("versions") or {}
    vstr = ", ".join(f"{k} {v[k]}" for k in ("Common", "Msal", "Broker") if v.get(k)) or "n/a"
    L.append(f"Hi {ctx.get('owner', 'there')},")
    L.append("")
    L.append(f"The Release Candidate for {rid} has been built and RC testing has "
             f"completed. Review the results below and approve 'RC verified — proceed "
             f"to bug bash' when ready.")
    L.append("")
    ch = model.get("checker") or {}
    L.append("PIPELINE HEALTH")
    L.append(f"  - Code Complete Checker: fired the release (run {ch.get('run_id')}).")
    park = ("parked at 'Remove RC Tags' (awaiting approval, later phase)"
            if o.get("parked") else "gate already cleared")
    L.append(f"  - Release Orchestrator: healthy — pre-gate stages green, {park}.")
    L.append(f"      Versions: {vstr}")
    L.append(f"      {build_url(o.get('run_id'))}")
    L.append("")
    L.append("RC TESTING — both provider runs ran to completion:")
    for prov in ("ECS", "Local"):
        r = (model.get("mrwp") or {}).get(prov) or {}
        t = r.get("tests") or {}
        L.append(f"  MRWP {prov} — run {r.get('run_id')} ({r.get('ran')}/{r.get('total')} stages)")
        if t:
            L.append(f"      Tests: {t.get('passed')}/{t.get('total')} passed, {t.get('failed')} FAILED")
        fs = r.get("failed_stages") or []
        if fs:
            L.append(f"      Red stages ({len(fs)}): {', '.join(fs)}")
        for s in (r.get("failed_suites") or []):
            L.append(f"      {s['name']} — {s['failed']} failed / {s['total']}:")
            names = s.get("tests", [])
            for tname in names[:12]:
                L.append(f"          - {tname}")
            if len(names) < s["failed"]:
                L.append(f"          … and {s['failed'] - len(names)} more (see the run)")
        L.append(f"      {build_url(r.get('run_id'))}")
        L.append("")
    probs = model.get("problems") or []
    if probs:
        L.append("BLOCKING ISSUES (a stage that never ran = the pipeline aborted):")
        L += [f"  - {p}" for p in probs]
        L.append("")
    L.append("NEXT: review the failing tests above. If they're acceptable to carry into "
             "bug bash, approve the gate (advances to Phase 3 — Test / Bug Bash). "
             "Otherwise investigate the red suites first.")
    L.append("")
    L.append("— Release Orchestrator (Scout)")
    return "\n".join(L)


def _rc_email_html(model, ctx) -> str:
    """Email-safe HTML form of the RC report (inline styles; Outlook-friendly)."""
    from steps.lib import templating as T
    rid = model.get("release", "?")
    o = model.get("orchestrator") or {}
    v = o.get("versions") or {}
    vstr = ", ".join(f"{k} {v[k]}" for k in ("Common", "Msal", "Broker") if v.get(k)) or "n/a"
    ch = model.get("checker") or {}
    park = ("parked at &lsquo;Remove RC Tags&rsquo; (awaiting approval, later phase)"
            if o.get("parked") else "gate already cleared")

    def mrwp_block(prov):
        r = (model.get("mrwp") or {}).get(prov) or {}
        t = r.get("tests") or {}
        fs = r.get("failed_stages") or []
        tline = (f"<strong>{t.get('passed')}/{t.get('total')}</strong> passed, "
                 f"<strong style='color:#b42318;'>{t.get('failed')} failed</strong>" if t else "n/a")
        red = (f"<div style='margin:4px 0;color:#b42318;font-size:13px;'>Red stages "
               f"({len(fs)}): {T.esc(', '.join(fs))}</div>" if fs else "")
        # Failing tests grouped by suite (repeated runs merged), each with its test names.
        suite_html = ""
        for s in (r.get("failed_suites") or []):
            names = s.get("tests", [])
            items = "".join(
                f"<li style='margin:1px 0;color:#475467;'>{T.esc(n)}</li>" for n in names[:12])
            more = (f"<li style='margin:1px 0;color:#98a2b3;list-style:none;'>… and "
                    f"{s['failed'] - len(names)} more (see the run)</li>"
                    if len(names) < s["failed"] else "")
            suite_html += (
                f"<div style='margin:6px 0;'>"
                f"<div style='font-size:13px;'><strong style='color:#b42318;'>{s['failed']}</strong>"
                f"<span style='color:#667085;'> / {s['total']}</span> &nbsp;{T.esc(s['name'])}</div>"
                f"<ul style='margin:2px 0 0 20px;padding:0;font-size:12px;font-family:Consolas,monospace;'>"
                f"{items}{more}</ul></div>")
        return (f"<div style='margin:10px 0;padding:10px 12px;border:1px solid #e4e7ec;border-radius:6px;'>"
                f"<div style='font-weight:600;'>MRWP {prov} — run {r.get('run_id')} "
                f"<span style='color:#667085;font-weight:400;'>({r.get('ran')}/{r.get('total')} stages)</span></div>"
                f"<div style='margin:4px 0;'>Tests: {tline}</div>{red}{suite_html}"
                f"<div style='margin-top:6px;'><a href='{build_url(r.get('run_id'))}' "
                f"style='color:#0b5cad;font-size:13px;'>open run {r.get('run_id')}</a></div></div>")

    probs = model.get("problems") or []
    issues = (("<div style='margin:12px 0;padding:10px 12px;background:#fef3f2;border:1px solid #fda29b;"
               "border-radius:6px;color:#b42318;'><strong>Blocking issues</strong> (a stage that never "
               "ran = pipeline aborted):<ul style='margin:6px 0 0 18px;'>"
               + "".join(f"<li>{T.esc(p)}</li>" for p in probs) + "</ul></div>")
              if probs else "")

    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;font-size:14px;color:#101828;line-height:1.5;max-width:720px;">
  <p>Hi {T.esc(ctx.get('owner','there'))},</p>
  <p>The Release Candidate for <strong>{T.esc(rid)}</strong> has been built and RC testing has completed.
     Review the results below and approve <strong>&ldquo;RC verified &mdash; proceed to bug bash&rdquo;</strong> when ready.</p>
  <p style="margin:14px 0 4px;font-weight:600;">Pipeline health</p>
  <ul style="margin:0 0 6px 18px;padding:0;">
    <li>Code Complete Checker: fired the release (run {ch.get('run_id')}).</li>
    <li>Release Orchestrator: healthy &mdash; pre-gate stages green, {park}.<br>
        <span style="color:#667085;">Versions: {T.esc(vstr)}</span> &middot;
        <a href="{build_url(o.get('run_id'))}" style="color:#0b5cad;">run {o.get('run_id')}</a></li>
  </ul>
  <p style="margin:14px 0 4px;font-weight:600;">RC testing &mdash; both provider runs ran to completion</p>
  {mrwp_block('ECS')}
  {mrwp_block('Local')}
  {issues}
  <p><strong>Next:</strong> if the failures are acceptable to carry into bug bash, approve the gate
     (the release advances to Phase 3 &mdash; Test / Bug Bash). Otherwise investigate the red suites first.</p>
  <p style="color:#667085;">&mdash; Release Orchestrator (Scout)</p>
</div>"""


def rc_email(state):
    """Compose the RC verification email (subject, html, plain) for this release from
    LIVE pipeline data. Returns (subject, html, plain, model)."""
    from steps.lib.context import release_ctx
    model = rc_report_model(state)
    ctx = release_ctx(state)
    return (rc_email_subject(model), _rc_email_html(model, ctx),
            _rc_email_plain(model, ctx), model)


def verify_mrwp(state, provider):
    """Shared body for the mrwp_ecs / mrwp_local steps. `provider` is 'ECS' or 'Local'.

    Resolves this release's MRWP (def 2519) run for the provider — from the orchestrator
    run's RC-<provider>=<id> tag, or a log-parse fallback — then applies the release
    stage-completion rule (every stage must have executed; skipped/canceled/pending =
    block) and attaches the Test-tab summary. Uses the step's mock knobs when present:
      mrwp_id : inject the MRWP build id (skip the orchestrator lookup)
      stages  : inject the stage list [{name,state,result}]
      tests   : inject the test summary {total,passed,failed[,runs]}
    Returns a Done/Blocked outcome.
    """
    from orchestrator.outcomes import Done, Blocked
    from steps.lib.mockctx import mock_input, MISSING
    from tools import pipelines as P

    label = f"MRWP {provider}"
    # 1) resolve the MRWP build id for this provider
    mid = mock_input("mrwp_id", MISSING)
    if mid is MISSING:
        ok, run, detail = P.find_orchestrator_run(ORG, PROJECT, ORCHESTRATOR_DEF, state.release_id)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read orchestrator run ({detail}){hint}.")
        if not run:
            return Blocked(
                f"{label}: no orchestrator run found for {state.release_id} — can't locate "
                f"the RC-testing runs. Verify the orchestrator first.")
        ok2, ids, detail2, source = P.mrwp_run_ids(ORG, PROJECT, run)
        if not ok2:
            hint = " — run `az login`" if str(detail2).startswith("AUTH") else ""
            return Blocked(
                f"{label}: could not resolve the MRWP run id ({detail2}){hint}.",
                links=links_for(run.get("id"), "Release Orchestrator run"))
        mid = ids.get(provider)
        if not mid:
            return Blocked(f"{label}: orchestrator didn't record a {provider} RC-testing run.")

    links = links_for(mid, f"{label} run")

    # 2) stage-completion rule
    stages = mock_input("stages", MISSING)
    if stages is MISSING:
        ok, stages, detail = P.get_stages(ORG, PROJECT, mid)
        if not ok:
            hint = " — run `az login`" if str(detail).startswith("AUTH") else ""
            return Blocked(f"{label}: could not read stages for run {mid} ({detail}){hint}.", links=links)
    comp = P.stage_completion(stages)
    if not comp["complete"]:
        never = ", ".join(n for n in comp["never_ran"] if n) or "(unknown)"
        return Blocked(
            f"{label} run {mid} did NOT run to completion — {len(comp['never_ran'])} stage(s) "
            f"never ran (pending/skipped/canceled): {never}. A stage that never ran means the "
            f"pipeline aborted partway.{UNBLOCK_HELP}", links=links)

    # 3) test summary (best-effort — never blocks; red/yellow tests are triaged later)
    tests = mock_input("tests", MISSING)
    if tests is MISSING:
        ok, tests, _ = P.get_test_summary(ORG, PROJECT, mid)
        if not ok:
            tests = None
    tnote = ""
    if tests:
        tnote = f" Tests: {tests['passed']}/{tests['total']} passed, {tests['failed']} failed."
    stage_note = f"{comp['ran']}/{comp['total']} stages ran"
    extras = []
    if comp["failed"]:
        extras.append(f"{len(comp['failed'])} red")
    if comp["yellow"]:
        extras.append(f"{len(comp['yellow'])} yellow")
    extra = f" ({', '.join(extras)} — triaged later)" if extras else ""
    stash_runs(state, **{f"mrwp_{provider.lower()}": mid})
    return Done(
        f"{label} run {mid} ran to completion — {stage_note}{extra}.{tnote}", links=links)
