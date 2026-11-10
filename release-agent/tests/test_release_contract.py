from orchestrator.release_contract import HoldKind, LifecycleStatus, lifecycle_status


def test_lifecycle_status_maps_focus_holds_consistently():
    assert lifecycle_status(
        suspension=None, frontier_exists=True, focus_kind=HoldKind.GATE.value
    ) == LifecycleStatus.HOLDING_GATE.value
    assert lifecycle_status(
        suspension=None, frontier_exists=True, focus_kind=HoldKind.SCHEDULED.value
    ) == LifecycleStatus.SCHEDULED.value
    assert lifecycle_status(
        suspension=None, frontier_exists=True, focus_kind=HoldKind.IN_FLIGHT.value
    ) == LifecycleStatus.RUNNING.value


def test_lifecycle_status_prefers_suspension_and_completion():
    assert lifecycle_status(
        suspension="halted", frontier_exists=True, focus_kind=HoldKind.GATE.value
    ) == LifecycleStatus.HALTED.value
    assert lifecycle_status(
        suspension=None, frontier_exists=False, focus_kind=None
    ) == LifecycleStatus.COMPLETE.value
