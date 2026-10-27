"""Complete, offline provider fixtures; never calls Scout."""
from datetime import datetime, timezone


def spec(name="Worker", schedule="every 1 hour", **changes):
    return {
        "name": name, "description": "Test worker", "prompt": "Reviewed test prompt",
        "schedule": schedule, "model": "gpt-5.4", "enabled": True, "oneShot": False,
        "triggerType": "schedule", "conditionCheckInterval": 15,
        "browserHeadless": True, "teamsNotify": "never", **changes,
    }


def observed(*rows):
    return {"observed_at": datetime.now(timezone.utc).isoformat(),
            "complete": True, "automations": list(rows)}


def seed_driver(runs_root, state, config=None):
    """Acknowledge a synthetic canonical driver in an isolated CLI fixture."""
    from orchestrator import automations, cli_common as C
    from orchestrator.registry import AutomationRegistry
    config = config or C.DEFAULT_CONFIG
    worker = next(item for item in automations.plan(
        config, state.release_id, state.ccd, owner_timezone=state.timezone)["automations"]
        if item["slug"] == "push-reminders")
    registry = AutomationRegistry(str(runs_root), state.release_id)
    spec = worker["provider_spec"]
    entry = registry.prepare(**worker["registration"], spec=spec)
    claim = registry.reconcile_create(entry["key"], observed(), spec=spec,
                                     executor="offline-fixture", claim=True)
    registry.create_result(
        entry["key"], claim["attempt_id"], "created", "synthetic provider acknowledgement",
        automation_id=f"fixture-driver-{state.release_id}", spec=spec)
    return registry, worker
