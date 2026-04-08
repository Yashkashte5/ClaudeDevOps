"""Chaos controls using SSH for kill/restart and local state for simulated faults."""

from __future__ import annotations

import threading
import time

from core.graph import downstream_dependents
from core.services import (
    DEFAULT_MEMORY_BASELINES,
    bump_memory,
    error_response,
    fault_state,
    get_live_snapshot,
    mark_killed,
    memory_percent,
    observe_latency,
    reset_latency_spike,
    reset_memory_leak,
    run_ssh,
    service_exists,
    SERVICE_ORDER,
    service_port,
    service_status,
    service_replica_count,
    set_fault_state,
    set_service_replica_count,
    set_memory_percent,
    service_version,
    set_service_version,
    rollback_service_version,
)
from core.timeline import append_event


def _wait_for_reachability(service_name: str, expected_reachable: bool, attempts: int = 10, interval_seconds: float = 1.0) -> dict:
    """Poll live status until reachability matches the expected value."""

    last = service_status(service_name)
    for _ in range(attempts):
        last = service_status(service_name)
        if bool(last.get("reachable")) == expected_reachable:
            return {"ok": True, "status": last}
        time.sleep(interval_seconds)
    return error_response(
        "service state transition did not complete in time",
        service_name=service_name,
        code="transition_timeout",
        details={"expected_reachable": expected_reachable, "last_status": last},
    )


def _snapshot(service_name: str) -> dict:
    live = get_live_snapshot(service_name)
    return {
        "service": service_name,
        "version": service_version(service_name),
        "replica_count": service_replica_count(service_name),
        "live": live,
        "fault_state": fault_state(service_name),
        "memory": round(memory_percent(service_name), 2),
    }


def inject_latency(service_name: str, latency_spike_ms: int, duration_seconds: int, triggered_by: str | None = None) -> dict:
    """Add a transient latency spike to a service and auto-revert it."""

    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")
    if latency_spike_ms < 0 or duration_seconds <= 0:
        return error_response("latency_spike_ms must be >= 0 and duration_seconds must be > 0", service_name=service_name, code="invalid_argument")
    if fault_state(service_name).get("latency_spike_ms"):
        return error_response("latency spike already active", service_name=service_name, code="fault_conflict")

    before = _snapshot(service_name)
    set_fault_state(service_name, latency_spike_ms=latency_spike_ms, latency_spike_until=time.time() + duration_seconds, triggered_by=triggered_by)
    live = service_status(service_name)
    if live.get("latency") is not None:
        observe_latency(service_name, float(live["latency"]))
    after = _snapshot(service_name)
    append_event("inject_latency", service_name, before, after, triggered_by, details={"latency_spike_ms": latency_spike_ms, "duration_seconds": duration_seconds})

    def revert() -> None:
        time.sleep(duration_seconds)
        if fault_state(service_name).get("latency_spike_ms") == latency_spike_ms:
            before_revert = _snapshot(service_name)
            reset_latency_spike(service_name)
            after_revert = _snapshot(service_name)
            append_event("inject_latency_revert", service_name, before_revert, after_revert, triggered_by)

    threading.Thread(target=revert, daemon=True).start()
    return {"ok": True, "service": service_name, "latency_spike_ms": latency_spike_ms, "duration_seconds": duration_seconds, "active_until": fault_state(service_name).get("latency_spike_until"), "simulation": True}


def kill_service(service_name: str, triggered_by: str | None = None) -> dict:
    """SSH into EC2 and stop the service process."""

    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")

    port = service_port(service_name)
    before = _snapshot(service_name)
    command = (
        f"PIDS=$(pgrep -f '[u]vicorn service:app --host 0.0.0.0 --port {port}' || true); "
        f"if [ -n \"$PIDS\" ]; then kill $PIDS || true; fi; "
        f"exit 0"
    )
    ssh_result = run_ssh(command)
    if "error" in ssh_result:
        return ssh_result

    transition = _wait_for_reachability(service_name, expected_reachable=False, attempts=12, interval_seconds=0.75)
    if "error" in transition:
        return transition

    mark_killed(service_name, True, triggered_by=triggered_by)
    set_fault_state(service_name, degraded=False, triggered_by=triggered_by)

    dependents = downstream_dependents(service_name)
    for dependent_name in dependents:
        dependent_before = _snapshot(dependent_name)
        set_fault_state(dependent_name, degraded=True, triggered_by=service_name)
        append_event("cascade_degrade", dependent_name, dependent_before, _snapshot(dependent_name), service_name)

    after = _snapshot(service_name)
    append_event("kill_service", service_name, before, after, triggered_by)
    return {
        "ok": True,
        "service": service_name,
        "status": "down",
        "ssh": ssh_result,
        "transition": transition,
        "cascaded_dependents": dependents,
    }


