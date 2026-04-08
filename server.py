"""ClaudeDevops MCP FastMCP server."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from core.chaos import clear_faults as chaos_clear_faults
from core.chaos import inject_latency as chaos_inject_latency
from core.chaos import kill_service as chaos_kill_service
from core.chaos import simulate_memory_leak as chaos_simulate_memory_leak
from core.graph import detect_anomaly as graph_detect_anomaly
from core.graph import get_blast_radius as graph_get_blast_radius
from core.graph import get_dependency_chain as graph_get_dependency_chain
from core.graph import get_incident_score as graph_get_incident_score
from core.graph import get_service_graph as graph_get_service_graph
from core.mock import get_service, list_service_names, make_log_lines, now_iso, service_health_score, service_info_payload, service_status_payload, tick_service_metrics, uptime_duration_text
from core.timeline import append_event as timeline_append_event
from core.timeline import get_incident_timeline as timeline_get_incident_timeline
from core.timeline import replay_incident as timeline_replay_incident


mcp = FastMCP("ClaudeDevops")


def _error(message: str) -> dict[str, str]:
    return {"error": message}


def _service_exists(service_name: str) -> bool:
    return get_service(service_name) is not None


@mcp.tool()
def list_services() -> dict:
    """Return all services with a compact operational status summary."""

    services = []
    for service_name in list_service_names():
        service = get_service(service_name)
        if not service:
            continue
        services.append({"name": service_name, "status": service["status"], "health_score": service_health_score(service_name), "replica_count": service["replica_count"], "dependencies": list(service["dependencies"]), "last_restart": service["last_restart"]})
    return {"timestamp": now_iso(), "services": services}


@mcp.tool()
def get_service_health(service_name: str) -> dict:
    """Return the health score and coarse status for a service."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    score = service_health_score(service_name)
    status = "healthy" if score >= 80 else "degraded" if score >= 40 else "down"
    service = get_service(service_name)
    if service and service["status"] == "down":
        status = "down"
    return {"service": service_name, "health_score": score, "status": status}


@mcp.tool()
def get_service_status(service_name: str) -> dict:
    """Return uptime percentage, current state, and last restart timestamp."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    return service_status_payload(service_name)


@mcp.tool()
def get_logs(service_name: str, line_count: int = 10) -> dict:
    """Return the last N log lines for a service."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    line_count = max(1, min(100, line_count))
    return {"service": service_name, "logs": make_log_lines(service_name, line_count)}


@mcp.tool()
def get_metrics(service_name: str) -> dict:
    """Return live CPU, memory, request rate, and p95 latency metrics."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    return {"service": service_name, "metrics": tick_service_metrics(service_name)}


@mcp.tool()
def get_uptime(service_name: str) -> dict:
    """Return uptime duration since the last restart."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    return {"service": service_name, "uptime": uptime_duration_text(service_name)}


@mcp.tool()
def get_service_info(service_name: str) -> dict:
    """Return version, port, dependencies, and service description."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    return service_info_payload(service_name)


@mcp.tool()
def restart_service(service_name: str) -> dict:
    """Restart a service, clear active faults, and recover downstream dependents."""

    return chaos_clear_faults(service_name, triggered_by="restart_service")


@mcp.tool()
def scale_service(service_name: str, replica_count: int) -> dict:
    """Adjust a service replica count between 1 and 10."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    if replica_count < 1 or replica_count > 10:
        return _error("replica_count must be between 1 and 10")
    service = get_service(service_name)
    before = service["replica_count"]
    service["replica_count"] = replica_count
    throughput = min(service["base_metrics"]["request_rate"] * replica_count, service["base_metrics"]["request_rate"] * 10)
    service["current_metrics"]["request_rate"] = throughput
    timeline_append_event("scale_service", service_name, {"replica_count": before}, {"replica_count": replica_count, "throughput_request_rate": throughput}, "scale_service")
    return {"service": service_name, "before": before, "after": replica_count, "throughput_request_rate": round(throughput, 2)}


@mcp.tool()
def rollback_service(service_name: str) -> dict:
    """Roll a service back to its previous version and log the event."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    service = get_service(service_name)
    history = service.setdefault("version_history", [service["version"]])
    before = service["version"]
    if len(history) > 1:
        history.pop()
        service["version"] = history[-1]
    timeline_append_event("rollback_service", service_name, {"version": before}, {"version": service["version"]}, "rollback_service")
    return {"service": service_name, "previous_version": before, "current_version": service["version"]}


@mcp.tool()
def get_active_alerts() -> dict:
    """List services in degraded or down states with their current reason."""

    alerts = []
    for service_name in list_service_names():
        service = get_service(service_name)
        if not service:
            continue
        if service["status"] == "down" or service["fault_state"]["degraded"] or service["fault_state"]["latency_spike_ms"] or service["fault_state"]["memory_leak"]:
            reason = "down" if service["status"] == "down" else "degraded dependency chain" if service["fault_state"]["degraded"] else "latency spike" if service["fault_state"]["latency_spike_ms"] else "memory leak"
            alerts.append({"service": service_name, "status": service["status"], "reason": reason})
    return {"alerts": alerts}


@mcp.tool()
def get_error_rate(service_name: str, minutes: int = 5) -> dict:
    """Return estimated errors per minute over the last N minutes."""

    if not _service_exists(service_name):
        return _error(f"service not found: {service_name}")
    service = get_service(service_name)
    metrics = tick_service_metrics(service_name)
    estimated = round(metrics["error_rate"] * max(1, minutes), 2)
    service["last_error_rate"] = metrics["error_rate"]
    return {"service": service_name, "minutes": minutes, "errors_per_minute": round(metrics["error_rate"], 2), "estimated_errors_in_window": estimated}


@mcp.tool()
def get_dependency_chain(service_name: str) -> dict:
    """Return upstream and downstream dependency chains for a service."""

    return graph_get_dependency_chain(service_name)


@mcp.tool()
def get_service_graph() -> dict:
    """Return the service topology as adjacency data."""

    return graph_get_service_graph()


@mcp.tool()
def get_blast_radius(service_name: str) -> dict:
    """Return downstream impacted services and estimated users."""

    return graph_get_blast_radius(service_name)


@mcp.tool()
def detect_anomaly() -> dict:
    """Scan the full topology and flag anomalous services."""

    return {"anomalies": graph_detect_anomaly()}


@mcp.tool()
def get_incident_score() -> dict:
    """Return a current system criticality score between 0 and 100."""

    return {"incident_score": graph_get_incident_score()}


@mcp.tool()
def inject_latency(service_name: str, latency_spike_ms: int, duration_seconds: int) -> dict:
    """Spike a service latency for a period of time and auto-revert it."""

    return chaos_inject_latency(service_name, latency_spike_ms, duration_seconds, triggered_by="inject_latency")


@mcp.tool()
def kill_service(service_name: str) -> dict:
    """Bring a service down and cascade degradation to dependents."""

    return chaos_kill_service(service_name, triggered_by="kill_service")


@mcp.tool()
def simulate_memory_leak(service_name: str) -> dict:
    """Start a background memory leak simulation for a service."""

    return chaos_simulate_memory_leak(service_name, triggered_by="simulate_memory_leak")


@mcp.tool()
def replay_incident(start_time: str, end_time: str) -> dict:
    """Replay the causal chain of an incident between two timestamps."""

    return timeline_replay_incident(start_time, end_time)


@mcp.tool()
def get_incident_timeline() -> dict:
    """Return the full timestamped incident log."""

    return {"events": timeline_get_incident_timeline()}


if __name__ == "__main__":
    mcp.run(transport="stdio")
