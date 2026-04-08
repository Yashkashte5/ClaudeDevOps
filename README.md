# ClaudeDevops MCP

Lightweight FastMCP server for the live EC2 microservices at 43.205.127.108.

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

## Claude Desktop Config

```json
{
  "mcpServers": {
    "ClaudeDevops": {
      "command": "python",
      "args": ["C:/Users/yashk/Desktop/ClaudeDevops-MCP/server.py"]
    }
  }
}
```

## Required Environment

Set these before using kill, restart, or log tools:

```bash
set CLAUDEDEVOPS_SSH_KEY_PATH=C:\path\to\key.pem
set CLAUDEDEVOPS_SSH_USER=ubuntu
set CLAUDEDEVOPS_SSH_HOST=43.205.127.108
```

## Tools

1. `list_services`
2. `get_service_health`
3. `get_service_status`
4. `get_metrics`
5. `get_logs`
6. `restart_service`
7. `kill_service`
8. `get_dependency_chain`