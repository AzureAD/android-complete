"""Release-agent tests — finalize. Shared harness in tests/_harness.py."""
from tests._context import context as _context, invoke as _invoke, invoke_effect as _invoke_effect
from tests._context import approval as _approval
from tests._harness import *  # noqa: F401,F403




def test_remove_rc_tags_gate_build_shows_pending_brief():
    """build() with an injected pending approval → a needs_human brief naming the build, the
    stage, and the publish consequences."""
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08")
    info = {"approval_id": "A1", "build_id": 1681228, "stage": "Remove RC Tags", "build_url": "u"}
    with mockctx.active({
        "approval": info,
        "stage_state": {"state": "pending", "result": None},
    }):
        out = as_dict(_invoke(gw.build, st))
    assert out["kind"] == "needs_human"
    assert "1681228" in out["prompt"] and "Remove RC Tags" in out["prompt"]
    assert "Maven Central" in out["prompt"]




def test_remove_rc_tags_gate_build_holds_when_not_parked():
    """No approval and an incomplete stage must hold; absence never skips the gate."""
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({
        "approval": None,
        "stage_state": {"state": "notStarted", "result": None},
    }):
        out = as_dict(_invoke(gw.build, st))
    assert out["kind"] == "needs_human"
    assert "isn't parked yet" in out["prompt"] and "remains incomplete" in out["prompt"]


def test_remove_rc_tags_gate_build_done_only_after_stage_succeeds():
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict

    st = ReleaseState(release_id="2026-08")
    with mockctx.active({
        "approval": None,
        "stage_state": {"state": "completed", "result": "succeeded"},
    }):
        out = as_dict(_invoke(gw.build, st))
    assert out["kind"] == "done" and "completed successfully" in out["note"]


def test_remove_rc_tags_gate_build_blocks_failed_stage():
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict

    st = ReleaseState(release_id="2026-08")
    with mockctx.active({
        "approval": None,
        "stage_state": {"state": "completed", "result": "failed"},
    }):
        out = as_dict(_invoke(gw.build, st))
    assert out["kind"] == "blocked" and "result 'failed'" in out["reason"]




def test_remove_rc_tags_gate_submit_approval_submits():
    """Preparation freezes the pending identity/comment; submission calls the injected writer."""
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx
    from tools import pipelines as P
    st = ReleaseState(release_id="2026-08")
    sent = {}

    def fake_submit(org, project, approval_id, comment="", status="approved", timeout=60):
        sent.update(org=org, project=project, id=approval_id, comment=comment)
        return (True, f"approval {approval_id} -> approved")

    o = P.submit_pipeline_approval
    P.submit_pipeline_approval = fake_submit
    try:
        info = {"approval_id": "A1", "build_id": 123, "stage": "Remove RC Tags", "build_url": "u"}
        with mockctx.active({
            "approval": info,
            "stage_state": {"state": "pending", "result": None},
        }):
            ok, detail = _approval(gw.submit_approval, st, "go")
    finally:
        P.submit_pipeline_approval = o
    assert ok and detail == "approval A1 -> approved"
    assert sent == {"org": gw.CONFIG["org"], "project": gw.CONFIG["project"],
                    "id": "A1", "comment": "go"}


def test_remove_rc_tags_gate_submit_approval_rejects_different_stage():
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx
    from tools import pipelines as P

    st = ReleaseState(release_id="2026-08")
    original = P.submit_pipeline_approval
    P.submit_pipeline_approval = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("wrong stage must not be submitted")
    )
    try:
        info = {
            "approval_id": "A2",
            "build_id": 124,
            "stage": "Publish GitHub Release Notes",
            "build_url": "u",
        }
        with mockctx.active({
            "approval": info,
            "stage_state": {"state": "pending", "result": None},
        }):
            ok, detail = _approval(gw.submit_approval, st, "go")
    finally:
        P.submit_pipeline_approval = original
    assert not ok
    assert "not 'Remove RC Tags'" in detail


def test_remove_rc_tags_gate_submit_refuses_completed_stage_without_approval_identity():
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx

    st = ReleaseState(release_id="2026-08")
    with mockctx.active({
        "approval": None,
        "stage_state": {"state": "completed", "result": "succeeded"},
    }):
        ok, detail = _approval(gw.submit_approval, st, "go")
    assert not ok
    assert "No identifiable pending approval" in detail
    assert "Stage completion alone" in detail


def test_remove_rc_tags_gate_refuses_later_gate_even_when_original_stage_completed():
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx

    st = ReleaseState(release_id="2026-08")
    later = {
        "approval_id": "A2",
        "build_id": 125,
        "stage": "Publish GitHub Release Notes",
        "build_url": "u",
    }
    with mockctx.active({
        "approval": later,
        "stage_state": {"state": "completed", "result": "succeeded"},
    }):
        ok, detail = _approval(gw.submit_approval, st, "recover")
    assert not ok
    assert "not 'Remove RC Tags'" in detail




def test_remove_rc_tags_gate_submit_approval_skip_knob():
    """Legacy submit: skip blocks preparation; it never fabricates provider success."""
    from steps.finalize import remove_rc_tags_gate as gw
    from steps.lib import mockctx
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"approval": {"approval_id": "A1", "build_id": 1, "stage": "x", "build_url": "u"},
                         "submit": "skip"}):
        ok, detail = _approval(gw.submit_approval, st, "go")
    assert not ok and "submit='skip' cannot authorize" in detail




# ---- Phase 4: publish_notes_gate (2nd orchestrator gate — 'Publish GitHub Release Notes') ----

def test_publish_notes_gate_brief_when_parked_at_notes():
    """build() with the notes stage parked → a needs_human brief naming the stage + consequence."""
    from steps.finalize import publish_notes_gate as PG
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08")
    info = {"approval_id": "A2", "build_id": 1678611, "stage": "Publish GitHub Release Notes", "build_url": "u"}
    with mockctx.active({"approval": info, "stage_state": {"state": "inProgress", "result": None}}):
        out = as_dict(_invoke(PG.build, st))
    assert out["kind"] == "needs_human"
    assert "Publish GitHub Release Notes" in out["prompt"] and "1678611" in out["prompt"]
    assert "GitHub release notes" in out["prompt"]




