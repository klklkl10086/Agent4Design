# MCP Usage

Agent4Design supports two MCP modes. In both modes the MCP server must run on
the Windows machine where IBM Rhapsody is open, because COM operations must be
executed in that local desktop session.

## Install

Install the core package and the Rhapsody COM dependency:

```powershell
python -m pip install -e ".[rhapsody]"
```

Install MCP support:

```powershell
python -m pip install -e ".[mcp]"
```

For development or all adapters:

```powershell
python -m pip install -e ".[rhapsody,mcp,graph,llm,dev]"
```

## Mode 1: Local MCP

Use this when the MCP client and Rhapsody are on the same machine.

```powershell
python -m agent4design.adapters.mcp --transport stdio
```

Typical MCP client config:

```json
{
  "mcpServers": {
    "agent4design": {
      "command": "python",
      "args": ["-m", "agent4design.adapters.mcp", "--transport", "stdio"]
    }
  }
}
```

## Mode 2: Remote MCP

Use this when another machine connects to the MCP server, but Rhapsody still
runs on the server machine.

On the Rhapsody machine:

```powershell
python -m agent4design.adapters.mcp `
  --transport streamable-http `
  --host 0.0.0.0 `
  --port 8766 `
  --path /mcp `
  --token your-secret-token
```

Equivalent environment variables:

```powershell
AGENT4DESIGN_MCP_TRANSPORT=streamable-http
AGENT4DESIGN_MCP_HOST=0.0.0.0
AGENT4DESIGN_MCP_PORT=8766
AGENT4DESIGN_MCP_PATH=/mcp
AGENT4DESIGN_MCP_TOKEN=your-secret-token
```

Remote clients connect to:

```text
http://<rhapsody-machine-ip>:8766/mcp
```

When a token is configured, tool calls must include:

```json
{
  "auth_token": "your-secret-token"
}
```

For write tools, approval is still required:

```json
{
  "approved": true,
  "auth_token": "your-secret-token"
}
```

## Recommended Flow

1. Open Rhapsody and load a project.
2. Select the target Class or Block in Rhapsody.
3. Call `initialize_rhapsody`.
4. Call `refresh_type_registry`.
5. Call `plan_agent4design_sync` or `plan_code_path_modeling`.
6. Review the plan.
7. Call the corresponding execute tool with `approved=true`.
8. Call `save_rhapsody_project` with `approved=true` if needed.

## Security Notes

Remote MCP means remote users can ask this machine to operate its local
Rhapsody model. Do not expose it directly to the public Internet.

Use at least:

- VPN or trusted LAN.
- `AGENT4DESIGN_MCP_TOKEN`.
- Rhapsody projects with appropriate write permissions.
- One user/task at a time for model-changing operations.

The dependency-free HTTP API in `agent4design.adapters.http` is a normal JSON
HTTP API. The MCP adapter is for MCP-capable AI clients.
