"""SSH-backed log retrieval and error counting."""

from __future__ import annotations

import json
import os
import shlex
from collections import OrderedDict
from typing import Any

from core.services import error_response, list_service_names, run_ssh, service_exists


DEFAULT_LOG_CANDIDATES = OrderedDict(
    {
        "api-gateway": ["/home/ubuntu/claudedevops/log1.txt"],
        "auth-service": ["/home/ubuntu/claudedevops/log2.txt"],
        "user-service": ["/home/ubuntu/claudedevops/log3.txt"],
        "payment-service": ["/home/ubuntu/claudedevops/log4.txt"],
        "notification-service": ["/home/ubuntu/claudedevops/log5.txt"],
    }
)


def _load_mapping() -> dict[str, list[str]]:
    raw = os.getenv("CLAUDEDEVOPS_LOG_FILES_JSON")
    if not raw:
        return dict(DEFAULT_LOG_CANDIDATES)
    try:
        parsed = json.loads(raw)
        mapping: dict[str, list[str]] = {}
        for service_name in list_service_names():
            value = parsed.get(service_name, [])
            if isinstance(value, str):
                mapping[service_name] = [value]
            elif isinstance(value, list):
                mapping[service_name] = [str(item) for item in value]
            else:
                mapping[service_name] = []
        return mapping
    except Exception:
        return dict(DEFAULT_LOG_CANDIDATES)


def candidate_log_files(service_name: str) -> list[str]:
    return _load_mapping().get(service_name, [])


def read_logs(service_name: str, line_count: int) -> dict[str, Any]:
    if not service_exists(service_name):
        return error_response(f"invalid service name: {service_name}", service_name=service_name, code="invalid_service")

    safe_line_count = max(1, int(line_count))
    quoted_candidates = " ".join(shlex.quote(candidate) for candidate in candidate_log_files(service_name))
    remote_command = (
        "for f in "
        + quoted_candidates
        + "; do "
        + "if [ -f \"$f\" ]; then "
        + "echo __LOG_FILE__:$f; "
        + f"tail -n {safe_line_count} \"$f\"; "
        + "exit 0; "
        + "fi; "
        + "done; "
        + "exit 3"
    )
    result = run_ssh(f"bash -lc {shlex.quote(remote_command)}")
    if result.get("ok") and result.get("stdout"):
        lines = result["stdout"].splitlines()
        if lines and lines[0].startswith("__LOG_FILE__:"):
            selected_file = lines[0].split(":", 1)[1]
            payload_lines = lines[1:]
            return {
                "ok": True,
                "service": service_name,
                "log_file": selected_file,
                "line_count": len(payload_lines),
                "lines": payload_lines,
            }

    return error_response(
        "unable to read logs from any candidate file",
        service_name=service_name,
        code="log_read_failed",
        details={"candidates": candidate_log_files(service_name)},
    )


def count_errors_in_logs(service_name: str, line_count: int = 100) -> dict[str, Any]:
    logs = read_logs(service_name, line_count)
    if not logs.get("ok"):
        return logs

    error_lines = [line for line in logs["lines"] if any(token in line.lower() for token in ("error", "exception", "traceback", "failed", "fatal"))]
    return {
        "ok": True,
        "service": service_name,
        "log_file": logs["log_file"],
        "lines_examined": logs["line_count"],
        "error_lines": error_lines,
        "error_count": len(error_lines),
        "error_rate": round(len(error_lines) / max(1, logs["line_count"]), 4),
    }