def test_publish_notes_gate_done_when_stage_completed():
    """If the notes stage already completed → Done (nothing to approve)."""
    from steps.finalize import publish_notes_gate as PG
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"stage_state": {"state": "completed", "result": "succeeded"}}):
        out = as_dict(_invoke(PG.build, st))
    assert out["kind"] == "done" and "already completed" in out["note"]




def test_publish_notes_gate_warns_when_parked_elsewhere():
    """Parked at a DIFFERENT stage (e.g. Remove RC Tags) → holds with a warning, not the gate."""
    from steps.finalize import publish_notes_gate as PG
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08")
    info = {"approval_id": "A1", "build_id": 1, "stage": "Remove RC Tags", "build_url": "u"}
    with mockctx.active({"approval": info, "stage_state": {"state": "notStarted", "result": None}}):
        out = as_dict(_invoke(PG.build, st))
    assert out["kind"] == "needs_human"
    assert "Remove RC Tags" in out["prompt"] and "only approves" in out["prompt"]




def test_publish_notes_gate_waits_when_not_parked():
    """Nothing parked + notes not completed → holds ('isn't parked yet')."""
    from steps.finalize import publish_notes_gate as PG
    from steps.lib import mockctx
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"approval": None, "stage_state": None}):
        out = as_dict(_invoke(PG.build, st))
    assert out["kind"] == "needs_human" and "isn't parked yet" in out["prompt"]




def test_publish_notes_gate_submit_verifies_stage():
    """Prepare/submit freezes ONLY the notes-stage approval and forwards its comment."""
    from steps.finalize import publish_notes_gate as PG
    from steps.lib import mockctx
    from tools import pipelines as P
    st = ReleaseState(release_id="2026-08")
    sent = {}

    def fake_submit(org, project, approval_id, comment="", status="approved", timeout=60):
        sent.update(org=org, project=project, id=approval_id, comment=comment)
        return (True, f"approval {approval_id} -> approved")

    o = P.submit_pipeline_approval
    P.submit_pipeline_approval = fake_submit
    try:
        # right stage → submits
        good = {"approval_id": "A2", "build_id": 5, "stage": "Publish GitHub Release Notes", "build_url": "u"}
        with mockctx.active({"approval": good}):
            ok, detail = _approval(PG.submit_approval, st, "go")
        assert ok and detail == "approval A2 -> approved"
        assert sent == {"org": PG.CONFIG["org"], "project": PG.CONFIG["project"],
                        "id": "A2", "comment": "go"}
        # wrong stage → refuses (no submit)
        sent.clear()
        bad = {"approval_id": "A1", "build_id": 5, "stage": "Remove RC Tags", "build_url": "u"}
        with mockctx.active({
            "approval": bad,
            "stage_state": {"state": "pending", "result": None},
        }):
            ok2, detail2 = _approval(PG.submit_approval, st, "go")
        assert ok2 is False and "not 'Publish GitHub Release Notes'" in detail2 and "id" not in sent
        # not parked → refuses
        with mockctx.active({
            "approval": None,
            "stage_state": {"state": "pending", "result": None},
        }):
            ok3, detail3 = _approval(PG.submit_approval, st, "go")
        assert ok3 is False and "No identifiable pending approval" in detail3
    finally:
        P.submit_pipeline_approval = o




def test_publish_notes_gate_config_is_gate_after_integ_prs():
    """phases.yaml: publish_notes_gate is a human gate placed after integ_prs and before verify_pub."""
    import yaml as _yaml
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    fin = next(p for p in cfg["phases"] if p["id"] == "finalize")
    s = next(x for x in fin["steps"] if x["id"] == "publish_notes_gate")
    assert s.get("owner") == "human" and s.get("gate") is True
    ids = [x["id"] for x in fin["steps"]]
    assert ids.index("integ_prs") < ids.index("publish_notes_gate") < ids.index("verify_pub")




def test_integ_prs_reads_versions_from_state():
    """integ_prs uses state.versions as the source of truth (no versions mock, no re-discovery),
    and derives authenticator branches from its stored release branch."""
    from steps.lib import mockctx
    from steps.finalize import integ_prs as S
    from tools import prs as P
    restore = _patch_pr_reads(P, exists=True, existing_pr=None, behind=0, gradle=[], conflicts=[])
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0",
                        "authenticator": "release/2026/08/22"})
    try:
        with mockctx.active({"pbi": "skip"}):        # NO versions mock -> must come from state
            p = S.plan(_context(st))
    finally:
        restore()
    by = {r["key"]: r for r in p["repos"]}
    assert by["msal"]["prs"][1]["head"] == "release-integration/8.4.2"
    auth = by["authenticator"]["prs"]
    assert auth[0]["head"] == "working-release/2026/08/22"        # WR token from the release branch
    assert auth[0]["base"] == "release/2026/08/22"                # R is the stored branch
    assert auth[1]["head"] == "release-integration/2026/08/22" and auth[1]["base"] == "working"




def test_integ_prs_plan_computes_8_prs_offline():
    """plan() computes 2 PRs for each of the 4 repos with the right head/base/labels and
    an RI-edit analysis for the integration PRs — all offline."""
    from steps.lib import mockctx
    from steps.finalize import integ_prs as S
    from tools import prs as P
    restore = _patch_pr_reads(P, exists=True, existing_pr=None, behind=3,
                              gradle=["msal/build.gradle"], conflicts=["msal/build.gradle", "changelog"])
    st = ReleaseState(release_id="2026-08")
    knobs = {"versions": {"common": "24.6.0", "msal": "8.4.2", "broker": "20.1.0",
                          "authenticator": "9.9.9"}, "pbi": "skip"}
    try:
        with mockctx.active(knobs):
            p = S.plan(_context(st))
    finally:
        restore()
    assert p["ready"] and len(p["repos"]) == 4
    by = {r["key"]: r for r in p["repos"]}
    # msal freeze + integration branches/targets
    msal = by["msal"]["prs"]
    assert msal[0]["kind"] == "freeze" and msal[0]["head"] == "working/release/8.4.2" and msal[0]["base"] == "release/8.4.2"
    assert msal[1]["kind"] == "integration" and msal[1]["head"] == "release-integration/8.4.2" and msal[1]["base"] == "dev"
    # authenticator integration targets 'working', not 'dev', with hyphenated WR prefix
    auth = by["authenticator"]["prs"]
    assert auth[1]["base"] == "working" and auth[0]["head"] == "working-release/9.9.9"
    # labels: common gets both; msal/broker just skip-coverage-check
    assert set(by["common"]["labels"]) == {"skip-coverage-check", "Skip-Consumers-Check"}
    assert by["msal"]["labels"] == ["skip-coverage-check"]
    # RI analysis separates the human conflict (changelog) from the build.gradle revert
    ri = msal[1]["ri_analysis"]
    assert ri["behind_target"] == 3 and ri["gradle_to_revert"] == ["msal/build.gradle"]
    assert ri["human_conflicts"] == ["changelog"]




