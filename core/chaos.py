"""Fault injection helpers for latency, kill, and memory leak simulation."""

from __future__ import annotations

import threading
import time

from core.graph import downstream_dependents, refresh_graph
from core.mock import get_service, now_iso, service_exists
from core.timeline import append_event


def _snapshot(service_name: str) -> dict | None:
    service = get_service(service_name)
    if not service:
        return None
    return {
        "status": service["status"],
        "last_restart": service["last_restart"],
        "replica_count": service["replica_count"],
        "fault_state": dict(service["fault_state"]),
        "current_metrics": dict(service["current_metrics"]),
        "version": service["version"],
    }


def inject_latency(service_name: str, latency_spike_ms: int, duration_seconds: int, triggered_by: str | None = None) -> dict:
    """Add a transient latency spike to a service and auto-revert it."""

    if not service_exists(service_name):
        return {"error": f"service not found: {service_name}"}
    if latency_spike_ms < 0 or duration_seconds <= 0:
        return {"error": "latency_spike_ms must be >= 0 and duration_seconds must be > 0"}

    service = get_service(service_name)
    before = _snapshot(service_name)
    service["fault_state"]["latency_spike_ms"] = latency_spike_ms
    service["fault_state"]["latency_spike_until"] = time.time() + duration_seconds
    service["fault_state"]["triggered_by"] = triggered_by
    service["current_metrics"]["latency"] += latency_spike_ms
    after = _snapshot(service_name)
    append_event("inject_latency", service_name, before, after, triggered_by)

    def revert() -> None:
        time.sleep(duration_seconds)
        current = get_service(service_name)
        if not current:
            return
        if current["fault_state"]["latency_spike_ms"] == latency_spike_ms:
            before_revert = _snapshot(service_name)
            current["fault_state"]["latency_spike_ms"] = 0
            current["fault_state"]["latency_spike_until"] = None
            after_revert = _snapshot(service_name)
            append_event("inject_latency_revert", service_name, before_revert, after_revert, triggered_by)

    threading.Thread(target=revert, daemon=True).start()
    return {"service": service_name, "latency_spike_ms": latency_spike_ms, "duration_seconds": duration_seconds, "active_until": service["fault_state"]["latency_spike_until"]}


def kill_service(service_name: str, triggered_by: str | None = None) -> dict:
    """Bring a service down and cascade degradation to direct downstream dependents."""

    if not service_exists(service_name):
        return {"error": f"service not found: {service_name}"}

    refresh_graph()
    service = get_service(service_name)
    before = _snapshot(service_name)
    service["status"] = "down"
    service["fault_state"]["killed"] = True
    service["fault_state"]["degraded"] = False
    service["fault_state"]["triggered_by"] = triggered_by
    service["current_metrics"]["request_rate"] = 0.0
    service["current_metrics"]["latency"] = max(service["current_metrics"]["latency"], 1000.0)

    dependents = downstream_dependents(service_name)
    for dependent_name in dependents:
        dependent = get_service(dependent_name)
        if dependent and dependent["status"] != "down":
            dependent_before = _snapshot(dependent_name)
            dependent["fault_state"]["degraded"] = True
            dependent["fault_state"]["triggered_by"] = service_name
            dependent["current_metrics"]["error_rate"] += 4.0
            dependent["current_metrics"]["latency"] += 40.0
            append_event("cascade_degrade", dependent_name, dependent_before, _snapshot(dependent_name), service_name)

    after = _snapshot(service_name)
    append_event("kill_service", service_name, before, after, triggered_by)
    return {"service": service_name, "status": "down", "cascaded_dependents": dependents}


def simulate_memory_leak(service_name: str, triggered_by: str | None = None) -> dict:
    """Start a daemon thread that increases memory until the service is restarted."""

    if not service_exists(service_name):
        return {"error": f"service not found: {service_name}"}

    service = get_service(service_name)
    before = _snapshot(service_name)
    service["fault_state"]["memory_leak"] = True
    service["fault_state"]["memory_leak_stop"] = False
    service["fault_state"]["triggered_by"] = triggered_by
    after = _snapshot(service_name)
    append_event("simulate_memory_leak", service_name, before, after, triggered_by)

    def leak() -> None:
        while True:
            time.sleep(10)
            current = get_service(service_name)
            if not current or current["fault_state"]["memory_leak_stop"]:
                return
            if current["status"] == "down":
                continue
            current["current_metrics"]["memory"] = min(99.0, current["current_metrics"]["memory"] + 2.0)

    threading.Thread(target=leak, daemon=True).start()
    return {"service": service_name, "memory_leak": True, "increment": 2, "interval_seconds": 10}


def clear_faults(service_name: str, triggered_by: str | None = None) -> dict:
    """Reset a service and recover its downstream dependents."""

    if not service_exists(service_name):
        return {"error": f"service not found: {service_name}"}
    refresh_graph()
    service = get_service(service_name)
    before = _snapshot(service_name)
    service["status"] = "healthy"
    service["fault_state"] = {
        "latency_spike_ms": 0,
        "latency_spike_until": None,
        "killed": False,
        "memory_leak": False,
        "memory_leak_stop": True,
        "degraded": False,
        "triggered_by": triggered_by,
    }
    service["last_restart"] = now_iso()
    service["current_metrics"].update(service["base_metrics"])
    after = _snapshot(service_name)
    append_event("restart_service", service_name, before, after, triggered_by)

    for dependent_name in downstream_dependents(service_name):
        dependent = get_service(dependent_name)
        if dependent and dependent["fault_state"]["degraded"]:
            dependent_before = _snapshot(dependent_name)
            dependent["fault_state"]["degraded"] = False
            dependent["fault_state"]["triggered_by"] = triggered_by
            dependent["status"] = "healthy" if dependent["status"] != "down" else dependent["status"]
            dependent["current_metrics"]["error_rate"] = max(0.0, dependent["base_metrics"]["error_rate"])
            dependent["current_metrics"]["latency"] = max(dependent["base_metrics"]["latency"], dependent["current_metrics"]["latency"] * 0.7)
            append_event("cascade_recover", dependent_name, dependent_before, _snapshot(dependent_name), service_name)

    return {"service": service_name, "status": "healthy", "recovered_dependents": downstream_dependents(service_name)}
