"""Broker-only fast-forward extension of the pending owned integration suffix."""
from copy import deepcopy
import json

from orchestrator import revision
from tools import git_review as G


def effective(record, original, requested_tip=None):
    plan = deepcopy(original)
    journal = record.data.get("integration_recovery") or {}
    history = deepcopy(journal.get("replans", []))
    if not isinstance(history, list) or len(history) > 8:
        raise ValueError("Invalid bounded Broker replan chain")
    _bounded(history)
    for entry in history:
        if (not isinstance(entry, dict) or set(entry) != {"change", "hash", "approval_hash"}
                or not revision.is_hash(entry["approval_hash"])
                or not any(a["hash"] == entry["approval_hash"]
                           and entry["hash"] in a.get("replan_hashes", [])
                           for a in journal.get("approvals", []))
                or revision.digest(entry["change"]) != entry["hash"]):
            raise ValueError("Invalid Broker replan approval provenance")
        change = entry["change"]
        rebuilt, proof = _extend(plan, change["after_target"])
        if proof != change:
            raise ValueError("Broker replan chain differs from exact merge evidence")
        plan = rebuilt
    if history and journal.get("effective_plan_hash") != revision.digest(plan):
        raise ValueError("Broker effective plan identity changed")
    remap = {}
    if requested_tip is not None:
        G.object_id(requested_tip)
        target = next((r for r in plan["preconditions"]["repos"] if r["target"]["key"] == "broker"), None)
        if target is None:
            raise ValueError("Broker is absent from original approved review")
        if requested_tip != target["tips"]["dev"]:
            if len(history) >= 8:
                raise ValueError("Broker replan history is full")
            updated, proof = _extend(plan, requested_tip)
            receipts = journal.get("receipts", [])
            if (len(receipts) != len(plan["operations"])
                    or any(r.get("status") not in ("pending", "applied") for r in receipts)):
                raise ValueError("Broker replan requires a certain, checkpointed recovery suffix")
            for index in proof["changed_operations"]:
                old, new = plan["operations"][index], updated["operations"][index]
                if (receipts[index]["status"] != "pending"
                        or receipts[index]["operation_hash"] != revision.digest(old)
                        or str(index) in record.data.get("integration_write_journal", {}).get("operations", {})):
                    raise ValueError("Broker replan cannot change an applied or fenced operation")
                remap[index] = (revision.digest(old), revision.digest(new))
            history.append({"change": proof, "hash": revision.digest(proof), "approval_hash": None})
            _bounded(history)
            plan = updated
    return plan, history, remap


def _bounded(history):
    if len(json.dumps(history, ensure_ascii=True)) > 8 * 1024 * 1024:
        raise ValueError("Broker replan evidence exceeds the bounded journal")


def _extend(plan, after):
    before = deepcopy(plan)
    plan = deepcopy(plan)
    repos = [r for r in plan["preconditions"]["repos"] if r["target"]["key"] == "broker"]
    if len(repos) != 1:
        raise ValueError("Broker replan requires one exact Broker repository")
    repo = repos[0]
    target = repo["target"]
    if (target["tool"] != "gh" or target["gh_repo"] != "msft.ghe.com/security/ad-accounts-for-android"):
        raise ValueError("Broker replan repository identity differs")
    root = G.clean_repository(target["root"])
    old = repo["tips"].get("dev")
    G.object_id(after)
    if old == after:
        raise ValueError("Broker replan must change the target")
    if G._git(root, "merge-base", old, after).decode().strip() != old:
        raise ValueError("Broker target drift is not a fast-forward")
    pushes = [(i, op) for i, op in enumerate(plan["operations"])
              if op["kind"] == "integration.push" and op["target"]["key"] == "broker"]
    if len(pushes) != 1:
        raise ValueError("Broker replan requires one existing pending RI push")
    index, push = pushes[0]
    if push["target"]["base"] != "dev" or push["preconditions"]["base_tip"] != old:
        raise ValueError("Broker replan target differs from approved dev")
    creates = [(i, op) for i, op in enumerate(plan["operations"])
               if op["kind"] == "integration.create" and op["target"] == push["target"]]
    if len(creates) != 1 or creates[0][0] != index + 1:
        raise ValueError("Broker replan requires the adjacent RI create")
    create_index, create = creates[0]
    if (create["preconditions"]["head_tip"] != push["content"]["commit_id"]
            or create["preconditions"]["base_tip"] != old
            or repo["tips"][push["target"]["head"]] != push["preconditions"]["head_tip"]):
        raise ValueError("Broker original source dependencies differ")
    content = G.plan_ri(str(root), push["preconditions"]["head_tip"], after, "dev")
    if not content["commit"] or content.get("resolved_gradle_conflicts"):
        raise ValueError("Broker target replan requires a conflict-free RI merge")
    push["content"] = content
    push["preconditions"]["base_tip"] = after
    create["preconditions"].update(head_tip=content["commit_id"], base_tip=after)
    repo["tips"]["dev"], repo["ri"] = after, deepcopy(content)
    proof = {
        "version": 1, "rule": "broker-dev-fast-forward", "before_plan_hash": revision.digest(before),
        "after_plan_hash": revision.digest(plan), "before_target": old, "after_target": after,
        "source_tip": push["preconditions"]["head_tip"], "changed_operations": [index, create_index],
        "before_content": before["operations"][index]["content"], "after_content": deepcopy(content),
        "target_commits": G._git(root, "log", "--format=%H %s", old + ".." + after).decode("utf-8"),
        "target_diff": G._git(root, "diff", "--no-ext-diff", "--no-textconv", "--binary", old, after).decode("utf-8"),
    }
    return plan, proof