def restart_service(service_name: str, triggered_by: str | None = None) -> dict:
    """Restart a service via SSH and recover its dependents."""

    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")

    port = service_port(service_name)
    before = _snapshot(service_name)
    service_index = SERVICE_ORDER.index(service_name) + 1

    restart_cmd = (
        f"cd /home/ubuntu && "
        f"export PATH=\"/home/ubuntu/.local/bin:$PATH\"; "
        f"mkdir -p /home/ubuntu/claudedevops; "
        f"fuser -k {port}/tcp || true; "
        f"nohup uvicorn service:app --host 0.0.0.0 --port {port} > /home/ubuntu/claudedevops/log{service_index}.txt 2>&1 & "
        f"exit 0"
    )
    restart_result = run_ssh(restart_cmd)
    if "error" in restart_result:
        return restart_result

    mark_killed(service_name, False, triggered_by=triggered_by)
    reset_latency_spike(service_name)
    reset_memory_leak(service_name)
    set_memory_percent(service_name, DEFAULT_MEMORY_BASELINES[service_name])
    set_fault_state(service_name, degraded=False, memory_leak=False, memory_leak_stop=True, latency_spike_ms=0, latency_spike_until=None, triggered_by=triggered_by)

    after = _snapshot(service_name)
    append_event("restart_service", service_name, before, after, triggered_by)

    recovered = []
    for dependent_name in downstream_dependents(service_name):
        if fault_state(dependent_name).get("degraded"):
            dependent_before = _snapshot(dependent_name)
            set_fault_state(dependent_name, degraded=False, triggered_by=triggered_by)
            append_event("cascade_recover", dependent_name, dependent_before, _snapshot(dependent_name), service_name)
            recovered.append(dependent_name)

    return {"service": service_name, "action": "restarted", "port": port}


def simulate_memory_leak(service_name: str, triggered_by: str | None = None) -> dict:
    """Artificially increase local memory state until restart clears it."""

    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")
    if fault_state(service_name).get("memory_leak"):
        return error_response("memory leak already active", service_name=service_name, code="fault_conflict")

    before = _snapshot(service_name)
    set_fault_state(service_name, memory_leak=True, memory_leak_stop=False, triggered_by=triggered_by)
    after = _snapshot(service_name)
    append_event("simulate_memory_leak", service_name, before, after, triggered_by, details={"increment": 2, "interval_seconds": 10})

    def leak() -> None:
        while True:
            time.sleep(10)
            if fault_state(service_name).get("memory_leak_stop"):
                return
            bump_memory(service_name, 2.0)

    threading.Thread(target=leak, daemon=True).start()
    return {"ok": True, "service": service_name, "memory_leak": True, "increment": 2, "interval_seconds": 10, "simulation": True}


def scale_service(service_name: str, replica_count: int, triggered_by: str | None = None) -> dict:
    """Adjust the local replica count and return a throughput estimate."""

    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")
    if replica_count < 1 or replica_count > 10:
        return error_response("replica_count must be between 1 and 10", service_name=service_name, code="invalid_argument")

    before = _snapshot(service_name)
    set_service_replica_count(service_name, replica_count)
    live = service_status(service_name)
    latency = live.get("latency") or 0.0
    throughput_estimate = round(max(0.0, (1000.0 / max(1.0, float(latency)))) * replica_count, 2)
    after = _snapshot(service_name)
    append_event(
        "scale_service",
        service_name,
        before,
        after,
        triggered_by,
        details={"replica_count": replica_count, "throughput_estimate": throughput_estimate},
    )
    return {
        "ok": True,
        "service": service_name,
        "before_replica_count": before.get("replica_count"),
        "replica_count": replica_count,
        "throughput_estimate": throughput_estimate,
    }


def rollback_service(service_name: str, triggered_by: str | None = None) -> dict:
    """Roll the local version metadata back to the previous value and log the event."""

    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")

    before = _snapshot(service_name)
    result = rollback_service_version(service_name)
    after = _snapshot(service_name)
    append_event("rollback_service", service_name, before, after, triggered_by, details=result)
    return {"ok": True, **result}
