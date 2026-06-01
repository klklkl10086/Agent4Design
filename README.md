# Agent4Design

Agent4Design exposes IBM Rhapsody COM and XMI synchronization as validated local
services. The current API supports read-only planning, approved model writes,
post-sync verification, an optional MCP server, and an optional LangGraph
workflow.

## Install

Use a Windows Python environment that can access the active Rhapsody desktop
session:

```powershell
python -m pip install -e ".[rhapsody]"
```

Optional adapters:

```powershell
python -m pip install -e ".[rhapsody,llm,mcp,graph,dev]"
```

## Start The Model Agent

Configure an OpenAI or OpenAI-compatible chat API:

```powershell
$env:AGENT4DESIGN_LLM_API_KEY = "..."
$env:AGENT4DESIGN_LLM_MODEL = "your-model-name"
# Optional for compatible providers:
$env:AGENT4DESIGN_LLM_BASE_URL = "https://provider.example/v1"
```

Start an interactive read-only Agent:

```powershell
python -m agent4design.adapters.llm
```

Allow terminal confirmation prompts when the model requests Rhapsody writes:

```powershell
python -m agent4design.adapters.llm --allow-writes
```

The model cannot approve its own write calls. Without `--allow-writes`, write
tools are rejected even if model-generated arguments include `approved=true`.

## Start The HTTP API

```powershell
python -m agent4design.adapters.http
```

The default address is `http://127.0.0.1:8765`.

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
Invoke-RestMethod http://127.0.0.1:8765/tools
```

Initialize Rhapsody after opening a project and selecting a writable model
container:

```powershell
$body = @{
  name = "initialize_rhapsody"
  arguments = @{ select_current_target = $true }
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8765/call `
  -ContentType application/json `
  -Body $body
```

See [HTTP API](agent4design/docs/http_api.md) for the available routes and sync
examples.

## Optional MCP Server

```powershell
python -m agent4design.adapters.mcp
```

MCP and HTTP both call the same framework-neutral `Agent4DesignService`. Neither
adapter contains COM write logic.

The model Agent uses that same service boundary. It does not call COM directly.

## Safety Defaults

- The API listens on localhost by default.
- Listening outside localhost requires `AGENT4DESIGN_API_TOKEN`.
- Model synchronization and project saving require explicit approval.
- Unknown custom types are rejected unless placeholder creation is enabled.
- Activity XMI import remains disabled by default until ownership mapping is
  validated against an XMI Toolkit export from Rhapsody.