def test_integ_prs_in_progress_when_branch_missing():
    """A missing required branch → InProgress (waiting on the orchestrator), not a crash."""
    from steps.lib import mockctx
    from steps.finalize import integ_prs as S
    from tools import prs as P
    restore = _patch_pr_reads(P, exists=False)
    st = ReleaseState(release_id="2026-08")
    try:
        with mockctx.active({"versions": {"msal": "8.4.2"}, "repos": ["msal"], "pbi": "skip",
                             "stage": "ready"}):
            out = _invoke(S.build, st)
    finally:
        restore()
    assert out.kind == "in_progress"




def test_integ_prs_monitors_ir_stage():
    """integ_prs gates on the orchestrator IR stage: not-done -> InProgress, failed -> Blocked,
    done + branches present -> the NeedsSkill action."""
    from steps.lib import mockctx
    from steps.finalize import integ_prs as S
    from tools import prs as P
    st = ReleaseState(release_id="2026-08")
    base = {"versions": {"msal": "8.4.2"}, "repos": ["msal"], "pbi": "skip"}
    # stage still running -> wait
    with mockctx.active({**base, "stage": "wait"}):
        assert _invoke(S.build, st).kind == "in_progress"
    # stage failed -> blocked (RI branches never created)
    with mockctx.active({**base, "stage": "failed"}):
        assert _invoke(S.build, st).kind == "blocked"
    # stage ready + branches exist -> needs_skill (the action)
    restore = _patch_pr_reads(P, exists=True, existing_pr=None, behind=0, gradle=[], conflicts=[])
    try:
        with mockctx.active({**base, "stage": "ready"}):
            assert _invoke(S.build, st).kind == "needs_skill"
    finally:
        restore()




def test_integ_prs_blocked_without_versions():
    """No versions resolved (and az blocked) → Blocked, no network crash."""
    from steps.lib import mockctx
    from steps.finalize import integ_prs as S
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({"repos": ["msal"]}):
        out = _invoke(S.build, st)
    assert out.kind == "blocked"


def test_step_action_persists_external_blocked_outcome():
    from argparse import Namespace
    from orchestrator.commands.step_action import prepare_step

    st = ReleaseState(release_id="2026-08")
    _active_step(st, "finalize", "integ_prs")
    orch = Orchestrator(CONFIG, st, mocks={})
    out = prepare_step(
        Namespace(
            phase="finalize",
            step="integ_prs",
            release=st.release_id,
            param=[],
            reserve=False,
            executor=None,
        ),
        st,
        orch,
    )
    assert out["kind"] == "blocked"
    assert out["state_changed"] is True
    assert st.get_step("finalize", "integ_prs").status == "blocked"




def _integration_provider(monkeypatch, *, behind=0, conflicts=()):
    from pathlib import Path
    from tools import git_review as G, prs as P

    tips, prs, writes = {}, {}, []
    tip = lambda name: tips.setdefault(name, "1" * 40)
    monkeypatch.setattr(G, "clean_repository", lambda root: Path(root))
    monkeypatch.setattr(G, "remote_url", lambda root: "https://example.test/msal.git")
    monkeypatch.setattr(G, "remote_tip", lambda root, remote, name: tip(name))
    monkeypatch.setattr(P, "provider_branch_object_id", lambda target, name: tip(name))
    monkeypatch.setattr(P, "provider_repository_urls", lambda target: ["https://example.test/msal.git"])
    monkeypatch.setattr(P, "gh_find_open_pr",
                        lambda repo, head, base: (True, prs.get((head, base)), ""))

    def plan_ri(root, head, base, target):
        if conflicts:
            raise ValueError("Human conflicts: " + ", ".join(conflicts))
        return {"behind": behind, "ahead": 0, "commit": "reviewed commit" if behind else None,
                "commit_id": "2" * 40 if behind else head, "tree": "3" * 40,
                "parents": [head, base], "message": "Merge and restore Gradle",
                "files_before": {"msal/build.gradle": "old"},
                "files_after": {"msal/build.gradle": "reviewed"},
                "gradle_reverted": ["msal/build.gradle"] if behind else []}

    def push(root, remote, head, expected, content, validate):
        validate()
        assert tips[head] == expected
        tips[head] = content["commit_id"]
        writes.append(("push", head))

    def create(repo, head, base, title, body, labels=None):
        url = f"https://example.test/pr/{len(prs) + 1}"
        prs[head, base] = {"number": len(prs) + 1, "title": title, "body": body,
                           "labels": list(labels or []), "url": url}
        writes.append((head, base))
        return True, url, ""

    monkeypatch.setattr(G, "plan_ri", plan_ri)
    monkeypatch.setattr(G, "push_reviewed", push)
    monkeypatch.setattr(P, "gh_create_pr", create)
    monkeypatch.setattr(P, "create_pbi",
                        lambda *a, **k: (writes.append(("pbi", 4242)) or
                                         (True, 4242, "https://example.test/wi/4242", "")))
    return writes


def _run_integration_review(ns):
    from orchestrator import write_review as W
    from orchestrator.commands import integ_prs_cmd as CMD
    from steps.lib import mockctx

    with C.state_lock(ns.runs_root, ns.release):
        _, orch = C.load_orch(ns.runs_root, ns.release, ns.config)
        with mockctx.active(CMD._step_mocks(orch)):
            plan = CMD.integration_plan(orch.context("finalize", "integ_prs"), ns)
        ns.review_hash = W.review_hash(orch, "finalize", "integ_prs", plan)
        return CMD.cmd_create_integration_prs(ns)


