"""Step: `integ_prs` — auto-create the release integration/freeze PRs (Phase 4, finalize, F2).

After `gate_watch` approves "Remove RC Tags", the orchestrator creates the
`release-integration/<v>` branches (and earlier cut `release/<v>` + `working/release/<v>`).
Today the release owner opens EIGHT PRs by hand off the compare links the pipeline prints.
This step opens them — 2 per repo across 4 repos / 3 hosts:

  A. FREEZE      working/release/<v> -> release/<v>          (clean direct merge)
  B. INTEGRATION release-integration/<v> -> dev | working    (curated: see below)

For the INTEGRATION PRs the step also "helps the human": it brings the RI branch
up to date with the target, reverts build.gradle changes (so the target stays
DYNAMIC — only version + changelog transfer), and surfaces any remaining conflict
for a person. One shared PBI is created and referenced (AB#<id>) in every PR body.

This step is PREVIEW-FIRST: `build()` only READS — it computes the full plan (branch
existence, existing-vs-new PR detection, per-RI edit analysis) and returns it for
review. The actual writes (create PBI, edit RI, open PRs, add labels) happen in the
`create-integration-prs` command, which supports `--dry-run`. Idempotent: an existing
open PR for a head->base is reused, never duplicated.

Gating: if the branches aren't all present yet (orchestrator still finishing the
Remove-RC-Tags stage), the step reports in-progress and is re-checked later.

Mock knobs (mocks.local.yaml / tests):
  versions : dict repo->version, e.g. {msal: "8.4.2"} — override state.versions for testing.
  branches : dict repo->{wr,r,ri,target} — override computed branch names (test on fakes).
  repos    : list of repo keys to include (default all) — e.g. ["msal"].
  pbi      : PBI work-item id to reference (skip creating one); "skip" = omit AB# line.
"""
from __future__ import annotations

from orchestrator.outcomes import InProgress, NeedsSkill, Blocked
from steps.lib.agent import legacy_run
from steps.lib.mockctx import mock_input, MISSING
from tools import prs as PR
from tools import pipelines as PL
from tools.coordinates import coords

ID = "integ_prs"
KIND = "agent"

# The authenticator repo lives in msazure/One; its coordinates come from coordinates.yaml.
_AUTH_REPO = coords.repo("authenticator")

# Shared branch prefixes (authenticator overrides wr_prefix + target below).
RELEASE_PREFIX = "release/"
WORKING_PREFIX = "working/release/"
INTEG_PREFIX = "release-integration/"

# The orchestrator stage that CREATES the release-integration branches. integ_prs must not
# run before this stage completes — the RI branches (and thus the integration PRs) don't
# exist until it does. This is the authoritative trigger, NOT merely gate_watch passing.
IR_STAGE = "Create PRs to Integrate Release Branches"

# The 4 release repos. `tool` selects the PR backend: 'gh' (github.com / GHE) or 'ado'.
# `gh_repo` is what we pass to `gh --repo` (bare owner/repo = github.com; host/owner/repo = GHE).
CONFIG = {
    "common": {
        "name": "common", "dir": "common", "tool": "gh",
        "gh_repo": "AzureAD/microsoft-authentication-library-common-for-android",
        "target": "dev", "labels": ["skip-coverage-check", "Skip-Consumers-Check"],
    },
    "msal": {
        "name": "msal", "dir": "msal", "tool": "gh",
        "gh_repo": "AzureAD/microsoft-authentication-library-for-android",
        "target": "dev", "labels": ["skip-coverage-check"],
    },
    "broker": {
        "name": "broker", "dir": "broker", "tool": "gh",
        "gh_repo": "msft.ghe.com/security/ad-accounts-for-android",
        "target": "dev", "labels": ["skip-coverage-check"],
    },
    "authenticator": {
        "name": "authenticator", "dir": "authenticator", "tool": "ado",
        "ado": {"org": _AUTH_REPO["org"].rstrip("/") + "/", "project": _AUTH_REPO["project"],
                "repository": _AUTH_REPO["name"]},
        # authenticator's mainline is `working` (not dev) and its WR prefix is hyphenated.
        "target": "working", "wr_prefix": "working-release/", "labels": [],
    },
}

REPO_ORDER = ["common", "msal", "broker", "authenticator"]

