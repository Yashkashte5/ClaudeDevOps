"""Live EC2 service registry, HTTP probing, SSM execution, and local fault state."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import boto3


def _load_env_file() -> None:
    """Load key=value pairs from a project .env file if present."""

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()

SERVICE_HOST = os.getenv("CLAUDEDEVOPS_SERVICE_HOST", "43.205.127.108")
PUBLIC_IP = SERVICE_HOST
INSTANCE_ID = os.getenv("CLAUDEDEVOPS_INSTANCE_ID", "<your-ec2-instance-id>")
AWS_REGION = os.getenv("CLAUDEDEVOPS_AWS_REGION", "ap-south-1")
ssm = boto3.client("ssm", region_name=AWS_REGION)

SERVICE_PORTS = {
    "api-gateway": 8001,
    "auth-service": 8002,
    "user-service": 8003,
    "payment-service": 8004,
    "notification-service": 8005,
}

SERVICES = {
    name: f"http://{SERVICE_HOST}:{port}" for name, port in SERVICE_PORTS.items()
}

DEPENDENCIES = {
    "api-gateway": ["auth-service", "user-service"],
    "user-service": ["payment-service"],
    "payment-service": ["notification-service"],
}

SERVICE_ORDER = list(SERVICES.keys())
SERVICE_DESCRIPTIONS = {
    "api-gateway": "Entry point for external traffic.",
    "auth-service": "Identity, authentication, and token issuance.",
    "user-service": "User profile and account orchestration.",
    "payment-service": "Billing, authorization, and settlement flows.",
    "notification-service": "Email, push, and webhook delivery.",
}
DEFAULT_VERSIONS = {
    "api-gateway": "1.4.2",
    "auth-service": "2.1.0",
    "user-service": "3.3.1",
    "payment-service": "4.0.4",
    "notification-service": "1.9.8",
}
DEFAULT_REPLICA_COUNTS = {
    "api-gateway": 3,
    "auth-service": 2,
    "user-service": 3,
    "payment-service": 2,
    "notification-service": 2,
}
USER_WEIGHTS = {
    "api-gateway": 25000,
    "auth-service": 15000,
    "user-service": 18000,
    "payment-service": 8000,
    "notification-service": 12000,
}
DEFAULT_MEMORY_BASELINES = {
    "api-gateway": 41.0,
    "auth-service": 44.0,
    "user-service": 48.0,
    "payment-service": 52.0,
    "notification-service": 39.0,
}

SSM_COMMAND_TIMEOUT = int(os.getenv("CLAUDEDEVOPS_SSM_COMMAND_TIMEOUT", "45"))
HTTP_TIMEOUT = float(os.getenv("CLAUDEDEVOPS_HTTP_TIMEOUT", "5"))

_SESSION = requests.Session()
_LOCK = threading.RLock()

STATE: dict[str, dict[str, Any]] = {
    name: {
        "version": DEFAULT_VERSIONS[name],
        "version_history": [DEFAULT_VERSIONS[name]],
        "replica_count": DEFAULT_REPLICA_COUNTS[name],
        "latency_baseline": None,
        "latency_samples": deque(maxlen=20),
        "memory_percent": DEFAULT_MEMORY_BASELINES[name],
        "fault_state": {
            "latency_spike_ms": 0,
            "latency_spike_until": None,
            "memory_leak": False,
            "memory_leak_stop": False,
            "killed": False,
            "degraded": False,
            "triggered_by": None,
        },
        "last_observation": None,
    }
    for name in SERVICE_ORDER
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def service_exists(service_name: str) -> bool:
    return service_name in SERVICES


def list_service_names() -> list[str]:
    return list(SERVICE_ORDER)


def service_url(service_name: str) -> str:
    if not service_exists(service_name):
        raise KeyError(service_name)
    return SERVICES[service_name]


def service_port(service_name: str) -> int:
    return int(SERVICE_PORTS[service_name])


def service_metadata(service_name: str) -> dict[str, Any]:
    if not service_exists(service_name):
        return {"error": f"invalid service name: {service_name}"}
    return {
        "service": service_name,
        "base_url": service_url(service_name),
        "port": service_port(service_name),
        "dependencies": list(DEPENDENCIES.get(service_name, [])),
        "description": SERVICE_DESCRIPTIONS[service_name],
        "version": STATE[service_name]["version"],
        "replica_count": STATE[service_name]["replica_count"],
    }


def direct_dependencies(service_name: str) -> list[str]:
    return list(DEPENDENCIES.get(service_name, [])) if service_exists(service_name) else []


def _reverse_graph() -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {name: [] for name in SERVICE_ORDER}
    for upstream, deps in DEPENDENCIES.items():
        for dependency in deps:
            reverse.setdefault(dependency, []).append(upstream)
    return reverse


def downstream_services(service_name: str) -> list[str]:
    if not service_exists(service_name):
        return []
    reverse = _reverse_graph()
    seen: set[str] = set()
    queue = [service_name]
    impacted: list[str] = []
    while queue:
        current = queue.pop(0)
        for child in reverse.get(current, []):
            if child in seen:
                continue
            seen.add(child)
            impacted.append(child)
            queue.append(child)
    return impacted


def upstream_services(service_name: str) -> list[str]:
    if not service_exists(service_name):
        return []
    seen: set[str] = set()
    queue = [service_name]
    upstream: list[str] = []
    while queue:
        current = queue.pop(0)
        for dependency in DEPENDENCIES.get(current, []):
            if dependency in seen:
                continue
            seen.add(dependency)
            upstream.append(dependency)
            queue.append(dependency)
    return upstream


def _request_json(url: str) -> tuple[requests.Response | None, dict[str, Any] | None, str | None]:
    try:
        response = _SESSION.get(url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text}
        return response, payload, None
    except requests.RequestException as exc:
        return None, None, str(exc)


def _coerce_float(value: Any, fallback: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def latency_score(latency: float) -> int:
    if latency < 100:
        return 100
    if latency < 200:
        return 75
    if latency < 300:
        return 50
    return 20


def _apply_local_faults(service_name: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    state = STATE[service_name]["fault_state"]
    adjusted = dict(snapshot)
    adjusted["local_faults"] = dict(state)
    adjusted["memory"] = round(float(STATE[service_name]["memory_percent"]), 2)

    if state["killed"]:
        adjusted["reachable"] = False
        adjusted["status"] = "unhealthy"
        adjusted["latency"] = None
        adjusted["uptime"] = None
        adjusted["health_score"] = 0
        adjusted["score"] = 0
        return adjusted

    latency = adjusted.get("latency")
    if latency is not None and state["latency_spike_ms"]:
        adjusted["latency"] = round(float(latency) + float(state["latency_spike_ms"]), 2)

    if state.get("degraded"):
        adjusted["status"] = "degraded"
        if adjusted.get("latency") is not None:
            adjusted["latency"] = round(float(adjusted["latency"]) + 40.0, 2)

    if state["memory_leak"]:
        adjusted["memory"] = round(float(STATE[service_name]["memory_percent"]), 2)

    if adjusted.get("reachable") and adjusted.get("latency") is not None:
        health_score = latency_score(float(adjusted["latency"]))
        adjusted["health_score"] = health_score
        adjusted["score"] = health_score
        if adjusted.get("status") == "healthy":
            adjusted["status"] = "healthy" if health_score >= 60 else "degraded"
    return adjusted


def _update_baseline(service_name: str, latency: float | None) -> None:
    if latency is None:
        return
    state = STATE[service_name]
    state["latency_samples"].append(float(latency))
    if state["latency_baseline"] is None:
        state["latency_baseline"] = float(latency)
        return
    if not state["fault_state"]["latency_spike_ms"] and not state["fault_state"]["killed"]:
        state["latency_baseline"] = round(0.8 * float(state["latency_baseline"]) + 0.2 * float(latency), 2)


def probe_service(service_name: str) -> dict[str, Any]:
    if not service_exists(service_name):
        return {"ok": False, "error": {"code": "invalid_service", "message": f"invalid service name: {service_name}"}}

    url = f"{service_url(service_name)}/health"
    response, payload, error = _request_json(url)
    if error:
        snapshot = {
            "service": service_name,
            "base_url": service_url(service_name),
            "reachable": False,
            "status": "unhealthy",
            "latency": None,
            "uptime": None,
            "error": error,
            "raw": None,
            "health_score": 0,
            "score": 0,
        }
        with _LOCK:
            STATE[service_name]["last_observation"] = snapshot
        return _apply_local_faults(service_name, snapshot)

    latency = _coerce_float(payload.get("latency"), None)
    uptime = _coerce_float(payload.get("uptime"), None)
    status = str(payload.get("status", "healthy")).lower()
    snapshot = {
        "service": service_name,
        "base_url": service_url(service_name),
        "reachable": True,
        "status": status,
        "latency": latency,
        "uptime": uptime,
        "http_status": response.status_code,
        "raw": payload,
    }
    adjusted = _apply_local_faults(service_name, snapshot)
    _update_baseline(service_name, adjusted.get("latency"))
    with _LOCK:
        STATE[service_name]["last_observation"] = adjusted
    return adjusted


def get_live_snapshot(service_name: str) -> dict[str, Any]:
    return probe_service(service_name)


def service_health(service_name: str) -> dict[str, Any]:
    snapshot = probe_service(service_name)
    if snapshot.get("ok") is False and "error" in snapshot:
        return {
            "service": service_name,
            "status": "unhealthy",
            "latency": 0,
            "score": 0,
        }

    service_status = str(snapshot.get("status", "unknown")).lower()
    latency_value = snapshot.get("latency")
    score = latency_score(float(latency_value)) if latency_value is not None else 0
    final_status = "healthy"
    if service_status == "unhealthy":
        final_status = "unhealthy"
    elif service_status == "degraded":
        final_status = "degraded"
    else:
        final_status = "degraded" if score < 60 else "healthy"

    state = STATE[service_name]["fault_state"]
    if state.get("killed"):
        final_status = "unhealthy"
        score = 0
        latency_value = 0
    elif state.get("degraded") and final_status != "unhealthy":
        final_status = "degraded"

    return {
        "service": service_name,
        "status": final_status,
        "latency": latency_value if latency_value is not None else 0,
        "score": score,
    }


def service_status(service_name: str) -> dict[str, Any]:
    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")

    port = service_port(service_name)
    result = run_ssm_command(["ps aux | grep uvicorn || true"])
    if result.get("status") != "success":
        return {
            "service": service_name,
            "status": "failed",
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }

    target = f"--port {port}"
    lines = (result.get("stdout") or "").splitlines()
    reachable = any("uvicorn" in line and target in line for line in lines)

    return {
        "ok": True,
        "service": service_name,
        "base_url": service_url(service_name),
        "reachable": reachable,
        "status": "healthy" if reachable else "down",
        "latency": None,
        "uptime": None,
        "http_status": None,
        "version": STATE[service_name]["version"],
        "replica_count": STATE[service_name]["replica_count"],
    }


def service_metrics(service_name: str) -> dict[str, Any]:
    snapshot = probe_service(service_name)
    if snapshot.get("ok") is False and "error" in snapshot:
        return snapshot
    return {
        "ok": True,
        "service": service_name,
        "base_url": service_url(service_name),
        "latency": snapshot.get("latency"),
        "uptime": snapshot.get("uptime"),
        "status": snapshot.get("status"),
        "reachable": snapshot.get("reachable"),
        "baseline_latency": STATE[service_name]["latency_baseline"],
        "health_score": snapshot.get("health_score"),
        "memory": round(float(STATE[service_name]["memory_percent"]), 2),
        "replica_count": STATE[service_name]["replica_count"],
    }


def error_response(message: str, *, service_name: str | None = None, code: str = "error", details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": message}
    if code:
        payload["code"] = code
    if service_name:
        payload["service"] = service_name
    if details:
        payload["details"] = details
    return payload


def run_ssm_command(commands: list[str]) -> dict[str, Any]:
    """Execute commands via SSM and wait for terminal status with retries."""

    if INSTANCE_ID == "<your-ec2-instance-id>":
        return {"status": "failed", "stdout": "", "stderr": "CLAUDEDEVOPS_INSTANCE_ID is not set"}

    max_retries = 3
    timeout_seconds = 15
    poll_interval = 0.5

    for attempt in range(1, max_retries + 1):
        try:
            response = ssm.send_command(
                InstanceIds=[INSTANCE_ID],
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": commands},
                TimeoutSeconds=SSM_COMMAND_TIMEOUT,
            )
            command_id = response["Command"]["CommandId"]
            print(f"[ssm] command_id={command_id} attempt={attempt}")

            deadline = time.time() + timeout_seconds
            while time.time() < deadline:
                try:
                    invocation = ssm.get_command_invocation(CommandId=command_id, InstanceId=INSTANCE_ID)
                except Exception:
                    time.sleep(poll_interval)
                    continue

                status = invocation.get("Status", "Unknown")
                print(f"[ssm] command_id={command_id} status={status}")
                if status in {"Success", "Cancelled", "Failed", "TimedOut", "Undeliverable", "Terminated"}:
                    return {
                        "status": "success" if status == "Success" else "failed",
                        "stdout": (invocation.get("StandardOutputContent") or "").strip(),
                        "stderr": (invocation.get("StandardErrorContent") or "").strip(),
                        "command_id": command_id,
                    }
                time.sleep(poll_interval)

            if attempt == max_retries:
                return {
                    "status": "failed",
                    "stdout": "",
                    "stderr": f"ssm command timed out after {timeout_seconds}s",
                    "command_id": command_id,
                }
        except Exception as exc:
            if attempt == max_retries:
                return {"status": "failed", "stdout": "", "stderr": str(exc)}
            time.sleep(0.5)

    return {"status": "failed", "stdout": "", "stderr": "ssm command failed"}


def set_fault_state(service_name: str, **updates: Any) -> dict[str, Any]:
    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")
    with _LOCK:
        STATE[service_name]["fault_state"].update(updates)
        return dict(STATE[service_name]["fault_state"])


def fault_state(service_name: str) -> dict[str, Any]:
    if not service_exists(service_name):
        return {}
    return dict(STATE[service_name]["fault_state"])


def memory_percent(service_name: str) -> float:
    return float(STATE[service_name]["memory_percent"])


def set_memory_percent(service_name: str, value: float) -> None:
    with _LOCK:
        STATE[service_name]["memory_percent"] = max(0.0, min(100.0, float(value)))


def bump_memory(service_name: str, delta: float) -> float:
    with _LOCK:
        STATE[service_name]["memory_percent"] = max(0.0, min(100.0, float(STATE[service_name]["memory_percent"] + delta)))
        return float(STATE[service_name]["memory_percent"])


def latency_baseline(service_name: str) -> float | None:
    return STATE[service_name]["latency_baseline"]


def latency_samples(service_name: str) -> list[float]:
    return list(STATE[service_name]["latency_samples"])


def store_observation(service_name: str, snapshot: dict[str, Any]) -> None:
    with _LOCK:
        STATE[service_name]["last_observation"] = snapshot


def observe_latency(service_name: str, latency: float | None) -> None:
    _update_baseline(service_name, latency)


def get_last_observation(service_name: str) -> dict[str, Any] | None:
    obs = STATE[service_name]["last_observation"]
    return dict(obs) if isinstance(obs, dict) else obs


def current_health_score(service_name: str) -> int | None:
    obs = get_last_observation(service_name)
    if not obs:
        return None
    return obs.get("score", obs.get("health_score"))


def service_version(service_name: str) -> str:
    return str(STATE[service_name]["version"])


def service_version_history(service_name: str) -> list[str]:
    return list(STATE[service_name]["version_history"])


def set_service_version(service_name: str, version: str) -> dict[str, Any]:
    with _LOCK:
        STATE[service_name]["version"] = version
        STATE[service_name]["version_history"].append(version)
        return {"service": service_name, "version": version}


def rollback_service_version(service_name: str) -> dict[str, Any]:
    with _LOCK:
        history = STATE[service_name]["version_history"]
        if len(history) < 2:
            return {"service": service_name, "version": STATE[service_name]["version"], "rolled_back": False}
        current = history.pop()
        previous = history[-1]
        STATE[service_name]["version"] = previous
        return {"service": service_name, "previous_version": current, "current_version": previous, "rolled_back": True}


def service_replica_count(service_name: str) -> int:
    return int(STATE[service_name]["replica_count"])


def set_service_replica_count(service_name: str, replica_count: int) -> dict[str, Any]:
    with _LOCK:
        STATE[service_name]["replica_count"] = int(replica_count)
        return {"service": service_name, "replica_count": int(replica_count)}


def service_uptime_text(service_name: str) -> str:
    uptime = service_status(service_name).get("uptime")
    if uptime is None:
        return "unknown"
    total_seconds = int(float(uptime))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h {minutes}m {seconds}s"


def service_baseline_summary(service_name: str) -> dict[str, Any]:
    return {
        "service": service_name,
        "latency_baseline": STATE[service_name]["latency_baseline"],
        "sample_count": len(STATE[service_name]["latency_samples"]),
        "memory_percent": STATE[service_name]["memory_percent"],
        "fault_state": dict(STATE[service_name]["fault_state"]),
    }


def reset_memory_leak(service_name: str) -> None:
    with _LOCK:
        STATE[service_name]["fault_state"]["memory_leak"] = False
        STATE[service_name]["fault_state"]["memory_leak_stop"] = True


def reset_latency_spike(service_name: str) -> None:
    with _LOCK:
        STATE[service_name]["fault_state"]["latency_spike_ms"] = 0
        STATE[service_name]["fault_state"]["latency_spike_until"] = None


def mark_killed(service_name: str, killed: bool, triggered_by: str | None = None) -> None:
    with _LOCK:
        STATE[service_name]["fault_state"]["killed"] = killed
        STATE[service_name]["fault_state"]["triggered_by"] = triggered_by


def as_jsonable(mapping: dict[str, Any]) -> str:
    return json.dumps(mapping, ensure_ascii=True)
