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
