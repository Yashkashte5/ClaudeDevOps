"""Simulated service state and live metric fluctuations."""

from __future__ import annotations

import copy
import random
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


SERVICE_ORDER = [
    "api-gateway",
    "auth-service",
    "user-service",
    "payment-service",
    "notification-service",
]

BASE_SERVICES = {
    "api-gateway": {
        "status": "healthy",
        "version": "1.4.2",
        "port": 8080,
        "replica_count": 3,
        "dependencies": [],
        "description": "Entry point for all customer traffic and request routing.",
        "base_metrics": {"cpu": 18.0, "memory": 36.0, "latency": 28.0, "request_rate": 2200.0, "error_rate": 0.2},
        "metric_std": {"cpu": 2.5, "memory": 3.0, "latency": 8.0, "request_rate": 180.0, "error_rate": 0.12},
        "users": 25000,
    },
    "auth-service": {
        "status": "healthy",
        "version": "2.1.0",
        "port": 8091,
        "replica_count": 2,
        "dependencies": ["api-gateway"],
        "description": "Validates sessions, issues tokens, and enforces identity policy.",
        "base_metrics": {"cpu": 21.0, "memory": 42.0, "latency": 46.0, "request_rate": 1400.0, "error_rate": 0.35},
        "metric_std": {"cpu": 3.0, "memory": 3.5, "latency": 12.0, "request_rate": 150.0, "error_rate": 0.18},
        "users": 15000,
    },
    "user-service": {
        "status": "healthy",
        "version": "3.3.1",
        "port": 8092,
        "replica_count": 3,
        "dependencies": ["api-gateway"],
        "description": "Owns user profiles, preferences, and account metadata.",
        "base_metrics": {"cpu": 24.0, "memory": 48.0, "latency": 64.0, "request_rate": 1700.0, "error_rate": 0.4},
        "metric_std": {"cpu": 3.5, "memory": 4.0, "latency": 14.0, "request_rate": 180.0, "error_rate": 0.2},
        "users": 18000,
    },
    "payment-service": {
        "status": "healthy",
        "version": "4.0.4",
        "port": 8093,
        "replica_count": 2,
        "dependencies": ["user-service"],
        "description": "Processes billing, authorization, and settlement workflows.",
        "base_metrics": {"cpu": 30.0, "memory": 51.0, "latency": 118.0, "request_rate": 780.0, "error_rate": 0.6},
        "metric_std": {"cpu": 4.0, "memory": 4.5, "latency": 20.0, "request_rate": 90.0, "error_rate": 0.24},
        "users": 8000,
    },
    "notification-service": {
        "status": "healthy",
        "version": "1.9.8",
        "port": 8094,
        "replica_count": 2,
        "dependencies": ["payment-service"],
        "description": "Sends email, push, and webhook notifications.",
        "base_metrics": {"cpu": 14.0, "memory": 34.0, "latency": 92.0, "request_rate": 620.0, "error_rate": 0.25},
        "metric_std": {"cpu": 2.0, "memory": 3.0, "latency": 16.0, "request_rate": 70.0, "error_rate": 0.12},
        "users": 12000,
    },
}


def _initial_service_payload(data: dict) -> dict:
    return {
        "status": data["status"],
        "version": data["version"],
        "version_history": [data["version"]],
        "port": data["port"],
        "replica_count": data["replica_count"],
        "dependencies": list(data["dependencies"]),
        "description": data["description"],
        "last_restart": utcnow().isoformat(),
        "fault_state": {
            "latency_spike_ms": 0,
            "latency_spike_until": None,
            "killed": False,
            "memory_leak": False,
            "memory_leak_stop": False,
            "degraded": False,
            "triggered_by": None,
        },
        "base_metrics": copy.deepcopy(data["base_metrics"]),
        "metric_std": copy.deepcopy(data["metric_std"]),
        "users": data["users"],
        "current_metrics": copy.deepcopy(data["base_metrics"]),
        "log_counter": 0,
        "last_error_rate": data["base_metrics"]["error_rate"],
    }


STATE = {"services": {name: _initial_service_payload(payload) for name, payload in BASE_SERVICES.items()}}


def service_exists(service_name: str) -> bool:
    return service_name in STATE["services"]


def list_service_names() -> list[str]:
    return list(SERVICE_ORDER)


def get_service(service_name: str) -> dict | None:
    return STATE["services"].get(service_name)


def now_iso() -> str:
    return utcnow().isoformat()


def _apply_dependency_penalty(service_name: str, metrics: dict) -> None:
    service = get_service(service_name)
    if not service:
        return
    if service["status"] == "down":
        metrics["request_rate"] = 0.0
        metrics["cpu"] = max(1.0, metrics["cpu"] * 0.3)
        metrics["memory"] = max(5.0, metrics["memory"] * 0.9)
        metrics["latency"] = max(metrics["latency"], 500.0)
        metrics["error_rate"] = min(100.0, metrics["error_rate"] + 40.0)
        return
    if service["fault_state"]["degraded"]:
        metrics["request_rate"] *= 0.7
        metrics["latency"] *= 1.4
        metrics["error_rate"] += 2.5