def test_create_integration_prs_dry_run_writes_nothing(monkeypatch):
    """The command's default (no --execute) prints the plan and performs NO writes."""
    import tempfile
    from orchestrator.commands import integ_prs_cmd as CMD
    writes = _integration_provider(monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        ns, restore_mocks = _integ_release(d, {"versions": {"msal": "8.4.2"},
                                             "repos": ["msal"], "pbi": "skip"})
        try:
            rc = CMD.cmd_create_integration_prs(ns)
        finally:
            restore_mocks()
    assert rc == 0 and writes == []




def test_create_integration_prs_execute_opens_and_records(monkeypatch):
    """--execute opens the msal PRs (freeze + integration), does the RI edit, and records the step."""
    import tempfile
    from orchestrator.commands import integ_prs_cmd as CMD
    from orchestrator import cli_common as _C
    created = _integration_provider(monkeypatch, behind=2)
    with tempfile.TemporaryDirectory() as d:
        ns, restore_mocks = _integ_release(d, {"versions": {"msal": "8.4.2"},
                                               "repos": ["msal"]})
        ns.execute = True
        try:
            rc = _run_integration_review(ns)
            after = _C.load_state(d, "2026-08")
        finally:
            restore_mocks()
    assert rc == 0
    assert ("working/release/8.4.2", "release/8.4.2") in created        # freeze opened
    assert ("release-integration/8.4.2", "dev") in created              # integration opened
    assert after.is_done("finalize", "integ_prs")                       # step recorded
    assert ("push", "release-integration/8.4.2") in created
    assert after.get_step("finalize", "integ_prs").data["last_write_review"]["hash"] == ns.review_hash




def test_create_integration_prs_holds_pr_on_human_conflict(monkeypatch):
    """Incomplete conflict planning refuses the entire write batch before approval."""
    import tempfile
    from orchestrator.commands import integ_prs_cmd as CMD
    created = _integration_provider(monkeypatch, behind=1, conflicts=["changelog"])
    with tempfile.TemporaryDirectory() as d:
        ns, restore_mocks = _integ_release(d, {"versions": {"msal": "8.4.2"},
                                               "repos": ["msal"], "pbi": "skip"})
        try:
            rc = CMD.cmd_create_integration_prs(ns)
        finally:
            restore_mocks()
    assert created == []
    assert rc == 1




def test_release_announcement_builds_channel_post_from_state_versions():
    """build() returns a needs_skill microsoft_teams channel post with the SDK table from
    state.versions and the correct title/target."""
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    from orchestrator.outcomes import as_dict
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0",
                        "authenticator": "release/2026/08/22"})
    with mockctx.active({}):
        out = as_dict(_invoke(RA.build, st))
    assert out["kind"] == "needs_skill"
    assert out["tool"] == "microsoft_teams-SendMessageToChannel"
    p = out["payload"]
    assert p["teamId"] == RA.CONFIG["team_id"] and p["channelId"] == RA.CONFIG["channel_id"]
    assert p["subject"] == "Auth Client Android SDKs September 2026 Release"
    assert p["contentType"] == "html"
    # table shows the 3 SDKs with their versions; authenticator branch is NOT in the table
    for v in ("24.6.0", "8.4.2", "16.5.0"):
        assert v in p["content"]
    assert "2026/08/22" not in p["content"]
    assert out["outbound"] is True




def test_release_announcement_cc_grouped_by_team():
    """cc renders one line PER TEAM (bold group label + member @mentions), and the mentions
    array covers every member across groups."""
    import json as _json
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"})
    groups = [{"group": "OneAuth", "members": [{"name": "Nick Bopp", "email": "nichbop@microsoft.com"}]},
              {"group": "Native Auth", "members": [{"name": "Yu Xin", "email": "yuxin@microsoft.com"}]}]
    with mockctx.active({"cc_groups": groups}):
        out = _invoke(RA.build, st)
    content = out.payload["content"]
    assert "<b>OneAuth</b>: @Nick Bopp" in content
    assert "<b>Native Auth</b>: @Yu Xin" in content
    mentions = _json.loads(out.payload["mentions"])
    assert {m["id"] for m in mentions} == {"nichbop@microsoft.com", "yuxin@microsoft.com"}




def test_release_announcement_cc_mode_self_pings_only_you():
    """cc_mode='self' mentions ONLY self_email (safe test — never pings the real members)."""
    import json as _json
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26")
    st.record_versions({"common": "24.6.0"})
    with mockctx.active({"cc_mode": "self", "self_email": "pedroro@microsoft.com"}):
        out = _invoke(RA.build, st)
    mentions = _json.loads(out.payload["mentions"])
    assert mentions == [{"displayName": "pedroro", "id": "pedroro@microsoft.com", "type": "user"}]




def test_release_announcement_post_to_renders_plain_text_real_cc():
    """A test redirect (post_to) shows the FULL real cc grouped as PLAIN TEXT — real member
    names present, but NO mentions (nobody pinged), mirroring reality closely."""
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26")
    st.record_versions({"common": "24.6.0"})
    with mockctx.active({"post_to": {"teamId": "T", "channelId": "19:test@thread.tacv2"}}):
        out = _invoke(RA.build, st)
    content = out.payload["content"]
    assert "mentions" not in out.payload                 # nobody @-pinged
    # full real cc present, grouped, as plain text (no @)
    assert "<b>CP/Intune</b>: Bing Xia" in content
    assert "<b>Native Auth</b>: Yu Xin" in content
    assert "@Bing Xia" not in content
    # explicit cc_mode:self still overrides a redirect
    with mockctx.active({"post_to": {"teamId": "T", "channelId": "19:test@thread.tacv2"},
                         "cc_mode": "self", "self_email": "pedroro@microsoft.com"}):
        out2 = _invoke(RA.build, st)
    import json as _json
    assert _json.loads(out2.payload["mentions"]) == \
        [{"displayName": "pedroro", "id": "pedroro@microsoft.com", "type": "user"}]




def test_release_announcement_cc_mode_off_is_plain_text():
    """cc_mode='off' lists member names grouped as plain text with NO mentions key."""
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26")
    st.record_versions({"common": "24.6.0"})
    groups = [{"group": "OneAuth", "members": [{"name": "Nick Bopp", "email": "nichbop@microsoft.com"}]}]
    with mockctx.active({"cc_mode": "off", "cc_groups": groups}):
        out = _invoke(RA.build, st)
    assert "mentions" not in out.payload
    assert "<b>OneAuth</b>: Nick Bopp" in out.payload["content"]
    assert "@Nick Bopp" not in out.payload["content"]




