# ClaudeDevops MCP

ClaudeDevops MCP is a compact FastMCP server that simulates a five-service microservices platform with live metrics, cascading failures, chaos injection, and incident replay for Claude Desktop. It gives an agent a realistic operational control plane without needing any external infrastructure.

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
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```
