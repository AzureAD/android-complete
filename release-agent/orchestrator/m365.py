"""Structured connector observations; no token acquisition or implicit provider calls."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json

from .step_context import thaw


def _instant(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Connector observation needs an offset-aware timestamp")
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Connector observation timestamp must include its timezone")
    return result.astimezone(timezone.utc)


def observation_request(tool, arguments, owner):
    return {
        "tool": tool, "arguments": thaw(arguments), "account": owner,
        "read_only": True,
        "observation_format": {
            "version": 1, "tool": tool, "arguments": thaw(arguments),
            "account": owner, "observed_at": "<actual UTC observation time>",
            "result": "<complete structured tool response>",
        },
    }


def profile_matches(profile, owner):
    if (not isinstance(profile, Mapping) or not isinstance(profile.get("id"), str)
            or not profile["id"].strip() or not isinstance(owner, str)):
        return False
    return any(isinstance(profile.get(key), str)
               and profile[key] == profile[key].strip()
               and profile[key].casefold() == owner.casefold()
               for key in ("userPrincipalName", "mail", "email"))


def validate_freshness(observed_at, *, now=None, max_age_seconds=900):
    if type(max_age_seconds) is not int or max_age_seconds < 1:
        raise ValueError("Connector observation lifetime must be a positive number of seconds")
    observed = _instant(observed_at)
    clock = _instant(now) if now is not None else datetime.now(timezone.utc)
    age = (clock - observed).total_seconds()
    if age < 0 or age > max_age_seconds:
        raise ValueError("Connector observation is stale or future-dated; rerun the exact read")


def validate_observation(value, *, tool, arguments, owner, now=None, max_age_seconds=900,
                         allow_text=False):
    """Validate host-recorded provenance, not independently authenticate its author."""
    if not isinstance(value, Mapping):
        raise ValueError("A structured connector observation is required; raw pasted results are not proof")
    value = thaw(value)
    if (type(value.get("version")) is not int or value["version"] != 1
            or value.get("tool") != tool or value.get("arguments") != thaw(arguments)):
        raise ValueError(f"Connector observation must match {tool} and its exact requested arguments")
    account = value.get("account")
    if (not isinstance(owner, str) or not owner.strip() or "@" not in owner
            or not isinstance(account, str) or account != account.strip()
            or account.casefold() != owner.casefold()):
        raise ValueError("Connector observation account differs from the release owner")
    validate_freshness(value.get("observed_at"), now=now, max_age_seconds=max_age_seconds)
    result = value.get("result")
    result_types = (dict, list, str) if allow_text and tool == "workiq_download_file" else (dict, list)
    if (not isinstance(result, result_types)
            or isinstance(result, dict) and (
                result.get("success") is False or result.get("error") or result.get("errors"))):
        raise ValueError("Connector read did not return a successful structured result")
    json.dumps(result, allow_nan=False)
    return result


def read_observation(path, **kwargs):
    with open(path, encoding="utf-8-sig") as stream:
        value = json.load(stream)
    return validate_observation(value, **kwargs)