def test_release_announcement_cc_registry_loads_4_groups_20_members():
    """The real config/announcement_cc.yaml resolves to 4 groups / 20 maintained members."""
    from steps.finalize import release_announcement as RA
    groups = RA._load_groups(_context(ReleaseState()))
    assert [g for g, _ in groups] == ["CP/Intune", "LTW", "OneAuth", "Native Auth"]
    assert sum(len(m) for _g, m in groups) == 20
    assert len(RA._load_members(_context(ReleaseState()))) == 20
    emails = {m["email"] for _g, ms in groups for m in ms}
    assert "bingxi@microsoft.com" in emails and "yuxin@microsoft.com" in emails




def test_release_announcement_post_to_redirects_target():
    """The `post_to` mock redirects to a test channel while keeping the post real."""
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2", "broker": "16.5.0"})
    with mockctx.active({"post_to": {"teamId": "T-test", "channelId": "19:test@thread.tacv2"}}):
        out = _invoke(RA.build, st)
    assert out.payload["teamId"] == "T-test"
    assert out.payload["channelId"] == "19:test@thread.tacv2"




def test_release_announcement_blocks_without_versions():
    """No SDK versions anywhere -> Blocked (never posts an empty table)."""
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    st = ReleaseState(release_id="2026-08", ccd="2026-08-26")   # no versions recorded
    with mockctx.active({}):
        out = _invoke(RA.build, st)
    assert out.kind == "blocked"




def test_release_announcement_month_year_from_release_id_without_ccd():
    """Title month/year is the ship month (release_id month + 1) even when no CCD is set."""
    from steps.lib import mockctx
    from steps.finalize import release_announcement as RA
    st = ReleaseState(release_id="2026-08")                     # no ccd
    st.record_versions({"common": "1.0.0"})
    with mockctx.active({}):
        out = _invoke(RA.build, st)
    assert out.payload["subject"] == "Auth Client Android SDKs September 2026 Release"




def test_verify_pub_all_published_done():
    """All three artifacts present on Maven Central -> Done, with links."""
    from steps.lib import mockctx
    from steps.finalize import verify_pub as VP
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2"})
    with mockctx.active({"results": {"common4j": "published", "common": "published", "msal": "published"}}):
        out = _invoke(VP.build, st)
    assert out.kind == "done"
    assert "24.6.0" in out.note and "8.4.2" in out.note
    assert any("24.6.0" in (l.get("url") or "") for l in out.links)




def test_verify_pub_missing_is_in_progress():
    """A not-yet-published artifact -> InProgress (poll), never Blocked/failed."""
    from steps.lib import mockctx
    from steps.finalize import verify_pub as VP
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2"})
    with mockctx.active({"results": {"common4j": "published", "common": "published", "msal": "missing"}}):
        out = _invoke(VP.build, st)
    assert out.kind == "in_progress"
    assert "MSAL 8.4.2" in out.note                # names the pending artifact
    assert "Common 24.6.0" in out.note             # notes what's already published




def test_verify_pub_network_error_blocks():
    """A check that errors out -> Blocked (surface it; don't assume 'not published')."""
    from steps.lib import mockctx
    from steps.finalize import verify_pub as VP
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2"})
    with mockctx.active({"results": {"common4j": "published", "common": "error", "msal": "published"}}):
        out = _invoke(VP.build, st)
    assert out.kind == "blocked" and "Common 24.6.0" in out.reason




def test_verify_pub_blocks_without_versions():
    """No versions in state -> Blocked (nothing to verify)."""
    from steps.lib import mockctx
    from steps.finalize import verify_pub as VP
    st = ReleaseState(release_id="2026-08")
    with mockctx.active({}):
        out = _invoke(VP.build, st)
    assert out.kind == "blocked"




def test_verify_pub_common4j_uses_common_version():
    """Common4j is checked at the COMMON version (not a separate one)."""
    from steps.lib import mockctx
    from steps.finalize import verify_pub as VP
    from tools import maven as M
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2"})
    seen = {}

    def fake(key, version, timeout=25):
        seen[key] = version
        return (True, True, "ok")

    o = M.is_published
    M.is_published = fake
    try:
        with mockctx.active({}):
            _invoke(VP.build, st)
    finally:
        M.is_published = o
    assert seen == {"common4j": "24.6.0", "common": "24.6.0", "msal": "8.4.2"}




def test_verify_release_notes_all_published_done():
    """All three GitHub releases present → Done, with per-repo links (broker via GHE)."""
    from steps.lib import mockctx
    from steps.finalize import verify_release_notes as VR
    from orchestrator.outcomes import as_dict
    with mockctx.active({"results": {"broker": "published", "msal": "published", "common": "published"}}):
        out = as_dict(_invoke(VR.build, _vrn_state()))
    assert out["kind"] == "done"
    assert "Broker 16.5.0" in out["note"] and "MSAL 8.4.2" in out["note"] and "Common 24.6.0" in out["note"]
    urls = [l["url"] for l in out["links"]]
    assert any("msft.ghe.com/security/ad-accounts-for-android/releases/tag/v16.5.0" in u for u in urls)
    assert any("AzureAD/microsoft-authentication-library-for-android/releases/tag/v8.4.2" in u for u in urls)




def test_verify_release_notes_missing_is_in_progress():
    """A not-yet-published release → InProgress (poll), never failed."""
    from steps.lib import mockctx
    from steps.finalize import verify_release_notes as VR
    with mockctx.active({"results": {"broker": "published", "msal": "published", "common": "missing"}}):
        out = _invoke(VR.build, _vrn_state())
    assert out.kind == "in_progress"
    assert "Common 24.6.0" in out.note                       # names the pending release
    assert "Broker 16.5.0" in out.note                       # notes what's already published




def test_verify_release_notes_error_blocks():
    """A gh error (auth/network/draft) → Blocked (surface it; don't assume 'not published')."""
    from steps.lib import mockctx
    from steps.finalize import verify_release_notes as VR
    with mockctx.active({"results": {"broker": "error", "msal": "published", "common": "published"}}):
        out = _invoke(VR.build, _vrn_state())
    assert out.kind == "blocked" and "Broker 16.5.0" in out.reason




def test_verify_release_notes_blocks_without_versions():
    """Missing a version in state → Blocked (nothing to verify)."""
    from steps.lib import mockctx
    from steps.finalize import verify_release_notes as VR
    st = ReleaseState(release_id="2026-08")
    st.record_versions({"common": "24.6.0", "msal": "8.4.2"})     # no broker
    with mockctx.active({}):
        out = _invoke(VR.build, st)
    assert out.kind == "blocked" and "broker" in out.reason.lower()