MOCKABLE = {
    "versions": {"kind": "input", "desc": "dict repo->version (override state.versions for testing)."},
    "branches": {"kind": "input", "desc": "dict repo->{wr,r,ri,target} to override branch names."},
    "repos": {"kind": "input", "desc": "list of repo keys to include (default all)."},
    "pbi": {"kind": "input", "desc": "PBI id to reference (skip create); 'skip' = no AB# line."},
    "stage": {"kind": "input", "desc": "inject the IR stage state: 'ready'|'wait'|'failed' "
                                       "(skip the live orchestrator monitor)."},
}


# --------------------------------------------------------------------------- helpers
def _selected_repos():
    sel = mock_input("repos", MISSING)
    keys = list(sel) if sel is not MISSING and sel else REPO_ORDER
    return [k for k in REPO_ORDER if k in keys]


def _versions(state):
    """repo_key -> version. SOURCE OF TRUTH is state.versions (populated at Phase 2 by
    build_verify.orchestrator_health). The `versions` mock overrides for testing;
    discover_versions is only a last-resort fallback if state has none yet (it shouldn't,
    since integ_prs runs in Phase 4, well after Phase 2)."""
    v = mock_input("versions", MISSING)
    if v is not MISSING and v:
        return dict(v)
    if getattr(state, "versions", None):
        return {k: val for k, val in state.versions.items() if val}
    try:
        ok, versions, _detail = PL.discover_versions(
            PL.ENGINEERING_ORG, PL.ENGINEERING_PROJECT, getattr(state, "release_id", ""))
    except Exception:  # noqa: BLE001 — never crash the engine on a network hiccup
        return {}
    return {k: val for k, val in (versions or {}).items() if val} if ok else {}


def _branches(repo_key, cfg, version):
    """Resolve the {wr, r, ri, target} branch names for a repo, honoring the `branches` mock
    override. For authenticator, state stores its release BRANCH (release/YYYY/MM/DD) rather
    than a semver, so strip the 'release/' prefix to get the branch token."""
    ov = (mock_input("branches", MISSING) or {})
    ov = ov.get(repo_key, {}) if isinstance(ov, dict) else {}
    wr_prefix = cfg.get("wr_prefix", WORKING_PREFIX)
    token = version
    if repo_key == "authenticator" and version and str(version).startswith(RELEASE_PREFIX):
        token = str(version)[len(RELEASE_PREFIX):]     # release/2026/08/22 -> 2026/08/22
    return {
        "wr": ov.get("wr", f"{wr_prefix}{token}"),
        "r": ov.get("r", f"{RELEASE_PREFIX}{token}"),
        "ri": ov.get("ri", f"{INTEG_PREFIX}{token}"),
        "target": ov.get("target", cfg["target"]),
    }


def _pbi_ref():
    """(mode, value) — how the PBI is referenced. ('existing', id) reuses an id;
    ('skip', None) omits the AB# line; ('create', None) means the command must create one."""
    p = mock_input("pbi", MISSING)
    if p is MISSING:
        return ("create", None)
    if str(p).lower() == "skip":
        return ("skip", None)
    return ("existing", str(p))


def _title(kind, version):
    return (f"Release integration/{version}" if kind == "integration"
            else f"Working/release/{version}")


def pr_body(repo_key, kind, br, pbi_id):
    """PR description skeleton (owner fills details later). Includes the shared PBI ref."""
    ab = f"\nAB#{pbi_id}\n" if pbi_id else "\n"
    if kind == "integration":
        what = (f"Merge `{br['ri']}` into `{br['target']}` to bring this release's version "
                f"+ changelog back. build.gradle changes are reverted so `{br['target']}` "
                f"stays dynamic.")
        checks = ("- [x] RI brought up to date with target\n"
                  "- [x] build.gradle reverted (dynamic preserved)\n"
                  "- [ ] <add details>")
    else:
        what = f"Merge `{br['wr']}` into `{br['r']}` to freeze & tag the release (direct merge)."
        checks = "- [ ] <add details>"
    return (f"## {repo_key} — {'Integration' if kind=='integration' else 'Freeze'}\n"
            f"{ab}\n### What this PR does\n{what}\n\n"
            f"### Scout automation (release-agent · integ_prs)\n{checks}\n\n"
            f"<!-- Scout-generated. Edit freely. -->\n")


