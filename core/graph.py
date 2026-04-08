"""Topology and graph-aware incident analysis."""

from __future__ import annotations

from collections import deque

import networkx as nx

from core.mock import get_service, list_service_names


def build_graph() -> nx.DiGraph:
    graph = nx.DiGraph()
    for service_name in list_service_names():
        graph.add_node(service_name)
    for service_name in list_service_names():
        service = get_service(service_name)
        if not service:
            continue
        for dependency in service["dependencies"]:
            graph.add_edge(dependency, service_name)
    return graph


GRAPH = build_graph()


def refresh_graph() -> None:
    GRAPH.clear()
    GRAPH.add_nodes_from(list_service_names())
    for service_name in list_service_names():
        service = get_service(service_name)
        if not service:
            continue
        for dependency in service["dependencies"]:
            GRAPH.add_edge(dependency, service_name)


def downstream_dependents(service_name: str) -> list[str]:
    if not GRAPH.has_node(service_name):
        return []
    seen = set()
    queue = deque([service_name])
    dependents = []
    while queue:
        current = queue.popleft()
        for child in GRAPH.successors(current):
            if child in seen:
                continue
            seen.add(child)
            dependents.append(child)
            queue.append(child)
    return dependents


def upstream_chain(service_name: str) -> list[str]:
    if not GRAPH.has_node(service_name):
        return []
    seen = set()
    queue = deque([service_name])
    chain = []
    while queue:
        current = queue.popleft()
        for parent in GRAPH.predecessors(current):
            if parent in seen:
                continue
            seen.add(parent)
            chain.append(parent)
            queue.append(parent)
    return chain


def get_dependency_chain(service_name: str) -> dict:
    service = get_service(service_name)
    if service is None:
        return {"error": f"service not found: {service_name}"}
    return {"service": service_name, "upstream": upstream_chain(service_name), "downstream": downstream_dependents(service_name)}


def get_service_graph() -> dict:
    refresh_graph()
    adjacency = {node: sorted(list(GRAPH.successors(node))) for node in GRAPH.nodes}
    return {"nodes": sorted(GRAPH.nodes), "adjacency": adjacency, "edges": sorted([list(edge) for edge in GRAPH.edges])}


def get_blast_radius(service_name: str) -> dict:
    refresh_graph()
    if not GRAPH.has_node(service_name):
        return {"error": f"service not found: {service_name}"}
    impacted = downstream_dependents(service_name)
    users = 0
    for impacted_service in impacted:
        service = get_service(impacted_service)
        if service:
            users += int(service["users"])
    return {"service": service_name, "impacted_services": impacted, "impacted_service_count": len(impacted), "estimated_users_impacted": users}


def detect_anomaly() -> list[dict]:
    refresh_graph()
    anomalies = []
    for service_name in list_service_names():
        service = get_service(service_name)
        if not service:
            continue
        if not downstream_dependents(service_name):
            continue
        current = service["current_metrics"]
        base = service["base_metrics"]
        std = service["metric_std"]
        deviations = {}
        for metric_name in ("cpu", "memory", "latency", "request_rate", "error_rate"):
            sigma = std[metric_name] or 1.0
            deviations[metric_name] = abs(current[metric_name] - base[metric_name]) / sigma
        if any(value > 2.0 for value in deviations.values()):
            anomalies.append({"service": service_name, "downstream_dependents": downstream_dependents(service_name), "current_metrics": current, "baseline": base, "deviations_sigma": deviations})
    return anomalies


def get_incident_score() -> int:
    refresh_graph()
    score = 0.0
    for service_name in list_service_names():
        service = get_service(service_name)
        if not service:
            continue
        if service["status"] == "down":
            score += 28
        elif service["fault_state"]["degraded"]:
            score += 14
        if service["fault_state"]["latency_spike_ms"]:
            score += min(12, service["fault_state"]["latency_spike_ms"] / 20)
        if service["fault_state"]["memory_leak"]:
            score += 8
    score += len(detect_anomaly()) * 10
    if any(get_service(service_name)["status"] == "down" for service_name in list_service_names() if get_service(service_name)):
        score += 12
    return int(max(0, min(100, round(score))))