def test_verify_release_notes_tags_v_prefixed_from_integ_config():
    """The checked tag is v<version> and the repo slugs come from integ_prs.CONFIG."""
    from steps.finalize import verify_release_notes as VR
    from steps.finalize import integ_prs as IP
    assert VR._tag("16.5.0") == "v16.5.0"
    assert VR._gh_repo("broker") == IP.CONFIG["broker"]["gh_repo"]
    assert VR._gh_repo("common") == IP.CONFIG["common"]["gh_repo"]




def test_verify_release_notes_config_is_agent_after_verify_pub():
    """phases.yaml: verify_release_notes is an agent step placed after verify_pub."""
    import yaml as _yaml
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    fin = next(p for p in cfg["phases"] if p["id"] == "finalize")
    s = next(x for x in fin["steps"] if x["id"] == "verify_release_notes")
    assert s.get("owner") == "agent" and s.get("source") != "scout"
    ids = [x["id"] for x in fin["steps"]]
    assert ids.index("verify_release_notes") > ids.index("verify_pub")




def test_tag_authenticator_creates_tag():
    """Discovers the version+commit from the release-app build and creates a lightweight tag
    (no 'v' prefix) at the built commit."""
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    from tools import pipelines as P
    seen = {}

    def fake_find(branch, timeout=90):
        seen["branch"] = branch
        return (True, {"build_id": 177976153, "version": "6.2608.5658", "commit": _TA_COMMIT}, "")

    def fake_create(org, project, repo, tag, commit, timeout=60):
        seen["create"] = (repo, tag, commit)
        return (True, {"created": True, "objectId": commit}, "")

    of, oc = P.find_auth_release_build, P.create_lightweight_tag
    P.find_auth_release_build, P.create_lightweight_tag = fake_find, fake_create
    try:
        with mockctx.active({}):
            out = _invoke(TA.build, _ta_state())
    finally:
        P.find_auth_release_build, P.create_lightweight_tag = of, oc
    assert out.kind == "done" and "6.2608.5658" in out.note and _TA_COMMIT[:8] in out.note
    assert seen["branch"] == "release/2026/08/13"
    # tags the built commit with the bare version (NO 'v' prefix) in the auth repo
    assert seen["create"] == ("AD-MFA-phonefactor-phoneApp-android", "6.2608.5658", _TA_COMMIT)
    assert TA.KIND == "agent"




def test_tag_authenticator_idempotent_same_commit():
    """An existing tag AT the same commit → Done (idempotent), no error."""
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    from tools import pipelines as P
    of, oc = P.find_auth_release_build, P.create_lightweight_tag
    P.find_auth_release_build = lambda b, timeout=90: (True, {"version": "6.2608.5658", "commit": _TA_COMMIT}, "")
    P.create_lightweight_tag = lambda o, pj, r, t, c, timeout=60: (True, {"created": False, "objectId": _TA_COMMIT}, "")
    try:
        with mockctx.active({}):
            out = _invoke(TA.build, _ta_state())
    finally:
        P.find_auth_release_build, P.create_lightweight_tag = of, oc
    assert out.kind == "done" and "idempotent" in out.note.lower()


def test_tag_authenticator_recovery_uses_frozen_target():
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    from tools import pipelines as P
    from orchestrator import effects
    from orchestrator.state import ReleaseState, StepState

    state = ReleaseState(release_id="2026-08")
    state.versions["authenticator"] = "release/2026/08/13"
    handler = effects.EffectHandler(
        step_id=TA.ID,
        mode=effects.EffectMode.IDEMPOTENT,
        recovery=effects.EffectRecovery.FROZEN,
        prepare=TA.prepare_effect,
        execute=TA.execute,
    )
    with mockctx.active({"version": "6.2608.5658", "commit": _TA_COMMIT}):
        prepared = effects.prepare(handler, _context(state))
    execution = {
        "id": "effect",
        "owner": "engine",
        "started_at": "2026-09-12T00:00:00Z",
        "refresh": False,
        "effect_mode": "idempotent",
        "effect_recovery": "frozen",
        "operation_key": prepared["operation_key"],
        "effect_input": prepared["input"],
    }
    state.set_step(
        "finalize", TA.ID, StepState(status="running", execution=execution)
    )
    seen = {}
    original = P.create_lightweight_tag
    P.create_lightweight_tag = lambda _o, _p, _r, tag, commit, timeout=60: (
        seen.update(tag=tag, commit=commit),
        (True, {"created": False, "objectId": commit}, ""),
    )[1]
    try:
        with mockctx.active({"version": "6.2608.9999", "commit": "f" * 40}):
            assert TA.prepare_effect(_context(state)) == prepared["input"]
            outcome = _invoke_effect(TA.execute, state, execution)
    finally:
        P.create_lightweight_tag = original

    assert outcome.kind == "done"
    assert seen == {"tag": "6.2608.5658", "commit": _TA_COMMIT}
    assert prepared["input"]["repository"] == {
        "org": TA._REPO["org"],
        "project": TA._REPO["project"],
        "name": TA._REPO["name"],
    }




def test_tag_authenticator_conflict_different_commit_blocks():
    """An existing tag pointing at a DIFFERENT commit → Blocked (human must reconcile)."""
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    from tools import pipelines as P
    of, oc = P.find_auth_release_build, P.create_lightweight_tag
    P.find_auth_release_build = lambda b, timeout=90: (True, {"version": "6.2608.5658", "commit": _TA_COMMIT}, "")
    P.create_lightweight_tag = lambda o, pj, r, t, c, timeout=60: (True, {"created": False, "objectId": "dead" * 10}, "")
    try:
        with mockctx.active({}):
            out = _invoke(TA.build, _ta_state())
    finally:
        P.find_auth_release_build, P.create_lightweight_tag = of, oc
    assert out.kind == "blocked" and "already exists" in out.reason and "reconcile" in out.reason




def test_tag_authenticator_dry_run_does_not_write():
    """dry_run composes the tag but never calls create."""
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    from tools import pipelines as P
    called = {"create": False}
    of, oc = P.find_auth_release_build, P.create_lightweight_tag
    P.find_auth_release_build = lambda b, timeout=90: (True, {"version": "6.2608.5658", "commit": _TA_COMMIT}, "")

    def _boom(*a, **k):
        called["create"] = True
        return (True, {"created": True, "objectId": _TA_COMMIT}, "")
    P.create_lightweight_tag = _boom
    try:
        with mockctx.active({"dry_run": "true"}):
            out = _invoke(TA.build, _ta_state())
    finally:
        P.find_auth_release_build, P.create_lightweight_tag = of, oc
    assert out.kind == "done" and "dry-run" in out.note.lower() and called["create"] is False