# --------------------------------------------------------------------------- planning
def _plan_repo(repo_key, version):
    """Read-only plan for one repo: resolve branches, check existence, detect existing PRs,
    and (for gh repos) analyze the RI edit. Returns a dict (never raises)."""
    cfg = CONFIG[repo_key]
    br = _branches(repo_key, cfg, version)
    out = {"key": repo_key, "tool": cfg["tool"], "version": version, "branches": br,
           "labels": cfg.get("labels", []), "prs": [], "notes": []}

    # branch existence (git ls-remote against the on-disk clone)
    exists = {}
    for role in ("wr", "r", "ri", "target"):
        ok, present, detail = PR.remote_branch_exists(cfg["dir"], br[role])
        exists[role] = present if ok else None
        if not ok:
            out["notes"].append(f"branch check failed for {br[role]}: {detail}")
    out["branch_exists"] = exists

    specs = [("freeze", br["wr"], br["r"]), ("integration", br["ri"], br["target"])]
    for kind, head, base in specs:
        pr = {"kind": kind, "head": head, "base": base,
              "title": _title(kind, version), "labels": cfg.get("labels", []),
              "head_exists": exists.get("wr" if kind == "freeze" else "ri"),
              "base_exists": exists.get("r" if kind == "freeze" else "target"),
              "existing": None}
        # existing-PR detection (idempotency) — tool-specific.
        if cfg["tool"] == "gh":
            ok, found, detail = PR.gh_find_open_pr(cfg["gh_repo"], head, base)
        else:
            a = cfg["ado"]
            ok, found, detail = PR.az_find_open_pr(a["org"], a["project"], a["repository"],
                                                   head, base)
        pr["existing"] = found if ok else None
        if not ok:
            pr["note"] = f"PR lookup failed: {detail}"
        # RI edit analysis (read-only) — same local-git logic for every host.
        if kind == "integration" and exists.get("ri") and exists.get("target"):
            pr["ri_analysis"] = _ri_analysis(cfg["dir"], head, base)
        out["prs"].append(pr)
    return out


def _ri_analysis(dir_name, ri, target):
    """Read-only 'what would the RI edit do' summary."""
    a = {}
    okb, n, _d = PR.behind_count(dir_name, ri, target)
    a["behind_target"] = n if okb else None
    okg, files, _d2 = PR.gradle_diff_files(dir_name, ri, target)
    a["gradle_to_revert"] = files if okg else None
    okm, conflicts, _d3 = PR.merge_conflict_preview(dir_name, ri, target)
    # conflicts that are NOT just build.gradle are the ones a human must resolve.
    if okm:
        gradle = set(files or [])
        a["conflicts"] = conflicts
        a["human_conflicts"] = [c for c in conflicts if c not in gradle]
    else:
        a["conflicts"] = None
        a["human_conflicts"] = None
    return a


def plan(state):
    """Full read-only preview across all selected repos."""
    pbi_mode, pbi_id = _pbi_ref()
    versions = _versions(state)
    repos = []
    missing = []
    for key in _selected_repos():
        v = versions.get(key)
        if not v:
            repos.append({"key": key, "tool": CONFIG[key]["tool"], "version": None,
                          "notes": ["no version resolved (provide via `versions` mock or "
                                    "orchestrator vars)"], "prs": []})
            missing.append(f"{key}: version")
            continue
        rp = _plan_repo(key, v)
        repos.append(rp)
        # a branch that must exist but doesn't -> not ready yet
        for role, label in (("wr", "working/release"), ("r", "release"),
                            ("ri", "release-integration"), ("target", rp["branches"]["target"])):
            if rp.get("branch_exists", {}).get(role) is False:
                missing.append(f"{key}: {rp['branches'][role]}")
    return {"pbi_mode": pbi_mode, "pbi_id": pbi_id, "versions": versions,
            "repos": repos, "missing": missing, "ready": not missing}


# --------------------------------------------------------------------------- outcome
def _ir_stage_status(state):
    """('ready'|'wait'|'failed'|'unknown', detail) — has the orchestrator's IR_STAGE completed?
    The release-integration branches don't exist until it does, so integ_prs must monitor it
    and only proceed on 'ready'. Mock-first via the `stage` knob; never raises."""
    inj = mock_input("stage", MISSING)
    if inj is not MISSING:
        s = str(inj).lower()
        if s in ("ready", "completed", "succeeded", "true"):
            return ("ready", f"injected stage={inj}")
        if s in ("failed", "canceled", "cancelled"):
            return ("failed", f"injected stage={inj}")
        return ("wait", f"injected stage={inj}")
    try:
        ok, st, detail = PL.orchestrator_stage_state(
            PL.ENGINEERING_ORG, PL.ENGINEERING_PROJECT, getattr(state, "release_id", ""), IR_STAGE)
    except Exception:  # noqa: BLE001 — never crash the engine on a network hiccup
        return ("unknown", "could not read the orchestrator stage")
    if not ok:
        return ("unknown", detail)
    if not st:
        return ("wait", detail)                       # stage not present on the run yet
    stt, res = st.get("state"), st.get("result")
    if stt != "completed":
        return ("wait", f"'{IR_STAGE}' is {stt or 'not started'}")
    if res in ("succeeded", "succeededWithIssues"):
        return ("ready", f"'{IR_STAGE}' completed ({res})")
    return ("failed", f"'{IR_STAGE}' completed with result={res}")


