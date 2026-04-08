"""Dependency graph, blast radius, and anomaly reasoning."""

from __future__ import annotations

from statistics import mean, pstdev

from core.services import DEPENDENCIES, SERVICE_ORDER, USER_WEIGHTS, downstream_services, latency_baseline, latency_samples, observe_latency, service_exists, service_metrics, service_status, upstream_services


def refresh_graph() -> None:
    return None


def downstream_dependents(service_name: str) -> list[str]:
    return downstream_services(service_name)


def upstream_chain(service_name: str) -> list[str]:
    return upstream_services(service_name)


def get_dependency_chain(service_name: str) -> dict:
    if not service_exists(service_name):
        return {"ok": False, "error": {"code": "invalid_service", "message": f"invalid service name: {service_name}"}}
    return {
        "ok": True,
        "service": service_name,
        "upstream": upstream_chain(service_name),
        "downstream": downstream_dependents(service_name),
        "direct_dependencies": list(DEPENDENCIES.get(service_name, [])),
    }


def get_service_graph() -> dict:
    adjacency = {name: list(DEPENDENCIES.get(name, [])) for name in SERVICE_ORDER}
    reverse_adjacency = {name: downstream_dependents(name) for name in SERVICE_ORDER}
    return {"ok": True, "nodes": SERVICE_ORDER, "adjacency": adjacency, "reverse_adjacency": reverse_adjacency}


def get_blast_radius(service_name: str) -> dict:
    if not service_exists(service_name):
        return {"ok": False, "error": {"code": "invalid_service", "message": f"invalid service name: {service_name}"}}
    impacted = downstream_dependents(service_name)
    users = sum(USER_WEIGHTS.get(name, 0) for name in impacted)
    return {"ok": True, "service": service_name, "impacted_services": impacted, "impacted_service_count": len(impacted), "estimated_users_impacted": users}


def detect_anomaly() -> list[dict]:
    anomalies: list[dict] = []
    for service_name in SERVICE_ORDER:
        if not downstream_dependents(service_name):
            continue
        snapshot = service_metrics(service_name)
        if not snapshot.get("ok"):
            continue
        current_latency = snapshot.get("latency")
        if current_latency is None:
            continue
        baseline = latency_baseline(service_name)
        samples = latency_samples(service_name)
        if baseline is None:
            observe_latency(service_name, current_latency)
            continue
        avg = mean(samples) if samples else baseline
        std_dev = pstdev(samples) if len(samples) >= 3 else 0.0
        threshold = max(baseline * 1.5, avg + 2 * std_dev)
        if current_latency > threshold:
            anomalies.append(
                {
                    "service": service_name,
                    "downstream_dependents": downstream_dependents(service_name),
                    "current_latency": current_latency,
                    "baseline_latency": baseline,
                    "threshold": round(threshold, 2),
                    "std_dev": round(std_dev, 2),
                }
            )
    return anomalies


def get_incident_score() -> int:
    score = 0.0
    degraded_services = []
    for service_name in SERVICE_ORDER:
        snapshot = service_status(service_name)
        if snapshot.get("ok") is False:
            continue
        if snapshot.get("status") == "down" or not snapshot.get("reachable"):
            score += 25
            degraded_services.append(service_name)
        elif snapshot.get("status") == "degraded":
            score += 12
            degraded_services.append(service_name)

    blast_score = max((get_blast_radius(name).get("impacted_service_count", 0) for name in degraded_services), default=0)
    score += blast_score * 8
    score += len(detect_anomaly()) * 10

    from core.logs import count_errors_in_logs

    error_score = 0.0
    for service_name in SERVICE_ORDER:
        error_info = count_errors_in_logs(service_name, 50)
        if error_info.get("ok"):
            error_score += min(8.0, error_info.get("error_rate", 0.0) * 40.0)

    score += error_score
    return int(max(0, min(100, round(score))))
