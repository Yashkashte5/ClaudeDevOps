"""ClaudeDevops MCP FastMCP server for live EC2 services."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from core.chaos import inject_latency as chaos_inject_latency
from core.chaos import kill_service as chaos_kill_service
from core.chaos import restart_service as chaos_restart_service
from core.graph import get_dependency_chain as graph_get_dependency_chain
from core.logs import read_logs
from core.s3 import list_s3_objects as s3_list_objects
from core.services import error_response, list_service_names, service_health, service_metrics, service_status, service_url


mcp = FastMCP("ClaudeDevops")


def _guard(service_name: str) -> dict | None:
    if service_name not in list_service_names():
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")
    return None


@mcp.tool()
def list_services() -> dict:
    """Return all services with base URLs and reachability."""

    services = []
    for service_name in list_service_names():
        status = service_status(service_name)
        services.append({"service": service_name, "base_url": service_url(service_name), "reachable": bool(status.get("reachable"))})
    return {"services": services}


@mcp.tool()
def get_service_health(service_name: str) -> dict:
    """Call /health and return the lightweight health payload."""

    guard = _guard(service_name)
    if guard:
        return guard
    return service_health(service_name)


@mcp.tool()
def get_service_status(service_name: str) -> dict:
    """Return reachable or down."""

    guard = _guard(service_name)
    if guard:
        return guard
    status = service_status(service_name)
    if "error" in status:
        return status
    return {"reachable": bool(status.get("reachable")), "status": status.get("status")}


@mcp.tool()
def get_metrics(service_name: str) -> dict:
    """Return latency and uptime."""

    guard = _guard(service_name)
    if guard:
        return guard
    metrics = service_metrics(service_name)
    if "error" in metrics:
        return metrics
    return {"latency": metrics.get("latency"), "uptime": metrics.get("uptime")}


@mcp.tool()
def get_logs(service_name: str, line_count: int = 10) -> dict:
    """Fetch logs from EC2 via SSM."""

    guard = _guard(service_name)
    if guard:
        return guard
    logs = read_logs(service_name, max(1, int(line_count)))
    if "error" in logs:
        return logs
    return {"logs": logs.get("lines", [])}


@mcp.tool()
def restart_service(service_name: str) -> dict:
    """Restart the service via SSM."""

    guard = _guard(service_name)
    if guard:
        return guard
    return chaos_restart_service(service_name, triggered_by="restart_service")


@mcp.tool()
def get_dependency_chain(service_name: str) -> dict:
    """Return upstream and downstream dependencies."""

    result = graph_get_dependency_chain(service_name)
    if "error" in result:
        return result
    return {"upstream": result.get("upstream", []), "downstream": result.get("downstream", [])}


@mcp.tool()
def kill_service(service_name: str) -> dict:
    """Kill the service process on its port."""

    guard = _guard(service_name)
    if guard:
        return guard
    return chaos_kill_service(service_name, triggered_by="kill_service")


@mcp.tool()
def list_s3_logs(prefix: str = "") -> dict:
    """List historical service logs from S3 bucket (claudedevops-logs).
    
    Args:
        prefix: Optional prefix to filter by service (e.g., 'api-gateway/')
    
    Returns:
        {"files": [{"key": "...", "last_modified": "..."}]} sorted by latest first
    """
    return s3_list_objects(prefix)


if __name__ == "__main__":
    mcp.run(transport="stdio")
