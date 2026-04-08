"""Incident timeline logging and replay helpers."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone

TIMELINE = deque(maxlen=500)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def append_event(tool_name: str, service_name: str | None, before_state, after_state, triggered_by: str | None, details=None) -> dict:
    event = {
        "timestamp": utcnow().isoformat(),
        "action": tool_name,
        "tool_name": tool_name,
        "service": service_name,
        "service_name": service_name,
        "before_state": before_state,
        "after_state": after_state,
        "triggered_by": triggered_by,
    }
    if details is not None:
        event["details"] = details
    TIMELINE.append(event)
    return event


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_incident_timeline() -> list[dict]:
    return list(TIMELINE)


def replay_incident(start_time: str, end_time: str) -> dict:
    start = _parse_ts(start_time)
    end = _parse_ts(end_time)
    if end < start:
        return {"ok": False, "error": {"code": "invalid_time_window", "message": "end_time must be after start_time"}}
    chain = [event for event in TIMELINE if start <= _parse_ts(event["timestamp"]) <= end]
    chain.sort(key=lambda event: event["timestamp"])
    return {"ok": True, "start_time": start.isoformat(), "end_time": end.isoformat(), "event_count": len(chain), "causal_chain": chain}