def test_tag_authenticator_injected_version_commit_skips_lookup():
    """version+commit mocks bypass the build lookup entirely (offline)."""
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    from tools import pipelines as P
    of, oc = P.find_auth_release_build, P.create_lightweight_tag

    def _nolookup(*a, **k):
        raise AssertionError("find_auth_release_build must not be called when both are injected")
    P.find_auth_release_build = _nolookup
    P.create_lightweight_tag = lambda o, pj, r, t, c, timeout=60: (True, {"created": True, "objectId": c}, "")
    try:
        with mockctx.active({"version": "6.2608.9999", "commit": "abc123"}):
            out = _invoke(TA.build, _ta_state())
    finally:
        P.find_auth_release_build, P.create_lightweight_tag = of, oc
    assert out.kind == "done" and "6.2608.9999" in out.note




def test_tag_authenticator_blocks_without_branch():
    """No authenticator release branch on state.versions → Blocked."""
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    with mockctx.active({}):
        out = _invoke(TA.build, ReleaseState(release_id="2026-08"))
    assert out.kind == "blocked" and "release branch" in out.reason




def test_tag_authenticator_blocks_when_build_not_run():
    """Release-app build hasn't run on the branch yet (info=None) → Blocked."""
    from steps.lib import mockctx
    from steps.finalize import tag_authenticator as TA
    from tools import pipelines as P
    of = P.find_auth_release_build
    P.find_auth_release_build = lambda b, timeout=90: (True, None, "no succeeded release-app build on refs/heads/release/2026/08/13")
    try:
        with mockctx.active({}):
            out = _invoke(TA.build, _ta_state())
    finally:
        P.find_auth_release_build = of
    assert out.kind == "blocked" and "hasn't run yet" in out.reason




def test_tag_authenticator_config_is_agent():
    """phases.yaml classifies tag_authenticator as an agent step in finalize (F6)."""
    import yaml as _yaml
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    fin = next(p for p in cfg["phases"] if p["id"] == "finalize")
    s = next(x for x in fin["steps"] if x["id"] == "tag_authenticator")
    assert s.get("owner") == "agent" and s.get("source") != "scout" and s.get("maps_to") == ["F6"]




def test_oneauth_common_pr_preview_computes_plan():
    """build() is preview-first: reads versions, merge-needed, edits, existing PR → NeedsSkill."""
    from steps.lib import mockctx
    from steps.finalize import oneauth_common_pr as S
    from orchestrator.outcomes import as_dict
    from tools import oneauth as OA
    o_ab, o_rt, o_fp = OA.ahead_behind, OA.read_text, OA.find_open_pr
    OA.ahead_behind = lambda base, target, timeout=60: (True, {"ahead": 29, "behind": 35}, "")
    key_by_path = {OA.FILES[k]: k for k in OA.FILES}
    OA.read_text = lambda path, ref, ref_type="branch", timeout=60: (True, _OA_FILES[key_by_path[path]], "")
    OA.find_open_pr = lambda source, target, timeout=60: (True, None, "")
    try:
        with mockctx.active({}):
            out = as_dict(_invoke(S.build, _oa_state()))
    finally:
        OA.ahead_behind, OA.read_text, OA.find_open_pr = o_ab, o_rt, o_fp
    assert out["kind"] == "needs_skill" and out["tool"] == "create-oneauth-common-pr"
    p = out["payload"]["plan"]
    assert p["common"] == "24.7.0" and p["msal"] == "8.5.0"
    assert p["merge_needed"] is True and p["behind"] == 35
    assert len(p["changed_files"]) == 4 and p["existing_pr"] is None
    assert "create-oneauth-common-pr --release 2026-08" in out["payload"]["followup_command"]




def test_oneauth_common_pr_reuses_existing_pr_in_plan():
    """An existing open ingestion->dev PR is surfaced in the plan (idempotency)."""
    from steps.lib import mockctx
    from steps.finalize import oneauth_common_pr as S
    from orchestrator.outcomes import as_dict
    from tools import oneauth as OA
    o_ab, o_rt, o_fp = OA.ahead_behind, OA.read_text, OA.find_open_pr
    OA.ahead_behind = lambda base, target, timeout=60: (True, {"ahead": 0, "behind": 0}, "")
    key_by_path = {OA.FILES[k]: k for k in OA.FILES}
    OA.read_text = lambda path, ref, ref_type="branch", timeout=60: (True, _OA_FILES[key_by_path[path]], "")
    OA.find_open_pr = lambda source, target, timeout=60: (True, {"id": 99, "url": "u", "title": "t"}, "")
    try:
        with mockctx.active({}):
            out = as_dict(_invoke(S.build, _oa_state()))
    finally:
        OA.ahead_behind, OA.read_text, OA.find_open_pr = o_ab, o_rt, o_fp
    p = out["payload"]["plan"]
    assert p["merge_needed"] is False and p["existing_pr"] == {"id": 99, "url": "u", "title": "t"}




def test_oneauth_common_pr_blocks_without_versions():
    """Missing Common/MSAL versions → Blocked."""
    from steps.lib import mockctx
    from steps.finalize import oneauth_common_pr as S
    with mockctx.active({}):
        out = _invoke(S.build, ReleaseState(release_id="2026-08"))
    assert out.kind == "blocked" and "Common and MSAL" in out.reason




def test_oneauth_common_pr_blocks_on_missing_anchor():
    """If a file's bump anchor can't be found, build() blocks (no silent no-op)."""
    from steps.lib import mockctx
    from steps.finalize import oneauth_common_pr as S
    from tools import oneauth as OA
    o_ab, o_rt, o_fp = OA.ahead_behind, OA.read_text, OA.find_open_pr
    OA.ahead_behind = lambda base, target, timeout=60: (True, {"ahead": 0, "behind": 1}, "")
    OA.read_text = lambda path, ref, ref_type="branch", timeout=60: (True, "garbage with no anchors", "")
    OA.find_open_pr = lambda source, target, timeout=60: (True, None, "")
    try:
        with mockctx.active({}):
            out = _invoke(S.build, _oa_state())
    finally:
        OA.ahead_behind, OA.read_text, OA.find_open_pr = o_ab, o_rt, o_fp
    assert out.kind == "blocked" and "anchors" in out.reason