def _active_fault_penalty(service: dict, metrics: dict) -> None:
    fault_state = service["fault_state"]
    if fault_state["killed"]:
        metrics["request_rate"] = 0.0
        metrics["cpu"] = max(0.5, metrics["cpu"] * 0.25)
        metrics["memory"] = max(10.0, metrics["memory"] * 0.8)
        metrics["latency"] = max(metrics["latency"], 1000.0)
        metrics["error_rate"] = 100.0
    elif fault_state["latency_spike_ms"]:
        metrics["latency"] += float(fault_state["latency_spike_ms"])
        metrics["error_rate"] += max(0.0, fault_state["latency_spike_ms"] / 120.0)
    if fault_state["memory_leak"]:
        metrics["memory"] = min(99.0, metrics["memory"] + 2.0)
        metrics["error_rate"] += 0.3


def tick_service_metrics(service_name: str) -> dict:
    """Return a live metric snapshot with small random fluctuation."""

    service = get_service(service_name)
    if service is None:
        raise KeyError(service_name)

    metrics = copy.deepcopy(service["current_metrics"])
    for key, spread in (("cpu", 1.6), ("memory", 1.8), ("latency", 3.5), ("request_rate", 65.0), ("error_rate", 0.12)):
        metrics[key] = max(0.0, metrics[key] + random.uniform(-spread, spread))

    _active_fault_penalty(service, metrics)
    _apply_dependency_penalty(service_name, metrics)

    metrics["cpu"] = round(min(100.0, metrics["cpu"]), 2)
    metrics["memory"] = round(min(100.0, metrics["memory"]), 2)
    metrics["latency"] = round(max(0.0, metrics["latency"]), 2)
    metrics["request_rate"] = round(max(0.0, metrics["request_rate"]), 2)
    metrics["error_rate"] = round(max(0.0, metrics["error_rate"]), 2)

    service["current_metrics"].update(metrics)
    service["last_error_rate"] = metrics["error_rate"]
    return metrics


def service_health_score(service_name: str) -> int:
    service = get_service(service_name)
    if service is None:
        raise KeyError(service_name)
    metrics = tick_service_metrics(service_name)
    score = 100.0
    if service["status"] == "down":
        return 0
    score -= metrics["cpu"] * 0.22
    score -= metrics["memory"] * 0.18
    score -= max(0.0, metrics["latency"] - service["base_metrics"]["latency"]) * 0.20
    score -= metrics["error_rate"] * 5.0
    if service["fault_state"]["degraded"]:
        score -= 18
    if service["fault_state"]["latency_spike_ms"]:
        score -= min(20, service["fault_state"]["latency_spike_ms"] / 25)
    return int(max(0, min(100, round(score))))


def get_uptime_fraction(service_name: str) -> float:
    service = get_service(service_name)
    if service is None:
        raise KeyError(service_name)
    last_restart = datetime.fromisoformat(service["last_restart"])
    elapsed = (utcnow() - last_restart).total_seconds()
    return max(0.0, min(1.0, elapsed / 86400.0))


def uptime_duration_text(service_name: str) -> str:
    service = get_service(service_name)
    if service is None:
        raise KeyError(service_name)
    last_restart = datetime.fromisoformat(service["last_restart"])
    elapsed = utcnow() - last_restart
    seconds = int(elapsed.total_seconds())
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m {seconds}s"


def make_log_lines(service_name: str, limit: int) -> list[str]:
    service = get_service(service_name)
    if service is None:
        raise KeyError(service_name)
    metrics = tick_service_metrics(service_name)
    service["log_counter"] += 1
    log_id = service["log_counter"]
    base_message = f"{now_iso()} [{service_name}] req={metrics['request_rate']:.1f}/m cpu={metrics['cpu']:.1f}% mem={metrics['memory']:.1f}% p95={metrics['latency']:.1f}ms err={metrics['error_rate']:.2f}/m"
    lines = [base_message]
    if service["status"] == "down":
        lines.append(f"{now_iso()} [{service_name}] error service unavailable; reconnect attempts suppressed")
    elif service["fault_state"]["latency_spike_ms"]:
        lines.append(f"{now_iso()} [{service_name}] warning latency spike active +{service['fault_state']['latency_spike_ms']}ms")
    elif service["fault_state"]["memory_leak"]:
        lines.append(f"{now_iso()} [{service_name}] warning memory growth detected, leak watch enabled")
    elif service["fault_state"]["degraded"]:
        lines.append(f"{now_iso()} [{service_name}] warning degraded dependency path, retry budget tightening")
    lines.append(f"{now_iso()} [{service_name}] info log sequence {log_id} complete")
    return lines[-limit:]


def service_status_payload(service_name: str) -> dict:
    service = get_service(service_name)
    if service is None:
        raise KeyError(service_name)
    return {
        "name": service_name,
        "status": service["status"],
        "uptime_percent": round(get_uptime_fraction(service_name) * 100.0, 2),
        "last_restart": service["last_restart"],
        "replica_count": service["replica_count"],
        "fault_state": copy.deepcopy(service["fault_state"]),
    }


def service_info_payload(service_name: str) -> dict:
    service = get_service(service_name)
    if service is None:
        raise KeyError(service_name)
    return {
        "name": service_name,
        "version": service["version"],
        "port": service["port"],
        "replica_count": service["replica_count"],
        "dependencies": list(service["dependencies"]),
        "description": service["description"],
    }