def build(state):
    if not _versions(state):
        return Blocked(
            "integ_prs: no release versions resolved. Provide them via the `versions` mock "
            "(e.g. {msal: '8.4.2'}) for testing, or wait for orchestrator version discovery.")

    # Gate on the orchestrator stage that creates the release-integration branches — NOT merely
    # on gate_watch passing. Monitor it and only proceed once it has completed successfully.
    status, detail = _ir_stage_status(state)
    if status == "failed":
        return Blocked(
            f"integ_prs: the Release Orchestrator '{IR_STAGE}' stage FAILED ({detail}) — the "
            "release-integration branches were not created. Investigate the orchestrator run "
            "before opening integration PRs.")
    if status != "ready":
        return InProgress(
            f"integ_prs: monitoring the Release Orchestrator — waiting for the '{IR_STAGE}' stage "
            f"to complete before the release-integration branches exist ({detail}).",
            poll_in_min=15)

    p = plan(state)
    if not p["ready"]:
        return InProgress(
            "integ_prs: the orchestrator IR stage is done but not all branches are visible yet — "
            f"missing: {', '.join(p['missing'])}. Re-checking on the next poll.", poll_in_min=15)

    n_new = sum(1 for r in p["repos"] for pr in r["prs"] if not pr.get("existing"))
    n_reuse = sum(1 for r in p["repos"] for pr in r["prs"] if pr.get("existing"))
    summary = (f"Open the release integration/freeze PRs "
               f"({n_new} to create, {n_reuse} already open) across "
               f"{len(p['repos'])} repo(s).")
    return NeedsSkill(
        tool="create-integration-prs",
        payload={
            "release": state.release_id,
            "plan": p,
            "followup_command": f"create-integration-prs --release {state.release_id} --dry-run",
            "_gather": {"preview": render_preview(p)},
        },
        record_as=ID,
        summary=summary,
        note=f"{n_new} PRs to open, {n_reuse} already open",
        outbound=True,
    )


def render_preview(p) -> str:
    """Human-readable dry-run preview of the whole plan."""
    lines = ["integ_prs -- PREVIEW (read-only; no branches touched, no PRs opened)"]
    if p["pbi_mode"] == "create":
        lines.append("PBI:      would CREATE one shared PBI and reference it in every PR body")
    elif p["pbi_mode"] == "existing":
        lines.append(f"PBI:      reuse AB#{p['pbi_id']} in every PR body")
    else:
        lines.append("PBI:      (skipped -- no AB# line)")
    for r in p["repos"]:
        head = f"\n{r['key']} ({r['tool']})  version={r.get('version')}"
        lines.append(head)
        for note in r.get("notes", []):
            lines.append(f"  ! {note}")
        for pr in r.get("prs", []):
            tag = (f"REUSE #{pr['existing']['number']}" if pr.get("existing")
                   else "NEW")
            lbl = (" labels: " + ", ".join(pr["labels"])) if pr.get("labels") else ""
            lines.append(f"  {pr['kind']:<11} {pr['head']} -> {pr['base']}   [{tag}]{lbl}")
            if pr.get("note"):
                lines.append(f"      ! {pr['note']}")
            a = pr.get("ri_analysis")
            if a:
                behind = a.get("behind_target")
                grad = a.get("gradle_to_revert")
                hc = a.get("human_conflicts")
                lines.append(
                    f"      RI edit: behind target by {behind if behind is not None else '?'} "
                    f"commit(s); revert {len(grad) if grad is not None else '?'} build.gradle; "
                    f"human conflicts: {len(hc) if hc is not None else '?'}")
                for c in (hc or [])[:5]:
                    lines.append(f"        !! conflict: {c}")
    if not p["ready"]:
        lines.append(f"\nNOT READY — missing: {', '.join(p['missing'])}")
    return "\n".join(lines)


run = legacy_run(build)
