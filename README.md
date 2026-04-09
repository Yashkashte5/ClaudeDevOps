# ClaudeDevops MCP

ClaudeDevops is a FastMCP-based DevOps control plane for a small EC2-hosted microservice stack. It provides service status checks, log access, dependency inspection, chaos operations, and S3-backed historical log browsing.

## Live Demo

[![ClaudeDevops Demo](https://img.youtube.com/vi/AzrGaJFpcjU/0.jpg)](https://youtube.com/watch?v=AzrGaJFpcjU)

## What This Project Does

- Manages five services: `api-gateway`, `auth-service`, `user-service`, `payment-service`, and `notification-service`
- Uses AWS SSM for runtime control and log retrieval from the EC2 host
- Uses AWS S3 for historical log browsing
- Exposes the functionality as MCP tools for Claude Desktop or any MCP client

## Repository Layout

```text
README.md
requirements.txt
server.py
core/
  chaos.py
  graph.py
  logs.py
  s3.py
  services.py
```

## Requirements

- Python 3.11+
- AWS credentials available through the default boto3 credential chain
- IAM permissions for:
  - SSM `SendCommand` and `GetCommandInvocation`
  - S3 `ListBucket` and `GetObject`

## Configuration

Create a local `.env` file with values for your environment. Do not commit real credentials or account-specific values.

```bash
CLAUDEDEVOPS_SERVICE_HOST=<ec2-hostname-or-ip>
CLAUDEDEVOPS_INSTANCE_ID=<ec2-instance-id>
CLAUDEDEVOPS_AWS_REGION=<aws-region>
CLAUDEDEVOPS_SSM_COMMAND_TIMEOUT=45
CLAUDEDEVOPS_HTTP_TIMEOUT=5
CLAUDEDEVOPS_S3_BUCKET=claudedevops-logs
```

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

## 🎥 Live Demo

[![ClaudeDevops Demo](https://img.youtube.com/vi/AzrGaJFpcjU/0.jpg)](https://youtube.com/watch?v=AzrGaJFpcjU)

## Claude Desktop Configuration

Use an absolute path to `server.py` on your machine:

```json
{
  "mcpServers": {
    "ClaudeDevops": {
      "command": "python",
      "args": ["<absolute-path-to-repo>/server.py"]
    }
  }
}
```

## Available Tools

1. `list_services` - List all services with base URLs and reachability
2. `get_service_health` - Return the lightweight health payload for a service
3. `get_service_status` - Return reachable/down status for a service
4. `get_metrics` - Return latency and uptime metrics
5. `get_logs` - Fetch recent service logs from EC2 via SSM
6. `restart_service` - Restart one service on its port via SSM
7. `kill_service` - Kill one service process on its port via SSM
8. `get_dependency_chain` - Show upstream and downstream dependencies
9. `list_s3_logs` - List historical log objects from S3, optionally filtered by prefix

## S3 Historical Logs

The `list_s3_logs(prefix="")` tool reads from the `claudedevops-logs` bucket and returns up to 50 objects sorted by newest first.

- Use `prefix="api-gateway/"` to narrow results to one service
- Folder markers ending in `/` are ignored
- If the bucket is empty or a prefix has no files, the tool returns `{"files": []}`

Example usage:

```python
list_s3_logs()
list_s3_logs("api-gateway/")
```

## Operational Notes

- Keep real AWS values out of the README and source control
- Use the `.env` file for local configuration only
- SSM is used for runtime operations so the tools do not require SSH access