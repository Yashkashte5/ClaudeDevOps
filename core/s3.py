"""AWS S3 integration for historical service logs."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import boto3


def _load_env_file() -> None:
    """Load key=value pairs from a project .env file if present."""
    from pathlib import Path
    
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

AWS_REGION = os.getenv("CLAUDEDEVOPS_AWS_REGION", "ap-south-1")
S3_BUCKET = "claudedevops-logs"

s3 = boto3.client("s3")


def list_s3_objects(prefix: str = "") -> dict[str, Any]:
    """
    List S3 objects from claudedevops-logs bucket.
    
    Args:
        prefix: Optional prefix to filter objects (e.g., "api-gateway/")
    
    Returns:
        {
            "files": [
                {"key": "<object key>", "last_modified": "<timestamp>"},
                ...
            ]
        }
        or error response on failure
    """
    try:
        params: dict[str, Any] = {"Bucket": S3_BUCKET, "MaxKeys": 50}
        if prefix:
            params["Prefix"] = prefix

        response = s3.list_objects_v2(**params)
        if "Contents" not in response:
            return {"files": []}

        objects = [obj for obj in response["Contents"] if not obj.get("Key", "").endswith("/")]
        objects.sort(key=lambda obj: obj["LastModified"], reverse=True)

        files = [
            {
                "key": obj["Key"],
                "last_modified": obj["LastModified"].isoformat(),
            }
            for obj in objects
        ]
        return {"files": files}
    except Exception as e:
        return {"error": str(e), "code": "s3_error"}


def create_example_s3_logs() -> dict[str, Any]:
    """
    Create empty example log files in S3 for demonstration.
    
    Requires:
    - S3 bucket to exist
    - IAM user to have s3:PutObject permission
    
    Returns:
        Status of operation
    """
    logs = [
        "api-gateway/logs-2026-04-08T12-30.txt",
        "auth-service/logs-2026-04-08T12-32.txt",
        "user-service/logs-2026-04-08T12-33.txt",
        "payment-service/logs-2026-04-08T12-34.txt",
        "notification-service/logs-2026-04-08T12-35.txt",
    ]
    
    created = []
    errors = []
    
    try:
        # Create example log files (assumes bucket already exists)
        for log_file in logs:
            try:
                s3.put_object(
                    Bucket=S3_BUCKET,
                    Key=log_file,
                    Body=b""  # Empty file
                )
                created.append(log_file)
            except Exception as e:
                errors.append({"file": log_file, "error": str(e)})
        
        return {
            "created": created,
            "file_count": len(created),
            "errors": errors if errors else None,
            "bucket": S3_BUCKET,
            "region": AWS_REGION
        }
    
    except Exception as e:
        return {
            "error": f"Failed to create example logs: {str(e)}",
            "code": "s3_error"
        }