def test_oneauth_common_pr_config_is_external():
    """phases.yaml classifies oneauth_common_pr as an agent step in finalize."""
    import yaml as _yaml
    cfg = _yaml.safe_load(open(CONFIG, encoding="utf-8"))
    fin = next(p for p in cfg["phases"] if p["id"] == "finalize")
    s = next(x for x in fin["steps"] if x["id"] == "oneauth_common_pr")
    assert s.get("kind") == "external" and s.get("source") == "scout"
    # runs AFTER verify_pub (Common must be published first)
    ids = [x["id"] for x in fin["steps"]]
    assert ids.index("oneauth_common_pr") > ids.index("verify_pub")




def test_wiki_payload_composes_page_and_filters_noise():
    """compose_payload renders the App Version line, the merged-PR list (LEGO noise dropped),
    the SDK versions, and the hand-curated placeholders."""
    from steps.lib import mockctx
    from steps.finalize import wiki_payload as W
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13")
    st.versions = {"authenticator": "release/2026/08/13", "broker": "16.5.0",
                   "common": "24.6.0", "msal": "8.4.2"}
    knobs = {"version": {"version": "6.2608.5658", "build_number": "20260824.9",
                         "build_url": "https://msazure.visualstudio.com/One/_build/results?buildId=177976153&view=results"},
             "prs": [{"id": 1, "title": "Real feature"},
                     {"id": 2, "title": "LEGO: check in to working."}]}
    with mockctx.active(knobs):
        ok, plan, det = W.compose_payload(_context(st))
    assert ok, det
    c = plan["content"]
    assert "#App Version\n6.2608.5658 [Pipelines - Run 20260824.9]" in c
    assert "PR 1: Real feature" in c and "LEGO: check in to working" not in c   # noise filtered
    assert "*   Broker: 16.5.0" in c and "*   Common: 24.6.0" in c and "*   Msal: 8.4.2" in c
    assert "### Release: September 2026" in c
    assert "_Add the Broker release-announcement email title._" in c
    assert "Expected Feature flags rollouts" in c
    assert plan["page_name"] == "September 2026 Release" and plan["pr_count"] == 1




def test_wiki_payload_build_reports_create_or_update():
    """build() is preview-first: it checks whether the page exists and names create vs update
    in a NeedsSkill(create-payload-wiki) with the composed content in the payload."""
    from steps.lib import mockctx
    from steps.finalize import wiki_payload as W
    from tools import checks
    st = ReleaseState(release_id="2026-08", ccd="2026-08-13")
    st.versions = {"authenticator": "release/2026/08/13", "broker": "16.5.0",
                   "common": "24.6.0", "msal": "8.4.2"}
    knobs = {"version": {"version": "6.2608.5658", "build_url": "https://x/y"},
             "prs": [{"id": 1, "title": "Feature"}]}
    oe = checks.wiki_page_exists
    checks.wiki_page_exists = lambda *a, **k: True         # page exists → update
    try:
        with mockctx.active(knobs):
            out = _invoke(W.build, st)
    finally:
        checks.wiki_page_exists = oe
    assert out.kind == "needs_skill" and out.tool == "create-payload-wiki"
    assert out.payload["plan"]["action"] == "update"
    assert "#App Version" in out.payload["plan"]["content"]
    assert out.payload["followup_command"].startswith("create-payload-wiki --release 2026-08 --dry-run")




def test_broker_change_list_reads_changes_txt():
    """broker_change_list reads changes.txt from the broker release branch (trying candidate
    refs), returns THIS version's 'Version <v>' section, joins wrapped bullets, parses the
    optional [LEVEL] + trailing (#PR), and never fabricates a level."""
    import base64
    from tools import prs
    changes_txt = (
        "vNext\n----------\n\n"
        "Version 16.5.0\n----------\n"
        "- [PATCH] Update common @24.6.0\n"
        "- [MINOR] Add GetDeviceTokenV1Executor to handle\n"
        "  GET_DEVICE_TOKEN_V1 protocol (#261)\n"          # wrapped bullet → joined
        "- Plain entry with no level (#238)\n"
        "\nVersion 16.4.1\n----------\n"
        "- [PATCH] Older release entry (#100)\n")           # must NOT bleed into 16.5.0
    b64 = base64.b64encode(changes_txt.encode()).decode()
    calls = {"refs": []}

    def fake_run(args, cwd=None, timeout=120):
        a = " ".join(args)
        import re as _re
        m = _re.search(r"contents/changes\.txt\?ref=(\S+)", a)
        if m:
            calls["refs"].append(m.group(1))
            # working/release/<v> 404s (e.g. not yet promoted) → falls back to release/<v>
            if m.group(1).startswith("working/release/"):
                return (1, "", "No commit found")
            return (0, b64, "")
        return (1, "", "unexpected")

    orig = prs._run
    prs._run = fake_run
    try:
        ok, ch, _d = prs.broker_change_list("msft.ghe.com/security/ad-accounts-for-android", "16.5.0")
    finally:
        prs._run = orig
    assert ok
    assert ch == [
        {"level": "PATCH", "text": "Update common @24.6.0", "pr": None},
        {"level": "MINOR", "text": "Add GetDeviceTokenV1Executor to handle GET_DEVICE_TOKEN_V1 protocol", "pr": 261},
        {"level": None, "text": "Plain entry with no level", "pr": 238}]
    # tried the finalized ref first, then fell back to release/<v>
    assert calls["refs"][0] == "working/release/16.5.0" and "release/16.5.0" in calls["refs"]




def test_final_status_email_step_sends_and_closes():
    from steps.lib import mockctx
    from steps.finalize import final_status_email as FSE
    st = _status_state("finalize")
    with mockctx.active({"send_to": "me@microsoft.com"}):
        out = FSE.build(Orchestrator(CONFIG, st).context(
            "finalize", FSE.ID, inputs={"send_to": "me@microsoft.com"}))
    assert out.kind == "needs_skill" and out.tool == "workiq_send_email"
    assert out.payload["to"] == ["me@microsoft.com"]
    assert "Final Status" in out.payload["subject"]
    assert "followup_command" not in out.payload
