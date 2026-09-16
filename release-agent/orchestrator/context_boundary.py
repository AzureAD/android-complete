"""Convert durable state to immutable inputs and apply authorized evidence only."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import fields

from .evidence import BrokerResource, PipelineEvidence, ReleaseVersions, StepData, UIFailureReminder
from .step_context import EvidenceCommitter, EvidenceView, ReleaseView, StepEvidence, freeze, thaw


def release_view(state):
    return ReleaseView(**{
        item.name: freeze(getattr(state, item.name))
        for item in fields(ReleaseView)
    })


def evidence_view(state):
    records = {}
    for key in state.steps:
        phase, step = key.split(".", 1)
        record = state.get_step(phase, step)
        records[key] = StepEvidence(**{
            item.name: freeze(getattr(record, item.name))
            for item in fields(StepEvidence)
        })
    return EvidenceView(
        freeze(records), freeze(state.pipeline_runs), freeze(state.resources),
        freeze(state.notification_deliveries),
    )


def _generation(record):
    return record.status, record.completed_at, record.invalidated_at, record.execution


def _reminder(record, update):
    import hashlib

    data = dict(record.data or {})
    generated = data.pop("ui_test_status_generated", {})
    human_note = record.note or ""
    length = generated.get("note_length", 0)
    if length:
        start = human_note.find("\U0001f9ea")
        if start >= 0 and hashlib.sha256(
            human_note[start:start + length].encode("utf-8")
        ).hexdigest() == generated.get("note_sha256"):
            human_note = (human_note[:start] + human_note[start + length:]).strip("\n")
    record.note = "\n".join(part for part in (update.note, human_note) if part)
    record.links = [link for link in (record.links or [])
                    if link not in generated.get("links", [])]
    added = [link for link in thaw(update.links) if link not in record.links]
    record.links.extend(added)
    for key in ("broker_failed_tests", "auth_failed_cases", "auth_failed_tests"):
        data.pop(key, None)
    if update.note:
        data.update(
            broker_failed_tests=update.broker_count,
            auth_failed_cases=thaw(update.failed_ids),
            auth_failed_tests=thaw(update.auth_failures),
            ui_test_status_generated={
                "note_length": len(update.note),
                "note_sha256": hashlib.sha256(update.note.encode("utf-8")).hexdigest(),
                "links": added,
            },
        )
    record.data = data


class EvidenceSession:
    """Execution-scoped evidence committer; no lifecycle mutation API."""
    def __init__(self, state, permit, validate, *, authority, durable):
        self.__state = state
        self.__permit = permit
        self.__validate = validate
        self.__durable = durable
        self.__authority = authority
        self.__snapshot = evidence_view(state)
        self.__versions = freeze(state.versions)

    def committer(self):
        if not self.__durable:
            raise ValueError("Read-only contexts cannot own a durable committer")
        return EvidenceCommitter(self.commit, self.read)

    def enable_durable(self):
        self.__validate(self.__permit)
        if evidence_view(self.__state) != self.__snapshot or self.__state.versions != self.__versions:
            raise ValueError("Evidence changed before effect authority was requested")
        self.__durable = True

    def read(self):
        self.__validate(self.__permit)
        return evidence_view(self.__state)

    def commit(self, update):
        if not self.__durable:
            raise ValueError("Read-only contexts cannot commit evidence")
        self.apply((update,), checkpoint=True)
        return self.__snapshot

    def apply(self, updates, *, checkpoint=False):
        self.__validate(self.__permit)
        if not isinstance(updates, tuple):
            raise TypeError("Outcome evidence updates must be a tuple")
        pid, sid = self.__permit.phase, self.__permit.step
        state = self.__state
        current = evidence_view(state)
        for update in updates:
            scope = self.__authority.for_update(type(update))
            if scope is None:
                if isinstance(update, UIFailureReminder):
                    raise ValueError("Unauthorized cross-step producer evidence")
                raise ValueError(f"Handler does not own {type(update).__name__} evidence")
            if isinstance(update, StepData):
                if current.step(pid, sid).data != self.__snapshot.step(pid, sid).data:
                    raise ValueError("Step evidence changed during invocation")
                previous = current.step(pid, sid).data
                if "last_write_review" in update.values and (
                        "last_write_review" not in previous
                        or update.values["last_write_review"] != previous["last_write_review"]):
                    raise ValueError("Closed write authorization is engine-owned evidence")
                if "last_approval" in update.values and (
                        "last_approval" not in previous or update.values["last_approval"] != previous["last_approval"]):
                    raise ValueError("Closed approval receipt is engine-owned evidence")
            elif isinstance(update, PipelineEvidence):
                if current.pipeline_runs != self.__snapshot.pipeline_runs:
                    raise ValueError("Pipeline evidence changed during invocation")
                _validate_pipeline_scope(scope.slot, current.pipeline_runs, update.values)
            elif isinstance(update, ReleaseVersions):
                if state.versions != self.__versions:
                    raise ValueError("Version evidence changed during invocation")
            elif isinstance(update, BrokerResource):
                if current.resources.get("broker_test_plan") != self.__snapshot.resources.get("broker_test_plan"):
                    raise ValueError("Broker resource changed during invocation")
            elif isinstance(update, UIFailureReminder):
                target = scope.target.split(".", 1)
                if _generation(current.step(*target)) != _generation(
                    self.__snapshot.step(*target)
                ):
                    raise ValueError("UI reminder generation changed during invocation")
            else:
                raise TypeError(f"Unsupported evidence update: {type(update).__name__}")
        before = (deepcopy(state.steps), deepcopy(state.pipeline_runs),
                  deepcopy(state.resources), deepcopy(state.versions))
        try:
            for update in updates:
                if isinstance(update, StepData):
                    record = state.get_step(pid, sid)
                    receipt = deepcopy(record.data.get("last_write_review"))
                    approval_receipt = deepcopy(record.data.get("last_approval"))
                    record.data = thaw(update.values)
                    if receipt is not None:
                        record.data["last_write_review"] = receipt
                    if approval_receipt is not None:
                        record.data["last_approval"] = approval_receipt
                    state.set_step(pid, sid, record)
                elif isinstance(update, PipelineEvidence):
                    state.pipeline_runs = thaw(update.values)
                elif isinstance(update, ReleaseVersions):
                    state.record_versions(thaw(update.values))
                elif isinstance(update, BrokerResource):
                    state.resources["broker_test_plan"] = thaw(update.values)
                elif isinstance(update, UIFailureReminder):
                    target = self.__authority.for_update(UIFailureReminder).target.split(".", 1)
                    record = state.get_step(*target)
                    _reminder(record, update)
                    state.set_step(*target, record)
            self.__validate(self.__permit)
            if checkpoint:
                state.checkpoint()
        except BaseException:
            state.steps, state.pipeline_runs, state.resources, state.versions = before
            raise
        self.__snapshot = evidence_view(state)
        self.__versions = freeze(state.versions)


def _validate_pipeline_scope(slot, previous, proposed):
    """A verifier may only replace its own lane, never another producer's evidence."""
    old, new = thaw(previous), thaw(proposed)
    lane = slot.value
    if not slot.rc_lane:
        old.pop(lane, None)
        new.pop(lane, None)
    else:
        old_rcs = old.pop("rcs", [])
        new_rcs = new.pop("rcs", [])
        if (not isinstance(new_rcs, list) or not isinstance(old_rcs, list)
                or any(not isinstance(entry, dict) for entry in (*old_rcs, *new_rcs))):
            raise ValueError("Pipeline RC evidence must be a list of records")
        if len(new_rcs) < len(old_rcs):
            raise ValueError("Pipeline evidence cannot remove RC iterations")
        for index, entry in enumerate(new_rcs):
            preserved = {k: v for k, v in entry.items() if k not in (lane, "resolved_at")}
            baseline = ({k: v for k, v in old_rcs[index].items()
                         if k not in (lane, "resolved_at")} if index < len(old_rcs)
                        else {"rc": entry.get("rc")})
            if preserved != baseline:
                raise ValueError("Pipeline update changed another producer's RC evidence")
    if old != new:
        raise ValueError("Pipeline update changed another producer's evidence")
