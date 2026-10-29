"""Explicit, receipt-backed continuation of one already-owned integration write.

This is not retry-effect, a new reservation, or runtime adoption. The original
review stays immutable; a separate manual review authorizes the reconciled suffix.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from orchestrator import cli_common as C, revision, write_review as W
from orchestrator.git_write_plans import _output, _read, _remote_matches_provider, _tip
from orchestrator import integration_resolutions as R
from orchestrator.transitions import TransitionResult
from tools import git_review as G, integration_receipts as providers, prs as PR


PHASE, STEP = "finalize", "integ_prs"
DATA_KEY = "integration_recovery"
COMMON = "AzureAD/microsoft-authentication-library-common-for-android"
MSAL = "AzureAD/microsoft-authentication-library-for-android"
PBI_LINK = ("https://identitydivision.visualstudio.com/"
            "fac9d424-53d2-45c0-91b5-ef6ba7a6bf26/_workitems/edit/")


def _record(orch, args):
    record = orch.state.get_step(PHASE, STEP)
    execution = record.execution or {}
    definition = orch.workflow.step(PHASE, STEP)
    if (not definition or definition.write_command != "create-integration-prs"
            or args.release != orch.state.release_id
            or record.status not in ("blocked", "in_flight")
            or execution.get("id") != args.execution_id
            or not args.executor or execution.get("owner") != args.executor
            or not execution.get("write_review") or not record.data.get("in_flight_since")):
        raise ValueError("Recovery requires the exact fenced, owned integration execution and executor")
    if (record.invalidated_at
            and datetime.fromisoformat(record.invalidated_at.replace("Z", "+00:00"))
            > datetime.fromisoformat(execution["started_at"].replace("Z", "+00:00"))):
        raise ValueError("Owned integration generation was invalidated")
    return record


def load_original(orch, args):
    record = _record(orch, args)
    artifact = json.loads(Path(args.original_preview).read_text(encoding="utf-8-sig"))
    original = artifact["plan"]
    review = record.execution["write_review"]
    from orchestrator import integration_runtime_migration as migration
    original_revision_matches = (
        migration.authenticate_original(orch, record, original)
        if migration.DATA_KEY in record.data else
        original.get("workflow_revision") == revision.revision_id(orch.state.workflow_revision))
    if (artifact.get("permission_to_execute") is not False
            or artifact.get("review_hash") != review["hash"]
            or revision.digest(original) != review["hash"]
            or original.get("version") != 1 or original.get("release") != args.release
            or original.get("step") != "finalize.integ_prs"
            or original.get("command") != "create-integration-prs"
            or not original_revision_matches
            or original.get("generation") != (record.invalidated_at or record.completed_at or "initial")):
        raise ValueError("Original frozen preview does not match the active execution's approved review")
    _validate_operations(original)
    return original


def _validate_operations(original):
    pbi, operations = original["parameters"]["pbi"], original["operations"]
    if not operations or any(op["kind"] not in (
            "pbi.create", "integration.create", "integration.push", "integration.reuse")
            for op in operations):
        raise ValueError("Recovery requires supported original integration operations")
    created = operations[0]["kind"] == "pbi.create"
    if (pbi["mode"] not in ("create", "existing")
            or sum(op["kind"] == "pbi.create" for op in operations) != int(created)
            or created != (pbi["mode"] == "create" and any(
                op["kind"] == "integration.create" for op in operations))):
        raise ValueError("Original PBI mode and operation sequence disagree")
    return created


def _pbi_id(original, args):
    created = _validate_operations(original)
    pbi, operations = original["parameters"]["pbi"], original["operations"]

    def positive_id(value):
        if not str(value).isdigit() or int(value) <= 0:
            raise ValueError("Recovery requires an explicit positive existing PBI ID")
        return str(int(value))

    supplied = getattr(args, "existing_pbi", None)
    if created:
        return positive_id(supplied)
    if pbi["mode"] == "existing":
        expected = positive_id(pbi["id"])
        if supplied is not None and positive_id(supplied) != expected:
            raise ValueError("Existing PBI differs from the original approved ID")
        return expected
    if supplied is not None or any(op["kind"] != "integration.reuse" for op in operations):
        raise ValueError("Original reuse-only plan does not reference a PBI")
    return None


def _body(operation, wid):
    body = operation["content"]["body"]
    return ("".join(str(_output(value, {"pbi.id": wid})) for value in body)
            if isinstance(body, list) else body)


def _content_change(operation, receipt, wid):
    content, target = operation["content"], operation["target"]
    expected = {"title": content["title"], "body": _body(operation, wid)}
    actual = {key: receipt[key] for key in expected}
    if actual == expected:
        return None
    if operation["kind"] == "integration.reuse":
        raise ValueError("Reused PR content differs from the approved snapshot")
    token = "AB#" + wid
    linked = "[" + token + "](" + PBI_LINK + wid + ")"
    linked_body = expected["body"].replace(token, linked)
    bodies = {expected["body"], linked_body}
    bodies.update(body.replace("\n", "\r\n") for body in tuple(bodies))
    if (target.get("gh_repo") not in (COMMON, MSAL) or expected["body"].count(token) != 1
            or actual["title"] not in (expected["title"], expected["title"] + ", Fixes " + token)
            or actual["body"] not in bodies):
        raise ValueError("Unexplained PR content edit; recovery is held")
    return {"rule": "common-validate-pr-ab-id", "operation": revision.digest(operation),
            "before": expected, "after": actual}


def _labels_match(operation, labels):
    if operation["kind"] == "integration.reuse":
        return labels == sorted(set(operation["content"]["labels"])
                                | set(operation["content"]["existing"]["labels"]))
    required = sorted(set(operation["content"]["labels"]))
    return labels == required or (
        operation["target"].get("gh_repo") in (COMMON, MSAL)
        and labels == sorted(set(required) | {"changelog:none"}))


def _receipt_content_matches(old, new, operation, wid):
    before, after = old.get("evidence") or {}, new.get("evidence") or {}
    if (operation["kind"] != "integration.create"
            or old.get("status") != new.get("status") or new.get("status") != "applied"):
        return False
    identity = lambda value: {k: v for k, v in value.items()
                             if k not in ("title", "body", "labels")}
    if identity(before) != identity(after):
        return False
    if operation["target"]["tool"] == "gh" and not _labels_match(operation, after["labels"]):
        return False
    _content_change(operation, before, wid)
    _content_change(operation, after, wid)
    return True


def _merged_receipt_matches(old, new, operation, wid):
    if (old.get("status") != "applied" or new.get("status") != "applied"
            or operation["kind"] != "integration.create"
            or operation["target"]["tool"] != "gh"):
        return False
    before, after = old.get("evidence") or {}, new.get("evidence") or {}
    if before.get("state") not in ("open", "merged") or after.get("state") != "merged":
        return False
    immutable = lambda value: {k: v for k, v in value.items()
                              if k not in ("state", "merge_tip", "merged_at", "labels", "title", "body")}
    if immutable(before) != immutable(after):
        return False
    if not _labels_match(operation, after.get("labels")):
        return False
    _content_change(operation, before, wid)
    _content_change(operation, after, wid)
    if before["state"] == "merged" and before != after:
        return False
    merge = G.object_id(after["merge_tip"])
    if (datetime.fromisoformat(after["merged_at"].replace("Z", "+00:00"))
            < datetime.fromisoformat(after["created_at"].replace("Z", "+00:00"))):
        return False
    root = operation["target"]["root"]
    base = operation["preconditions"]["base_tip"]
    if G._git(root, "merge-base", base, merge).decode().strip() != base:
        return False
    head = after["head_tip"]
    if G._git(root, "merge-base", head, merge).decode().strip() != head:
        # A squash must be exactly the reviewed source tree on the reviewed base.
        if (G._git(root, "rev-parse", merge + "^").decode().strip() != base
                or G._git(root, "rev-parse", merge + "^{tree}").strip()
                != G._git(root, "rev-parse", head + "^{tree}").strip()):
            return False
    return True


def observe(orch, args, original, *, active_index=None, push_root=None, remap=None):
    record = _record(orch, args)
    operations, repos = original["operations"], original["preconditions"]["repos"]
    first = operations[0]
    wid = _pbi_id(original, args)
    pbi = (providers.pbi_receipt(first["target"], wid, first["content"]["title"],
                               record.execution["started_at"])
           if first["kind"] == "pbi.create" else None)
    receipts, changes, current_tips = [], [], {}
    pushes = {(op["target"]["key"], op["target"]["head"]): op for op in operations
              if op["kind"] == "integration.push"}
    branch_changes = []
    for repo in repos:
        target = repo["target"]
        if target["root"] != push_root and str(G.clean_repository(target["root"])) != target["root"]:
            raise ValueError("Reviewed checkout root changed")
        if G.remote_url(target["root"]) != target["remote"]:
            raise ValueError("Reviewed origin changed")
        _remote_matches_provider(target)
        for branch, expected in repo["tips"].items():
            observed = _tip(target, branch)
            push = pushes.get((target["key"], branch))
            if observed != expected and (not push or observed != push["content"]["commit_id"]):
                branch_changes.append((target["key"], branch, observed))
            current_tips[target["key"], branch] = observed
    parameters = original["parameters"]
    if "resolutions" in parameters:
        R.recheck(parameters["resolutions"], parameters["repos"], parameters["resolution_versions"])
    for index, op in enumerate(operations):
        target, kind = op["target"], op["kind"]
        evidence, change = None, None
        if kind == "pbi.create":
            evidence = pbi
        elif kind == "integration.push":
            tip = current_tips[target["key"], target["head"]]
            if tip == op["content"]["commit_id"]:
                evidence = {"branch": target["head"], "tip": tip}
            elif tip != op["preconditions"]["head_tip"]:
                raise ValueError("Integration push cannot be reconciled")
        else:
            existing = op["content"]["existing"] if kind == "integration.reuse" else None
            evidence = (providers.pr_receipt(target, number=existing["number"])
                        if existing else providers.pr_receipt(target))
            if existing and (evidence is None
                    or evidence["number"] != existing["number"] or evidence["url"] != existing["url"]):
                raise ValueError("Reviewed reuse PR identity is missing or changed")
            if evidence is not None:
                if (str(evidence.get("number", "")).isdigit() is False
                        or not isinstance(evidence.get("url"), str)
                        or not evidence["url"]
                        or evidence["head_tip"] != op["preconditions"]["head_tip"]
                        or current_tips[target["key"], target["head"]] != evidence["head_tip"]
                        or evidence["state"] not in (("open",) if existing else ("open", "merged"))
                        or evidence["head"] != target["head"] or evidence["base"] != target["base"]
                        or evidence["repository"] != target.get("gh_repo", target.get("ado"))
                        or not existing and datetime.fromisoformat(evidence["created_at"].replace("Z", "+00:00"))
                        < datetime.fromisoformat(record.execution["started_at"].replace("Z", "+00:00"))):
                    raise ValueError("PR source commit or identity differs from approved plan")
                change = _content_change(op, evidence, wid)
                if target["tool"] == "gh":
                    if not _labels_match(op, evidence["labels"]):
                        if existing and evidence["labels"] == sorted(set(existing["labels"])):
                            evidence = None
                        else:
                            raise ValueError("PR labels differ from exact approved labels")
                elif not existing and evidence["work_items"] != [wid]:
                    raise ValueError("ADO PR work-item links differ from approved PBI")
        receipt = {"index": index, "operation_hash": revision.digest(op),
                   "status": "applied" if evidence is not None else "pending",
                   "evidence": evidence}
        if evidence and kind == "integration.create" and evidence["state"] == "merged":
            previous = record.data.get(DATA_KEY, {}).get("receipts", [])
            if (index >= len(previous)
                   or not _merged_receipt_matches(previous[index], receipt, op, wid)):
                raise ValueError("Merged PR is not an exact previously applied operation")
        if change:
            changes.append(change)
        receipts.append(receipt)
    for key, branch, tip in branch_changes:
        proofs = [
            r for r, op in zip(receipts, operations)
            if op["kind"] == "integration.create" and op["target"]["key"] == key
            and op["target"]["base"] == branch
            and (r.get("evidence") or {}).get("state") == "merged"
            and r["evidence"]["merge_tip"] == tip
        ]
        if len(proofs) != 1:
            raise ValueError("Unexplained branch change: " + key + "/" + branch)
    # This writer is sequential. A hole followed by a remote effect is not its suffix.
    pending_seen = False
    for receipt, op in zip(receipts, operations):
        if receipt["status"] == "pending":
            pending_seen = True
        elif pending_seen and not (
                op["kind"] == "integration.reuse" and (op["target"]["tool"] != "gh"
                    or set(op["content"]["labels"]).issubset(op["content"]["existing"]["labels"]))):
            raise ValueError("Provider effects are not a contiguous prefix of this execution")
    previous = record.data.get(DATA_KEY)
    if previous:
        if (previous["original_hash"] != record.execution["write_review"]["hash"]
                or previous.get("execution_id") != record.execution["id"]):
            raise ValueError("Recovery journal belongs to another execution/review")
        if len(previous["receipts"]) != len(receipts):
            raise ValueError("Recovery receipt count differs from approved operations")
        for old, new in zip(previous["receipts"], receipts):
            if old["operation_hash"] != new["operation_hash"] and not (
                    old["status"] == new["status"] == "pending"
                    and (remap or {}).get(old["index"]) == (old["operation_hash"], new["operation_hash"])):
                raise ValueError("Recovery operation changed")
            if (old["status"] == "applied" and old != new
                    and not _merged_receipt_matches(old, new, operations[new["index"]], wid)):
                if not _receipt_content_matches(old, new, operations[new["index"]], wid):
                    raise ValueError("Previously checkpointed receipt changed; owner review required")
            if (old["status"] == "in_flight" and new["status"] != "applied"
                    and old["index"] != active_index):
                raise ValueError("Uncertain provider operation is absent; blind replay is forbidden")
    journal = record.data.get("integration_write_journal", {})
    if journal and (journal["execution_id"] != record.execution["id"]
                    or journal["original_hash"] != record.execution["write_review"]["hash"]):
        raise ValueError("Original operation journal belongs to another execution/review")
    for index, entry in journal.get("operations", {}).items():
        receipt = receipts[int(index)]
        if entry["operation_hash"] != receipt["operation_hash"]:
            raise ValueError("Original operation journal hash differs from approved operation")
        if (entry["status"] == "in_flight" and receipts[int(index)]["status"] != "applied"):
            raise ValueError("Original invocation has an uncertain operation; blind replay is forbidden")
        if entry["status"] == "applied":
            evidence = receipt["evidence"] or {}
            recorded = entry["evidence"]
            if (receipt["status"] != "applied"
                    or any(evidence.get("id" if key == "pbi.id" else key) != value
                           for key, value in recorded.items())):
                raise ValueError("Original completed operation receipt changed")
    if len(json.dumps(receipts, ensure_ascii=True)) > 131072:
        raise ValueError("Provider receipts exceed the bounded recovery journal")
    return receipts, changes


def preview(orch, args, original=None):
    # The exception is reads only. Execution still requires assert_current().
    revision.assert_automation_recovery_compatible(orch)
    original = original if original is not None else load_original(orch, args)
    from orchestrator import integration_replan as replan
    effective, history, remap = replan.effective(
        _record(orch, args), original, getattr(args, "broker_target_tip", None))
    receipts, changes = observe(orch, args, effective, remap=remap)
    execution = _record(orch, args).execution
    plan = {"version": 1, "command": "recover-integration-prs", "release": args.release,
            "execution_id": execution["id"], "owner": execution["owner"],
            "original_review": deepcopy(execution["write_review"]),
            "original_revision": original["workflow_revision"],
            "runtime_hash": orch._loaded_runtime_hash,
            "receipts": receipts, "content_changes_requiring_approval": changes,
            "remaining": [{"index": r["index"], "operation_hash": r["operation_hash"],
                           "kind": effective["operations"][r["index"]]["kind"],
                           "target": effective["operations"][r["index"]]["target"]}
                          for r in receipts if r["status"] == "pending"]}
    merged = [
        {"index": r["index"], "receipt": r["evidence"]}
        for r in receipts if (r.get("evidence") or {}).get("state") == "merged"
    ]
    if merged:
        plan["external_merges_requiring_approval"] = merged
    wid = _pbi_id(effective, args)
    edits = [
        {"operation_hash": revision.digest(op), "repository": op["target"]["gh_repo"],
         "title_suffix": ", Fixes AB#" + wid,
         "plain_reference": "AB#" + wid,
         "linked_reference": "[AB#" + wid + "](" + PBI_LINK + wid + ")",
         "line_endings": ["LF", "CRLF"], "optional_added_label": "changelog:none"}
        for op in effective["operations"] if op["kind"] == "integration.create"
        and op["target"].get("gh_repo") in (COMMON, MSAL)
    ]
    if edits:
        plan["permitted_repository_edits_requiring_approval"] = edits
    migration = _record(orch, args).data.get("integration_runtime_migration")
    if migration:
        from orchestrator.integration_runtime_migration import latest_hash
        plan["runtime_migration_hash"] = latest_hash(_record(orch, args))
    if history:
        plan.update(effective_plan_hash=revision.digest(effective), replans=history)
    return {"plan": plan, "recovery_hash": revision.digest(plan), "permission_to_execute": False,
            "execution_blocker": revision.mismatch_reason(orch)}


def _checkpoint(orch, change):
    before = deepcopy(asdict(orch.state))
    change()
    W._checkpoint(orch, before)


def execute(orch, args):
    revision.assert_current(orch)
    transaction = C._LOCKED_STATE.get()
    if transaction is None or transaction[0] != C.os.path.abspath(C.state_path(args.runs_root, args.release)):
        raise ValueError("Recovery requires the existing release transaction lock")
    if (not revision.is_hash(args.recovery_hash) or not args.approved_by
            or not args.approved_by.strip() or not args.confirm_no_inflight):
        raise ValueError("Manual --recovery-hash, --approved-by and --confirm-no-inflight are required")
    original = load_original(orch, args)
    reviewed = preview(orch, args, original)
    if reviewed["recovery_hash"] != args.recovery_hash:
        raise ValueError("Recovery hash is stale; review fresh provider receipts")
    # Re-read the original artifact and every provider after review, before checkpointing.
    if load_original(orch, args) != original or preview(orch, args, original) != reviewed:
        raise ValueError("Recovery evidence changed before authorization")
    from orchestrator import integration_replan as replan
    effective, _history, _remap = replan.effective(
        _record(orch, args), original, getattr(args, "broker_target_tip", None))
    kernel = orch._transition_kernel()
    before = deepcopy(asdict(orch.state))
    permit = kernel.begin_integration_recovery(args.execution_id, reviewed["plan"],
                                                args.recovery_hash, args.approved_by.strip())
    if isinstance(permit, TransitionResult):
        raise ValueError(permit.message)
    try:
        W._checkpoint(orch, before)
    except BaseException:
        kernel._outcome_permits.pop(permit, None)
        raise
    approved_replans = deepcopy(_record(orch, args).data[DATA_KEY].get("replans", []))

    def check(active_index=None, push_root=None):
        revision.assert_current(orch)
        orch.validate_outcome_permit(permit)
        if load_original(orch, args) != original:
            raise ValueError("Original approved artifact changed during recovery")
        journal = _record(orch, args).data[DATA_KEY]
        if (journal.get("replans", []) != approved_replans
                or approved_replans and journal.get("effective_plan_hash") != revision.digest(effective)):
            raise ValueError("Approved effective plan changed during recovery")
        actual, _changes = observe(orch, args, effective, active_index=active_index, push_root=push_root)
        expected = deepcopy(_record(orch, args).data[DATA_KEY]["receipts"])
        if active_index is not None:
            expected[active_index]["status"] = "pending"
        if actual != expected:
            raise ValueError("Provider evidence changed during recovery")

    try:
        for entry in reviewed["plan"]["remaining"]:
            index = entry["index"]
            check()
            op = effective["operations"][index]
            target, content = op["target"], op["content"]
            if (_tip(target, target["head"]) != op["preconditions"]["head_tip"]
                    or _tip(target, target["base"]) != op["preconditions"]["base_tip"]):
                raise ValueError("Remaining operation branch guards changed")

            def fence():
                record = _record(orch, args)
                record.data[DATA_KEY]["receipts"][index]["status"] = "in_flight"
                orch.state.set_step(PHASE, STEP, record)
            _checkpoint(orch, fence)
            # Once fenced, failures (including process death) require reconciliation,
            # never resetting an operation to pending on the strength of absence.
            check(index)
            if op["kind"] == "integration.push":
                G.push_reviewed(target["root"], target["remote"], target["head"],
                                op["preconditions"]["head_tip"], content,
                                # push_reviewed checks this root excluding only its
                                # own isolated scratch immediately before callback.
                                lambda: check(index, target["root"]))
            elif op["kind"] == "integration.create":
                wid = _pbi_id(effective, args)
                if target["tool"] == "gh":
                    result = PR.gh_create_pr(target["gh_repo"], target["head"], target["base"],
                                             content["title"], _body(op, wid), labels=content["labels"])
                else:
                    a = target["ado"]
                    result = PR.az_create_pr(a["org"], a["project"], a["repository"],
                                             target["head"], target["base"], content["title"],
                                             _body(op, wid), work_items=wid)
                url = _read(result, "Recovery PR creation failed or uncertain")
                if not url:
                    raise ValueError("Recovery PR creation returned no receipt")
            elif op["kind"] == "integration.reuse":
                ok, detail = PR.gh_ensure_labels(
                    target["gh_repo"], content["existing"]["number"], content["labels"])
                if not ok:
                    raise ValueError("Recovery label update failed or uncertain: " + detail)
            else:
                raise ValueError("Recovery never creates another PBI")
            receipts, changes = observe(orch, args, effective)
            old = _record(orch, args).data[DATA_KEY]["receipts"]
            if (receipts[index]["status"] != "applied"
                    or any(a != b for n, (a, b) in enumerate(zip(old, receipts)) if n != index)
                    or op["kind"] == "integration.create" and receipts[index]["evidence"]["url"] != url):
                raise ValueError("Remote result differs from reviewed recovery; owner inspection required")
            def checkpoint_receipts():
                record = _record(orch, args)
                record.data[DATA_KEY]["receipts"] = receipts
                orch.state.set_step(PHASE, STEP, record)
            _checkpoint(orch, checkpoint_receipts)
        check()
        before = deepcopy(asdict(orch.state))
        result = kernel.complete_integration_recovery(permit)
        if not result.changed:
            raise ValueError(result.message)
        W._checkpoint(orch, before)
        return result.message
    finally:
        kernel._outcome_permits.pop(permit, None)
